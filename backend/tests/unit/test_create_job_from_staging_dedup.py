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
