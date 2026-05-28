# Phase 2: Fingerprint Network Server + Client Uploader — Design

> Companion to Phase 1 ([docs/superpowers/plans/2026-05-27-phase1-chromaprint-foundation.md](../plans/2026-05-27-phase1-chromaprint-foundation.md), shipped as [#242](https://github.com/Jsakkos/engram/pull/242)) and the original architecture brief ([what-would-it-take-golden-fog.md, design source of truth](file:///C:/Users/jonat/.claude/plans/what-would-it-take-golden-fog.md)).

## Context

Phase 1 added the local foundation: every successful Engram match now extracts a chromaprint via `fpcalc` and queues a `FingerprintContribution` row in SQLite. Nothing leaves the machine. Phase 1 also shipped the opt-out config (`enable_fingerprint_contributions`), per-install pseudonym auto-generation, the bootstrap CLI for fingerprinting existing libraries, and the local audit endpoint at `GET /api/fingerprint/contributions`.

Phase 2 stands up the receiving side and the drainer: a Cloudflare Worker server that accepts contributions, runs anti-poison cross-checks, promotes corroborated fingerprints into a canonical tier nightly, and writes per-show packs to R2 (for Phase 3's identification path to consume later). On the client we add `ContributionUploader` — a background asyncio task that drains the queue, a just-in-time disclosure modal that gates the first upload, a `POST /api/fingerprint/forget` endpoint, and a one-time Alembic migration for two new bookkeeping columns.

Out of scope for Phase 2 (intentionally — Phase 3 territory): `GET /v1/identify`, the public `GET /v1/pack/{tmdb_id}` endpoint, the client-side `ChromaprintMatcher`, and any cascade-integration changes in `curator.py`/`matching_coordinator.py` beyond what Phase 1 already shipped.

## Locked-In Decisions

These were validated in the 2026-05-27 brainstorming session and are load-bearing for the rest of the spec.

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Server stack | Cloudflare Workers + D1 + R2 | Scales to zero, native CDN for the eventual `/v1/pack`, $5/mo at v1 scale. Anti-poison query is feasible on Worker CPU via minhash sketching. |
| 2 | Repo layout | Separate sibling repo `engram-fingerprint-server` | Independent CI/release cadence, clean secrets boundary, GitHub permissions can diverge. Wire-format changes coordinate via versioned `wire_format_version` field. |
| 3 | Wire format | JSON body, `fingerprint_b64` = base64(zstd-varint) | ~2x smaller than gzip-JSON at rest; D1's 10GB limit forces this above ~600K canonical rows. Client re-encodes at upload time (Phase 1's local gzip-JSON storage stays intact). |
| 4 | Pack endpoint scope | PackBuilder worker only in Phase 2; public `GET /v1/pack` deferred to Phase 3 | Format will be tuned by Phase 3's matcher consumer. Worker writes to R2 nightly so we have data to validate Phase 3 against. |
| 5 | Anti-poison threshold | 70% Hamming-overlap as Worker env var (`POISON_CONFLICT_THRESHOLD`); log overlap pct on EVERY contribution into `overlap_observation` table | Tunable without redeploy; observability data is the prerequisite for empirical tuning. |
| 6 | `match_source` validation | Code-level allowlist in the Worker (not schema-enforced enum) | Future-proof: adding a new source for Phase 3's chromaprint corroboration becomes a Worker deploy, not a D1 schema migration. Still rejects spoofed/typo'd values. |
| 7 | Privacy disclosure UX | Just-in-time blocking modal before first upload (NOT first-run wizard) | More honest — the choice arrives at the moment it matters. ConfigWizard is already crowded; abstract disclosures during initial setup get skimmed. |

## System Architecture

```
┌──────────── Engram Client (this repo, NEW Phase 2 additions) ──────────────────┐
│                                                                                │
│  [Phase 1 — already shipped]                                                   │
│  Drive → JobManager → MatchingCoordinator → ChromaprintExtractor               │
│                                                ↓                               │
│                                      fingerprint_contributions table           │
│                                      (uploaded_at IS NULL when queued)         │
│                                                                                │
│  [Phase 2 — NEW]                                                               │
│                                                                                │
│  ┌── ContributionUploader (asyncio background task) ───────────────────┐       │
│  │   • 5-min tick OR queue-size signal                                 │       │
│  │   • Reads enable_fingerprint_contributions + disclosure_accepted    │       │
│  │   • Decodes gzip-JSON blob → re-encodes as zstd-varint              │       │
│  │   • Batches ≤ 10 per POST                                           │       │
│  │   • Per-row exp backoff in next_attempt_at column, cap 7d           │       │
│  │   • Writes ~/.engram/cache/contribution_log.jsonl on every action   │       │
│  └─────────────────────────────────────────────────────────────────────┘       │
│                                                                                │
│  POST /api/fingerprint/forget   (NEW — local-only, require_localhost)          │
│    → call server's /v1/forget                                                  │
│    → DELETE local pending queue (uploaded_at IS NULL only)                     │
│    → Rotate contribution_pseudonym                                             │
│    → Reset fingerprint_disclosure_accepted                                     │
│                                                                                │
│  JIT-disclosure modal (NEW — frontend, in App.tsx)                             │
│    → Triggered by WS event "fingerprint_disclosure_required"                   │
│    → Blocks: Accept → PUT /api/config; Decline → disable contributions         │
└────────────────────────────────────────┬───────────────────────────────────────┘
                                         │ TLS only
                                         ▼
┌──────── engram-fingerprint-server (NEW separate repo, Cloudflare) ─────────────┐
│                                                                                │
│  Cloudflare Worker (TypeScript)                                                │
│    POST /v1/contribute   → AntiPoisonCheck (sync) → D1 contribution            │
│    POST /v1/forget       → D1 delete by pseudonym                              │
│                                                                                │
│  D1 Database (SQLite-on-edge)                                                  │
│    • contribution         — append-only raw uploads                            │
│    • episode_canonical    — nightly-promoted aggregate                         │
│    • canonical_sketch     — minhash sketches; anti-poison fast path            │
│    • contributor          — pseudonym registry + flag counters                 │
│    • overlap_observation  — empirical-tuning data for the 70% threshold        │
│                                                                                │
│  Scheduled Workers (Cron Triggers)                                             │
│    • PromotionWorker (0 3 * * *) — CANDIDATE/CONFIRMED/CANONICAL transitions   │
│    • PackBuilderWorker (0 4 * * *) — per-show packs written to R2              │
│                                                                                │
│  R2 Bucket: engram-fp-packs/{tmdb_id}.zstd  (no public read endpoint in P2)    │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Server-Side Components (`engram-fingerprint-server`)

### D1 Schema

```sql
-- Raw contributions, append-only.
CREATE TABLE contribution (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at INTEGER NOT NULL DEFAULT (unixepoch()),
  pseudonym TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  season INTEGER,
  episode INTEGER,
  fingerprint BLOB NOT NULL,                     -- zstd-varint, the canonical wire format
  fingerprint_sha256 BLOB NOT NULL,              -- SHA256 of decompressed varint stream
  disc_content_hash BLOB,
  match_confidence REAL NOT NULL,
  match_source TEXT NOT NULL,
  client_version TEXT NOT NULL,
  poison_check TEXT NOT NULL DEFAULT 'pending',  -- 'pass' | 'flag_conflict' | 'flag_duplicate'
  promoted_at INTEGER                            -- set by PromotionWorker
);
CREATE INDEX idx_contribution_episode ON contribution (tmdb_id, season, episode);
CREATE INDEX idx_contribution_pseudonym ON contribution (pseudonym, received_at);
CREATE INDEX idx_contribution_unpromoted ON contribution (promoted_at) WHERE promoted_at IS NULL;
CREATE UNIQUE INDEX idx_contribution_dedupe
  ON contribution (pseudonym, tmdb_id, season, episode, fingerprint_sha256);

-- Per-pseudonym registry.
CREATE TABLE contributor (
  pseudonym TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  contribution_count INTEGER NOT NULL DEFAULT 0,
  flagged INTEGER NOT NULL DEFAULT 0,
  flag_count INTEGER NOT NULL DEFAULT 0
);

-- Promoted episodes.
CREATE TABLE episode_canonical (
  tmdb_id INTEGER NOT NULL,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  tier TEXT NOT NULL,                            -- 'candidate' | 'confirmed' | 'canonical'
  fingerprint BLOB NOT NULL,                     -- consensus fingerprint (zstd-varint)
  unique_contributors INTEGER NOT NULL,
  mean_confidence REAL NOT NULL,
  promoted_at INTEGER NOT NULL,
  PRIMARY KEY (tmdb_id, season, episode)
);
CREATE INDEX idx_canonical_tier ON episode_canonical (tier);

-- Minhash sketches; anti-poison fast path.
CREATE TABLE canonical_sketch (
  tmdb_id INTEGER NOT NULL,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  sketch BLOB NOT NULL,                          -- 128 × uint32 LE-packed minhashes (512 bytes)
  hash_count INTEGER NOT NULL,                   -- denominator for Jaccard normalization
  generated_at INTEGER NOT NULL,
  PRIMARY KEY (tmdb_id, season, episode)
);

-- Anti-poison observability (Q5 empirical-tuning data).
CREATE TABLE overlap_observation (
  contribution_id INTEGER PRIMARY KEY REFERENCES contribution(id) ON DELETE CASCADE,
  max_overlap_pct REAL NOT NULL,
  max_overlap_target_tmdb_id INTEGER,
  max_overlap_target_season INTEGER,
  max_overlap_target_episode INTEGER,
  candidates_checked INTEGER NOT NULL,
  computed_at INTEGER NOT NULL
);
```

D1 migration files are numbered SQL in `migrations/` and applied via `wrangler d1 execute` in CI on deploy.

### Endpoints

#### `POST /v1/contribute`

```ts
interface ContributionRequest {
  wire_format_version: 1;
  pseudonym: string;                // UUIDv4 string
  tmdb_id: number;
  season: number | null;
  episode: number | null;
  fingerprint_b64: string;          // base64(zstd-varint blob)
  fingerprint_sha256_b64: string;   // base64 of SHA256 of decompressed varint stream
  disc_content_hash_b64: string | null;
  match_confidence: number;         // 0.0–1.0
  match_source: "engram_asr" | "engram_discdb" | "bootstrap"
              | "user_review" | "engram_chromaprint_corroboration";
  client_version: string;           // e.g. "engram/0.9.2"
}

interface ContributionResponse {
  contribution_id: number;
  poison_check: "pass" | "flag_conflict" | "flag_duplicate";
  overlap_pct: number;              // 0.0–1.0; exposed for client audit
}
```

Status codes:
- `202 Accepted` — stored. `poison_check` may be `pass` or `flag_conflict`.
- `200 OK` — silently accepted but **not** stored. Two distinct cases share this response shape (clients cannot tell them apart): (a) dedupe hit — we already have this exact (pseudonym, episode, fingerprint_sha256) tuple, or (b) the pseudonym is flagged and we are silently shadowbanning. Both return `poison_check='flag_duplicate'`. This ambiguity is intentional defense-in-depth.
- `400 Bad Request` — schema invalid, base64/zstd decode failed, or match_source not in allowlist.
- `429 Too Many Requests` — Cloudflare's pseudonymous rate-limiter; not IP-keyed.

#### `POST /v1/forget`

```ts
interface ForgetRequest { pseudonym: string; }
interface ForgetResponse {
  rows_deleted: number;             // contribution + contributor rows
  canonical_unaffected: true;       // documented invariant
}
```

`200 OK` always (idempotent; deleting an unknown pseudonym is not an error). `400` if pseudonym is malformed.

### Anti-Poison Algorithm

```
on POST /v1/contribute:
  1. Validate schema; 400 on fail.
  2. base64-decode + zstd-decompress + varint-decode fingerprint → uint32[]; 400 on fail.
  3. SELECT contributor WHERE pseudonym = ?
     IF flagged = 1:
        // silent shadowban: don't tell attackers they're flagged
        return 200 with poison_check='flag_duplicate'
  4. Dedupe: SELECT contribution WHERE (pseudonym, tmdb_id, season, episode, fingerprint_sha256)
     IF exists: return 200 with existing contribution_id, poison_check='flag_duplicate'

  5. ANTI-POISON FAST PATH:
       - sketch_in = minhash128(uint32[])
       - SELECT sketch, hash_count, tmdb_id, season, episode FROM canonical_sketch
         WHERE NOT (tmdb_id = ? AND season = ? AND episode = ?)
       - For each row: jaccard_est = popcount(sketch_in ∩ sketch_row) / 128
       - (max_est, target) = argmax of jaccard_est

  6. ANTI-POISON CONFIRM (only if max_est > POISON_CONFLICT_THRESHOLD - 0.10):
       - Load full canonical fingerprint for target from episode_canonical.
       - exact_overlap = count(query_hash ∈ canonical with hamming ≤ 6) / |query_hash|
       - IF exact_overlap > POISON_CONFLICT_THRESHOLD (env var, default 0.70):
            poison_check = 'flag_conflict'
            UPDATE contributor SET flag_count = flag_count + 1
            IF flag_count > 3: UPDATE contributor SET flagged = 1
       ELSE:
            poison_check = 'pass'
       Record exact_overlap_pct + target in overlap_observation.

  7. ALWAYS-LOG (max_est ≤ POISON_CONFLICT_THRESHOLD - 0.10):
       poison_check = 'pass'
       Record max_est + target in overlap_observation (estimate flag set)

  8. INSERT INTO contribution.
  9. UPSERT contributor (increment contribution_count, update last_seen).
  10. Return 202 with contribution_id + poison_check + overlap_pct.
```

**Why minhash sketches.** A naive cross-overlap query — for each incoming fingerprint, Hamming-compare against every canonical fingerprint — would be O(N_canonicals × hashes_per_ep) per request. For 100K canonicals × 10K hashes each, that's 1B comparisons per request, fatal on Workers' 30s CPU ceiling. Minhash sketches let us approximate Jaccard similarity in 128 int comparisons per candidate; checking 50K sketches is ~6M ops (~50ms). The estimate has a ~5% error band, so we screen at `THRESHOLD - 0.10` and only run exact Hamming on the survivor.

**Why silent shadowban.** Telling a flagged contributor they're shadowbanned invites pseudonym rotation; silent drops waste their effort and provide better defense in depth.

### PromotionWorker (Cron Trigger, `0 3 * * *`)

```
For each distinct (tmdb_id, season, episode) in contribution WHERE promoted_at IS NULL:
  1. Pull all contributions for episode where poison_check = 'pass'.
  2. Group by pseudonym; keep most recent per pseudonym.
  3. Filter to match_confidence >= 0.70.
  4. Count distinct (pseudonym × disc_content_hash) pairs → independent_count.
  5. Tier:
       independent_count >= 3 AND mean(match_confidence) >= 0.85
         AND no contributor.flagged                                  → CANONICAL
       independent_count >= 2                                        → CONFIRMED
       independent_count >= 1                                        → CANDIDATE
  6. If tier changes vs episode_canonical:
       - Compute consensus fingerprint: hash union where occurrence_count >= 50% of contributors.
       - UPSERT episode_canonical.
       - Compute 128-minhash sketch of consensus fingerprint.
       - UPSERT canonical_sketch.
  7. UPDATE contribution SET promoted_at = unixepoch() WHERE id IN (processed).
```

### PackBuilderWorker (Cron Trigger, `0 4 * * *`)

```
For each tmdb_id with changes in episode_canonical since last run:
  1. SELECT all CANONICAL episodes for tmdb_id.
  2. Assemble per-show pack:
       header: { wire_format_version: 1, tmdb_id, n_episodes, generated_at }
       body:   for each episode: { season, episode, fingerprint_blob, hash_count }
  3. zstd-compress the assembled pack.
  4. R2.PUT engram-fp-packs/{tmdb_id}.zstd with ETag = sha256(pack).
  5. Log pack size + episode count to a small tracking table for ops visibility.
```

The pack format is intentionally underspecified — Phase 3's `ChromaprintMatcher` dictates the layout once we know what local lookups need. Phase 2 ships `wire_format_version=1`; Phase 3 may bump.

## Client-Side Components (this repo)

### `backend/app/services/contribution_uploader.py` (NEW)

Singleton asyncio task started in `app/main.py`'s `lifespan` hook, alongside the existing `init_db()` and Phase 1's pseudonym-generator.

Key behaviors:
- **Tick cadence:** 5-minute timer OR `ContributionQueue.signal()` size-pressure notification (≥10 unuploaded rows).
- **Pre-flight check:** reads `enable_fingerprint_contributions` and `fingerprint_disclosure_accepted` each tick. If either is False, broadcasts `fingerprint_disclosure_required` (when disclosure unset) and returns.
- **Batching:** SELECT up to `BATCH_SIZE=10` rows WHERE `uploaded_at IS NULL` AND `upload_attempts < MAX_ATTEMPTS` AND (`next_attempt_at IS NULL` OR `next_attempt_at <= now()`), ordered by `queued_at`.
- **Constants** (module-level in `contribution_uploader.py`): `BATCH_SIZE = 10`, `MAX_ATTEMPTS = 20`, `TICK_INTERVAL_SECONDS = 300`, `BACKOFF_BASE_SECONDS = 60`, `BACKOFF_CAP_SECONDS = 7 * 86400`.
- **Per-row processing:** decode Phase 1's gzip-JSON blob via `ChromaprintResult.from_blob()`, re-encode hashes as zstd-varint, base64-wrap, POST to `/v1/contribute`.
- **Result handling:**
  - `200/202/409` → `uploaded_at = now()`, append audit-log line.
  - `400` → permanent fail; set `uploaded_at` to skip forever, record `upload_error`.
  - `429/503/network` → exponential backoff: `next_attempt_at = now() + min(60 * 2^attempts, 7d)`, increment `upload_attempts`.
- **No backlog throttling** (Q3-meta decision). When disclosure is accepted, the existing 10-row batch + 5-min tick rate-limits naturally to ~120 uploads/hour. Server-side Cloudflare rate-limiter is the final guardrail.

### `AppConfig` additions

Three new fields beyond Phase 1's `contribution_pseudonym` + `enable_fingerprint_contributions`:

```python
fingerprint_server_url: str = Field(default="https://fp.engram.example/v1")  # deployment-time value; see "Deployment" section
fingerprint_disclosure_accepted: bool = Field(default=False)
fingerprint_disclosure_accepted_at: datetime | None = Field(default=None)
```

`fingerprint_server_url` is NOT redacted by `GET /api/config` — transparency is the whole point. Users with privacy concerns can override it to a custom server (or a `/dev/null` sink) via `PUT /api/config`.

### `fingerprint_contributions` table additions

Two new columns to support per-row backoff and error visibility:

```python
next_attempt_at: datetime | None = Field(default=None)  # when row becomes eligible to retry
upload_error: str | None = Field(default=None)          # last error message
```

Alembic migration adds these as nullable, no backfill needed (NULL means "ready to attempt").

### `POST /api/fingerprint/forget` (NEW local route)

`require_localhost` dependency — must not be callable from LAN. Sequence:

1. POST to server `/v1/forget {pseudonym: <current>}`. If network fails: 503, abort.
2. DELETE FROM `fingerprint_contributions` WHERE `uploaded_at IS NULL`.
3. Generate fresh UUIDv4; assign to `app_config.contribution_pseudonym`.
4. Reset `fingerprint_disclosure_accepted = False`.
5. Append `{event: "forget"}` line to audit log.
6. Return `{old_pseudonym, new_pseudonym, local_rows_deleted, server_rows_deleted}`.

### Audit log (`~/.engram/cache/contribution_log.jsonl`)

Append-only JSONL. One line per upload (success or hard-fail) and one line per forget event. Each line self-describing:

```json
{"ts":"2026-05-28T03:14:15Z","event":"upload","contribution_id":42,"tmdb_id":95396,"season":1,"episode":1,"match_source":"engram_asr","match_confidence":0.91,"server_contribution_id":17,"poison_check":"pass","overlap_pct":0.03}
{"ts":"2026-05-28T03:14:16Z","event":"upload_failed","contribution_id":43,"error":"400: invalid fingerprint encoding"}
{"ts":"2026-05-29T10:00:00Z","event":"forget","old_pseudonym":"abc-...","rows_deleted":12,"server_rows_deleted":47}
```

`GET /api/fingerprint/contributions` (Phase 1 endpoint) gains a `?include_log=true` query param that interleaves audit-log entries with queue rows.

### Just-in-Time disclosure modal (frontend)

**Trigger:** `ContributionUploader._drain_one_batch()` finds rows to upload but `fingerprint_disclosure_accepted=False`. Broadcasts WS event:

```ts
type FingerprintDisclosureRequiredEvent = {
  type: "fingerprint_disclosure_required";
  data: {
    pending_count: number;
    pseudonym: string;
    server_url: string;
    fields_sent: [
      "chromaprint",
      "tmdb_id+season+episode",
      "match_confidence+source",
      "disc_content_hash",
      "client_version"
    ];
  };
};
```

**Modal behavior:** blocking — cannot be dismissed without choosing Accept or Decline. No banner fallback (Q-meta decision). Modal text:

> **Engram is about to start contributing audio fingerprints.**
>
> The local matcher just finished identifying an episode. Engram can upload a short audio fingerprint to the engram fingerprint network so future Engram users can identify the same episode in milliseconds — no subtitles, no LLM call.
>
> **What gets sent:**
> 1. The audio fingerprint (~7 KB; not the audio itself, not subtitles).
> 2. The episode it matched (TMDB ID + season + episode).
> 3. How confident we are in the match (so the network can weight your contribution).
> 4. The disc release identifier from TheDiscDB (m2ts file size hash; not the file itself).
> 5. A random per-install ID (`{pseudonym}`) — you can rotate this anytime.
>
> Your IP is not stored. You can opt out at any time in Settings. The pending **{pending_count}** contributions are queued locally; nothing has been uploaded yet.
>
> **Once contributions promote into the network's canonical layer, your individual contribution is indistinguishable from the consensus — it cannot be retroactively removed.**
>
> &nbsp;&nbsp;**[ Accept and start contributing ]** &nbsp;&nbsp; **[ Disable contributions ]**

Accept → `PUT /api/config {fingerprint_disclosure_accepted: true, fingerprint_disclosure_accepted_at: <now>}`. Uploader's next tick drains. Decline → `PUT /api/config {enable_fingerprint_contributions: false}`. Phase 1's toggle wins; nothing uploads.

## Data Flow Walkthroughs

### Flow A — First successful match after Phase 2 ships (first-time disclosure)

1. User rips a disc; existing pipeline matches a title.
2. Phase 1 hook in `matching_coordinator._match_single_file_inner()` extracts a chromaprint, queues a `FingerprintContribution` row.
3. `ContributionUploader` ticks. Reads `enable_fingerprint_contributions=True` (default) and `fingerprint_disclosure_accepted=False` (default).
4. Broadcasts `fingerprint_disclosure_required` over WebSocket. Returns without uploading.
5. Frontend `App.tsx` receives event, shows the JIT modal.
6. User clicks Accept → `PUT /api/config {fingerprint_disclosure_accepted: true}`.
7. Next uploader tick (within 5 min): decodes each row's gzip-JSON, re-encodes as zstd-varint, POSTs `/v1/contribute`.
8. Server runs anti-poison (minhash screen, exact confirm if needed), INSERTs, returns 202 with poison verdict + overlap_pct.
9. Client writes audit-log line, sets `uploaded_at=now()`.

### Flow B — Steady state

Steps 1, 2, 7, 8, 9 from Flow A. No modal. No WS event.

### Flow C — Promotion (server-side only)

1. PromotionWorker runs at 03:00 UTC.
2. For each `(tmdb_id, season, episode)` with unpromoted contributions: filter to `poison_check='pass'` AND `match_confidence>=0.70`, dedup by pseudonym, count distinct `(pseudonym × disc_content_hash)` pairs.
3. Tier assignment per the algorithm. Compute consensus fingerprint + minhash sketch on tier change. UPSERT into `episode_canonical` + `canonical_sketch`.
4. SET `promoted_at` on processed rows.

### Flow D — PackBuilder (server-side only)

1. PackBuilderWorker runs at 04:00 UTC (after PromotionWorker).
2. For each tmdb_id with changes in `episode_canonical` since last run: assemble pack, zstd-compress, R2.PUT with ETag.
3. No public read endpoint until Phase 3.

### Flow E — Forget

1. User clicks "Forget me" in Settings → frontend `POST /api/fingerprint/forget`.
2. Backend `POST` to server `/v1/forget {pseudonym}`. Server deletes from `contribution` + `contributor`; cascade deletes `overlap_observation` rows.
3. Backend DELETE WHERE `uploaded_at IS NULL` from local `fingerprint_contributions`.
4. Backend rotates `contribution_pseudonym`; resets `fingerprint_disclosure_accepted`.
5. Audit log gets `{event: "forget"}` line.
6. Future matches will queue under the new pseudonym; user sees the JIT modal again on next upload.

**Privacy invariant after forget:** the rows are gone, but canonical fingerprints already aggregated from them are not retroactively removed. The modal text documents this explicitly.

## Privacy — Exactly What Is Stored and Retained

### In `contribution` (deletable via /v1/forget)
- chromaprint hash stream (zstd-varint)
- SHA256 of decompressed fingerprint (dedupe)
- tmdb_id / season / episode
- match_confidence / match_source / client_version
- disc_content_hash (TheDiscDB m2ts MD5 — identifies a disc *release*, not the user's file)
- pseudonym (UUIDv4)
- received_at
- poison_check verdict

### Explicitly NOT in `contribution`
- IP address (Cloudflare may log briefly; our DB never)
- User agent
- Filename, path, library structure
- Audio itself
- Subtitle text
- TMDB account info or any user-identifying data

### In `contributor` (deletable via /v1/forget)
pseudonym, first_seen, last_seen, contribution_count, flagged, flag_count.

### In `episode_canonical` (NOT deletable via /v1/forget)
Consensus fingerprint blob, tier, `unique_contributors` COUNT (not identities), `mean_confidence`. The contributors' identity is gone once the row holds the aggregate.

### In `canonical_sketch` (derived, NOT deletable)
Minhash sketches of canonical fingerprints.

### In `overlap_observation` (deletable via /v1/forget — cascades via `contribution_id` FK)
max_overlap_pct + target + candidates_checked.

## Testing Strategy

### Server (`engram-fingerprint-server`, TypeScript)

- **Vitest unit tests**:
  - `anti-poison.test.ts` — synthetic fingerprints designed to overlap precisely 65%, 70%, 75% with another canonical; assert flag verdicts.
  - `promotion.test.ts` — 3-pseudonym threshold edge, conflict-blocked promotion, confidence floor.
  - `minhash.test.ts` — Jaccard estimation error band (target: 95% of estimates within ±5% of true Jaccard).
  - `schema-validation.test.ts` — every 400 path triggers correctly.

- **Miniflare integration**:
  - Spin up the Worker locally with scratch D1 + R2.
  - POST synthetic contributions, assert DB state transitions.
  - Run PromotionWorker as a one-shot cron, assert episode_canonical transitions.

- **E2E**:
  - Engram client pointed at `wrangler dev`; run `simulate/insert-disc` end-to-end; verify contribution lands with expected poison_check.

### Client (this repo)

- `tests/unit/test_contribution_uploader.py` — mocked httpx; batching, dedupe-409 success-path, exponential backoff math, 400-permanent-fail path, disclosure-required gating.
- `tests/unit/test_zstd_varint_encoder.py` — encode/decode roundtrip; size assertions (zstd-varint < 70% of gzip-JSON for representative input).
- `tests/integration/test_uploader_pipeline.py` — full path against a mocked server endpoint (httpx_mock); verify audit log writes, WS event broadcast, forget round-trip.
- `frontend/e2e/fingerprint-disclosure.spec.ts` — Playwright; simulate a match, verify modal pops, click Accept, verify next batch drains.

### Empirical validation against the spike

Re-run `spikes/chromaprint/spike.py` with a synthetic-attacker scenario: deliberately label one show's episodes as another's. Pipe the resulting fingerprints through the anti-poison check; verify >70%-overlap detection triggers consistently against the same-content target.

## Deployment

### Cloudflare resources (Phase 2 launch)

- 1 Worker (`engram-fp-prod`) with Cron Triggers `0 3 * * *` (promotion) and `0 4 * * *` (pack-builder).
- 1 D1 database (`engram-fingerprint`).
- 1 R2 bucket (`engram-fp-packs`).
- 1 custom domain — needs registration before deploy. Candidate: `fp.engram.app`. Once chosen, update `AppConfig.fingerprint_server_url` default in the client repo and the `wrangler.toml` `routes` array in the server repo.

### Migration management

D1 doesn't ship a migration framework. The server repo includes numbered SQL files in `migrations/` (e.g. `001_initial.sql`, `002_add_canonical_sketch.sql`) applied via `wrangler d1 execute` in CI on deploy. If schema complexity grows, switch to `drizzle-kit` or similar later.

### Secrets

None in v1. The server has no upstream API keys. Cloudflare-built-in rate limiting is the only authenticated-ish surface. If contribution-rate attacks materialize, add HMAC-signed pseudonyms.

### Cost forecast (v1, <100 active contributors)

- Workers Paid: $5/mo (cron triggers require Paid tier).
- D1: free tier sufficient (5M reads, 100K writes/day).
- R2: free tier sufficient (10GB storage, 10M Class A ops/mo).
- Bandwidth: R2 zero-egress; Workers responses tiny.
- **Total: ~$5/mo through Phase 2.**

### Observability

Cloudflare Worker logs to Logpush (free; writes to R2). Grafana Cloud dashboard reading from a Logpush sink, displaying:
- contributions/min
- anti-poison flag rate (`poison_check ≠ 'pass'` rate)
- p95 endpoint latency
- D1 query time histogram
- `overlap_observation.max_overlap_pct` distribution (for empirical tuning of the 70% threshold)

Phase 2 launch is gated on this dashboard existing.

## Day-1 Commit List — `engram-fingerprint-server` Bootstrap

Per the Q-meta decision, the spec includes the server-repo bootstrap as a concrete commit sequence. The implementation plan (next step) will turn each commit into a TDD step with tests.

```
commit 1  chore: wrangler init engram-fp-prod
            wrangler.toml, .gitignore, package.json (pnpm), tsconfig.json,
            .github/workflows/deploy.yml (deploy on push to main),
            README.md scaffold.

commit 2  feat(schema): D1 initial migration
            migrations/001_initial.sql with all five tables + indices.
            scripts/apply-migrations.sh wrapping wrangler d1 execute.
            CI step that runs it against a scratch DB before deploy.

commit 3  feat(types): shared types + Zod schemas
            src/types.ts: ContributionRequest, ContributionResponse, ForgetRequest, etc.
            src/schemas.ts: Zod validators that double as the 400-handler source.

commit 4  feat(util): zstd + varint + base64 codec
            src/codec.ts: encodeZstdVarint(uint32[]) → Uint8Array,
                          decodeZstdVarint(Uint8Array) → uint32[].
            Tested via vitest with roundtrip fixtures.

commit 5  feat(util): minhash sketching
            src/minhash.ts: minhash128(uint32[]) → Uint8Array (512 bytes),
                            jaccardEstimate(a, b) → number.
            Tested with known-distribution fixtures.

commit 6  feat(api): POST /v1/contribute skeleton — schema validation only
            src/routes/contribute.ts: parse → Zod validate → 202 stub.
            No DB writes yet. Tests assert all 400 paths.

commit 7  feat(api): POST /v1/contribute — DB insert + dedupe check
            Skeleton extended to actually INSERT contribution + UPSERT contributor.
            Dedupe via UNIQUE INDEX collision; 409→200 silent.

commit 8  feat(api): POST /v1/contribute — anti-poison fast path
            Minhash screen against canonical_sketch.
            Records overlap_observation always.

commit 9  feat(api): POST /v1/contribute — anti-poison exact confirm
            On screen-trigger: load canonical fingerprint, Hamming-confirm,
            set poison_check, increment contributor.flag_count if conflict.

commit 10 feat(api): POST /v1/forget
            Delete contribution + contributor for pseudonym.
            overlap_observation cascades via FK.

commit 11 feat(worker): PromotionWorker
            src/workers/promotion.ts. Cron: 0 3 * * *.
            Implements the algorithm; tested in Miniflare.

commit 12 feat(worker): PackBuilderWorker
            src/workers/pack-builder.ts. Cron: 0 4 * * *.
            R2.put per-show packs; no public endpoint yet.

commit 13 feat(ops): logpush + grafana dashboard
            Logpush config; grafana JSON committed under docs/observability/.

commit 14 chore: deploy v0.1.0 to production
            Tag, push, wrangler deploy. End of Phase 2 server bootstrap.
```

## Files to Touch — Client Repo (this repo)

### New files

- `backend/app/services/contribution_uploader.py` — the drain loop.
- `backend/app/services/zstd_varint_codec.py` — encode/decode helper (mirror of server's codec).
- `backend/app/api/fingerprint_routes.py` (or extend existing `routes.py`) — `POST /api/fingerprint/forget`.
- `backend/migrations/versions/<rev>_phase2_uploader_columns.py` — add `next_attempt_at`, `upload_error` to `fingerprint_contributions`; add `fingerprint_server_url`, `fingerprint_disclosure_accepted`, `fingerprint_disclosure_accepted_at` to `app_config`.
- `frontend/src/components/FingerprintDisclosureModal.tsx` — JIT modal.
- `backend/tests/unit/test_contribution_uploader.py`
- `backend/tests/unit/test_zstd_varint_codec.py`
- `backend/tests/integration/test_uploader_pipeline.py`
- `frontend/e2e/fingerprint-disclosure.spec.ts`

### Modified files

- `backend/app/main.py` — start `ContributionUploader` task in `lifespan`.
- `backend/app/models/app_config.py` — three new fields.
- `backend/app/models/fingerprint.py` — two new fields on `FingerprintContribution`.
- `backend/app/api/routes.py` — `?include_log=true` on `GET /api/fingerprint/contributions`.
- `backend/app/services/event_broadcaster.py` — new `broadcast_fingerprint_disclosure_required` method.
- `frontend/src/App.tsx` — wire the modal into the WS event handler.
- `frontend/src/components/ConfigWizard.tsx` — Settings panel for the existing toggle + new "Forget me" button.
- `pyproject.toml` — add `zstandard` dependency.

## Out of Scope — Deferred to Phase 3

- `GET /v1/identify` endpoint (per-window classification API).
- Public `GET /v1/pack/{tmdb_id}` endpoint.
- Client-side `ChromaprintMatcher` (consumes packs + identifies via minhash + temporal voting).
- Cascade integration (`chromaprint first → ASR fallback`) in `curator.py` / `matching_coordinator.py`.
- Lowering canonical confidence gate from 0.85 (Phase 4 tuning).
- Commentary-track detection / multi-track handling (Phase 4).
- Edition / alternate-cut UX surfacing (Phase 4).

## Verification Plan Summary

Launch Phase 2 only when:

1. Server smoke test passes: a real `engram-fp-prod` deployment receives a real client `POST /v1/contribute`, anti-poison runs, contribution lands, `GET` audit-log reflects the upload.
2. `POST /v1/forget` round-trip verified: rows deleted server-side, local queue cleared, pseudonym rotated.
3. JIT disclosure modal renders on first upload; Accept and Decline both honored.
4. PromotionWorker dry-run against a seed dataset transitions a 3-contributor episode to CANONICAL.
5. PackBuilderWorker writes a real R2 object for a CANONICAL show.
6. Grafana dashboard displays live `overlap_observation` distribution and contribution/min counters.
7. The `overlap_observation` table has accumulated ≥ 1000 rows so we have a starting distribution for Q5 empirical tuning of the 70% threshold.
8. End-to-end Playwright test passes in CI.

Two-to-four-week soak in production after Phase 2 launch before starting Phase 3, so the canonical layer can accumulate data and we can tune the 70% threshold from real distributions before Phase 3 starts consuming canonical fingerprints for identification.
