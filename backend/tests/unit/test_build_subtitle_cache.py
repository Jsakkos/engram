"""Unit tests for build_subtitle_cache helpers.

The build script is a long-running entry point that's hard to test end-to-end
without burning a real OpenSubtitles quota — these tests cover the pure
helpers (RunTally), the _harvest_show contract (mutates tally in place,
calls on_season_done), and a full round-trip of main() with subtitle harvest
mocked: pre-staged SRTs → main() → tarball + manifest → load through
EpisodeMatcher → match. The round-trip catches drift between what the script
produces and what the consumer expects (manifest schema, tarball layout,
vectorizer config identity).
"""

import json
import shutil
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.config_service as cfg_svc
from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    vectorizer_config_hash,
)

# `bsc` (build_subtitle_cache) and `vsc` (validate_subtitle_cache) fixtures
# live in conftest.py as session-scoped — each script's module-level code
# (including sys.path.insert) executes exactly once per pytest run.


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
        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": True,  # bypass the W3 skip-list fast-path
                "skip_window_days": 30,
            },
        )()

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally)

        assert tally.cache_hits == 1
        assert tally.downloaded == 2
        assert tally.not_found == 1
        assert tally.seasons_done == 1
        # Per-source breakdown counts only episodes that produced a file;
        # the not_found episode (source=None) is correctly excluded.
        assert tally.by_source == {"cache": 1, "opensubtitles_api": 1, "addic7ed": 1}

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
        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": True,  # bypass the W3 skip-list fast-path
                "skip_window_days": 30,
            },
        )()
        calls = []

        with patch.object(bsc, "download_subtitles", side_effect=downloads):
            bsc._harvest_show(show, args, tally, on_season_done=lambda: calls.append(None))

        assert len(calls) == 3, "on_season_done must fire once per season"
        assert tally.seasons_done == 1
        assert tally.seasons_skipped_below_threshold == 1
        assert tally.seasons_failed == 1


_SHOW = "Test Show"
_EPISODES = [
    ("S01E01", "detective solves the murder in the old mansion at midnight"),
    ("S01E02", "the spaceship crew explores a distant alien planet"),
    ("S01E03", "a chef cooks an elaborate pasta dinner in a small kitchen"),
]


