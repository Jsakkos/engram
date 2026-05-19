"""Endpoint test: fetch_cover rejects SSRF-unsafe image URLs.

The SSRF guard runs before the job lookup, so a disallowed URL must yield
HTTP 400 even for a non-existent job_id — proving the guard fires first.
"""

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
