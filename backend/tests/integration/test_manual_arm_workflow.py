"""Arm/disarm API: validation, drive-occupied rejection, one-shot semantics."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscJob, JobState
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
    yield
    arm_store.disarm("E:")


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
