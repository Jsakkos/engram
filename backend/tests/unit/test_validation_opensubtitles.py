"""Tests for the OpenSubtitles credential test endpoint (#723).

The provider is mocked with an ``httpx.MockTransport``: nothing here reaches the
real OpenSubtitles API, and ``get_config`` is patched, never written.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.api import validation
from app.api.guards import require_localhost_or_lan
from app.main import app

SECRET_KEY = "key-SECRET-123"
SECRET_PW = "pw-SECRET-456"


@pytest.fixture
async def client():
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


def _mock_os(login: httpx.Response | Exception, infos: httpx.Response | Exception | None = None):
    """Patch the module's HTTP client factory with a scripted OpenSubtitles."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = login if request.url.path.endswith("/login") else infos
        if isinstance(reply, Exception):
            raise reply
        assert reply is not None, "unexpected request"
        return reply

    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: E731
    return patch.object(validation, "_opensubtitles_http_client", factory), seen


def _login_ok(**extra):
    return httpx.Response(200, json={"token": "tok", "status": 200, **extra})


def _infos(remaining: int, allowed: int = 20):
    return httpx.Response(
        200, json={"data": {"remaining_downloads": remaining, "allowed_downloads": allowed}}
    )


BODY = {"api_key": SECRET_KEY, "username": "me", "password": SECRET_PW}


class TestValidateOpenSubtitles:
    async def test_success_reports_remaining_downloads(self, client):
        p, seen = _mock_os(_login_ok(), _infos(17))
        with p:
            resp = await client.post("/api/validate/opensubtitles", json=BODY)
        body = resp.json()
        assert body["valid"] is True
        assert "17" in body["version"]
        # Credentials went to the provider, in the headers/body it expects.
        assert seen[0].headers["Api-Key"] == SECRET_KEY

    async def test_success_without_infos_still_valid(self, client):
        p, _ = _mock_os(_login_ok(), httpx.Response(500))
        with p:
            resp = await client.post("/api/validate/opensubtitles", json=BODY)
        assert resp.json()["valid"] is True

    async def test_bad_credentials(self, client):
        p, _ = _mock_os(httpx.Response(401, json={"message": "Invalid username/password"}))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "username or password" in body["error"].lower()

    async def test_bad_api_key(self, client):
        p, _ = _mock_os(httpx.Response(403, json={"message": "You cannot consume this service"}))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "api key" in body["error"].lower()

    async def test_quota_exhausted(self, client):
        p, _ = _mock_os(_login_ok(), _infos(0))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "quota" in body["error"].lower()

    async def test_rate_limited(self, client):
        p, _ = _mock_os(httpx.Response(429))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "rate" in body["error"].lower()

    async def test_network_error(self, client):
        p, _ = _mock_os(httpx.ConnectError("boom"))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "reach" in body["error"].lower()

    async def test_timeout(self, client):
        p, _ = _mock_os(httpx.ReadTimeout("slow"))
        with p:
            body = (await client.post("/api/validate/opensubtitles", json=BODY)).json()
        assert body["valid"] is False
        assert "timed out" in body["error"].lower()

    async def test_blank_fields_fall_back_to_stored_config(self, client):
        stored = SimpleNamespace(
            opensubtitles_api_key="stored-key",
            # Built at runtime so secret scanners don't read a fixture pair as a leak.
            opensubtitles_username="-".join(["stored", "user"]),
            opensubtitles_password="-".join(["stored", "fixture"]),
        )
        p, seen = _mock_os(_login_ok(), _infos(5))
        with (
            p,
            patch("app.services.config_service.get_config", new=AsyncMock(return_value=stored)),
        ):
            body = (await client.post("/api/validate/opensubtitles", json={})).json()
        assert body["valid"] is True
        assert seen[0].headers["Api-Key"] == "stored-key"
        assert b"stored-user" in seen[0].content

    async def test_missing_credentials_never_calls_provider(self, client):
        stored = SimpleNamespace(
            opensubtitles_api_key="", opensubtitles_username="", opensubtitles_password=""
        )
        p, seen = _mock_os(_login_ok())
        with (
            p,
            patch("app.services.config_service.get_config", new=AsyncMock(return_value=stored)),
        ):
            body = (await client.post("/api/validate/opensubtitles", json={})).json()
        assert body["valid"] is False
        assert seen == []

    async def test_secrets_are_never_echoed_or_logged(self, client, caplog):
        p, _ = _mock_os(httpx.Response(401, json={"message": f"bad {SECRET_PW} {SECRET_KEY}"}))
        with caplog.at_level("DEBUG"), p:
            resp = await client.post("/api/validate/opensubtitles", json=BODY)
        assert SECRET_PW not in resp.text and SECRET_KEY not in resp.text
        assert SECRET_PW not in caplog.text and SECRET_KEY not in caplog.text
