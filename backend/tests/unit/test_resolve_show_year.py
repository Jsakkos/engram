"""Unit tests for identification year resolution."""

from types import SimpleNamespace
from unittest.mock import patch

from app.services.identification_coordinator import _resolve_show_year


def test_none_tmdb_id_returns_none():
    assert _resolve_show_year(None) is None


def test_zero_tmdb_id_short_circuits_without_network():
    # Falsy id (0) short-circuits — no pointless fetch_show_details(0) call.
    with patch("app.matcher.tmdb_client.fetch_show_details") as m:
        assert _resolve_show_year(0, None) is None
        m.assert_not_called()


def test_fast_path_reads_year_from_candidates():
    sig = SimpleNamespace(
        all_candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 50.0},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 30.0},
        ]
    )
    assert _resolve_show_year(3452, sig) == 1993
    assert _resolve_show_year(195241, sig) == 2023


def test_fallback_to_tmdb_details_when_no_candidates():
    with patch(
        "app.matcher.tmdb_client.fetch_show_details",
        return_value={"first_air_date": "1993-09-16"},
    ):
        assert _resolve_show_year(3452, None) == 1993


def test_returns_none_when_details_missing():
    with patch("app.matcher.tmdb_client.fetch_show_details", return_value=None):
        assert _resolve_show_year(3452, None) is None


def test_candidate_year_blank_falls_through_to_details():
    sig = SimpleNamespace(all_candidates=[{"tmdb_id": 3452, "year": ""}])
    with patch(
        "app.matcher.tmdb_client.fetch_show_details",
        return_value={"first_air_date": "1993-09-16"},
    ):
        assert _resolve_show_year(3452, sig) == 1993
