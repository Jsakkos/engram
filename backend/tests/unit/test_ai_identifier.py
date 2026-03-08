"""Tests for AI-powered disc title resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ai_identifier import _parse_response, identify_from_label


class TestParseResponse:
    def test_valid_json(self):
        result = _parse_response('{"title": "Inception", "year": 2010, "type": "movie"}')
        assert result == {"title": "Inception", "year": 2010, "type": "movie"}

    def test_json_with_code_fences(self):
        text = '```json\n{"title": "Inception", "year": 2010, "type": "movie"}\n```'
        result = _parse_response(text)
        assert result == {"title": "Inception", "year": 2010, "type": "movie"}

    def test_null_title(self):
        result = _parse_response('{"title": null, "year": null, "type": null}')
        assert result is None

    def test_empty_title(self):
        result = _parse_response('{"title": "", "year": null, "type": null}')
        assert result is None

    def test_invalid_json(self):
        result = _parse_response("I don't know what this disc is")
        assert result is None

    def test_missing_year(self):
        result = _parse_response('{"title": "Some Movie", "type": "movie"}')
        assert result == {"title": "Some Movie", "year": None, "type": "movie"}

    def test_year_as_string(self):
        result = _parse_response('{"title": "Test", "year": "2020", "type": "movie"}')
        assert result["year"] == 2020

    def test_non_dict_json(self):
        result = _parse_response('["not", "a", "dict"]')
        assert result is None

    def test_code_fence_without_json_label(self):
        text = '```\n{"title": "Test", "year": 2020, "type": "tv"}\n```'
        result = _parse_response(text)
        assert result == {"title": "Test", "year": 2020, "type": "tv"}


def _make_mock_client(response_json: dict):
    """Create a mock httpx.AsyncClient with the given response."""
    # Use MagicMock for response — httpx.Response.json() is sync, not async
    mock_response = MagicMock()
    mock_response.json.return_value = response_json
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


class TestIdentifyFromLabel:
    @pytest.mark.asyncio
    async def test_anthropic_success(self):
        mock_client = _make_mock_client(
            {"content": [{"text": '{"title": "Inception", "year": 2010, "type": "movie"}'}]}
        )
        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("INCEPTION_2010", "anthropic", "sk-ant-test")

        assert result is not None
        assert result["title"] == "Inception"
        assert result["year"] == 2010

    @pytest.mark.asyncio
    async def test_openai_success(self):
        mock_client = _make_mock_client(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"title": "Breaking Bad", "year": 2008, "type": "tv"}'
                        }
                    }
                ]
            }
        )
        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("BB_S1D1", "openai", "sk-test")

        assert result is not None
        assert result["title"] == "Breaking Bad"
        assert result["type"] == "tv"

    @pytest.mark.asyncio
    async def test_unknown_provider(self):
        result = await identify_from_label("TEST", "gemini", "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("TEST", "anthropic", "key")

        assert result is None

    @pytest.mark.asyncio
    async def test_ai_returns_null_title(self):
        mock_client = _make_mock_client(
            {"content": [{"text": '{"title": null, "year": null, "type": null}'}]}
        )
        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("RANDOM_GARBAGE", "anthropic", "key")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_content_list(self):
        mock_client = _make_mock_client({"content": []})
        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("TEST", "anthropic", "key")

        assert result is None

    @pytest.mark.asyncio
    async def test_openai_empty_choices(self):
        mock_client = _make_mock_client({"choices": []})
        with patch("app.core.ai_identifier.httpx.AsyncClient", return_value=mock_client):
            result = await identify_from_label("TEST", "openai", "key")

        assert result is None
