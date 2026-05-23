"""Unit tests for the UPC product lookup service.

Covers the pure confidence-scoring helper and the async upcitemdb lookup,
with httpx stubbed via AsyncMock (mirrors tests/unit/test_ai_identifier.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.upc_lookup import (
    UPCLookupResult,
    compute_match_confidence,
    lookup_upc,
)


def _make_mock_client(*, json_data: dict | None = None, raise_exc: Exception | None = None):
    """Build a mock httpx.AsyncClient usable as an async context manager.

    httpx.Response.json() / raise_for_status() are sync, so the response is a
    MagicMock while the client itself is an AsyncMock.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = json_data or {}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if raise_exc is not None:
        mock_client.get = AsyncMock(side_effect=raise_exc)
    else:
        mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.unit
class TestComputeMatchConfidence:
    @pytest.mark.parametrize(
        "product, detected, expected",
        [
            # Direct substring matches -> high
            ("Inception (2010) Blu-ray", "Inception", "high"),
            ("Inception", "Inception (2010) Blu-ray", "high"),
            # Word overlap >= 0.5 (not a substring) -> high
            ("Breaking Bad Complete", "Breaking Bad Marathon", "high"),
            # 0 < ratio < 0.5 -> low
            ("alpha beta gamma delta", "alpha wun too three", "low"),
            # Zero overlap -> none
            ("foo bar", "baz qux", "none"),
        ],
    )
    def test_scoring(self, product, detected, expected):
        assert compute_match_confidence(product, detected) == expected

    @pytest.mark.parametrize(
        "product, detected",
        [
            (None, "Inception"),
            ("Inception", None),
            ("", "Inception"),
            ("Inception", ""),
            (None, None),
        ],
    )
    def test_missing_inputs_return_none(self, product, detected):
        assert compute_match_confidence(product, detected) == "none"

    def test_filler_only_words_return_none(self):
        # Both titles collapse to empty sets after filler removal, and neither
        # is a substring of the other.
        assert compute_match_confidence("The Season", "A Disc") == "none"

    def test_overlap_ratio_uses_smaller_set(self):
        # Not a substring; overlap {quest}=1, min(|{galaxy,quest,special}|,
        # |{quest,bonus}|)=2 -> ratio 0.5 -> high.
        assert compute_match_confidence("Galaxy Quest Special", "Quest Bonus") == "high"


@pytest.mark.unit
class TestLookupUpc:
    async def test_blank_upc_returns_error_without_request(self):
        result = await lookup_upc("   ")
        assert result.success is False
        assert result.error == "UPC code is required"

    async def test_successful_lookup_extracts_fields_and_dedupes_asins(self):
        json_data = {
            "items": [
                {
                    "title": "Inception",
                    "brand": "Warner",
                    "description": "A dream heist.",
                    "images": ["http://img/1.jpg"],
                    "asin": "B0TOP",
                    "offers": [
                        {"asin": "B0OFFER1"},
                        {"asin": "B0OFFER1"},  # duplicate, should collapse
                        {"asin": "B0OFFER2"},
                        {},  # no asin key
                    ],
                }
            ]
        }
        mock_client = _make_mock_client(json_data=json_data)
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("  012345678905  ")

        assert result.success is True
        assert result.product_title == "Inception"
        assert result.brand == "Warner"
        assert result.description == "A dream heist."
        assert result.images == ["http://img/1.jpg"]
        # Top-level asin is inserted first, offer asins follow, no duplicates.
        assert result.asins == ["B0TOP", "B0OFFER1", "B0OFFER2"]
        # UPC was stripped before the request.
        _, kwargs = mock_client.get.call_args
        assert kwargs["params"] == {"upc": "012345678905"}

    async def test_lookup_without_top_level_asin(self):
        json_data = {"items": [{"title": "Foo", "offers": [{"asin": "B0OFFER1"}]}]}
        mock_client = _make_mock_client(json_data=json_data)
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("012345678905")
        assert result.success is True
        assert result.asins == ["B0OFFER1"]

    async def test_no_items_returns_error(self):
        mock_client = _make_mock_client(json_data={"items": []})
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("000000000000")
        assert result.success is False
        assert "No product found" in result.error

    async def test_rate_limit_returns_friendly_message(self):
        response = httpx.Response(429, request=httpx.Request("GET", "http://x"))
        exc = httpx.HTTPStatusError("429", request=response.request, response=response)
        mock_client = _make_mock_client(raise_exc=exc)
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("012345678905")
        assert result.success is False
        assert "rate limit" in result.error.lower()

    async def test_other_http_status_returns_generic_message(self):
        response = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        exc = httpx.HTTPStatusError("500", request=response.request, response=response)
        mock_client = _make_mock_client(raise_exc=exc)
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("012345678905")
        assert result.success is False
        assert "HTTP 500" in result.error

    async def test_network_error_returns_message(self):
        exc = httpx.ConnectError("boom", request=httpx.Request("GET", "http://x"))
        mock_client = _make_mock_client(raise_exc=exc)
        with patch("app.core.upc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup_upc("012345678905")
        assert result.success is False
        assert "network error" in result.error.lower()

    def test_result_defaults(self):
        result = UPCLookupResult()
        assert result.success is False
        assert result.asins == []
        assert result.images == []
