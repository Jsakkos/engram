"""Unit tests for the DiscAnalyst — disc classification engine.

Tests TV vs Movie detection heuristics with various title patterns,
including TMDB signal integration.
"""

from app.core.analyst import DiscAnalyst, TitleInfo
from app.core.tmdb_classifier import TmdbSignal
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

    def test_parse_volume_label_is_static(self):
        """_parse_volume_label should work as a static method."""
        name, season, disc = DiscAnalyst._parse_volume_label("THE_OFFICE_S01D02")
        assert season == 1
        assert disc == 2
        assert name is not None


class TestTmdbSignalIntegration:
    """Test Analyst behavior with TMDB signal."""

    def test_tmdb_confirms_tv_boosts_confidence(self):
        """TMDB TV signal + heuristic TV -> boosted confidence."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 22, 23, 22, 21, 23, 22, 22])
        signal = TmdbSignal(
            content_type=ContentType.TV, confidence=0.85, tmdb_id=123, tmdb_name="Test Show"
        )
        result = analyst.analyze(titles, "ARRESTED_DEVELOPMENT_S1D1", tmdb_signal=signal)

        assert result.content_type == ContentType.TV
        assert result.confidence >= 0.85
        assert result.classification_source == "tmdb+heuristic"
        assert result.tmdb_id == 123

    def test_tmdb_confirms_movie_boosts_confidence(self):
        """TMDB MOVIE signal + heuristic MOVIE -> boosted confidence."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140])
        signal = TmdbSignal(
            content_type=ContentType.MOVIE, confidence=0.85, tmdb_id=27205, tmdb_name="Inception"
        )
        result = analyst.analyze(titles, "INCEPTION_2010", tmdb_signal=signal)

        assert result.content_type == ContentType.MOVIE
        assert result.confidence >= 0.85
        assert result.classification_source == "tmdb+heuristic"
        assert result.detected_name == "Inception"

    def test_tmdb_overrides_to_tv_picard_case(self):
        """TMDB says TV for disc with varying episode lengths (Star Trek Picard case)."""
        analyst = DiscAnalyst(config=_default_config())
        # 12 titles with varying durations — heuristic can't cluster them
        # Durations vary by more than ±2 min, so no cluster of 3+ forms
        titles = _make_titles([48, 44, 55, 50, 3, 5, 2, 7, 4, 3, 5, 2])
        signal = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.85,
            tmdb_id=85949,
            tmdb_name="Star Trek: Picard",
        )
        result = analyst.analyze(titles, "STAR_TREK_PICARD_S1D3", tmdb_signal=signal)

        assert result.content_type == ContentType.TV
        assert result.tmdb_name == "Star Trek: Picard"
        assert result.tmdb_id == 85949

    def test_no_tmdb_signal_uses_heuristics_only(self):
        """No TMDB signal -> behavior identical to current code."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140])
        result = analyst.analyze(titles, "INCEPTION_2010", tmdb_signal=None)

        assert result.content_type == ContentType.MOVIE
        assert result.classification_source == "heuristic"
        assert result.tmdb_id is None

    def test_tmdb_resolves_unknown_to_movie(self):
        """Heuristic gives UNKNOWN, TMDB says movie -> movie."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([5, 15, 75, 10, 30])  # Ambiguous durations
        signal = TmdbSignal(
            content_type=ContentType.MOVIE,
            confidence=0.85,
            tmdb_id=27205,
            tmdb_name="Inception",
        )
        result = analyst.analyze(titles, "MYSTERY_DISC", tmdb_signal=signal)

        assert result.content_type == ContentType.MOVIE
        assert result.classification_source == "tmdb"
        assert result.needs_review is False

    def test_tmdb_resolves_unknown_to_tv(self):
        """Heuristic gives UNKNOWN, TMDB says TV -> TV."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([5, 15, 75, 10, 30])  # Ambiguous durations
        signal = TmdbSignal(
            content_type=ContentType.TV, confidence=0.85, tmdb_id=456, tmdb_name="Some Show"
        )
        result = analyst.analyze(titles, "MYSTERY_DISC", tmdb_signal=signal)

        assert result.content_type == ContentType.TV
        assert result.classification_source == "tmdb"

    def test_tmdb_low_confidence_override_triggers_review(self):
        """TMDB contradicts strong heuristic with low confidence -> needs_review."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140])  # Clear movie at 0.9 confidence
        # Low-confidence TMDB TV signal: 0.55 * 0.8 = 0.44 < 0.5
        signal = TmdbSignal(content_type=ContentType.TV, confidence=0.55)
        result = analyst.analyze(titles, "SOME_DISC", tmdb_signal=signal)

        assert result.needs_review is True
        assert "TMDB suggests" in result.review_reason

    def test_tmdb_uses_canonical_name(self):
        """TMDB name should be used in result when available."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 22, 23, 22])
        signal = TmdbSignal(
            content_type=ContentType.TV,
            confidence=0.85,
            tmdb_id=123,
            tmdb_name="The Office (US)",
        )
        result = analyst.analyze(titles, "THE_OFFICE_S1D2", tmdb_signal=signal)

        assert result.detected_name == "The Office (US)"

    def test_tmdb_unknown_signal_ignored(self):
        """TmdbSignal with UNKNOWN content type is treated as no signal."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140])
        signal = TmdbSignal(content_type=ContentType.UNKNOWN, confidence=0.0)
        result = analyst.analyze(titles, "INCEPTION_2010", tmdb_signal=signal)

        assert result.content_type == ContentType.MOVIE
        assert result.classification_source == "heuristic"


