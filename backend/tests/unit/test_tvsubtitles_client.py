"""Unit tests for the TVsubtitles.net subtitle client.

These tests use captured-shape HTML/JS — the *live* validation that the
shapes still match the deployed site lives in
``backend/tests/live/test_tvsubtitles_live.py`` and is opt-in via
``ENGRAM_LIVE_PROVIDER_TESTS=1``.
"""

import io
import zipfile
from unittest.mock import Mock, patch

import pytest

from app.matcher.tvsubtitles_client import (
    TVSubtitlesClient,
    TVSubtitlesSubtitle,
    _extract_download_page_url,
    _extract_zip_path_from_js,
    _find_episode_page,
    _parse_first_show_id,
    _parse_subtitle_candidates,
)

# Search page: a POST to /search1.php returns the homepage chrome plus
# a list of show hits. Only ``/tvshow-{id}.html`` (no trailing -season)
# is the canonical show link.
_SEARCH_HTML = """
<html><body>
  <a href="/tvshow-133.html">Breaking Bad</a>
  <a href="/tvshow-133-1.html">season 1</a>
  <a href="/tvshow-789.html">Better Call Saul</a>
</body></html>
"""

# Season page: each row has a per-row counter cell, an episode designator
# in ``{S}x{EE}`` form, and an /episode-{n}.html link.
_SEASON_HTML = """
<html><body>
<table>
  <tr><td>15</td><td>1x01</td><td><a href="/episode-8080.html">Pilot</a></td></tr>
  <tr><td>15</td><td>1x02</td><td><a href="/episode-8081.html">Cat's in the Bag</a></td></tr>
  <tr><td>15</td><td>1x03</td><td><a href="/episode-8082.html">And the Bag's in the River</a></td></tr>
</table>
</body></html>
"""

# Episode page: each subtitle entry is an anchor with an inner div whose
# title attribute identifies the language. English-only here.
_EPISODE_HTML = """
<html><body>
<table>
  <tr><td>
    <a href="/subtitle-2001.html">
      <div title="Download English subtitles" class="subtitlen">WEB-DL.x264 — 1250 downloads</div>
    </a>
  </td></tr>
  <tr><td>
    <a href="/subtitle-2002.html">
      <div title="Download Spanish subtitles" class="subtitlen">HDTV — 400 downloads</div>
    </a>
  </td></tr>
</table>
</body></html>
"""

# Subtitle landing page: contains a button-anchor to /download-{id}.html
_SUBTITLE_PAGE_HTML = """
<html><body>
  <a href="/download-2001.html">Click here to download</a>
</body></html>
"""

# Download stub: TVsubtitles assembles the ZIP path from a few var
# fragments via JS to discourage simple scraping. The fragments are
# captured in source order; concatenated they form a ``files/...zip``
# path.
_DOWNLOAD_STUB_JS = """
<center><br /><br /><br /><div id="linkPlace"><b>Wait: <span id="timeNumer">0</span> sec ...</b></div></center>
<script type="text/javascript">
  var s1= 'fil';
  var s2= 'es/B';
  var s3= 're';
  var s4= 'aking Bad_1x01_DVDRip.ORPHEUS.en.zip';
  document.location = s1+s2+s3+s4;
</script>
"""


def _zip_with_srt(content: str = "subtitle content") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("episode.srt", content)
    return buf.getvalue()


@pytest.mark.unit
class TestParseFirstShowId:
    def test_returns_base_show_id_not_season(self):
        """A search result page contains BOTH ``/tvshow-{id}.html`` and
        ``/tvshow-{id}-{season}.html`` links from sidebar chrome. The
        base show URL is what we want; the season variant would
        construct a malformed second-season URL when re-templated."""
        assert _parse_first_show_id(_SEARCH_HTML) == 133

    def test_returns_none_when_no_match(self):
        assert _parse_first_show_id("<html><body><a href='/about'>x</a></body></html>") is None


