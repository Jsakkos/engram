"""Unit tests for the staging import endpoint (POST /api/staging/import).

Tests that the endpoint is accessible without DEBUG mode, validates paths,
and creates jobs from directories containing MKV files.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from tests.unit.conftest import _unit_session_factory


@pytest.fixture
async def client():
    """Provide an async HTTP client with the patched DB session."""

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestStagingImportEndpoint:
    """Tests for POST /api/staging/import."""

    async def test_missing_staging_path_returns_404(self, client: AsyncClient):
        """A nonexistent staging path should return 404."""
        resp = await client.post(
            "/api/staging/import",
            json={"staging_path": "/nonexistent/path/to/nowhere"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_empty_directory_returns_404(self, client: AsyncClient, tmp_path):
        """A directory with no MKV files should return 404."""
        empty_dir = tmp_path / "empty_staging"
        empty_dir.mkdir()

        resp = await client.post(
            "/api/staging/import",
            json={"staging_path": str(empty_dir)},
        )
        assert resp.status_code == 404
        assert "no mkv files" in resp.json()["detail"].lower()

    async def test_directory_with_non_mkv_files_returns_404(self, client: AsyncClient, tmp_path):
        """A directory with only non-MKV files should return 404."""
        staging_dir = tmp_path / "staging_txt"
        staging_dir.mkdir()
        (staging_dir / "readme.txt").write_text("not a video")
        (staging_dir / "image.jpg").write_bytes(b"\xff\xd8\xff")

        resp = await client.post(
            "/api/staging/import",
            json={"staging_path": str(staging_dir)},
        )
        assert resp.status_code == 404

    async def test_valid_staging_creates_job(self, client: AsyncClient, tmp_path):
        """A valid directory with MKV files should create a job."""
        staging_dir = tmp_path / "MY_SHOW_S1D1"
        staging_dir.mkdir()
        (staging_dir / "title_t00.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)
        (staging_dir / "title_t01.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

        # Mock create_job_from_staging to avoid running the full pipeline
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=42,
        ):
            resp = await client.post(
                "/api/staging/import",
                json={
                    "staging_path": str(staging_dir),
                    "volume_label": "MY_SHOW_S1D1",
                    "content_type": "tv",
                    "detected_title": "My Show",
                    "detected_season": 1,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["job_id"] == 42
        assert data["titles_count"] == 2

    async def test_available_without_debug_mode(self, client: AsyncClient, tmp_path):
        """The endpoint must be accessible even when DEBUG=false."""
        staging_dir = tmp_path / "test_disc"
        staging_dir.mkdir()
        (staging_dir / "title_t00.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

        with (
            patch(
                "app.services.job_manager.job_manager.create_job_from_staging",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch("app.config.settings.debug", False),
        ):
            resp = await client.post(
                "/api/staging/import",
                json={"staging_path": str(staging_dir)},
            )

        # Should succeed (200), not 403
        assert resp.status_code == 200

    async def test_default_volume_label_from_directory_name(self, client: AsyncClient, tmp_path):
        """When no volume_label is provided, it should default to empty string."""
        staging_dir = tmp_path / "my_disc"
        staging_dir.mkdir()
        (staging_dir / "title_t00.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

        mock_create = AsyncMock(return_value=1)
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            mock_create,
        ):
            resp = await client.post(
                "/api/staging/import",
                json={"staging_path": str(staging_dir)},
            )

        assert resp.status_code == 200
        # Verify the method was called with default empty volume_label
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs["volume_label"] == ""
        assert call_kwargs.kwargs["content_type"] == "unknown"

    async def test_all_parameters_passed_through(self, client: AsyncClient, tmp_path):
        """All request parameters should be forwarded to create_job_from_staging."""
        staging_dir = tmp_path / "test"
        staging_dir.mkdir()
        (staging_dir / "movie.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

        mock_create = AsyncMock(return_value=7)
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            mock_create,
        ):
            resp = await client.post(
                "/api/staging/import",
                json={
                    "staging_path": str(staging_dir),
                    "volume_label": "INCEPTION_2010",
                    "content_type": "movie",
                    "detected_title": "Inception",
                    "detected_season": None,
                },
            )

        assert resp.status_code == 200
        mock_create.assert_called_once_with(
            staging_path=str(staging_dir),
            volume_label="INCEPTION_2010",
            content_type="movie",
            detected_title="Inception",
            detected_season=None,
        )
