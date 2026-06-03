"""Unit tests for the Organizer — file naming and organization.

Tests movie/TV naming conventions, conflict resolution, and filename sanitization.
"""

from unittest.mock import patch

import pytest

from app.core.organizer import (
    clean_movie_name,
    organize_movie,
    organize_tv_episode,
    organize_tv_extras,
    sanitize_filename,
)


class TestCleanMovieName:
    """Test movie name cleanup."""

    def test_underscores_to_spaces(self):
        assert clean_movie_name("THE_SOCIAL_NETWORK") == "The Social Network"

    def test_removes_disc_identifiers(self):
        assert "Disc" not in clean_movie_name("INCEPTION DISC 1")
        assert "Bluray" not in clean_movie_name("INCEPTION BLURAY")

    def test_title_case_small_words(self):
        result = clean_movie_name("THE LORD OF THE RINGS")
        assert result == "The Lord of the Rings"

    def test_dashes_to_spaces(self):
        result = clean_movie_name("SPIDER-MAN-HOMECOMING")
        assert "Spider" in result


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_colons(self):
        assert ":" not in sanitize_filename("Star Wars: A New Hope")

    def test_removes_question_marks(self):
        assert "?" not in sanitize_filename("What If?")

    def test_removes_quotes(self):
        assert '"' not in sanitize_filename('She Said "Hello"')

    def test_strips_leading_dots(self):
        result = sanitize_filename("...hidden")
        assert not result.startswith(".")

    def test_preserves_valid_chars(self):
        result = sanitize_filename("Movie Name (2023)")
        assert result == "Movie Name (2023)"


