"""Integration tests for Phase 2 C1.1b: fingerprint disclosure consent fields."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean app_config between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM app_config"))
        await session.commit()


@pytest.fixture
async def client():
    """Async HTTP client backed by the FastAPI app under test."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_disclosure_fields_default_false(client: AsyncClient):
    """GET /api/config returns disclosure fields with correct defaults."""
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fingerprint_disclosure_accepted"] is False
    assert data["fingerprint_disclosure_accepted_at"] is None


@pytest.mark.asyncio
async def test_accepting_disclosure_stamps_timestamp(client: AsyncClient):
    """PUT /api/config with fingerprint_disclosure_accepted=true stamps the timestamp."""
    put_resp = await client.put(
        "/api/config",
        json={"fingerprint_disclosure_accepted": True},
    )
    assert put_resp.status_code == 200

    get_resp = await client.get("/api/config")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["fingerprint_disclosure_accepted"] is True
    assert data["fingerprint_disclosure_accepted_at"] is not None
    # Should be a parseable ISO 8601 datetime string
    from datetime import datetime

    dt = datetime.fromisoformat(data["fingerprint_disclosure_accepted_at"].replace("Z", "+00:00"))
    assert dt.year >= 2026
