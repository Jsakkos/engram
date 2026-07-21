"""Arm/disarm API: validation, drive-occupied rejection, one-shot semantics."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import app.api.websocket as websocket_module
from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscJob, JobState
from app.services.job_manager import job_manager
from app.services.manual_identity import arm_store


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()
    arm_store.disarm("E:")
    arm_store.disarm("F:")
    yield
    arm_store.disarm("E:")
    arm_store.disarm("F:")


async def test_arm_stores_payload(client):
    resp = await client.post(
        "/api/manual/arm",
        json={
            "drive_id": "E:",
            "title": "Arrested Development",
            "content_type": "tv",
            "season": 1,
            "tmdb_id": 4589,
        },
    )

    assert resp.status_code == 200
    armed = arm_store.peek("E:")
    assert armed is not None
    assert armed.title == "Arrested Development"
    assert armed.tmdb_id == 4589


async def test_arm_rejects_blank_title(client):
    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "   ", "content_type": "tv"},
    )

    assert resp.status_code == 422
    assert arm_store.peek("E:") is None


async def test_arm_rejects_bad_content_type(client):
    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "X", "content_type": "audiobook"},
    )

    assert resp.status_code == 422


async def test_arm_conflicts_when_drive_has_active_job(client):
    async with async_session() as session:
        session.add(DiscJob(drive_id="E:", volume_label="BUSY", state=JobState.RIPPING))
        await session.commit()

    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "Arrested Development", "content_type": "tv"},
    )

    assert resp.status_code == 409
    assert arm_store.peek("E:") is None


async def test_arm_conflict_check_is_scoped_to_drive(client):
    """A non-terminal job on a DIFFERENT drive must not block arming E:.

    The endpoint's 409 query filters on ``DiscJob.drive_id == req.drive_id``.
    Without a job on another drive in the fixture, dropping that filter
    entirely would still pass every other test in this file.
    """
    async with async_session() as session:
        session.add(DiscJob(drive_id="F:", volume_label="BUSY_ELSEWHERE", state=JobState.RIPPING))
        await session.commit()

    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "Arrested Development", "content_type": "tv"},
    )

    assert resp.status_code == 200
    assert arm_store.peek("E:") is not None


async def test_arm_allowed_when_drive_job_is_terminal(client):
    async with async_session() as session:
        session.add(DiscJob(drive_id="E:", volume_label="OLD", state=JobState.COMPLETED))
        await session.commit()

    resp = await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "The Office", "content_type": "tv"},
    )

    assert resp.status_code == 200


async def test_disarm_clears(client):
    await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "The Office", "content_type": "tv"},
    )

    resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "disarmed"
    assert arm_store.peek("E:") is None


async def test_disarm_when_not_armed_is_not_an_error(client):
    resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_armed"


async def test_arm_broadcasts_drive_armed(client):
    """Arming must call broadcast_drive_armed with the drive id and full identity.

    The route imports ``manager`` locally inside the function body (deferred
    import), so patching the module attribute is required for the mock to be
    the object the handler actually calls.
    """
    with patch.object(
        websocket_module.manager, "broadcast_drive_armed", new_callable=AsyncMock
    ) as mock_broadcast:
        resp = await client.post(
            "/api/manual/arm",
            json={
                "drive_id": "E:",
                "title": "Arrested Development",
                "content_type": "tv",
                "season": 1,
                "tmdb_id": 4589,
            },
        )

    assert resp.status_code == 200
    mock_broadcast.assert_called_once_with(
        "E:",
        {
            "title": "Arrested Development",
            "content_type": "tv",
            "season": 1,
            "tmdb_id": 4589,
            "disc_number": None,
        },
    )


async def test_disarm_broadcasts_none_when_something_was_armed(client):
    await client.post(
        "/api/manual/arm",
        json={"drive_id": "E:", "title": "The Office", "content_type": "tv"},
    )

    with patch.object(
        websocket_module.manager, "broadcast_drive_armed", new_callable=AsyncMock
    ) as mock_broadcast:
        resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    mock_broadcast.assert_called_once_with("E:", None)


async def test_disarm_does_not_broadcast_when_nothing_was_armed(client):
    with patch.object(
        websocket_module.manager, "broadcast_drive_armed", new_callable=AsyncMock
    ) as mock_broadcast:
        resp = await client.post("/api/manual/disarm", json={"drive_id": "E:"})

    assert resp.status_code == 200
    mock_broadcast.assert_not_called()


async def test_re_identify_accepted_while_identifying(client):
    """The always-on card control offers re-identify for the whole scanning
    window (#520), which maps to the backend's IDENTIFYING state. The 400
    guard must accept it, not just REVIEW_NEEDED/RIPPING."""
    async with async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="X", state=JobState.IDENTIFYING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch.object(job_manager, "re_identify_job", new=AsyncMock()):
        resp = await client.post(
            f"/api/jobs/{job_id}/re-identify",
            json={"title": "The Office", "content_type": "tv", "season": 2},
        )

    assert resp.status_code == 200


async def test_re_identify_still_rejected_when_completed(client):
    """COMPLETED must still be rejected — the History page's AmendTitleModal
    owns post-hoc edits, not this endpoint."""
    async with async_session() as session:
        job = DiscJob(drive_id="E:", volume_label="X", state=JobState.COMPLETED)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    resp = await client.post(
        f"/api/jobs/{job_id}/re-identify",
        json={"title": "The Office", "content_type": "tv", "season": 2},
    )

    assert resp.status_code == 400
