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

# Season page: VERBATIM-captured shape from tvshow-110-1.html (How I Met
# Your Mother S1, 2026-05-20). Two structural facts the old hand-written
# fixture missed and which broke _find_episode_page in production:
#   1. The episode table is wrapped in OUTER tables (``table4`` → ``table5``),
#      so a recursive ``find_all('td')`` on the outer <tr> collects EVERY
#      episode's cells at once.
#   2. Episodes are listed in DESCENDING order (1x22 first), and the first
#      column IS the ``{S}x{EE}`` designator (no separate counter cell).
# Together these made the old parser match the wrapper row for any target
# and return the first episode link (1x22) regardless of the request.
# The ``episode-7289-gr.html`` link in the flags cell guards the href regex
# against language-suffixed episode links.
_SEASON_HTML = """
<html><body>
<table id="table4" align="center" width=100%><tr><td>
<table id="table5">
<tr align="middle"><th><b>#</b></th><th><b>Episode</b></th><th><b>Amount</b></th><th><b>Subtitles</b></th></tr>
<tr align="middle" bgcolor="#ffffff">
<td>1x22</td>
<td align=left style="padding: 0 4px;"><a href="episode-7310.html"><b>Come On</b></a></td>
<td>10</td>
<td><nobr><a href="subtitle-8548.html"><img src="images/flags/en.gif" alt="en" border=0></a></nobr></td>
</tr>
<tr align="middle" bgcolor="#ffffff">
<td>1x02</td>
<td align=left style="padding: 0 4px;"><a href="episode-7290.html"><b>Purple Giraffe</b></a></td>
<td>10</td>
<td><nobr><a href="subtitle-8528.html"><img src="images/flags/en.gif" alt="en" border=0></a></nobr></td>
</tr>
<tr align="middle" bgcolor="#ffffff">
<td>1x01</td>
<td align=left style="padding: 0 4px;"><a href="episode-7289.html"><b>Pilot</b></a></td>
<td>11</td>
<td><nobr><a href="subtitle-8527.html"><img src="images/flags/en.gif" alt="en" border=0></a>&nbsp;<a href="episode-7289-gr.html"><img src="images/flags/gr.gif" alt="gr" border=0></a></nobr></td>
</tr>
</table>
</td></tr></table>
</body></html>
"""

