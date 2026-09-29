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

    def test_exactly_the_minimum_sample_is_eligible(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 20, 3)]) is True

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


def _existing(tid, name="Show", discdb="0"):
    """A row as csv.DictReader yields it from today's curated_shows.csv."""
    return {
        "rank": "1",
        "tmdb_id": str(tid),
        "name": name,
        "year": "2005",
        "origin_country": "US",
        "networks": "ABC",
        "discdb_discs": discdb,
    }


def _ids(rows):
    return [int(r["tmdb_id"]) for r in rows]


@pytest.mark.unit
class TestBuildCuratedRows:
    def test_retained_rows_keep_order_and_discdb_count(self, cur):
        existing = [_existing(2, "B", discdb="33"), _existing(1, "A")]
        details = {1: _details(1, "A"), 2: _details(2, "B")}
        rows, report = cur.build_curated_rows(existing, [], [], details, {})
        assert _ids(rows) == [2, 1]
        assert rows[0]["discdb_discs"] == "33"
        assert rows[0]["tier"] == str(cur.TIER_RETAINED)
        assert report.retained == 2

    def test_non_english_retained_row_is_dropped_and_reported(self, cur):
        existing = [_existing(1), _existing(2, "El Chapo")]
        details = {1: _details(1), 2: _details(2, "El Chapo", lang="es")}
        rows, report = cur.build_curated_rows(existing, [], [], details, {})
        assert _ids(rows) == [1]
        assert report.dropped_language == [2]

    def test_outcome_excluded_retained_row_is_dropped_and_reported(self, cur):
        existing = [_existing(7842, "The Tom and Jerry Show")]
        details = {7842: _details(7842, "The Tom and Jerry Show")}
        coverage = {7842: [_cov(cur, "2026-05-01", 48, 7)]}
        rows, report = cur.build_curated_rows(existing, [], [], details, coverage)
        assert rows == []
        assert report.excluded_outcome == [7842]

    def test_retained_row_without_details_is_kept_verbatim(self, cur):
        # TMDB could not describe it: an infrastructure failure, not a fact.
        existing = [_existing(5, "Unreachable", discdb="4")]
        rows, report = cur.build_curated_rows(existing, [], [], {}, {})
        assert _ids(rows) == [5]
        assert rows[0]["name"] == "Unreachable"
        assert rows[0]["discdb_discs"] == "4"
        assert report.kept_unverified == [5]

    def test_published_english_show_missing_from_the_list_joins_tier_zero(self, cur):
        existing = [_existing(1)]
        details = {
            1: _details(1),
            30: _details(30, "Low votes", votes=10),
            31: _details(31, "High votes", votes=900),
        }
        rows, report = cur.build_curated_rows(existing, [1, 30, 31], [], details, {})
        assert _ids(rows) == [1, 31, 30]
        assert all(r["tier"] == str(cur.TIER_RETAINED) for r in rows)
        assert report.added_published == [31, 30]

    def test_published_non_english_show_is_not_added_or_reported_as_dropped(self, cur):
        details = {40: _details(40, "Anime", lang="ja")}
        rows, report = cur.build_curated_rows([], [40], [], details, {})
        assert rows == []
        assert report.dropped_language == []

    def test_new_candidates_order_by_tier_then_discovery_order(self, cur):
        details = {
            50: _details(50, "Kids", genres=(10762,)),
            51: _details(51, "Stream", networks=("Netflix",)),
            52: _details(52, "Drama A"),
            53: _details(53, "Drama B"),
        }
        rows, report = cur.build_curated_rows([], [], [50, 51, 52, 53], details, {})
        assert _ids(rows) == [52, 53, 51, 50]
        assert report.added_by_tier == {
            cur.TIER_BROADCAST: 2,
            cur.TIER_STREAMING_ONLY: 1,
            cur.TIER_LAST: 1,
        }

    def test_new_candidate_with_poor_healthy_coverage_is_excluded(self, cur):
        details = {60: _details(60)}
        coverage = {60: [_cov(cur, "2026-09-20", 40, 1)]}
        rows, report = cur.build_curated_rows([], [], [60], details, coverage)
        assert rows == []
        assert report.excluded_outcome == [60]

    def test_new_candidate_without_details_is_skipped_and_reported(self, cur):
        rows, report = cur.build_curated_rows([], [], [70], {}, {})
        assert rows == []
        assert report.skipped_no_details == [70]

    def test_a_show_appears_once_at_its_first_position(self, cur):
        existing = [_existing(1)]
        details = {1: _details(1), 2: _details(2)}
        rows, _ = cur.build_curated_rows(existing, [1, 2], [2, 1], details, {})
        assert _ids(rows) == [1, 2]
