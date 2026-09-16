"""Tests for configurable naming conventions (#26) and extras policy (#25)."""

from app.core.organizer import (
    ALLOWED_MOVIE_PLACEHOLDERS,
    ALLOWED_TV_PLACEHOLDERS,
    format_episode_filename,
    format_movie_folder,
    format_season_folder,
    validate_naming_format,
)

# ---------------------------------------------------------------------------
# format_season_folder
# ---------------------------------------------------------------------------


class TestFormatSeasonFolder:
    def test_default_plex_format(self):
        assert format_season_folder("Season {season:02d}", 1) == "Season 01"
        assert format_season_folder("Season {season:02d}", 12) == "Season 12"

    def test_kodi_format(self):
        assert format_season_folder("Season {season:d}", 1) == "Season 1"
        assert format_season_folder("Season {season:d}", 12) == "Season 12"

    def test_minimal_format(self):
        assert format_season_folder("S{season:02d}", 3) == "S03"

    def test_invalid_format_falls_back(self):
        # Unknown placeholder falls back to default
        assert format_season_folder("{bad}", 5) == "Season 05"

    def test_sanitizes_output(self):
        # Colon removed from output
        assert ":" not in format_season_folder("Season:{season:02d}", 1)


# ---------------------------------------------------------------------------
# format_episode_filename
# ---------------------------------------------------------------------------


class TestFormatEpisodeFilename:
    def test_default_format(self):
        result = format_episode_filename(
            "{show} - S{season:02d}E{episode:02d}", "Breaking Bad", 1, 5
        )
        assert result == "Breaking Bad - S01E05"

    def test_custom_format(self):
        result = format_episode_filename("{show} {season:d}x{episode:02d}", "The Office", 3, 7)
        assert result == "The Office 3x07"

    def test_invalid_format_falls_back(self):
        result = format_episode_filename("{bad}", "Show", 1, 1)
        assert result == "Show - S01E01"


class TestCombinedEpisodeFilename:
    """One track holding several episodes is named with the Plex/Jellyfin range
    form, so both scanners resolve every episode in it instead of only the first
    (segment-format shows: three ~7min segments in one 22min DVD track)."""

    def test_contiguous_run_collapses_to_a_compact_range(self):
        result = format_episode_filename(
            "{show} - S{season:02d}E{episode:02d}",
            "Segment Show",
            1,
            1,
            extra_episodes=[2, 3],
        )
        assert result == "Segment Show - S01E01-E03"

    def test_gapped_set_uses_the_run_on_form(self):
        # "S01E01-E03" would claim E02, which this file does not contain — the
        # run-on form says "E01 and E03" and nothing else.
        result = format_episode_filename(
            "{show} - S{season:02d}E{episode:02d}", "Show", 1, 1, extra_episodes=[3]
        )
        assert result == "Show - S01E01E03"

    def test_no_extras_is_unchanged(self):
        assert (
            format_episode_filename(
                "{show} - S{season:02d}E{episode:02d}", "Show", 1, 5, extra_episodes=[]
            )
            == "Show - S01E05"
        )

    def test_range_is_spliced_before_a_trailing_segment(self):
        # A format with something AFTER the episode number must keep the range
        # attached to the number, or a scanner reads the file as one episode.
        result = format_episode_filename(
            "{show} - S{season:02d}E{episode:02d} [{year}]",
            "Show",
            1,
            1,
            year=1996,
            extra_episodes=[2],
        )
        assert result == "Show - S01E01-E02 [1996]"

    def test_custom_format_without_an_e_token_still_carries_the_range(self):
        # "3x07" has no E-token to splice into; the range must still be recorded
        # rather than dropped, even if the name reads oddly.
        result = format_episode_filename(
            "{show} {season:d}x{episode:02d}", "Show", 3, 7, extra_episodes=[8]
        )
        assert result.endswith("-E08")


# ---------------------------------------------------------------------------
# format_movie_folder
# ---------------------------------------------------------------------------


class TestFormatMovieFolder:
    def test_default_with_year(self):
        result = format_movie_folder("{title} ({year})", "Inception", 2010)
        assert result == "Inception (2010)"

    def test_default_without_year(self):
        result = format_movie_folder("{title} ({year})", "Inception", None)
        assert result == "Inception"

    def test_title_only_format(self):
        result = format_movie_folder("{title}", "Inception", 2010)
        assert result == "Inception"

    def test_invalid_format_falls_back(self):
        result = format_movie_folder("{bad}", "Inception", 2010)
        assert result == "Inception (2010)"


# ---------------------------------------------------------------------------
# validate_naming_format
# ---------------------------------------------------------------------------


class TestValidateNamingFormat:
    def test_valid_tv_format(self):
        assert (
            validate_naming_format("{show} - S{season:02d}E{episode:02d}", ALLOWED_TV_PLACEHOLDERS)
            is None
        )

    def test_valid_movie_format(self):
        assert validate_naming_format("{title} ({year})", ALLOWED_MOVIE_PLACEHOLDERS) is None

    def test_unknown_placeholder(self):
        error = validate_naming_format("{show} - {bad}", ALLOWED_TV_PLACEHOLDERS)
        assert error is not None
        assert "bad" in error

    def test_path_traversal_rejected(self):
        error = validate_naming_format("../{show}", ALLOWED_TV_PLACEHOLDERS)
        assert error is not None
        assert "traversal" in error.lower()

    def test_absolute_path_rejected(self):
        error = validate_naming_format("/root/{title}", ALLOWED_MOVIE_PLACEHOLDERS)
        assert error is not None

    def test_plain_text_valid(self):
        assert validate_naming_format("Season", set()) is None

    def test_invalid_format_syntax(self):
        error = validate_naming_format("{unclosed", ALLOWED_TV_PLACEHOLDERS)
        assert error is not None
