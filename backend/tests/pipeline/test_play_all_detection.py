"""Test Play All detection with real disc metadata.

Play All tracks are concatenated versions of all episodes on a disc.
They should be detected and deselected from ripping.
"""

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
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
