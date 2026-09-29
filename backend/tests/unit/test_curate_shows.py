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


@pytest.mark.unit
class TestHealthyWindow:
    def test_before_the_poisoned_window_is_healthy(self, cur):
        assert cur.is_healthy(_ts("2026-06-10")) is True

    def test_start_of_the_poisoned_window_is_unhealthy(self, cur):
        assert cur.is_healthy(_ts("2026-06-11")) is False

    def test_partially_repaired_days_are_unhealthy(self, cur):
        # 2026-09-01..04: #631 had landed, #636 (scraper outage) had not.
        # Drake & Josh's 2/51 was written on these days.
        assert cur.is_healthy(_ts("2026-09-03")) is False

    def test_repaired_window_is_healthy(self, cur):
        assert cur.is_healthy(_ts("2026-09-05")) is True


@pytest.mark.unit
class TestOutcomeExcluded:
    def test_low_healthy_coverage_with_enough_sample_is_excluded(self, cur):
        # The Tom and Jerry Show: 7 of 48 healthy episodes.
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 48, 7)]) is True

    def test_thin_sample_is_kept(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 19, 0)]) is False

    def test_exactly_twenty_percent_is_kept(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 50, 10)]) is False

    def test_poisoned_window_zeros_are_ignored(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-06-12", 200, 0)]) is False

    def test_poisoned_zeros_do_not_dilute_a_healthy_measurement(self, cur):
        rows = [
            _cov(cur, "2026-05-01", 30, 29, season=1),
            _cov(cur, "2026-06-12", 200, 0, season=2),
        ]
        assert cur.outcome_excluded(rows) is False

    def test_low_coverage_measured_after_the_repair_is_excluded(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-09-20", 25, 2)]) is True

    def test_no_measurement_is_kept(self, cur):
        assert cur.outcome_excluded([]) is False


@pytest.mark.unit
class TestPriorityTier:
    def test_adult_animation_on_cable_is_not_demoted(self, cur):
        archer = _details(10283, "Archer", genres=(16, 35), networks=("FX", "FXX"))
        assert cur.priority_tier(archer) == cur.TIER_BROADCAST

    def test_rick_and_morty_is_not_demoted(self, cur):
        rm = _details(
            60625, "Rick and Morty", genres=(16, 35, 10765, 10759), networks=("Adult Swim",)
        )
        assert cur.priority_tier(rm) == cur.TIER_BROADCAST

    @pytest.mark.parametrize("genre", [10762, 10764, 10767, 10763])
    def test_kids_reality_talk_and_news_rank_last(self, cur, genre):
        assert cur.priority_tier(_details(1, genres=(genre,))) == cur.TIER_LAST

    def test_streaming_only_show_is_tier_two(self, cur):
        assert cur.priority_tier(_details(1, networks=("Netflix",))) == cur.TIER_STREAMING_ONLY

    def test_apple_tv_network_name_counts_as_streaming(self, cur):
        # TMDB names the network "Apple TV", not "Apple TV+".
        assert cur.priority_tier(_details(1, networks=("Apple TV",))) == cur.TIER_STREAMING_ONLY

    def test_streaming_plus_broadcast_is_not_demoted(self, cur):
        details = _details(1, networks=("Netflix", "ABC"))
        assert cur.priority_tier(details) == cur.TIER_BROADCAST

    def test_genre_outranks_network(self, cur):
        kids_on_netflix = _details(1, genres=(10762,), networks=("Netflix",))
        assert cur.priority_tier(kids_on_netflix) == cur.TIER_LAST

    def test_unknown_networks_are_not_demoted(self, cur):
        assert cur.priority_tier(_details(1, networks=())) == cur.TIER_BROADCAST