def _write_srt(path: Path, text: str) -> None:
    """Write a minimal valid SRT containing ``text`` as the only cue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"1\n00:00:01,000 --> 00:01:00,000\n{text}\n",
        encoding="utf-8",
    )


@pytest.mark.unit
class TestMainRoundTrip:
    """Run main() with subtitle harvest mocked; verify the produced tarball
    loads through the real matcher and matches the right episode.

    This is the only test that exercises the actual packaging path
    (tarball write, manifest write, sha256, vectorizer config hash) against
    the actual consumer. Catches drift between build-side and consume-side
    that purely-unit tests on either side would miss.
    """

    def test_main_produces_loadable_tarball(self, bsc, vsc, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data" / _SHOW / "S01"
        srt_paths: dict[str, Path] = {}
        for code, text in _EPISODES:
            srt_path = data_dir / f"{_SHOW} - {code}.srt"
            _write_srt(srt_path, text)
            srt_paths[code] = srt_path

        def fake_download(show_name, season):
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": len(_EPISODES),
                "episodes": [
                    {
                        "code": code,
                        "status": "downloaded",
                        "path": str(srt_paths[code]),
                        "source": "opensubtitles_api",
                    }
                    for code, _ in _EPISODES
                ],
                "cache_dir": str(data_dir),
            }

        def fake_select_shows(args):
            return [{"name": _SHOW, "tmdb_id": 1, "seasons": 1}]

        def fake_config():
            return SimpleNamespace(
                tmdb_api_key="fake-tmdb-key",
                opensubtitles_api_key=None,
                opensubtitles_username=None,
                opensubtitles_password=None,
                subtitles_cache_path=str(cache_dir),
            )

        # Patch the DB + TMDB + harvest seams so main() exercises only the
        # vectorize+package path under test.
        monkeypatch.setattr(bsc, "_ensure_db_schema", lambda: None)
        monkeypatch.setattr(bsc, "_bootstrap_config_from_env", lambda: None)
        monkeypatch.setattr(bsc, "_select_shows", fake_select_shows)
        monkeypatch.setattr(bsc, "download_subtitles", fake_download)
        # main() does `from app.services.config_service import get_config_sync`
        # at call time, so patching the source module is what main() sees.
        monkeypatch.setattr(cfg_svc, "get_config_sync", fake_config)

        output_tarball = tmp_path / "engram-subtitle-cache.tar.gz"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "build_subtitle_cache.py",
                "--output",
                str(output_tarball),
                "--min-episodes-ratio",
                "0.5",
                "--sleep",
                "0",
                "--content-version",
                "test-run",
            ],
        )

        exit_code = bsc.main()
        assert exit_code == 0

        # Tarball + sibling release manifest both exist.
        assert output_tarball.exists()
        release_manifest_path = output_tarball.with_name("manifest.json")
        assert release_manifest_path.exists()

        release_manifest = json.loads(release_manifest_path.read_text())

        # Manifest fields match the constants the consumer reads. If anyone
        # bumps CACHE_FORMAT_VERSION or HASHING_N_FEATURES without rebuilding
        # the cache, this fails — surfacing the drift before it ships.
        assert release_manifest["cache_format_version"] == CACHE_FORMAT_VERSION
        assert release_manifest["n_features"] == HASHING_N_FEATURES
        assert release_manifest["vectorizer_config_hash"] == vectorizer_config_hash()
        assert release_manifest["content_version"] == "test-run"
        assert _SHOW in release_manifest["shows"]
        assert release_manifest["shows"][_SHOW]["seasons"] == [1]

        # sha256 in the release manifest matches the actual file — and is
        # computed via the validator's streaming helper so this test also
        # proves the build script and validator agree on the hash for the
        # same bytes (the smoke workflow depends on this equality).
        assert release_manifest["tarball_sha256"] == vsc._sha256_of_file(output_tarball)

        # End-to-end: the live smoke validator (the one CI runs daily against
        # subtitle-cache-latest) must pass on the artifact main() just
        # produced. Closes the loop — a future renamed manifest field would
        # be caught here, not in production.
        smoke_result = vsc.validate(tmp_path)
        assert smoke_result.failures == [], smoke_result.failures

        # Unpack and load through the real matcher — the round-trip assertion.
        unpack_dir = tmp_path / "unpacked"
        with tarfile.open(output_tarball, "r:gz") as tar:
            # filter= requires Python >= 3.11.4 (PEP 706 backport). CI's
            # python-version: "3.11" resolves to the latest patch, and the
            # project targets >= 3.11 — a local env pinned below 3.11.4
            # would TypeError here. Acceptable tradeoff for CVE-2007-4559
            # mitigation.
            tar.extractall(unpack_dir, filter="data")
        precomputed = unpack_dir / "precomputed"
        assert (precomputed / "idf.npy").exists()
        assert (precomputed / _SHOW / "S01.npz").exists()
        assert (precomputed / _SHOW / "S01.index.json").exists()
        assert (precomputed / "manifest.json").exists()

        # Re-home the cache so EpisodeMatcher sees it under the expected layout.
        install_dir = tmp_path / "installed"
        install_dir.mkdir()
        shutil.copytree(precomputed, install_dir / "precomputed")

        matcher = EpisodeMatcher(cache_dir=install_dir, show_name=_SHOW)
        # Calling the private _load_precomputed_season directly gives a
        # specific failure message when a manifest/format mismatch causes the
        # loader to short-circuit to None — distinct from "the matcher ran
        # but matched the wrong episode" further down. This is also the
        # established test seam: test_precomputed_cache_service.py and
        # test_precomputed_cache.py exercise the loader the same way at
        # 8 other sites.
        loaded = matcher._load_precomputed_season(1)
        assert loaded is not None, (
            "matcher refused to load the cache main() just produced — "
            "build/consumer drift in manifest schema or vectorizer config"
        )
        tfidf = TfidfMatcher()
        tfidf.load_precomputed(*loaded)
        results = tfidf.match("the crew explores a far away planet")
        # Guard before indexing so a silent loader failure or all-zero vectors
        # surface as a readable assertion rather than an IndexError.
        assert results, (
            "tfidf.match returned no candidates — precomputed cache may not "
            "have loaded correctly or vectorizer produced empty output"
        )
        assert results[0][0] == "S01E02", (
            "matched against the wrong episode — vectorizer or IDF "
            "computation differs between build script and consumer"
        )
