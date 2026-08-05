# Import Re-Import Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a previously-imported folder re-importable, so a mistaken import can be corrected without renaming the source folder (issue #571).

**Architecture:** Replace the manual-import dedup guard's "has this path ever been touched?" rule with "is this path owned by an in-flight job?". In-flight jobs hard-block; `COMPLETED` soft-blocks and is overridable by an explicit, per-unit user confirmation. `POST /api/import/start` stops returning 409 and instead reports every blocked unit with an opaque `unit_key`, which the client echoes back as `force_keys`. The ImportModal renders those blocks inline and offers Re-import Anyway.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / pytest on the backend; React 18 / TypeScript / Vitest / Playwright on the frontend.

**Spec:** `docs/superpowers/specs/2026-08-04-import-reimport-recovery-design.md`

---

## Corrections to the spec

Two things found while writing this plan. Both override the spec text.

1. **`clearCompleted` must filter on `'failed'`, not `'error'`.** The spec says to include
   `'error'`. That is right for `useDiscFilters`, which operates on **adapted** `Disc`
   objects, and wrong for `useJobManagement`, which operates on raw `Job` objects
   straight from `/api/jobs`. `JobState` is `'failed'`
   (`frontend/src/types/index.ts:11`); the `failed -> error` rename happens in
   `frontend/src/types/adapters.ts:20`. Task 7 uses the correct value in each file.

2. **`StagingJobResult.blocking_job_ids` is `tuple[int, ...]`, not `list[int]`.** The
   dataclass is frozen, and a mutable default would need `field(default_factory=list)`
   for no benefit. It is converted to a list at the JSON boundary.

## File structure

**Backend**

| File | Responsibility |
|---|---|
| `backend/app/services/import_guard.py` (create) | Owns two things and nothing else: the stable `unit_key` for a staging path, and the ownership classification of that path. Kept out of `job_manager.py` (already ~1166 lines and documented as a thin orchestrator) so `routes.py` can compute keys without reaching into job-manager internals. |
| `backend/app/services/job_manager.py` (modify) | `create_job_from_staging` delegates to the guard, gains `force`, and returns `StagingJobResult` instead of a bare `int`. |
| `backend/app/api/routes.py` (modify) | `ImportStartRequest.force_keys`; `import_start` builds the `blocked` array and drops the 409; `/api/staging/import` reports blocks instead of `job_id: -1`. |

**Frontend**

| File | Responsibility |
|---|---|
| `frontend/src/api/client.ts` (modify) | `BlockedUnit` / `ImportStartResult` types; `startImport` gains `forceKeys`. |
| `frontend/src/components/ImportModal.tsx` (modify) | Conflict state, `runImport` helper, and a local `ConflictPanel` component next to the existing `Row` / `Notice` components. |
| `frontend/src/app/hooks/useDiscFilters.ts` (modify) | Add `failedCount`. |
| `frontend/src/app/hooks/useJobManagement.ts` (modify) | `clearCompleted` reaches failed jobs. |
| `frontend/src/app/App.tsx` (modify) | Button gate and label. |

---

## Task 1: Import guard module

**Files:**
- Create: `backend/app/services/import_guard.py`
- Test: `backend/tests/unit/test_import_guard.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_import_guard.py`:

```python
"""Unit tests for staging-path ownership classification and unit keys.

A staging path is OWNED by an in-flight job and merely RECORDED by a finished
one. These tests pin that boundary and the stability of the opaque unit key the
API round-trips through the client.
"""

import importlib

from app.models import DiscJob, JobState
from app.services.import_guard import (
    ImportBlock,
    classify_staging_path,
    unit_key_for,
)

# app.services.job_manager is name-shadowed by the singleton in the package
# __init__, so resolve the real module the way conftest does. Its async_session
# is the one the autouse unit fixture patches to the in-memory StaticPool DB.
jm_mod = importlib.import_module("app.services.job_manager")


async def _insert_job(staging_path: str, state: JobState) -> int:
    async with jm_mod.async_session() as session:
        job = DiscJob(
            drive_id="import",
            volume_label="SEINFELD",
            staging_path=staging_path,
            state=state,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


class TestUnitKey:
    def test_is_stable_for_the_same_path(self):
        assert unit_key_for(r"X:\rips\Seinfeld") == unit_key_for(r"X:\rips\Seinfeld")

    def test_differs_between_paths(self):
        assert unit_key_for(r"X:\rips\Seinfeld") != unit_key_for(r"X:\rips\Deadwood")

    def test_does_not_leak_the_path(self):
        """The key is opaque: a client must not be able to read a path out of it."""
        key = unit_key_for(r"X:\rips\Seinfeld")
        assert "Seinfeld" not in key
        assert len(key) == 16


class TestClassifyStagingPath:
    async def test_unknown_path_is_unblocked(self):
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, r"X:\rips\Nothing")
        assert block is None
        assert ids == []

    async def test_failed_job_does_not_block(self):
        path = r"X:\rips\Seinfeld"
        await _insert_job(path, JobState.FAILED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is None
        assert ids == []

    async def test_completed_job_soft_blocks(self):
        path = r"X:\rips\Sopranos"
        jid = await _insert_job(path, JobState.COMPLETED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.ALREADY_IMPORTED
        assert ids == [jid]

    async def test_in_flight_job_hard_blocks(self):
        path = r"X:\rips\True Detective"
        jid = await _insert_job(path, JobState.IDENTIFYING)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [jid]

    async def test_review_needed_hard_blocks(self):
        """Unlike the disc path, review still owns the files: they sit on disk."""
        path = r"X:\rips\Deadwood"
        jid = await _insert_job(path, JobState.REVIEW_NEEDED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [jid]

    async def test_in_flight_wins_over_completed(self):
        """A path with both a finished and a live job is owned by the live one."""
        path = r"X:\rips\Mixed"
        await _insert_job(path, JobState.COMPLETED)
        live = await _insert_job(path, JobState.RIPPING)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [live]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_import_guard.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'app.services.import_guard'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/import_guard.py`:

