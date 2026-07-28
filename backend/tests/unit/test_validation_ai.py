"""Tests for the AI provider connection-test endpoint."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import require_localhost_or_lan
from app.core.errors import AIProviderError
from app.main import app


@pytest.fixture
async def client():
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


class TestValidateAI:
    @pytest.mark.asyncio
    async def test_success_reports_the_model(self, client):
        with patch("app.core.ai_client.complete_json", new=AsyncMock(return_value={"ok": True})):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["version"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_provider_error_message_is_reported_verbatim(self, client):
        err = AIProviderError("no credits on this account", code="no_credits")
        with patch("app.core.ai_client.complete_json", new=AsyncMock(side_effect=err)):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["error"] == "no credits on this account"

    @pytest.mark.asyncio
    async def test_unknown_provider_is_rejected(self, client):
        resp = await client.post("/api/validate/ai", json={"provider": "hal9000", "api_key": "k"})
        assert resp.json()["valid"] is False
        assert "provider" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_blank_key_falls_back_to_stored_config(self, client):
        """The primary support case: a key was saved earlier and the user does not
        have it to hand. NOTE: get_config is mocked, never written to."""
        stored = SimpleNamespace(ai_api_key="sk-stored")
        spy = AsyncMock(return_value={"ok": True})
        with (
            patch("app.services.config_service.get_config", new=AsyncMock(return_value=stored)),
            patch("app.core.ai_client.complete_json", new=spy),
        ):
            resp = await client.post("/api/validate/ai", json={"provider": "openai"})
        assert resp.json()["valid"] is True
        assert spy.await_args.kwargs["api_key"] == "sk-stored"

    @pytest.mark.asyncio
    async def test_no_key_anywhere_is_invalid(self, client):
        stored = SimpleNamespace(ai_api_key="")
        with patch("app.services.config_service.get_config", new=AsyncMock(return_value=stored)):
            resp = await client.post("/api/validate/ai", json={"provider": "openai"})
        assert resp.json()["valid"] is False
        assert "no api key" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_retries_disabled_and_short_timeout(self, client):
        """A dead key must fail in one request, not pay the backoff ladder."""
        spy = AsyncMock(return_value={"ok": True})
        with patch("app.core.ai_client.complete_json", new=spy):
            await client.post("/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"})
        assert spy.await_args.kwargs["retries"] == 0
        assert spy.await_args.kwargs["timeout"] == 10.0

    @pytest.mark.asyncio
    async def test_empty_provider_response_is_invalid(self, client):
        with patch("app.core.ai_client.complete_json", new=AsyncMock(return_value=None)):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.json()["valid"] is False
