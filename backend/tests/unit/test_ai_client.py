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


def _status_error_with_body(status: int, body: str):
    from httpx import HTTPStatusError, Request, Response

    req = Request("POST", "http://x")
    return HTTPStatusError("err", request=req, response=Response(status, request=req, text=body))


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


class TestClassifyProviderError:
    def _status_error(self, status: int, body: str):
        from httpx import HTTPStatusError, Request, Response

        req = Request("POST", "http://x")
        return HTTPStatusError(
            "err", request=req, response=Response(status, request=req, text=body)
        )

    @pytest.mark.parametrize(
        "provider,status,body,expected",
        [
            ("openai", 429, '{"error":{"code":"insufficient_quota"}}', "no_credits"),
            ("openai", 429, '{"error":{"code":"rate_limit_exceeded"}}', "rate_limited"),
            ("openai", 401, '{"error":{"code":"invalid_api_key"}}', "bad_key"),
            ("openai", 404, '{"error":{"code":"model_not_found"}}', "model_unavailable"),
            ("openrouter", 402, '{"error":{"message":"Insufficient credits"}}', "no_credits"),
            ("anthropic", 401, '{"error":{"type":"authentication_error"}}', "bad_key"),
            ("anthropic", 429, '{"error":{"type":"rate_limit_error"}}', "rate_limited"),
            ("gemini", 429, '{"error":{"status":"RESOURCE_EXHAUSTED"}}', "rate_limited"),
            # Bare INVALID_ARGUMENT with no key-related message is Gemini's catch-all
            # for validation failures (bad prompt, bad schema, bad model name, ...),
            # not evidence of a bad key.
            ("gemini", 400, '{"error":{"status":"INVALID_ARGUMENT"}}', "bad_request"),
            (
                "gemini",
                400,
                '{"error":{"status":"INVALID_ARGUMENT",'
                '"message":"API key not valid. Please pass a valid API key."}}',
                "bad_key",
            ),
            # Regression for PR #344: a schema-union 400 was misclassified as bad_key
            # because it also carries status INVALID_ARGUMENT. It must stay bad_request.
            (
                "gemini",
                400,
                '{"error":{"status":"INVALID_ARGUMENT",'
                '"message":"Proto field is not repeating, cannot start list."}}',
                "bad_request",
            ),
            ("gemini", 403, '{"error":{"status":"PERMISSION_DENIED"}}', "model_unavailable"),
            ("openai", 500, '{"error":{"message":"oops"}}', "unknown"),
        ],
    )
    def test_http_statuses(self, provider, status, body, expected):
        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(provider, self._status_error(status, body))
        assert code == expected
        assert message  # never empty; the UI renders it verbatim

    def test_connect_error_is_network(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, _ = classify_provider_error("openai", httpx.ConnectError("no route"))
        assert code == "network"

    def test_timeout_is_timeout(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, _ = classify_provider_error("openai", httpx.ReadTimeout("slow"))
        assert code == "timeout"

    def test_message_never_echoes_body_content(self):
        from app.core.ai_client import classify_provider_error

        huge = '{"error":{"message":"' + ("x" * 9000) + '"}}'
        code, message = classify_provider_error("openai", self._status_error(429, huge))
        assert "x" * 9000 not in message

    @pytest.mark.parametrize(
        "provider,label",
        [
            ("openai", "OpenAI"),
            ("anthropic", "Anthropic"),
            ("openrouter", "OpenRouter"),
            ("gemini", "Gemini"),
        ],
    )
    def test_message_uses_the_display_label_not_the_slug(self, provider, label):
        """These sentences are read by users, so the lowercase internal slug
        must not leak into them ("OpenAI accepted the key", not "openai")."""
        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error(
            provider, self._status_error(429, '{"error":{"code":"insufficient_quota"}}')
        )
        assert label in message
        assert provider not in message

    def test_unknown_provider_falls_back_to_its_own_name(self):
        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error("hal9000", self._status_error(500, "{}"))
        assert "hal9000" in message


class TestDetailFrom:
    def _status_error(self, status: int, body: str):
        from httpx import HTTPStatusError, Request, Response

        req = Request("POST", "http://x")
        return HTTPStatusError(
            "err", request=req, response=Response(status, request=req, text=body)
        )

    def test_http_status_error_body_is_capped_at_2048(self):
        from app.core.ai_client import detail_from

        huge = '{"error":{"message":"' + ("x" * 9000) + '"}}'
        detail = detail_from(self._status_error(429, huge))
        assert len(detail) == 2048
        assert detail == huge[:2048]

    def test_non_http_status_error_falls_back_to_str_and_is_capped(self):
        import httpx

        from app.core.ai_client import detail_from

        exc = httpx.ConnectError("no route" * 500)
        detail = detail_from(exc)
        assert detail == str(exc)[:2048]
        assert len(detail) <= 2048


class TestCompleteJsonErrorCodes:
    @pytest.mark.asyncio
    async def test_429_quota_raises_no_credits(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=429)
        mock.post.return_value.raise_for_status.side_effect = _status_error_with_body(
            429, '{"error":{"code":"insufficient_quota"}}'
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="k",
                    raise_on_error=True,
                    retries=0,
                )
        assert exc_info.value.code == "no_credits"
        assert "credits" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_no_credits_is_not_retried(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=429)
        mock.post.return_value.raise_for_status.side_effect = _status_error_with_body(
            429, '{"error":{"code":"insufficient_quota"}}'
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError):
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="k",
                    raise_on_error=True,
                )
        assert mock.post.await_count == 1

    @pytest.mark.asyncio
    async def test_rate_limited_is_still_retried(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=429)
        mock.post.return_value.raise_for_status.side_effect = _status_error_with_body(
            429, '{"error":{"code":"rate_limit_exceeded"}}'
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with patch("app.core.ai_client.asyncio.sleep", new=AsyncMock()):
                with pytest.raises(AIProviderError) as exc_info:
                    await complete_json(
                        prompt="x",
                        schema=None,
                        provider="openai",
                        api_key="k",
                        raise_on_error=True,
                        retries=2,
                    )
        assert exc_info.value.code == "rate_limited"
        assert mock.post.await_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_timeout_param_reaches_the_client(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"message": {"content": '{"ok": true}'}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(
                prompt="x", schema=None, provider="openai", api_key="k", timeout=10.0
            )
        assert ctor.call_args.kwargs["timeout"] == 10.0


class TestNullContent:
    """A provider that sends an explicit JSON null for its text field must produce
    a clean empty result, not an AttributeError.

    ``.get(key, "")`` returns None when the key is PRESENT with a null value, so
    the default never applies. OpenAI does this on a refusal.

    Since Task 9 (surfacing truncated/malformed responses), the AttributeError
    concern is verified differently under ``raise_on_error=True``: null content
    is indistinguishable from any other unparseable body, so it now raises the
    typed ``AIProviderError(code="malformed_response")`` rather than either
    silently returning None or blowing up with an AttributeError. The plain
    ``None`` contract is preserved for the default (``raise_on_error=False``)
    path, which ``curator._maybe_add_llm_suggestion`` and
    ``ai_identifier.identify_from_label`` rely on.
    """

    @pytest.mark.asyncio
    async def test_openai_null_content_raises_malformed_when_raise_on_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({"choices": [{"message": {"content": None}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "malformed_response"

    @pytest.mark.asyncio
    async def test_openai_null_content_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"message": {"content": None}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x",
                schema=None,
                provider="openai",
                api_key="k",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_anthropic_null_text_raises_malformed_when_raise_on_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({"content": [{"text": None}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="anthropic",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "malformed_response"

    @pytest.mark.asyncio
    async def test_anthropic_null_text_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": None}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x",
                schema=None,
                provider="anthropic",
                api_key="k",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_gemini_null_text_raises_malformed_when_raise_on_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({"candidates": [{"content": {"parts": [{"text": None}]}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "malformed_response"

    @pytest.mark.asyncio
    async def test_gemini_null_text_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": [{"content": {"parts": [{"text": None}]}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x",
                schema=None,
                provider="gemini",
                api_key="k",
            )
        assert result is None

    def test_parse_json_text_tolerates_none(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text(None) is None
        assert _parse_json_text("") is None
        assert _parse_json_text("   ") is None


class TestTruncatedAndMalformed:
    @pytest.mark.asyncio
    async def test_openai_finish_reason_length_raises_truncated(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(
            {
                "choices": [
                    {"finish_reason": "length", "message": {"content": '{"episode": 3, "confid'}}
                ]
            }
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "response_truncated"

    @pytest.mark.asyncio
    async def test_anthropic_max_tokens_raises_truncated(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(
            {"stop_reason": "max_tokens", "content": [{"text": '{"episode": 3, "conf'}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="anthropic",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "response_truncated"

    @pytest.mark.asyncio
    async def test_gemini_max_tokens_raises_truncated(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(
            {
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {"parts": [{"text": '{"episode": 3, "conf'}]},
                    }
                ]
            }
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "response_truncated"

    @pytest.mark.asyncio
    async def test_malformed_raises_when_raise_on_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(
            {"choices": [{"finish_reason": "stop", "message": {"content": "I don't know"}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "malformed_response"

    @pytest.mark.asyncio
    async def test_malformed_still_returns_none_by_default(self):
        """curator._maybe_add_llm_suggestion relies on the None contract."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"finish_reason": "stop", "message": {"content": "I don't know"}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="openai", api_key="k")
        assert result is None

    @pytest.mark.asyncio
    async def test_truncated_still_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {
                "choices": [
                    {"finish_reason": "length", "message": {"content": '{"episode": 3, "confid'}}
                ]
            }
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(prompt="x", schema=None, provider="openai", api_key="k")
        assert result is None


def _mock_httpx_with_models(post_status: int, models: list[str] | None = None, get_exc=None):
    """Mocked client whose POST fails and whose GET answers Gemini's ListModels.

    Both calls go through the same patched ``httpx.AsyncClient``, so the ListModels
    probe is told apart from the completion by verb, not by URL.
    """
    client = _mock_httpx({}, status=post_status)
    if get_exc is not None:
        client.get = AsyncMock(side_effect=get_exc)
        return client
    listing = MagicMock()
    listing.status_code = 200
    listing.raise_for_status = MagicMock()
    listing.json.return_value = {
        "models": [
            {"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
            for m in (models or [])
        ]
    }
    client.get = AsyncMock(return_value=listing)
    return client


class TestGeminiModelUnavailableIsActionable:
    """A Gemini 404 proves the key authenticated but cannot see the model.

    Google resolves auth *before* the model (a bad key is 400, a missing key 403),
    so the only useful next step is naming the models the key *can* reach. Without
    that, the user is told the model is unavailable and given nowhere to go.
    """

    @pytest.mark.asyncio
    async def test_404_message_names_the_models_the_key_can_use(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx_with_models(404, ["gemini-3.5-flash", "gemini-2.5-pro"])
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="AIzaSy-x",
                    raise_on_error=True,
                    retries=0,
                )

        assert exc.value.code == "model_unavailable"
        message = str(exc.value)
        assert "gemini-3.5-flash" in message
        assert "gemini-2.5-pro" in message

    @pytest.mark.asyncio
    async def test_404_names_the_model_that_was_requested(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx_with_models(404, ["gemini-3.5-flash"])
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="AIzaSy-x",
                    raise_on_error=True,
                    retries=0,
                )

        assert "gemini-2.5-flash-lite" in str(exc.value)

    @pytest.mark.asyncio
    async def test_listmodels_failure_leaves_the_base_message_intact(self):
        """The probe is a bonus. It must never turn one failure into two."""
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx_with_models(404, get_exc=RuntimeError("boom"))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="AIzaSy-x",
                    raise_on_error=True,
                    retries=0,
                )

        assert exc.value.code == "model_unavailable"
        assert "does not have access" in str(exc.value)

    @pytest.mark.asyncio
    async def test_bulk_path_does_not_pay_for_the_probe(self):
        """raise_on_error=False is the bulk matcher, whose message nobody reads.
        Probing there would add one request per title on a disc-wide failure."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx_with_models(404, ["gemini-3.5-flash"])
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="AIzaSy-x", retries=0
            )

        assert result is None
        mock.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_gemini_404_does_not_probe(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx_with_models(404, ["gpt-4o-mini"])
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError):
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="openai",
                    api_key="sk-x",
                    raise_on_error=True,
                    retries=0,
                )

        mock.get.assert_not_awaited()


class TestModelOverride:
    """A hardcoded model leaves a user whose key cannot reach it with no recourse."""

    @pytest.mark.asyncio
    async def test_gemini_override_reaches_the_url(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="x",
                schema=None,
                provider="gemini",
                api_key="k",
                model="gemini-3.5-flash",
            )

        assert mock.post.await_args.args[0].endswith("/models/gemini-3.5-flash:generateContent")

    @pytest.mark.asyncio
    async def test_openrouter_slashed_model_is_allowed(self):
        """OpenRouter model ids legitimately contain a slash."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"message": {"content": '{"x": 1}'}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x",
                schema=None,
                provider="openrouter",
                api_key="k",
                model="google/gemini-2.5-flash-lite",
            )

        assert result == {"x": 1}
        assert mock.post.await_args.kwargs["json"]["model"] == "google/gemini-2.5-flash-lite"

    @pytest.mark.parametrize(
        "bad",
        [
            "../../v1beta/models/evil",
            "/absolute/path",
            "model name with spaces",
            "model?key=leak",
            "model#frag",
            "..",
            # Python's $ matches before a single trailing newline, so a ^...$
            # anchor would let this through. The pattern uses \Z for that reason.
            "gemini-2.5-flash-lite\n",
            "gemini-2.5-flash-lite\r\n",
        ],
    )
    @pytest.mark.asyncio
    async def test_unsafe_model_names_are_rejected_before_any_request(self, bad):
        """The Gemini model is interpolated into a URL path, so an unvalidated
        override is a request-forgery primitive, not merely a typo."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": []})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="k", model=bad
            )

        assert result is None
        mock.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsafe_model_name_is_explained_when_raising(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({"candidates": []})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="gemini",
                    api_key="k",
                    model="../evil",
                    raise_on_error=True,
                )

        assert exc.value.code == "bad_request"
        assert "model name" in str(exc.value).lower()
        mock.post.assert_not_awaited()


class TestLocalProviderVocabulary:
    def test_local_providers_are_ollama_and_lmstudio(self):
        from app.core.ai_client import LOCAL_PROVIDERS

        assert LOCAL_PROVIDERS == {"ollama", "lmstudio"}

    def test_default_base_urls_match_upstream_conventions(self):
        from app.core.ai_client import LOCAL_DEFAULT_BASE_URLS

        assert LOCAL_DEFAULT_BASE_URLS["ollama"] == "http://localhost:11434/v1"
        assert LOCAL_DEFAULT_BASE_URLS["lmstudio"] == "http://localhost:1234/v1"

    def test_blank_configured_url_falls_back_to_default(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("ollama", "") == "http://localhost:11434/v1"
        assert resolve_local_base_url("lmstudio", "   ") == "http://localhost:1234/v1"

    def test_configured_url_wins_and_trailing_slash_is_stripped(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("ollama", "http://box:8080/v1/") == "http://box:8080/v1"

    def test_resolve_returns_blank_for_a_remote_provider(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("openai", "") == ""

    def test_known_providers_covers_remote_and_local(self):
        from app.core.ai_client import KNOWN_PROVIDERS

        assert {"anthropic", "openai", "openrouter", "gemini"} <= KNOWN_PROVIDERS
        assert {"ollama", "lmstudio"} <= KNOWN_PROVIDERS


class TestAiIsConfigured:
    def test_remote_provider_requires_a_key(self):
        from app.core.ai_client import ai_is_configured

        assert ai_is_configured("openai", "sk-x") is True
        assert ai_is_configured("openai", "") is False

    def test_local_provider_needs_no_key(self):
        from app.core.ai_client import ai_is_configured

        assert ai_is_configured("ollama", "") is True
        assert ai_is_configured("lmstudio", "") is True


class TestLocalErrorMessages:
    def test_network_failure_names_the_product_and_url(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(
            "ollama",
            httpx.ConnectError("refused"),
            base_url="http://localhost:11434/v1",
        )
        assert code == "network"
        assert "Ollama" in message
        assert "http://localhost:11434/v1" in message
        assert "ollama serve" in message

    def test_lmstudio_network_failure_does_not_mention_ollama(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error(
            "lmstudio",
            httpx.ConnectError("refused"),
            base_url="http://localhost:1234/v1",
        )
        assert "LM Studio" in message
        assert "ollama" not in message.lower()

    def test_404_tells_the_user_to_pull_the_model(self):
        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(
            "ollama",
            _status_error_with_body(404, "model not found"),
            model="llama3.1:8b",
        )
        assert code == "model_unavailable"
        assert "ollama pull llama3.1:8b" in message

    def test_remote_providers_keep_their_wording(self):
        """Regression guard: the local table must not leak into remote text."""
        import httpx

        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error("openai", httpx.ConnectError("refused"))
        assert message == "Could not reach OpenAI. Check the internet connection."

    def test_no_credits_is_never_reported_for_a_local_provider(self):
        """A local server has no billing, so the remote wording would be nonsense."""
        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error("ollama", _status_error_with_body(402, ""))
        assert "credits" not in message.lower()


def _openai_shaped(text: str):
    """A minimal OpenAI-compatible chat-completions body."""
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


class TestLocalDispatch:
    @pytest.mark.asyncio
    async def test_ollama_posts_to_the_default_endpoint_without_a_key(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="llama3.1:8b",
            )

        assert result == {"ok": True}
        call = mock.post.await_args
        assert call.args[0] == "http://localhost:11434/v1/chat/completions"
        assert call.kwargs["json"]["model"] == "llama3.1:8b"

    @pytest.mark.asyncio
    async def test_lmstudio_uses_its_own_default_port(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=None,
                provider="lmstudio",
                api_key="",
                model="qwen2.5-7b-instruct",
            )

        assert mock.post.await_args.args[0] == "http://localhost:1234/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_configured_base_url_overrides_the_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="llama3.1:8b",
                base_url="http://gpubox:11434/v1",
            )

        assert mock.post.await_args.args[0] == "http://gpubox:11434/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_local_default_timeout_is_generous(self):
        """A cold LM Studio JIT-loads weights; 30s would look like a broken feature."""
        from app.core.ai_client import LOCAL_TIMEOUT_SECONDS, complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(prompt="hi", schema=None, provider="ollama", api_key="", model="m")

        assert ctor.call_args.kwargs["timeout"] == LOCAL_TIMEOUT_SECONDS
        assert LOCAL_TIMEOUT_SECONDS >= 300.0

    @pytest.mark.asyncio
    async def test_remote_default_timeout_is_unchanged(self):
        from app.core.ai_client import _TIMEOUT_SECONDS, complete_json

        mock = _mock_httpx({"content": [{"text": '{"ok": true}'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(prompt="hi", schema=None, provider="anthropic", api_key="sk-ant-x")

        assert ctor.call_args.kwargs["timeout"] == _TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_explicit_timeout_still_wins_for_a_local_provider(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="m",
                timeout=5.0,
            )

        assert ctor.call_args.kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_blank_model_raises_a_pointed_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        with pytest.raises(AIProviderError) as exc:
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model=None,
                raise_on_error=True,
            )

        assert "Select a model" in str(exc.value)
        assert exc.value.code == "bad_request"

    @pytest.mark.asyncio
    async def test_blank_model_returns_none_when_not_raising(self):
        from app.core.ai_client import complete_json

        result = await complete_json(
            prompt="hi", schema=None, provider="ollama", api_key="", model=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unsafe_base_url_is_refused_before_any_request(self):
        from app.core.ai_client import complete_json

        with patch("app.core.ai_client.httpx.AsyncClient") as ctor:
            result = await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="m",
                base_url="file:///etc/passwd",
            )

        assert result is None
        ctor.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model_id",
        ["llama3.1:8b", "qwen2.5:7b-instruct-q4_K_M", "lmstudio-community/Model-GGUF"],
    )
    async def test_real_local_model_ids_are_accepted(self, model_id):
        """A rejection here surfaces as a misleading 'clear the AI model field'."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="hi", schema=None, provider="ollama", api_key="", model=model_id
            )

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_remote_provider_with_empty_key_still_returns_none(self):
        """ai_is_configured must not loosen the credential gate for remote slugs."""
        from app.core.ai_client import complete_json

        with patch("app.core.ai_client.httpx.AsyncClient") as ctor:
            result = await complete_json(prompt="hi", schema=None, provider="openai", api_key="")

        assert result is None
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_openai_still_routes_to_its_own_endpoint(self):
        """The is_local branch must not swallow the named remote slugs."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(prompt="hi", schema=None, provider="openai", api_key="sk-x")

        assert mock.post.await_args.args[0] == "https://api.openai.com/v1/chat/completions"


class TestLocalStructuredOutputDialect:
    """LM Studio rejects response_format={"type": "json_object"} outright, so it

    needs the richer json_schema form; ollama and the remote OpenAI-compatible
    slugs stay on json_object (verified live against LM Studio; see #606).
    """

    @pytest.mark.asyncio
    async def test_lmstudio_with_schema_uses_json_schema_dialect(self):
        from app.core.ai_client import complete_json

        schema = {"type": "object", "properties": {"episode": {"type": "integer"}}}
        mock = _mock_httpx(_openai_shaped('{"episode": 3}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=schema,
                provider="lmstudio",
                api_key="",
                model="qwen2.5-7b-instruct",
            )

        response_format = mock.post.await_args.kwargs["json"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == schema

    @pytest.mark.asyncio
    async def test_ollama_with_schema_still_uses_json_object(self):
        from app.core.ai_client import complete_json

        schema = {"type": "object", "properties": {"episode": {"type": "integer"}}}
        mock = _mock_httpx(_openai_shaped('{"episode": 3}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=schema,
                provider="ollama",
                api_key="",
                model="llama3.1:8b",
            )

        assert mock.post.await_args.kwargs["json"]["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_openai_with_schema_still_uses_json_object(self):
        """Regression guard: existing openai/openrouter users must not change dialect."""
        from app.core.ai_client import complete_json

        schema = {"type": "object", "properties": {"episode": {"type": "integer"}}}
        mock = _mock_httpx(_openai_shaped('{"episode": 3}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(prompt="hi", schema=schema, provider="openai", api_key="sk-x")

        assert mock.post.await_args.kwargs["json"]["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_lmstudio_without_schema_sends_no_response_format(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=None,
                provider="lmstudio",
                api_key="",
                model="qwen2.5-7b-instruct",
            )

        assert "response_format" not in mock.post.await_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_lmstudio_forwards_the_real_episode_matcher_schema_intact(self):
        """Pins the union-type case (runner_up: ["object", "null"]) that live

        testing against LM Studio exercised with strict=True.
        """
        from app.core.ai_client import complete_json
        from app.matcher.llm_episode_matcher import RESPONSE_SCHEMA

        mock = _mock_httpx(_openai_shaped('{"episode": 3, "confidence": 0.9}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=RESPONSE_SCHEMA,
                provider="lmstudio",
                api_key="",
                model="qwen2.5-7b-instruct",
            )

        response_format = mock.post.await_args.kwargs["json"]["response_format"]
        assert response_format == {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": RESPONSE_SCHEMA},
        }


class TestProviderLabelInFallbackMessages:
    """The truncated/malformed paths must name the provider, not its slug."""

    @pytest.mark.asyncio
    async def test_truncated_response_uses_the_display_label_for_ollama(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        truncated = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        mock = _mock_httpx(truncated)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="hi",
                    schema=None,
                    provider="ollama",
                    api_key="",
                    model="m",
                    raise_on_error=True,
                )

        assert "Ollama" in str(exc.value)
        assert "ollama " not in str(exc.value)

    @pytest.mark.asyncio
    async def test_truncated_response_uses_the_display_label_for_openai(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        truncated = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        mock = _mock_httpx(truncated)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="hi",
                    schema=None,
                    provider="openai",
                    api_key="sk-x",
                    raise_on_error=True,
                )

        assert "OpenAI cut the response off" in str(exc.value)

    @pytest.mark.asyncio
    async def test_malformed_response_uses_the_display_label(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(_openai_shaped("not json at all"))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc:
                await complete_json(
                    prompt="hi",
                    schema=None,
                    provider="openai",
                    api_key="sk-x",
                    raise_on_error=True,
                )

        assert "OpenAI returned a reply that was not valid JSON" in str(exc.value)


class TestPreambleTolerantParsing:
    def test_extracts_json_after_a_reasoning_preamble(self):
        from app.core.ai_client import _parse_json_text

        text = 'Let me think about this. The answer is:\n{"episode": 3, "confidence": 0.8}'
        assert _parse_json_text(text) == {"episode": 3, "confidence": 0.8}

    def test_ignores_trailing_commentary(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('{"ok": true}\nHope that helps!') == {"ok": True}

    def test_handles_nested_braces(self):
        from app.core.ai_client import _parse_json_text

        text = 'Result: {"a": {"b": 1}, "c": 2} done'
        assert _parse_json_text(text) == {"a": {"b": 1}, "c": 2}

    def test_ignores_braces_inside_strings(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('x {"title": "a } b"} y') == {"title": "a } b"}

    def test_plain_json_is_unaffected(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('{"ok": true}') == {"ok": True}

    def test_fenced_json_is_still_handled(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('```json\n{"ok": true}\n```') == {"ok": True}

    def test_genuinely_unparseable_text_still_returns_none(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text("I could not determine the episode.") is None

    def test_unbalanced_braces_return_none(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('here you go {"ok": true') is None

    def test_a_bare_json_array_still_returns_none(self):
        """complete_json's contract is a dict; an array is not a usable result."""
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text("[1, 2, 3]") is None


class TestModelSubstitutionWarning:
    """LM Studio serves whichever model is loaded when asked for an unknown one.

    validate_ai's pre-flight only runs when the user presses Test Connection, so
    swapping the loaded model afterwards would let real matches run on a
    different model with nothing in the result showing it. The response names
    the model that answered, so this costs no extra request.
    """

    @pytest.mark.asyncio
    async def test_warns_when_a_local_server_answers_with_another_model(self, caplog):
        from app.core.ai_client import complete_json

        body = _openai_shaped('{"ok": true}')
        body["model"] = "google/gemma-4-e4b"
        mock = _mock_httpx(body)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with caplog.at_level("WARNING"):
                result = await complete_json(
                    prompt="hi",
                    schema=None,
                    provider="lmstudio",
                    api_key="",
                    model="ibm/granite-4-h-tiny",
                )

        # Still usable: a warning, not a failure. Refusing would strand the job.
        assert result == {"ok": True}
        assert "google/gemma-4-e4b" in caplog.text
        assert "ibm/granite-4-h-tiny" in caplog.text

    @pytest.mark.asyncio
    async def test_no_warning_when_the_model_matches(self, caplog):
        from app.core.ai_client import complete_json

        body = _openai_shaped('{"ok": true}')
        body["model"] = "ibm/granite-4-h-tiny"
        mock = _mock_httpx(body)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with caplog.at_level("WARNING"):
                await complete_json(
                    prompt="hi",
                    schema=None,
                    provider="lmstudio",
                    api_key="",
                    model="ibm/granite-4-h-tiny",
                )

        assert "not the requested" not in caplog.text

    @pytest.mark.asyncio
    async def test_hosted_providers_are_not_warned_about_pinned_versions(self, caplog):
        """OpenAI answers "gpt-4o-mini" with "gpt-4o-mini-2024-07-18"."""
        from app.core.ai_client import complete_json

        body = _openai_shaped('{"ok": true}')
        body["model"] = "gpt-4o-mini-2024-07-18"
        mock = _mock_httpx(body)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with caplog.at_level("WARNING"):
                await complete_json(prompt="hi", schema=None, provider="openai", api_key="sk-x")

        assert "not the requested" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_response_without_a_model_field_does_not_warn(self, caplog):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with caplog.at_level("WARNING"):
                await complete_json(
                    prompt="hi", schema=None, provider="ollama", api_key="", model="m"
                )

        assert "not the requested" not in caplog.text
