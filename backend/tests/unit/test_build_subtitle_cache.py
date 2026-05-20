"""Unit tests for build_subtitle_cache helpers.

The build script is a long-running entry point that's hard to test end-to-end
without burning a real OpenSubtitles quota — these tests cover the pure
helpers (RunTally) and the _harvest_show contract (mutates tally in place,
calls on_season_done).
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_build_module():
    """Import scripts/build_subtitle_cache.py without polluting sys.modules.

    The script lives outside the importable backend/app/ tree (it uses
    ``sys.path.insert`` to find app/), so we load it by file path. This
    keeps the test setup honest about what the script's structure is.
    """
    backend_root = Path(__file__).parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "build_subtitle_cache",
        backend_root / "scripts" / "build_subtitle_cache.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_subtitle_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bsc():
    return _load_build_module()


@pytest.mark.unit
class TestRunTally:
    def test_initial_state(self, bsc):
        tally = bsc.RunTally()
        assert tally.downloaded == 0
        assert tally.cache_hits == 0
        assert tally.not_found == 0
        assert tally.cache_hit_rate == 0.0

    def test_cache_hit_rate(self, bsc):
        tally = bsc.RunTally()
        tally.cache_hits = 30
        tally.downloaded = 10
        # 30 hits / (30 + 10) = 75%
        assert tally.cache_hit_rate == 0.75

    def test_elapsed_str_format(self, bsc):
        tally = bsc.RunTally()
        # Right after construction this is "0:00:00" or close to it; just
        # assert the shape rather than the exact value.
        elapsed = tally.elapsed_str()
        assert elapsed.count(":") == 2


@pytest.mark.unit
class TestHarvestShowAccumulatesTally:
    """`_harvest_show` is the only place that calls download_subtitles, and
    it's responsible for translating per-episode statuses back into the
    RunTally fields the final summary reports. A regression here would mean
    the user sees zeros at the end of a real run."""

    def test_per_status_counts_accumulated(self, bsc):
        """One season with mixed cached / downloaded / not_found episodes —
        each must increment the matching tally field."""

        def fake_download(show_name, season):
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": 4,
                "episodes": [
                    {
                        "code": "S01E01",
                        "status": "cached",
                        "path": "/tmp/x.srt",
                        "source": "cache",
                    },
                    {
                        "code": "S01E02",
                        "status": "downloaded",
                        "path": "/tmp/x.srt",
                        "source": "opensubtitles_api",
                    },
                    {
                        "code": "S01E03",
                        "status": "downloaded",
                        "path": "/tmp/x.srt",
                        "source": "addic7ed",
                    },
                    {"code": "S01E04", "status": "not_found", "path": None, "source": None},
                ],
                "cache_dir": "/tmp",
            }

        tally = bsc.RunTally()
        show = {"name": "X", "tmdb_id": 1, "seasons": 1}
        args = type("Args", (), {"min_episodes_ratio": 0.5, "sleep": 0})()

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally)

        assert tally.cache_hits == 1
        assert tally.downloaded == 2
        assert tally.not_found == 1
        assert tally.seasons_done == 1

    def test_on_season_done_called_on_success_skip_and_fail(self, bsc):
        """The progress-bar advance hook must fire for every season —
        otherwise the bar stalls on shows with mixed outcomes."""

        def downloads(show_name, season):
            # 3 seasons → success / below-threshold / exception
            if season == 1:
                return {
                    "show_name": show_name,
                    "season": 1,
                    "total_episodes": 1,
                    "episodes": [
                        {
                            "code": "S01E01",
                            "status": "downloaded",
                            "path": "/tmp/x.srt",
                            "source": "addic7ed",
                        }
                    ],
                    "cache_dir": "/tmp",
                }
            if season == 2:
                return {
                    "show_name": show_name,
                    "season": 2,
                    "total_episodes": 4,
                    "episodes": [
                        {
                            "code": "S02E01",
                            "status": "downloaded",
                            "path": "/tmp/x.srt",
                            "source": "addic7ed",
                        },
                        {"code": "S02E02", "status": "not_found", "path": None, "source": None},
                        {"code": "S02E03", "status": "not_found", "path": None, "source": None},
                        {"code": "S02E04", "status": "not_found", "path": None, "source": None},
                    ],
                    "cache_dir": "/tmp",
                }
            raise RuntimeError("boom")

        tally = bsc.RunTally()
        show = {"name": "X", "tmdb_id": 1, "seasons": 3}
        args = type("Args", (), {"min_episodes_ratio": 0.5, "sleep": 0})()
        calls = []

        with patch.object(bsc, "download_subtitles", side_effect=downloads):
            bsc._harvest_show(show, args, tally, on_season_done=lambda: calls.append(None))

        assert len(calls) == 3, "on_season_done must fire once per season"
        assert tally.seasons_done == 1
        assert tally.seasons_skipped_below_threshold == 1
        assert tally.seasons_failed == 1
