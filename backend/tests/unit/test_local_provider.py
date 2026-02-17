"""Unit tests for LocalSubtitleProvider."""


import pytest

from app.matcher.core.providers.subtitles import (
    LocalSubtitleProvider,
    parse_season_episode,
)


@pytest.mark.unit
class TestParseSeasonEpisode:
    """Tests for season/episode parsing from filenames."""

    def test_parse_sxxexx_format(self):
        """Test parsing S##E## format."""
        result = parse_season_episode("Breaking_Bad - S01E05.srt")

        assert result is not None
        assert result.season == 1
        assert result.episode == 5

    def test_parse_lowercase_sxxexx(self):
        """Test parsing lowercase s##e## format."""
        result = parse_season_episode("show - s02e10.srt")

        assert result is not None
        assert result.season == 2
        assert result.episode == 10

    def test_parse_1x01_format(self):
        """Test parsing 1x01 format."""
        result = parse_season_episode("show.3x07.mkv")

        assert result is not None
        assert result.season == 3
        assert result.episode == 7

    def test_parse_single_digit_season_episode(self):
        """Test parsing single-digit season and episode."""
        result = parse_season_episode("S1E2.srt")

        assert result is not None
        assert result.season == 1
        assert result.episode == 2

    def test_parse_returns_none_for_invalid(self):
        """Test that invalid filenames return None."""
        result = parse_season_episode("random_file.srt")

        assert result is None

    def test_parse_extracts_from_complex_filename(self):
        """Test parsing from complex filename with extra metadata."""
        result = parse_season_episode(
            "Breaking.Bad.S05E16.Felina.1080p.WEB-DL.DD5.1.H.264-BS.srt"
        )

        assert result is not None
        assert result.season == 5
        assert result.episode == 16


