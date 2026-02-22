"""Unit tests for TMDB classification signal."""

from unittest.mock import MagicMock, patch

import requests

from app.core.tmdb_classifier import TmdbSignal, classify_from_tmdb
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
