"""Unit tests for testing service orchestration."""

from unittest.mock import Mock, patch

import pytest

from app.matcher.testing_service import download_subtitles


@pytest.mark.unit
class TestDownloadSubtitles:
    """Tests for subtitle download orchestration."""

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_successful_download(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test complete download workflow with all mocks."""
        # Mock config
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        # Mock TMDB
        mock_show_id.return_value = "4589"
        mock_show_details.return_value = {"name": "Arrested Development"}
        mock_season.return_value = 3  # 3 episodes

        # Mock Addic7ed client (primary scraper)
        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        mock_subtitle.language = "English"
        mock_subtitle.version = "WEB"
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle for {save_path.name}\n"
            )
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        # Mock OpenSubtitles client (not called since Addic7ed succeeds)
        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute
        result = download_subtitles("Arrested Development", 1)

        # Verify
        assert result["show_name"] == "Arrested Development"
        assert result["season"] == 1
        assert result["total_episodes"] == 3
        assert len(result["episodes"]) == 3
        assert all(ep["status"] == "downloaded" for ep in result["episodes"])
        assert all(ep["source"] == "addic7ed" for ep in result["episodes"])

        # Verify files were created
        cache_path = tmp_path / "data" / "Arrested Development"
        assert cache_path.exists()
        assert (cache_path / "Arrested Development - S01E01.srt").exists()
        assert (cache_path / "Arrested Development - S01E02.srt").exists()
        assert (cache_path / "Arrested Development - S01E03.srt").exists()

    @patch("app.matcher.testing_service.fetch_show_id")
    def test_tmdb_show_not_found_raises_error(self, mock_show_id):
        """Test that ValueError is raised when show not found on TMDB."""
        mock_show_id.return_value = None

        with pytest.raises(ValueError, match="Could not find show"):
            download_subtitles("Nonexistent Show", 1)

    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    def test_no_episodes_found_raises_error(self, mock_show_id, mock_season):
        """Test that ValueError is raised when no episodes found."""
        mock_show_id.return_value = "123"
        mock_season.return_value = 0  # No episodes

        with pytest.raises(ValueError, match="No episodes found"):
            download_subtitles("Test Show", 1)

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_cached_subtitles_not_redownloaded(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that cached files aren't re-downloaded."""
        # Setup cache with existing files (valid SRT content with --> markers)
        cache_dir = tmp_path / "data" / "Test Show"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Test Show - S01E01.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 1\n"
        )
        (cache_dir / "Test Show - S01E02.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 2\n"
        )

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 2  # 2 episodes

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify no downloads were attempted
        assert addic7ed_client.get_best_subtitle.call_count == 0
        assert os_client.get_best_subtitle.call_count == 0
        assert all(ep["status"] == "cached" for ep in result["episodes"])
        assert all(ep["source"] == "cache" for ep in result["episodes"])

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_partial_cache_downloads_missing(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that only missing episodes are downloaded."""
        # Setup cache with partial files (valid SRT content with --> markers)
        cache_dir = tmp_path / "data" / "Test Show"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Test Show - S01E01.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 1\n"
        )

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 3  # 3 episodes total

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nDownloaded subtitle\n")
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify: 1 cached, 2 downloaded
        statuses = [ep["status"] for ep in result["episodes"]]
        assert statuses.count("cached") == 1
        assert statuses.count("downloaded") == 2
        assert addic7ed_client.get_best_subtitle.call_count == 2  # Only for missing episodes

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_subtitle_not_found_on_addic7ed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test handling when subtitle not found on both scrapers."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 2  # 2 episodes

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        addic7ed_client.get_best_subtitle.return_value = None  # Not found

        os_client = Mock()
        mock_opensubtitles.return_value = os_client
        os_client.get_best_subtitle.return_value = None  # Not found either

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify both scrapers were tried
        assert all(ep["status"] == "not_found" for ep in result["episodes"])
        assert all(ep["path"] is None for ep in result["episodes"])
        assert all(ep["source"] is None for ep in result["episodes"])

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_download_failure_marked_as_failed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test handling when both downloads fail but subtitle entries exist."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 1  # 1 episode

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle
        addic7ed_client.download_subtitle.return_value = None  # Download failed

        os_client = Mock()
        mock_opensubtitles.return_value = os_client
        os_client.get_best_subtitle.return_value = mock_subtitle
        os_client.download_subtitle.return_value = None  # Download failed too

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify both scrapers were tried
        assert result["episodes"][0]["status"] == "not_found"

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_exception_during_download_marked_as_failed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that exceptions during download are caught and both scrapers tried."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 1

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        addic7ed_client.get_best_subtitle.side_effect = Exception("Network error")

        os_client = Mock()
        mock_opensubtitles.return_value = os_client
        os_client.get_best_subtitle.side_effect = Exception("Network error")

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify
        assert result["episodes"][0]["status"] == "not_found"


@pytest.mark.unit
class TestSubtitleFilenameFormat:
    """Tests for subtitle filename format."""

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_filename_format(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that subtitle filenames follow correct format."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "The Office"}
        mock_season.return_value = 2

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle for {save_path.name}\n"
            )
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute
        download_subtitles("The Office", 1)

        # Verify filename format: Show_Name - S##E##.srt
        cache_path = tmp_path / "data" / "The Office"
        assert (cache_path / "The Office - S01E01.srt").exists()
        assert (cache_path / "The Office - S01E02.srt").exists()
