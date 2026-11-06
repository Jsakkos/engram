"""#718: the job payload says when a job has stopped making progress.

The dashboard shows "Force advance" as a primary control only for a job that looks
stuck. The signal is the watchdog's own activity clock (``_last_activity``), exposed
as ``stalled_for_seconds`` / ``looks_stuck`` on the REST payload and pushed over the
WebSocket by a small tick (a stall is the *absence* of events, so nothing would
otherwise tell the client to start showing the button).
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.database as db
from app.api.websocket import manager as ws_manager
from app.main import app
from app.models import DiscJob, JobState
from app.services.job_manager import STUCK_AFTER_SECONDS, JobManager


@pytest.fixture
def manager():
    return JobManager()


def test_healthy_job_is_not_stuck(manager):
    manager._last_activity[1] = time.monotonic() - 5
    stalled, stuck = manager.stall_state(1, JobState.RIPPING)
    assert stuck is False
    assert 4 <= stalled <= 10


def test_job_idle_past_threshold_is_stuck(manager):
    manager._last_activity[1] = time.monotonic() - (STUCK_AFTER_SECONDS + 30)
    stalled, stuck = manager.stall_state(1, JobState.MATCHING)
    assert stuck is True
    assert stalled >= STUCK_AFTER_SECONDS + 30


@pytest.mark.parametrize(
    "state",
    [JobState.IDLE, JobState.REVIEW_NEEDED, JobState.COMPLETED, JobState.FAILED],
)
def test_resting_and_terminal_states_are_never_stuck(manager, state):
    manager._last_activity[1] = time.monotonic() - 99999
    assert manager.stall_state(1, state) == (None, False)


def test_job_with_no_clock_yet_is_not_stuck(manager):
    # First sighting by the watchdog seeds the clock; until then there is no signal.
    assert manager.stall_state(7, JobState.RIPPING) == (None, False)


@pytest.mark.asyncio
async def test_tick_broadcasts_stall_then_clears_it(manager):
    async with db.async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="STALL_TEST", state=JobState.RIPPING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    manager._last_activity[job_id] = time.monotonic() - (STUCK_AFTER_SECONDS + 10)
    with patch.object(ws_manager, "broadcast", new=AsyncMock()) as bc:
        await manager._broadcast_stall_changes()
        sent = [c.args[0] for c in bc.await_args_list]
        assert any(
            m["type"] == "job_update" and m["job_id"] == job_id and m["looks_stuck"] is True
            for m in sent
        )

        # Activity resumes: the next tick clears the flag exactly once.
        manager._note_activity(job_id)
        bc.reset_mock()
        await manager._broadcast_stall_changes()
        cleared = [c.args[0] for c in bc.await_args_list if c.args[0]["job_id"] == job_id]
        assert cleared and cleared[0]["looks_stuck"] is False
        bc.reset_mock()
        await manager._broadcast_stall_changes()
        assert not [c for c in bc.await_args_list if c.args[0]["job_id"] == job_id]


@pytest.mark.asyncio
async def test_rest_job_payload_carries_stall_fields():
    from app.services.job_manager import job_manager

    async with db.async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="STALL_REST", state=JobState.RIPPING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    try:
        job_manager._last_activity[job_id] = time.monotonic() - (STUCK_AFTER_SECONDS + 60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            listed = next(j for j in (await ac.get("/api/jobs")).json() if j["id"] == job_id)
            single = (await ac.get(f"/api/jobs/{job_id}")).json()
        for payload in (listed, single):
            assert payload["looks_stuck"] is True
            assert payload["stalled_for_seconds"] >= STUCK_AFTER_SECONDS + 60

        job_manager._note_activity(job_id)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            listed = next(j for j in (await ac.get("/api/jobs")).json() if j["id"] == job_id)
        assert listed["looks_stuck"] is False
    finally:
        job_manager._last_activity.pop(job_id, None)
