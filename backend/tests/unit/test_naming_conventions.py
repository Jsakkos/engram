"""Tests for configurable naming conventions (#26) and extras policy (#25)."""

import pytest

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


# ---------------------------------------------------------------------------
# {episode_title} placeholder (#826)
# ---------------------------------------------------------------------------

TITLED = "{show} - S{season:02d}E{episode:02d} - {episode_title}"


class TestEpisodeTitlePlaceholder:
    def test_title_is_rendered(self):
        assert (
            format_episode_filename(TITLED, "Show", 1, 1, episode_title="Pilot")
            == "Show - S01E01 - Pilot"
        )

    def test_placeholder_is_allowed(self):
        from app.core.organizer import ALLOWED_EPISODE_PLACEHOLDERS

        assert validate_naming_format(TITLED, ALLOWED_EPISODE_PLACEHOLDERS) is None

    def test_default_format_ignores_the_title(self):
        assert (
            format_episode_filename(
                "{show} - S{season:02d}E{episode:02d}", "Show", 1, 1, episode_title="Pilot"
            )
            == "Show - S01E01"
        )

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty_trailing_title_drops_its_separator(self, empty):
        assert format_episode_filename(TITLED, "Show", 1, 1, episode_title=empty) == "Show - S01E01"

    def test_empty_middle_title_drops_one_separator(self):
        fmt = "{show} - {episode_title} - S{season:02d}E{episode:02d}"
        assert format_episode_filename(fmt, "Show", 1, 1, episode_title=None) == "Show - S01E01"

    def test_empty_leading_title_drops_the_separator_after_it(self):
        fmt = "{episode_title} - {show} S{season:02d}E{episode:02d}"
        assert format_episode_filename(fmt, "Show", 1, 1, episode_title=None) == "Show S01E01"

    def test_empty_title_before_a_year_keeps_the_year_clean(self):
        fmt = "{show} - S{season:02d}E{episode:02d} - {episode_title} ({year})"
        out = format_episode_filename(fmt, "Show", 1, 1, year=1999, episode_title=None)
        assert out == "Show - S01E01 (1999)"

    @pytest.mark.parametrize(
        "fmt",
        [
            "{show} - S{season:02d}E{episode:02d} ({episode_title})",
            "{show} - S{season:02d}E{episode:02d} [{episode_title}]",
        ],
    )
    def test_empty_title_takes_its_brackets_with_it(self, fmt):
        assert format_episode_filename(fmt, "Show", 1, 1, episode_title=None) == "Show - S01E01"

    def test_dotted_style_separator_is_dropped(self):
        fmt = "{show}.S{season:02d}E{episode:02d}.{episode_title}"
        assert format_episode_filename(fmt, "Show", 1, 1, episode_title=None) == "Show.S01E01"

    def test_illegal_characters_are_removed(self):
        out = format_episode_filename(
            TITLED, "Show", 1, 1, episode_title='Who: What? <Now> "Here" a/b\\c|d*'
        )
        assert out == "Show - S01E01 - Who What Now Here abcd"

    def test_whitespace_left_by_removed_characters_is_collapsed(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, episode_title="Left / Right")
        assert out == "Show - S01E01 - Left Right"

    def test_trailing_dots_and_spaces_are_trimmed(self):
        out = format_episode_filename(TITLED + " [x]", "Show", 1, 1, episode_title="Wait... ")
        assert out == "Show - S01E01 - Wait [x]"

    def test_control_characters_never_reach_the_filename(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, episode_title="Bad\x00Ti\ntle")
        assert out == "Show - S01E01 - BadTitle"

    def test_a_title_that_sanitizes_to_nothing_counts_as_empty(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, episode_title='?*"')
        assert out == "Show - S01E01"

    def test_very_long_titles_are_capped(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, episode_title="word " * 100)
        prefix = "Show - S01E01 - "
        assert out.startswith(prefix)
        title = out[len(prefix) :]
        assert 0 < len(title) <= 100
        assert title == title.strip()

    def test_braces_in_a_title_are_literal(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, episode_title="{show} {0}")
        assert out == "Show - S01E01 - {show} {0}"

    def test_multi_episode_range_is_not_spliced_into_a_title_that_looks_like_a_code(self):
        # "E1 Rising" matches the E0*1 pattern the range splice searches for.
        out = format_episode_filename(
            TITLED, "Show", 1, 1, extra_episodes=[2], episode_title="E1 Rising"
        )
        assert out == "Show - S01E01-E02 - E1 Rising"

    def test_multi_episode_with_empty_title(self):
        out = format_episode_filename(TITLED, "Show", 1, 1, extra_episodes=[2], episode_title=None)
        assert out == "Show - S01E01-E02"

    def test_title_before_the_episode_token_still_gets_the_range(self):
        fmt = "{episode_title} - {show} - S{season:02d}E{episode:02d}"
        out = format_episode_filename(fmt, "Show", 1, 1, extra_episodes=[2], episode_title="A & B")
        assert out == "A & B - Show - S01E01-E02"
