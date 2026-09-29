"""Unit tests for scripts/curate_shows.py (subtitle-cache show-list curation).

Every test builds synthetic TMDB payloads and coverage rows. None calls TMDB
or reads ~/.engram; the `cur` fixture lives in conftest.py.
"""

import datetime

import pytest


def _details(
    tid,
    name="Show",
    *,
    lang="en",
    genres=(),
    networks=("ABC",),
    votes=1000,
    year="2005",
    origin=("US",),
):
    """A minimal TMDB /tv/{id} payload with only the fields curation reads."""
    return {
        "id": tid,
        "name": name,
        "original_language": lang,
        "genres": [{"id": g} for g in genres],
        "networks": [{"name": n} for n in networks],
        "vote_count": votes,
        "first_air_date": f"{year}-01-01",
        "origin_country": list(origin),
    }


def _ts(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.UTC).timestamp()


def _cov(cur, day, total, covered, season=1):
    return cur.CoverageRow(season, _ts(day), total, covered)


@pytest.mark.unit
class TestIsEnglish:
    def test_english_original_language_passes(self, cur):
        assert cur.is_english(_details(1, lang="en")) is True

    def test_spanish_show_with_us_origin_is_rejected(self, cur):
        # The Telemundo case: origin_country says US, the audio is Spanish.
        assert cur.is_english(_details(2, lang="es", origin=("US",))) is False

    def test_missing_language_is_rejected(self, cur):
        details = _details(3)
        del details["original_language"]
        assert cur.is_english(details) is False
