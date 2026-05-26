"""Tests for the shared AI client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_httpx(response_json: dict, status: int = 200):
    """Build a mocked httpx.AsyncClient context manager with one POST response."""
    response = MagicMock()
    response.json.return_value = response_json
    response.status_code = status
    response.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response

        req = Request("POST", "http://x")
        response.raise_for_status.side_effect = HTTPStatusError(
            "err", request=req, response=Response(status, request=req)
        )

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return client


class TestCompleteJsonAnthropic:
    @pytest.mark.asyncio
    async def test_anthropic_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": '{"episode": 3, "confidence": 0.9}'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="match this",
                schema=None,
                provider="anthropic",
                api_key="sk-ant-x",
            )

        assert result == {"episode": 3, "confidence": 0.9}
        call = mock.post.await_args
        assert call.args[0] == "https://api.anthropic.com/v1/messages"
        assert call.kwargs["headers"]["x-api-key"] == "sk-ant-x"
        assert call.kwargs["headers"]["anthropic-version"] == "2023-06-01"
        body = call.kwargs["json"]
        assert body["model"] == "claude-haiku-4-5-20251001"
        assert body["messages"][0]["content"] == "match this"

    @pytest.mark.asyncio
    async def test_anthropic_unparseable_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": "I don't know"}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="anthropic", api_key="k")

        assert result is None

    @pytest.mark.asyncio
    async def test_anthropic_strips_code_fence(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": '```json\n{"a": 1}\n```'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="anthropic", api_key="k")

        assert result == {"a": 1}


class TestCompleteJsonOpenAI:
    @pytest.mark.asyncio
    async def test_openai_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"message": {"content": '{"episode": 5, "confidence": 0.8}'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="openai", api_key="sk-x")

        assert result == {"episode": 5, "confidence": 0.8}
        call = mock.post.await_args
        assert call.args[0] == "https://api.openai.com/v1/chat/completions"
        assert call.kwargs["headers"]["Authorization"] == "Bearer sk-x"
        body = call.kwargs["json"]
        assert body["model"] == "gpt-4o-mini"
        assert body["temperature"] == 0

    @pytest.mark.asyncio
    async def test_openai_response_format_when_schema(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"message": {"content": '{"episode": 1, "confidence": 0.5}'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="x",
                schema={"type": "object", "properties": {"episode": {"type": "integer"}}},
                provider="openai",
                api_key="sk-x",
            )

        body = mock.post.await_args.kwargs["json"]
        assert body["response_format"] == {"type": "json_object"}


class TestCompleteJsonOpenRouter:
    @pytest.mark.asyncio
    async def test_openrouter_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"message": {"content": '{"ok": true}'}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="openrouter", api_key="sk-or-x"
            )

        assert result == {"ok": True}
        call = mock.post.await_args
        assert call.args[0] == "https://openrouter.ai/api/v1/chat/completions"
        body = call.kwargs["json"]
        assert body["model"] == "anthropic/claude-haiku-4-5-20251001"
