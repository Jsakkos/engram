# Fast Contribution Uploads + Server Safety Valve — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drain the local fingerprint contribution backlog in minutes (not hours) via a drain-then-idle loop with bounded concurrency, make 429 a retryable signal client-side, and add a per-pseudonym rate-limit safety valve server-side — joined by a single 429 + `Retry-After` contract.

**Architecture:** Two repos, two PRs.
- **Part A (engram client, `C:\Github\engram`):** Rewrite `ContributionUploader._upload_loop` to `while True: drain(); sleep(idle)`. `_drain()` uploads pending rows in back-to-back batches (semaphore-bounded, one shared `httpx.AsyncClient`) until the queue empties, then the loop sleeps a shortened idle poll. `_upload_one` treats 429 as transient and honors `Retry-After`.
- **Part B (server worker, `C:\Github\engram-fingerprint-server`):** Add an **optional** Cloudflare Workers Rate-Limiting binding keyed by pseudonym in `handleContribute`, returning 429 + `Retry-After` over the ceiling. Guarded by `if (env.CONTRIBUTE_RATE_LIMITER)` so local dev and the vitest workers pool stay green.
- **Seam:** Server emits 429 + `Retry-After` → client treats it as transient, waits, retries → no rows lost.

**Tech Stack:** Python 3.11 / asyncio / httpx / SQLModel (async SQLite, WAL) / pytest-asyncio (Part A). Cloudflare Workers / TypeScript / Wrangler / Vitest workers pool (Part B).

**Spec:** `docs/superpowers/specs/2026-05-30-fast-contribution-uploads-design.md` (source of truth).

---

## Validated facts (checked before writing this plan)

- **Cloudflare binding format MOVED.** The spec's `[[unsafe.bindings]]` + `type = "ratelimit"` form is the *old* one. Current Cloudflare docs (`developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit`, fetched 2026-05-30) use a dedicated top-level `[[ratelimits]]` array with a nested `[ratelimits.simple]` table. **This plan uses the current form.** The spec itself instructed: "validate exact stanza against current Cloudflare docs at implementation — the binding has moved between forms."
- **`RateLimit` type already exists** in `worker-configuration.d.ts` (global ambient): `interface RateLimit { limit(options: { key: string }): Promise<{ success: boolean }> }`. No import needed for the `Env` member.
- **`.limit()` does NOT emit `Retry-After`** — it only returns `{ success }`. We synthesize `Retry-After: "60"` ourselves (matches the 60s `period`).
- **Uploader is instantiated arg-less** at `backend/app/main.py:127` (`ContributionUploader()`), so changing the `__init__` default `poll_interval_seconds` is sufficient to change production behavior.
- **Existing client tests call `_process_batch()`** (8 call sites in `test_contribution_uploader.py`). The refactor renames the entry point to `_drain()`; those call sites get updated.
- **Engram conventions:** `uv run pytest`, `uv run ruff check/format`, pre-commit (ruff), conventional commits.
- **Server conventions:** `pnpm test` (vitest run), `pnpm exec biome check --write` scoped to changed files, lefthook pre-commit (biome) + pre-push (typecheck), conventional commits.
- **All commits in both repos** end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File Structure

### Part A — engram client (`C:\Github\engram`)
- **Modify:** `backend/app/services/contribution_uploader.py`
  - New module constant `_CONCURRENCY = 5`.
  - New module function `_retry_after_seconds(value: str | None) -> float | None`.
  - `__init__` default `poll_interval_seconds: int = 900` (was `3600`).
  - `_upload_loop` → drain-then-idle shape.
  - New `_drain(self) -> int` (replaces `_process_batch`; holds the consent gates + batch loop + shared client + semaphore).
  - New `_upload_row(self, row_id, client, server_url, semaphore) -> bool` (per-row session + semaphore wrapper).
  - `_upload_one(self, contrib, session, client, server_url)` — now takes a shared `client`; 429 branch added.
- **Modify (tests):** `backend/tests/integration/test_contribution_uploader.py`
  - Update `_process_batch()` → `_drain()` at all call sites.
  - Add tests: 429-retry, multi-batch drain, shared-client-reused, bounded-concurrency, drain-then-idle loop, default-poll-interval, `_retry_after_seconds` unit cases.

### Part B — server worker (`C:\Github\engram-fingerprint-server`)
- **Modify:** `src/routes/contribute.ts`
  - `Env` interface: add `CONTRIBUTE_RATE_LIMITER?: RateLimit;`.
  - `handleContribute`: insert the guard after `const req = parsed.data;` and before `getContributor(...)`.
