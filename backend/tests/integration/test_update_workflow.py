"""Integration tests for /api/updates/* endpoints.

Uses the same pattern as other integration tests: ASGITransport with the real
app, real async_session from app.database. The update_checker singleton's state
is reset between tests.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.updater import UpdateStatus, update_checker
from app.database import async_session, init_db
from app.main import app
from app.models import AppConfig


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


@pytest.fixture
async def app_config():
    """Create a minimal AppConfig row so skip_version() can find and update it."""
    async with async_session() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key",
            staging_path="/tmp/staging",
            library_movies_path="/tmp/movies",
            library_tv_path="/tmp/tv",
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_token",
            max_concurrent_matches=2,
            ffmpeg_path="/usr/bin/ffmpeg",
            conflict_resolution_default="rename",
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


@pytest.fixture(autouse=True)
def reset_update_checker():
    """Reset singleton state between tests."""
    original_state = update_checker.state
    original_latest = update_checker.latest_version
    original_frozen = update_checker._is_frozen
    yield
    update_checker.state = original_state
    update_checker.latest_version = original_latest
    update_checker._is_frozen = original_frozen


class TestGetUpdateStatus:
    async def test_returns_expected_shape(self, client: AsyncClient):
        """GET /api/updates/status returns all required fields."""
        response = await client.get("/api/updates/status")
        assert response.status_code == 200
        data = response.json()

        required_fields = {
            "state",
            "current_version",
            "latest_version",
            "release_notes",
            "release_url",
            "download_progress",
            "error",
            "is_frozen",
        }
        assert required_fields.issubset(data.keys()), (
            f"Missing fields: {required_fields - data.keys()}"
        )
        # current_version should be a non-empty string
        assert isinstance(data["current_version"], str)
        assert len(data["current_version"]) > 0

    async def test_state_is_valid_value(self, client: AsyncClient):
        """State field should be one of the known UpdateStatus values."""
        response = await client.get("/api/updates/status")
        data = response.json()
        valid_states = {
            "idle",
            "checking",
            "up_to_date",
            "downloading",
            "ready",
            "skipped",
            "error",
        }
        assert data["state"] in valid_states


class TestSkipVersion:
    async def test_skip_version_returns_200(self, client: AsyncClient, app_config):
        """POST /api/updates/skip persists and returns ok."""
        response = await client.post(
            "/api/updates/skip",
            json={"version": "99.9.9"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    async def test_skip_version_persists_to_db(self, client: AsyncClient, app_config):
        """Skipped version should be persisted in AppConfig."""
        await client.post(
            "/api/updates/skip",
            json={"version": "0.0.0"},
        )

        # skip_version() uses app.database.async_session directly — same session
        # factory as the one we read from here, so the write is visible.
        from sqlmodel import select

        from app.models.app_config import AppConfig as _AppConfig

        async with async_session() as session:
            result = await session.execute(select(_AppConfig).limit(1))
            config = result.scalar_one_or_none()
            assert config is not None
            assert config.skipped_update_version == "0.0.0"


class TestRestartForUpdate:
    async def test_restart_returns_400_in_non_frozen(self, client: AsyncClient):
        """POST /api/updates/restart returns 400 in non-frozen (test) environment."""
        # update_checker._is_frozen is False in test (no PyInstaller)
        update_checker._is_frozen = False
        update_checker.state = UpdateStatus.READY

        response = await client.post("/api/updates/restart")
        assert response.status_code == 400
        assert "frozen" in response.json()["detail"].lower()

    async def test_restart_returns_400_when_not_ready(self, client: AsyncClient):
        """POST /api/updates/restart returns 400 when state is not READY."""
        update_checker._is_frozen = True
        update_checker.state = UpdateStatus.IDLE

        response = await client.post("/api/updates/restart")
        assert response.status_code == 400
