"""Unit tests for the canonical->output episode-ordering projection.

Pinned to a CAPTURED REAL TMDB response for Firefly (`tests/fixtures/
firefly_episode_groups.json`) — not a synthetic fixture — because the
season-derivation rule is the feature's highest-risk piece: Firefly's DVD
group[0] is named "Season 1" but carries TMDB ``order=1`` (its "Specials"
group is ``order=0``), so a naive ``season = group.order + 1`` would misfile
the whole show into Season 2. The real data is the only trustworthy guard.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import episode_ordering

_FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "firefly_episode_groups.json").read_text(
        encoding="utf-8"
    )
)
FIREFLY = "1437"
DVD_GROUP_ID = "6463b4890f3655011907aac2"  # type 3
INTENDED_GROUP_ID = "5acfb484c3a368738a004068"  # type 2 (absolute)


@pytest.fixture
def firefly_tmdb():
    """Patch the TMDB fetchers to serve the captured Firefly responses."""
    with (
        patch(
            "app.core.episode_ordering.tmdb_client.fetch_episode_groups",
            return_value=_FIXTURE["results"],
        ) as m_groups,
        patch(
            "app.core.episode_ordering.tmdb_client.fetch_episode_group",
            side_effect=lambda gid, _key: _FIXTURE["details"].get(gid),
        ) as m_group,
    ):
        yield m_groups, m_group


@pytest.mark.unit
class TestProjectEpisode:
    def test_dvd_remaps_serenity_to_episode_one(self, firefly_tmdb):
        # "Serenity" is the intended pilot; it aired LAST (canonical S1E11)
        # but DVD/intended order puts it first -> DVD S1E1.
        assert episode_ordering.project_episode(FIREFLY, "dvd", 1, 11, "k") == (1, 1)

    def test_dvd_remaps_the_train_job_to_episode_two(self, firefly_tmdb):
        # "The Train Job" aired first (canonical S1E1) -> DVD S1E2.
        assert episode_ordering.project_episode(FIREFLY, "dvd", 1, 1, "k") == (1, 2)

    def test_specials_map_to_season_zero(self, firefly_tmdb):
        # Canonical S0E4 lives in the DVD ordering's "Specials" group
        # (name -> season 0), at position order=0 -> S0E1.
        assert episode_ordering.project_episode(FIREFLY, "dvd", 0, 4, "k") == (0, 1)

    def test_aired_is_identity_without_fetching(self, firefly_tmdb):
        m_groups, m_group = firefly_tmdb
        assert episode_ordering.project_episode(FIREFLY, "aired", 1, 5, "k") == (1, 5)
        assert m_groups.call_count == 0
        assert m_group.call_count == 0

    def test_unknown_canonical_pair_falls_back_to_input(self, firefly_tmdb):
        # A pair not present in the group must not be invented.
        assert episode_ordering.project_episode(FIREFLY, "dvd", 9, 99, "k") == (9, 99)

    def test_ordering_with_no_group_falls_back(self, firefly_tmdb):
        # Firefly has no "digital" (type 4) group -> identity.
        assert episode_ordering.project_episode(FIREFLY, "digital", 1, 1, "k") == (1, 1)

    def test_missing_key_falls_back_without_fetching(self, firefly_tmdb):
        m_groups, _ = firefly_tmdb
        assert episode_ordering.project_episode(FIREFLY, "dvd", 1, 11, "") == (1, 11)
        assert m_groups.call_count == 0

    def test_missing_show_id_falls_back(self, firefly_tmdb):
        assert episode_ordering.project_episode(None, "dvd", 1, 11, "k") == (1, 11)

    def test_absolute_is_excluded_in_v1(self, firefly_tmdb):
        # Absolute is deferred; passing it must not renumber.
        assert "absolute" not in episode_ordering.ALLOWED_ORDERINGS
        assert episode_ordering.project_episode(FIREFLY, "absolute", 1, 11, "k") == (1, 11)

    def test_never_raises_on_malformed_group(self):
        with (
            patch(
                "app.core.episode_ordering.tmdb_client.fetch_episode_groups",
                return_value=[{"id": "x", "type": 3}],
            ),
            patch(
                "app.core.episode_ordering.tmdb_client.fetch_episode_group",
                return_value={"groups": [{"episodes": [{"garbage": True}]}]},
            ),
        ):
            assert episode_ordering.project_episode(FIREFLY, "dvd", 1, 1, "k") == (1, 1)


@pytest.mark.unit
class TestResolveEpisodeGroupId:
    def test_resolves_dvd_group(self, firefly_tmdb):
        assert episode_ordering.resolve_episode_group_id(FIREFLY, "dvd", "k") == DVD_GROUP_ID

    def test_aired_resolves_to_none(self, firefly_tmdb):
        assert episode_ordering.resolve_episode_group_id(FIREFLY, "aired", "k") is None

    def test_absent_type_resolves_to_none(self, firefly_tmdb):
        assert episode_ordering.resolve_episode_group_id(FIREFLY, "digital", "k") is None

    def test_tiebreak_prefers_more_complete_group(self):
        groups = [
            {"id": "small", "type": 3, "episode_count": 10, "group_count": 1},
            {"id": "big", "type": 3, "episode_count": 26, "group_count": 2},
        ]
        with patch(
            "app.core.episode_ordering.tmdb_client.fetch_episode_groups", return_value=groups
        ):
            assert episode_ordering.resolve_episode_group_id("99", "dvd", "k") == "big"

    def test_tiebreak_is_deterministic_on_equal_counts(self):
        groups = [
            {"id": "bbb", "type": 3, "episode_count": 26, "group_count": 2},
            {"id": "aaa", "type": 3, "episode_count": 26, "group_count": 2},
        ]
        with patch(
            "app.core.episode_ordering.tmdb_client.fetch_episode_groups", return_value=groups
        ):
            # equal counts -> lexicographically smallest id wins, stably
            assert episode_ordering.resolve_episode_group_id("99", "dvd", "k") == "aaa"


@pytest.mark.unit
class TestBuildOrderingOptions:
    """Powers the review-queue selector: which orderings exist, whether they
    diverge for the disc's matched episodes, and the per-episode projection."""

    def test_firefly_offers_aired_and_dvd(self, firefly_tmdb):
        opts = episode_ordering.build_ordering_options(
            FIREFLY, 1, roster_pairs=[(1, 1), (1, 11)], matched_pairs=[(1, 11)], api_key="k"
        )
        assert opts["available"] is True
        assert opts["diverges"] is True
        orderings = [o["ordering"] for o in opts["options"]]
        assert orderings[0] == "aired"  # aired always first
        assert "dvd" in orderings
        # absolute ("Intended Order", type 2) is excluded in v1
        assert "absolute" not in orderings

    def test_dvd_option_carries_projection_and_divergence(self, firefly_tmdb):
        opts = episode_ordering.build_ordering_options(
            FIREFLY, 1, roster_pairs=[(1, 1), (1, 11)], matched_pairs=[(1, 11)], api_key="k"
        )
        dvd = next(o for o in opts["options"] if o["ordering"] == "dvd")
        assert dvd["diverges"] is True
        # canonical S01E11 ("Serenity") projects to DVD S01E01
        assert dvd["projection"]["S01E11"] == "S01E01"
        # aired option is identity
        aired = opts["options"][0]
        assert aired["ordering"] == "aired" and aired["diverges"] is False

    def test_no_groups_offers_only_aired(self):
        with patch("app.core.episode_ordering.tmdb_client.fetch_episode_groups", return_value=[]):
            opts = episode_ordering.build_ordering_options(
                "999", 1, roster_pairs=[(1, 1)], matched_pairs=[(1, 1)], api_key="k"
            )
        assert opts["available"] is False
        assert opts["diverges"] is False
        assert [o["ordering"] for o in opts["options"]] == ["aired"]

    def test_only_aired_and_dvd_surfaced_even_when_other_groups_exist(self):
        # v1 scope is narrowed to aired + DVD: a show carrying digital/story-arc/
        # production/tv groups that diverge must still surface ONLY aired + DVD,
        # so the review-queue selector never shows more than two buttons.
        groups = [
            {"id": "g_dvd", "type": 3, "name": "DVD Order", "episode_count": 14, "group_count": 1},
            {"id": "g_dig", "type": 4, "name": "Digital", "episode_count": 14, "group_count": 1},
            {"id": "g_sto", "type": 5, "name": "Story Arc", "episode_count": 14, "group_count": 1},
            {"id": "g_pro", "type": 6, "name": "Production", "episode_count": 14, "group_count": 1},
            {"id": "g_tv", "type": 7, "name": "TV", "episode_count": 14, "group_count": 1},
        ]
        remap = {(1, 1): (1, 2), (1, 2): (1, 1)}  # every non-aired ordering diverges
        with (
            patch(
                "app.core.episode_ordering.tmdb_client.fetch_episode_groups", return_value=groups
            ),
            patch("app.core.episode_ordering.get_projection_map", return_value=remap),
        ):
            opts = episode_ordering.build_ordering_options(
                "999", 1, roster_pairs=[(1, 1), (1, 2)], matched_pairs=[(1, 1)], api_key="k"
            )
        orderings = [o["ordering"] for o in opts["options"]]
        assert orderings == ["aired", "dvd"]
        for deferred in ("digital", "story_arc", "production", "tv"):
            assert deferred not in orderings


@pytest.mark.unit
class TestAllowedOrderingsScope:
    """v1 selectable scope is narrowed to aired + DVD; everything else deferred."""

    def test_allowed_orderings_are_exactly_aired_and_dvd(self):
        assert episode_ordering.ALLOWED_ORDERINGS == frozenset({"aired", "dvd"})

    @pytest.mark.parametrize("deferred", ["digital", "story_arc", "production", "tv", "absolute"])
    def test_deferred_orderings_excluded(self, deferred):
        assert deferred not in episode_ordering.ALLOWED_ORDERINGS


@pytest.mark.unit
class TestComputeDivergence:
    def test_dvd_diverges_for_reordered_episode(self, firefly_tmdb):
        # canonical S1E11 -> DVD S1E1 is a remap, so divergence is True.
        assert episode_ordering.compute_divergence(FIREFLY, "dvd", [(1, 11), (1, 1)], "k") is True

    def test_aired_never_diverges(self, firefly_tmdb):
        assert episode_ordering.compute_divergence(FIREFLY, "aired", [(1, 11)], "k") is False

    def test_no_group_does_not_diverge(self, firefly_tmdb):
        assert episode_ordering.compute_divergence(FIREFLY, "digital", [(1, 1)], "k") is False