- **Modify:** `wrangler.toml` — add the `[[ratelimits]]` stanza (current form).
- **Create (tests):** `test/contribute_rate_limit.test.ts` — stub-injected limiter tests.

---

# PART A — engram client

Work in `C:\Github\engram`. Branch off the current `spec/fingerprint-upload-followups` branch (it carries the spec):

```bash
git checkout spec/fingerprint-upload-followups
git checkout -b feat/fast-contribution-uploads
```

Run tests with: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`

---

### Task A1: `_retry_after_seconds` parser (pure function, TDD)

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Test: `backend/tests/integration/test_contribution_uploader.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_contribution_uploader.py`:

```python
def test_retry_after_seconds_parses_integer():
    """A plain integer Retry-After header parses to float seconds."""
    assert uploader_mod._retry_after_seconds("60") == 60.0
    assert uploader_mod._retry_after_seconds(" 30 ") == 30.0
    assert uploader_mod._retry_after_seconds("0") == 0.0


def test_retry_after_seconds_returns_none_for_unparseable():
    """Absent or non-integer (e.g. HTTP-date) Retry-After falls back to None."""
    assert uploader_mod._retry_after_seconds(None) is None
    assert uploader_mod._retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert uploader_mod._retry_after_seconds("") is None
    assert uploader_mod._retry_after_seconds("-5") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -k retry_after_seconds -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_retry_after_seconds'`

- [ ] **Step 3: Add the constant and the function**

In `backend/app/services/contribution_uploader.py`, change the constants block:

```python
_BATCH_SIZE = 50
_MAX_ATTEMPTS = 5
_UPLOAD_TIMEOUT = 30.0
_CONCURRENCY = 5
```

Then add this module-level function immediately after the constants (before `class ContributionUploader`):

```python
def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header value (integer seconds) into float seconds.

    Returns None when the header is absent or not a non-negative integer. We do
    not support the HTTP-date form — our server only ever emits integer seconds —
    so callers fall back to exponential backoff when this returns None.
    """
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except (ValueError, AttributeError):
        return None
    return float(seconds) if seconds >= 0 else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -k retry_after_seconds -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/integration/test_contribution_uploader.py
git commit -m "feat(uploader): add Retry-After header parser

Parses integer-seconds Retry-After values; returns None for absent or
non-integer values so callers fall back to exponential backoff. Groundwork
for treating 429 as a retryable signal.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A2: Drain-then-idle loop with shared client + bounded concurrency

This is the core refactor. `_process_batch` becomes `_drain`; `_upload_one` takes a shared client; a semaphore bounds concurrency.

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Test: `backend/tests/integration/test_contribution_uploader.py`

- [ ] **Step 1: Write the failing tests**

Append these tests to `backend/tests/integration/test_contribution_uploader.py`:

```python
@pytest.mark.asyncio
async def test_drain_uploads_all_rows_across_multiple_batches(setup_db, tmp_path, monkeypatch):
    """_drain loops batches until the queue empties, uploading every pending row."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    # Small batch size so 5 rows span 3 batches (2 + 2 + 1).
    monkeypatch.setattr(uploader_mod, "_BATCH_SIZE", 2)

    async with async_session() as session:
        for i in range(5):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        drained = await ContributionUploader()._drain()

        # Every row uploaded, and a single shared client was constructed once.
        assert mock_client.post.call_count == 5
        assert MockClient.call_count == 1

    assert drained == 5
    async with async_session() as session:
        rows = (
            (await session.execute(select(FingerprintContribution))).scalars().all()
        )
    assert all(r.upload_status == "success" for r in rows)


@pytest.mark.asyncio
async def test_drain_bounds_concurrency_to_semaphore(setup_db, tmp_path, monkeypatch):
    """No more than _CONCURRENCY uploads are in flight at once."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    monkeypatch.setattr(uploader_mod, "_CONCURRENCY", 3)

    async with async_session() as session:
        for i in range(12):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    in_flight = 0
    max_in_flight = 0

    async def tracking_post(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await _asyncio.sleep(0.01)
        in_flight -= 1
        return mock_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=tracking_post)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    assert max_in_flight <= 3, f"concurrency exceeded semaphore: {max_in_flight}"


