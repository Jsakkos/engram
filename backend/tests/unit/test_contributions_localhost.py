"""Endpoint tests: DiscDB ``/contributions/*`` routes are localhost-only.

These routes drive TheDiscDB contribution workflow (list, export, submit,
cover fetch, enhance, release groups). They surface and act on local library
data, so — like the fingerprint-contribution routes — they must only be
reachable from the host machine. ``require_localhost`` rejects LAN peers even
when ``allow_lan_access`` binds the server to ``0.0.0.0``.

httpx's ``ASGITransport`` defaults ``request.client.host`` to ``127.0.0.1``
(an allowed loopback), so we set an explicit LAN client to exercise the guard.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from tests.unit.conftest import _unit_session_factory


def _client(host: str) -> AsyncClient:
    """Async client whose requests originate from ``host``."""
    transport = ASGITransport(app=app, client=(host, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def db_override():
    """Point get_session at the in-memory test DB for the duration of a test."""

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = override_get_session
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


class TestContributionsLocalhostGuard:
    """``/api/contributions/*`` must reject off-box callers with 403."""

    async def test_list_rejects_non_localhost(self, db_override):
        """A LAN peer cannot enumerate contributions."""
        async with _client("10.0.0.5") as client:
            resp = await client.get("/api/contributions")
        assert resp.status_code == 403
        assert "host machine" in resp.json()["detail"].lower()

    async def test_fetch_cover_rejects_non_localhost(self, db_override):
        """The localhost guard fires before the SSRF guard.

        Off-box, even a disallowed URL yields 403 — not the 400 the SSRF guard
        would raise — proving the localhost check runs first.
        """
        async with _client("10.0.0.5") as client:
            resp = await client.post(
                "/api/contributions/999/fetch-cover",
                json={"image_url": "http://169.254.169.254/latest/meta-data/"},
            )
        assert resp.status_code == 403

    async def test_submit_rejects_non_localhost(self, db_override):
        """A LAN peer cannot trigger a TheDiscDB submission."""
        async with _client("10.0.0.5") as client:
            resp = await client.post("/api/contributions/999/submit")
        assert resp.status_code == 403

    async def test_localhost_still_allowed(self, db_override):
        """Positive control: a loopback client reaches the handler.

        An empty DB yields an empty list with 200, proving the guard does not
        break legitimate dashboard access.
        """
        async with _client("127.0.0.1") as client:
            resp = await client.get("/api/contributions")
        assert resp.status_code == 200