class TestMovieOrganization:
    """Test movie file organization and naming."""

    def test_movie_naming_with_year(self, tmp_path):
        """Movies/Name (Year)/Name (Year).mkv"""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "main.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "library"
        result = organize_movie(
            staging_dir=staging,
            movie_name="Inception",
            year=2010,
            library_path=library,
        )

        assert result["success"] is True
        dest = result["main_file"]
        assert dest.name == "Inception (2010).mkv"
        assert dest.parent.name == "Inception (2010)"

    def test_movie_naming_without_year(self, tmp_path):
        """Movies/Name/Name.mkv when no year."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "main.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "library"
        result = organize_movie(
            staging_dir=staging,
            movie_name="Inception",
            library_path=library,
        )

        assert result["success"] is True
        assert result["main_file"].name == "Inception.mkv"

    def test_movie_no_mkv_files(self, tmp_path):
        """Empty staging dir → error."""
        staging = tmp_path / "staging"
        staging.mkdir()

        result = organize_movie(
            staging_dir=staging,
            movie_name="Nothing",
            library_path=tmp_path / "library",
        )

        assert result["success"] is False
        assert "No MKV files" in result["error"]

    def test_conflict_skip(self, tmp_path):
        """Existing file + skip mode → no overwrite."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "main.mkv").write_bytes(b"new content")

        library = tmp_path / "library"
        dest_dir = library / "Test Movie"
        dest_dir.mkdir(parents=True)
        (dest_dir / "Test Movie.mkv").write_bytes(b"existing")

        result = organize_movie(
            staging_dir=staging,
            movie_name="Test Movie",
            library_path=library,
            conflict_resolution="skip",
        )

        assert result["success"] is True
        assert result.get("skipped") is True
        # Original file should be preserved
        assert (dest_dir / "Test Movie.mkv").read_bytes() == b"existing"

    def test_special_characters_stripped(self, tmp_path):
        """Colons, question marks stripped from names."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "main.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "library"
        result = organize_movie(
            staging_dir=staging,
            movie_name="Star Wars: A New Hope?",
            year=1977,
            library_path=library,
        )

        assert result["success"] is True
        # No colons or question marks in the filename itself
        filename = result["main_file"].name
        assert ":" not in filename
        assert "?" not in filename


class TestTVOrganization:
    """Test TV episode organization and naming."""

    def test_tv_naming(self, tmp_path):
        """TV/Show/Season XX/Show - SXXEXX.mkv"""
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 1024)

        library = tmp_path / "library"
        result = organize_tv_episode(
            source_file=source,
            show_name="The Office",
            episode_code="S01E01",
            library_path=library,
        )

        assert result["success"] is True
        dest = result["final_path"]
        assert dest.name == "The Office - S01E01.mkv"
        assert "Season 01" in str(dest)
        assert "The Office" in str(dest)

    def test_tv_invalid_episode_code(self, tmp_path):
        """Invalid episode code → error."""
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 1024)

        result = organize_tv_episode(
            source_file=source,
            show_name="Test",
            episode_code="INVALID",
            library_path=tmp_path / "library",
        )

        assert result["success"] is False
        assert "Invalid episode code" in result["error"]

    def test_tv_conflict_skip(self, tmp_path):
        """Existing episode + skip → no overwrite."""
        source = tmp_path / "source.mkv"
        source.write_bytes(b"new")

        library = tmp_path / "library"
        dest_dir = library / "Test Show" / "Season 01"
        dest_dir.mkdir(parents=True)
        (dest_dir / "Test Show - S01E01.mkv").write_bytes(b"existing")

        result = organize_tv_episode(
            source_file=source,
            show_name="Test Show",
            episode_code="S01E01",
            library_path=library,
            conflict_resolution="skip",
        )

        assert result["success"] is True
        assert result.get("skipped") is True


@pytest.mark.unit
class TestTVOrderingProjection:
    """Output ordering (#200) is a filename-only projection applied at organize
    time. The canonical episode_code arg is untouched; only the on-disk numbers
    change. matched_episode in the DB stays canonical (verified at a higher
    layer in the finalization invariant test)."""

    def test_aired_default_uses_canonical_without_projecting(self, tmp_path):
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 16)
        with patch("app.core.episode_ordering.project_episode") as proj:
            result = organize_tv_episode(
                source_file=source,
                show_name="Firefly",
                episode_code="S01E11",
                library_path=tmp_path / "library",
            )
        assert result["success"] is True
        assert result["final_path"].name == "Firefly - S01E11.mkv"
        # aired is the identity case — the projection must not even be invoked.
        assert proj.call_count == 0

    def test_dvd_projects_episode_number(self, tmp_path):
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 16)
        # Canonical S01E11 ("Serenity") -> DVD S01E01.
        with patch("app.core.episode_ordering.project_episode", return_value=(1, 1)) as proj:
            result = organize_tv_episode(
                source_file=source,
                show_name="Firefly",
                episode_code="S01E11",
                library_path=tmp_path / "library",
                tmdb_id="1437",
                ordering="dvd",
            )
        assert result["success"] is True
        assert result["final_path"].name == "Firefly - S01E01.mkv"
        proj.assert_called_once()
        # called with the CANONICAL season/episode parsed from episode_code
        args = proj.call_args[0]
        assert args[1] == "dvd" and args[2] == 1 and args[3] == 11

    def test_projection_can_change_season_folder(self, tmp_path):
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 16)
        with patch("app.core.episode_ordering.project_episode", return_value=(2, 5)):
            result = organize_tv_episode(
                source_file=source,
                show_name="Show",
                episode_code="S01E03",
                library_path=tmp_path / "library",
                tmdb_id="42",
                ordering="dvd",
            )
        dest = result["final_path"]
        assert dest.name == "Show - S02E05.mkv"
        assert "Season 02" in str(dest)

    def test_no_tmdb_id_skips_projection(self, tmp_path):
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 16)
        with patch("app.core.episode_ordering.project_episode") as proj:
            result = organize_tv_episode(
                source_file=source,
                show_name="Firefly",
                episode_code="S01E11",
                library_path=tmp_path / "library",
                tmdb_id=None,
                ordering="dvd",
            )
        assert result["final_path"].name == "Firefly - S01E11.mkv"
        assert proj.call_count == 0

    def test_projection_identity_when_no_group(self, tmp_path):
        source = tmp_path / "source.mkv"
        source.write_bytes(b"x" * 16)
        # project_episode returns the input unchanged when no DVD group exists.
        with patch("app.core.episode_ordering.project_episode", return_value=(1, 11)):
            result = organize_tv_episode(
                source_file=source,
                show_name="Firefly",
                episode_code="S01E11",
                library_path=tmp_path / "library",
                tmdb_id="1437",
                ordering="dvd",
            )
        assert result["final_path"].name == "Firefly - S01E11.mkv"


class TestNamingHelpers:
    """format_tv_show_folder, widened placeholders, episode-filename year."""

    def test_show_folder_plex_full(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", 1993, "3452") == "Frasier (1993) {tmdb-3452}"

    def test_show_folder_jellyfin_full(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) [tmdbid-{tmdb_id}]"
        assert (
            format_tv_show_folder(fmt, "Frasier", 2023, "195241")
            == "Frasier (2023) [tmdbid-195241]"
        )

    def test_show_folder_missing_year_keeps_id(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", None, "3452") == "Frasier {tmdb-3452}"

    def test_show_folder_whitespace_only_format_falls_back_to_bare(self):
        # A whitespace-only format must NOT collapse the show-folder level
        # (would file every show flat under the TV root).
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("   ", "Frasier", 1993, "3452") == "Frasier"

    def test_show_folder_jellyfin_missing_id_strips_tag(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) [tmdbid-{tmdb_id}]"
        assert format_tv_show_folder(fmt, "Frasier", 1993, None) == "Frasier (1993)"

    def test_show_folder_missing_both_is_bare(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", None, None) == "Frasier"

    def test_show_folder_show_only_format_ignores_extras(self):
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("{show}", "Frasier", 1993, "3452") == "Frasier"

    def test_show_folder_empty_format_falls_back_to_bare(self):
        # Existing DBs may have backfilled '' for this column; degrade, don't break.
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("", "Frasier", 1993, "3452") == "Frasier"

    def test_show_folder_default_preserves_internal_double_space(self):
        # Default "{show}" must be byte-identical to pre-feature behavior: a show
        # name that sanitizes to a double space is NOT collapsed (no silent reloc).
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("{show}", "Tom  Jerry", 1993, "3452") == "Tom  Jerry"

    def test_episode_filename_with_year(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} ({year}) - S{season:02d}E{episode:02d}", "Frasier", 1, 2, year=1993
        )
        assert out == "Frasier (1993) - S01E02"

    def test_episode_filename_year_missing_strips_parens(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} ({year}) - S{season:02d}E{episode:02d}", "Frasier", 1, 2, year=None
        )
        assert out == "Frasier - S01E02"

    def test_episode_filename_default_unchanged(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename("{show} - S{season:02d}E{episode:02d}", "Frasier", 1, 2)
        assert out == "Frasier - S01E02"

    def test_episode_filename_plex_tag_with_id(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} {{tmdb-{tmdb_id}}} - S{season:02d}E{episode:02d}",
            "Frasier",
            1,
            2,
            tmdb_id="3452",
        )
        assert out == "Frasier {tmdb-3452} - S01E02"

    def test_episode_filename_plex_tag_missing_id_stripped(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} {{tmdb-{tmdb_id}}} - S{season:02d}E{episode:02d}",
            "Frasier",
            1,
            2,
            tmdb_id=None,
        )
        assert out == "Frasier - S01E02"

    def test_placeholder_sets_validate(self):
        from app.core.organizer import (
            ALLOWED_EPISODE_PLACEHOLDERS,
            ALLOWED_TV_SHOW_PLACEHOLDERS,
            validate_naming_format,
        )

        assert ALLOWED_TV_SHOW_PLACEHOLDERS == {"show", "year", "tmdb_id"}
        assert {"year", "tmdb_id"} <= ALLOWED_EPISODE_PLACEHOLDERS
        assert (
            validate_naming_format(
                "{show} ({year}) {{tmdb-{tmdb_id}}}", ALLOWED_TV_SHOW_PLACEHOLDERS
            )
            is None
        )
        assert (
            validate_naming_format(
                "{show} ({year}) [tmdbid-{tmdb_id}]", ALLOWED_TV_SHOW_PLACEHOLDERS
            )
            is None
        )
        assert (
            validate_naming_format(
                "{show} ({year}) - S{season:02d}E{episode:02d}", ALLOWED_EPISODE_PLACEHOLDERS
            )
            is None
        )
        # Unknown placeholder still rejected.
        assert validate_naming_format("{bogus}", ALLOWED_TV_SHOW_PLACEHOLDERS) is not None


class TestTVDisambiguation:
    """End-to-end folder building with the disambiguating format."""

    @staticmethod
    def _patch_cfg(**over):
        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    def test_same_name_twins_land_in_distinct_folders(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            s1 = tmp_path / "a.mkv"
            s1.write_bytes(b"x")
            r1 = organize_tv_episode(
                s1, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
            s2 = tmp_path / "b.mkv"
            s2.write_bytes(b"x")
            r2 = organize_tv_episode(
                s2, "Frasier", "S01E02", library_path=lib, tmdb_id="195241", year=2023
            )
        assert r1["success"] and r2["success"]
        assert "Frasier (1993) {tmdb-3452}" in str(r1["final_path"])
        assert "Frasier (2023) {tmdb-195241}" in str(r2["final_path"])
        assert r1["final_path"] != r2["final_path"]

    def test_default_format_keeps_bare_folder(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg():  # naming_tv_show_format defaults to "{show}"
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"] == lib / "Frasier" / "Season 01" / "Frasier - S01E02.mkv"

    def test_missing_year_keeps_id_no_empty_parens(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=None
            )
        assert r["success"]
        assert r["final_path"] == lib / "Frasier {tmdb-3452}" / "Season 01" / "Frasier - S01E02.mkv"
        assert "()" not in str(r["final_path"])

    def test_episode_filename_year_opt_in(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg(
            naming_tv_show_format="{show} ({year}) {{tmdb-{tmdb_id}}}",
            naming_episode_format="{show} ({year}) - S{season:02d}E{episode:02d}",
        ):
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"].name == "Frasier (1993) - S01E02.mkv"

    def test_extras_share_show_folder_with_episode(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            ep = tmp_path / "e.mkv"
            ep.write_bytes(b"x")
            r_ep = organize_tv_episode(
                ep, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
            ex = tmp_path / "x.mkv"
            ex.write_bytes(b"x")
            r_ex = organize_tv_extras(
                ex,
                "Frasier",
                season=1,
                library_path=lib,
                disc_number=1,
                title_index=3,
                tmdb_id="3452",
                year=1993,
            )
        assert r_ep["success"] and r_ex["success"]
        show_dir = str(lib / "Frasier (1993) {tmdb-3452}")
        assert str(r_ep["final_path"]).startswith(show_dir)
        assert str(r_ex["final_path"]).startswith(show_dir)
        assert "Extras" in str(r_ex["final_path"])
