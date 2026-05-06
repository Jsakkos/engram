"""Unit tests for TheDiscDB submission client."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.discdb_submitter import (
    _find_release_image_files,
    _upload_release_images,
    ensure_release_group_id,
    submit_disc,
    submit_job,
    submit_release_image,
    submit_scan_log,
)
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState


@pytest.fixture
def api_key():
    return "test-api-key-12345"


@pytest.fixture
def base_url():
    return "https://thediscdb.com"


@pytest.fixture
def sample_payload():
    return {
        "export_version": "1.1",
        "disc": {"content_hash": "ABC123", "volume_label": "TEST_DISC"},
        "titles": [],
    }


@pytest.fixture
def config(api_key, base_url):
    return AppConfig(
        discdb_contributions_enabled=True,
        discdb_contribution_tier=2,
        discdb_export_path="",
        discdb_api_key=api_key,
        discdb_api_url=base_url,
    )


@pytest.fixture
def completed_job():
    return DiscJob(
        id=1,
        drive_id="E:",
        volume_label="TEST",
        content_type=ContentType.TV,
        state=JobState.COMPLETED,
        content_hash="D7CAB58DAC87C58C46FDA35A33759839",
        detected_title="Test Show",
        detected_season=1,
        tmdb_id=1234,
    )


@pytest.fixture
def titles():
    return [
        DiscTitle(
            id=1,
            job_id=1,
            title_index=0,
            duration_seconds=3600,
            file_size_bytes=10000000000,
            chapter_count=10,
            matched_episode="S01E01",
            match_details=json.dumps({"source": "subtitle"}),
        ),
    ]


def _mock_response(status_code, json_data=None):
    """Create a properly formed httpx.Response with request set."""
    request = httpx.Request("POST", "https://thediscdb.com/api/engram/disc")
    return httpx.Response(status_code, json=json_data, request=request)


class TestSubmitDisc:
    @pytest.mark.anyio
    async def test_successful_submission(self, sample_payload, api_key, base_url):
        mock_response = _mock_response(
            200,
            {"id": 5, "contentHash": "ABC123", "updated": False},
        )

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_disc(sample_payload, api_key, base_url)

        assert result.success is True
        assert result.submission_id == "5"
        assert result.contribute_url is None  # No release_id in sample payload
        assert result.error is None

        # Verify auth header
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == f"ApiKey {api_key}"

    @pytest.mark.anyio
    async def test_contribute_url_from_release_id(self, api_key, base_url):
        """When payload has release_id, contribute_url is constructed."""
        payload = {
            "disc": {"content_hash": "ABC123", "release_id": "uuid-123"},
            "titles": [],
        }
        mock_response = _mock_response(200, {"id": 6, "contentHash": "ABC123"})

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_disc(payload, api_key, base_url)

        assert result.success is True
        assert result.contribute_url == "https://thediscdb.com/contribute/engram/uuid-123"

    @pytest.mark.anyio
    async def test_401_unauthorized(self, sample_payload, api_key, base_url):
        mock_response = _mock_response(401, {"error": "invalid key"})

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_disc(sample_payload, api_key, base_url)

        assert result.success is False
        assert "invalid or expired" in result.error

    @pytest.mark.anyio
    async def test_network_error(self, sample_payload, api_key, base_url):
        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_disc(sample_payload, api_key, base_url)

        assert result.success is False
        assert "Network error" in result.error

    @pytest.mark.anyio
    async def test_no_auth_header_without_key(self, sample_payload, base_url):
        """Submission proceeds without API key; no Authorization header sent."""
        mock_response = _mock_response(200, {"id": 7, "contentHash": "ABC123"})

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_disc(sample_payload, "", base_url)

        assert result.success is True
        call_kwargs = mock_client.post.call_args
        assert "Authorization" not in call_kwargs.kwargs["headers"]


class TestSubmitScanLog:
    @pytest.mark.anyio
    async def test_successful_log_submission(self, api_key, base_url, tmp_path):
        log_file = tmp_path / "scan.log"
        log_file.write_text('MSG:1,0,0,"MakeMKV scan output"', encoding="utf-8")

        mock_response = _mock_response(200)

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await submit_scan_log("ABC123", log_file, api_key, base_url)

        assert result is True

        call_kwargs = mock_client.post.call_args
        assert call_kwargs.args[0] == "https://thediscdb.com/api/engram/disc/ABC123/logs/scan"
        assert call_kwargs.kwargs["headers"]["Content-Type"] == "text/plain"

    @pytest.mark.anyio
    async def test_missing_log_file(self, api_key, base_url, tmp_path):
        missing = tmp_path / "nonexistent.log"
        result = await submit_scan_log("ABC123", missing, api_key, base_url)
        assert result is False


class TestAuthHeaders:
    def test_auth_headers_with_key(self):
        from app.core.discdb_submitter import _auth_headers

        headers = _auth_headers("my-secret-key")
        assert headers == {"Authorization": "ApiKey my-secret-key"}

    def test_auth_headers_without_key(self):
        from app.core.discdb_submitter import _auth_headers

        assert _auth_headers("") == {}
        assert _auth_headers(None) == {}


class TestSubmitJob:
    @pytest.mark.anyio
    async def test_skip_without_content_hash(self, titles, config):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="TEST",
            state=JobState.COMPLETED,
            content_hash=None,
        )
        result = await submit_job(job, titles, config)
        assert result.success is False
        assert "No content hash" in result.error


class TestSubmitReleaseImage:
    @pytest.mark.anyio
    async def test_uploads_front_image(self, api_key, base_url, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            ok = await submit_release_image("rel-uuid-1", "front", cover, api_key, base_url)

        assert ok is True
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.args[0] == "https://thediscdb.com/api/engram/rel-uuid-1/images/front"
        assert call_kwargs.kwargs["headers"]["Content-Type"] == "image/jpeg"
        assert call_kwargs.kwargs["headers"]["Authorization"] == f"ApiKey {api_key}"
        assert call_kwargs.kwargs["content"] == cover.read_bytes()

    @pytest.mark.anyio
    async def test_uploads_back_image(self, api_key, base_url, tmp_path):
        cover = tmp_path / "cover_back.jpg"
        cover.write_bytes(b"back-bytes")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            ok = await submit_release_image("rel-2", "back", cover, api_key, base_url)

        assert ok is True
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.args[0] == "https://thediscdb.com/api/engram/rel-2/images/back"

    @pytest.mark.anyio
    async def test_png_content_type(self, api_key, base_url, tmp_path):
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNGfake")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            ok = await submit_release_image("rel-3", "front", cover, api_key, base_url)

        assert ok is True
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["headers"]["Content-Type"] == "image/png"

    @pytest.mark.anyio
    async def test_missing_file_returns_false(self, api_key, base_url, tmp_path):
        missing = tmp_path / "nope.jpg"
        ok = await submit_release_image("rel-4", "front", missing, api_key, base_url)
        assert ok is False

    @pytest.mark.anyio
    async def test_invalid_kind_raises(self, api_key, base_url, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"x")
        with pytest.raises(ValueError):
            await submit_release_image("rel-5", "side", cover, api_key, base_url)

    @pytest.mark.anyio
    async def test_http_error_returns_false(self, api_key, base_url, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"x")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(500)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            ok = await submit_release_image("rel-6", "front", cover, api_key, base_url)

        assert ok is False


class TestFindReleaseImageFiles:
    def test_finds_front_cover(self, tmp_path):
        disc_a = tmp_path / "HASH_A"
        disc_a.mkdir()
        (disc_a / "cover.jpg").write_bytes(b"front")

        result = _find_release_image_files([disc_a])
        assert result["front"] == disc_a / "cover.jpg"
        assert result["back"] is None

    def test_finds_back_cover(self, tmp_path):
        disc_a = tmp_path / "HASH_A"
        disc_a.mkdir()
        (disc_a / "cover_back.png").write_bytes(b"back")

        result = _find_release_image_files([disc_a])
        assert result["back"] == disc_a / "cover_back.png"
        assert result["front"] is None

    def test_first_disc_with_image_wins(self, tmp_path):
        disc_a = tmp_path / "A"
        disc_b = tmp_path / "B"
        disc_a.mkdir()
        disc_b.mkdir()
        (disc_b / "cover.jpg").write_bytes(b"front-b")

        result = _find_release_image_files([disc_a, disc_b])
        assert result["front"] == disc_b / "cover.jpg"

    def test_no_images_returns_none(self, tmp_path):
        disc_a = tmp_path / "A"
        disc_a.mkdir()
        result = _find_release_image_files([disc_a])
        assert result == {"front": None, "back": None}

    def test_skips_missing_dirs(self, tmp_path):
        result = _find_release_image_files([tmp_path / "does-not-exist"])
        assert result == {"front": None, "back": None}


class TestEnsureReleaseGroupId:
    def test_returns_existing_id(self):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="X",
            state=JobState.COMPLETED,
            release_group_id="existing-uuid",
        )
        assert ensure_release_group_id(job) == "existing-uuid"
        assert job.release_group_id == "existing-uuid"

    def test_mints_new_uuid_when_missing(self):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="X",
            state=JobState.COMPLETED,
            release_group_id=None,
        )
        assigned = ensure_release_group_id(job)
        assert assigned
        assert job.release_group_id == assigned
        # Standard UUID4 string format: 8-4-4-4-12
        parts = assigned.split("-")
        assert len(parts) == 5
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]

    def test_treats_empty_string_as_missing(self):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="X",
            state=JobState.COMPLETED,
            release_group_id="",
        )
        assigned = ensure_release_group_id(job)
        assert assigned != ""
        assert job.release_group_id == assigned


class TestUploadReleaseImages:
    @pytest.mark.anyio
    async def test_uploads_when_image_present(self, api_key, base_url, tmp_path):
        export_dir = tmp_path / "HASH"
        export_dir.mkdir()
        (export_dir / "cover.jpg").write_bytes(b"front-bytes")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await _upload_release_images("rel-x", [export_dir], api_key, base_url)

        assert results == {"front": True}
        assert mock_client.post.call_args.args[0] == (
            "https://thediscdb.com/api/engram/rel-x/images/front"
        )

    @pytest.mark.anyio
    async def test_skip_when_no_images(self, api_key, base_url, tmp_path):
        export_dir = tmp_path / "HASH"
        export_dir.mkdir()
        results = await _upload_release_images("rel-x", [export_dir], api_key, base_url)
        assert results == {}

    @pytest.mark.anyio
    async def test_uploads_front_and_back(self, api_key, base_url, tmp_path):
        export_dir = tmp_path / "HASH"
        export_dir.mkdir()
        (export_dir / "cover.jpg").write_bytes(b"f")
        (export_dir / "cover_back.png").write_bytes(b"b")

        with patch("app.core.discdb_submitter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await _upload_release_images("rel-y", [export_dir], api_key, base_url)

        assert results == {"front": True, "back": True}
        assert mock_client.post.call_count == 2
