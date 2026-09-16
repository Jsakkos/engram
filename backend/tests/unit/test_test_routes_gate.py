"""The /api/test/* matcher harness must not be reachable from a release build.

Every endpoint on that router hands a client-supplied path to ffmpeg and
Whisper, so it carries two gates: require_debug (a release build has DEBUG off)
and require_localhost_or_lan (a DEBUG build on a LAN-exposed bind still only
answers the host machine unless the user opted in).

ASGITransport defaults the peer address to loopback, so the origin gate's 403
branch is only reachable by passing an explicit non-loopback client= to the
transport.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# (method, path, json body) for every route the gated router exposes.
ENDPOINTS = [
    ("post", "/api/test/subtitles", {"show_name": "Frasier", "season": 1}),
    ("post", "/api/test/transcribe", {"video_path": "/etc/passwd"}),
    ("post", "/api/test/match", {"video_path": "/etc/passwd", "show_name": "Frasier", "season": 1}),
]


def _client(peer: str | None = None) -> AsyncClient:
    transport = (
        ASGITransport(app=app) if peer is None else ASGITransport(app=app, client=(peer, 51234))
    )
    return AsyncClient(transport=transport, base_url="http://testserver")


def _lan_access(enabled: bool):
    """Drive require_localhost_or_lan's config read, whatever this machine's DB says."""
    stub = MagicMock(allow_lan_access=enabled)
    return patch("app.services.config_service.get_config", new=AsyncMock(return_value=stub))


@pytest.fixture
def debug_off(monkeypatch):
    monkeypatch.setattr("app.api.guards.settings.debug", False)


@pytest.fixture
def debug_on(monkeypatch):
    monkeypatch.setattr("app.api.guards.settings.debug", True)


@pytest.fixture
def stub_testing_service():
    """Neutralize the handlers so a test that passes the gates does no real work.

    test_routes imports these lazily inside each handler, so patching the
    source module is what the call site actually resolves.
    """
    with (
        patch("app.matcher.testing_service.download_subtitles", return_value={"ok": True}),
        patch("app.matcher.testing_service.transcribe_chunk", return_value={"ok": True}),
        patch("app.matcher.testing_service.match_episodes", return_value=[]),
    ):
        yield


class TestDebugGate:
    @pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
    async def test_blocked_when_debug_is_off(self, debug_off, method, path, body):
        async with _client() as ac:
            response = await getattr(ac, method)(path, json=body)
        assert response.status_code == 403
        assert "debug" in response.json()["detail"].lower()

    async def test_the_gate_runs_before_the_handler(self, debug_off):
        """A 403 must cost nothing: no ffmpeg, no Whisper, no scraping."""
        with patch("app.matcher.testing_service.transcribe_chunk") as transcribe:
            async with _client() as ac:
                response = await ac.post("/api/test/transcribe", json={"video_path": "/etc/passwd"})
        assert response.status_code == 403
        transcribe.assert_not_called()

    @pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
    async def test_allowed_from_loopback_when_debug_is_on(
        self, debug_on, stub_testing_service, method, path, body
    ):
        async with _client() as ac:
            response = await getattr(ac, method)(path, json=body)
        assert response.status_code == 200


class TestOriginGate:
    @pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
    async def test_lan_peer_blocked_when_lan_access_disabled(
        self, debug_on, stub_testing_service, method, path, body
    ):
        with _lan_access(False):
            async with _client(peer="192.168.1.50") as ac:
                response = await getattr(ac, method)(path, json=body)
        assert response.status_code == 403
        assert "host machine" in response.json()["detail"]

    async def test_lan_peer_allowed_when_lan_access_enabled(self, debug_on, stub_testing_service):
        with _lan_access(True):
            async with _client(peer="192.168.1.50") as ac:
                response = await ac.post(
                    "/api/test/subtitles", json={"show_name": "X", "season": 1}
                )
        assert response.status_code == 200

    async def test_debug_gate_still_closes_for_a_permitted_lan_peer(self, debug_off):
        """The two gates are independent: LAN opt-in does not unlock a release build."""
        with _lan_access(True):
            async with _client(peer="192.168.1.50") as ac:
                response = await ac.post(
                    "/api/test/subtitles", json={"show_name": "X", "season": 1}
                )
        assert response.status_code == 403
        assert "debug" in response.json()["detail"].lower()