```python
"""Ownership rules for manual-import staging paths.

A staging path is *owned* by an in-flight job and merely *recorded* by a
finished one:

* in-flight (any non-terminal state, including REVIEW_NEEDED) hard-blocks, because
  a second job would race the first over the same files on disk;
* COMPLETED soft-blocks, because it is history rather than a live claim, and an
  explicit user re-import overrides it;
* FAILED never blocks.

This replaced a ``state != FAILED`` guard inherited from the import *watch
folder* that PR #463 removed. A poller fires unbidden and forever, so it needs a
permanent memory of every path it has handled. A human pressing Import has
already stated intent, so the same rule became an override of that intent, and
left users renaming folders to escape it (issue #571).

Deliberately NOT a copy of the disc-side guard in ``job_manager._on_disc_inserted``,
which lets REVIEW_NEEDED through: a disc in review has been ejected and the drive
is free, whereas an import in review still holds its files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select as sa_select

from app.models import TERMINAL_JOB_STATES, DiscJob, JobState


class ImportBlock(StrEnum):
    """Why a staging path refused a new job."""

    IN_FLIGHT = "in_flight"
    ALREADY_IMPORTED = "already_imported"


@dataclass(frozen=True)
class StagingJobResult:
    """Outcome of a create-job-from-staging attempt.

    Replaces a bare ``int`` with a ``-1`` sentinel. The sentinel could encode
    "it didn't happen" but had nowhere to put *why*, so the reason was discarded
    at the API boundary and the caller could only reconstruct a count.
    """

    job_id: int | None = None
    block: ImportBlock | None = None
    blocking_job_ids: tuple[int, ...] = ()


def unit_key_for(dedup_path: str) -> str:
    """Return the stable, opaque key identifying an import unit.

    Hashed rather than sent as the raw path for two reasons: a client cannot
    parse a digest into a path and start depending on its structure, which makes
    the opacity of the wire contract enforceable rather than merely documented;
    and absolute filesystem paths stay out of a value the UI round-trips.

    Pure function of the path, so no server-side state is needed: the forced
    second call re-scans, re-derives a key per unit, and matches the incoming
    ``force_keys`` against those.
    """
    return hashlib.sha256(str(dedup_path).encode("utf-8")).hexdigest()[:16]


async def classify_staging_path(
    session: AsyncSession, staging_path: str
) -> tuple[ImportBlock | None, list[int]]:
    """Classify a staging path's ownership.

    Returns the block tier and the ids of the jobs responsible for it, or
    ``(None, [])`` when the path is free. In-flight beats completed: a path with
    both a finished and a live job is owned by the live one.
    """
    result = await session.execute(sa_select(DiscJob).where(DiscJob.staging_path == staging_path))
    jobs = list(result.scalars().all())

    in_flight = [j.id for j in jobs if j.state not in TERMINAL_JOB_STATES]
    if in_flight:
        return ImportBlock.IN_FLIGHT, in_flight

    completed = [j.id for j in jobs if j.state == JobState.COMPLETED]
    if completed:
        return ImportBlock.ALREADY_IMPORTED, completed

    return None, []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_import_guard.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Lint**

Run: `cd backend && uv run ruff check app/services/import_guard.py tests/unit/test_import_guard.py && uv run ruff format app/services/import_guard.py tests/unit/test_import_guard.py`
Expected: `All checks passed!` then `2 files left unchanged` (or reformatted)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/import_guard.py backend/tests/unit/test_import_guard.py
git commit -m "feat: add staging-path ownership guard for manual import (#571)"
```

---

## Task 2: create_job_from_staging returns a result object

**Files:**
- Modify: `backend/app/services/job_manager.py:771-861`
- Test: `backend/tests/unit/test_create_job_from_staging_dedup.py` (rewrite)

- [ ] **Step 1: Rewrite the test file**

Replace the whole contents of `backend/tests/unit/test_create_job_from_staging_dedup.py`.
The old module docstring describes the import watch folder, which PR #463 deleted;
`test_completed_job_still_blocks_reimport` pinned that behavior and explicitly guarded
against this change, so it is replaced deliberately rather than removed. The FAILED and
in-flight cases survive unchanged, which is the signal that the guard was narrowed
rather than dropped.

```python
"""Unit tests for the ownership guard in JobManager.create_job_from_staging.

A staging path can be re-submitted at any time (a manual re-import, a retry after
a mistake, a server restart). Ownership, not history, decides whether that is
allowed:

* an in-flight job hard-blocks, so two jobs can never race over the same files;
* a COMPLETED job soft-blocks, and an explicit ``force=True`` re-import overrides it;
* a FAILED job never blocks (regression guard: jobs left FAILED by a cancel or a
  restart recovery once silently wedged re-import).

The soft tier replaced an unconditional block on every non-FAILED state, which
was inherited from the polled watch folder PR #463 removed and left users renaming
folders to escape it (issue #571). See
docs/superpowers/specs/2026-08-04-import-reimport-recovery-design.md.
"""

import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest

from app.models import DiscJob, JobState
from app.services.import_guard import ImportBlock

# app.services.job_manager is name-shadowed by the singleton in the package
# __init__, so `from app.services import job_manager` yields the instance, not
# the module. Resolve the real module the same way conftest does.
jm_mod = importlib.import_module("app.services.job_manager")
job_manager = jm_mod.job_manager


async def _insert_job(staging_path: str, state: JobState) -> int:
    """Insert a DiscJob for a given staging_path/state into the in-memory DB."""
    async with jm_mod.async_session() as session:
        job = DiscJob(
            drive_id="import",
            volume_label="SEINFELD",
            staging_path=staging_path,
            state=state,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Prevent create_job_from_staging from spawning the real pipeline/broadcast."""
    monkeypatch.setattr(job_manager._identification, "identify_from_staging", AsyncMock())
    monkeypatch.setattr(jm_mod.event_broadcaster, "broadcast_drive_inserted", AsyncMock())


async def _drain(job_id: int) -> None:
    """Await and clear the background task create_job_from_staging spawned."""
    task = job_manager._active_jobs.pop(job_id, None)
    # Assert rather than skip silently: if _active_jobs is ever renamed this
    # helper would become a no-op (leaked task) while the test still passed.
    assert task is not None, f"no active task tracked for job {job_id}"
    # gather() (not a bare `await task`) so the await is a call expression —
    # avoids CodeQL py/ineffectual-statement misflagging the bare await.
    await asyncio.gather(task)


class TestCreateJobFromStagingOwnership:
    async def test_failed_job_does_not_block_reimport(self, stub_pipeline):
        """A FAILED job for the path must not prevent a fresh import job."""
        path = r"X:\media\rips\Seinfeld"
        failed_id = await _insert_job(path, JobState.FAILED)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SEINFELD", drive_id="import"
        )

        assert result.job_id is not None, "FAILED job wrongly blocked re-import"
        assert result.job_id != failed_id
        assert result.block is None
        await _drain(result.job_id)

    async def test_active_job_hard_blocks_reimport(self, stub_pipeline):
        """A non-terminal (in-flight) job for the path must still be deduped."""
        path = r"X:\media\rips\True Detective\Season 1"
        jid = await _insert_job(path, JobState.IDENTIFYING)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SEASON_1", drive_id="import"
        )

        assert result.job_id is None, "active job should still dedup to prevent duplicates"
        assert result.block is ImportBlock.IN_FLIGHT
        assert result.blocking_job_ids == (jid,)

    async def test_review_needed_job_hard_blocks_reimport(self, stub_pipeline):
        """A REVIEW_NEEDED job still owns its files: they remain on disk."""
        path = r"X:\media\rips\Deadwood"
        jid = await _insert_job(path, JobState.REVIEW_NEEDED)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="DEADWOOD", drive_id="import"
        )

        assert result.job_id is None, "review-pending job should still dedup"
        assert result.block is ImportBlock.IN_FLIGHT
        assert result.blocking_job_ids == (jid,)

    async def test_completed_job_soft_blocks_reimport(self, stub_pipeline):
        """A COMPLETED job blocks by default, and names itself so the UI can offer force."""
        path = r"X:\media\rips\Sopranos"
        jid = await _insert_job(path, JobState.COMPLETED)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SOPRANOS", drive_id="import"
        )

        assert result.job_id is None
        assert result.block is ImportBlock.ALREADY_IMPORTED
        assert result.blocking_job_ids == (jid,)

    async def test_force_overrides_a_completed_job(self, stub_pipeline):
        """force=True re-imports a finished path: the fix for issue #571."""
        path = r"X:\media\rips\Sopranos"
        old_id = await _insert_job(path, JobState.COMPLETED)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SOPRANOS", drive_id="import", force=True
        )

        assert result.job_id is not None, "force must override a completed import"
        assert result.job_id != old_id
        assert result.block is None
        await _drain(result.job_id)

    async def test_force_never_overrides_an_in_flight_job(self, stub_pipeline):
        """The hard tier is not forceable: a second job would race the first."""
        path = r"X:\media\rips\True Detective\Season 1"
        jid = await _insert_job(path, JobState.RIPPING)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SEASON_1", drive_id="import", force=True
        )

        assert result.job_id is None, "force must not defeat an in-flight job"
        assert result.block is ImportBlock.IN_FLIGHT
        assert result.blocking_job_ids == (jid,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_create_job_from_staging_dedup.py -v`
