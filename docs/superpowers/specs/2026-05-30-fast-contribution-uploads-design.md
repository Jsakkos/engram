# Fast contribution uploads + server-side safety valve

- **Date:** 2026-05-30
- **Status:** Approved design (pre-implementation)
- **Repos touched:** `engram` (client uploader), `engram-fingerprint-server` (worker)
- **Related:** Phase 2 fingerprint network ([2026-05-27-phase2-fingerprint-server-design.md](2026-05-27-phase2-fingerprint-server-design.md)); follow-up from the catalog-dashboard fix (engram-fingerprint-server PR #24).

## Problem

The `ContributionUploader` drains the local `fingerprint_contributions` queue far too slowly for the bootstrap case. It is instantiated as `ContributionUploader()` (`backend/app/main.py`) with the default `poll_interval_seconds=3600`, and its loop uploads **one** batch of `_BATCH_SIZE=50` and then sleeps a full hour — **~50 uploads/hour regardless of backlog**.

Measured reality (one machine, mid-bootstrap): queue of **1,049 rows / 22 shows**, **599 still pending**, fingerprints **~90 KB/row raw** (≈54 MB remaining, less on the wire after zstd). At 50/hour that backlog takes **~12 hours**, during which the app must stay open. The per-upload work itself is fast (~1/s observed); ~98% of wall-clock is the loop sleeping.

There is **no server-side limit** behind the 50/hour figure — the worker's `POST /v1/contribute` does no rate limiting. The slow cadence is an untuned client-side default meant for steady state (a disc or two ripped per day), wrongly applied to bootstrap.

## Goals

- A queued backlog drains in **minutes, not hours**, without the user babysitting the app.
- The loop stays **quiet at rest** (no busy-polling when the queue is empty).
- Protection against overload/abuse lives **server-side** (the shared resource), not as client politeness.
- No contributions are lost when the server pushes back.

## Non-goals

- Replacing the queue/poll model with push/event delivery (a future option; YAGNI now).
- Hardening against a determined attacker who rotates pseudonyms — that remains the job of the existing anti-poison shadowban, not this rate limit.
- Changing the wire format or the contribution schema.

## Design

### Part A — Client uploader (`engram/backend/app/services/contribution_uploader.py`)

**1. Drain-then-idle.** Replace "one batch then sleep an hour" with: while rows are pending, upload batches back-to-back until the queue is empty; only then sleep the idle poll. New shape:

```
_upload_loop():
  while True:
    drained = await _drain()        # loops batches until queue empty; returns count
    await sleep(poll_interval)      # idle wait for the next steady-state trickle
```

`_drain()` repeats: fetch up to `_BATCH_SIZE` pending ids → upload them → repeat until a fetch returns zero. The consent/disclosure gates currently in `_process_batch` (`enable_fingerprint_contributions`, `fingerprint_disclosure_accepted`, JIT disclosure event) move to the **top of `_drain`**, unchanged in behavior — if consent is missing it fires the disclosure event and uploads nothing.

**2. Bounded concurrency + shared client.** Within a batch, upload `_CONCURRENCY` rows at once behind an `asyncio.Semaphore`, sharing **one** `httpx.AsyncClient` for the whole drain (HTTP keep-alive) instead of constructing a client per row. Each row still uses its own short-lived DB session so status updates commit independently; the engram DB is WAL-mode, so a handful of concurrent writers is fine.

**3. Retry-on-429.** `_upload_one` currently marks **every** 4xx (including 429) as a permanent failure. Change: **429 is transient** — increment attempts, honor the `Retry-After` response header (fall back to exponential `2**attempt`) for the backoff, and retry within the existing `_MAX_ATTEMPTS` budget. All other 4xx stay permanent; 5xx stays transient as today.

**Idle interval.** Reduce the default `poll_interval_seconds` from `3600` to `900` (15 min) for steadier pickup of new rips. Secondary to drain-then-idle, which already removes the backlog wait.

### Part B — Server safety valve (`engram-fingerprint-server`)

Add a **per-pseudonym** rate limit using Cloudflare's Workers Rate-Limiting binding, keyed by `pseudonym`, returning **429 + `Retry-After`** when exceeded.

`wrangler.toml` (validate exact stanza against current Cloudflare docs at implementation — the binding has moved between forms):

```toml
[[unsafe.bindings]]
name = "CONTRIBUTE_RATE_LIMITER"
type = "ratelimit"
namespace_id = "1001"
simple = { limit = 600, period = 60 }   # period ∈ {10, 60}
```

In `handleContribute`, **after** schema validation (so we have `req.pseudonym`) and **before** the expensive decode/minhash/insert work:

```ts
if (env.CONTRIBUTE_RATE_LIMITER) {
  const { success } = await env.CONTRIBUTE_RATE_LIMITER.limit({ key: req.pseudonym });
  if (!success) {
    return new Response("rate limited", { status: 429, headers: { "Retry-After": "60" } });
  }
}
```

The binding is treated as **optional** (`if (env.CONTRIBUTE_RATE_LIMITER)`), so local `wrangler dev` and the existing vitest workers pool — which don't provision it — keep working and existing tests stay green. `Env` gains an optional `CONTRIBUTE_RATE_LIMITER?` member.

**This is a circuit-breaker, not a security control.** Per-colo best-effort counting and a generous ceiling mean it stops runaway loops and casual hammering; it does not stop a pseudonym-rotating attacker. The anti-poison screen + shadowban remain the abuse defense.

### The 429 contract

Server throttles → returns 429 + `Retry-After` → client treats it as transient, waits, retries → no rows lost. This is the single seam binding Part A and Part B.

### Parameters (coordinated, all tunable)

| Parameter | Value | Notes |
|---|---|---|
| Client `_CONCURRENCY` | 5 | peak ≈ 300–600 req/min for one client |
| Client `_BATCH_SIZE` | 50 (unchanged) | fetch granularity |
| Client idle `poll_interval` | 900 s | steady-state pickup; backlog no longer waits on it |
| Server limit | 600 / 60 s / pseudonym | **must exceed** an honest client's peak so real users never hit it |

The server ceiling is deliberately set **above** the client's peak: the limiter only bites on pathological rates.

## Error handling

- **429:** transient; honor `Retry-After`; retry within `_MAX_ATTEMPTS`.
- **Other 4xx:** permanent failure (unchanged).
- **5xx / network:** transient with exponential backoff (unchanged).
- **Consent missing:** fire JIT disclosure event, upload nothing (unchanged).
- **One row failing** must not abort the batch (`asyncio.gather(..., return_exceptions=True)`; log and continue).

## Testing

- **Client:** drain-then-idle loops until the queue empties then idles; bounded concurrency never exceeds the semaphore; a single shared client is reused; **429 → retry honoring `Retry-After`** (not permanent fail); other 4xx still permanent; consent gate still blocks uploads. Existing `tests/integration/test_contribution_uploader.py` is the home for these.
- **Server:** limiter **absent** → request proceeds (guards existing tests); limiter **present and over limit** → 429 + `Retry-After` and no DB write. The real binding isn't available in miniflare/vitest, so tests inject a stub `CONTRIBUTE_RATE_LIMITER` into `env`.

## Rollout / backward compatibility

- Ship the **client 429-retry change first** (or simultaneously). With the server ceiling generous, ordering is low-risk, but a client that mis-handles 429 must never reach production before the server can emit 429.
- The server limiter is additive and optional; deploying it changes nothing for clients under the ceiling.