class TestPlayAllDetection:
    """Test Play All title detection and filtering."""

    def test_tv_with_play_all(self):
        """TV disc with Play All: episodes ~22min × 4 = ~88min Play All."""
        analyst = DiscAnalyst(config=_default_config())
        # 4 episodes at 22min + 1 Play All at 88min (= 4×22)
        titles = _make_titles([22, 22, 23, 22, 88])
        result = analyst.analyze(titles, "THE_OFFICE_S1D1")

        assert result.content_type == ContentType.TV
        assert 4 in result.play_all_title_indices  # Title index 4 is the 88min Play All

    def test_tv_with_play_all_picard_like(self):
        """Picard-like disc: varying episodes, long Play All."""
        analyst = DiscAnalyst(config=_default_config())
        # 3 episodes (48+44+55 = 147min), Play All at 156min (~147 + padding),
        # plus short extras
        titles = _make_titles([48, 44, 55, 156, 5, 7])
        result = analyst.analyze(titles, "STAR_TREK_PICARD_S1D3")

        assert result.content_type == ContentType.TV
        assert 3 in result.play_all_title_indices  # Title 3 (156min) is Play All

    def test_tv_without_play_all(self):
        """TV disc without Play All: just episodes."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([22, 22, 23, 22])
        result = analyst.analyze(titles, "THE_OFFICE_S1D2")

        assert result.content_type == ContentType.TV
        assert result.play_all_title_indices == []

    def test_movie_no_play_all(self):
        """Movie disc: play_all_title_indices should be empty."""
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([140, 5, 8])
        result = analyst.analyze(titles, "INCEPTION_2010")

        assert result.content_type == ContentType.MOVIE
        assert result.play_all_title_indices == []

    def test_tv_long_title_not_play_all(self):
        """Long title that doesn't match episode total shouldn't be flagged."""
        analyst = DiscAnalyst(config=_default_config())
        # 4 episodes at 22min = 88min total, but long title is 120min (not close to 88)
        titles = _make_titles([22, 22, 22, 22, 120])
        result = analyst.analyze(titles, "SOME_SHOW_S1D1")

        assert result.content_type == ContentType.TV
        assert 4 not in result.play_all_title_indices  # 120min ≠ ~88min

    def test_play_all_with_conflict_resolution(self):
        """Movie+TV conflict (Play All detected as movie): Play All should be flagged."""
        analyst = DiscAnalyst(config=_default_config())
        # 8 episodes at 22min = 176min, Play All at 175min (detected as movie too)
        titles = _make_titles([22, 22, 23, 22, 21, 23, 22, 22, 175])
        result = analyst.analyze(titles, "ARRESTED_DEVELOPMENT_S1D1")

        assert result.content_type == ContentType.TV
        assert 8 in result.play_all_title_indices  # Title 8 (175min) is Play All

    def test_label_fallback_tv_detects_play_all(self):
        """Label-fallback TV path: Play All detected via fallback method."""
        analyst = DiscAnalyst(config=_default_config())
        # 2 episodes (not enough for cluster) + Play All
        # Volume label has season → label fallback TV
        # Episode total: 45+46 = 91min, Play All at 90min
        titles = _make_titles([45, 46, 90])
        result = analyst.analyze(titles, "SOME_SHOW_S1D1")

        assert result.content_type == ContentType.TV
        assert 2 in result.play_all_title_indices  # Title 2 (90min) is Play All
