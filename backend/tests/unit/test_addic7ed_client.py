"""Unit tests for Addic7ed client."""

from unittest.mock import Mock, patch

import pytest

from app.matcher.addic7ed_client import Addic7edClient, SubtitleEntry


@pytest.mark.unit
class TestSubtitleEntry:
    """Tests for SubtitleEntry dataclass."""

    def test_subtitle_entry_creation(self):
        """Test creating a SubtitleEntry."""
        entry = SubtitleEntry(
            language="English",
            version="WEB.x264-TBS",
            downloads=1500,
            download_url="http://example.com/subtitle.srt",
            uploader="TestUser",
            is_hearing_impaired=False,
        )

        assert entry.language == "English"
        assert entry.version == "WEB.x264-TBS"
        assert entry.downloads == 1500
        assert entry.download_url == "http://example.com/subtitle.srt"
        assert entry.uploader == "TestUser"
        assert entry.is_hearing_impaired is False


@pytest.mark.unit
class TestAddic7edClient:
    """Tests for Addic7ed scraping client."""

    def test_client_initialization(self):
        """Test client initializes with correct headers."""
        client = Addic7edClient()

        assert "User-Agent" in client.session.headers
        assert client.BASE_URL == "https://www.addic7ed.com"

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_search_show_success(self, mock_get):
        """Test successful show search."""
        client = Addic7edClient()
        show_results = client.search_show("Arrested Development")

        assert isinstance(show_results, list)
        assert len(show_results) >= 1
        assert show_results[0]["name"] == "Arrested Development"

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_search_show_not_found(self, mock_get):
        """Test show search returns list even when not found (Addic7ed limitation)."""
        client = Addic7edClient()
        show_results = client.search_show("Nonexistent Show")

        # search_show always returns a list with the show name
        # since Addic7ed doesn't have a proper search API
        assert isinstance(show_results, list)
        assert len(show_results) >= 1

    def test_get_best_subtitle_selects_highest_downloads(self):
        """Test that best subtitle is selected by download count."""
        client = Addic7edClient()

        # Mock the _get method directly
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "http://example.com/referer"
        mock_response.text = """
        <html>
        <body>
            <table class="tabel95">
                <tr class="epeven">
                    <td class="language">English</td>
                    <td class="NewsTitle">1x1 - Pilot</td>
                    <td class="newsDate">WEB.x264-TBS</td>
                    <td><a class="buttonDownload" href="/updated/1/123/0">Download</a></td>
                    <td>500 Downloads</td>
                    <td>User1</td>
                </tr>
                <tr class="epeven">
                    <td class="language">English</td>
                    <td class="NewsTitle">1x1 - Pilot</td>
                    <td class="newsDate">WEB.x264-KILLERS</td>
                    <td><a class="buttonDownload" href="/updated/1/124/0">Download</a></td>
                    <td>2000 Downloads</td>
                    <td>User2</td>
                </tr>
            </table>
        </body>
        </html>
        """

        client._get = Mock(return_value=mock_response)

        best_sub = client.get_best_subtitle("Arrested Development", 1, 1, "English")

        assert best_sub is not None
        assert best_sub.downloads == 2000
        assert "124" in best_sub.download_url

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_get_best_subtitle_no_match_returns_none(self, mock_get):
        """Test that None is returned when no subtitle matches."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "http://example.com/referer"
        mock_response.text = "<html><body><p>No subtitles</p></body></html>"
        mock_get.return_value = mock_response

        client = Addic7edClient()
        best_sub = client.get_best_subtitle("Test Show", 1, 1, "English")

        assert best_sub is None

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_download_subtitle_success(self, mock_get, tmp_path):
        """Test successful subtitle download."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"1\n00:00:00,000 --> 00:00:02,000\nTest subtitle\n"
        mock_response.headers = {"content-type": "text/plain"}
        mock_get.return_value = mock_response

        subtitle = SubtitleEntry(
            language="English",
            version="WEB",
            downloads=1000,
            download_url="http://example.com/sub/123",
        )

        client = Addic7edClient()
        save_path = tmp_path / "test_subtitle.srt"
        result = client.download_subtitle(subtitle, save_path)

        assert result == save_path
        assert save_path.exists()
        assert save_path.read_bytes() == b"1\n00:00:00,000 --> 00:00:02,000\nTest subtitle\n"

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_download_subtitle_creates_directories(self, mock_get, tmp_path):
        """Test that download creates parent directories if needed."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Subtitle content"
        mock_response.headers = {"content-type": "text/plain"}
        mock_get.return_value = mock_response

        subtitle = SubtitleEntry(
            language="English",
            version="WEB",
            downloads=1000,
            download_url="http://example.com/sub/123",
        )

        client = Addic7edClient()
        save_path = tmp_path / "nested" / "dir" / "subtitle.srt"
        result = client.download_subtitle(subtitle, save_path)

        assert result == save_path
        assert save_path.exists()
        assert save_path.parent.exists()

    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_download_subtitle_api_error_returns_none(self, mock_get, tmp_path):
        """Test that download returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        subtitle = SubtitleEntry(
            language="English",
            version="WEB",
            downloads=1000,
            download_url="http://example.com/sub/123",
        )

        client = Addic7edClient()
        save_path = tmp_path / "subtitle.srt"
        result = client.download_subtitle(subtitle, save_path)

        assert result is None
        assert not save_path.exists()

    @patch("app.matcher.addic7ed_client.time.sleep")
    @patch("app.matcher.addic7ed_client.requests.Session.get")
    def test_rate_limiting_applied(self, mock_get, mock_sleep):
        """Test that rate limiting is applied between requests."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html><body></body></html>"
        mock_get.return_value = mock_response

        client = Addic7edClient()

        # Make multiple requests
        client._get("/test1")
        client._get("/test2")

        # Verify sleep was called for rate limiting
        assert mock_sleep.called


@pytest.mark.unit
class TestShowNameAliases:
    """Tests for show name alias mapping."""

    def test_alias_mapping_exists(self):
        """Test that SHOW_NAME_ALIASES dict is available."""
        from app.matcher.addic7ed_client import SHOW_NAME_ALIASES

        assert isinstance(SHOW_NAME_ALIASES, dict)
        # Check some known aliases
        assert "The Office" in SHOW_NAME_ALIASES
        assert SHOW_NAME_ALIASES["The Office"] == "The Office (US)"
