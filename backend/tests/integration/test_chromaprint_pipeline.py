"""Integration tests for chromaprint Phase 1: fpcalc detection + extraction pipeline."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


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
    """Async HTTP client backed by the FastAPI app under test."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_detect_tools_includes_fpcalc(client):
    """GET /api/detect-tools surfaces fpcalc alongside makemkv and ffmpeg."""
    response = await client.get("/api/detect-tools")
    assert response.status_code == 200
    data = response.json()
    assert "fpcalc" in data, f"detect-tools should include fpcalc, got keys: {list(data.keys())}"


@pytest.mark.asyncio
async def test_validate_fpcalc_endpoint_rejects_missing(client):
    """POST /api/validate/fpcalc with a bogus path reports found=False."""
    response = await client.post(
        "/api/validate/fpcalc",
        json={"path": "/definitely/not/a/binary"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False


@pytest.mark.asyncio
async def test_validate_fpcalc_rejects_disallowed_basename(client):
    """The validate endpoint refuses paths whose basename is not in the fpcalc whitelist."""
    response = await client.post(
        "/api/validate/fpcalc",
        json={"path": "/usr/bin/whoami"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
