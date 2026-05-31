"""Endpoint tests: DiscDB contribution routes are localhost-only.

The DiscDB contribution surface — the 11 ``/contributions/*`` routes plus
``POST /jobs/{job_id}/flag-discdb`` (which writes the ``discdb_flagged``
metadata the contribution pipeline reads) — surfaces and mutates local
library data. Like the fingerprint-contribution routes, it must only be
reachable from the host machine. ``require_localhost`` rejects LAN peers even
when ``allow_lan_access`` binds the server to ``0.0.0.0``.

httpx's ``ASGITransport`` defaults ``request.client.host`` to ``127.0.0.1``
(an allowed loopback), so we set an explicit client tuple to drive the guard.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from tests.unit.conftest import _unit_session_factory

# Every route the localhost guard must cover: (method, path). Bodies are
# omitted deliberately — the guard runs *before* body validation, so a LAN
# caller is rejected with 403 regardless, and a loopback caller falls through
# to validation/lookup (422/404/400) which is, crucially, never 403.
GUARDED_ROUTES = [
    ("GET", "/api/contributions"),
    ("GET", "/api/contributions/stats"),
    ("POST", "/api/contributions/999/export"),
    ("POST", "/api/contributions/999/skip"),
    ("POST", "/api/contributions/999/upc-lookup"),
    ("POST", "/api/contributions/999/fetch-cover"),
    ("POST", "/api/contributions/999/enhance"),
    ("POST", "/api/contributions/999/submit"),
    ("POST", "/api/contributions/release-group"),
    ("PUT", "/api/contributions/999/release-group"),
    ("POST", "/api/contributions/release-group/some-group/submit"),
    ("POST", "/api/jobs/999/flag-discdb"),
]


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
    """Every DiscDB contribution route rejects off-box callers with 403."""

    @pytest.mark.parametrize(("method", "path"), GUARDED_ROUTES)
    async def test_lan_client_rejected(self, db_override, method: str, path: str):
        """A LAN peer (10.0.0.5) is refused with 403 on every guarded route."""
        async with _client("10.0.0.5") as client:
            resp = await client.request(method, path)
        assert resp.status_code == 403, f"{method} {path} not guarded"
        assert "host machine" in resp.json()["detail"].lower()

    @pytest.mark.parametrize(("method", "path"), GUARDED_ROUTES)
    async def test_loopback_not_overblocked(self, db_override, method: str, path: str):
        """A loopback caller passes the guard and reaches the handler.

        This is the meaningful positive control: for the *same* route and
        request, switching only the client IP from LAN to loopback changes the
        outcome away from 403 — proving the guard keys off the client address,
        not on route behavior. (An empty-DB 200 alone would be vacuous.)
        """
        async with _client("127.0.0.1") as client:
            resp = await client.request(method, path)
        assert resp.status_code != 403, f"{method} {path} over-blocks loopback"

    async def test_fetch_cover_guard_precedes_ssrf(self, db_override):
        """The localhost guard fires before the fetch-cover SSRF allowlist.

        Off-box, even an SSRF-triggering URL yields 403 — not the 400 the SSRF
        guard would raise — proving the localhost check runs first.
        """
        async with _client("10.0.0.5") as client:
            resp = await client.post(
                "/api/contributions/999/fetch-cover",
                json={"image_url": "http://169.254.169.254/latest/meta-data/"},
            )
        assert resp.status_code == 403

    async def test_ipv4_mapped_loopback_allowed(self, db_override):
        """A dual-stack loopback (``::ffff:127.0.0.1``) is treated as local.

        When the server binds ``HOST=::``, local connections can arrive as an
        IPv4-mapped IPv6 address. The guard must not 403 the host's own user.
        """
        async with _client("::ffff:127.0.0.1") as client:
            resp = await client.get("/api/contributions")
        assert resp.status_code != 403
