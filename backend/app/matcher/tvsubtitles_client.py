"""TVsubtitles.net subtitle scraper.

TVsubtitles.net is a small TV-focused community site with simple, stable
HTML structure. Coverage is thinner than OpenSubtitles or Addic7ed but
non-zero for English episodes of mainstream shows — useful as a fallback
when the other providers are exhausted or rate-limited.

URL chain (verified live 2026-05-20 via curl):

1.  ``POST /search1.php`` with form field ``qs={show_name}``
    → HTML page; the first ``/tvshow-{id}.html`` anchor is the top hit.
    (The search is **POST** with field name ``qs`` — not the
    ``GET /search.php?q=`` shape various stale third-party docs claim.)

2.  ``GET /tvshow-{id}-{season}.html``
    → Season page. Each episode row contains a ``<td>{S}x{EE}</td>`` cell
    (NOT ``S01E01``) and an ``<a href="/episode-{n}.html">`` link
    elsewhere in the same row.

3.  ``GET /episode-{n}.html``
    → Lists every uploaded subtitle. English subtitles are identified by
    ``<div title="Download English subtitles" class="subtitlen">``
    inside an ``<a href="/subtitle-{m}.html">`` anchor.

4.  ``GET /subtitle-{m}.html``
    → A landing page with a "Download" anchor pointing at
    ``/download-{m}.html``.

5.  ``GET /download-{m}.html``
    → **Response varies with cookie state**. A cookieless request
    receives a tiny HTML stub with a JavaScript redirect that assembles
    a ``files/...zip`` path from string fragments (anti-scraping).
    After step 4 sets the ``visited1`` and ``lfp`` session cookies,
    the SAME URL streams the ZIP directly with no intermediate hop.
    We detect which form we got via the ZIP magic bytes ``PK\\x03\\x04``
    and fall back to JS parsing + a final ``/files/...zip`` GET only
    if we somehow lost the cookies.

This client is conservative on rate (1 req/sec) because the site is
small and aggressive scraping risks an IP block.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class TVSubtitlesSubtitle:
    """A search hit on TVsubtitles. ``subtitle_page_url`` points at
    ``/subtitle-{id}.html`` (step 4 above); the client follows the redirect
    chain through ``/download-{id}.html`` to the final ZIP on demand."""

    language: str
    release: str
    downloads: int
    subtitle_page_url: str


class TVSubtitlesClient:
    """Search and download English subtitles from TVsubtitles.net."""

    BASE_URL = "https://www.tvsubtitles.net"

    # Conservative 1 req/sec — the site is small and easily annoyed.
    REQUESTS_PER_MINUTE = 60
    MIN_REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE

    def __init__(self):
        self.session = requests.Session()
        # ``Connection: close`` is non-negotiable here. The TVsubtitles
        # backend runs Apache 2.4.6 / PHP 5.3.29, which keeps the TCP
        # socket open after the response and silently closes it before
        # the next request lands — producing a ``RemoteDisconnected:
        # Remote end closed connection without response`` exception that
        # only shows up on the *second* request via a ``requests.Session``.
        # Forcing a fresh connection per request side-steps the bug
        # entirely; the 1 req/sec rate limit means the TCP handshake
        # overhead is negligible.
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": self.BASE_URL,
                "Connection": "close",
            }
        )
        self._last_request_time = 0.0
        # Show-id lookup is the slow step; cache it within the instance so
        # repeated episode lookups on the same show don't re-pay the search.
        self._show_id_cache: dict[str, int | None] = {}

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._rate_limit()
        return self.session.get(url, timeout=30, **kwargs)

    def _post(self, url: str, data: dict, **kwargs) -> requests.Response:
        self._rate_limit()
        return self.session.post(url, data=data, timeout=30, **kwargs)

    def _find_show_id(self, show_name: str) -> int | None:
        """Map a show name to TVsubtitles' internal numeric id via
        the POST search endpoint."""
        if show_name in self._show_id_cache:
            return self._show_id_cache[show_name]

        try:
            response = self._post(
                urljoin(self.BASE_URL, "/search1.php"),
                data={"qs": show_name},
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"TVsubtitles search failed for {show_name}: {e}", exc_info=True)
            self._show_id_cache[show_name] = None
            return None

        show_id = _parse_first_show_id(response.text)
        self._show_id_cache[show_name] = show_id
        return show_id

    def get_best_subtitle(
        self, show_name: str, season: int, episode: int, language: str = "en"
    ) -> TVSubtitlesSubtitle | None:
        """Find the best English subtitle for one episode."""
        show_id = self._find_show_id(show_name)
        if show_id is None:
            return None

        season_url = urljoin(self.BASE_URL, f"/tvshow-{show_id}-{season}.html")
        try:
            response = self._get(season_url)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(
                f"TVsubtitles season page failed for {show_name} S{season:02d}: {e}",
                exc_info=True,
            )
            return None

        episode_page_url = _find_episode_page(
            response.text, season, episode, base_url=self.BASE_URL
        )
        if not episode_page_url:
            return None

        try:
            ep_response = self._get(episode_page_url)
            ep_response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"TVsubtitles episode page failed: {e}", exc_info=True)
            return None

        candidates = _parse_subtitle_candidates(
            ep_response.text, base_url=self.BASE_URL, language=language
        )
        if not candidates:
            return None

        # Prefer highest download count — rough proxy for community trust.
        candidates.sort(key=lambda s: s.downloads, reverse=True)
        return candidates[0]

    def download_subtitle(self, subtitle: TVSubtitlesSubtitle, save_path: Path) -> Path | None:
        """Walk subtitle page → download endpoint → ZIP, then extract the
        ``.srt`` to ``save_path``.

        The download endpoint's response shape depends on whether the
        request carries the session cookies set by the subtitle page:

        - With cookies (the normal flow): the ZIP is streamed directly.
        - Without cookies: a JS-redirect stub is returned that points at
          ``/files/{name}.zip``. We parse the JS and fetch the ZIP in a
          second hop. This branch exists for robustness — the primary
          flow uses cookies and rarely hits it — but keeping it means
          a session reset between subtitle and download steps doesn't
          break the chain.
        """
        # Step 4 — subtitle landing page. This sets ``visited1`` and
        # ``lfp`` cookies on the session, which the next step relies on.
        try:
            sub_page = self._get(subtitle.subtitle_page_url)
            sub_page.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"TVsubtitles subtitle page failed: {e}", exc_info=True)
            return None

        download_page_url = _extract_download_page_url(sub_page.text, base_url=self.BASE_URL)
        if not download_page_url:
            logger.warning(f"TVsubtitles: no download-X.html link on {subtitle.subtitle_page_url}")
            return None

        # Step 5 — fetch the download endpoint. The Referer matters: with
        # it the server identifies us as a real user-flow and streams the
        # ZIP directly; without it we get the JS-redirect stub.
        try:
            response = self._get(download_page_url, headers={"Referer": subtitle.subtitle_page_url})
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"TVsubtitles download endpoint failed: {e}", exc_info=True)
            return None

        zip_bytes = self._resolve_zip_bytes(response, download_page_url)
        if zip_bytes is None:
            return None

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                srt_names = [n for n in zf.namelist() if n.lower().endswith(".srt")]
                if not srt_names:
                    logger.warning("TVsubtitles ZIP had no .srt entries")
                    return None
                # If multiple .srt (forced + full), prefer the largest.
                best_name = max(srt_names, key=lambda n: zf.getinfo(n).file_size)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(zf.read(best_name))
        except zipfile.BadZipFile:
            logger.warning("TVsubtitles response was not a valid ZIP")
            return None

        return save_path

    def _resolve_zip_bytes(
        self, response: requests.Response, download_page_url: str
    ) -> bytes | None:
        """Return raw ZIP bytes from the download-endpoint response.

        Fast path: response.content already starts with the ZIP local-file
        header magic (``PK\\x03\\x04``). This is what the cookie-bearing
        request gets and is the common case.

        Slow path: the response is the JS-redirect stub. Parse the JS,
        construct the ``files/...zip`` URL, and fetch it with a Referer
        matching the stub page (the server checks Referer on /files/).
        """
        body = response.content
        if body[:4] == b"PK\x03\x04":
            return body

        # JS-redirect fallback. ``response.text`` decodes through the
        # session's response encoding; for short HTML stubs this is safe.
        zip_path = _extract_zip_path_from_js(response.text)
        if not zip_path:
            logger.warning(
                f"TVsubtitles: response from {download_page_url} is neither a ZIP "
                "nor a recognisable JS-redirect stub"
            )
            return None
        zip_url = urljoin(self.BASE_URL + "/", zip_path)
        try:
            zip_response = self._get(zip_url, headers={"Referer": download_page_url})
            zip_response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"TVsubtitles ZIP fetch failed: {e}", exc_info=True)
            return None
        return zip_response.content


def _parse_first_show_id(html: str) -> int | None:
    """Find the first ``tvshow-{id}.html`` anchor on the search-results
    page. The search page returns the homepage chrome plus a list of hits
    near the bottom; ``find`` returns them in document order, which is also
    the top-hit-first order TVsubtitles uses.

    Subtleties verified against live HTML (2026-05-20):

    - Hrefs are inconsistent: relative (``tvshow-133.html``), absolute
      (``/tvshow-133.html``), AND fully-qualified to alternate-language
      subdomains (``https://es.tvsubtitles.net/tvshow-133-1.html``) all
      coexist in the same response. We accept ``www.tvsubtitles.net`` and
      bare-path forms only.
    - The pattern matches ONLY the base show URL (no ``-{season}``
      suffix), otherwise we'd grab a season link from the "recently
      updated" sidebar and later reconstruct a malformed season URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Match: optional ``http(s)://www.tvsubtitles.net``, optional leading
    # slash, then ``tvshow-{id}.html`` with no ``-{season}`` suffix.
    pattern = re.compile(r"^(?:https?://www\.tvsubtitles\.net)?/?tvshow-(\d+)\.html$")
    for link in soup.find_all("a", href=pattern):
        m = pattern.match(link["href"])
        if m:
            return int(m.group(1))
    return None


def _find_episode_page(html: str, season: int, episode: int, base_url: str) -> str | None:
    """Locate the ``/episode-{n}.html`` link for the requested
    ``{season}x{episode}`` row.

    The season page renders episodes as a table where one cell of each row
    contains the literal text ``{S}x{EE}`` (zero-padded episode), and the
    ``/episode-{n}.html`` link sits in another cell of the SAME row.

    Crucially, that episode table is nested inside outer layout tables, so
    ``find_all("td")`` is restricted to each row's DIRECT children
    (``recursive=False``). Without that restriction the outermost wrapper
    ``<tr>`` transitively contains every episode's cells, matches any
    target, and returns the first episode link in the document — i.e. every
    requested episode resolves to the highest-numbered episode's page.
    """
    soup = BeautifulSoup(html, "html.parser")
    target = f"{season}x{episode:02d}"
    # Season-page episode hrefs are RELATIVE (``episode-8080.html``), not
    # absolute. urljoin with a base_url ending in ``/`` reattaches the
    # host correctly. The regex tolerates either form so the parser
    # doesn't silently miss episodes if the markup ever flips, and the
    # trailing ``\.html$`` rejects language-suffixed links such as
    # ``episode-7308-gr.html`` that appear in the flags cell.
    href_re = re.compile(r"^/?episode-\d+\.html$")
    for row in soup.find_all("tr"):
        # ``recursive=False`` for the cell scan: only the row's OWN cells,
        # so the wrapper <tr> (whose descendants span every episode) can't
        # match. The link search below is deliberately recursive — the
        # ``<a href="episode-*.html">`` lives inside a child <td>, not as a
        # direct <tr> child, so ``recursive=False`` there would find nothing.
        cells_text = [td.get_text(strip=True) for td in row.find_all("td", recursive=False)]
        if target not in cells_text:
            continue
        link = row.find("a", href=href_re)
        if link:
            return urljoin(base_url + "/", link["href"])
    return None


def _parse_subtitle_candidates(
    html: str, base_url: str, language: str
) -> list[TVSubtitlesSubtitle]:
    """Return one entry per English subtitle on the ``/episode-{n}.html`` page.

    TVsubtitles marks the language of each subtitle two ways: by a
    ``flag-{lang}.png`` image near the entry AND by ``title="Download
    English subtitles"`` on the inner div. We use the title attribute as
    the authoritative signal (the flag layout has shifted historically)
    and fall back to anchor text for legacy pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[TVSubtitlesSubtitle] = []

    lang_full = _LANGUAGE_NAMES.get(language, language).lower()

    for anchor in soup.find_all("a", href=re.compile(r"^/?subtitle-\d+\.html$")):
        # Inner <div title="Download English subtitles" ...> is the cleanest signal.
        inner = anchor.find("div", title=True)
        title_attr = (inner.get("title", "") if inner else "").lower()
        anchor_text = anchor.get_text(" ", strip=True).lower()
        if lang_full not in title_attr and lang_full not in anchor_text:
            continue

        downloads = _parse_downloads_near(anchor)
        release = _parse_release_near(anchor)
        results.append(
            TVSubtitlesSubtitle(
                language=language,
                release=release,
                downloads=downloads,
                subtitle_page_url=urljoin(base_url, anchor["href"]),
            )
        )
    return results


_LANGUAGE_NAMES = {"en": "english", "es": "spanish", "fr": "french", "de": "german"}


def _parse_downloads_near(anchor) -> int:
    """Extract one subtitle entry's download count.

    The count is a bare integer inside the entry's own
    ``<p title="downloaded"><img .../> 32167</p>`` cell — there is no
    literal "downloads" text on the page, and the small ``{x}/{y}`` widget
    at the top of each entry is a rating, not a download count. We read the
    labelled cell *within this anchor* (each anchor wraps exactly one entry)
    and pull the integer out, tolerating spaced thousands like ``32 167``.
    """
    cell = anchor.find("p", attrs={"title": "downloaded"})
    if cell is None:
        return 0
    digits = re.sub(r"\D", "", cell.get_text())
    return int(digits) if digits else 0


def _parse_release_near(anchor) -> str:
    """Extract one subtitle entry's release tag.

    The site exposes the rip source and release group in two labelled cells
    (``<p title="rip">DVDRip</p>`` / ``<p title="release">ORPHEUS</p>``);
    we join the non-empty parts (``"DVDRip.ORPHEUS"``). When both are blank —
    e.g. an uploader put the tag only in the title — we fall back to the
    ``<h5>`` parenthetical (``"... 1x07 (WEB-DL)"`` → ``"WEB-DL"``), or the
    whole ``<h5>`` label if there is no parenthetical. Everything is read
    from *within this anchor* — the entries are siblings in a shared
    container with no per-entry ``<tr>``, so walking up to a parent would
    grab a neighbouring (often different-language) entry's tag.
    """
    parts = []
    for label in ("rip", "release"):
        cell = anchor.find("p", attrs={"title": label})
        if cell:
            text = cell.get_text(strip=True)
            if text:
                parts.append(text)
    if parts:
        return ".".join(parts)
    h5 = anchor.find("h5")
    if h5 is None:
        return ""
    label = h5.get_text(strip=True)
    paren = re.search(r"\(([^)]+)\)\s*$", label)
    return paren.group(1) if paren else label


def _extract_download_page_url(html: str, base_url: str) -> str | None:
    """The subtitle landing page has a button-anchor pointing at
    ``download-{id}.html`` (NOT the final ZIP — that step's parsing is
    in :func:`_extract_zip_path_from_js`).

    The href is relative on the live site (``download-12100.html``); the
    regex permits either form for resilience.
    """
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("a", href=re.compile(r"^/?download-\d+\.html$"))
    if link:
        return urljoin(base_url + "/", link["href"])
    return None


# The JS-redirect page assembles the ZIP path from a few string fragments:
#
#   var s1= 'fil';
#   var s2= 'es/B';
#   var s3= 're';
#   var s4= 'aking Bad_1x01_DVDRip.ORPHEUS.en.zip';
#   document.location = s1+s2+s3+s4;
#
# We capture every ``var sN = '...'`` assignment in declaration order and
# concatenate. The split is presumably anti-scraper, but reversible without
# running JS.
_JS_FRAGMENT_RE = re.compile(r"var\s+s\d+\s*=\s*'([^']*)'\s*;", re.IGNORECASE)


def _extract_zip_path_from_js(html: str) -> str | None:
    """Reconstruct the ``files/{name}.zip`` path from the JS-redirect stub.

    Falls back to a direct regex if the page has been simplified to a
    plain ``document.location = "files/..."`` form."""
    fragments = _JS_FRAGMENT_RE.findall(html)
    if fragments:
        joined = "".join(fragments)
        # Only return paths that actually look like the ZIP we expect.
        if joined.lower().endswith(".zip"):
            return joined

    # Fallback: look for an unquoted-or-quoted files/... .zip path anywhere
    # in the JS. Picks up the simplified form if TVsubtitles ever drops
    # the fragment-concatenation indirection.
    m = re.search(r"['\"](files/[^'\"]+\.zip)['\"]", html)
    if m:
        return m.group(1)
    return None
