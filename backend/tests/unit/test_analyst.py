"""Unit tests for the DiscAnalyst — disc classification engine.

Tests TV vs Movie detection heuristics with various title patterns.
"""

from app.core.analyst import DiscAnalyst, TitleInfo
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType


def _make_titles(durations_min: list[int], **kwargs) -> list[TitleInfo]:
    """Helper: create TitleInfo list from a list of durations in minutes."""
    return [
        TitleInfo(
            index=i,
            duration_seconds=d * 60,
            size_bytes=1024 * 1024 * 500,
            chapter_count=10,
            **kwargs,
        )
        for i, d in enumerate(durations_min)
    ]


def _default_config() -> AppConfig:
    """Config with default analyst thresholds."""
    return AppConfig(
        analyst_movie_min_duration=4800,  # 80 min
        analyst_tv_duration_variance=120,  # ±2 min
        analyst_tv_min_cluster_size=3,
        analyst_tv_min_duration=1080,  # 18 min
        analyst_tv_max_duration=4200,  # 70 min
        analyst_movie_dominance_threshold=0.6,
    )


class TestTVDetection:
    """Test TV show classification."""

    def test_classify_tv_uniform_durations(self):
        """8 titles at ~22 min each → should detect as TV."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 22, 23, 22, 21, 23, 22, 22])
        result = analyst.analyze(titles, volume_label="ARRESTED_DEVELOPMENT_S1D1")

        assert result.content_type == ContentType.TV
        assert result.confidence >= 0.7
        assert result.needs_review is False

    def test_classify_tv_45min_episodes(self):
        """4 titles at ~45 min each → TV (drama episodes)."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([44, 45, 46, 44])
        result = analyst.analyze(titles, volume_label="BREAKING_BAD_S1D1")

        assert result.content_type == ContentType.TV
        assert result.confidence >= 0.7

    def test_tv_requires_minimum_cluster(self):
        """Only 2 similar-duration titles → not enough for TV detection."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 23])
        result = analyst.analyze(titles, volume_label="SOME_SHOW_S1D1")

        # With volume label containing S1, it might still be detected as TV
        # via the label fallback, but with only moderate confidence
        assert result.content_type == ContentType.TV  # label fallback
        assert result.confidence <= 0.8

    def test_volume_label_season_detection(self):
        """Volume label with S1D2 pattern → parsed correctly."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 22, 22, 22])
        result = analyst.analyze(titles, volume_label="THE_OFFICE_S1D2")

        assert result.content_type == ContentType.TV
        assert result.detected_name is not None
        assert result.detected_season == 1


class TestMovieDetection:
    """Test movie classification."""

    def test_classify_movie_single_long(self):
        """1 title at 2h20m → Movie (high confidence)."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140])
        result = analyst.analyze(titles, volume_label="INCEPTION_2010")

        assert result.content_type == ContentType.MOVIE
        assert result.confidence >= 0.75

    def test_classify_movie_with_extras(self):
        """1 long title + short bonus clips → Movie."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([120, 5, 8, 3, 12])
        result = analyst.analyze(titles, volume_label="THE_MATRIX")

        assert result.content_type == ContentType.MOVIE
        assert result.confidence >= 0.75

    def test_classify_movie_multiple_versions(self):
        """2 long titles (theatrical + extended) → ambiguous Movie (needs review)."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([120, 135])
        result = analyst.analyze(titles, volume_label="BLADE_RUNNER")

        assert result.content_type == ContentType.MOVIE
        assert result.needs_review is True

    def test_classify_movie_many_long_titles(self):
        """4+ long titles → ambiguous (multi-movie disc)."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([90, 95, 100, 88])
        result = analyst.analyze(titles, volume_label="COLLECTION_DISC")

        assert result.content_type == ContentType.MOVIE
        assert result.needs_review is True


class TestAmbiguousClassification:
    """Test ambiguous/unknown classification cases."""

    def test_classify_ambiguous_mixed_durations(self):
        """Wildly different durations with no clear pattern → review needed."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([5, 15, 75, 10, 30])
        result = analyst.analyze(titles, volume_label="MYSTERY_DISC")

        assert result.needs_review is True

    def test_classify_empty_disc(self):
        """No titles → UNKNOWN, needs review."""
        analyst = DiscAnalyst(config=_default_config())
        result = analyst.analyze([], volume_label="BLANK_DISC")

        assert result.content_type == ContentType.UNKNOWN
        assert result.needs_review is True
        assert "No titles" in result.review_reason


class TestVolumeLabelParsing:
    """Test volume label parsing logic."""

    def test_parse_season_disc_combined(self):
        """S01D02 pattern should extract season and disc."""
        analyst = DiscAnalyst(config=_default_config())
        name, season, disc = analyst._parse_volume_label("THE_OFFICE_S01D02")
        assert season == 1
        assert disc == 2
        assert name is not None

    def test_parse_season_only(self):
        """SEASON_2 pattern should extract season."""
        analyst = DiscAnalyst(config=_default_config())
        name, season, disc = analyst._parse_volume_label("BREAKING_BAD_SEASON_2")
        assert season == 2

    def test_parse_no_season(self):
        """Plain movie label → no season."""
        analyst = DiscAnalyst(config=_default_config())
        name, season, disc = analyst._parse_volume_label("INCEPTION_2010")
        assert season is None

    def test_parse_empty_label(self):
        """Empty label → all None."""
        analyst = DiscAnalyst(config=_default_config())
        name, season, disc = analyst._parse_volume_label("")
        assert name is None
        assert season is None
        assert disc is None
