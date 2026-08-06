"""Movie release-year resolution (issue #576).

Before this change, movie jobs called _resolve_show_year, which queries the TV
endpoint with a movie id. That either returned nothing or an unrelated show's
first-air year.
"""

from unittest.mock import patch

from app.core.tmdb_classifier import TmdbSignal, _make_movie_signal
from app.models import ContentType
from app.services.identification_coordinator import _resolve_movie_year, _resolve_year


class TestMovieSignalYear:
    def test_release_date_captured_from_search_result(self):
        signal = _make_movie_signal(
            {"id": 78, "title": "Blade Runner", "release_date": "1982-06-25", "popularity": 30.0}
        )
        assert signal.year == 1982

    def test_missing_release_date_is_none(self):
        signal = _make_movie_signal({"id": 78, "title": "Blade Runner", "popularity": 30.0})
        assert signal.year is None

    def test_malformed_release_date_is_none(self):
        signal = _make_movie_signal(
            {"id": 78, "title": "Blade Runner", "release_date": "", "popularity": 30.0}
        )
        assert signal.year is None


class TestResolveMovieYear:
    def test_signal_fast_path_avoids_network(self):
        signal = TmdbSignal(ContentType.MOVIE, 0.9, tmdb_id=78, tmdb_name="Blade Runner", year=1982)
        with patch("app.matcher.tmdb_client.fetch_movie_details") as fetch:
            assert _resolve_movie_year(78, signal) == 1982
            fetch.assert_not_called()

    def test_falls_back_to_movie_details(self):
        with patch(
            "app.matcher.tmdb_client.fetch_movie_details",
            return_value={"release_date": "1982-06-25"},
        ):
            assert _resolve_movie_year(78, None) == 1982

    def test_falsy_id_short_circuits(self):
        with patch("app.matcher.tmdb_client.fetch_movie_details") as fetch:
            assert _resolve_movie_year(None, None) is None
            fetch.assert_not_called()

    def test_unknown_year_is_none(self):
        with patch("app.matcher.tmdb_client.fetch_movie_details", return_value=None):
            assert _resolve_movie_year(78, None) is None


class TestResolveYearDispatch:
    def test_movie_never_hits_the_tv_endpoint(self):
        """Regression guard: a movie id must not be sent to /tv/{id}."""
        with (
            patch("app.matcher.tmdb_client.fetch_show_details") as show,
            patch(
                "app.matcher.tmdb_client.fetch_movie_details",
                return_value={"release_date": "1982-06-25"},
            ),
        ):
            assert _resolve_year(ContentType.MOVIE, 78, None) == 1982
            show.assert_not_called()

    def test_tv_still_uses_the_show_resolver(self):
        with patch(
            "app.matcher.tmdb_client.fetch_show_details",
            return_value={"first_air_date": "1993-09-16"},
        ):
            assert _resolve_year(ContentType.TV, 3452, None) == 1993
