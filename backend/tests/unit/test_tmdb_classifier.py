"""Unit tests for TMDB classification signal."""

from unittest.mock import MagicMock, patch

import requests

from app.core.tmdb_classifier import TmdbSignal, _name_similarity, classify_from_tmdb
from app.models.disc_job import ContentType


def _mock_response(status_code=200, json_data=None):
    """Create a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {"results": []}
    return mock


class TestClassifyFromTmdb:
    """Test the TMDB classification function."""

    @patch("app.core.tmdb_classifier.requests.get")
    def test_tv_match_returns_tv_signal(self, mock_get):
        """TMDB returns TV result only -> TmdbSignal with TV type."""
        tv_response = _mock_response(
            json_data={"results": [{"id": 85949, "name": "Star Trek: Picard", "popularity": 120.5}]}
        )
        movie_response = _mock_response(json_data={"results": []})
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb(
            "Star Trek Picard", "fake_api_key_that_is_long_enough_for_v4_auth"
        )
        assert result is not None
        assert result.content_type == ContentType.TV
        assert result.tmdb_id == 85949
        assert result.tmdb_name == "Star Trek: Picard"
        assert result.confidence == 0.85  # popularity > 50

    @patch("app.core.tmdb_classifier.requests.get")
    def test_movie_match_returns_movie_signal(self, mock_get):
        """TMDB returns movie result only -> TmdbSignal with MOVIE type."""
        tv_response = _mock_response(json_data={"results": []})
        movie_response = _mock_response(
            json_data={"results": [{"id": 27205, "title": "Inception", "popularity": 95.0}]}
        )
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb("Inception", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is not None
        assert result.content_type == ContentType.MOVIE
        assert result.tmdb_id == 27205
        assert result.tmdb_name == "Inception"
        assert result.confidence == 0.85

    @patch("app.core.tmdb_classifier.requests.get")
    def test_both_match_higher_popularity_wins(self, mock_get):
        """Both TV and movie match -> higher popularity wins."""
        tv_response = _mock_response(
            json_data={"results": [{"id": 100, "name": "Fargo", "popularity": 200.0}]}
        )
        movie_response = _mock_response(
            json_data={"results": [{"id": 200, "title": "Fargo", "popularity": 50.0}]}
        )
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb("Fargo", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is not None
        assert result.content_type == ContentType.TV
        assert result.tmdb_id == 100

    @patch("app.core.tmdb_classifier.requests.get")
    def test_both_match_close_popularity_ambiguous(self, mock_get):
        """Both TV and movie match with close popularity -> lower confidence."""
        tv_response = _mock_response(
            json_data={"results": [{"id": 100, "name": "Test Show", "popularity": 80.0}]}
        )
        movie_response = _mock_response(
            json_data={"results": [{"id": 200, "title": "Test Movie", "popularity": 60.0}]}
        )
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb("Test", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is not None
        assert result.confidence == 0.60  # Ambiguous (ratio < 2)

    @patch("app.core.tmdb_classifier.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        """Network timeout/error -> returns None gracefully."""
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

        result = classify_from_tmdb("Test", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is None

    @patch("app.core.tmdb_classifier.requests.get")
    def test_no_results_tries_variations(self, mock_get):
        """Empty TMDB results -> tries name variations."""
        # First two calls (TV + movie for original name) return nothing
        empty = _mock_response(json_data={"results": []})
        # Variation search finds a TV match
        tv_variation = _mock_response(
            json_data={"results": [{"id": 456, "name": "South Park", "popularity": 150.0}]}
        )
        movie_variation = _mock_response(json_data={"results": []})

        mock_get.side_effect = [empty, empty, tv_variation, movie_variation]

        result = classify_from_tmdb("Southpark", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is not None
        assert result.content_type == ContentType.TV

    def test_empty_name_returns_none(self):
        """Empty name -> returns None without API call."""
        result = classify_from_tmdb("", "fake_key")
        assert result is None

    def test_empty_api_key_returns_none(self):
        """Empty API key -> returns None without API call."""
        result = classify_from_tmdb("Test", "")
        assert result is None

    @patch("app.core.tmdb_classifier.requests.get")
    def test_low_popularity_tv_lower_confidence(self, mock_get):
        """TV match with low popularity -> confidence 0.70."""
        tv_response = _mock_response(
            json_data={"results": [{"id": 999, "name": "Obscure Show", "popularity": 10.0}]}
        )
        movie_response = _mock_response(json_data={"results": []})
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb("Obscure Show", "fake_api_key_that_is_long_enough_for_v4_auth")
        assert result is not None
        assert result.confidence == 0.70

    @patch("app.core.tmdb_classifier.requests.get")
    def test_v3_api_key_uses_query_param(self, mock_get):
        """Short API key (v3) -> uses api_key query param."""
        tv_response = _mock_response(
            json_data={"results": [{"id": 1, "name": "Test", "popularity": 100.0}]}
        )
        movie_response = _mock_response(json_data={"results": []})
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb("Test", "abc123def456")  # Short v3 key
        assert result is not None

        # Verify the first call used api_key param
        call_kwargs = mock_get.call_args_list[0]
        assert "api_key" in call_kwargs.kwargs.get("params", {})

    @patch("app.core.tmdb_classifier.requests.get")
    def test_both_match_name_similarity_wins_over_popularity(self, mock_get):
        """When both TV and movie match but one has a much closer name, prefer it.

        Regression test for The Grandmaster misclassification (#33).
        """
        tv_response = _mock_response(
            json_data={
                "results": [{"id": 500, "name": "The Grand Master Chef", "popularity": 200.0}]
            }
        )
        movie_response = _mock_response(
            json_data={"results": [{"id": 600, "title": "The Grandmaster", "popularity": 30.0}]}
        )
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb(
            "The Grandmaster", "fake_api_key_that_is_long_enough_for_v4_auth"
        )
        assert result is not None
        assert result.content_type == ContentType.MOVIE
        assert result.tmdb_id == 600

    @patch("app.core.tmdb_classifier.requests.get")
    def test_search_picks_best_name_match_from_results(self, mock_get):
        """_search_tmdb should prefer name-similar results over the first result."""
        movie_response = _mock_response(
            json_data={
                "results": [
                    {"id": 1, "title": "Grandmaster Flash", "popularity": 50.0},
                    {"id": 2, "title": "The Grandmaster", "popularity": 30.0},
                ]
            }
        )
        tv_response = _mock_response(json_data={"results": []})
        mock_get.side_effect = [tv_response, movie_response]

        result = classify_from_tmdb(
            "The Grandmaster", "fake_api_key_that_is_long_enough_for_v4_auth"
        )
        assert result is not None
        assert result.tmdb_id == 2  # Better name match, not first result


class TestTmdbSignal:
    """Test TmdbSignal dataclass."""

    def test_repr(self):
        signal = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.85,
            tmdb_id=123,
            tmdb_name="Test Show",
        )
        repr_str = repr(signal)
        assert "TV" in repr_str or "tv" in repr_str
        assert "123" in repr_str
        assert "Test Show" in repr_str


class TestNameSimilarity:
    """Test _name_similarity fuzzy matching."""

    def test_exact_match(self):
        assert _name_similarity("Thunderbirds", "Thunderbirds") == 1.0

    def test_prefix_match_singular_plural(self):
        """Thunderbird vs Thunderbirds should score high via prefix matching."""
        score = _name_similarity("Thunderbird", "Thunderbirds")
        assert score > 0.5

    def test_prefix_match_alien_aliens(self):
        score = _name_similarity("Alien", "Aliens")
        assert score > 0.5

    def test_multi_word_with_fuzzy_token(self):
        """'Star Trek Picard' vs 'Star Trek: Picard' — punctuation stripped, exact match."""
        score = _name_similarity("Star Trek Picard", "Star Trek: Picard")
        assert score > 0.8

    def test_completely_different(self):
        assert _name_similarity("Inception", "Interstellar") == 0.0

    def test_partial_overlap_multi_word(self):
        """'The Office' vs 'The Office US' — 2/3 exact match."""
        score = _name_similarity("The Office", "The Office US")
        assert 0.5 < score < 1.0

    def test_empty_string(self):
        assert _name_similarity("", "Thunderbirds") == 0.0
        assert _name_similarity("Thunderbirds", "") == 0.0

    def test_single_char_tokens_filtered(self):
        """Single-char words (len <= 1) are filtered out."""
        # "A" is filtered, so "A Walk" -> {"walk"} vs "Walk" -> {"walk"}
        assert _name_similarity("A Walk", "Walk") == 1.0
