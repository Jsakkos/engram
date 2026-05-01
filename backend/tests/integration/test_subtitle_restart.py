"""Integration tests for subtitle download restart on re-identification.

Regression test for the bug where re-identifying a TV disc with a corrected
title left subtitle download in a stuck "failed" state, gating matching back
into REVIEW.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.api.websocket import manager as ws_manager
from app.database import async_session, init_db
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import MatchingCoordinator


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
def matching_coordinator():
    broadcaster = EventBroadcaster(ws_manager)
    state_machine = JobStateMachine(broadcaster)
    return MatchingCoordinator(broadcaster, state_machine)


async def _seed_failed_subtitle_job(matching: MatchingCoordinator) -> int:
    """Create a job mid-way through a failed subtitle attempt.

    Mirrors the state left behind when initial subtitle download fails:
    DB has `subtitle_status="failed"` + subtitle-prefixed error_message,
    and the in-memory `_subtitle_ready` event has been set() by the
    download task's finally block.
    """
    async with async_session() as session:
        job = DiscJob(
            volume_label="STRANGENEWWORLDS",
            drive_id="E:",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Star Trek: Strange New Worlds",
            detected_season=3,
            subtitle_status="failed",
            error_message="Subtitle download failed: No subtitles found",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    # Simulate the stale event from the prior failed download — set in the
    # finally block at matching_coordinator.py:957-958 regardless of outcome.
    stale_event = asyncio.Event()
    stale_event.set()
    matching._subtitle_ready[job_id] = stale_event

    # And a stale finished task reference, mimicking a completed-but-failed task.
    async def _no_op():
        return None

    stale_task = asyncio.create_task(_no_op())
    await stale_task
    matching._subtitle_tasks[job_id] = stale_task

    return job_id


@pytest.mark.asyncio
async def test_restart_clears_failed_state_and_starts_new_task(matching_coordinator):
    job_id = await _seed_failed_subtitle_job(matching_coordinator)
    stale_event = matching_coordinator._subtitle_ready[job_id]
    stale_task = matching_coordinator._subtitle_tasks[job_id]

    # Stub the actual download so the test stays offline. The new task will
    # await this and write subtitle_status="completed" to the DB.
    async def fake_download(self, jid, show_name, season):
        # Capture the show_name the new task was started with.
        fake_download.last_args = (jid, show_name, season)
        from sqlalchemy import update

        async with async_session() as s:
            await s.execute(
                update(DiscJob).where(DiscJob.id == jid).values(subtitle_status="completed")
            )
            await s.commit()
        # Mirror the real download_subtitles `finally` behavior.
        if jid in self._subtitle_ready:
            self._subtitle_ready[jid].set()

    with patch.object(MatchingCoordinator, "download_subtitles", fake_download):
        await matching_coordinator.restart_subtitle_download(
            job_id, "Star Trek: Strange New Worlds", 3
        )
        # Let the freshly-spawned task run.
        await matching_coordinator._subtitle_tasks[job_id]

    # In-memory state was reset (event/task replaced, not the same instances).
    assert matching_coordinator._subtitle_ready[job_id] is not stale_event
    assert matching_coordinator._subtitle_tasks[job_id] is not stale_task

    # The new task was started with the corrected show name.
    assert fake_download.last_args == (job_id, "Star Trek: Strange New Worlds", 3)

    # DB state was cleared then updated by the new task.
    async with async_session() as session:
        job = await session.get(DiscJob, job_id)
        assert job.subtitle_status == "completed"
        assert job.error_message is None


@pytest.mark.asyncio
async def test_restart_cancels_inflight_task(matching_coordinator):
    """A still-running stale task must be cancelled before the new one starts."""
    async with async_session() as session:
        job = DiscJob(
            volume_label="BOGUS",
            drive_id="E:",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Some Show",
            detected_season=1,
            subtitle_status="downloading",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    started = asyncio.Event()

    async def long_running():
        started.set()
        await asyncio.sleep(60)

    matching_coordinator._subtitle_ready[job_id] = asyncio.Event()
    stale_task = asyncio.create_task(long_running())
    matching_coordinator._subtitle_tasks[job_id] = stale_task

    # Make sure the stale task has actually started running (entered the sleep)
    # before we trigger restart, so cancellation lands at a real await point.
    await started.wait()

    async def fake_download(self, jid, show_name, season):
        return None

    with patch.object(MatchingCoordinator, "download_subtitles", fake_download):
        await matching_coordinator.restart_subtitle_download(job_id, "Some Show", 1)

    assert stale_task.cancelled(), "stale in-flight subtitle task must be cancelled"
    # And it should not be the same task as the new one in the dict.
    assert matching_coordinator._subtitle_tasks[job_id] is not stale_task


@pytest.mark.asyncio
async def test_restart_preserves_non_subtitle_error_message(matching_coordinator):
    """error_message from non-subtitle sources (e.g. matching) must not be wiped."""
    async with async_session() as session:
        job = DiscJob(
            volume_label="BOGUS",
            drive_id="E:",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Some Show",
            detected_season=1,
            subtitle_status="failed",
            error_message="Matching failed: ambiguous results",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    async def fake_download(self, jid, show_name, season):
        return None

    with patch.object(MatchingCoordinator, "download_subtitles", fake_download):
        await matching_coordinator.restart_subtitle_download(job_id, "Some Show", 1)

    async with async_session() as session:
        job = await session.get(DiscJob, job_id)
        assert job.subtitle_status is None
        # Non-subtitle error preserved.
        assert job.error_message == "Matching failed: ambiguous results"


@pytest.mark.asyncio
async def test_rerun_matching_retries_subtitles_when_failed():
    """JobManager.rerun_matching should restart subtitles for stuck TV jobs."""
    from app.services.job_manager import job_manager

    async with async_session() as session:
        job = DiscJob(
            volume_label="STRANGENEWWORLDS",
            drive_id="E:",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Star Trek: Strange New Worlds",
            detected_season=3,
            subtitle_status="failed",
            error_message="Subtitle download failed: No subtitles found",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with (
        patch.object(
            job_manager._matching, "restart_subtitle_download", new_callable=AsyncMock
        ) as mock_restart,
        patch.object(job_manager, "_rerun_matching", new_callable=AsyncMock),
    ):
        await job_manager.rerun_matching(job_id)

    mock_restart.assert_awaited_once_with(job_id, "Star Trek: Strange New Worlds", 3)


@pytest.mark.asyncio
async def test_rerun_matching_skips_subtitle_retry_when_completed():
    """rerun_matching should NOT trigger subtitle retry if subtitles already succeeded."""
    from app.services.job_manager import job_manager

    async with async_session() as session:
        job = DiscJob(
            volume_label="GOODSHOW",
            drive_id="E:",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Good Show",
            detected_season=1,
            subtitle_status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with (
        patch.object(
            job_manager._matching, "restart_subtitle_download", new_callable=AsyncMock
        ) as mock_restart,
        patch.object(job_manager, "_rerun_matching", new_callable=AsyncMock),
    ):
        await job_manager.rerun_matching(job_id)

    mock_restart.assert_not_awaited()