@pytest.mark.unit
class TestFindEpisodePage:
    def test_returns_url_for_matched_episode(self):
        url = _find_episode_page(
            _SEASON_HTML, season=1, episode=2, base_url="https://tvsubtitles.net"
        )
        assert url == "https://tvsubtitles.net/episode-8081.html"

    def test_returns_none_for_missing_episode(self):
        assert _find_episode_page(_SEASON_HTML, 1, 99, base_url="x") is None

    def test_does_not_match_per_row_counter(self):
        """The first cell of each row is a per-row counter (often 15 or
        similar) — it must NOT be mistaken for the episode number."""
        url = _find_episode_page(
            _SEASON_HTML, season=1, episode=15, base_url="https://tvsubtitles.net"
        )
        assert url is None


@pytest.mark.unit
class TestParseSubtitleCandidates:
    def test_returns_only_english_via_title_attr(self):
        results = _parse_subtitle_candidates(
            _EPISODE_HTML, base_url="https://tvsubtitles.net", language="en"
        )
        assert len(results) == 1
        assert results[0].subtitle_page_url.endswith("/subtitle-2001.html")
        assert results[0].downloads == 1250


@pytest.mark.unit
class TestExtractDownloadPageUrl:
    def test_extracts_download_anchor(self):
        url = _extract_download_page_url(_SUBTITLE_PAGE_HTML, base_url="https://tvsubtitles.net")
        assert url == "https://tvsubtitles.net/download-2001.html"

    def test_returns_none_when_absent(self):
        assert _extract_download_page_url("<html></html>", base_url="x") is None


@pytest.mark.unit
class TestExtractZipPathFromJs:
    def test_concatenates_var_fragments_in_order(self):
        """The fragments ``s1..s4`` are assigned in source order; the
        regex captures them in that same order. Concatenating yields the
        ``files/Breaking Bad_1x01_DVDRip.ORPHEUS.en.zip`` path."""
        assert _extract_zip_path_from_js(_DOWNLOAD_STUB_JS) == (
            "files/Breaking Bad_1x01_DVDRip.ORPHEUS.en.zip"
        )

    def test_falls_back_to_inline_path(self):
        """If TVsubtitles ever simplifies the JS to a plain
        ``document.location = "files/foo.zip"``, we still recover it."""
        simple = """<script>document.location = "files/foo.en.zip";</script>"""
        assert _extract_zip_path_from_js(simple) == "files/foo.en.zip"

    def test_returns_none_when_no_zip_reference(self):
        assert _extract_zip_path_from_js("<html>no js here</html>") is None


@pytest.mark.unit
class TestGetBestSubtitle:
    def test_walks_search_to_episode_to_subtitle(self):
        """End-to-end walk: search (POST) → season → episode → first
        English subtitle entry."""
        client = TVSubtitlesClient()
        with (
            patch.object(client, "_post") as mock_post,
            patch.object(client, "_get") as mock_get,
        ):
            mock_post.return_value = Mock(text=_SEARCH_HTML, raise_for_status=Mock())
            mock_get.side_effect = [
                Mock(text=_SEASON_HTML, raise_for_status=Mock()),
                Mock(text=_EPISODE_HTML, raise_for_status=Mock()),
            ]
            result = client.get_best_subtitle("Breaking Bad", season=1, episode=2)
        assert result is not None
        assert result.downloads == 1250
        assert result.subtitle_page_url.endswith("/subtitle-2001.html")

    def test_returns_none_when_show_not_found(self):
        client = TVSubtitlesClient()
        with patch.object(client, "_post") as mock_post:
            mock_post.return_value = Mock(text="<html></html>", raise_for_status=Mock())
            assert client.get_best_subtitle("Nonexistent Show", 1, 1) is None

    def test_show_id_cached_across_calls(self):
        """Per-instance _show_id_cache prevents a second /search1.php POST
        when looking up additional episodes of the same show."""
        client = TVSubtitlesClient()
        with (
            patch.object(client, "_post") as mock_post,
            patch.object(client, "_get") as mock_get,
        ):
            mock_post.return_value = Mock(text=_SEARCH_HTML, raise_for_status=Mock())
            mock_get.side_effect = [
                Mock(text=_SEASON_HTML, raise_for_status=Mock()),
                Mock(text=_EPISODE_HTML, raise_for_status=Mock()),
                # Second episode lookup must NOT re-POST /search1.php.
                Mock(text=_SEASON_HTML, raise_for_status=Mock()),
                Mock(text=_EPISODE_HTML, raise_for_status=Mock()),
            ]
            client.get_best_subtitle("Breaking Bad", 1, 2)
            client.get_best_subtitle("Breaking Bad", 1, 3)
        # 1 POST + 2×(season GET + episode GET) = 1 + 4 = 5 total.
        assert mock_post.call_count == 1
        assert mock_get.call_count == 4


