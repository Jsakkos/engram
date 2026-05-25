"""Unit tests for select_movie_main_feature.

Distinguishes the real movie feature from long bonus tracks so that a disc only
goes to review when 2+ titles genuinely qualify as the feature (alternate
versions / obfuscation), not when it merely carries long extras.
"""

from app.core.analyst import TitleInfo, select_movie_main_feature


def _title(index: int, minutes: float, mbps: float, chapters: int = 12) -> TitleInfo:
    """Build a TitleInfo from a duration (minutes) and a bitrate (Mbps)."""
    duration = int(minutes * 60)
    size = int(mbps * 1_000_000 * duration / 8)
    return TitleInfo(
        index=index,
        duration_seconds=duration,
        size_bytes=size,
        chapter_count=chapters,
    )


class TestSingleFeature:
    def test_marty_supreme_shape_no_review(self):
        """149min feature + short bonus tracks → 1 feature, rest extras, no review."""
        titles = [
            _title(0, 149.7, 38, chapters=16),
            _title(1, 20.0, 19, chapters=1),
            _title(2, 4.1, 17, chapters=1),
            _title(3, 3.9, 18, chapters=2),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=149)
        assert decision.needs_review is False
        assert decision.feature_index == 0
        assert sorted(decision.extra_indices) == [1, 2, 3]

    def test_long_low_bitrate_bonus_is_extra(self):
        """100min feature + 90min low-bitrate making-of → bonus is an extra, no review."""
        titles = [
            _title(0, 100, 38),  # feature, high bitrate
            _title(1, 90, 4),  # long documentary, low bitrate
            _title(2, 5, 15),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=100)
        assert decision.needs_review is False
        assert decision.feature_index == 0
        assert 1 in decision.extra_indices

    def test_no_runtime_single_long_title_no_review(self):
        """Fallback (no TMDB runtime): one long title + short extras → no review."""
        titles = [
            _title(0, 130, 35),
            _title(1, 8, 20),
            _title(2, 3, 18),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=None)
        assert decision.needs_review is False
        assert decision.feature_index == 0

    def test_runtime_wildly_off_falls_back_to_longest(self):
        """Wrong/garbage runtime → fall back to longest eligible, no false review."""
        titles = [
            _title(0, 142, 36),
            _title(1, 6, 20),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=600)
        assert decision.needs_review is False
        assert decision.feature_index == 0


class TestAmbiguousFeatures:
    def test_theatrical_plus_extended_needs_review(self):
        """Two large near-runtime cuts → review with both as candidates."""
        titles = [
            _title(0, 120, 36),  # theatrical
            _title(1, 145, 35),  # extended
            _title(2, 6, 18),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=120)
        assert decision.needs_review is True
        assert sorted(decision.candidate_indices) == [0, 1]
        assert 2 in decision.extra_indices

    def test_obfuscation_identical_titles_needs_review(self):
        """Two identical-duration high-bitrate titles → review."""
        titles = [
            _title(0, 107, 30),
            _title(1, 107, 30),
            _title(2, 4, 18),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=107)
        assert decision.needs_review is True
        assert sorted(decision.candidate_indices) == [0, 1]

    def test_no_runtime_two_long_high_bitrate_titles_review(self):
        """Fallback with two long, similar, high-bitrate titles → review."""
        titles = [
            _title(0, 118, 34),
            _title(1, 122, 35),
            _title(2, 5, 18),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=None)
        assert decision.needs_review is True
        assert sorted(decision.candidate_indices) == [0, 1]


class TestEdgeCases:
    def test_empty_titles(self):
        decision = select_movie_main_feature([], runtime_minutes=120)
        assert decision.feature_index is None
        assert decision.needs_review is False

    def test_no_long_titles_picks_longest(self):
        """No title clears the feature floor → pick the longest, no review."""
        titles = [
            _title(0, 40, 20),
            _title(1, 55, 22),
            _title(2, 10, 18),
        ]
        decision = select_movie_main_feature(titles, runtime_minutes=None)
        assert decision.needs_review is False
        assert decision.feature_index == 1