# Episode page: VERBATIM-captured shape from episode-8600.html (Breaking
# Bad, 2026-05-20). Each subtitle is ``<a href="/subtitle-N.html"><div
# class="subtitlen">…</div></a>`` and all anchors are SIBLINGS inside one
# ``left_articles`` container — there are NO per-entry <tr> rows. The real
# download count is a bare integer in ``<p title="downloaded">`` (there is
# no literal "downloads" text anywhere), and rip/release tags live in
# labelled <p> cells / the <h5>. Two English entries (different releases &
# counts) plus one Spanish entry exercise language filtering, per-entry
# download parsing, and download-descending selection.
_EPISODE_HTML = """
<html><body>
<div id="content"><div class="left"><div class="left_articles">
<h2>Breaking Bad 1x07</h2>
<b>Subtitles for this episode:</b>
<div style="clear:both; padding-top:10px;"><span style="color:#666666"><b>English  subtitles</b></span></div>
<a href="/subtitle-11668.html"><div title="Download English subtitles"  class="subtitlen" >
<div style="float:right; margin:0 2px;"><span style="color:black; font-weight:bold"><span style="color:red">9</span>/<span style="color:green">34</span></span></div>
    <h5 style="width:600px;"><img src="images/flags/en.gif" width="18" height="12" alt="" border=0 hspace=4 align=absmiddle>Breaking Bad 1x07 (DSR.LOL)</h5>
    <p style="width:110px; margin-left:50px" alt="rip" title="rip"><img src="images/rip.gif" width="16" height="16" alt="rip" title="rip" border=0 hspace=4 align="absmiddle">
	DSR</p>
	<p style="width:110px;" alt="release" title="release"><img src="images/release.gif" width="16" height="16" alt="release" title="release" border=0 hspace=4 align="absmiddle">
	LOL</p>
    <p style="width:70px;line-height: 10px;margin-right:40px;\\ alt="uploaded" title="uploaded"><img src="images/time.png" width="16" height="16" alt="uploaded" title="uploaded" border=0 vspace=4 hspace=4 align="left">
	<small style="margin:0; padding:0; display:inline; line-height: 10px;">02.11.09 10:49:44</small></p>
	<p style="width:120px; line-height: 10px;" alt="author" title="author"><nobr><img src="images/user.png" width="16" height="16" alt="author" title="author" border=0 hspace=2 align="absmiddle">
	<small style="margin:0; padding:0; display:inline; line-height: 10px;">&nbsp;</small></nobr></p>
	<p style="width:100px;" alt="downloaded" title="downloaded"><img src="images/downloads.png" width="16" height="16" alt="downloaded" title="downloaded" border=0 hspace=4 align="absmiddle">
	29041</p>
</div></a>
<a href="/subtitle-51323.html"><div title="Download English subtitles"  class="subtitlen" >
<div style="float:right; margin:0 2px;"><span style="color:black; font-weight:bold"><span style="color:red">0</span>/<span style="color:green">6</span></span></div>
    <h5 style="width:600px;"><img src="images/flags/en.gif" width="18" height="12" alt="" border=0 hspace=4 align=absmiddle>Breaking Bad 1x07 (DVDRip.ORPHEUS)</h5>
    <p style="width:110px; margin-left:50px" alt="rip" title="rip"><img src="images/rip.gif" width="16" height="16" alt="rip" title="rip" border=0 hspace=4 align="absmiddle">
	DVDRip</p>
	<p style="width:110px;" alt="release" title="release"><img src="images/release.gif" width="16" height="16" alt="release" title="release" border=0 hspace=4 align="absmiddle">
	ORPHEUS</p>
    <p style="width:70px;line-height: 10px;margin-right:40px;\\ alt="uploaded" title="uploaded"><img src="images/time.png" width="16" height="16" alt="uploaded" title="uploaded" border=0 vspace=4 hspace=4 align="left">
	<small style="margin:0; padding:0; display:inline; line-height: 10px;">02.11.09 10:51:16</small></p>
	<p style="width:120px; line-height: 10px;" alt="author" title="author"><nobr><img src="images/user.png" width="16" height="16" alt="author" title="author" border=0 hspace=2 align="absmiddle">
	<small style="margin:0; padding:0; display:inline; line-height: 10px;">&nbsp;</small></nobr></p>
	<p style="width:100px;" alt="downloaded" title="downloaded"><img src="images/downloads.png" width="16" height="16" alt="downloaded" title="downloaded" border=0 hspace=4 align="absmiddle">
	30749</p>
</div></a>
<div style="clear:both; padding-top:10px;"><span style="color:#666666"><b>Spanish  subtitles</b></span></div>
<a href="/subtitle-72826.html"><div title="Download Spanish subtitles"  class="subtitlen" >
<div style="float:right; margin:0 2px;"><span style="color:black; font-weight:bold"><span style="color:red">1</span>/<span style="color:green">3</span></span></div>
    <h5 style="width:600px;"><img src="images/flags/es.gif" width="18" height="12" alt="" border=0 hspace=4 align=absmiddle>Breaking Bad 1x07 </h5>
    <p style="width:110px; margin-left:50px" alt="rip" title="rip"><img src="images/rip.gif" width="16" height="16" alt="rip" title="rip" border=0 hspace=4 align="absmiddle">
	</p>
	<p style="width:110px;" alt="release" title="release"><img src="images/release.gif" width="16" height="16" alt="release" title="release" border=0 hspace=4 align="absmiddle">
	</p>
	<p style="width:100px;" alt="downloaded" title="downloaded"><img src="images/downloads.png" width="16" height="16" alt="downloaded" title="downloaded" border=0 hspace=4 align="absmiddle">
	8104</p>
</div></a>
</div></div></div>
</body></html>
"""

