"""Test disc classification using real disc metadata snapshots.

Feeds frozen metadata from 4 real disc rips through the actual DiscAnalyst.analyze()
to verify correct TV/Movie classification, label parsing, and review detection.
"""

import pytest

from app.core.analyst import DiscAnalyst
from app.models.disc_job import ContentType

from tests.pipeline.conftest import load_snapshot, snapshot_to_titles, snapshot_to_tmdb_signal


@pytest.mark.pipeline
class TestArrestedDevelopmentClassification:
    """ARRESTED_Development_S1D1: TV with 8 episodes, non-sequential indices."""

    def test_detects_tv_content_type(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.content_type == ContentType.TV

    def test_high_confidence(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.confidence >= 0.7

    def test_parses_season_from_label(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.detected_season == 1

    def test_detected_name(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.detected_name is not None
        assert "arrested" in result.detected_name.lower()

    def test_does_not_need_review(self, analyst):
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.needs_review is False

    def test_works_without_tmdb_signal(self, analyst):
        """Label S1D1 pattern + episode cluster should classify without TMDB."""
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=None)
        assert result.content_type == ContentType.TV
        assert result.detected_season == 1


@pytest.mark.pipeline
class TestStarTrekPicardClassification:
    """STAR TREK PICARD S1D3: TV with episodes, Play All, and extras."""

    def test_detects_tv_content_type(self, analyst):
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.content_type == ContentType.TV

    def test_parses_season_and_disc(self, analyst):
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.detected_season == 1

    def test_detects_play_all(self, analyst):
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert 3 in result.play_all_title_indices

    def test_does_not_need_review(self, analyst):
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)
        assert result.needs_review is False

    def test_works_without_tmdb_signal(self, analyst):
        """Label 'STAR TREK PICARD S1D3' has S1D3 pattern — should classify as TV."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=None)
        assert result.content_type == ContentType.TV
        assert result.detected_season == 1


@pytest.mark.pipeline
class TestTerminatorClassification:
    """THE TERMINATOR: Movie with 2 identical-duration features (ambiguous)."""

    def test_detects_movie_content_type(self, analyst):
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.content_type == ContentType.MOVIE

    def test_ambiguous_needs_review(self, analyst):
        """Two feature-length titles at 107min each should trigger ambiguity review."""
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.needs_review is True

    def test_review_reason_mentions_multiple(self, analyst):
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.review_reason is not None
        assert "multiple" in result.review_reason.lower() or "long" in result.review_reason.lower()

    def test_detected_name(self, analyst):
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.detected_name is not None
        assert "terminator" in result.detected_name.lower()


@pytest.mark.pipeline
class TestLogicalVolumeIdClassification:
    """LOGICAL_VOLUME_ID: Generic label that can't be parsed."""

    def test_generic_label_returns_no_name(self):
        """_parse_volume_label should return (None, None, None) for generic labels."""
        name, season, disc = DiscAnalyst._parse_volume_label("LOGICAL_VOLUME_ID")
        assert name is None
        assert season is None
        assert disc is None

    def test_detects_movie_content_type(self, analyst):
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.content_type == ContentType.MOVIE

    def test_detected_name_is_none(self, analyst):
        """Generic label -> detected_name should be None, triggering name prompt."""
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.detected_name is None

    def test_single_feature_confidence(self, analyst):
        """Single feature at 110min should give reasonable confidence."""
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.confidence >= 0.5
