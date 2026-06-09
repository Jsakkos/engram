# Truncated-Rip Fast-Fail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a truncated/aborted disc rip from wedging a job in `MATCHING` for hours; recognize a stable-but-undersized ripped file as an incomplete rip within ~90 s and route that title to review with a clear reason.

**Architecture:** The fix is localized to `MatchingCoordinator._wait_for_file_ready`, which currently cannot distinguish "rip still writing slowly" from "rip aborted, file stable at 12% of the scanned size." Today it resets its stability counter whenever a stable file is below 85% of the disc-scan size estimate, looping until a size-proportional timeout (~4.3 h for an 8 GB title). We make the waiter return an explicit three-way outcome (`READY` / `TRUNCATED` / `TIMEOUT`), bound the undersized-stable wait to a short grace window, and dispatch the outcome through the existing `_handle_match_failure` convention. A separate, smaller follow-up (Part B) fixes the unrelated cosmetic issue that the UI shows the disc-scan size *estimate* rather than the real ripped file size.

**Tech Stack:** Python 3.11, FastAPI, async SQLModel/SQLite, pytest (`uv run pytest`), `unittest.mock`. Frontend (Part B only): React + TypeScript.

---

## Background — Root Cause (from the job #99 investigation, 2026-06-09)

Live evidence from a real stuck job (Breaking Bad S2 D1, job #99):

- MakeMKV hit an **uncorrectable disc read error** ~1.04 GB into title #2's stream
  (`MSG:2003 Error 'Scsi error - MEDIUM ERROR:L-EC UNCORRECTABLE ERROR' ... '/BDMV/STREAM/00275.m2ts' at offset '1042612224'`, 12 retries), aborted that title at exactly **960 MiB** (`1,006,632,960` bytes), and moved on to rip t03/t04 fully.
- The matcher's [`_wait_for_file_ready`](../../../backend/app/services/matching_coordinator.py) entered its poll loop. The file is **stable** (frozen since 09:15:51) but only ~12% of the `8199999999`-byte disc-scan estimate, so `size_matches_expected` (`ratio >= 0.85`) is `False`. The loop's `elif stable_count >= required_stable and not size_matches_expected:` branch **resets `stable_count = 0` and keeps waiting**, treating an aborted rip as "still writing." Its dynamic timeout is `expected_MB × 2 = 15640 s ≈ 4.3 h`.
- During that whole wait the loop broadcasts `TitleState.RIPPING` over the WebSocket (the DB row is actually `QUEUED`), so the dashboard shows a spinning **RIPPING** badge — the user-reported symptom.
- The stale-job watchdog cannot help: [`_watchdog_check_job`](../../../backend/app/services/job_manager.py) explicitly skips a `MATCHING` job that still `_has_pending_match_work` (t02 is `QUEUED`), refreshing its clock and deferring to "the per-track timeout in the matcher" — which *is* the 4.3 h wait. The safety net delegates straight to the stuck loop.

**Conclusion:** the defect is that "file size below a fraction of an unreliable scan estimate" is used as a proxy for "rip still in progress." A truncated-and-stable file is indistinguishable from a slowly-growing one under that proxy, so the loop burns the full size-proportional timeout. The fix must give the waiter a *time-bounded* way to conclude the rip has stopped.

---

## File Structure

**Part A — fast-fail (backend only):**

- Modify: `backend/app/services/matching_coordinator.py`
  - Add `FileWaitResult` enum + three tuning constants + `INCOMPLETE_RIP_MESSAGE`.
  - Rewrite `_wait_for_file_ready` to return `FileWaitResult` and bound the undersized-stable wait; broadcast `QUEUED` (not `RIPPING`) while waiting.
  - Add `_handle_file_wait_result` dispatcher and call it from the match flow (replacing the inline `if not file_ready:` block around line 679).
- Test: `backend/tests/unit/test_wait_for_file_ready.py` (new).

**Part B — real-size display (follow-up, separate PR/plan):** spans `backend/app/models/disc_job.py`, an Alembic migration, `backend/app/api/routes.py` (`build_job_detail`), and the frontend DiscCard. Designed below but **not** broken into executable tasks here.

---

## Part A — Tasks

### Task 1: Make `_wait_for_file_ready` return a bounded three-way outcome

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (add enum/constants near the top of the module, after the existing imports; rewrite `_wait_for_file_ready`, currently at lines 1433–1575)
- Test: `backend/tests/unit/test_wait_for_file_ready.py` (create)

- [ ] **Step 1: Add the import, enum, and constants**

At the top of `matching_coordinator.py`, ensure `StrEnum` is importable (add `from enum import StrEnum` to the imports if not already present). Then add, just below the imports (above the `MatchingCoordinator` class):

```python
class FileWaitResult(StrEnum):
    """Outcome of waiting for a ripped title file to finalize on disk."""

    READY = "ready"  # Complete, or stable at a plausible size — safe to match.
    TRUNCATED = "truncated"  # Stable far below the scanned size — an aborted rip.
    TIMEOUT = "timeout"  # Never stabilized within the timeout budget.


# A ripped file that has stopped growing for this long is treated as final by
# the ripper even if it is smaller than the disc-scan size estimate. Comfortably
# longer than MakeMKV's longest mid-title write pause (a few seconds while it
# retries a marginal sector) but a tiny fraction of the old size-proportional
# timeout that let a truncated title wedge a job for hours.
TRUNCATED_STABLE_GRACE_SECONDS = 90.0

# Fast path: once a stable file reaches this fraction of the scanned size we
# accept it as complete without waiting out the grace window.
READY_SIZE_RATIO = 0.85

# A file that stops growing below this fraction of the scanned size is judged a
# truncated/aborted rip (e.g. an uncorrectable disc read error) rather than a
# legitimately small title.
TRUNCATED_SIZE_RATIO = 0.5

INCOMPLETE_RIP_MESSAGE = (
    "Incomplete rip: the ripped file is far smaller than the disc-scan size "
    "estimate, which usually means an uncorrectable disc read error aborted the "
    "rip. Clean the disc and re-rip this title."
)
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/unit/test_wait_for_file_ready.py`:

```python
"""Unit tests for MatchingCoordinator._wait_for_file_ready truncation handling.

A rip aborted by an uncorrectable disc read error leaves a stable file far below
the scanned size estimate. The waiter must recognize that as a truncated rip and
bail out quickly (FileWaitResult.TRUNCATED) instead of spinning to the
size-proportional timeout (which wedged job #99 / Breaking Bad S2 t02 for ~4.3h).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services import matching_coordinator as mc
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import FileWaitResult, MatchingCoordinator
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    monkeypatch.setattr(mc, "async_session", _unit_session_factory)


def _make_coord() -> MatchingCoordinator:
    broadcaster = MagicMock()
    return MatchingCoordinator(broadcaster, JobStateMachine(broadcaster))


def _patch_config(monkeypatch, *, poll=0.01, stable=2, timeout=5.0):
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(
            return_value=SimpleNamespace(
                ripping_file_poll_interval=poll,
                ripping_stability_checks=stable,
                ripping_file_ready_timeout=timeout,
            )
        ),
    )


async def _seed_title(expected_bytes: int) -> int:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=2,
            disc_number=1,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            file_size_bytes=expected_bytes,
            state=TitleState.QUEUED,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return title.id


@pytest.mark.asyncio
async def test_ready_when_file_matches_expected(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 1000)  # 100% of expected
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.READY


@pytest.mark.asyncio
async def test_truncated_when_stable_far_below_expected(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    # The t02 case, scaled: expected ~8.2 GB, real file tiny → ratio ~0.
    title_id = await _seed_title(expected_bytes=8_200_000_000)
    f = tmp_path / "t02.mkv"
    f.write_bytes(b"x" * 1000)
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.TRUNCATED


@pytest.mark.asyncio
async def test_ready_after_grace_when_modestly_undersized(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 600)  # 60% — smaller than projected but NOT truncated
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.READY


@pytest.mark.asyncio
async def test_timeout_when_file_never_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    _patch_config(monkeypatch, timeout=0.05)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "missing.mkv"  # never created
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=0.05)
    assert result == FileWaitResult.TIMEOUT


@pytest.mark.asyncio
async def test_wait_broadcasts_queued_not_ripping(tmp_path, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_title_update", spy)
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 600)  # undersized → loops a few times before grace
    coord = _make_coord()
    await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    states = [c.args[2] for c in spy.call_args_list if len(c.args) > 2]
    assert states, "expected at least one title broadcast during the wait"
    assert TitleState.RIPPING.value not in states
    assert TitleState.QUEUED.value in states
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_wait_for_file_ready.py -v`
Expected: `ImportError`/`AttributeError` — `FileWaitResult` does not exist yet, and the existing `_wait_for_file_ready` returns `bool`, so `test_truncated_*` and the `READY` assertions fail.

- [ ] **Step 4: Rewrite `_wait_for_file_ready`**

Replace the entire method body (currently lines 1433–1575) with:

```python
    async def _wait_for_file_ready(
        self,
        file_path: Path,
        title_id: int,
        job_id: int,
        timeout: float | None = None,
    ) -> FileWaitResult:
        """Wait until a ripped file is finalized on disk.

        Returns READY when the file is complete (or has stopped growing at a
        plausible size), TRUNCATED when it has clearly stopped far below the
        scanned size (an aborted rip), or TIMEOUT if it never stabilized.
        """
        from app.services.config_service import get_config

        config = await get_config()
        check_interval = config.ripping_file_poll_interval
        required_stable = config.ripping_stability_checks

        # Expected (disc-scan estimate) size from the DB.
        expected_size = 0
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if title and title.file_size_bytes:
                expected_size = title.file_size_bytes

        if timeout is None:
            if expected_size > 0:
                base_timeout = (expected_size / (1024 * 1024)) * 2
                timeout = max(config.ripping_file_ready_timeout, base_timeout)
            else:
                timeout = config.ripping_file_ready_timeout

        # Consecutive stable polls after which a still-undersized file is judged
        # final (the rip has stopped), bounded well under `timeout`.
        grace_checks = max(
            required_stable,
            int(TRUNCATED_STABLE_GRACE_SECONDS / check_interval) + 1,
        )

        last_size = -1
        stable_count = 0
        start = time.monotonic()

        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): waiting for file to finish "
            f"writing: {file_path.name} (expected ~{expected_size / 1024 / 1024:.0f} MB, "
            f"timeout {timeout:.0f}s)"
        )

        def _readable() -> bool:
            try:
                with open(file_path, "rb") as _f:
                    _f.read(1)
                return True
            except PermissionError:
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): size stable but file "
                    f"still locked ({file_path.name}) — waiting..."
                )
                return False

        async def _broadcast(progress: float, actual: int) -> None:
            await ws_manager.broadcast_title_update(
                job_id,
                title_id,
                TitleState.QUEUED.value,
                match_stage="waiting_for_file",
                match_progress=progress,
                expected_size_bytes=expected_size,
                actual_size_bytes=actual,
            )

        while time.monotonic() - start < timeout:
            if not file_path.exists():
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file not yet on disk, "
                    f"waiting... ({file_path.name})"
                )
                await _broadcast(0.0, 0)
                await asyncio.sleep(check_interval)
                continue

            try:
                current_size = file_path.stat().st_size
            except OSError as e:
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): cannot stat file ({e}), retrying..."
                )
                await asyncio.sleep(check_interval)
                continue

            if current_size > 0 and current_size == last_size:
                stable_count += 1
                size_ratio = current_size / expected_size if expected_size > 0 else 1.0

                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file size stable "
                    f"({current_size / 1024 / 1024:.0f} MB) — check {stable_count}/{required_stable}"
                    + (
                        f" — {size_ratio * 100:.1f}% of expected {expected_size / 1024 / 1024:.0f} MB"
                        if expected_size > 0
                        else ""
                    )
                )

                # Fast path: complete-enough and briefly stable.
                if stable_count >= required_stable and size_ratio >= READY_SIZE_RATIO:
                    if _readable():
                        logger.info(
                            f"[MATCH] Title {title_id} (Job {job_id}): file ready "
                            f"({current_size / 1024 / 1024:.0f} MB, stable for "
                            f"{stable_count * check_interval:.0f}s): {file_path.name}"
                        )
                        return FileWaitResult.READY
                    stable_count = 0

                # Slow path: the file has stopped growing for the whole grace
                # window but never reached the scanned size. The rip is done —
                # decide whether it is merely smaller than projected or truncated.
                elif stable_count >= grace_checks:
                    if _readable():
                        if expected_size > 0 and size_ratio < TRUNCATED_SIZE_RATIO:
                            logger.warning(
                                f"[MATCH] Title {title_id} (Job {job_id}): file stopped at "
                                f"{current_size / 1024 / 1024:.0f} MB "
                                f"({size_ratio * 100:.1f}% of expected "
                                f"{expected_size / 1024 / 1024:.0f} MB), stable for "
                                f"{stable_count * check_interval:.0f}s — treating as a "
                                f"truncated/incomplete rip: {file_path.name}"
                            )
                            return FileWaitResult.TRUNCATED
                        logger.warning(
                            f"[MATCH] Title {title_id} (Job {job_id}): file stable at "
                            f"{current_size / 1024 / 1024:.0f} MB "
                            f"({size_ratio * 100:.1f}% of expected) for "
                            f"{stable_count * check_interval:.0f}s — proceeding with match: "
                            f"{file_path.name}"
                        )
                        return FileWaitResult.READY
                    stable_count = 0
            else:
                if stable_count > 0:
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size changed "
                        f"({last_size} -> {current_size}), resetting stability counter"
                    )
                stable_count = 0

            last_size = current_size

            if expected_size > 0:
                wait_progress = min(99.0, (current_size / expected_size) * 100.0)
            else:
                wait_progress = min(99.0, (stable_count / required_stable) * 100.0)
            await _broadcast(wait_progress, current_size)

            await asyncio.sleep(check_interval)

        elapsed = time.monotonic() - start
        logger.warning(
            f"[MATCH] Title {title_id} (Job {job_id}): timed out waiting for file "
            f"after {elapsed:.0f}s: {file_path.name}"
        )
        return FileWaitResult.TIMEOUT
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_wait_for_file_ready.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_wait_for_file_ready.py
git commit -m "fix(matching): detect truncated rips in _wait_for_file_ready instead of spinning to timeout"
```

---

### Task 2: Dispatch the wait outcome (TRUNCATED → review, TIMEOUT → failed)

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (add `_handle_file_wait_result`; replace the inline handling at lines 679–692)
- Test: `backend/tests/unit/test_wait_for_file_ready.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_wait_for_file_ready.py`:

```python
async def _seed_queued_title() -> tuple[int, int]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=2,
            disc_number=1,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            file_size_bytes=8_200_000_000,
            state=TitleState.QUEUED,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _reload_title(title_id: int) -> DiscTitle:
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.asyncio
async def test_truncated_result_routes_title_to_review(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.TRUNCATED, job_id, title_id, Path("t02.mkv")
    )
    assert handled is True
    title = await _reload_title(title_id)
    assert title.state == TitleState.REVIEW
    assert "Incomplete rip" in (title.match_details or "")
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_timeout_result_fails_title(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.TIMEOUT, job_id, title_id, Path("t02.mkv")
    )
    assert handled is True
    title = await _reload_title(title_id)
    assert title.state == TitleState.FAILED
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_ready_result_proceeds(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.READY, job_id, title_id, Path("t02.mkv")
    )
    assert handled is False
    title = await _reload_title(title_id)
    assert title.state == TitleState.QUEUED  # untouched — caller proceeds to match


@pytest.mark.asyncio
async def test_legacy_truthy_result_proceeds(monkeypatch):
    # Existing integration tests patch _wait_for_file_ready to return True;
    # the dispatcher must treat any non-TRUNCATED/non-TIMEOUT value as "proceed".
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(True, job_id, title_id, Path("t02.mkv"))
    assert handled is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_wait_for_file_ready.py -k "result" -v`
Expected: FAIL — `AttributeError: 'MatchingCoordinator' object has no attribute '_handle_file_wait_result'`.

- [ ] **Step 3: Add the dispatcher method**

Add this method to `MatchingCoordinator` (place it directly above `_wait_for_file_ready`):

```python
    async def _handle_file_wait_result(
        self,
        wait_result: "FileWaitResult",
        job_id: int,
        title_id: int,
        file_path: Path,
    ) -> bool:
        """Act on a `_wait_for_file_ready` outcome.

        Returns True if the title was routed to review/failed and the caller
        must stop processing it; False to proceed with matching. Any value that
        is not TRUNCATED or TIMEOUT (e.g. a legacy ``True`` from older test
        stubs) is treated as READY.
        """
        if wait_result == FileWaitResult.TRUNCATED:
            # Reuse the standard failure convention: routes the (still-active)
            # title to REVIEW with a structured match_details reason and runs
            # the job-completion check so the rest of the disc can finish.
            await self._handle_match_failure(job_id, title_id, INCOMPLETE_RIP_MESSAGE)
            return True

        if wait_result == FileWaitResult.TIMEOUT:
            logger.error(
                f"[MATCH] Title {title_id} (Job {job_id}): file never became ready, "
                f"skipping match for {file_path.name}"
            )
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.FAILED
                    session.add(title)
                    await session.commit()
                await self._check_job_completion(session, job_id)
            return True

        return False
```

- [ ] **Step 4: Replace the inline caller block**

Replace the current call site (lines 679–692):

```python
        # 3. Wait for the file to be fully written before matching
        file_ready = await self._wait_for_file_ready(file_path, title_id, job_id)
        if not file_ready:
            logger.error(
                f"[MATCH] Title {title_id} (Job {job_id}): file never became ready, "
                f"skipping match for {file_path.name}"
            )
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.FAILED
                    session.add(title)
                    await session.commit()
                await self._check_job_completion(session, job_id)
            return
```

with:

```python
        # 3. Wait for the file to be fully written before matching
        wait_result = await self._wait_for_file_ready(file_path, title_id, job_id)
        if await self._handle_file_wait_result(wait_result, job_id, title_id, file_path):
            return
```

- [ ] **Step 5: Run the new tests + the existing matching/integration suites**

Run: `cd backend; uv run pytest tests/unit/test_wait_for_file_ready.py tests/unit/test_matching_coordinator.py tests/unit/test_stuck_job_recovery.py -v`
Expected: all PASS. (The integration suites that stub `_wait_for_file_ready` to return `True` — `tests/integration/test_chromaprint_pipeline.py`, `tests/integration/test_llm_matching_workflow.py` — still pass because `_handle_file_wait_result(True, ...)` returns `False`, so matching proceeds as before.)

- [ ] **Step 6: Run the broader backend unit + integration suites for regressions**

Run: `cd backend; uv run pytest tests/unit tests/integration -q`
Expected: no new failures vs. baseline. (Known pre-existing flake: `test_movie_ambiguous_rip_first_workflow`.)

- [ ] **Step 7: Lint**

Run: `cd backend; uv run ruff check app/services/matching_coordinator.py tests/unit/test_wait_for_file_ready.py; uv run ruff format app/services/matching_coordinator.py tests/unit/test_wait_for_file_ready.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_wait_for_file_ready.py
git commit -m "fix(matching): route truncated rips to review and fail-fast on file-wait timeout"
```

---

### Task 3: Changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an Unreleased entry**

Under `## [Unreleased]` → `### Fixed`, add:

```markdown
- A disc title aborted mid-rip by an uncorrectable read error (e.g. a scratched disc) no longer wedges the whole job in MATCHING for hours. Engram now recognizes a ripped file that has stopped growing far below its scanned size as an incomplete rip within ~90s, routes that title to review with a clear "incomplete rip — clean the disc and re-rip" reason, and lets the rest of the disc finish. While waiting for a ripped file to finalize, the track is shown as QUEUED rather than a misleading spinning RIPPING badge. (#NNN)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note truncated-rip fast-fail fix"
```

---

## Part A — Self-Review

**Spec coverage:**
- "Don't hang for hours on a truncated rip" → Task 1 bounds the undersized-stable wait to `grace_checks` (~90 s) and returns `TRUNCATED`; Task 2 routes it to review. ✓
- "Stable-but-undersized must be distinguishable from still-writing" → fast path (`>= READY_SIZE_RATIO`) returns in ~15 s; a still-growing file keeps resetting `stable_count` via the `else` branch; only a file stable for the full grace window is judged final. ✓
- "Don't false-positive a legitimately small title" → `test_ready_after_grace_when_modestly_undersized` (60% → READY); the `< TRUNCATED_SIZE_RATIO` (0.5) gate sits far above t02's 0.12 and far below the ~1.0+ real ratios. ✓
- "Stop showing RIPPING for a queued title" → wait loop broadcasts `TitleState.QUEUED`; `test_wait_broadcasts_queued_not_ripping`. ✓
- "Don't break existing stubs" → `_handle_file_wait_result(True, ...) → False`; `test_legacy_truthy_result_proceeds`. ✓

**Placeholder scan:** none — every step has full code and exact commands.

**Type consistency:** `FileWaitResult` (READY/TRUNCATED/TIMEOUT) is defined in Task 1 and consumed in Task 2; `_handle_file_wait_result` returns `bool`; `_wait_for_file_ready` returns `FileWaitResult`. `_handle_match_failure(job_id, title_id, error)` is the real existing signature (matching_coordinator.py:1589) and sets `TitleState.REVIEW`. Config field names (`ripping_file_poll_interval`, `ripping_stability_checks`, `ripping_file_ready_timeout`) match `app_config.py:78-80`. `broadcast_title_update(job_id, title_id, state, ...)` positional order matches existing call sites.

**Risk notes:**
- The `QUEUED` broadcast (Task 1) assumes the frontend renders the `queued` title state (it already does post import-storm `QUEUED` work). Worth eyeballing a DiscCard during the waiting_for_file phase, but it is cosmetic and separable from the functional fix (Tasks 1 logic + Task 2 dispatch).
- `TRUNCATED_STABLE_GRACE_SECONDS`/`READY_SIZE_RATIO`/`TRUNCATED_SIZE_RATIO` are module constants (YAGNI: avoids the AppConfig three-way-sync surface). Promote to config later only if a user needs to tune them.

---

## Part B — Real ripped-file size in the UI (design only; separate plan/PR)

**Problem (secondary, cosmetic):** `DiscTitle.file_size_bytes` is set from the **disc-scan size estimate** (a round number like `8199999999`) during analysis and is *never* overwritten with the real output size, so the UI always shows the estimate, not what is on disk. This is what made the t02 size look wrong (8.2 GB shown vs 0.94 GB real), and it is a long-standing discrepancy for *every* title (~8% off even on healthy rips).

**Why it is a separate change:** Part A intentionally keeps `file_size_bytes` as the *estimate* because the truncation detector compares the real on-disk size against it. Surfacing the real size therefore needs a **new column**, not an overwrite, and it spans model + migration + API + frontend.

**Proposed approach (to be detailed in its own plan):**
1. Add `actual_size_bytes: int | None = None` to `DiscTitle` (`backend/app/models/disc_job.py`). Frozen builds converge via `database.py` `_add_missing_columns` (ALTER TABLE ADD COLUMN); add the matching Alembic migration for dev parity. (See memory: frozen builds skip Alembic — the reconciler is what reaches users.)
2. Populate it in `JobManager._on_title_ripped` (`backend/app/services/job_manager.py:2003`), which already receives the final `path`: `title.actual_size_bytes = path.stat().st_size` (guarded against `OSError`). This is the seam where the rip-complete callback fires.
3. Surface it in `build_job_detail` / the title serializer (`backend/app/api/routes.py`) as `actual_size_bytes`, and in the `broadcast_title_update` payload.
4. Frontend: DiscCard / track inspector shows `actual_size_bytes` when present, falling back to `file_size_bytes` (the estimate) before ripping completes. Identify the exact render site in `frontend/src/app/components/DiscCard/*` before writing tasks.
5. Bonus once present: `_on_title_ripped` can compare `actual_size_bytes` against the `file_size_bytes` estimate and emit the same "incomplete rip" warning *at rip time*, complementing Part A's detection at match time.

**Recommendation:** ship Part A first (it resolves the actual stuck-job defect); schedule Part B as a follow-up once Part A lands.

---

## Out of scope (noted for follow-up)

- **Premature completion callback.** The extractor fired `Title file completed` for t02 twice (at 64 MB then 960 MB) because its stability window (`STABLE_CHECKS_REQUIRED` in `backend/app/core/extractor.py`) is short enough that MakeMKV's mid-bad-sector write pause looked like completion. Part A makes this harmless (the match-side waiter now resolves correctly regardless), but the double-fire is a separate robustness issue.
- **Re-rip affordance.** A review/failed title from a truncated rip currently has no one-click "re-rip this title" action; the user must re-insert the disc. Candidate future enhancement.
