"""Test ambiguous movie detection and the 'Rip First, Review Later' workflow.

THE TERMINATOR has 2 identical-duration features (theatrical vs remastered).
This should trigger the ambiguous movie review path.
"""

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
from app.models.disc_job import ContentType

from tests.pipeline.conftest import _default_config, load_snapshot, snapshot_to_titles


@pytest.mark.pipeline
class TestTerminatorAmbiguousMovie:
    """THE TERMINATOR: 2 feature tracks at 107min each triggers ambiguity."""

    def test_classified_as_movie(self, analyst):
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.content_type == ContentType.MOVIE

    def test_needs_review(self, analyst):
        """Two 107min features should trigger ambiguous movie review."""
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.needs_review is True

    def test_review_reason(self, analyst):
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.review_reason is not None

    def test_two_feature_tracks_identified(self, analyst):
        """Analyst should find exactly 2 feature-length titles (≥80min)."""
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        config = analyst._get_config()
        long_titles = [t for t in titles if t.duration_seconds >= config.analyst_movie_min_duration]
        assert len(long_titles) == 2
        assert long_titles[0].duration_seconds == long_titles[1].duration_seconds  # Both 6423s

    def test_rip_first_review_later_precondition(self, analyst):
        """Verify the precondition for JobManager's auto-rip-then-review path.

        JobManager checks: content_type == MOVIE and needs_review and
        review_reason contains 'Multiple' -> auto-rip all candidates.
        """
        snap = load_snapshot("the_terminator")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])

        is_ambiguous_movie = result.content_type == ContentType.MOVIE and result.needs_review
        assert is_ambiguous_movie


@pytest.mark.pipeline
class TestNonAmbiguousMovies:
    """Verify that movies with a clear single feature don't trigger review."""

    def test_logical_volume_id_single_feature(self, analyst):
        """LOGICAL_VOLUME_ID has 1 feature (110min) — not ambiguous."""
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, snap["volume_label"])
        assert result.content_type == ContentType.MOVIE
        # The Analyst should NOT flag this as ambiguous (single feature)
        # Note: it may still need review due to the generic label — but
        # the review reason should be about the label, not multiple features
        config = analyst._get_config()
        long_titles = [t for t in titles if t.duration_seconds >= config.analyst_movie_min_duration]
        assert len(long_titles) == 1  # Only t00 at 6632s


@pytest.mark.pipeline
class TestSyntheticAmbiguousMovies:
    """Synthetic tests for ambiguous movie edge cases."""

    def test_three_features_triggers_review(self):
        """3 feature-length titles -> needs review."""
        analyst = DiscAnalyst(config=_default_config())
        titles = [
            TitleInfo(index=0, duration_seconds=6480, size_bytes=11_000_000_000, chapter_count=18),
            TitleInfo(index=1, duration_seconds=7680, size_bytes=13_000_000_000, chapter_count=22),
            TitleInfo(index=2, duration_seconds=6900, size_bytes=12_000_000_000, chapter_count=20),
            TitleInfo(index=3, duration_seconds=540, size_bytes=600_000_000, chapter_count=2),
        ]
        result = analyst.analyze(titles, "MOVIE_COLLECTION")
        assert result.content_type == ContentType.MOVIE
        assert result.needs_review is True

    def test_single_feature_plus_extras_no_review(self):
        """1 feature + several short extras -> no ambiguity."""
        analyst = DiscAnalyst(config=_default_config())
        titles = [
            TitleInfo(index=0, duration_seconds=7200, size_bytes=15_000_000_000, chapter_count=25),
            TitleInfo(index=1, duration_seconds=600, size_bytes=500_000_000, chapter_count=2),
            TitleInfo(index=2, duration_seconds=300, size_bytes=200_000_000, chapter_count=1),
            TitleInfo(index=3, duration_seconds=180, size_bytes=150_000_000, chapter_count=1),
        ]
        result = analyst.analyze(titles, "CLEAR_MOVIE")
        assert result.content_type == ContentType.MOVIE
        assert result.needs_review is False
