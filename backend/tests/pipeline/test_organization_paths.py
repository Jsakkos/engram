"""Test Organizer dry-run path generation for all disc scenarios.

Verifies naming conventions without actually needing a running database.
Uses tmp_path for real file operations with tiny dummy files.
"""

import pytest

from app.core.organizer import (
    clean_movie_name,
    organize_movie,
    organize_tv_episode,
    organize_tv_extras,
)


@pytest.mark.pipeline
class TestMovieOrganizationPaths:
    """Verify movie organization path generation."""

    def test_italian_job_path(self, tmp_path):
        """The Italian Job (2003) -> Movies/The Italian Job (2003)/The Italian Job (2003).mkv"""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "title_t00.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "movies"
        result = organize_movie(staging, "The Italian Job", year=2003, library_path=library)

        assert result["success"], f"Failed: {result.get('error')}"
        assert result["main_file"].name == "The Italian Job (2003).mkv"
        assert result["main_file"].parent.name == "The Italian Job (2003)"

    def test_terminator_path(self, tmp_path):
        """The Terminator (1984) -> Movies/The Terminator (1984)/The Terminator (1984).mkv"""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "THE TERMINATOR_t01.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "movies"
        result = organize_movie(staging, "The Terminator", year=1984, library_path=library)

        assert result["success"], f"Failed: {result.get('error')}"
        assert result["main_file"].name == "The Terminator (1984).mkv"

    def test_movie_without_year(self, tmp_path):
        """Movie organized without year -> no parenthetical."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "movie.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "movies"
        result = organize_movie(staging, "The Italian Job", year=None, library_path=library)

        assert result["success"]
        assert "()" not in str(result["main_file"])


@pytest.mark.pipeline
class TestMovieNameCleaning:
    """Verify the clean_movie_name function with real disc label patterns."""

    def test_clean_uppercase_with_underscores(self):
        assert clean_movie_name("THE_ITALIAN_JOB") == "The Italian Job"

    def test_clean_with_disc_suffix(self):
        result = clean_movie_name("INCEPTION_DISC1")
        assert "Disc" not in result
        assert "Inception" in result

    def test_clean_with_bluray_suffix(self):
        result = clean_movie_name("THE_TERMINATOR_BLURAY")
        assert "Bluray" not in result and "bluray" not in result.lower()

    def test_clean_preserves_existing_title_case(self):
        result = clean_movie_name("The Italian Job")
        assert result == "The Italian Job"


@pytest.mark.pipeline
class TestTVEpisodeOrganizationPaths:
    """Verify TV episode organization path generation."""

    def test_picard_episode_path(self, tmp_path):
        library = tmp_path / "tv"
        source = tmp_path / "staging" / "t00.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x" * 1024)

        result = organize_tv_episode(
            source,
            "Star Trek Picard",
            "S01E07",
            library_path=library,
        )
        assert result["success"]
        assert result["final_path"].name == "Star Trek Picard - S01E07.mkv"
        assert "Season 01" in str(result["final_path"])

    def test_arrested_dev_all_episodes(self, tmp_path):
        """All 8 episodes produce correct S01EXX filenames."""
        library = tmp_path / "tv"

        for ep_num in range(1, 9):
            source = tmp_path / "staging" / f"ep{ep_num}.mkv"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"x" * 1024)

            result = organize_tv_episode(
                source,
                "Arrested Development",
                f"S01E{ep_num:02d}",
                library_path=library,
            )
            assert result["success"]
            assert f"S01E{ep_num:02d}" in result["final_path"].name
            assert "Arrested Development" in result["final_path"].name
            assert "Season 01" in str(result["final_path"])


@pytest.mark.pipeline
class TestTVExtrasOrganizationPaths:
    """Verify TV extras organization path generation."""

    def test_picard_extras_path(self, tmp_path):
        library = tmp_path / "tv"
        source = tmp_path / "staging" / "extra.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x" * 1024)

        result = organize_tv_extras(
            source,
            "Star Trek Picard",
            season=1,
            library_path=library,
            disc_number=3,
            extra_index=1,
        )
        assert result["success"]
        assert "Extras" in str(result["final_path"])
        assert "Disc 3" in str(result["final_path"])
        assert "Season 01" in str(result["final_path"])

    def test_arrested_dev_extras_path(self, tmp_path):
        library = tmp_path / "tv"
        source = tmp_path / "staging" / "bonus.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x" * 1024)

        result = organize_tv_extras(
            source,
            "Arrested Development",
            season=1,
            library_path=library,
            disc_number=1,
            extra_index=1,
        )
        assert result["success"]
        assert "Extras" in str(result["final_path"])
        assert "Disc 1" in str(result["final_path"])


@pytest.mark.pipeline
class TestMovieExtrasMapping:
    """Verify organize_movie returns correct source→destination mapping for extras."""

    def test_extras_mapping_populated(self, tmp_path):
        """Main + 2 extras: extras_mapping keys are source basenames, values are Extras/ paths."""
        staging = tmp_path / "staging"
        staging.mkdir()
        # Make main file larger so find_main_movie_file picks it
        (staging / "t00.mkv").write_bytes(b"x" * 4096)
        (staging / "t01.mkv").write_bytes(b"x" * 512)
        (staging / "t02.mkv").write_bytes(b"x" * 512)

        library = tmp_path / "movies"
        result = organize_movie(staging, "Apocalypse Now", year=1979, library_path=library)

        assert result["success"], result.get("error")
        assert set(result["extras_mapping"].keys()) == {"t01.mkv", "t02.mkv"}
        for dest_path in result["extras_mapping"].values():
            assert "Extras" in str(dest_path)
            assert dest_path.suffix == ".mkv"

    def test_no_extras_mapping_empty(self, tmp_path):
        """Single-file disc: extras_mapping is empty on a single-file disc."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "t00.mkv").write_bytes(b"x" * 1024)

        library = tmp_path / "movies"
        result = organize_movie(staging, "Inception", year=2010, library_path=library)

        assert result["success"]
        assert result["extras_mapping"] == {}

    def test_extras_mapped_to_sequential_names(self, tmp_path):
        """Extras destinations are named Extra 1.mkv, Extra 2.mkv in order."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "t00.mkv").write_bytes(b"x" * 4096)
        (staging / "t01.mkv").write_bytes(b"x" * 256)
        (staging / "t02.mkv").write_bytes(b"x" * 256)

        library = tmp_path / "movies"
        result = organize_movie(staging, "The Godfather", year=1972, library_path=library)

        assert result["success"]
        dest_names = {dest.name for dest in result["extras_mapping"].values()}
        assert dest_names == {"Extra 1.mkv", "Extra 2.mkv"}


@pytest.mark.pipeline
class TestTVSameNameCoexistence:
    """Same-name shows coexist when disambiguation is enabled; default unchanged."""

    @staticmethod
    def _patch_cfg(**over):
        from unittest.mock import patch

        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    def test_frasier_twins_coexist(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            a = tmp_path / "staging" / "a.mkv"
            a.parent.mkdir(parents=True, exist_ok=True)
            a.write_bytes(b"x" * 1024)
            r1 = organize_tv_episode(
                a, "Frasier", "S01E01", library_path=lib, tmdb_id="3452", year=1993
            )
            b = tmp_path / "staging" / "b.mkv"
            b.write_bytes(b"x" * 1024)
            r2 = organize_tv_episode(
                b, "Frasier", "S01E01", library_path=lib, tmdb_id="195241", year=2023
            )
        assert r1["success"] and r2["success"]
        assert r1["final_path"].parent.parent.name == "Frasier (1993) {tmdb-3452}"
        assert r2["final_path"].parent.parent.name == "Frasier (2023) {tmdb-195241}"
        assert r1["final_path"] != r2["final_path"]

    def test_default_format_unchanged_bare_folder(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg():  # default naming_tv_show_format == "{show}"
            s = tmp_path / "staging" / "c.mkv"
            s.parent.mkdir(parents=True, exist_ok=True)
            s.write_bytes(b"x" * 1024)
            r = organize_tv_episode(
                s, "Frasier", "S01E01", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"].parent.parent.name == "Frasier"
        assert r["final_path"].name == "Frasier - S01E01.mkv"


@pytest.mark.pipeline
class TestMovieNamingParity:
    """Movie folder/filename formats, edition tags, and canonical-title handling (#576)."""

    @staticmethod
    def _patch_cfg(**over):
        from unittest.mock import patch

        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    @staticmethod
    def _staged(tmp_path, name="t00.mkv"):
        staging = tmp_path / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / name).write_bytes(b"x" * 1024)
        return staging

    def test_default_no_edition_matches_pre_feature_layout(self, tmp_path):
        """Guard: the common case must not move relative to the old behavior."""
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(staging, "Blade Runner", year=1982, library_path=tmp_path / "movies")
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982)"
        assert r["main_file"].name == "Blade Runner (1982).mkv"

    def test_edition_lands_on_the_file_unmangled(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                edition="Final Cut",
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982)"
        assert r["main_file"].name == "Blade Runner (1982) {edition-Final Cut}.mkv"

    def test_blank_folder_format_never_yields_a_bare_extension(self, tmp_path):
        """A cleared folder format must not file the movie as ".mkv".

        Blank passes validation and is persistable, and the filename format
        inherits it, so both levels could collapse at once.
        """
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_format="", naming_movie_file_format=""):
            r = organize_movie(staging, "Blade Runner", year=1982, library_path=tmp_path / "movies")
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner"
        assert r["main_file"].name == "Blade Runner.mkv"

    def test_blank_folder_format_with_edition_keeps_the_title(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_format="", naming_movie_file_format=""):
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                edition="Final Cut",
            )
        assert r["success"], r.get("error")
        assert r["main_file"].name.startswith("Blade Runner")
        assert r["main_file"].name != "{edition-Final Cut}.mkv"

    def test_custom_folder_format_propagates_to_the_filename(self, tmp_path):
        """A blank file format inherits the folder format, so the two cannot disagree."""
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_format="{title} - {year}"):
            r = organize_movie(staging, "Blade Runner", year=1982, library_path=tmp_path / "movies")
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner - 1982"
        assert r["main_file"].name == "Blade Runner - 1982.mkv"

    def test_custom_folder_format_still_earns_the_edition_tag(self, tmp_path):
        """Inheriting must not cost the Plex edition tag, which belongs on the file."""
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_format="{title} - {year}"):
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                edition="Final Cut",
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner - 1982"
        assert r["main_file"].name == "Blade Runner - 1982 {edition-Final Cut}.mkv"

    def test_explicit_file_format_overrides_inheritance(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg(
            naming_movie_format="{title} ({year}) {{tmdb-{tmdb_id}}}",
            naming_movie_file_format="{title}",
        ):
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                tmdb_id=78,
                edition="Final Cut",
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982) {tmdb-78}"
        assert r["main_file"].name == "Blade Runner.mkv"

    def test_tmdb_id_tag_in_folder(self, tmp_path):
        staging = self._staged(tmp_path)
        plex = "{title} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_movie_format=plex):
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                tmdb_id=78,
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982) {tmdb-78}"

    def test_blank_file_format_inherits_folder_format(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_file_format=""):
            r = organize_movie(staging, "Blade Runner", year=1982, library_path=tmp_path / "movies")
        assert r["success"], r.get("error")
        assert r["main_file"].name == "Blade Runner (1982).mkv"

    def test_canonical_tmdb_title_is_not_normalized(self, tmp_path):
        """Regression: clean_movie_name used to turn Spider-Man into Spider Man."""
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging,
                "Spider-Man: No Way Home",
                year=2021,
                library_path=tmp_path / "movies",
                already_clean=True,
            )
        assert r["success"], r.get("error")
        # The colon is stripped by sanitize_filename (invalid on Windows); the
        # hyphen and the capitalization must survive.
        assert r["main_file"].parent.name.startswith("Spider-Man")
        assert "Spider Man" not in r["main_file"].parent.name

    def test_volume_label_still_normalized_by_default(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging, "THE_SOCIAL_NETWORK", year=2010, library_path=tmp_path / "movies"
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "The Social Network (2010)"
