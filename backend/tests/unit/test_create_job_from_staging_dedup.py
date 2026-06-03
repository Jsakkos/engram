"""Unit tests for the dedup guard in JobManager.create_job_from_staging.

The import watch folder re-detects the same directory on every poll and on
every server restart. A prior *terminal-failed* job for that path must NOT
permanently block re-import (regression: jobs left FAILED by a cancel or a
server-restart recovery silently wedged the watch folder). A still-active or
review-pending job for the path must still be deduped so a polling watcher
cannot spawn duplicate jobs.
"""

import importlib
from unittest.mock import AsyncMock

import pytest

from app.models import DiscJob, JobState

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
    if task is not None:
        await task


class TestCreateJobFromStagingDedup:
    async def test_failed_job_does_not_block_reimport(self, stub_pipeline):
        """A FAILED job for the path must not prevent a fresh import job."""
        path = r"X:\media\rips\Seinfeld"
        failed_id = await _insert_job(path, JobState.FAILED)

        new_id = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SEINFELD", drive_id="import"
        )

        assert new_id != -1, "FAILED job wrongly blocked re-import"
        assert new_id != failed_id
        await _drain(new_id)

    async def test_active_job_still_blocks_reimport(self, stub_pipeline):
        """A non-terminal (in-flight) job for the path must still be deduped."""
        path = r"X:\media\rips\True Detective\Season 1"
        await _insert_job(path, JobState.IDENTIFYING)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="SEASON_1", drive_id="import"
        )

        assert result == -1, "active job should still dedup to prevent duplicates"

    async def test_review_needed_job_still_blocks_reimport(self, stub_pipeline):
        """A REVIEW_NEEDED job must still dedup (files remain in the watch folder)."""
        path = r"X:\media\rips\Deadwood"
        await _insert_job(path, JobState.REVIEW_NEEDED)

        result = await job_manager.create_job_from_staging(
            staging_path=path, volume_label="DEADWOOD", drive_id="import"
        )

        assert result == -1, "review-pending job should still dedup"
