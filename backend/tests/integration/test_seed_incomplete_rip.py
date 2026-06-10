"""Integration tests for the seed-incomplete-rip simulation endpoint."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscTitle, TitleState


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_seed_incomplete_rip_returns_job_and_title_ids(client):
    """Seed endpoint returns job_id and title_id."""
    response = await client.post("/api/simulate/seed-incomplete-rip")
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert "title_id" in data
    assert isinstance(data["job_id"], int)
    assert isinstance(data["title_id"], int)


@pytest.mark.asyncio
async def test_seed_incomplete_rip_job_is_review_needed(client):
    """Seeded job is in REVIEW_NEEDED state."""
    response = await client.post("/api/simulate/seed-incomplete-rip")
    data = response.json()
    job_id = data["job_id"]

    job_response = await client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["state"] == "review_needed"


@pytest.mark.asyncio
async def test_seed_incomplete_rip_title_has_correct_state_and_details(client):
    """Seeded title is REVIEW state with incomplete_rip error and rerip_eligible=True."""
    response = await client.post("/api/simulate/seed-incomplete-rip")
    data = response.json()
    title_id = data["title_id"]

    async with async_session() as session:
        title = await session.get(DiscTitle, title_id)
        assert title is not None
        assert title.state == TitleState.REVIEW

        details = json.loads(title.match_details)
        assert details["error"] == "incomplete_rip"
        assert details["rerip_eligible"] is True


@pytest.mark.asyncio
async def test_seed_incomplete_rip_custom_volume_label(client):
    """Seed endpoint accepts a custom volume_label query param."""
    response = await client.post("/api/simulate/seed-incomplete-rip?volume_label=CUSTOM_DISC_S2D3")
    assert response.status_code == 200
    data = response.json()
    job_id = data["job_id"]

    job_response = await client.get(f"/api/jobs/{job_id}")
    job = job_response.json()
    assert job["volume_label"] == "CUSTOM_DISC_S2D3"


@pytest.mark.asyncio
async def test_seed_incomplete_rip_blocked_in_production(client):
    """Seed endpoint returns 403 when debug mode is off."""
    from unittest.mock import patch

    with patch("app.api.routes.settings") as mock_settings:
        mock_settings.debug = False
        response = await client.post("/api/simulate/seed-incomplete-rip")
        assert response.status_code == 403
