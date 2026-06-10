# Single-Track Re-Rip After Clean & Reinsert (Feature C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user recover a single damaged track without re-ripping the whole disc — a rip-level failure holds the job in `REVIEW_NEEDED`, and reinserting the *same* disc (verified by `content_hash`) auto re-rips just the failed title(s), re-matches, and re-checks completion.

**Architecture:** Rip-level failures (truncated *or* stall/error) route to `TitleState.REVIEW` with a structured `match_details` code (`incomplete_rip` / `rip_stalled`) instead of `FAILED`, so the job stays non-terminal (`COMPLETED` now means every title succeeded). The disc-insert dedup seam (`JobManager._create_job_for_disc`, already computing the per-disc `content_hash` from PR #369) is extended: a reinsert whose hash matches a `REVIEW_NEEDED` job with re-rippable titles triggers a focused `rerip_titles` operation (reusing the existing rip→match→complete callbacks) instead of spawning a new full job. A bounded retry cap (`RERIP_MAX_ATTEMPTS`) governs auto re-rips; a manual endpoint bypasses the cap.

**Tech Stack:** Python 3.11, FastAPI, async SQLModel/SQLite, Alembic, pytest (`uv run pytest`), `unittest.mock`. Frontend: React 18 + TypeScript + Vite, Vitest/RTL, Playwright E2E.

**Spec:** `docs/superpowers/specs/2026-06-09-single-track-rerip-design.md`

---

## File Structure

**Backend — modify:**
- `backend/app/models/disc_job.py` — add `DiscTitle.rerip_attempts` column.
- `backend/migrations/versions/d4e5f6a7b8c9_add_disc_titles_rerip_attempts.py` — **create** (Alembic parity; head is `c5e9a1b3d7f2`).
- `backend/app/services/matching_coordinator.py` — add `RERIP_MAX_ATTEMPTS`, `RIP_FAILURE_ERROR_CODES`, `route_rip_failure_to_review`; reroute the truncated branch to `incomplete_rip`.
- `backend/app/services/job_manager.py` — reroute both stall sites to REVIEW; add `_is_auto_rerippable`, `_find_rerip_job`, `rerip_titles`, `rerip_title_manual`; hook the interception into `_create_job_for_disc`.
- `backend/app/api/routes.py` — add `POST /api/jobs/{job_id}/titles/{title_id}/rerip`.
- `backend/app/services/simulation_service.py` — add a DEBUG-only seed helper for an `incomplete_rip` review title.

**Backend — create tests:**
- `backend/tests/unit/test_rerip.py` — reroute helper, eligibility, `_find_rerip_job`, `rerip_titles`, `rerip_title_manual`.
- Update: `backend/tests/unit/test_job_manager.py` (stall→REVIEW semantics).

**Frontend — create:**
- `frontend/src/components/ReviewQueue/rerip.ts` (+ `rerip.test.ts`) — `getRerippableState` detection helper.
- `frontend/src/components/ReviewQueue/DamagedTrackNotice.tsx` — the review affordance.

**Frontend — modify:**
- `frontend/src/lib/client.ts` — `reripTitle(jobId, titleId)`.
- `frontend/src/components/ReviewQueue.tsx` — render the notice on rip-failed titles.
- `frontend/src/app/components/DiscCard.tsx` — damaged-track indicator.
- `frontend/e2e/` — a re-rip E2E spec.

**Docs:** `CHANGELOG.md`.

---

## Task 1: Add `DiscTitle.rerip_attempts` column + migration

**Files:**
- Modify: `backend/app/models/disc_job.py`
- Create: `backend/migrations/versions/d4e5f6a7b8c9_add_disc_titles_rerip_attempts.py`
- Test: `backend/tests/unit/test_rerip.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_rerip.py`:

```python
"""Unit tests for single-track re-rip (Feature C)."""

from app.models.disc_job import DiscTitle


def test_disc_title_has_rerip_attempts_default_zero():
    t = DiscTitle(job_id=1, title_index=0, duration_seconds=100)
    assert t.rerip_attempts == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -v`
Expected: FAIL — `AttributeError: 'DiscTitle' object has no attribute 'rerip_attempts'`.

- [ ] **Step 3: Add the column to the model**

In `backend/app/models/disc_job.py`, in the `DiscTitle` class, immediately after the line
`match_details: str | None = None  # JSON string with score breakdown`, add:

```python
    # Number of automatic/manual re-rip attempts for this title (Feature C).
    # Bounds auto re-rip after a clean & reinsert; see RERIP_MAX_ATTEMPTS.
    rerip_attempts: int = 0
```

- [ ] **Step 4: Create the Alembic migration**

Create `backend/migrations/versions/d4e5f6a7b8c9_add_disc_titles_rerip_attempts.py`:

```python
"""add disc_titles.rerip_attempts

Tracks how many times a rip-failed title has been re-ripped (Feature C —
single-track re-rip after clean & reinsert). Mirrors the database.py reconciler
path used by frozen builds (which skip Alembic) — the two must stay in agreement.

Revision ID: d4e5f6a7b8c9
Revises: c5e9a1b3d7f2
Create Date: 2026-06-09 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c5e9a1b3d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("rerip_attempts", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("rerip_attempts")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -v`
Expected: PASS.

- [ ] **Step 6: Verify the reconciler adds the column on an existing DB**

Run: `cd backend; uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"`
Expected: no error (the table name in the migration, `disc_titles`, matches `DiscTitle.__tablename__`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/disc_job.py backend/migrations/versions/d4e5f6a7b8c9_add_disc_titles_rerip_attempts.py backend/tests/unit/test_rerip.py
git commit -m "feat(rerip): add DiscTitle.rerip_attempts column + migration"
```

---

## Task 2: Shared reroute helper + relabel truncated rips as `incomplete_rip`

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`
- Test: `backend/tests/unit/test_rerip.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_rerip.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services import matching_coordinator as mc
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import (
    RERIP_MAX_ATTEMPTS,
    MatchingCoordinator,
    route_rip_failure_codes,  # noqa: F401  (see below; alias of RIP_FAILURE_ERROR_CODES)
)
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    monkeypatch.setattr(mc, "async_session", _unit_session_factory)


def _make_coord() -> MatchingCoordinator:
    broadcaster = MagicMock()
    coord = MatchingCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord._check_job_completion = AsyncMock()
    return coord


async def _seed_title(state: TitleState, attempts: int = 0) -> tuple[int, int]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            staging_path="/tmp/staging",
            content_hash="ABC123",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            state=state,
            rerip_attempts=attempts,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _reload(title_id: int) -> DiscTitle:
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.asyncio
async def test_route_marks_review_with_code_and_eligible(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.QUEUED, attempts=0)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    title = await _reload(title_id)
    assert title.state == TitleState.REVIEW
    d = json.loads(title.match_details)
    assert d["error"] == "incomplete_rip"
    assert d["rerip_eligible"] is True
    assert d["rerip_attempts"] == 0
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_route_marks_ineligible_at_cap(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.RIPPING, attempts=RERIP_MAX_ATTEMPTS)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    d = json.loads((await _reload(title_id)).match_details)
    assert d["rerip_eligible"] is False
    assert "stopped after" in d["message"].lower()


@pytest.mark.asyncio
async def test_route_ignores_terminal_title(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.MATCHED)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    assert (await _reload(title_id)).state == TitleState.MATCHED  # untouched
```

> Note: the `route_rip_failure_codes` import line is a placeholder guard — delete it; the real symbol is `RIP_FAILURE_ERROR_CODES`. (It is removed in Step 3's test cleanup.)

Remove the stray `route_rip_failure_codes` import line before running (it does not exist). The needed imports are `RERIP_MAX_ATTEMPTS` and `MatchingCoordinator`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "route" -v`
Expected: FAIL — `ImportError`/`AttributeError` (`RERIP_MAX_ATTEMPTS` / `route_rip_failure_to_review` do not exist).

- [ ] **Step 3: Add constants + the reroute helper**

In `backend/app/services/matching_coordinator.py`, just below the existing
`INCOMPLETE_RIP_MESSAGE` constant (near line 169), add:

```python
# Automatic re-rip attempt cap (Feature C). After this many auto/manual re-rips a
# title stays in review but stops auto-triggering on reinsert (rerip_eligible=False).
RERIP_MAX_ATTEMPTS = 2

# match_details["error"] codes that mean "the rip itself failed" — these titles
# are eligible for single-track re-rip after a clean & reinsert.
RIP_FAILURE_ERROR_CODES = frozenset({"incomplete_rip", "rip_stalled"})
```

Then add this method to the `MatchingCoordinator` class (place it directly above
`_handle_match_failure`):

```python
    async def route_rip_failure_to_review(
        self, job_id: int, title_id: int, error_code: str, message: str
    ) -> None:
        """Route a rip-level failure (truncated/stall) to REVIEW, not FAILED.

        Writes a structured ``match_details`` carrying the error code, the
        current attempt count, and a ``rerip_eligible`` flag (False once the
        retry cap is reached). Keeping rip failures in REVIEW holds the job in
        REVIEW_NEEDED so COMPLETED means every title succeeded (Feature C).
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            active_states = (
                TitleState.PENDING,
                TitleState.RIPPING,
                TitleState.QUEUED,
                TitleState.MATCHING,
            )
            if title and title.state in active_states:
                attempts = title.rerip_attempts or 0
                eligible = attempts < RERIP_MAX_ATTEMPTS
                detail_msg = message
                if not eligible:
                    detail_msg = (
                        f"{message} Automatic re-rip stopped after {attempts} attempt(s) — "
                        "clean or replace the disc and use Re-rip, or skip this title."
                    )
                title.state = TitleState.REVIEW
                title.match_details = json.dumps(
                    {
                        "error": error_code,
                        "message": detail_msg,
                        "rerip_eligible": eligible,
                        "rerip_attempts": attempts,
                    }
                )
                session.add(title)
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_id,
                    title.state.value,
                    match_details=title.match_details,
                )
            await self._check_job_completion(session, job_id)
```

- [ ] **Step 4: Relabel the truncated branch to use the helper**

In `_handle_file_wait_result` (the `FileWaitResult.TRUNCATED` branch, near line 1468–1473),
replace:

```python
        if wait_result == FileWaitResult.TRUNCATED:
            # Reuse the standard failure convention: routes the (still-active)
            # title to REVIEW with a structured match_details reason and runs
            # the job-completion check so the rest of the disc can finish.
            await self._handle_match_failure(job_id, title_id, INCOMPLETE_RIP_MESSAGE)
            return True
```

with:

```python
        if wait_result == FileWaitResult.TRUNCATED:
            # Route to REVIEW with the rip-failure code so the title is
            # re-rippable (Feature C) and the rest of the disc can finish.
            await self.route_rip_failure_to_review(
                job_id, title_id, "incomplete_rip", INCOMPLETE_RIP_MESSAGE
            )
            return True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "route" -v`
Expected: PASS (all 3).

- [ ] **Step 6: Run the truncated-rip suite to confirm no regression**

Run: `cd backend; uv run pytest tests/unit/test_wait_for_file_ready.py -v`
Expected: PASS. (The existing `test_truncated_result_routes_title_to_review` asserts
`"Incomplete rip" in match_details` — still true via the message; if it asserts the *error code*
`matching_task_failed`, update that assertion to `incomplete_rip`.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_rerip.py
git commit -m "feat(rerip): route truncated rips to REVIEW with incomplete_rip code"
```

---

## Task 3: Reroute both stall sites to REVIEW (`rip_stalled`) — completion-semantics change

**Files:**
- Modify: `backend/app/services/job_manager.py` (`_on_title_error` near line 2106; the fallback block near line 1762–1789)
- Test: `backend/tests/unit/test_rerip.py` (extend); update `backend/tests/unit/test_job_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_rerip.py`:

```python
@pytest.mark.asyncio
async def test_on_title_error_routes_to_review_not_failed(monkeypatch):
    """A ripping stall now holds the title in REVIEW (rip_stalled), not FAILED."""
    from app.services.job_manager import job_manager

    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.RIPPING)
    # Real coordinator with a stubbed completion check.
    job_manager._matching._check_job_completion = AsyncMock()

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title_id)
        sorted_titles = [title]

    await job_manager._on_title_error(job_id, 1, "disc dirty", sorted_titles)

    t = await _reload(title_id)
    assert t.state == TitleState.REVIEW
    d = json.loads(t.match_details)
    assert d["error"] == "rip_stalled"
    assert d["rerip_eligible"] is True
```

> This test uses the module-level `job_manager` singleton. If `_unit_session_factory`
> patching of `mc.async_session` does not cover `job_manager`'s session, also
> `monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "on_title_error_routes" -v`
Expected: FAIL — current `_on_title_error` sets `TitleState.FAILED` with `{"reason": …}`.

- [ ] **Step 3: Reroute `_on_title_error`**

In `backend/app/services/job_manager.py`, replace the body of `_on_title_error`
(the block from `db_title = await session.get(...)` through the broadcast, lines ~2124–2140):

```python
        stalled_title = sorted_titles[list_idx]
        async with async_session() as session:
            db_title = await session.get(DiscTitle, stalled_title.id)
            if not db_title:
                return
            if db_title.state in (TitleState.COMPLETED, TitleState.MATCHED):
                return

            db_title.state = TitleState.FAILED
            db_title.match_details = json.dumps({"reason": reason})
            await session.commit()

            logger.warning(f"Job {job_id}: title {db_title.title_index} marked FAILED ({reason})")
            await ws_manager.broadcast_title_update(
                job_id,
                db_title.id,
                TitleState.FAILED.value,
                error=reason,
            )
```

with:

```python
        stalled_title = sorted_titles[list_idx]
        # A ripping stall is a rip-level failure: route to REVIEW (re-rippable),
        # not FAILED, so the job holds in REVIEW_NEEDED (Feature C).
        await self._matching.route_rip_failure_to_review(
            job_id, stalled_title.id, "rip_stalled", reason
        )
```

(Leave the out-of-range guard above it intact.)

- [ ] **Step 4: Reroute the fallback stall block**

In the rip flow (the `if result.stalled_titles:` block, lines ~1762–1789), replace the inner
mark-FAILED logic:

```python
            # Fallback: mark stalled titles as FAILED
            if result.stalled_titles:
                async with async_session() as stall_session:
                    for cmd_idx in result.stalled_titles:
                        list_idx = cmd_idx - 1
                        if 0 <= list_idx < len(stall_title_list):
                            stalled_title = stall_title_list[list_idx]
                            db_title = await stall_session.get(DiscTitle, stalled_title.id)
                            if db_title and db_title.state not in (
                                TitleState.COMPLETED,
                                TitleState.MATCHED,
                                TitleState.FAILED,
                            ):
                                db_title.state = TitleState.FAILED
                                db_title.match_details = json.dumps(
                                    {"reason": STALL_FAILURE_REASON}
                                )
                                logger.warning(
                                    f"Job {safe_job}: title {db_title.title_index} "
                                    f"marked FAILED (ripping stall, fallback)"
                                )
                                await ws_manager.broadcast_title_update(
                                    job_id,
                                    db_title.id,
                                    TitleState.FAILED.value,
                                    error=STALL_FAILURE_REASON,
                                )
                    await stall_session.commit()
```

with:

```python
            # Fallback: a stalled title is a rip-level failure → REVIEW
            # (re-rippable), not FAILED, so the job holds in REVIEW_NEEDED.
            if result.stalled_titles:
                for cmd_idx in result.stalled_titles:
                    list_idx = cmd_idx - 1
                    if 0 <= list_idx < len(stall_title_list):
                        stalled_title = stall_title_list[list_idx]
                        logger.warning(
                            f"Job {safe_job}: title {stalled_title.title_index} "
                            f"stalled (fallback) → REVIEW (re-rippable)"
                        )
                        await self._matching.route_rip_failure_to_review(
                            job_id, stalled_title.id, "rip_stalled", STALL_FAILURE_REASON
                        )
```

- [ ] **Step 5: Run the new test + the job-manager suite**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "on_title_error_routes" tests/unit/test_job_manager.py -v`
Expected: the new test PASSES. Some `test_job_manager.py` tests that assert a stall yields
`TitleState.FAILED` (or job `COMPLETED` with a failed title) will now FAIL — update those
assertions to expect `TitleState.REVIEW` with `match_details.error == "rip_stalled"` and the job
in `REVIEW_NEEDED`. (Search them with `grep -n "FAILED" backend/tests/unit/test_job_manager.py`
and fix the stall-specific ones; do **not** change assertions about deliberate user-skip → FAILED
or whole-disc-failure → FAILED.)

- [ ] **Step 6: Run the broader suite for regressions**

Run: `cd backend; uv run pytest tests/unit tests/integration -q`
Expected: no new failures beyond the known `test_movie_ambiguous_rip_first_workflow` flake. Fix any
remaining stall→FAILED assertions surfaced here the same way.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_rerip.py backend/tests/unit/test_job_manager.py
git commit -m "feat(rerip): hold rip-stalled titles in REVIEW so COMPLETED means all-succeeded"
```

---

## Task 4: `rerip_titles` orchestration

**Files:**
- Modify: `backend/app/services/job_manager.py`
- Test: `backend/tests/unit/test_rerip.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_rerip.py`:

```python
from pathlib import Path


@pytest.mark.asyncio
async def test_rerip_titles_transitions_deletes_and_rips(monkeypatch, tmp_path):
    from app.core.extractor import RipResult
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

    # Seed a REVIEW_NEEDED job with one incomplete_rip REVIEW title + a stale file.
    stale = tmp_path / "show_t02.mkv"
    stale.write_bytes(b"truncated")
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:", volume_label="SHOW_S2D1", content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED, staging_path=str(tmp_path), content_hash="ABC123",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id, title_index=2, duration_seconds=2819, state=TitleState.REVIEW,
            output_filename=str(stale), rerip_attempts=0,
            match_details=json.dumps({"error": "incomplete_rip", "rerip_eligible": True}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    captured = {}

    async def fake_rip_titles(drive, output_dir, title_indices=None, **kw):
        captured["drive"] = drive
        captured["indices"] = title_indices
        return RipResult(success=True, output_files=[], error_message=None)

    monkeypatch.setattr(job_manager._extractor, "rip_titles", fake_rip_titles)
    monkeypatch.setattr(job_manager, "_drive_monitor", MagicMock())
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda d: None)

    await job_manager.rerip_titles(job_id, [title_id])

    assert captured["indices"] == [2]
    assert captured["drive"] == "F:"
    assert not stale.exists()  # stale file deleted before re-rip
    t = await _reload(title_id)
    assert t.rerip_attempts == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "rerip_titles_transitions" -v`
Expected: FAIL — `AttributeError: 'JobManager' object has no attribute 'rerip_titles'`.

- [ ] **Step 3: Implement `rerip_titles`**

Add to the `JobManager` class in `backend/app/services/job_manager.py` (place it in the
"--- Ripping ---" region, e.g. after `_transition_title_out_of_ripping`):

```python
    async def rerip_titles(self, job_id: int, title_ids: list[int]) -> None:
        """Re-rip specific rip-failed titles using the disc currently in the drive.

        Reuses the normal rip→match→complete machinery for a focused subset:
        transitions the job back to RIPPING (also blocking spurious reinserts),
        deletes each title's stale staging file so MakeMKV overwrites cleanly,
        re-rips only ``title_ids``, and lets the existing title-complete/-error
        callbacks drive re-matching and completion (Feature C).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return
            titles = []
            for tid in title_ids:
                t = await session.get(DiscTitle, tid)
                if t and t.job_id == job_id and t.state == TitleState.REVIEW:
                    titles.append(t)
            if not titles:
                logger.info(f"Job {job_id}: no eligible titles to re-rip ({title_ids})")
                return

            # Un-hide a cleared job that is being actively re-processed.
            if job.cleared_at is not None:
                job.cleared_at = None
                session.add(job)

            # REVIEW_NEEDED -> RIPPING (valid; also makes a spurious reinsert an
            # unconditional drive-busy block during the re-rip).
            await state_machine.transition(job, JobState.RIPPING, session)

            staging_dir = Path(job.staging_path)
            staging_dir.mkdir(parents=True, exist_ok=True)
            drive_id = job.drive_id

            for t in titles:
                t.rerip_attempts = (t.rerip_attempts or 0) + 1
                t.state = TitleState.RIPPING
                if t.output_filename:
                    old = Path(t.output_filename)
                    try:
                        if old.exists():
                            old.unlink()
                    except OSError as e:
                        logger.warning(f"Job {job_id}: could not remove stale file {old}: {e}")
                t.output_filename = None
                session.add(t)
                await ws_manager.broadcast_title_update(
                    job_id, t.id, TitleState.RIPPING.value
                )
            await session.commit()

            subset_sorted = sorted(titles, key=lambda x: x.title_index)
            rip_indices = [t.title_index for t in subset_sorted]
            for t in subset_sorted:
                session.expunge(t)

        self._note_activity(job_id)

        def on_title_complete(idx: int, path: Path):
            future = asyncio.run_coroutine_threadsafe(
                self._on_title_ripped(job_id, idx, path, subset_sorted), self._loop
            )

            def _check(fut):
                try:
                    fut.result(timeout=30)
                except Exception as e:  # noqa: BLE001 — surface, never swallow
                    logger.exception(f"[RERIP] _on_title_ripped failed (Job {job_id}): {e}")

            future.add_done_callback(_check)

        def on_title_error(cmd_idx: int, reason: str):
            list_idx = cmd_idx - 1
            if not (0 <= list_idx < len(subset_sorted)):
                logger.error(f"Job {job_id}: re-rip title error cmd_idx={cmd_idx} out of range")
                return
            title_id_err = subset_sorted[list_idx].id
            asyncio.run_coroutine_threadsafe(
                self._matching.route_rip_failure_to_review(
                    job_id, title_id_err, "rip_stalled", reason
                ),
                self._loop,
            )

        from app.core.discdb_exporter import get_makemkv_log_dir
        from app.services.config_service import get_config

        cfg = await get_config()
        stall_timeout = cfg.ripping_stall_timeout if cfg else 120.0

        result = await self._extractor.rip_titles(
            drive_id,
            staging_dir,
            title_indices=rip_indices,
            title_complete_callback=on_title_complete,
            stall_timeout=stall_timeout,
            title_error_callback=on_title_error,
            log_dir=get_makemkv_log_dir(job_id),
            job_id=job_id,
        )

        # A clean MakeMKV failure (disc unreadable) returns success=False without
        # a per-title stall callback — route any still-RIPPING title back to review.
        if not result.success:
            async with async_session() as session:
                for t in subset_sorted:
                    db_t = await session.get(DiscTitle, t.id)
                    if db_t and db_t.state == TitleState.RIPPING:
                        await self._matching.route_rip_failure_to_review(
                            job_id, t.id, "incomplete_rip", INCOMPLETE_RIP_MESSAGE
                        )

        # Free the drive for the next disc.
        try:
            from app.core.sentinel import eject_disc

            await asyncio.to_thread(eject_disc, drive_id)
            self._drive_monitor.notify_ejected(drive_id)
        except (OSError, RuntimeError) as e:
            logger.warning(f"Job {job_id}: eject after re-rip failed: {e}")
```

Ensure `INCOMPLETE_RIP_MESSAGE` is importable in `job_manager.py`. Add to the existing
`from app.services.matching_coordinator import ...` line (or create one):
`from app.services.matching_coordinator import INCOMPLETE_RIP_MESSAGE`. If a circular import
results, import it lazily inside the method instead.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "rerip_titles_transitions" -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend; uv run ruff check app/services/job_manager.py tests/unit/test_rerip.py; uv run ruff format app/services/job_manager.py tests/unit/test_rerip.py
git add backend/app/services/job_manager.py backend/tests/unit/test_rerip.py
git commit -m "feat(rerip): rerip_titles re-rips a failed-title subset and re-matches"
```

---

## Task 5: `_find_rerip_job` + reinsert interception in `_create_job_for_disc`

**Files:**
- Modify: `backend/app/services/job_manager.py`
- Test: `backend/tests/unit/test_rerip.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_rerip.py`:

```python
async def _seed_rerip_job(*, eligible: bool, hash_="ABC123", state=JobState.REVIEW_NEEDED):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:", volume_label="SHOW_S2D1", content_type=ContentType.TV,
            state=state, staging_path="/tmp/staging", content_hash=hash_,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id, title_index=2, duration_seconds=2819, state=TitleState.REVIEW,
            match_details=json.dumps({"error": "incomplete_rip", "rerip_eligible": eligible}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


@pytest.mark.asyncio
async def test_find_rerip_job_hash_match_eligible(monkeypatch):
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    job_id, title_id = await _seed_rerip_job(eligible=True)
    found = await job_manager._find_rerip_job("ABC123")
    assert found == (job_id, [title_id])


@pytest.mark.asyncio
async def test_find_rerip_job_hash_mismatch(monkeypatch):
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    await _seed_rerip_job(eligible=True)
    assert await job_manager._find_rerip_job("DIFFERENT") is None
    assert await job_manager._find_rerip_job(None) is None


@pytest.mark.asyncio
async def test_find_rerip_job_excludes_ineligible_and_busy(monkeypatch):
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    await _seed_rerip_job(eligible=False)  # cap reached
    await _seed_rerip_job(eligible=True, hash_="OTHER", state=JobState.MATCHING)  # still busy
    assert await job_manager._find_rerip_job("ABC123") is None
    assert await job_manager._find_rerip_job("OTHER") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "find_rerip_job" -v`
Expected: FAIL — `_find_rerip_job` does not exist.

- [ ] **Step 3: Implement `_is_auto_rerippable` + `_find_rerip_job`**

Add a module-level helper near the top of `job_manager.py` (after the imports), and import the
error-code set:

```python
from app.services.matching_coordinator import RIP_FAILURE_ERROR_CODES


def _is_auto_rerippable(title: DiscTitle) -> bool:
    """True if a REVIEW title is a rip failure still eligible for an auto re-rip."""
    if not title.match_details:
        return False
    try:
        details = json.loads(title.match_details)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(details, dict):
        return False
    return bool(details.get("rerip_eligible")) and details.get("error") in RIP_FAILURE_ERROR_CODES
```

Then add this method to the `JobManager` class (near `_create_job_for_disc`):

```python
    async def _find_rerip_job(self, new_hash: str | None) -> tuple[int, list[int]] | None:
        """Find a REVIEW_NEEDED job for this disc with auto-re-rippable titles.

        Returns ``(job_id, [title_id])`` when the inserted disc's ContentHash
        matches a settled job holding rip-failed titles still eligible for an
        automatic re-rip; ``None`` for a different/unfingerprintable disc, a job
        still actively matching, or no eligible titles (Feature C).
        """
        if not new_hash:
            return None
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(
                    DiscJob.content_hash == new_hash,
                    DiscJob.state == JobState.REVIEW_NEEDED,
                )
            )
            jobs = sorted(result.scalars().all(), key=lambda j: j.id, reverse=True)
            for job in jobs:
                titles_res = await session.execute(
                    select(DiscTitle).where(
                        DiscTitle.job_id == job.id,
                        DiscTitle.state == TitleState.REVIEW,
                    )
                )
                eligible = [t.id for t in titles_res.scalars().all() if _is_auto_rerippable(t)]
                if eligible:
                    return job.id, eligible
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "find_rerip_job" -v`
Expected: PASS (all 3).

- [ ] **Step 5: Hook the interception into `_create_job_for_disc`**

In `_create_job_for_disc`, immediately after the line
`new_hash = await self._compute_disc_hash(drive_letter)` (and before
`async with async_session() as session:`), insert:

```python
            # Feature C: a reinsert of the SAME disc (hash match) with re-rippable
            # titles re-rips just those titles instead of spawning a new job.
            rerip = await self._find_rerip_job(new_hash)
            if rerip is not None:
                rerip_job_id, rerip_title_ids = rerip
                logger.info(
                    f"Disc reinserted (hash match) for job {rerip_job_id}; re-ripping "
                    f"{len(rerip_title_ids)} failed title(s) instead of creating a new job."
                )
                task = asyncio.create_task(
                    with_job_log_context(
                        rerip_job_id, self.rerip_titles(rerip_job_id, rerip_title_ids)
                    )
                )
                task.add_done_callback(lambda t, jid=rerip_job_id: self._on_task_done(t, jid))
                self._active_jobs[rerip_job_id] = task
                return
```

- [ ] **Step 6: Run the dedup + rerip suites for regressions**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py backend/tests/unit/test_job_manager.py -q`
(Adjust path: `cd backend; uv run pytest tests/unit/test_rerip.py tests/unit/test_job_manager.py -q`.)
Expected: PASS. The existing #369 dedup tests still pass (the interception returns `None` when no
re-rippable job matches, so the dedup path is unchanged for normal inserts).

- [ ] **Step 7: Lint + commit**

```bash
cd backend; uv run ruff check app/services/job_manager.py; uv run ruff format app/services/job_manager.py
git add backend/app/services/job_manager.py backend/tests/unit/test_rerip.py
git commit -m "feat(rerip): reinsert of the same disc re-rips failed titles via _create_job_for_disc"
```

---

## Task 6: Manual re-rip endpoint (`POST /api/jobs/{job_id}/titles/{title_id}/rerip`)

**Files:**
- Modify: `backend/app/services/job_manager.py` (`rerip_title_manual`)
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/unit/test_rerip.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_rerip.py`:

```python
@pytest.mark.asyncio
async def test_rerip_title_manual_verifies_hash_and_spawns(monkeypatch):
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    job_id, title_id = await _seed_rerip_job(eligible=False)  # cap reached: manual bypasses it

    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value="ABC123"))
    spawned = {}

    async def fake_rerip(jid, tids):
        spawned["args"] = (jid, tids)

    monkeypatch.setattr(job_manager, "rerip_titles", fake_rerip)

    await job_manager.rerip_title_manual(job_id, title_id)
    assert spawned["args"] == (job_id, [title_id])


@pytest.mark.asyncio
async def test_rerip_title_manual_rejects_wrong_disc(monkeypatch):
    from app.services.job_manager import job_manager

    monkeypatch.setattr("app.services.job_manager.async_session", _unit_session_factory)
    job_id, title_id = await _seed_rerip_job(eligible=True)
    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value="WRONG"))
    monkeypatch.setattr(job_manager, "rerip_titles", AsyncMock())

    with pytest.raises(ValueError, match="different disc"):
        await job_manager.rerip_title_manual(job_id, title_id)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "title_manual" -v`
Expected: FAIL — `rerip_title_manual` does not exist.

- [ ] **Step 3: Implement `rerip_title_manual`**

Add to the `JobManager` class:

```python
    async def rerip_title_manual(self, job_id: int, title_id: int) -> None:
        """Manually re-rip one title using the disc currently in the drive.

        Verifies the inserted disc matches the job by ContentHash and bypasses
        the automatic retry cap (the user explicitly asked). Spawns the re-rip in
        the background so the request returns promptly (Feature C).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            title = await session.get(DiscTitle, title_id)
            if not job or not title or title.job_id != job_id:
                raise ValueError("Job or title not found")
            if title.state != TitleState.REVIEW:
                raise ValueError("Title is not awaiting re-rip")
            drive_id = job.drive_id
            job_hash = job.content_hash

        current_hash = await self._compute_disc_hash(drive_id)
        if not current_hash:
            raise ValueError("No readable disc in the drive — insert the matching disc first")
        if job_hash and current_hash != job_hash:
            raise ValueError("A different disc is in the drive — insert the original disc")

        task = asyncio.create_task(
            with_job_log_context(job_id, self.rerip_titles(job_id, [title_id]))
        )
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task
```

> The unit tests patch `rerip_titles` with a plain coroutine, so `asyncio.create_task` runs it.
> If `_on_task_done` requires a running event loop the test already provides one (pytest-asyncio).

- [ ] **Step 4: Add the endpoint**

In `backend/app/api/routes.py`, after the `skip_title` endpoint (near line 896), add:

```python
@router.post("/jobs/{job_id}/titles/{title_id}/rerip")
async def rerip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Manually re-rip a single rip-failed title using the disc in the drive."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    try:
        await job_manager.rerip_title_manual(job.id, title_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "reripping", "job_id": job.id, "title_id": title_id}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_rerip.py -k "title_manual" -v`
Expected: PASS (both).

- [ ] **Step 6: Lint + commit**

```bash
cd backend; uv run ruff check app/services/job_manager.py app/api/routes.py; uv run ruff format app/services/job_manager.py app/api/routes.py
git add backend/app/services/job_manager.py backend/app/api/routes.py backend/tests/unit/test_rerip.py
git commit -m "feat(rerip): manual re-rip endpoint with disc-hash verification (cap bypass)"
```

---

## Task 7: Frontend — detection helper + API client

**Files:**
- Create: `frontend/src/components/ReviewQueue/rerip.ts`
- Create: `frontend/src/components/ReviewQueue/rerip.test.ts`
- Modify: `frontend/src/lib/client.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ReviewQueue/rerip.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { getRerippableState } from './rerip';

describe('getRerippableState', () => {
  it('detects an auto-eligible incomplete_rip title', () => {
    const md = JSON.stringify({ error: 'incomplete_rip', message: 'clean it', rerip_eligible: true, rerip_attempts: 0 });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.autoEligible).toBe(true);
    expect(s.errorCode).toBe('incomplete_rip');
    expect(s.message).toBe('clean it');
  });

  it('detects a cap-reached rip_stalled title as rerippable but not auto', () => {
    const md = JSON.stringify({ error: 'rip_stalled', rerip_eligible: false, rerip_attempts: 2 });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.autoEligible).toBe(false);
    expect(s.attempts).toBe(2);
  });

  it('returns not-rerippable for match-level review and bad input', () => {
    expect(getRerippableState(JSON.stringify({ error: 'low_confidence' })).isRerippable).toBe(false);
    expect(getRerippableState(null).isRerippable).toBe(false);
    expect(getRerippableState('not json').isRerippable).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend; npm run test:unit -- rerip`
Expected: FAIL — `./rerip` module not found.

- [ ] **Step 3: Implement the helper**

Create `frontend/src/components/ReviewQueue/rerip.ts`:

```ts
const RIP_FAILURE_CODES = new Set(['incomplete_rip', 'rip_stalled']);

export interface RerippableState {
  /** True when this REVIEW title failed at the rip level (re-rippable). */
  isRerippable: boolean;
  errorCode: string | null;
  /** User-facing message from the backend. */
  message: string | null;
  /** True while auto re-rip on reinsert is still allowed (under the cap). */
  autoEligible: boolean;
  attempts: number;
}

const EMPTY: RerippableState = {
  isRerippable: false,
  errorCode: null,
  message: null,
  autoEligible: false,
  attempts: 0,
};

/** Parse a title's `match_details` JSON into its re-rip state (Feature C). */
export function getRerippableState(matchDetails?: string | null): RerippableState {
  if (!matchDetails) return EMPTY;
  try {
    const d = JSON.parse(matchDetails);
    const code = typeof d?.error === 'string' ? d.error : null;
    if (!code || !RIP_FAILURE_CODES.has(code)) return EMPTY;
    return {
      isRerippable: true,
      errorCode: code,
      message: typeof d.message === 'string' ? d.message : null,
      autoEligible: Boolean(d.rerip_eligible),
      attempts: typeof d.rerip_attempts === 'number' ? d.rerip_attempts : 0,
    };
  } catch {
    return EMPTY;
  }
}
```

- [ ] **Step 4: Add the API client function**

In `frontend/src/lib/client.ts`, following the existing fetch-helper style, add:

```ts
/** Manually re-rip a single rip-failed title (Feature C). */
export async function reripTitle(jobId: number, titleId: number): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/titles/${titleId}/rerip`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Re-rip request failed');
  }
}
```

(If `client.ts` exports through a shared object/namespace rather than free functions, match that
convention instead.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend; npm run test:unit -- rerip`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ReviewQueue/rerip.ts frontend/src/components/ReviewQueue/rerip.test.ts frontend/src/lib/client.ts
git commit -m "feat(rerip): frontend re-rip detection helper + API client"
```

---

## Task 8: Frontend — DamagedTrackNotice + ReviewQueue/DiscCard integration

**Files:**
- Create: `frontend/src/components/ReviewQueue/DamagedTrackNotice.tsx`
- Modify: `frontend/src/components/ReviewQueue.tsx`
- Modify: `frontend/src/app/components/DiscCard.tsx`

- [ ] **Step 1: Implement the notice component**

Create `frontend/src/components/ReviewQueue/DamagedTrackNotice.tsx`:

```tsx
import { useState } from 'react';
import { reripTitle } from '../../lib/client';
import type { RerippableState } from './rerip';

interface Props {
  jobId: number;
  titleId: number;
  state: RerippableState;
}

/**
 * Review affordance for a rip-failed (damaged) track. Tells the user to clean &
 * reinsert (auto re-rip), offers a manual Re-rip button, and never hides the
 * existing skip action elsewhere on the card (Feature C).
 */
export function DamagedTrackNotice({ jobId, titleId, state }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onRerip = async () => {
    setBusy(true);
    setError(null);
    try {
      await reripTitle(jobId, titleId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Re-rip failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-none border border-magenta/40 bg-magenta/5 p-3 text-sm" data-testid="damaged-track-notice">
      <div className="font-mono uppercase tracking-wide text-magenta">⚠ Damaged track</div>
      <p className="mt-1 text-zinc-300">
        {state.message ||
          'This track failed to rip cleanly. Clean the disc and reinsert it to re-rip automatically, or skip it.'}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onRerip}
          disabled={busy}
          className="border border-cyan/50 px-2 py-1 font-mono text-xs uppercase text-cyan disabled:opacity-50"
          data-testid="rerip-button"
        >
          {busy ? 'Re-ripping…' : 'Re-rip this title'}
        </button>
        {state.attempts > 0 && (
          <span className="font-mono text-xs text-zinc-500">attempt {state.attempts}</span>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}
```

> Match the project's actual Tailwind/brand tokens (cyan/magenta, `rounded-none`, JetBrains Mono)
> used by neighboring components like `SvNotice`; the classes above follow the Synapse v2 system
> described in CLAUDE.md. If a shared `SvNotice`/button primitive exists, prefer composing it.

- [ ] **Step 2: Render it in the review cards**

In `frontend/src/components/ReviewQueue.tsx`, import the helper + component:

```tsx
import { getRerippableState } from './ReviewQueue/rerip';
import { DamagedTrackNotice } from './ReviewQueue/DamagedTrackNotice';
```

In the per-title render (inside both the TV and Movie title card bodies, where `match_details` is
available as `title.match_details`), add near the top of the card body:

```tsx
{(() => {
  const rerip = getRerippableState(title.match_details);
  return rerip.isRerippable ? (
    <DamagedTrackNotice jobId={jobId} titleId={title.id} state={rerip} />
  ) : null;
})()}
```

Place it before the episode-selector / movie-save controls so the damaged-track guidance leads.
Keep the existing skip control rendered (do not gate it on rerip state) so a dead disc can still be
abandoned.

- [ ] **Step 3: DiscCard damaged-track indicator**

In `frontend/src/app/components/DiscCard.tsx`, compute whether any track is rip-failed and show a
small badge. Where the card maps over its tracks (or has access to the title list), add:

```tsx
import { getRerippableState } from '../../components/ReviewQueue/rerip';

// ...where tracks/titles are available:
const hasDamagedTrack = (job.titles ?? []).some(
  (t) => getRerippableState(t.match_details).isRerippable,
);

// ...in the badge row:
{hasDamagedTrack && (
  <span
    className="border border-magenta/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-magenta"
    data-testid="disccard-damaged-badge"
  >
    Damaged track
  </span>
)}
```

> Use the exact field name the DiscCard already uses for its track array and per-track
> `match_details`. If the DiscCard receives a transformed view model (via `useDiscFilters`), read
> `match_details` from that shape; verify the field is carried through before relying on it.

- [ ] **Step 4: Build + lint**

Run: `cd frontend; npm run build; npm run lint`
Expected: type-check + lint clean. (Fix field-name mismatches surfaced here against the real
title/job types.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue/DamagedTrackNotice.tsx frontend/src/components/ReviewQueue.tsx frontend/src/app/components/DiscCard.tsx
git commit -m "feat(rerip): damaged-track review notice + DiscCard indicator"
```

---

## Task 9: Simulation seed + E2E coverage

**Files:**
- Modify: `backend/app/services/simulation_service.py` and `backend/app/api/routes.py` (DEBUG-only seed)
- Create: `frontend/e2e/rerip.spec.ts`

- [ ] **Step 1: Add a DEBUG-only seed helper**

In `backend/app/services/simulation_service.py`, add a method that creates a `REVIEW_NEEDED` job
with one `incomplete_rip` REVIEW title (mirrors the existing simulation seeders):

```python
    async def seed_incomplete_rip(self, volume_label: str = "DAMAGED_DISC_S1D1") -> dict:
        """DEBUG-only: seed a REVIEW_NEEDED job with one incomplete_rip review title."""
        import json as _json

        from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState

        async with async_session() as session:
            job = DiscJob(
                drive_id="F:",
                volume_label=volume_label,
                content_type=ContentType.TV,
                state=JobState.REVIEW_NEEDED,
                staging_path="/tmp/engram_sim_staging",
                content_hash="SIMHASH123",
                detected_title="Damaged Show",
                detected_season=1,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            title = DiscTitle(
                job_id=job.id,
                title_index=2,
                duration_seconds=2600,
                state=TitleState.REVIEW,
                match_details=_json.dumps(
                    {
                        "error": "incomplete_rip",
                        "message": "Incomplete rip — clean the disc and re-rip this title.",
                        "rerip_eligible": True,
                        "rerip_attempts": 0,
                    }
                ),
            )
            session.add(title)
            await session.commit()
            await session.refresh(title)
            return {"job_id": job.id, "title_id": title.id}
```

In `backend/app/api/routes.py`, expose it among the other `/api/simulate/*` endpoints (which are
already DEBUG-gated):

```python
@router.post("/simulate/seed-incomplete-rip")
async def simulate_seed_incomplete_rip(volume_label: str = "DAMAGED_DISC_S1D1") -> dict:
    _require_debug()  # use the existing DEBUG guard pattern in this file
    from app.services.job_manager import job_manager

    return await job_manager._simulation.seed_incomplete_rip(volume_label)
```

(Match the file's existing DEBUG-gating helper/pattern — reuse whatever the neighboring
`/api/simulate/*` routes use rather than inventing `_require_debug`.)

- [ ] **Step 2: Write the E2E spec**

Create `frontend/e2e/rerip.spec.ts`:

```ts
import { expect, test } from '@playwright/test';

test('damaged track shows re-rip affordance in review', async ({ page, request }) => {
  const res = await request.post('/api/simulate/seed-incomplete-rip', {
    data: { volume_label: 'DAMAGED_DISC_S1D1' },
  });
  expect(res.ok()).toBeTruthy();

  await page.goto('/');
  // Open the review surface for the seeded job (follow the app's review entry point).
  await expect(page.getByTestId('damaged-track-notice')).toBeVisible();
  await expect(page.getByTestId('rerip-button')).toBeVisible();
});
```

> Adapt the navigation to how the app opens the review queue for a `REVIEW_NEEDED` job (e.g.
> clicking the job card's Review action). Keep the assertion on the `data-testid` hooks added in
> Task 8.

- [ ] **Step 3: Run the E2E spec**

Run (backend must be running with `DEBUG=true`): `cd frontend; npm run test:e2e -- rerip`
Expected: PASS. (If the review navigation differs, fix the spec's navigation, not the component.)

- [ ] **Step 4: Lint + commit**

```bash
cd backend; uv run ruff check app/services/simulation_service.py app/api/routes.py; uv run ruff format app/services/simulation_service.py app/api/routes.py
git add backend/app/services/simulation_service.py backend/app/api/routes.py frontend/e2e/rerip.spec.ts
git commit -m "test(rerip): DEBUG seed endpoint + E2E for the damaged-track affordance"
```

---

## Task 10: Changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an Unreleased entry**

Under `## [Unreleased]` → `### Added` (create `### Changed` too if needed), add:

```markdown
### Added
- Recover a single damaged track without re-ripping the whole disc. When a title fails at the rip level (a scratch/bad-sector truncation or a ripping stall), Engram now holds it in review with a "clean the disc and reinsert to re-rip this title" prompt. Reinserting the **same** disc (verified by its content fingerprint) automatically re-rips just that track, re-matches it, and finishes the job — with a manual "Re-rip this title" button and a bounded automatic-retry cap as fallbacks. (#NNN)

### Changed
- A disc with an unrecoverable track no longer auto-completes with that track silently failed; it now waits in review until the track is re-ripped or explicitly skipped, so a "completed" job means every title succeeded. (#NNN)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): single-track re-rip after clean & reinsert"
```

---

## Final verification

- [ ] **Backend suite:** `cd backend; uv run pytest tests/unit tests/integration -q` — green modulo the known `test_movie_ambiguous_rip_first_workflow` flake.
- [ ] **Backend lint:** `cd backend; uv run ruff check .; uv run ruff format --check .` — clean.
- [ ] **Frontend:** `cd frontend; npm run build; npm run lint; npm run test:unit` — clean.
- [ ] **Servers:** stop any `uvicorn`/`makemkvcon` processes started during testing before opening the PR (scoped by port per CLAUDE.md; never `--reload`).
- [ ] **PR:** one PR for Feature C off `main`; reference the spec; commits per task.

---

## Self-Review

**Spec coverage:**
- §2 completion semantics (COMPLETED = all succeeded; rip failures → REVIEW_NEEDED; no terminal reopen) → Tasks 2 (truncated) + 3 (stalls) reroute to REVIEW; verified by `route_*` and `on_title_error_routes` tests. ✓
- §3 trigger marking (`incomplete_rip`/`rip_stalled` + `rerip_eligible` + `rerip_attempts`) → Tasks 1–3 (column, helper, codes). ✓
- §4 reinsert interception in `_create_job_for_disc` → Task 5 (`_find_rerip_job` + hook). ✓
- §5 `rerip_titles` (subset re-rip, stale-file delete, reuse callbacks, eject) → Task 4. ✓
- §6 retry cap + manual endpoint (hash verify, cap bypass) → `RERIP_MAX_ATTEMPTS` (Task 2), Task 6. ✓
- §7 frontend (ReviewQueue notice, DiscCard indicator, WS via existing events) → Tasks 7–8. ✓
- §8 edge cases: hash mismatch/null → `_find_rerip_job` returns None (Task 5 test); busy MATCHING job excluded (Task 5 test); cleared job un-hidden (Task 4 code); all-unreadable → REVIEW (Task 3 reroute). ✓
- §9 testing incl. simulation note + E2E → Task 9. ✓

**Placeholder scan:** every code step is complete. The one stray-import note in Task 2 Step 1 is explicitly flagged for deletion. No "TBD"/"handle errors"/"similar to". ✓

**Type consistency:** `route_rip_failure_to_review(job_id, title_id, error_code, message)` defined in Task 2, called identically in Tasks 3–5. `RIP_FAILURE_ERROR_CODES` (frozenset) defined Task 2, imported in Task 5. `_is_auto_rerippable(title)` and `_find_rerip_job(new_hash) -> (job_id, [title_id]) | None` consistent across Tasks 5–6. `rerip_titles(job_id, title_ids)` defined Task 4, called in Tasks 5–6. `getRerippableState(matchDetails) -> RerippableState` defined Task 7, used in Task 8. `reripTitle(jobId, titleId)` defined Task 7, used in Tasks 8. Migration `revision=d4e5f6a7b8c9`, `down_revision=c5e9a1b3d7f2` (current head). ✓

**Risk notes:**
- Task 3 is the only behavior change to existing flows — it WILL break stall→FAILED test assertions; Steps 5–6 budget for updating them. Reconfirm only stall-specific assertions change.
- Frontend field names (`title.match_details`, `job.titles`) must be verified against the real types in Task 8 Step 4 — the plan flags this inline.
- The `INCOMPLETE_RIP_MESSAGE` import into `job_manager.py` (Task 4) may need to be lazy if a circular import appears; the step says so.