# Cross-language episode shape from episode-7310.html (How I Met Your
# Mother, 2026-05-20). The English entry has NO release tag (empty rip/
# release cells, no parenthetical in its <h5>), and it sits immediately
# before a Russian entry whose <h5> and rip cell say "HDTV". This is the
# exact arrangement that made the old shared-container release parser
# attribute the Russian "(HDTV)" tag to the English subtitle.
_EPISODE_HTML_CROSS_LANG = """
<html><body>
<div id="content"><div class="left"><div class="left_articles">
<h2>How I Met Your Mother 1x22</h2>
<div style="clear:both; padding-top:10px;"><span style="color:#666666"><b>English  subtitles</b></span></div>
<a href="/subtitle-8548.html"><div title="Download English subtitles"  class="subtitlen" >
<div style="float:right; margin:0 2px;"><span style="color:black; font-weight:bold"><span style="color:red">12</span>/<span style="color:green">5</span></span></div>
    <h5 style="width:600px;"><img src="images/flags/en.gif" width="18" height="12" alt="" border=0 hspace=4 align=absmiddle>How I Met Your Mother 1x22 </h5>
    <p style="width:110px; margin-left:50px" alt="rip" title="rip"><img src="images/rip.gif" width="16" height="16" alt="rip" title="rip" border=0 hspace=4 align="absmiddle">
	</p>
	<p style="width:110px;" alt="release" title="release"><img src="images/release.gif" width="16" height="16" alt="release" title="release" border=0 hspace=4 align="absmiddle">
	</p>
	<p style="width:100px;" alt="downloaded" title="downloaded"><img src="images/downloads.png" width="16" height="16" alt="downloaded" title="downloaded" border=0 hspace=4 align="absmiddle">
	32167</p>
</div></a>
<div style="clear:both; padding-top:10px;"><span style="color:#666666"><b>Russian  subtitles</b></span></div>
<a href="/subtitle-23772.html"><div title="Download Russian subtitles"  class="subtitlen" >
<div style="float:right; margin:0 2px;"><span style="color:black; font-weight:bold"><span style="color:red">0</span>/<span style="color:green">0</span></span></div>
    <h5 style="width:600px;"><img src="images/flags/ru.gif" width="18" height="12" alt="" border=0 hspace=4 align=absmiddle>How I Met Your Mother 1x22 (HDTV)</h5>
    <p style="width:110px; margin-left:50px" alt="rip" title="rip"><img src="images/rip.gif" width="16" height="16" alt="rip" title="rip" border=0 hspace=4 align="absmiddle">
	HDTV</p>
	<p style="width:110px;" alt="release" title="release"><img src="images/release.gif" width="16" height="16" alt="release" title="release" border=0 hspace=4 align="absmiddle">
	</p>
	<p style="width:100px;" alt="downloaded" title="downloaded"><img src="images/downloads.png" width="16" height="16" alt="downloaded" title="downloaded" border=0 hspace=4 align="absmiddle">
	1793</p>
</div></a>
</div></div></div>
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
            _SEASON_HTML, season=1, episode=1, base_url="https://tvsubtitles.net"
        )
        assert url == "https://tvsubtitles.net/episode-7289.html"

    def test_returns_none_for_missing_episode(self):
        assert _find_episode_page(_SEASON_HTML, 1, 99, base_url="x") is None

    def test_nested_wrapper_tables_do_not_collapse_to_first_episode(self):
        """The season table is wrapped in outer tables, so the outermost
        <tr> transitively contains every episode's cells. A recursive cell
        scan matched that wrapper row for ANY target and returned the first
        episode link (1x22) — so every requested episode resolved to the
        same wrong page. Each target must resolve to its OWN episode link."""
        first = _find_episode_page(_SEASON_HTML, 1, 1, base_url="https://x")
        mid = _find_episode_page(_SEASON_HTML, 1, 2, base_url="https://x")
        assert first == "https://x/episode-7289.html"
        assert mid == "https://x/episode-7290.html"
        # If the wrapper-row bug regressed, both would collapse to 1x22.
        assert first != "https://x/episode-7310.html"
        assert mid != "https://x/episode-7310.html"

    def test_ignores_language_suffixed_episode_links(self):
        """The flags cell can carry ``episode-{n}-{lang}.html`` links; the
        href regex must require ``.html`` right after the digits so those
        don't shadow the canonical episode link."""
        url = _find_episode_page(_SEASON_HTML, 1, 1, base_url="https://x")
        assert url == "https://x/episode-7289.html"


@pytest.mark.unit
class TestParseSubtitleCandidates:
    def test_returns_only_english_via_title_attr(self):
        results = _parse_subtitle_candidates(
            _EPISODE_HTML, base_url="https://tvsubtitles.net", language="en"
        )
        # Two English entries are kept; the Spanish entry is filtered out.
        assert {r.subtitle_page_url for r in results} == {
            "https://tvsubtitles.net/subtitle-11668.html",
            "https://tvsubtitles.net/subtitle-51323.html",
        }

    def test_parses_each_entrys_own_download_count(self):
        """The count is a bare integer in the entry's ``<p title="downloaded">``
        cell. The old ``(\\d+)\\s*downloads?`` regex found no "downloads"
        text on the page and returned 0 for every entry, making the
        download-descending selection arbitrary."""
        results = _parse_subtitle_candidates(_EPISODE_HTML, base_url="https://x", language="en")
        by_file = {r.subtitle_page_url.rsplit("/", 1)[-1]: r for r in results}
        assert by_file["subtitle-11668.html"].downloads == 29041
        assert by_file["subtitle-51323.html"].downloads == 30749

    def test_release_uses_structured_rip_and_release_cells(self):
        results = _parse_subtitle_candidates(_EPISODE_HTML, base_url="https://x", language="en")
        by_file = {r.subtitle_page_url.rsplit("/", 1)[-1]: r for r in results}
        assert by_file["subtitle-11668.html"].release == "DSR.LOL"
        assert by_file["subtitle-51323.html"].release == "DVDRip.ORPHEUS"

    def test_release_is_read_from_the_same_entry_not_a_neighbour(self):
        """Regression: the English entry has empty rip/release cells and no
        parenthetical, while the adjacent Russian entry is tagged "HDTV".
        The old parser walked up to the shared ``left_articles`` container
        (no per-entry <tr> exists) and grabbed the Russian tag for the
        English subtitle. Each entry's data must come from within its own
        anchor."""
        results = _parse_subtitle_candidates(
            _EPISODE_HTML_CROSS_LANG, base_url="https://x", language="en"
        )
        assert len(results) == 1
        english = results[0]
        assert english.subtitle_page_url.endswith("/subtitle-8548.html")
        assert english.downloads == 32167
        assert "HDTV" not in english.release


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
        """End-to-end walk: search (POST) → season → episode → English
        subtitle entries. Of the two English entries the higher
        download count (30749, subtitle-51323) must win the selection."""
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
            result = client.get_best_subtitle("Breaking Bad", season=1, episode=1)
        assert result is not None
        assert result.downloads == 30749
        assert result.subtitle_page_url.endswith("/subtitle-51323.html")

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
            client.get_best_subtitle("Breaking Bad", 1, 1)
            client.get_best_subtitle("Breaking Bad", 1, 2)
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