Expected: FAIL. `AttributeError: 'int' object has no attribute 'job_id'` on the passing
cases, and `TypeError: create_job_from_staging() got an unexpected keyword argument 'force'`
on the two force cases.

- [ ] **Step 3: Change the signature and imports**

In `backend/app/services/job_manager.py`, add to the imports near line 44 (alongside the
other `app.services.*` imports):

```python
from app.services.import_guard import (
    ImportBlock,
    StagingJobResult,
    classify_staging_path,
)
```

Change the signature at `backend/app/services/job_manager.py:771-781` to:

```python
    async def create_job_from_staging(
        self,
        staging_path: str,
        volume_label: str = "",
        content_type: str = "unknown",
        detected_title: str | None = None,
        detected_season: int | None = None,
        destination_mode: str = "library",
        drive_id: str = "staging",
        import_manifest: dict | None = None,
        force: bool = False,
    ) -> StagingJobResult:
        """Create a job from pre-ripped MKV files in a staging directory.

        Returns a StagingJobResult: either a created job id, or the block tier and
        the ids of the jobs responsible for it. ``force`` suppresses the soft
        (ALREADY_IMPORTED) tier only and can never suppress the hard one.
        """
```

Delete the now-unused local import on the following line:

```python
        from sqlmodel import select as sa_select
```

- [ ] **Step 4: Replace the guard body**

Replace `backend/app/services/job_manager.py:800-815` (the comment block through
`return -1`) with:

```python
                # Guard: a staging path is OWNED by an in-flight job and merely
                # RECORDED by a finished one. See app/services/import_guard.py for
                # why this is not the old `state != FAILED` rule, and why it is not
                # a copy of the disc-side guard either.
                block, blocking_ids = await classify_staging_path(session, str(staging_dir))
                if force and block is ImportBlock.ALREADY_IMPORTED:
                    block = None
                if block is not None:
                    safe_path = str(staging_dir).replace("\n", "").replace("\r", "")
                    logger.info(
                        "Import blocked for staging path %s (%s, blocking jobs %s)",
                        safe_path,
                        block.value,
                        blocking_ids,
                    )
                    return StagingJobResult(
                        block=block, blocking_job_ids=tuple(blocking_ids)
                    )
```

- [ ] **Step 5: Change the success return**

Change the final line of the method (`backend/app/services/job_manager.py:861`) from
`return job_id` to:

```python
        return StagingJobResult(job_id=job_id)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_create_job_from_staging_dedup.py tests/unit/test_import_guard.py -v`
Expected: PASS, 16 passed

- [ ] **Step 7: Confirm the callers are now broken, as expected**