@pytest.mark.unit
class TestLocalSubtitleProvider:
    """Tests for LocalSubtitleProvider cache loading."""

    def test_get_subtitles_from_populated_cache(self, populated_cache_dir):
        """Test loading subtitles from populated cache."""
        provider = LocalSubtitleProvider(cache_dir=populated_cache_dir)

        subtitles = provider.get_subtitles("Breaking_Bad", season=1)

        assert len(subtitles) == 3
        assert all(sub.episode_info.season == 1 for sub in subtitles)
        assert all(sub.path.exists() for sub in subtitles)
        # Verify episodes 1, 2, 3
        episode_numbers = sorted([sub.episode_info.episode for sub in subtitles])
        assert episode_numbers == [1, 2, 3]

    def test_empty_cache_returns_empty_list(self, temp_cache_dir):
        """Test non-existent show returns empty list."""
        provider = LocalSubtitleProvider(cache_dir=temp_cache_dir)

        subtitles = provider.get_subtitles("Nonexistent_Show", season=1)

        assert subtitles == []

    def test_cache_dir_appends_data_internally(self, tmp_path):
        """Test that provider appends /data to cache_dir internally."""
        cache_root = tmp_path / "cache"
        cache_root.mkdir()

        # Create subtitle in correct location
        data_dir = cache_root / "data" / "Test_Show"
        data_dir.mkdir(parents=True)
        (data_dir / "Test_Show - S01E01.srt").write_text("Subtitle")

        # Pass cache_root (not cache_root/data)
        provider = LocalSubtitleProvider(cache_dir=cache_root)

        subtitles = provider.get_subtitles("Test_Show", season=1)

        assert len(subtitles) == 1

    def test_filters_by_season(self, temp_cache_dir):
        """Test that only matching season subtitles are returned."""
        show_dir = temp_cache_dir / "data" / "Multi_Season_Show"
        show_dir.mkdir(parents=True)

        # Create subtitles for multiple seasons
        (show_dir / "Multi_Season_Show - S01E01.srt").write_text("S1E1")
        (show_dir / "Multi_Season_Show - S01E02.srt").write_text("S1E2")
        (show_dir / "Multi_Season_Show - S02E01.srt").write_text("S2E1")
        (show_dir / "Multi_Season_Show - S02E02.srt").write_text("S2E2")

        provider = LocalSubtitleProvider(cache_dir=temp_cache_dir)

        # Get season 1 only
        s1_subtitles = provider.get_subtitles("Multi_Season_Show", season=1)
        assert len(s1_subtitles) == 2
        assert all(sub.episode_info.season == 1 for sub in s1_subtitles)

        # Get season 2 only
        s2_subtitles = provider.get_subtitles("Multi_Season_Show", season=2)
        assert len(s2_subtitles) == 2
        assert all(sub.episode_info.season == 2 for sub in s2_subtitles)

    def test_handles_uppercase_srt_extension(self, temp_cache_dir):
        """Test that .SRT files are also loaded."""
        show_dir = temp_cache_dir / "data" / "Test_Show"
        show_dir.mkdir(parents=True)
        (show_dir / "Test_Show - S01E01.SRT").write_text("Uppercase extension")

        provider = LocalSubtitleProvider(cache_dir=temp_cache_dir)

        subtitles = provider.get_subtitles("Test_Show", season=1)

        assert len(subtitles) == 1
        assert subtitles[0].path.suffix.upper() == ".SRT"

    def test_ignores_non_srt_files(self, temp_cache_dir):
        """Test that non-SRT files are ignored."""
        show_dir = temp_cache_dir / "data" / "Test_Show"
        show_dir.mkdir(parents=True)
        (show_dir / "Test_Show - S01E01.srt").write_text("Valid SRT")
        (show_dir / "Test_Show - S01E02.txt").write_text("Not SRT")
        (show_dir / "Test_Show - S01E03.sub").write_text("Different format")

        provider = LocalSubtitleProvider(cache_dir=temp_cache_dir)

        subtitles = provider.get_subtitles("Test_Show", season=1)

        assert len(subtitles) == 1
        assert subtitles[0].path.suffix == ".srt"

    def test_ignores_files_without_episode_info(self, temp_cache_dir):
        """Test that files without S##E## pattern are ignored."""
        show_dir = temp_cache_dir / "data" / "Test_Show"
        show_dir.mkdir(parents=True)
        (show_dir / "Test_Show - S01E01.srt").write_text("Valid")
        (show_dir / "README.srt").write_text("No episode info")
        (show_dir / "metadata.srt").write_text("No episode info")

        provider = LocalSubtitleProvider(cache_dir=temp_cache_dir)

        subtitles = provider.get_subtitles("Test_Show", season=1)

        assert len(subtitles) == 1

    def test_subtitle_file_contains_episode_info(self, populated_cache_dir):
        """Test that SubtitleFile objects contain correct episode info."""
        provider = LocalSubtitleProvider(cache_dir=populated_cache_dir)

        subtitles = provider.get_subtitles("Breaking_Bad", season=1)

        # Find episode 1
        ep1 = next(sub for sub in subtitles if sub.episode_info.episode == 1)

        assert ep1.episode_info.season == 1
        assert ep1.episode_info.episode == 1
        assert ep1.path.name == "Breaking_Bad - S01E01.srt"

    def test_handles_missing_cache_dir_gracefully(self, tmp_path):
        """Test that missing cache directory is handled gracefully."""
        non_existent_cache = tmp_path / "does_not_exist"

        provider = LocalSubtitleProvider(cache_dir=non_existent_cache)

        # Should not raise exception
        subtitles = provider.get_subtitles("Test_Show", season=1)

        assert subtitles == []

    def test_subtitle_paths_are_absolute(self, populated_cache_dir):
        """Test that returned subtitle paths are absolute."""
        provider = LocalSubtitleProvider(cache_dir=populated_cache_dir)

        subtitles = provider.get_subtitles("Breaking_Bad", season=1)

        assert len(subtitles) > 0
        for sub in subtitles:
            assert sub.path.is_absolute()
