"""Test Play All detection with real disc metadata.

Play All tracks are concatenated versions of all episodes on a disc.
They should be detected and deselected from ripping.
"""

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo, _matches_expected_runtime
from app.core.tmdb_classifier import TmdbSignal
from app.models.disc_job import ContentType
from tests.pipeline.conftest import (
    _default_config,
    load_snapshot,
    snapshot_to_titles,
    snapshot_to_tmdb_signal,
)


@pytest.mark.pipeline
class TestPicardPlayAllDetection:
    """Star Trek Picard S1D3 has a clear Play All track (t03 at 157min)."""

    def test_play_all_flagged(self, analyst):
        """t03 (9416s) ≈ t00+t01+t02 (9416s) — should be flagged as Play All."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert 3 in result.play_all_title_indices

    def test_episodes_not_flagged_as_play_all(self, analyst):
        """Individual episode tracks should NOT be flagged as Play All."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        for i in [0, 1, 2]:
            assert i not in result.play_all_title_indices

    def test_extra_not_flagged_as_play_all(self, analyst):
        """Short extra track (t04 at 306s) should NOT be flagged as Play All."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert 4 not in result.play_all_title_indices

    def test_selected_tracks_exclude_play_all(self, analyst):
        """Simulating JobManager's deselection: Play All indices should be excluded."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)

        play_all_set = set(result.play_all_title_indices)
        selected = [t for t in titles if t.index not in play_all_set]
        selected_indices = [t.index for t in selected]

        assert 3 not in selected_indices
        assert sorted(selected_indices) == [0, 1, 2, 4]


@pytest.mark.pipeline
class TestArrestedDevNoPlayAll:
    """ARRESTED_Development_S1D1 has no Play All track."""

    def test_no_play_all_detected(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.play_all_title_indices == []


@pytest.mark.pipeline
class TestLogicalVolumeIdNoPlayAll:
    """LOGICAL_VOLUME_ID: t00 is the feature, not Play All (no episode cluster)."""

    def test_feature_not_flagged_as_play_all(self, analyst):
        """Without a TV episode cluster, nothing should be flagged as Play All."""
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.play_all_title_indices == []


@pytest.mark.pipeline
class TestPlayAllEdgeCases:
    """Synthetic edge cases for Play All detection boundary conditions."""

    def test_play_all_at_tolerance_boundary(self):
        """Play All duration at exactly 120% of episode sum should still be caught."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        # 3 episodes at 22min each = 66min total
        # Play All at 79.2min = 120% of 66min (right at boundary)
        titles = [
            TitleInfo(index=0, duration_seconds=1320, size_bytes=500_000_000, chapter_count=5),
            TitleInfo(index=1, duration_seconds=1320, size_bytes=500_000_000, chapter_count=5),
            TitleInfo(index=2, duration_seconds=1320, size_bytes=500_000_000, chapter_count=5),
            TitleInfo(
                index=3,
                duration_seconds=int(3960 * 1.20),
                size_bytes=1_500_000_000,
                chapter_count=15,
            ),
        ]
        result = analyst.analyze(titles, "TEST_S1D1")
        assert result.content_type == ContentType.TV
        # t03 at 4752s (79.2min) is within the 80-min movie threshold
        # but should still be detected as Play All since it matches episode sum

    def test_no_false_positive_on_short_disc(self):
        """3 episodes without any long track should not flag Play All."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        titles = [
            TitleInfo(index=0, duration_seconds=1320, size_bytes=500_000_000, chapter_count=5),
            TitleInfo(index=1, duration_seconds=1380, size_bytes=520_000_000, chapter_count=5),
            TitleInfo(index=2, duration_seconds=1290, size_bytes=480_000_000, chapter_count=5),
        ]
        result = analyst.analyze(titles, "SHOW_S1D1")
        assert result.content_type == ContentType.TV
        assert result.play_all_title_indices == []


# ---------------------------------------------------------------------------
# Fix 2: runtime-aware Play-All (double-length pilot is NOT a Play-All)
# ---------------------------------------------------------------------------


def test_matches_expected_runtime_single_and_two_parter():
    # 90.5-min title matches a single 90-min expected episode (pilot)
    assert _matches_expected_runtime(5429, [90, 45, 45]) is True
    # Same title matches sum of two consecutive 45-min episodes (two-parter)
    assert _matches_expected_runtime(5429, [45, 45, 45]) is True
    # A real 157-min Play-All matches no single/two-parter runtime
    assert _matches_expected_runtime(9416, [45, 45, 45]) is False
    # Empty / zero runtimes -> no match (caller falls back to heuristic)
    assert _matches_expected_runtime(5429, []) is False
    assert _matches_expected_runtime(5429, [0, 0]) is False


@pytest.mark.pipeline
class TestDS9PilotNotPlayAll:
    """DS9 S1D1: t0 is the 90-min 'Emissary' pilot, not a Play-All of t1+t2."""

    def _titles(self):
        return [
            TitleInfo(index=0, duration_seconds=5429, size_bytes=2_000_000_000, chapter_count=18),
            TitleInfo(index=1, duration_seconds=2718, size_bytes=1_000_000_000, chapter_count=8),
            TitleInfo(index=2, duration_seconds=2715, size_bytes=1_000_000_000, chapter_count=8),
        ]

    def test_pilot_flagged_without_runtimes_regression(self):
        """Without runtimes, the old behavior stands (t0 ~ sum -> flagged)."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        result = analyst.analyze(self._titles(), "DS9S1D1")
        assert 0 in result.play_all_title_indices

    def test_pilot_not_flagged_with_runtimes(self):
        """With expected runtimes [90,45,45,...], t0 is recognized as a real episode."""
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        signal = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.70,
            tmdb_id=580,
            tmdb_name="Star Trek: Deep Space Nine",
        )
        result = analyst.analyze(
            self._titles(),
            "DS9S1D1",
            tmdb_signal=signal,
            expected_episode_runtimes=[90, 45, 45, 45, 45],
        )
        assert 0 not in result.play_all_title_indices
        # A runtime-confirmed pilot must keep the disc classified as TV, not flip
        # it to MOVIE (only the single long title would otherwise look movie-like).
        assert result.content_type == ContentType.TV


@pytest.mark.pipeline
class TestDS9Job153Reproduction:
    """End-to-end analyst reproduction of the real DS9 S1D1 rip (Job 153)."""

    def test_ds9_resolves_correctly(self):
        config = _default_config()
        analyst = DiscAnalyst(config=config)
        titles = [
            TitleInfo(index=0, duration_seconds=5429, size_bytes=int(2e9), chapter_count=18),
            TitleInfo(index=1, duration_seconds=2718, size_bytes=int(1e9), chapter_count=8),
            TitleInfo(index=2, duration_seconds=2715, size_bytes=int(1e9), chapter_count=8),
        ]
        signal = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.70,
            tmdb_id=580,
            tmdb_name="Star Trek: Deep Space Nine",
        )
        result = analyst.analyze(
            titles,
            "DS9S1D1",
            tmdb_signal=signal,
            disc_title="DS9S1D1",
            expected_episode_runtimes=[90, 45, 45, 45, 45],
        )

        # Fix 1: abbreviation corroboration adopts the TMDB name
        assert result.detected_name == "Star Trek: Deep Space Nine"
        # Fix 2: the 90-min pilot is NOT dropped as a Play-All/extra
        assert 0 not in result.play_all_title_indices
        # Fix 2 corollary: disc stays TV (pilot is an episode, not a movie)
        assert result.content_type == ContentType.TV
        # Fix 3: identity is corroborated, so no spurious review
        assert result.needs_review is False
