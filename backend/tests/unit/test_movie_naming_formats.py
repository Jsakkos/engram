"""Unit tests for the movie naming formatters (issue #576).

Pure-function tests: no database, no filesystem. The organize-level
behavior is covered in tests/pipeline/test_organization_paths.py.
"""

from app.core.organizer import (
    ALLOWED_MOVIE_FILE_PLACEHOLDERS,
    ALLOWED_MOVIE_PLACEHOLDERS,
    format_movie_filename,
    format_movie_folder,
    validate_naming_format,
)


class TestFormatMovieFolder:
    def test_default_format_with_year(self):
        assert (
            format_movie_folder("{title} ({year})", "Blade Runner", 1982) == "Blade Runner (1982)"
        )

    def test_default_format_without_year_strips_empty_parens(self):
        assert format_movie_folder("{title} ({year})", "Blade Runner", None) == "Blade Runner"

    def test_plex_tmdb_tag(self):
        result = format_movie_folder(
            "{title} ({year}) {{tmdb-{tmdb_id}}}", "Blade Runner", 1982, tmdb_id=78
        )
        assert result == "Blade Runner (1982) {tmdb-78}"

    def test_jellyfin_tmdb_tag(self):
        result = format_movie_folder(
            "{title} ({year}) [tmdbid-{tmdb_id}]", "Blade Runner", 1982, tmdb_id=78
        )
        assert result == "Blade Runner (1982) [tmdbid-78]"

    def test_missing_tmdb_id_strips_empty_tag(self):
        result = format_movie_folder(
            "{title} ({year}) {{tmdb-{tmdb_id}}}", "Blade Runner", 1982, tmdb_id=None
        )
        assert result == "Blade Runner (1982)"

    def test_unknown_placeholder_falls_back_safely(self):
        result = format_movie_folder("{title} {nonsense}", "Blade Runner", 1982)
        assert result == "Blade Runner (1982)"

    def test_no_year_with_tmdb_id_leaves_no_stray_parens(self):
        """The empty-group helper must strip mid-string parens, not just trailing ones."""
        result = format_movie_folder(
            "{title} ({year}) {{tmdb-{tmdb_id}}}", "Blade Runner", None, tmdb_id=78
        )
        assert result == "Blade Runner {tmdb-78}"

    def test_blank_format_falls_back_to_bare_title(self):
        assert format_movie_folder("", "Blade Runner", 1982) == "Blade Runner"
        assert format_movie_folder("   ", "Blade Runner", 1982) == "Blade Runner"


class TestMoviePlaceholderValidation:
    def test_tmdb_id_is_now_allowed(self):
        assert (
            validate_naming_format(
                "{title} ({year}) {{tmdb-{tmdb_id}}}", ALLOWED_MOVIE_PLACEHOLDERS
            )
            is None
        )

    def test_unknown_placeholder_rejected(self):
        error = validate_naming_format("{title} {director}", ALLOWED_MOVIE_PLACEHOLDERS)
        assert error is not None
        assert "director" in error

    def test_path_traversal_rejected(self):
        assert validate_naming_format("../{title}", ALLOWED_MOVIE_PLACEHOLDERS) is not None


class TestFormatMovieFilename:
    DEFAULT = "{title} ({year}) {{edition-{edition}}}"

    def test_default_without_edition_matches_folder_name(self):
        """No edition renders byte-identically to the plain folder format."""
        assert format_movie_filename(self.DEFAULT, "Blade Runner", 1982) == "Blade Runner (1982)"

    def test_default_with_edition_renders_plex_tag(self):
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Final Cut")
        assert result == "Blade Runner (1982) {edition-Final Cut}"

    def test_edition_hyphen_is_preserved(self):
        """Regression: the edition tag used to be flattened by clean_movie_name."""
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Director's Cut")
        assert result == "Blade Runner (1982) {edition-Director's Cut}"
        assert "{Edition" not in result

    def test_empty_string_edition_strips_tag(self):
        assert format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="") == (
            "Blade Runner (1982)"
        )

    def test_no_year_with_edition_leaves_no_stray_parens(self):
        result = format_movie_filename(self.DEFAULT, "Blade Runner", None, edition="Final Cut")
        assert result == "Blade Runner {edition-Final Cut}"

    def test_tmdb_id_and_edition_together(self):
        result = format_movie_filename(
            "{title} ({year}) {{tmdb-{tmdb_id}}} {{edition-{edition}}}",
            "Blade Runner",
            1982,
            tmdb_id=78,
            edition="Final Cut",
        )
        assert result == "Blade Runner (1982) {tmdb-78} {edition-Final Cut}"

    def test_unknown_placeholder_falls_back_safely(self):
        assert format_movie_filename("{title} {nonsense}", "Blade Runner", 1982) == (
            "Blade Runner (1982)"
        )

    def test_edition_ending_in_hyphen_is_not_swallowed(self):
        """A trailing hyphen used to make the empty-tag regex eat the whole tag."""
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Cut-")
        assert result == "Blade Runner (1982) {edition-Cut}"

    def test_edition_braces_cannot_break_the_tag(self):
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Weird}Name")
        assert result == "Blade Runner (1982) {edition-WeirdName}"
        assert result.count("{") == result.count("}")

    def test_edition_of_only_punctuation_strips_the_tag(self):
        assert format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="}{") == (
            "Blade Runner (1982)"
        )


class TestMovieFilePlaceholderValidation:
    def test_edition_allowed_on_file_format_only(self):
        assert (
            validate_naming_format("{title} {{edition-{edition}}}", ALLOWED_MOVIE_FILE_PLACEHOLDERS)
            is None
        )
        # edition is deliberately NOT valid on the folder format
        assert validate_naming_format("{title} {edition}", ALLOWED_MOVIE_PLACEHOLDERS) is not None