Run: `cd backend && uv run pytest tests/unit/test_staging_import.py tests/integration/test_import_endpoints.py -v`
Expected: FAIL. `test_valid_staging_creates_job` returns `job_id` as a serialized
object, and the import-start tests fail because `result.job_id` is read as `jid != -1`.
Tasks 3 and 4 fix these. Do not fix them here.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_create_job_from_staging_dedup.py
git commit -m "feat: two-tier ownership guard for staging imports (#571)"
```

---

## Task 3: import_start per-unit conflict contract

**Files:**
- Modify: `backend/app/api/routes.py:2807-2809` (request model), `:2904-2974` (handler)
- Test: `backend/tests/integration/test_import_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_import_endpoints.py`, after
`test_start_no_mkvs_400` (line 146). The module's autouse `_clean_import_jobs` fixture
already deletes `drive_id = 'import'` rows around each test.

```python
async def _seed_job(staging_path: Path, state) -> int:
    """Insert a prior import job for a staging path, bypassing the pipeline.

    Deliberately NOT "call /api/import/start, then mutate the row": that leaves the
    identification background task in flight, and it can overwrite the state this
    test just set up. Seeding directly is race-free.

    The staging path a unit deduplicates on is os.path.commonpath(files), which for
    a single-file unit is that file's parent directory. Every helper below seeds one
    .mkv per season folder, so the season folder IS the dedup path.
    """
    from app.models.disc_job import DiscJob

    async with async_session() as session:
        job = DiscJob(
            drive_id="import",
            volume_label="PRIOR",
            staging_path=str(staging_path),
            state=state,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def test_start_reports_completed_units_as_blocked(client, tmp_path: Path):
    """A finished import soft-blocks and names itself, instead of a dead 409."""
    from app.models import JobState

    season = tmp_path / "Sopranos" / "Season 1"
    _mkv(season / "e1.mkv")
    prior_id = await _seed_job(season, JobState.COMPLETED)

    res = await client.post(
        "/api/import/start", json={"path": str(season), "destination_mode": "library"}
    )
    assert res.status_code == 200, "a conflict is an outcome, not an error"
    body = res.json()
    assert body["job_ids"] == []
    assert len(body["blocked"]) == 1
    blocked = body["blocked"][0]
    assert blocked["reason"] == "already_imported"
    assert blocked["job_ids"] == [prior_id]
    assert blocked["season"] == 1
    assert blocked["unit_key"]


async def test_start_force_keys_reimports_a_completed_unit(client, tmp_path: Path):
    """The fix for #571: an explicit re-import overrides a finished job."""
    from app.models import JobState

    season = tmp_path / "Sopranos" / "Season 1"
    _mkv(season / "e1.mkv")
    prior_id = await _seed_job(season, JobState.COMPLETED)

    blocked = (
        await client.post(
            "/api/import/start", json={"path": str(season), "destination_mode": "library"}
        )
    ).json()["blocked"]
    key = blocked[0]["unit_key"]

    forced = await client.post(
        "/api/import/start",
        json={"path": str(season), "destination_mode": "library", "force_keys": [key]},
    )
    assert forced.status_code == 200
    body = forced.json()
    assert body["blocked"] == []
    assert len(body["job_ids"]) == 1
    assert body["job_ids"][0] != prior_id


async def test_start_reports_partial_conflicts_without_silent_skips(client, tmp_path: Path):
    """Some units start, some are blocked, and the response says which."""
    from app.models import JobState

    show = tmp_path / "Seinfeld"
    _mkv(show / "Season 1" / "e1.mkv")
    _mkv(show / "Season 2" / "e1.mkv")
    _mkv(show / "Season 3" / "e1.mkv")
    await _seed_job(show / "Season 1", JobState.COMPLETED)
    await _seed_job(show / "Season 2", JobState.COMPLETED)

    res = await client.post(
        "/api/import/start", json={"path": str(show), "destination_mode": "library"}
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["job_ids"]) == 1, "the new season must start"
    assert len(body["blocked"]) == 2, "the finished seasons must be reported, not skipped"
    assert {b["season"] for b in body["blocked"]} == {1, 2}
    assert all(b["reason"] == "already_imported" for b in body["blocked"])


async def test_start_force_keys_are_scoped_to_the_units_given(client, tmp_path: Path):
    """Forcing one unit must not force another the user never confirmed."""
    from app.models import JobState

    show = tmp_path / "Seinfeld"
    _mkv(show / "Season 1" / "e1.mkv")
    _mkv(show / "Season 2" / "e1.mkv")
    await _seed_job(show / "Season 1", JobState.COMPLETED)
    await _seed_job(show / "Season 2", JobState.COMPLETED)

    blocked = (
        await client.post(
            "/api/import/start", json={"path": str(show), "destination_mode": "library"}
        )
    ).json()["blocked"]
    assert len(blocked) == 2
    s1_key = next(b["unit_key"] for b in blocked if b["season"] == 1)

    forced = await client.post(
        "/api/import/start",
        json={"path": str(show), "destination_mode": "library", "force_keys": [s1_key]},
    )
    body = forced.json()
    assert len(body["job_ids"]) == 1
    assert len(body["blocked"]) == 1
    assert body["blocked"][0]["season"] == 2


async def test_start_reports_in_flight_units_as_unforceable(client, tmp_path: Path):
    """An in-flight job hard-blocks, and force_keys must not defeat it."""
    from app.models import JobState

    season = tmp_path / "Deadwood" / "Season 1"
    _mkv(season / "e1.mkv")
    await _seed_job(season, JobState.REVIEW_NEEDED)

    blocked = (
        await client.post(
            "/api/import/start", json={"path": str(season), "destination_mode": "library"}
        )
    ).json()["blocked"]
    assert blocked[0]["reason"] == "in_flight"

    forced = await client.post(
        "/api/import/start",
        json={
            "path": str(season),
            "destination_mode": "library",
            "force_keys": [blocked[0]["unit_key"]],
        },
    )
    body = forced.json()
    assert body["job_ids"] == []
    assert body["blocked"][0]["reason"] == "in_flight"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -v -k "blocked or force or partial"`
Expected: FAIL with `KeyError: 'blocked'` (the handler does not return that key yet).

- [ ] **Step 3: Add force_keys to the request model**

Replace `backend/app/api/routes.py:2807-2809` with:

```python
class ImportStartRequest(BaseModel):
    path: str
    destination_mode: str = "library"
    # Unit keys the user explicitly confirmed re-importing. Scoped rather than a
    # blanket `force: bool` so a unit that became blocked between the two calls
    # cannot be forced by a confirmation the user gave about a different unit.
    force_keys: list[str] = []
```

- [ ] **Step 4: Rewrite the handler body**

Replace `backend/app/api/routes.py:2928-2974` (from `root_str = str(scan.root)` through
the final `return`) with:

```python
    root_str = str(scan.root)
    force_keys = set(req.force_keys)
    job_ids: list[int] = []
    blocked: list[dict] = []
    for unit in scan.units:
        files = [str(f) for f in unit.files]
        staging = str(Path(files[0]).parent) if len(files) == 1 else os.path.commonpath(files)
        # The key must be derived from the dedup path, never scan.root and never
        # the pre-resolve() req.path, or force_keys will not round-trip.
        key = unit_key_for(staging)
        manifest = {
            "root": root_str,
            "files": files,
            "picked_is_show": scan.picked_is_show,
            "picked_is_season": scan.picked_is_season,
        }
        result = await job_manager.create_job_from_staging(
            staging_path=staging,
            content_type="tv" if unit.season is not None else "unknown",
            detected_title=unit.show_name,
            detected_season=unit.season,
            destination_mode=req.destination_mode,
            drive_id="import",
            import_manifest=manifest,
            force=key in force_keys,
        )
        if result.job_id is not None:
            job_ids.append(result.job_id)
        else:
            blocked.append(
                {
                    "unit_key": key,
                    "show_name": unit.show_name,
                    "season": unit.season,
                    "display_path": staging,
                    "reason": result.block.value,
                    "job_ids": list(result.blocking_job_ids),
                }
            )

    # Persist the path/destination as next-time defaults even when everything was
    # blocked, so the modal still reopens where the user last browsed.
    await config_service.update_config(
        import_watch_path=req.path, import_destination_mode=req.destination_mode
    )
    logger.info(
        "Import start: %s -> %d job(s) created, %d blocked",
        sanitize_log_value(req.path),
        len(job_ids),
        len(blocked),
    )
    # No 409: a conflict is a normal outcome carrying its own remedy (force_keys),
    # and a non-2xx alongside partial success would invite a blind client retry
    # that double-starts the units that succeeded.
    return {"job_ids": job_ids, "blocked": blocked}
```

- [ ] **Step 5: Add the guard import to the handler**

In `import_start`, extend the local import block at `backend/app/api/routes.py:2911-2913` to:

```python
    from app.core import import_scanner
    from app.services import config_service
    from app.services.import_guard import unit_key_for
    from app.services.job_manager import job_manager
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -v`
Expected: PASS, all tests including the four pre-existing ones

- [ ] **Step 7: Lint and commit**

```bash
cd backend && uv run ruff check app/api/routes.py tests/integration/test_import_endpoints.py && uv run ruff format app/api/routes.py tests/integration/test_import_endpoints.py
```

```bash
git add backend/app/api/routes.py backend/tests/integration/test_import_endpoints.py
git commit -m "feat: per-unit import conflict reporting with scoped force (#571)"
```

---

## Task 4: staging/import reports blocks instead of job_id -1

**Files:**
- Modify: `backend/app/api/routes.py:3007-3015`
- Test: `backend/tests/unit/test_staging_import.py:69-97`

`POST /api/staging/import` currently returns `{"status": "created", "job_id": -1}` when
the guard blocks: success status, impossible id. Same root cause as `import_start`.

- [ ] **Step 1: Write the failing test**

In `backend/tests/unit/test_staging_import.py`, add this import at the top with the others:

```python
from app.services.import_guard import ImportBlock, StagingJobResult
```

Change the mock at `backend/tests/unit/test_staging_import.py:80` from
`return_value=42` to `return_value=StagingJobResult(job_id=42)`, then add this test to
`TestStagingImportEndpoint`:

```python
    async def test_blocked_staging_reports_the_reason(self, client: AsyncClient, tmp_path):
        """A blocked path must not report success with an impossible job id."""
        staging_dir = tmp_path / "ALREADY_DONE"
        staging_dir.mkdir()
        (staging_dir / "title_t00.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=StagingJobResult(
                block=ImportBlock.ALREADY_IMPORTED, blocking_job_ids=(7,)
            ),
        ):
            resp = await client.post(
                "/api/staging/import", json={"staging_path": str(staging_dir)}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "blocked"
        assert data["job_id"] is None
        assert data["reason"] == "already_imported"
        assert data["blocking_job_ids"] == [7]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_staging_import.py -v`
Expected: FAIL. `test_blocked_staging_reports_the_reason` asserts `status == "blocked"`
but gets `"created"`.

- [ ] **Step 3: Write the implementation**

Replace `backend/app/api/routes.py:3007-3015` with:

```python
    result = await job_manager.create_job_from_staging(
        staging_path=str(staging_dir),
        volume_label=request.volume_label,
        content_type=request.content_type,
        detected_title=request.detected_title,
        detected_season=request.detected_season,
    )

    if result.job_id is None:
        # Never report "created" with a sentinel id: the caller has to be able to
        # tell a real job from a refused one.
        return {
            "status": "blocked",
            "job_id": None,
            "reason": result.block.value,
            "blocking_job_ids": list(result.blocking_job_ids),
            "titles_count": len(mkv_files),
        }

    return {"status": "created", "job_id": result.job_id, "titles_count": len(mkv_files)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_staging_import.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole backend unit tier for regressions**

Run: `cd backend && uv run pytest tests/unit tests/integration -q`
Expected: PASS. If anything else calls `create_job_from_staging` and reads an `int`,
it surfaces here.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app/api/routes.py tests/unit/test_staging_import.py && uv run ruff format app/api/routes.py tests/unit/test_staging_import.py
```

```bash
git add backend/app/api/routes.py backend/tests/unit/test_staging_import.py
git commit -m "fix: staging import reports blocks instead of job_id -1 (#571)"
```

---

## Task 5: client types and startImport forceKeys

**Files:**
- Modify: `frontend/src/api/client.ts:366-376`
- Test: `frontend/src/api/importClient.test.ts:34-41`

- [ ] **Step 1: Update the failing test**

`importClient.test.ts:40` asserts the request body with `toEqual`, so adding a field
breaks it. Replace the `startImport` test at `frontend/src/api/importClient.test.ts:34-41`
with:

```ts
  it("startImport posts path, destination, and force keys", async () => {
    const fetchMock = mockJson({ job_ids: [1, 2], blocked: [] });
    vi.stubGlobal("fetch", fetchMock);
    const res = await startImport("/x", "library");
    expect(res.job_ids).toEqual([1, 2]);
    expect(res.blocked).toEqual([]);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toEqual({ path: "/x", destination_mode: "library", force_keys: [] });
  });

  it("startImport forwards explicit force keys", async () => {
    const fetchMock = mockJson({ job_ids: [3], blocked: [] });
    vi.stubGlobal("fetch", fetchMock);
    await startImport("/x", "library", ["abc123"]);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.force_keys).toEqual(["abc123"]);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/importClient.test.ts`
Expected: FAIL. Body is `{ path, destination_mode }`, missing `force_keys`.

- [ ] **Step 3: Write the implementation**

Replace `frontend/src/api/client.ts:366-376` with:

```ts
/** Why a scanned unit was not started by {@link startImport}. */
export type ImportBlockReason = "in_flight" | "already_imported";

/** One (show, season) unit that `/api/import/start` refused to start. */
export interface BlockedUnit {
  /**
   * Opaque token identifying the unit. Echo it back in `force_keys`; never parse
   * it. It is deliberately not a path so the server can change how identity is
   * derived without breaking this client.
   */
  unit_key: string;
  show_name: string | null;
  season: number | null;
  /** Human-readable only. Never use as an identity. */
  display_path: string;
  reason: ImportBlockReason;
  /** Prior jobs responsible for the block. */
  job_ids: number[];
}

export interface ImportStartResult {
  job_ids: number[];
  blocked: BlockedUnit[];
}

/**
 * Create one import job per (show, season) unit under path.
 *
 * Units already owned by another job are reported in `blocked` rather than
 * failing the whole call. Re-send with those units' `unit_key` values in
 * `forceKeys` to re-import ones whose prior job has completed; `in_flight`
 * blocks are never forceable.
 */
export async function startImport(
  path: string,
  destinationMode: "library" | "in_place",
  forceKeys: string[] = [],
): Promise<ImportStartResult> {
  return apiFetch<ImportStartResult>("/api/import/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      destination_mode: destinationMode,
      force_keys: forceKeys,
    }),
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/importClient.test.ts`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/importClient.test.ts
git commit -m "feat: import client carries per-unit conflicts and force keys (#571)"
```

---

## Task 6: ImportModal conflict panel

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx:1-13` (imports), `:118-129` (onStart), `:380` (render), end of file (new component)
- Test: `frontend/src/components/ImportModal.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/ImportModal.test.tsx`, first fix the existing mock and
assertion for the widened contract.

Change line 30 from `mockResolvedValue({ job_ids: [1] })` to:

```ts
  vi.spyOn(client, "startImport").mockResolvedValue({ job_ids: [1], blocked: [] });
```

Change the assertion at line 47 to include the third argument:

```ts
    await waitFor(() => expect(client.startImport).toHaveBeenCalledWith("/media/King of Queens", "library", []));
```

Then add these tests inside the `describe("ImportModal", ...)` block:

```ts
  const blockedUnit = (over: Partial<client.BlockedUnit> = {}): client.BlockedUnit => ({
    unit_key: "key-s1",
    show_name: "King of Queens",
    season: 1,
    display_path: "/media/King of Queens/Season 1",
    reason: "already_imported",
    job_ids: [7],
    ...over,
  });

  async function startAndExpectConflict(result: client.ImportStartResult) {
    vi.mocked(client.startImport).mockResolvedValueOnce(result);
    const onClose = vi.fn();
    render(<ImportModal onClose={onClose} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));
    fireEvent.click(screen.getByText("King of Queens"));
    fireEvent.click(await screen.findByTestId("import-start-btn"));
    await waitFor(() => expect(screen.getByTestId("import-conflict-panel")).toBeInTheDocument());
    return onClose;
  }

  it("keeps the modal open and reports blocked units instead of closing silently", async () => {
    const onClose = await startAndExpectConflict({ job_ids: [], blocked: [blockedUnit()] });
    expect(screen.getByText(/previously imported/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("re-imports only the soft-blocked units when Re-import Anyway is pressed", async () => {
    await startAndExpectConflict({
      job_ids: [],
      blocked: [
        blockedUnit(),
        blockedUnit({ unit_key: "key-s2", season: 2, reason: "in_flight", job_ids: [9] }),
      ],
    });

    fireEvent.click(screen.getByTestId("import-force-btn"));

    // Only the already_imported unit is forced; the in_flight one is never forceable.
    await waitFor(() =>
      expect(client.startImport).toHaveBeenLastCalledWith(
        "/media/King of Queens",
        "library",
        ["key-s1"],
      ),
    );
  });

  it("offers no force action when every block is in flight", async () => {
    await startAndExpectConflict({
      job_ids: [],
      blocked: [blockedUnit({ reason: "in_flight", job_ids: [9] })],
    });
    expect(screen.getByText(/already processing/i)).toBeInTheDocument();
    expect(screen.queryByTestId("import-force-btn")).not.toBeInTheDocument();
  });

  it("reports how many units started when only some were blocked", async () => {
    await startAndExpectConflict({ job_ids: [11], blocked: [blockedUnit()] });
    expect(screen.getByText(/started 1 of 2/i)).toBeInTheDocument();
  });

  it("closes once a forced re-import succeeds", async () => {
    const onClose = await startAndExpectConflict({ job_ids: [], blocked: [blockedUnit()] });
    vi.mocked(client.startImport).mockResolvedValueOnce({ job_ids: [12], blocked: [] });
    fireEvent.click(screen.getByTestId("import-force-btn"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ImportModal.test.tsx`
Expected: FAIL. `Unable to find an element by: [data-testid="import-conflict-panel"]`.

- [ ] **Step 3: Update the imports**

Replace `frontend/src/components/ImportModal.tsx:7-13` with:

```tsx
import {
  browseDir,
  previewImport,
  startImport,
  type BlockedUnit,
  type BrowseEntry,
  type ImportStartResult,
  type PreviewResult,
} from "../api/client";
```

- [ ] **Step 4: Add conflict state**

After `frontend/src/components/ImportModal.tsx:30` (`const [starting, setStarting] = useState(false);`), add:

```tsx
  // Set when a start attempt came back with blocked units. Holds the modal open
  // so the user can act on them instead of the modal closing on a silent skip.
  const [conflict, setConflict] = useState<ImportStartResult | null>(null);
```

- [ ] **Step 5: Replace onStart**

Replace `frontend/src/components/ImportModal.tsx:118-129` with:

```tsx
  const runImport = useCallback(
    async (forceKeys: string[]) => {
      if (!selected) return;
      setStarting(true);
      setError(null);
      try {
        const res = await startImport(selected, destMode, forceKeys);
        if (res.blocked.length === 0) {
          onClose();
          return;
        }
        setConflict(res);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Import failed to start");
      } finally {
        setStarting(false);
      }
    },
    [selected, destMode, onClose],
  );

  const onStart = useCallback(() => {
    if (!preview || preview.total_jobs === 0) return;
    setConflict(null);
    void runImport([]);
  }, [preview, runImport]);

  // Only already_imported units are forceable. in_flight blocks are excluded here
  // and have no button, because a second job would race the live one.
  const onForce = useCallback(() => {
    const keys = (conflict?.blocked ?? [])
      .filter((b) => b.reason === "already_imported")
      .map((b) => b.unit_key);
    if (keys.length > 0) void runImport(keys);
  }, [conflict, runImport]);
```

Note: `runImport` no longer calls `setStarting(false)` before `onClose()`, because the
`finally` handles both paths and the component unmounts on close either way.

- [ ] **Step 6: Render the panel**

Replace `frontend/src/components/ImportModal.tsx:380` (`{error && <Notice text={error} tone="error" />}`) with:

```tsx
                {error && <Notice text={error} tone="error" />}
                {conflict && (
                  <ConflictPanel result={conflict} onForce={onForce} busy={starting} />
                )}
```

- [ ] **Step 7: Add the ConflictPanel component**

Append to the end of `frontend/src/components/ImportModal.tsx`, after `Notice`:

```tsx
/**
 * Blocked-unit report shown in place of closing the modal.
 *
 * Deliberately an inline panel rather than a nested modal: a dismissed modal in
 * this app can leave a stuck opacity-0 overlay that swallows clicks under
 * automation, and nesting one here would put that hazard directly in the path of
 * this feature's E2E test.
 */
function ConflictPanel({
  result,
  onForce,
  busy,
}: {
  result: ImportStartResult;
  onForce: () => void;
  busy: boolean;
}) {
  const soft = result.blocked.filter((b) => b.reason === "already_imported");
  const hard = result.blocked.filter((b) => b.reason === "in_flight");
  const total = result.job_ids.length + result.blocked.length;

  const label = (b: BlockedUnit) =>
    `${b.show_name ?? "Unknown"}${b.season != null ? ` · Season ${b.season}` : ""}`;

  const heading = (text: string) => (
    <div
      style={{
        fontFamily: sv.mono,
        fontSize: 9,
        letterSpacing: "0.15em",
        color: sv.inkFaint,
        marginTop: 8,
        marginBottom: 4,
      }}
    >
      {text}
    </div>
  );

  const row = (key: string, text: string) => (
    <div
      key={key}
      style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim, padding: "2px 0" }}
    >
      {text}
    </div>
  );

  return (
    <div
      data-testid="import-conflict-panel"
      style={{
        marginTop: 10,
        padding: "10px 12px",
        border: `1px solid ${sv.yellow}4d`,
        background: `${sv.yellow}14`,
      }}
    >
      {result.job_ids.length > 0 && (
        <div style={{ fontFamily: sv.mono, fontSize: 11, color: sv.yellow }}>
          Started {result.job_ids.length} of {total}.
        </div>
      )}

      {hard.length > 0 && (
        <>
          {heading("ALREADY PROCESSING")}
          {hard.map((b) => row(b.unit_key, `${label(b)} (job ${b.job_ids.join(", ")})`))}
        </>
      )}

      {soft.length > 0 && (
        <>
          {heading("PREVIOUSLY IMPORTED")}
          {soft.map((b) => row(b.unit_key, label(b)))}
          <button
            onClick={onForce}
            disabled={busy}
            data-testid="import-force-btn"
            style={{
              marginTop: 8,
              fontFamily: sv.mono,
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.1em",
              padding: "6px 12px",
              border: `1px solid ${sv.yellow}`,
              background: "transparent",
              color: sv.yellow,
              cursor: busy ? "not-allowed" : "pointer",
            }}
          >
            RE-IMPORT ANYWAY
          </button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ImportModal.test.tsx`
Expected: PASS, 14 passed

- [ ] **Step 9: Typecheck and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, no ESLint errors

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/ImportModal.tsx frontend/src/components/ImportModal.test.tsx
git commit -m "feat: import modal reports blocked units and offers re-import (#571)"
```

---

## Task 7: Failed-job dismissal

**Files:**
- Modify: `frontend/src/app/hooks/useJobManagement.ts:252-266`
- Modify: `frontend/src/app/hooks/useDiscFilters.ts:51-62`
- Modify: `frontend/src/app/App.tsx:419`, `:423`, `:438`
- Test: `frontend/src/app/hooks/__tests__/useDiscFilters.test.ts` (create)

A failed job is currently undismissable from the UI: `clearCompleted` filters it out,
the button that calls it is gated on `completedCount > 0`, and both filter tabs exclude
it (`useDiscFilters.ts:37` and `:40`), so it only appears under "All".

**Watch the vocabulary.** `useJobManagement` holds raw `Job` objects from `/api/jobs`
where the state is `'failed'`. `useDiscFilters` holds adapted `Disc` objects where the
same state is `'error'` (`frontend/src/types/adapters.ts:20`). Using the wrong string in
either file compiles and silently matches nothing.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/app/hooks/__tests__/useDiscFilters.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDiscFilters } from "../useDiscFilters";
import type { Job } from "../../../types";

function job(id: number, state: Job["state"]): Job {
  return {
    id,
    drive_id: "import",
    volume_label: `VOL${id}`,
    content_type: "tv",
    state,
    current_speed: "0.0x",
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 0,
    error_message: null,
  } as Job;
}

describe("useDiscFilters", () => {
  it("counts failed jobs separately from completed ones", () => {
    const jobs = [job(1, "completed"), job(2, "failed"), job(3, "ripping")];
    const { result } = renderHook(() => useDiscFilters(jobs, {}, false));

    expect(result.current.completedCount).toBe(1);
    // Without this, App.tsx renders no clear button at all on a dashboard that
    // holds only failed jobs, leaving them permanently undismissable.
    expect(result.current.failedCount).toBe(1);
    expect(result.current.activeCount).toBe(1);
  });

  it("reports no failures when there are none", () => {
    const { result } = renderHook(() => useDiscFilters([job(1, "completed")], {}, false));
    expect(result.current.failedCount).toBe(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useDiscFilters.test.ts`
Expected: FAIL. `expected undefined to be 1` on `failedCount`.

- [ ] **Step 3: Add failedCount**

In `frontend/src/app/hooks/useDiscFilters.ts`, insert after the `completedCount` memo
(line 53) and before the `return`:

```ts
    // Failed jobs sit in neither the Active nor the Done bucket above, so without
    // their own count the clear button (gated on completedCount) never renders and
    // they cannot be dismissed at all.
    const failedCount = useMemo(() => {
        return discsData.filter((d) => d.state === "error").length;
    }, [discsData]);
```

and add `failedCount,` to the returned object.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useDiscFilters.test.ts`
Expected: PASS, 2 passed

- [ ] **Step 5: Widen clearCompleted**

Replace `frontend/src/app/hooks/useJobManagement.ts:252-266` with:

```ts
    async function clearCompleted() {
        try {
            // Both terminal states. DELETE /api/jobs/{id} already accepts COMPLETED
            // and FAILED; only this filter was narrower, which left failed jobs with
            // no dismissal affordance anywhere in the UI.
            // NOTE: raw Job state, so 'failed'. The 'error' rename happens in the
            // Disc adapter, which this hook does not use.
            const finishedJobs = jobs.filter(
                j => j.state === 'completed' || j.state === 'failed',
            );
            await Promise.all(
                finishedJobs.map(job =>
                    apiFetchVoid(`/api/jobs/${job.id}`, { method: 'DELETE' }),
                ),
            );
            // Refresh jobs
            await fetchJobsAndTitles();
        } catch (error) {
            console.error('Failed to clear finished jobs:', error);
            toast.error('Failed to clear finished jobs. Please try again.');
        }
    }
```

- [ ] **Step 6: Update the button gate and label**

In `frontend/src/app/App.tsx`, change line 217 to destructure the new count:

```tsx
  const { filter, setFilter, discsData, filteredDiscs, activeCount, completedCount, failedCount } = useDiscFilters(jobs, titlesMap, DEV_MODE);
```

Change line 419 from `{completedCount > 0 && (` to:

```tsx
          {completedCount + failedCount > 0 && (
```

Change line 423 from `title="Clear Completed"` to:

```tsx
              title="Clear Finished"
```

- [ ] **Step 7: Verify nothing else depends on the old shape**

Run: `cd frontend && npm run build`
Expected: build succeeds. `App.routing.test.tsx:33` and `App.test.tsx` stub
`useDiscFilters`; if the build or tests complain about a missing `failedCount`, add
`failedCount: 0,` to those stubs.

- [ ] **Step 8: Run the frontend unit tier**

Run: `cd frontend && npm run test:unit`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add frontend/src/app/hooks/useDiscFilters.ts frontend/src/app/hooks/useJobManagement.ts frontend/src/app/App.tsx frontend/src/app/hooks/__tests__/useDiscFilters.test.ts
git commit -m "fix: failed jobs can be dismissed from the dashboard (#571)"
```

---

## Task 8: E2E for the conflict flow

**Files:**
- Modify: `frontend/e2e/import-flow.spec.ts`

**Why this test intercepts the network.** Driving a real conflict end-to-end would need
the first import to reach `COMPLETED`, but E2E imports use 1 KB fake MKVs whose
identification fails fast, landing the job in `FAILED`, which by design does not block.
The test would race the pipeline and flake. The backend contract is already covered
deterministically by the Task 3 integration tests, so this test covers the part they
cannot: that a real browser renders the panel, keeps the modal open, and sends the right
body on force.

- [ ] **Step 1: Write the failing test**

Add to `frontend/e2e/import-flow.spec.ts` inside the existing `describe` block, after
the first test:

```ts
    test('a blocked unit keeps the modal open and can be re-imported', async ({ page, request }) => {
        const root = seedShowTree();
        await request.put('/api/config', { data: { import_watch_path: root } });

        // The pipeline cannot be driven to COMPLETED reliably with fake MKVs, so the
        // conflict response is served directly. What is under test here is the UI
        // wiring in a real browser; the contract itself is covered by
        // backend/tests/integration/test_import_endpoints.py.
        const startBodies: string[] = [];
        let call = 0;
        await page.route('**/api/import/start', async (route) => {
            startBodies.push(route.request().postData() ?? '');
            call += 1;
            const body =
                call === 1
                    ? {
                          job_ids: [],
                          blocked: [
                              {
                                  unit_key: 'key-s1',
                                  show_name: 'Demo Show',
                                  season: 1,
                                  display_path: `${root}/Demo Show/Season 1`,
                                  reason: 'already_imported',
                                  job_ids: [7],
                              },
                          ],
                      }
                    : { job_ids: [42], blocked: [] };
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
        });

        await page.goto('/');
        await expect(page.locator('text=/LIVE/i')).toBeVisible({ timeout: 10000 });
        await page.getByTestId('sv-import-btn').click();
        await page.getByText('Demo Show', { exact: true }).first().click();
        await expect(page.getByText(/SEASON 1/)).toBeVisible({ timeout: 5000 });

        await page.getByTestId('import-start-btn').click();

        // The modal must stay open and explain itself, not close on a silent skip.
        await expect(page.getByTestId('import-conflict-panel')).toBeVisible();
        await expect(page.getByTestId('import-start-btn')).toBeVisible();

        await page.getByTestId('import-force-btn').click();

        // The modal closes once the forced retry succeeds.
        await expect(page.getByTestId('import-start-btn')).toBeHidden({ timeout: 5000 });

        expect(JSON.parse(startBodies[0]).force_keys).toEqual([]);
        expect(JSON.parse(startBodies[1]).force_keys).toEqual(['key-s1']);
    });
```

- [ ] **Step 2: Run the test**

Run: `cd frontend && npx playwright test e2e/import-flow.spec.ts --reporter=line`
Expected: PASS. Requires the backend running with `DEBUG=true`; the Playwright config's
web server handles this.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/import-flow.spec.ts
git commit -m "test: e2e for the import conflict panel and forced re-import (#571)"
```

---

## Task 9: Documentation

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, add (creating the subsections if absent):

```markdown
### Fixed

- Re-importing a folder that was already imported no longer fails with an unactionable
  conflict. A finished import now warns and offers "Re-import Anyway" instead of blocking
  permanently, so correcting a mistaken season or show no longer requires renaming the
  source folder (#571)
- Importing a folder where only some seasons were previously imported no longer closes the
  dialog and silently skips them; every skipped unit is now listed with the reason (#571)
- Failed jobs can now be dismissed from the dashboard. The clear action covers both
  finished states rather than completed jobs only (#571)
```

- [ ] **Step 2: Update the architecture notes**

In `CLAUDE.md`, in the "Key Patterns" list, add:

```markdown
- **Import path ownership**: a staging path is owned by an *in-flight* job (hard block, not
  overridable) and merely recorded by a *completed* one (soft block, overridable via
  `force_keys`). `FAILED` never blocks. Rules live in `app/services/import_guard.py`;
  `POST /api/import/start` returns `{job_ids, blocked[]}` and never 409s.
```

- [ ] **Step 3: Verify the changelog is well-formed**

Run: `cd backend && uv run python scripts/extract_changelog.py --version 0.28.2`
Expected: prints the 0.28.2 section without error, confirming the file still parses.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs: changelog and architecture notes for import re-import (#571)"
```

---

## Final verification

- [ ] **Backend suite**

Run: `cd backend && uv run pytest tests/unit tests/integration -q`
Expected: PASS

- [ ] **Frontend unit suite**

Run: `cd frontend && npm run test:unit`
Expected: PASS

- [ ] **Frontend build and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: both succeed

- [ ] **E2E import spec**

Run: `cd frontend && npx playwright test e2e/import-flow.spec.ts --reporter=line`
Expected: PASS

- [ ] **Manual smoke test**

1. Start the backend and frontend (see CLAUDE.md for the per-worktree port setup).
2. Import a folder of MKVs; let it finish.
3. Import the same folder again. Expect the modal to stay open with "PREVIOUSLY IMPORTED"
   and a RE-IMPORT ANYWAY button.
4. Press it. Expect a new job to start and the modal to close.
5. Cancel a job to leave it failed, then press CLEAR. Expect it to disappear.

- [ ] **Stop the servers this session started** (CLAUDE.md, Important Rules) before opening the PR.
