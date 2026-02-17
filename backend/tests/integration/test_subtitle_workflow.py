"""Integration tests for end-to-end subtitle workflow."""

from unittest.mock import Mock, patch

import pytest

from app.matcher.testing_service import download_subtitles
from tests.fixtures.tmdb_responses import (
    TMDB_SEARCH_ARRESTED_DEVELOPMENT,
    TMDB_SEASON_DETAILS_S01_3EP,
)


@pytest.mark.integration
class TestSubtitleWorkflowIntegration:
    """Tests for complete TMDB → Addic7ed → cache flow."""

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.tmdb_client.requests.get")
    @patch("app.services.config_service.get_config_sync")
    def test_full_download_workflow(
        self,
        mock_config_sync,
        mock_requests,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test TMDB lookup → Addic7ed download → cache creation."""
        # Setup config
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.tmdb_api_key = "test_key_12345"
        mock_config_sync.return_value = mock_config

        # Mock TMDB responses (search + season details)
        mock_requests.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_ARRESTED_DEVELOPMENT),
            Mock(status_code=200, json=lambda: TMDB_SEASON_DETAILS_S01_3EP),
        ]

        # Mock Addic7ed downloads
        client = Mock()
        mock_addic7ed.return_value = client

        mock_subtitle = Mock()
        mock_subtitle.language = "English"
        mock_subtitle.version = "WEB"
        client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle for {save_path.name}\n"
            )
            return save_path

        client.download_subtitle.side_effect = download_side_effect

        # Mock OpenSubtitles (not called since Addic7ed succeeds)
        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute workflow
        result = download_subtitles("Arrested Development", 1)

        # Verify result structure
        assert result["show_name"] == "Arrested Development"
        assert result["season"] == 1
        assert result["total_episodes"] == 3
        assert len(result["episodes"]) == 3, (
            f"Expected 3 episodes, got {len(result['episodes'])}: {result['episodes']}"
        )
        # Verify all episodes downloaded
        assert all(ep["status"] == "downloaded" for ep in result["episodes"]), (
            f"Not all downloaded: {[ep['status'] for ep in result['episodes']]}"
        )

        # Verify files were created in cache
        cache_path = tmp_path / "data" / "Arrested Development"
        assert cache_path.exists(), (
            f"Cache path doesn't exist: {cache_path}. Episodes: {result['episodes']}"
        )
        assert (cache_path / "Arrested Development - S01E01.srt").exists()
        assert (cache_path / "Arrested Development - S01E02.srt").exists()
        assert (cache_path / "Arrested Development - S01E03.srt").exists()

        # Verify file contents
        ep1_content = (cache_path / "Arrested Development - S01E01.srt").read_text()
        assert "Subtitle for" in ep1_content

        # Verify Addic7ed was called correctly
        assert client.get_best_subtitle.call_count == 3
        client.get_best_subtitle.assert_any_call("Arrested Development", 1, 1)
        client.get_best_subtitle.assert_any_call("Arrested Development", 1, 2)
        client.get_best_subtitle.assert_any_call("Arrested Development", 1, 3)

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.tmdb_client.requests.get")
    @patch("app.services.config_service.get_config_sync")
    def test_workflow_with_tmdb_variations(
        self, mock_config_sync, mock_requests, mock_addic7ed, tmp_path
    ):
        """Test workflow uses TMDB variation generation for difficult names."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.tmdb_api_key = "test_key_12345"
        mock_config_sync.return_value = mock_config

        # First TMDB search fails, second succeeds (variation)
        mock_requests.side_effect = [
            Mock(status_code=200, json=lambda: {"results": []}),  # First attempt fails
            Mock(
                status_code=200, json=lambda: TMDB_SEARCH_ARRESTED_DEVELOPMENT
            ),  # Variation succeeds
            Mock(status_code=200, json=lambda: TMDB_SEASON_DETAILS_S01_3EP),
        ]

        client = Mock()
        mock_addic7ed.return_value = client

        mock_subtitle = Mock()
        client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("Subtitle")
            return save_path

        client.download_subtitle.side_effect = download_side_effect

        # Execute - should succeed via variation
        result = download_subtitles("Arrested Development Complete Series", 1)

        assert result["total_episodes"] == 3
        # Verify multiple TMDB requests were made (original + variations)
        assert mock_requests.call_count >= 2


@pytest.mark.integration
class TestCacheBehavior:
    """Tests for cache hit/miss scenarios."""

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.tmdb_client.requests.get")
    @patch("app.services.config_service.get_config_sync")
    def test_cache_hit_skips_download(
        self,
        mock_config_sync,
        mock_requests,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that cached files aren't re-downloaded."""
        # Pre-populate cache
        show_dir = tmp_path / "data" / "Breaking Bad"
        show_dir.mkdir(parents=True)
        for ep in range(1, 4):
            (show_dir / f"Breaking Bad - S01E{ep:02d}.srt").write_text(f"Cached {ep}")

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.tmdb_api_key = "test_key_12345"
        mock_config_sync.return_value = mock_config

        # Mock TMDB
        mock_requests.side_effect = [
            Mock(
                status_code=200,
                json=lambda: {"results": [{"id": 1396, "name": "Breaking Bad"}]},
            ),
            Mock(
                status_code=200,
                json=lambda: {
                    "episodes": [{"episode_number": i} for i in range(1, 4)]
                },
            ),
        ]

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # Execute
        result = download_subtitles("Breaking Bad", 1)

        # Verify all marked as cached
        assert all(ep["status"] == "cached" for ep in result["episodes"])
        assert all(ep["source"] == "cache" for ep in result["episodes"])

        # Verify neither scraper was called
        assert addic7ed_client.get_best_subtitle.call_count == 0
        assert os_client.get_best_subtitle.call_count == 0

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.tmdb_client.requests.get")
    @patch("app.services.config_service.get_config_sync")
    def test_partial_cache_downloads_missing_only(
        self,
        mock_config_sync,
        mock_requests,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test that only missing episodes are downloaded."""
        # Pre-populate cache with episode 1 only
        show_dir = tmp_path / "data" / "Test Show"
        show_dir.mkdir(parents=True)
        (show_dir / "Test Show - S01E01.srt").write_text("Cached episode 1")

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.tmdb_api_key = "test_key_12345"
        mock_config_sync.return_value = mock_config

        # Mock TMDB
        mock_requests.side_effect = [
            Mock(
                status_code=200,
                json=lambda: {"results": [{"id": 123, "name": "Test Show"}]},
            ),
            Mock(
                status_code=200,
                json=lambda: {
                    "episodes": [{"episode_number": i} for i in range(1, 4)]
                },
            ),
        ]

        # Mock Addic7ed
        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("Downloaded")
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

        # Verify only 2 download calls made (for episodes 2 and 3)
        assert addic7ed_client.get_best_subtitle.call_count == 2

        # Verify files exist
        assert (show_dir / "Test Show - S01E01.srt").read_text() == "Cached episode 1"
        assert (show_dir / "Test Show - S01E02.srt").read_text() == "Downloaded"
        assert (show_dir / "Test Show - S01E03.srt").read_text() == "Downloaded"

    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.tmdb_client.requests.get")
    @patch("app.services.config_service.get_config_sync")
    def test_mixed_results_cache_download_notfound(
        self,
        mock_config_sync,
        mock_requests,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        """Test workflow with mixed cache hits, downloads, and not found."""
        # Pre-populate cache with episode 1
        show_dir = tmp_path / "data" / "Test Show"
        show_dir.mkdir(parents=True)
        (show_dir / "Test Show - S01E01.srt").write_text("Cached")

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.tmdb_api_key = "test_key_12345"
        mock_config_sync.return_value = mock_config

        # Mock TMDB
        mock_requests.side_effect = [
            Mock(
                status_code=200,
                json=lambda: {"results": [{"id": 123, "name": "Test Show"}]},
            ),
            Mock(
                status_code=200,
                json=lambda: {
                    "episodes": [{"episode_number": i} for i in range(1, 4)]
                },
            ),
        ]

        # Mock Addic7ed: episode 2 found, episode 3 not found
        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        def get_best_side_effect(show, season, episode):
            if episode == 2:
                mock_sub = Mock()
                mock_sub.language = "English"
                return mock_sub
            else:
                return None  # Episode 3 not found

        addic7ed_client.get_best_subtitle.side_effect = get_best_side_effect

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("Downloaded")
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        # Mock OpenSubtitles: also doesn't have episode 3
        os_client = Mock()
        mock_opensubtitles.return_value = os_client
        os_client.get_best_subtitle.return_value = None

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify mixed results
        statuses = [ep["status"] for ep in result["episodes"]]
        assert statuses[0] == "cached"
        assert statuses[1] == "downloaded"
        assert statuses[2] == "not_found"


@pytest.mark.integration
class TestErrorHandling:
    """Tests for error handling in the workflow."""

    @patch("app.matcher.testing_service.fetch_show_id")
    def test_tmdb_show_not_found_raises_error(self, mock_show_id):
        """Test that workflow raises ValueError when show not found on TMDB."""
        mock_show_id.return_value = None

        with pytest.raises(ValueError, match="Could not find show"):
            download_subtitles("Completely Fake Show", 1)

    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    def test_no_episodes_found_raises_error(self, mock_show_id, mock_season):
        """Test that workflow raises ValueError when no episodes found."""
        mock_show_id.return_value = "123"
        mock_season.return_value = 0

        with pytest.raises(ValueError, match="No episodes found"):
            download_subtitles("Test Show", 99)
