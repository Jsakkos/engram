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


@pytest.fixture
async def async_session_ctx():
    """Yield an open async database session."""
    async with async_session() as session:
        yield session


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


@pytest.mark.asyncio
async def test_pseudonym_generated_on_first_startup(async_session_ctx):
    """The pseudonym bootstrap ensures app_config always has a valid UUIDv4 pseudonym.

    Simulates the startup flow: get_config() guarantees the row exists, then the
    bootstrap generates a pseudonym when the field is empty.
    """
    from sqlmodel import select

    from app.models.app_config import AppConfig
    from app.services.config_service import get_config
    from app.services.contribution_pseudonym import generate_pseudonym, validate_pseudonym

    # Ensure the app_config row exists (mirrors get_config() call in lifespan).
    await get_config()

    # Run the bootstrap — same logic as the lifespan block.
    result = await async_session_ctx.execute(select(AppConfig))
    cfg = result.scalar_one_or_none()
    assert cfg is not None, "app_config row should exist after get_config()"

    if not validate_pseudonym(cfg.contribution_pseudonym):
        cfg.contribution_pseudonym = generate_pseudonym()
        async_session_ctx.add(cfg)
        await async_session_ctx.commit()

    # Verify the pseudonym is now valid.
    await async_session_ctx.refresh(cfg)
    assert validate_pseudonym(cfg.contribution_pseudonym), (
        f"contribution_pseudonym should be a UUIDv4, got {cfg.contribution_pseudonym!r}"
    )
