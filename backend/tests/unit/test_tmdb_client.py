"""Unit tests for TMDB client with focus on variation generation bug fix."""

from unittest.mock import Mock, patch

import pytest
import requests

from app.matcher.tmdb_client import fetch_season_details, fetch_show_id
from tests.fixtures.tmdb_responses import (
    TMDB_SEARCH_ARRESTED_DEVELOPMENT,
    TMDB_SEARCH_BREAKING_BAD,
    TMDB_SEARCH_EMPTY,
    TMDB_SEARCH_OFFICE,
    TMDB_SEARCH_STAR_TREK,
    TMDB_SEASON_DETAILS_S01_3EP,
)


@pytest.mark.unit
class TestFetchShowIdVariations:
    """Tests specifically for the variation generation bug fix."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_clean_name_generates_multiple_variations(self, mock_get):
        """
        BUG FIX TEST: 'Arrested Development' should try multiple variations.
        Before fix: tried 1 variation
        After fix: tried 3+ variations
        """
        # Setup: Capture queries at call time (params dict gets mutated)
        captured_queries = []

        def capture_call(*args, **kwargs):
            if "params" in kwargs:
                # Make a copy of the query at call time
                captured_queries.append(kwargs["params"]["query"])
            return Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = capture_call

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Arrested Development")

        assert result is None
        # After fix, should try multiple variations (not just 1)
        assert len(captured_queries) >= 2, (
            f"Should try multiple variations for clean names, "
            f"got {len(captured_queries)}: {captured_queries}"
        )

        # Verify different queries were tried
        assert "Arrested Development" in captured_queries, (
            f"Original name should be tried, got: {captured_queries}"
        )
        assert len(set(captured_queries)) > 1, (
            f"Should try multiple unique variations, got: {set(captured_queries)}"
        )

    @patch("app.matcher.tmdb_client.requests.get")
    def test_the_prefix_variation_tried(self, mock_get):
        """Test that 'The' prefix is removed as variation."""
        # First call fails, second succeeds
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_OFFICE),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("The Office")

        assert result == "2316"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        assert "The Office" in queries
        assert "Office" in queries, "'The' prefix should be removed as variation"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_punctuation_colon_variations_tried(self, mock_get):
        """Test colon variations are tried (: -> space-dash, : -> empty)."""
        # First attempts fail, then colon variation succeeds
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_STAR_TREK),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Star Trek: TNG")

        assert result == "655"
        assert mock_get.call_count >= 2, "Should try colon variations"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_ampersand_to_and_variation(self, mock_get):
        """Test that & is converted to 'and' as variation."""
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(
                status_code=200,
                json=lambda: {"results": [{"id": 123, "name": "Law and Order"}]},
            ),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Law & Order")

        assert result == "123"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        # Should try converting & to "and"
        assert any("and" in q.lower() for q in queries)

    @patch("app.matcher.tmdb_client.requests.get")
    def test_common_word_removal_variations(self, mock_get):
        """Test that common words like 'Season', 'Complete', 'Series' are removed."""
        # Return empty for first few attempts, then success
        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            # Return success only for "Breaking Bad" (clean name)
            if query == "Breaking Bad":
                return Mock(status_code=200, json=lambda: TMDB_SEARCH_BREAKING_BAD)
            return Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = side_effect

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Breaking Bad Complete Series")

        assert result == "1396"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        # Should try variations
        assert "Breaking Bad Complete Series" in queries, "Should try original"
        # Should eventually try without both "Complete" and "Series"
        assert any(
            "complete" not in q.lower() and "series" not in q.lower() for q in queries
        ), f"Should remove common words as variation. Tried: {queries}"


@pytest.mark.unit
class TestFetchShowIdExactMatch:
    """Tests for exact match functionality."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_exact_match_returns_immediately(self, mock_get):
        """Test that exact match returns without trying variations."""
        mock_get.return_value = Mock(
            status_code=200, json=lambda: TMDB_SEARCH_ARRESTED_DEVELOPMENT
        )

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Arrested Development")

        assert result == "4589"
        assert (
            mock_get.call_count == 1
        ), "Should not try variations if exact match succeeds"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_returns_first_result_from_multiple_matches(self, mock_get):
        """Test that first result is returned when multiple matches exist."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"id": 111, "name": "Show A"},
                    {"id": 222, "name": "Show B"},
                ]
            },
        )

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show")

        assert result == "111", "Should return first result"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_no_api_key_returns_none(self, mock_get):
        """Test that missing API key returns None."""
        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = None
            result = fetch_show_id("Test Show")

            assert result is None
            assert mock_get.call_count == 0, "Should not make API call without key"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_api_error_returns_none(self, mock_get):
        """Test that API error returns None."""
        mock_get.return_value = Mock(status_code=500)

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show")

        assert result is None


@pytest.mark.unit
class TestFetchSeasonDetails:
    """Tests for season details fetching."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_success(self, mock_get):
        """Test successful season details fetch."""
        mock_get.return_value = Mock(
            status_code=200, json=lambda: TMDB_SEASON_DETAILS_S01_3EP
        )

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        assert result == 3, "Should return episode count"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "4589" in call_args[0][0]
        assert "season/1" in call_args[0][0]

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_no_api_key(self, mock_get):
        """Test that missing API key returns 0 (error condition)."""
        # When API key is missing, the request will fail and return 0
        mock_get.side_effect = requests.exceptions.RequestException("No API key")

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = None
            result = fetch_season_details("4589", 1)

            # Returns 0 on error (including missing API key)
            assert result == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_api_error(self, mock_get):
        """Test that API error returns 0."""
        mock_response = Mock(status_code=404)
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        # Returns 0 on API error
        assert result == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_empty_episodes(self, mock_get):
        """Test that season with no episodes returns 0."""
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {"episodes": []}
        )

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        assert result == 0


@pytest.mark.unit
class TestVariationEdgeCases:
    """Tests for edge cases in variation generation."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_empty_string_returns_none(self, mock_get):
        """Test that empty string returns None without API calls."""
        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("")

        assert result is None
        # May make one call with empty query, but should fail gracefully
        assert mock_get.call_count <= 1

    @patch("app.matcher.tmdb_client.requests.get")
    def test_show_name_with_year_removes_year(self, mock_get):
        """Test that year in parentheses is removed as variation."""
        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            if query == "Test Show":
                return Mock(
                    status_code=200,
                    json=lambda: {"results": [{"id": 999, "name": "Test Show"}]},
                )
            return Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = side_effect

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show (2020)")

        assert result == "999"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        assert "Test Show (2020)" in queries
        # Should try without year
        assert any("2020" not in q for q in queries)

    @patch("app.matcher.tmdb_client.requests.get")
    def test_show_name_with_underscores(self, mock_get):
        """Test that underscores are normalized to spaces."""
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_BREAKING_BAD),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Breaking_Bad")

        assert result == "1396"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        # Should try with spaces instead of underscores
        assert any(" " in q and "_" not in q for q in queries)