@pytest.mark.asyncio
async def test_upload_loop_drains_then_idles(monkeypatch):
    """_upload_loop drains, then sleeps the idle poll interval."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    uploader = ContributionUploader(poll_interval_seconds=900)
    drain_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(uploader, "_drain", drain_mock)

    sleep_calls: list[float] = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        raise _asyncio.CancelledError  # break the loop after one iteration

    monkeypatch.setattr(uploader_mod.asyncio, "sleep", fake_sleep)

    await uploader._upload_loop()  # CancelledError is caught → returns

    drain_mock.assert_awaited_once()
    assert sleep_calls == [900]
```

Also update the existing tests that call `_process_batch()` to call `_drain()`. Replace **every** occurrence of `._process_batch()` with `._drain()` in this file (8 call sites: `test_uploader_falls_back_to_default_url_when_unset`, `test_uploader_posts_pending_contributions`, `test_uploader_marks_failed_on_4xx`, `test_uploader_posts_wire_format_v1`, `test_uploader_increments_attempts_on_5xx`, `test_uploader_skips_when_opted_out`, `test_uploader_prompts_when_disclosure_not_accepted`, `test_uploader_uploads_when_all_gates_pass`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: New drain tests FAIL with `AttributeError: 'ContributionUploader' object has no attribute '_drain'`; the renamed `_process_batch` call sites also FAIL with the same `AttributeError`.

- [ ] **Step 3: Refactor the uploader**

In `backend/app/services/contribution_uploader.py`, replace `_upload_loop`, `_process_batch`, and `_upload_one`'s signature/HTTP call. The full replacement of the loop + drain + per-row helper:

Replace `_upload_loop`:

```python
    async def _upload_loop(self) -> None:
        while True:
            try:
                drained = await self._drain()
                if drained:
                    logger.info("ContributionUploader drained {} contribution(s)", drained)
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ContributionUploader loop error — will retry next interval")
```

Replace the whole `_process_batch` method with `_drain` + `_upload_row`:

```python
    async def _drain(self) -> int:
        """Upload pending contributions in back-to-back batches until empty.

        Pre-flight privacy gate — nothing leaves the machine unless BOTH hold:
          1. the user has not opted out (enable_fingerprint_contributions),
          2. the user has accepted the disclosure (fingerprint_disclosure_accepted).
        The server URL falls back to DEFAULT_FINGERPRINT_SERVER_URL when unset, so
        existing installs (NULL column) still engage. If data is queued but consent
        is missing, fire the JIT disclosure event — and upload nothing.

        Returns the number of rows successfully uploaded this drain.
        """
        cfg = await get_config()
        if not cfg.enable_fingerprint_contributions:
            logger.debug("fingerprint contributions disabled by user; skipping upload")
            return 0

        # NULL/blank stored URL means "use the default network base origin".
        server_url = cfg.fingerprint_server_url or DEFAULT_FINGERPRINT_SERVER_URL

        drained = 0
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        # One client for the whole drain → HTTP keep-alive across every batch/row.
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
            while True:
                # Collect IDs in a short-lived session so the connection is
                # released before any per-row upload work.
                async with async_session() as session:
                    stmt = (
                        select(FingerprintContribution.id)
                        .where(FingerprintContribution.upload_status.is_(None))
                        .where(FingerprintContribution.upload_attempts < _MAX_ATTEMPTS)
                        .limit(_BATCH_SIZE)
                    )
                    row_ids = (await session.execute(stmt)).scalars().all()

                if not row_ids:
                    break

                if not cfg.fingerprint_disclosure_accepted:
                    # Data is queued but the user hasn't consented yet. Prompt; don't upload.
                    logger.info(
                        "%d fingerprint contribution(s) queued but disclosure not accepted; "
                        "prompting user",
                        len(row_ids),
                    )
                    await self._notify_disclosure_required(
                        len(row_ids), cfg.contribution_pseudonym, server_url
                    )
                    break

                # One row failing must not abort the batch.
                results = await asyncio.gather(
                    *(
                        self._upload_row(row_id, client, server_url, semaphore)
                        for row_id in row_ids
                    ),
                    return_exceptions=True,
                )
                for r in results:
                    if r is True:
                        drained += 1
                    elif isinstance(r, Exception):
                        logger.warning("Contribution upload task errored: {}", r)

        return drained

    async def _upload_row(
        self,
        row_id: int,
        client: httpx.AsyncClient,
        server_url: str,
        semaphore: asyncio.Semaphore,
    ) -> bool:
        """Upload one queued row under the concurrency semaphore.

        Uses its own short-lived DB session so each row's status update commits
        independently (the engram DB is WAL-mode, so concurrent writers are fine).
        Returns True when the row was uploaded successfully.
        """
        async with semaphore:
            async with async_session() as session:
                row = await session.get(FingerprintContribution, row_id)
                if row is None:
                    return False  # deleted between the ID query and now
                await self._upload_one(row, session, client=client, server_url=server_url)
                return row.upload_status == "success"
```

Change the `_upload_one` signature to accept the shared `client` and use it instead of constructing one per row. Replace its signature line:

```python
    async def _upload_one(
        self,
        contrib: FingerprintContribution,
        session,
        client: httpx.AsyncClient,
        server_url: str,
    ) -> None:
```

And replace the `async with httpx.AsyncClient(...) as client:` block inside the retry loop with a direct call on the passed-in client. Specifically, change:

```python
            try:
                async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                    resp = await client.post(
                        f"{server_url.rstrip('/')}/v1/contribute",
                        json=payload,
                    )
                    resp.raise_for_status()
```

to:

```python
            try:
                resp = await client.post(
                    f"{server_url.rstrip('/')}/v1/contribute",
                    json=payload,
                )
                resp.raise_for_status()
```

(Leave the 429/4xx/5xx handling exactly as-is in this task — Task A3 adds the 429 branch.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: PASS (all tests green, including the renamed `_drain` call sites, multi-batch, concurrency, and loop-idle tests).

- [ ] **Step 5: Lint + format**

Run: `cd backend && uv run ruff check app/services/contribution_uploader.py tests/integration/test_contribution_uploader.py && uv run ruff format app/services/contribution_uploader.py tests/integration/test_contribution_uploader.py`
Expected: no errors; formatting clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/integration/test_contribution_uploader.py
git commit -m "feat(uploader): drain-then-idle loop with bounded concurrency

Replace one-batch-then-sleep-an-hour with a drain loop: upload pending rows
in back-to-back batches until the queue empties, then sleep the idle poll.
Within a drain, upload _CONCURRENCY (5) rows at once behind an asyncio
Semaphore, sharing one httpx.AsyncClient (HTTP keep-alive). Consent and
disclosure gates move to the top of the drain, unchanged in behavior. One
row failing no longer aborts the batch (gather return_exceptions).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A3: 429 is retryable, honoring `Retry-After`

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Test: `backend/tests/integration/test_contribution_uploader.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_contribution_uploader.py`:

```python
@pytest.mark.asyncio
async def test_uploader_retries_on_429_then_succeeds(setup_db, tmp_path, monkeypatch):
    """429 is transient: the row retries (honoring Retry-After) and can still succeed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    # First call → 429 with Retry-After: 30; second call → success.
    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={"Retry-After": "30"}),
    )
    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()

    sleep_durations: list[float] = []

    async def fake_sleep(duration):
        sleep_durations.append(duration)

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # Not marked permanently failed; eventually succeeded after retrying.
    assert refreshed.upload_status == "success"
    # The 429 backoff honored Retry-After (30s), not the 2**0 = 1s exponential default.
    assert 30.0 in sleep_durations