@pytest.mark.unit
class TestDownloadSubtitle:
    def test_fast_path_zip_returned_directly(self, tmp_path):
        """The normal flow: visiting the subtitle page sets cookies that
        cause /download-{id}.html to stream the ZIP directly. We detect
        this via the ZIP magic bytes ``PK\\x03\\x04`` on the response
        body and skip the JS-redirect dance entirely."""
        client = TVSubtitlesClient()
        subtitle = TVSubtitlesSubtitle(
            language="en",
            release="WEB",
            downloads=100,
            subtitle_page_url="https://www.tvsubtitles.net/subtitle-2001.html",
        )
        with patch.object(client, "_get") as mock_get:
            mock_get.side_effect = [
                Mock(text=_SUBTITLE_PAGE_HTML, raise_for_status=Mock()),
                Mock(content=_zip_with_srt("hello"), raise_for_status=Mock()),
            ]
            save_path = tmp_path / "x.srt"
            assert client.download_subtitle(subtitle, save_path) == save_path
            assert save_path.read_text() == "hello"

    def test_slow_path_js_redirect_then_zip(self, tmp_path):
        """Fallback flow: if the download endpoint returns HTML (cookie
        was lost / Referer rejected), parse the JS-redirect stub and
        fetch the inner ``/files/...zip`` URL in a second hop."""
        client = TVSubtitlesClient()
        subtitle = TVSubtitlesSubtitle(
            language="en",
            release="WEB",
            downloads=100,
            subtitle_page_url="https://www.tvsubtitles.net/subtitle-2001.html",
        )
        with patch.object(client, "_get") as mock_get:
            mock_get.side_effect = [
                Mock(text=_SUBTITLE_PAGE_HTML, raise_for_status=Mock()),
                Mock(
                    content=_DOWNLOAD_STUB_JS.encode(),
                    text=_DOWNLOAD_STUB_JS,
                    raise_for_status=Mock(),
                ),
                Mock(content=_zip_with_srt("from zip"), raise_for_status=Mock()),
            ]
            save_path = tmp_path / "x.srt"
            assert client.download_subtitle(subtitle, save_path) == save_path
            assert save_path.read_text() == "from zip"

    def test_returns_none_when_response_is_neither_zip_nor_js(self, tmp_path):
        client = TVSubtitlesClient()
        subtitle = TVSubtitlesSubtitle(
            language="en",
            release="WEB",
            downloads=100,
            subtitle_page_url="https://www.tvsubtitles.net/subtitle-2001.html",
        )
        with patch.object(client, "_get") as mock_get:
            mock_get.side_effect = [
                Mock(text=_SUBTITLE_PAGE_HTML, raise_for_status=Mock()),
                Mock(
                    content=b"<html>no js with zip ref</html>",
                    text="<html>no js with zip ref</html>",
                    raise_for_status=Mock(),
                ),
            ]
            assert client.download_subtitle(subtitle, tmp_path / "x.srt") is None
