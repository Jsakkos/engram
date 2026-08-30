"""Tests for the local AI model-list endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.guards import require_localhost_or_lan
from app.main import app


@pytest.fixture
async def client():
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


def _mock_get(payload: dict, status: int = 200):
    response = MagicMock()
    response.json.return_value = payload
    response.status_code = status
    response.raise_for_status = MagicMock()
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(return_value=response)
    return c


class TestLocalModelsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_model_ids_from_the_server(self, client):
        payload = {"data": [{"id": "llama3.1:8b"}, {"id": "qwen2.5:7b"}]}
        with patch("app.api.validation.httpx.AsyncClient", return_value=_mock_get(payload)):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == ["llama3.1:8b", "qwen2.5:7b"]
        assert resp.json()["error"] is None

    @pytest.mark.asyncio
    async def test_queries_the_default_port_for_the_slug(self, client):
        mock = _mock_get({"data": []})
        with patch("app.api.validation.httpx.AsyncClient", return_value=mock):
            await client.get("/api/ai/local-models?provider=lmstudio")

        assert mock.get.await_args.args[0] == "http://localhost:1234/v1/models"

    @pytest.mark.asyncio
    async def test_unreachable_server_returns_200_with_an_error_string(self, client):
        import httpx

        c = AsyncMock()
        c.__aenter__ = AsyncMock(return_value=c)
        c.__aexit__ = AsyncMock(return_value=False)
        c.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("app.api.validation.httpx.AsyncClient", return_value=c):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        assert "Ollama" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_remote_provider_is_rejected(self, client):
        resp = await client.get("/api/ai/local-models?provider=openai")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        assert "local" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_unsafe_base_url_is_refused_without_a_request(self, client):
        with patch("app.api.validation.httpx.AsyncClient") as ctor:
            resp = await client.get(
                "/api/ai/local-models?provider=ollama&base_url=file:///etc/passwd"
            )

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_garbage_body_does_not_500(self, client):
        with patch("app.api.validation.httpx.AsyncClient", return_value=_mock_get({"nope": 1})):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
