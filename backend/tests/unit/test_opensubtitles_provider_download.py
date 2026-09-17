"""OpenSubtitlesProvider download handling (#653 review follow-up).

A download that is not a real SRT (an HTML error page, an empty body) must not
be moved into the cache under the episode's real filename, and must not be
left behind in the temp dir either.
"""

import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.matcher.subtitle_provider import OpenSubtitlesProvider

_VALID_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello world, this is a padding test line.\n\n"


def _provider(tmp_path, body):
    provider = object.__new__(OpenSubtitlesProvider)
    provider.config = types.SimpleNamespace(cache_dir=tmp_path)
    provider.client = Mock()
    provider.network_timeout = 30
    subtitle = types.SimpleNamespace(season_number=1, episode_number=1, file_name="Show.S01E01.srt")
    provider._search_with_retry = Mock(return_value=types.SimpleNamespace(data=[subtitle]))
    temp_file = tmp_path / "engram-os-temp.srt"

    def fake_download(_subtitle):
        temp_file.write_text(body, encoding="utf-8")
        return str(temp_file)

    provider._download_with_retry = fake_download
    return provider, temp_file


@pytest.mark.unit
class TestOpenSubtitlesProviderDownloadValidation:
    def test_valid_download_moved_into_cache(self, tmp_path):
        provider, temp_file = _provider(tmp_path, _VALID_SRT)
        subs = provider.get_subtitles("Show", 1)
        assert [s.episode_info.episode for s in subs] == [1]
        assert Path(subs[0].path).exists()
        assert not temp_file.exists()

    def test_invalid_download_not_cached_and_temp_removed(self, tmp_path):
        provider, temp_file = _provider(tmp_path, "<html><body>Error</body></html>" * 3)
        subs = provider.get_subtitles("Show", 1)
        assert subs == []
        assert not list((tmp_path / "data").rglob("*.srt"))
        assert not temp_file.exists()