@pytest.mark.asyncio
async def test_uploader_429_falls_back_to_exponential_without_retry_after(
    setup_db, tmp_path, monkeypatch
):
    """429 without a Retry-After header falls back to exponential backoff (not permanent)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=2,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    # Always 429 with no Retry-After → exhausts the attempt budget, then fails.
    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={}),
    )

    with patch("httpx.AsyncClient") as MockClient, patch("asyncio.sleep", AsyncMock()):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=rate_limited)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # 429 is transient: it consumes the budget like a 5xx, not an instant 4xx fail.
    assert refreshed.upload_status == "failed"
    assert refreshed.upload_attempts == _MAX_ATTEMPTS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -k 429 -v`
Expected: FAIL — current code marks 429 as permanent (`upload_status == "failed"` after one attempt, `upload_attempts == 1`), so the success and budget-exhaustion assertions fail.

- [ ] **Step 3: Add the 429 branch and Retry-After backoff**

In `_upload_one`, change the retry loop so each attempt computes a `backoff` and the `HTTPStatusError` handler special-cases 429. Replace the loop body's opening and the `except httpx.HTTPStatusError` block:

```python
        # Honour the lifetime attempt cap: prior failures consumed some budget.
        remaining = _MAX_ATTEMPTS - contrib.upload_attempts
        for attempt in range(remaining):
            backoff: float = 2**attempt
            try:
                resp = await client.post(
                    f"{server_url.rstrip('/')}/v1/contribute",
                    json=payload,
                )
                resp.raise_for_status()

                contrib.upload_status = "success"
                contrib.uploaded_at = datetime.now(UTC)
                await session.commit()
                self._append_audit_log(contrib)
                logger.info(
                    f"Uploaded contribution {contrib.id} "
                    f"(tmdb={contrib.tmdb_id} s{contrib.season}e{contrib.episode})"
                )
                return

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    # Rate limited — transient. Honor Retry-After; else exponential.
                    backoff = _retry_after_seconds(e.response.headers.get("Retry-After")) or backoff
                    contrib.upload_attempts += 1
                    await session.commit()
                    logger.warning(
                        f"Contrib {contrib.id}: rate limited (429), "
                        f"backoff {backoff}s, attempt {attempt + 1}"
                    )
                elif 400 <= status < 500:
                    contrib.upload_status = "failed"
                    contrib.upload_error_msg = f"HTTP {status} (permanent)"
                    contrib.upload_attempts += 1
                    await session.commit()
                    logger.warning(
                        f"Contrib {contrib.id}: permanent HTTP {status}; marking failed"
                    )
                    return
                else:
                    # 5xx — transient, fall through to retry
                    contrib.upload_attempts += 1
                    await session.commit()
                    logger.warning(
                        f"Contrib {contrib.id}: transient HTTP {status}, attempt {attempt + 1}"
                    )

            except httpx.HTTPError as e:
                contrib.upload_attempts += 1
                await session.commit()
                logger.warning(f"Contrib {contrib.id}: network error, attempt {attempt + 1}: {e}")

            if attempt < remaining - 1:
                await asyncio.sleep(backoff)
```

(The trailing "Exhausted retries" block stays unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: PASS — including the two new 429 tests and the still-passing `test_uploader_marks_failed_on_4xx` (422 stays permanent) and `test_uploader_increments_attempts_on_5xx` (503 stays transient).

- [ ] **Step 5: Lint + format + full uploader suite**

Run: `cd backend && uv run ruff check app/services/contribution_uploader.py && uv run ruff format app/services/contribution_uploader.py tests/integration/test_contribution_uploader.py && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: clean lint, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/integration/test_contribution_uploader.py
git commit -m "fix(uploader): treat 429 as transient, honor Retry-After

Previously every 4xx (including 429) was marked a permanent failure, so a
rate-limited row was lost. Now 429 increments attempts and retries within
_MAX_ATTEMPTS, sleeping the Retry-After header value (falling back to
exponential backoff when absent). Other 4xx stay permanent; 5xx stays
transient. This is the client half of the 429 contract with the server.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A4: Shorten default idle poll interval 3600 → 900

**Files:**
- Modify: `backend/app/services/contribution_uploader.py`
- Test: `backend/tests/integration/test_contribution_uploader.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_contribution_uploader.py`:

```python
def test_uploader_default_poll_interval_is_900():
    """The default idle poll interval is 15 minutes (900s), not an hour."""
    assert ContributionUploader().poll_interval == 900
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -k default_poll_interval -v`
Expected: FAIL — `assert 3600 == 900`

- [ ] **Step 3: Change the default**

In `backend/app/services/contribution_uploader.py`, change the `__init__` signature:

```python
    def __init__(self, poll_interval_seconds: int = 900) -> None:
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -k default_poll_interval -v`
Expected: PASS

- [ ] **Step 5: Run the full uploader suite**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/contribution_uploader.py backend/tests/integration/test_contribution_uploader.py
git commit -m "feat(uploader): reduce default idle poll interval to 15 min

Drain-then-idle already removes the backlog wait, so the idle interval now
only governs steady-state pickup of new rips. 900s (was 3600s) gives steadier
pickup without busy-polling.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A5: Part A regression sweep

- [ ] **Step 1: Run the touched test module plus a broad integration sweep**

Run: `cd backend && uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: all pass.

- [ ] **Step 2: Run the full integration suite to catch interaction regressions**

Run: `cd backend && uv run pytest tests/integration -q`
Expected: pass (note any pre-existing known failures from MEMORY.md, e.g. `test_movie_ambiguous_rip_first_workflow` staging-cleanup race — that's unrelated to this change).

- [ ] **Step 3: Verify pre-commit hooks are clean on the staged diff**

Run: `pre-commit run --files backend/app/services/contribution_uploader.py backend/tests/integration/test_contribution_uploader.py`
Expected: ruff + ruff-format pass.

---

# PART B — server worker

Work in `C:\Github\engram-fingerprint-server`. **This is a separate repo with its own branch + PR.** The `main` branch auto-deploys to production, so branch first (per MEMORY: SEPARATE server repo, main auto-deploys → branch before changes):

```bash
cd C:\Github\engram-fingerprint-server
git checkout main
git checkout -b feat/per-pseudonym-rate-limit
```

Run tests with: `pnpm test` (vitest run). Lint with: `pnpm exec biome check --write src test scripts dashboard`.

---

### Task B1: Optional rate-limit guard in `handleContribute` (TDD via stub injection)

The real binding isn't available in miniflare/vitest, so tests inject a stub `CONTRIBUTE_RATE_LIMITER` into a spread of the runtime `env`.

**Files:**
- Modify: `src/routes/contribute.ts` (`Env` interface + guard)
- Create: `test/contribute_rate_limit.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `test/contribute_rate_limit.test.ts`:

```typescript
import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { encodeZstdVarint, initCodec } from "../src/codec";
import { handleContribute } from "../src/routes/contribute";

beforeAll(async () => {
  await initCodec();
});

async function makeBody(overrides: Record<string, unknown> = {}) {
  const encoded = await encodeZstdVarint([1, 2, 3, 4, 5]);
  const b64 = btoa(String.fromCharCode(...encoded));
  return {
    wire_format_version: 1,
    pseudonym: "11111111-1111-4111-8111-111111111111",
    tmdb_id: 12345,
    season: 1,
    episode: 1,
    fingerprint_b64: b64,
    fingerprint_sha256_b64: btoa(String.fromCharCode(...new Uint8Array(32))),
    disc_content_hash_b64: null,
    match_confidence: 0.91,
    match_source: "engram_asr",
    client_version: "engram/0.9.2",
    ...overrides,
  };
}

function makeRequest(body: object): Request {
  return new Request("https://example.com/v1/contribute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const allow: RateLimit = { limit: async () => ({ success: true }) };
const block: RateLimit = { limit: async () => ({ success: false }) };

describe("POST /v1/contribute — per-pseudonym rate limit", () => {
  it("proceeds when the limiter binding is absent", async () => {
    const pseudonym = "a0000000-0000-4000-8000-000000000001";
    const noLimiterEnv = { ...env };
    delete (noLimiterEnv as { CONTRIBUTE_RATE_LIMITER?: RateLimit }).CONTRIBUTE_RATE_LIMITER;
    const res = await handleContribute(makeRequest(await makeBody({ pseudonym })), noLimiterEnv);
    expect(res.status).toBe(202);
  });

  it("proceeds when under the limit", async () => {
    const pseudonym = "a0000000-0000-4000-8000-000000000002";
    const okEnv = { ...env, CONTRIBUTE_RATE_LIMITER: allow };
    const res = await handleContribute(makeRequest(await makeBody({ pseudonym })), okEnv);
    expect(res.status).toBe(202);
  });

  it("returns 429 + Retry-After and writes nothing when over the limit", async () => {
    const pseudonym = "a0000000-0000-4000-8000-000000000003";
    const blockedEnv = { ...env, CONTRIBUTE_RATE_LIMITER: block };
    const res = await handleContribute(makeRequest(await makeBody({ pseudonym })), blockedEnv);

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("60");

    // No contributor row was upserted → no decode/insert work happened.
    const contributor = await env.DB.prepare("SELECT * FROM contributor WHERE pseudonym = ?")
      .bind(pseudonym)
      .first();
    expect(contributor).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm test contribute_rate_limit`
Expected: the over-limit test FAILS — the guard doesn't exist yet, so the request proceeds to 202 and a contributor row IS written (so `expect(res.status).toBe(429)` fails). The absent/under tests pass incidentally (no guard = proceeds), which is fine.

- [ ] **Step 3: Add the optional `Env` member and the guard**

In `src/routes/contribute.ts`, add the optional member to the `Env` interface (bottom of the file):

```typescript
export interface Env {
  DB: D1Database;
  PACKS: R2Bucket;
  POISON_CONFLICT_THRESHOLD: string;
  IDENTIFY_MIN_SCORE?: string;
  ALLOW_DEV_SEED?: string;
  CONTRIBUTE_RATE_LIMITER?: RateLimit;
}
```

In `handleContribute`, insert the guard immediately after `const req = parsed.data;` and before `const contributor = await getContributor(...)`:

```typescript
  const req = parsed.data;

  // Per-pseudonym safety valve: a generous circuit-breaker against runaway
  // loops / casual hammering. Optional binding — absent in local dev and the
  // vitest workers pool, so those paths skip it. Placed after schema validation
  // (so we have req.pseudonym) and before the expensive decode/minhash/insert.
  if (env.CONTRIBUTE_RATE_LIMITER) {
    const { success } = await env.CONTRIBUTE_RATE_LIMITER.limit({ key: req.pseudonym });
    if (!success) {
      return new Response("rate limited", {
        status: 429,
        headers: { "Retry-After": "60" },
      });
    }
  }

  const contributor = await getContributor(env.DB, req.pseudonym);
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pnpm test contribute_rate_limit`
Expected: all 3 pass.

- [ ] **Step 5: Verify the existing contribute tests still pass (guard is a no-op when absent)**

Run: `pnpm test contribute`
Expected: `contribute_validation`, `contribute_db`, and `contribute_rate_limit` all pass.

- [ ] **Step 6: Lint + typecheck**

Run: `pnpm exec biome check --write src test scripts dashboard && pnpm typecheck`
Expected: biome clean (note `RateLimit` is a global ambient type from `worker-configuration.d.ts`; no import needed). typecheck passes.

- [ ] **Step 7: Commit**

```bash
git add src/routes/contribute.ts test/contribute_rate_limit.test.ts
git commit -m "feat(contribute): per-pseudonym rate-limit safety valve

Optional Cloudflare Rate-Limiting binding keyed by pseudonym. When over the
ceiling, return 429 + Retry-After before any decode/minhash/insert work.
Guarded by if (env.CONTRIBUTE_RATE_LIMITER) so local wrangler dev and the
vitest workers pool — which don't provision it — keep working. Tests inject
a stub limiter into env (absent -> proceeds; over-limit -> 429, no DB write).

This is a circuit-breaker, not a security control; the anti-poison screen +
shadowban remain the abuse defense.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task B2: Wire the binding in `wrangler.toml` (current Cloudflare form)

**Files:**
- Modify: `wrangler.toml`

- [ ] **Step 1: Add the `[[ratelimits]]` stanza**

In `wrangler.toml`, after the `[[r2_buckets]]` block (before `[vars]`), add:

```toml
# Per-pseudonym rate-limit safety valve for POST /v1/contribute. Ceiling is set
# ABOVE an honest client's peak (client _CONCURRENCY=5 ≈ 300–600 req/min) so real
# users never hit it — the limiter only bites on pathological rates. Best-effort
# per-colo counting; a circuit-breaker, not a security control.
# Binding is read as optional in code (if (env.CONTRIBUTE_RATE_LIMITER)).
[[ratelimits]]
name = "CONTRIBUTE_RATE_LIMITER"
namespace_id = "1001"

  [ratelimits.simple]
  limit = 600
  period = 60
```

> **Note for the implementer:** This is the *current* Cloudflare form (validated against `developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit` on 2026-05-30), NOT the spec's older `[[unsafe.bindings]]` form. `period` must be `10` or `60`.

- [ ] **Step 2: Verify the vitest workers pool still boots with the stanza**

The vitest config points at this `wrangler.toml` (`wrangler: { configPath: "./wrangler.toml" }`), so miniflare parses the new stanza on startup. Confirm the suite still runs:

Run: `pnpm test`
Expected: the full suite boots and passes. If miniflare provisions a local no-op/real limiter, `env.CONTRIBUTE_RATE_LIMITER` may now be defined in tests — that's fine: the injected-stub tests don't depend on the runtime binding's presence (the absent-case test deletes the key explicitly), and existing SELF.fetch tests stay under the 600 ceiling so they still get `success: true`.

> **If `pnpm test` fails to boot** because this `@cloudflare/vitest-pool-workers` version (0.12.x) rejects the `[[ratelimits]]` stanza: that is the one real risk in Part B. Investigate with `systematic-debugging` — do NOT silently drop the stanza. Options in order of preference: (a) upgrade `@cloudflare/vitest-pool-workers`/`wrangler` to a version that simulates the binding; (b) if upgrade is out of scope, the guard + stub tests (Task B1) already prove the code path, and the stanza can ship while tests reference it only via injection — but first confirm whether miniflare errors or merely ignores it.

- [ ] **Step 3: Typecheck (regenerates worker types from the new binding)**

Run: `pnpm typecheck`
Expected: passes. (`pretypecheck` runs `wrangler types`, which now sees the `CONTRIBUTE_RATE_LIMITER` binding.)

- [ ] **Step 4: Commit**

```bash
git add wrangler.toml
git commit -m "feat(contribute): declare CONTRIBUTE_RATE_LIMITER binding (600/60s)

Per-pseudonym ceiling, current Cloudflare [[ratelimits]] form (validated
against current docs; the spec's [[unsafe.bindings]] form is the older one).
600/60s sits above an honest client's peak so real users never hit it.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# PART C — the 429 seam, end to end

The 429 + `Retry-After` contract is the single seam binding the two repos. Verify both halves line up.

- [ ] **Step 1: Confirm the server's 429 shape matches what the client parses**

- Server emits: `status: 429`, header `Retry-After: "60"` (Task B1).
- Client parses: `_retry_after_seconds("60") == 60.0` (Task A1), and 429 increments attempts + sleeps that value rather than failing permanently (Task A3).
- Cross-check the header name casing: server sets `"Retry-After"`; client reads `e.response.headers.get("Retry-After")`. httpx response headers are case-insensitive, so casing is safe either way.

- [ ] **Step 2: Confirm directionality of the rollout note**

Per the spec's rollout section: a client that mis-handles 429 must never reach production before the server can emit 429. Since the client now *correctly* handles 429 (Task A3) and the server ceiling is generous, ship order is low-risk. Record in each PR description that the client-side 429 handling (engram PR) is safe to merge first or simultaneously with the server PR.

- [ ] **Step 3: (Optional manual end-to-end)** With both branches checked out, run the server locally with a tiny temporary ceiling and point the client at it:
  - Server: temporarily set `limit = 2, period = 10` in `wrangler.toml`, `pnpm dev`.
  - Client: set `fingerprint_server_url` to the local `wrangler dev` origin, queue >2 contributions, run a drain.
  - Expect: the 3rd+ request gets 429, the client logs `rate limited (429), backoff ...`, and after the window the rows upload successfully (none marked `failed`). Revert the temporary ceiling before committing.

---

## Finishing up

- [ ] Push each branch and open its PR (separate PRs, one per repo). Use `superpowers:finishing-a-development-branch`.
- [ ] Engram PR body: summarize drain-then-idle + 429-retry + 900s idle; link the spec. Note CI must pass the 9 named checks (MEMORY: merge-protection gates) and that `review`/`claude` checks are not required.
- [ ] Server PR body: summarize the optional rate-limit binding; note `main` auto-deploys on merge (so the limiter goes live immediately, but is additive/optional and changes nothing for clients under the ceiling).

---

## Self-Review (run against the spec)

**Spec coverage:**
- Spec A1 (drain-then-idle) → Task A2 (`_drain` + reshaped `_upload_loop`). ✓
- Spec A1 (consent/disclosure gates move to top of drain, unchanged) → Task A2 (`_drain` keeps enable gate at top, disclosure gate fires once when rows queued). Existing `test_uploader_skips_when_opted_out` / `test_uploader_prompts_when_disclosure_not_accepted` retargeted to `_drain`. ✓
- Spec A2 (bounded concurrency + shared client) → Task A2 (`asyncio.Semaphore(_CONCURRENCY)`, one `httpx.AsyncClient`); tests `test_drain_bounds_concurrency_to_semaphore`, `test_drain_uploads_all_rows_across_multiple_batches` (asserts `MockClient.call_count == 1`). ✓
- Spec A3 (429 transient, honor Retry-After, retry within budget; other 4xx permanent; 5xx transient) → Tasks A1 + A3; tests `test_uploader_retries_on_429_then_succeeds`, `test_uploader_429_falls_back_to_exponential_without_retry_after`, plus retained `test_uploader_marks_failed_on_4xx` (422) and `test_uploader_increments_attempts_on_5xx`. ✓
- Spec A (idle interval 3600 → 900) → Task A4. ✓
- Spec B (per-pseudonym binding, optional, after schema validation before decode, 429 + Retry-After, Env optional member, ceiling 600/60) → Tasks B1 + B2. ✓
- Spec B (validate wrangler stanza against current docs) → done in this plan (current `[[ratelimits]]` form). ✓
- Spec error handling (one row failing must not abort batch via gather return_exceptions) → Task A2. ✓
- Spec testing (client + server matrices) → covered across A1–A4, B1. ✓
- Spec rollout/back-compat → Part C Step 2. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to" placeholders; every code step shows complete code. ✓

**Type/name consistency:** `_drain` / `_upload_row` / `_upload_one(..., client, server_url)` / `_retry_after_seconds` / `_CONCURRENCY` used identically across tasks. Server `CONTRIBUTE_RATE_LIMITER` / `RateLimit` / `Retry-After: "60"` consistent across B1, B2, C. ✓
