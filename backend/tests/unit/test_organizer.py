"""Unit tests for the Organizer — file naming and organization.

Tests movie/TV naming conventions, conflict resolution, and filename sanitization.
"""

from app.core.organizer import (
    clean_movie_name,
    organize_movie,
    organize_tv_episode,
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
