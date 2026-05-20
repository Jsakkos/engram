"""Live HTTP tests for the TVsubtitles.net client.

See ``tests/live/conftest.py`` for opt-in mechanics. These tests pick
Breaking Bad S01E01 — a long-running, very-broadly-subtitled episode —
and walk the full TVsubtitles URL chain:

  POST /search1.php  →  /tvshow-{id}-{season}.html  →
  /episode-{n}.html  →  /subtitle-{m}.html  →
  /download-{m}.html  (JS redirect)  →  /files/{name}.zip

If any step fails, the assertion message points at the parser that
needs updating, since the unit-test fixtures only validate against
captured HTML and won't catch layout drift.
"""

import pytest
import requests

from app.matcher.subtitle_utils import is_valid_srt_file
from app.matcher.tvsubtitles_client import TVSubtitlesClient

_KNOWN_SHOW = "Breaking Bad"
_KNOWN_SEASON = 1
_KNOWN_EPISODE = 1


@pytest.mark.live
class TestTVSubtitlesLive:
    def test_search_resolves_show_id(self):
        """The /search.php parser should find a numeric show id for a
        well-known show."""
        client = TVSubtitlesClient()
        try:
            show_id = client._find_show_id(_KNOWN_SHOW)
        except requests.ConnectionError as e:
            pytest.skip(f"TVsubtitles unreachable: {e}")
        # If show_id is None, EITHER the show isn't on TVsubtitles
        # (unlikely for Breaking Bad) OR _parse_first_show_id failed.
        assert show_id is not None and show_id > 0, (
            "TVsubtitles search returned no show id for Breaking Bad — "
            "the search page layout may have changed and "
            "_parse_first_show_id needs updating"
        )

    def test_get_best_subtitle_returns_candidate(self):
        """End-to-end walk: search → season → episode → first English subtitle."""
        client = TVSubtitlesClient()
        try:
            entry = client.get_best_subtitle(_KNOWN_SHOW, _KNOWN_SEASON, _KNOWN_EPISODE)
        except requests.ConnectionError as e:
            pytest.skip(f"TVsubtitles unreachable: {e}")
        assert entry is not None, (
            "TVsubtitles returned no candidate for Breaking Bad S01E01 — "
            "one of /search.php, /tvshow-{id}-{n}.html, or "
            "/episode-{id}.html parsing is broken"
        )
        assert entry.subtitle_page_url.startswith("https://www.tvsubtitles.net"), (
            f"subtitle_page_url should be absolute, got {entry.subtitle_page_url!r}"
        )
        # Sanity: the language flag scrape should produce an "en"-ish entry.
        assert entry.language == "en"

    def test_download_writes_valid_srt(self, tmp_path):
        """Pulling the ZIP and writing its .srt member should land a real
        subtitle file on disk."""
        client = TVSubtitlesClient()
        try:
            entry = client.get_best_subtitle(_KNOWN_SHOW, _KNOWN_SEASON, _KNOWN_EPISODE)
        except requests.ConnectionError as e:
            pytest.skip(f"TVsubtitles unreachable: {e}")
        if entry is None:
            pytest.skip("TVsubtitles returned no candidate; covered by sibling test")

        save_path = tmp_path / "breaking_bad_s01e01.srt"
        try:
            result = client.download_subtitle(entry, save_path)
        except requests.ConnectionError as e:
            pytest.skip(f"TVsubtitles download unreachable: {e}")

        assert result == save_path
        assert save_path.exists()
        assert is_valid_srt_file(save_path), (
            f"Downloaded file at {save_path} is not a valid SRT — "
            "the intermediate-HTML-then-ZIP fallback in download_subtitle "
            "may have failed to follow the final ZIP link"
        )
