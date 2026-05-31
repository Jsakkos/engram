"""Endpoint test: fetch_cover rejects SSRF-unsafe image URLs.

The SSRF guard runs before the job lookup, so a disallowed URL must yield
HTTP 400 even for a non-existent job_id — proving the guard fires first.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models.disc_job import ContentType, DiscJob, JobState
from tests.unit.conftest import _unit_session_factory


@pytest.fixture
async def client():
    """Provide an async HTTP client with the patched DB session."""

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


class TestFetchCoverSsrfGuard:
    """POST /api/contributions/{job_id}/fetch-cover SSRF rejection."""

    async def test_rejects_cloud_metadata_endpoint(self, client: AsyncClient):
        """A link-local metadata IP must be refused with 400."""
        resp = await client.post(
            "/api/contributions/999/fetch-cover",
            json={"image_url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert resp.status_code == 400
        assert "allowlist" in resp.json()["detail"].lower()

    async def test_rejects_unlisted_external_host(self, client: AsyncClient):
        """An arbitrary external host (not on the allowlist) must be refused."""
        resp = await client.post(
            "/api/contributions/999/fetch-cover",
            json={"image_url": "https://evil.example.com/cover.jpg"},
        )
        assert resp.status_code == 400

    async def test_rejects_non_http_scheme(self, client: AsyncClient):
        """A file:// URL must be refused before any fetch is attempted."""
        resp = await client.post(
            "/api/contributions/999/fetch-cover",
            json={"image_url": "file:///etc/passwd"},
        )
        assert resp.status_code == 400

    async def test_allows_allowlisted_host(self, client: AsyncClient):
        """An allowlisted host must pass the SSRF guard.

        With a valid host the guard does not reject, so the request reaches
        the job lookup — and a bogus job_id yields 404, not the 400 the
        guard would raise. This catches an accidentally inverted guard.
        """
        resp = await client.post(
            "/api/contributions/999/fetch-cover",
            json={"image_url": "https://m.media-amazon.com/images/I/test.jpg"},
        )
        assert resp.status_code == 404


class TestFetchCoverExportPath:
    """fetch_cover must use the export-dir fallback, not 400, when the path is unset."""

    async def test_empty_export_path_falls_back_and_saves(
        self, client: AsyncClient, monkeypatch, tmp_path
    ):
        """Default config has discdb_export_path="" — the cover must still save.

        Regression for the silent fetch-cover failure: the route used to 400
        with "No export path configured" instead of falling back to
        ~/.engram/discdb-exports/ like every other call site.
        """
        async with _unit_session_factory() as s:
            job = DiscJob(
                drive_id="E:",
                volume_label="X",
                content_hash="HASH123",
                content_type=ContentType.MOVIE,
                state=JobState.COMPLETED,
            )
            s.add(job)
            await s.commit()
            await s.refresh(job)
            job_id = job.id

        # Redirect the fallback dir to tmp so the test never writes to ~/.engram.
        import app.core.discdb_exporter as exp

        monkeypatch.setattr(exp, "get_export_directory", lambda config: tmp_path)

        download = httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"\xff\xd8\xff\xe0fake-jpeg",
            request=httpx.Request("GET", "https://m.media-amazon.com/x.jpg"),
        )
        with patch("app.api.routes.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get.return_value = download
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            cls.return_value = mc

            resp = await client.post(
                f"/api/contributions/{job_id}/fetch-cover",
                json={"image_url": "https://m.media-amazon.com/images/I/cover.jpg"},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"] == "cover.jpg"
        assert (tmp_path / "HASH123" / "cover.jpg").read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg"
