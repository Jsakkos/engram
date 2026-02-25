"""Test Organizer dry-run path generation for all disc scenarios.

Verifies naming conventions without actually needing a running database.
Uses tmp_path for real file operations with tiny dummy files.
"""

import pytest
from pathlib import Path

from app.core.organizer import (
    clean_movie_name,
    organize_movie,
    organize_tv_episode,
    organize_tv_extras,
    sanitize_filename,
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
            source, "Star Trek Picard", "S01E07", library_path=library,
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
                source, "Arrested Development", f"S01E{ep_num:02d}",
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
            source, "Star Trek Picard", season=1,
            library_path=library, disc_number=3, extra_index=1,
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
            source, "Arrested Development", season=1,
            library_path=library, disc_number=1, extra_index=1,
        )
        assert result["success"]
        assert "Extras" in str(result["final_path"])
        assert "Disc 1" in str(result["final_path"])
