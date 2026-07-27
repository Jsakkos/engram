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


class TestCompleteJsonGemini:
    @pytest.mark.asyncio
    async def test_gemini_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"episode": 3, "confidence": 0.95}'}]}}
                ]
            }
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="match this episode",
                schema={
                    "type": "object",
                    "properties": {
                        "episode": {"type": "integer"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["episode", "confidence"],
                },
                provider="gemini",
                api_key="AIzaSy-x",
            )

        assert result == {"episode": 3, "confidence": 0.95}
        call = mock.post.await_args
        url = call.args[0]
        assert url.startswith(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
        )
        assert call.kwargs["headers"]["x-goog-api-key"] == "AIzaSy-x"

        body = call.kwargs["json"]
        gen_cfg = body["generationConfig"]
        assert gen_cfg["responseMimeType"] == "application/json"
        assert gen_cfg["responseSchema"]["properties"]["episode"]["type"] == "integer"
        assert body["contents"][0]["parts"][0]["text"] == "match this episode"

    @pytest.mark.asyncio
    async def test_gemini_union_null_type_is_translated_to_nullable(self):
        """A JSON-Schema union ``type: [T, "null"]`` must be translated to
        Gemini's OpenAPI-subset form (single ``type`` + ``nullable: True``).

        Gemini's ``responseSchema`` is Protobuf-backed and rejects a list-valued
        ``type`` with HTTP 400 ("Proto field is not repeating, cannot start
        list"), which silently disabled all Gemini structured-output matching.
        """
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"episode": 1, "confidence": 1.0}'}]}}
                ]
            }
        )
        schema = {
            "type": "object",
            "properties": {
                "episode": {"type": "integer"},
                "runner_up": {
                    "type": ["object", "null"],
                    "properties": {"episode": {"type": "integer"}},
                },
            },
            "required": ["episode"],
        }
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(prompt="x", schema=schema, provider="gemini", api_key="AIzaSy-x")

        sent = mock.post.await_args.kwargs["json"]["generationConfig"]["responseSchema"]
        runner_up = sent["properties"]["runner_up"]
        assert runner_up["type"] == "object", f"expected scalar type, got {runner_up['type']!r}"
        assert runner_up.get("nullable") is True
        # Caller's original schema object must not be mutated in place.
        assert schema["properties"]["runner_up"]["type"] == ["object", "null"]

    def test_to_gemini_schema_recurses_into_array_items(self):
        """Union-null types nested inside array ``items`` are also translated,
        and plain scalar types / sibling keys are preserved untouched."""
        from app.core.ai_client import _to_gemini_schema

        translated = _to_gemini_schema(
            {
                "type": "object",
                "properties": {
                    "episodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"note": {"type": ["string", "null"]}},
                        },
                    },
                    "title": {"type": "string"},
                },
                "required": ["episodes"],
            }
        )

        note = translated["properties"]["episodes"]["items"]["properties"]["note"]
        assert note["type"] == "string"
        assert note["nullable"] is True
        assert translated["properties"]["title"]["type"] == "string"
        assert translated["required"] == ["episodes"]

    @pytest.mark.asyncio
    async def test_gemini_no_schema_still_works(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="AIzaSy-x"
            )

        assert result == {"x": 1}
        body = mock.post.await_args.kwargs["json"]
        gen_cfg = body["generationConfig"]
        assert gen_cfg["responseMimeType"] == "application/json"
        assert "responseSchema" not in gen_cfg

    @pytest.mark.asyncio
    async def test_gemini_empty_candidates_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": []})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="gemini", api_key="k")

        assert result is None


class TestRateLimitRetry:
    @pytest.mark.asyncio
    async def test_429_then_success(self):
        from httpx import HTTPStatusError, Request, Response

        from app.core.ai_client import complete_json

        bad_resp = MagicMock()
        bad_resp.status_code = 429
        req = Request("POST", "http://x")
        bad_resp.raise_for_status.side_effect = HTTPStatusError(
            "429", request=req, response=Response(429, request=req)
        )
        bad_resp.json.return_value = {}

        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.raise_for_status = MagicMock()
        good_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        }

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=[bad_resp, good_resp])

        with (
            patch("app.core.ai_client.httpx.AsyncClient", return_value=client),
            patch("app.core.ai_client.asyncio.sleep", new=AsyncMock()),
        ):
            result = await complete_json(prompt="x", schema=None, provider="gemini", api_key="k")

        assert result == {"ok": True}
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_429_exhausted_returns_none(self):
        from httpx import HTTPStatusError, Request, Response

        from app.core.ai_client import complete_json

        bad_resp = MagicMock()
        bad_resp.status_code = 429
        req = Request("POST", "http://x")
        bad_resp.raise_for_status.side_effect = HTTPStatusError(
            "429", request=req, response=Response(429, request=req)
        )
        bad_resp.json.return_value = {}

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=bad_resp)

        with (
            patch("app.core.ai_client.httpx.AsyncClient", return_value=client),
            patch("app.core.ai_client.asyncio.sleep", new=AsyncMock()),
        ):
            result = await complete_json(prompt="x", schema=None, provider="gemini", api_key="k")

        assert result is None
        assert client.post.await_count == 4  # initial + 3 retries


class TestCompleteJsonRaiseOnError:
    @pytest.mark.asyncio
    async def test_http_error_raises_aiprovidererror_when_flag_set(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=500)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError):
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="anthropic",
                    api_key="k",
                    raise_on_error=True,
                )

    @pytest.mark.asyncio
    async def test_http_error_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({}, status=500)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="anthropic", api_key="k")
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates_unwrapped_when_flag_set(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=client):
            with pytest.raises(RuntimeError):
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="anthropic",
                    api_key="k",
                    raise_on_error=True,
                )
        # A non-transport error must NOT be coerced into AIProviderError.
        assert not issubclass(RuntimeError, AIProviderError)
