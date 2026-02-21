from unittest.mock import Mock, patch

import pytest

from app.matcher.testing_service import download_subtitles


@pytest.mark.unit
class TestCanonicalNameFix:
    @patch("app.matcher.testing_service.OpenSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_download_uses_canonical_name(
        self,
        mock_config_sync,
        mock_show_id,
        mock_show_details,
        mock_season,
        mock_addic7ed,
        mock_opensubtitles,
        tmp_path,
    ):
        # Setup Config
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        # 1. Mock TMDB ID fetch (returns an ID for the raw name)
        mock_show_id.return_value = "12345"

        # 2. Mock Show Details (returns the CANONICAL name)
        mock_show_details.return_value = {"name": "South Park", "id": 12345}

        # 3. Mock Season Details
        mock_season.return_value = 1  # 1 episode

        # 4. Mock Scrapers
        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle
        addic7ed_client.download_subtitle.return_value = str(tmp_path / "South Park - S01E01.srt")

        os_client = Mock()
        mock_opensubtitles.return_value = os_client

        # EXECUTE with RAW name
        result = download_subtitles("Southpark6", 1)

        # VERIFY

        # Ensure show details were fetched
        mock_show_details.assert_called_with("12345")

        # Ensure scrapers were called with CANONICAL name
        addic7ed_client.get_best_subtitle.assert_called_with("South Park", 1, 1)

        # Ensure result contains canonical name
        assert result["show_name"] == "South Park"
