"""Unit tests for the nightly publish shrink guard.

The guard is pure arithmetic over two manifests, so these tests never touch the
network, the real ~/.engram/cache, or the live release.
"""

import pytest


def _manifest(shows: dict[str, dict]) -> dict:
    return {
        "cache_format_version": "3",
        "content_version": "2026-09-08",
        "shows": shows,
    }


def _show(name: str, counts: dict[str, int]) -> dict:
    return {
        "tmdb_id": 1,
        "name": name,
        "seasons": [int(s) for s in counts],
        "episode_counts": counts,
    }


class TestManifestTotals:
    def test_counts_shows_and_episodes(self, pg):
        m = _manifest({"1": _show("A", {"1": 10, "2": 12}), "2": _show("B", {"1": 5})})
        assert pg.manifest_totals(m) == (2, 27)

    def test_empty_manifest_is_zero(self, pg):
        assert pg.manifest_totals(_manifest({})) == (0, 0)

    def test_missing_shows_key_is_zero(self, pg):
        assert pg.manifest_totals({"cache_format_version": "3"}) == (0, 0)


class TestVerdictForGrowth:
    def test_growth_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(600, 37000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.GROWTH

    def test_identical_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(467, 36742), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True


class TestVerdictForShrink:
    def test_shrink_within_tolerance_is_allowed(self, pg):
        # 36,742 * 0.98 = 36,007.16, so 36,100 is inside tolerance.
        v = pg.verdict_for(candidate=(467, 36100), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.WITHIN_TOLERANCE

    def test_shrink_beyond_tolerance_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(467, 30000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EPISODES_SHRANK
        assert "30000" in v.reason and "36742" in v.reason

    def test_show_count_drop_is_blocked_even_when_episodes_hold(self, pg):
        # A resolution regression can collapse many shows into few while the
        # episode total barely moves. Shows are guarded independently.
        v = pg.verdict_for(candidate=(300, 36700), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.SHOWS_SHRANK


class TestVerdictForNoBaseline:
    def test_no_published_baseline_is_allowed(self, pg):
        # First publish ever, or a release with no manifest asset yet.
        v = pg.verdict_for(candidate=(467, 36742), published=None, tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.NO_BASELINE

    def test_empty_candidate_is_always_blocked(self, pg):
        v = pg.verdict_for(candidate=(0, 0), published=None, tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EMPTY_CANDIDATE


class TestTolerance:
    @pytest.mark.parametrize("tolerance", [-0.01, 1.01])
    def test_out_of_range_tolerance_rejected(self, pg, tolerance):
        with pytest.raises(ValueError):
            pg.verdict_for(candidate=(1, 1), published=(1, 1), tolerance=tolerance)
