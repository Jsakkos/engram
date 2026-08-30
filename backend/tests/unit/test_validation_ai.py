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
    async def test_config_read_failure_does_not_500(self, client):
        """This endpoint promises never to 500. A config read can fail on a
        locked or migrating DB, and it is the one call outside the try block."""
        with patch(
            "app.services.config_service.get_config",
            new=AsyncMock(side_effect=RuntimeError("database is locked")),
        ):
            resp = await client.post("/api/validate/ai", json={"provider": "openai"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        assert "saved api key" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_provider_response_is_invalid(self, client):
        with patch("app.core.ai_client.complete_json", new=AsyncMock(return_value=None)):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.json()["valid"] is False


class TestValidateAiLocalProviders:
    @pytest.mark.asyncio
    async def test_local_provider_is_accepted_without_a_key(self, client):
        from unittest.mock import AsyncMock, patch

        with patch("app.core.ai_client.complete_json", new=AsyncMock(return_value={"ok": True})):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "ollama", "model": "llama3.1:8b"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_local_provider_with_no_model_is_rejected_clearly(self, client):
        resp = await client.post("/api/validate/ai", json={"provider": "ollama"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "model" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_provider_is_still_rejected(self, client):
        resp = await client.post("/api/validate/ai", json={"provider": "notreal"})

        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        assert "Unknown AI provider" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_local_validation_uses_the_longer_timeout(self, client):
        from unittest.mock import AsyncMock, patch

        from app.core.ai_client import LOCAL_VALIDATE_TIMEOUT_SECONDS

        spy = AsyncMock(return_value={"ok": True})
        with (
            patch("app.core.ai_client.complete_json", new=spy),
            patch(
                "app.api.validation._fetch_local_model_ids",
                new=AsyncMock(return_value=(["qwen2.5-7b-instruct"], None)),
            ),
        ):
            await client.post(
                "/api/validate/ai",
                json={"provider": "lmstudio", "model": "qwen2.5-7b-instruct"},
            )

        assert spy.await_args.kwargs["timeout"] == LOCAL_VALIDATE_TIMEOUT_SECONDS


class TestValidateAiLocalModelPreflight:
    """LM Studio answers a request for an unknown model by silently serving
    whichever model is currently loaded (HTTP 200, no error), so a typo'd model
    name would otherwise sail through this endpoint. The pre-flight confirms the
    requested model is actually in the server's own list before spending a
    completion request on it."""

    @pytest.mark.asyncio
    async def test_model_not_on_server_is_rejected_before_any_completion(self, client):
        spy = AsyncMock(return_value={"ok": True})
        with (
            patch("app.core.ai_client.complete_json", new=spy),
            patch(
                "app.api.validation._fetch_local_model_ids",
                new=AsyncMock(return_value=(["google/gemma-4-e4b", "qwen2.5-7b"], None)),
            ),
        ):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "lmstudio", "model": "nope/not-a-real-model"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "not installed" in body["error"].lower() or "not-a-real-model" in body["error"]
        assert "google/gemma-4-e4b" in body["error"]
        spy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_on_server_proceeds_to_completion(self, client):
        spy = AsyncMock(return_value={"ok": True})
        with (
            patch("app.core.ai_client.complete_json", new=spy),
            patch(
                "app.api.validation._fetch_local_model_ids",
                new=AsyncMock(return_value=(["google/gemma-4-e4b", "qwen2.5-7b"], None)),
            ),
        ):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "lmstudio", "model": "qwen2.5-7b"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreachable_server_does_not_short_circuit(self, client):
        """An unreadable model list must not block validation with a confusing
        "not installed" message; fall through and let the completion produce
        the better "could not reach" error instead."""
        spy = AsyncMock(return_value={"ok": True})
        with (
            patch("app.core.ai_client.complete_json", new=spy),
            patch(
                "app.api.validation._fetch_local_model_ids",
                new=AsyncMock(return_value=([], "Could not reach LM Studio")),
            ),
        ):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "lmstudio", "model": "whatever-model"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remote_provider_never_triggers_preflight(self, client):
        fetch_spy = AsyncMock(return_value=(["gpt-4o-mini"], None))
        with (
            patch("app.core.ai_client.complete_json", new=AsyncMock(return_value={"ok": True})),
            patch("app.api.validation._fetch_local_model_ids", new=fetch_spy),
        ):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "openai", "api_key": "sk-x", "model": "some-other-model"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        fetch_spy.assert_not_awaited()
