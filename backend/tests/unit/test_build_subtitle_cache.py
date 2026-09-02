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

import io
import json
import shutil
import sys
import tarfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.config_service as cfg_svc
from app.matcher import coverage_tracker
from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher
from app.matcher.subtitle_utils import corpus_dir_name
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

    def test_per_status_counts_accumulated(self, bsc, tmp_path):
        """One season with mixed cached / downloaded / not_found episodes —
        each must increment the matching tally field."""

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
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
                "refresh": True,  # bypass the complete-on-disk fast-path
                "skip_window_days": 30,
            },
        )()

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally, tmp_path)

        assert tally.cache_hits == 1
        assert tally.downloaded == 2
        assert tally.not_found == 1
        assert tally.seasons_done == 1
        # Per-source breakdown counts only NEW downloads by provider; the
        # cached episode (source=cache) and the not_found episode (source=None)
        # are both excluded.
        assert tally.by_source == {"opensubtitles_api": 1, "addic7ed": 1}

    def test_on_season_done_called_on_success_skip_and_fail(self, bsc, tmp_path):
        """The progress-bar advance hook must fire for every season —
        otherwise the bar stalls on shows with mixed outcomes."""

        def downloads(show_name, season, *, tmdb_id=None, use_precomputed=False):
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
                "refresh": True,  # bypass the complete-on-disk fast-path
                "skip_window_days": 30,
            },
        )()
        calls = []

        with patch.object(bsc, "download_subtitles", side_effect=downloads):
            bsc._harvest_show(
                show, args, tally, tmp_path, on_season_done=lambda: calls.append(None)
            )

        assert len(calls) == 3, "on_season_done must fire once per season"
        assert tally.seasons_done == 1
        assert tally.seasons_skipped_below_threshold == 1
        assert tally.seasons_failed == 1


@pytest.mark.unit
class TestHarvestShowCompleteOnDisk:
    """A season already at/above the coverage threshold is shipped straight from
    the SRTs on disk without calling download_subtitles — the fast path that
    keeps daily re-runs near-instant and stops re-scraping the missing tail."""

    @staticmethod
    def _write_min_srt(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\n00:00:01,000 --> 00:01:00,000\nhello\n", encoding="utf-8")

    def test_covered_season_shipped_from_disk_without_network(self, bsc, tmp_path):
        from app.matcher.subtitle_utils import corpus_dir_name

        show = {"name": "Disk Show", "tmdb_id": 555, "seasons": 1}
        # The data/ scrape cache is keyed by tmdb_id (matching where the runtime
        # matcher reads); the complete-on-disk fast path globs that id-keyed dir.
        data_dir = tmp_path / "data" / corpus_dir_name(show["tmdb_id"], show["name"])
        for code in ("S01E01", "S01E02"):
            self._write_min_srt(data_dir / f"{show['name']} - {code}.srt")
        # Prior attempt at full coverage → is_done() returns True.
        coverage_tracker.record(555, 1, total=2, covered=2)

        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": False,
                "refresh": False,
                "skip_window_days": 30,
            },
        )()
        tally = bsc.RunTally()

        def boom(*a, **k):
            raise AssertionError("download_subtitles must not run for a complete-on-disk season")

        with patch.object(bsc, "download_subtitles", side_effect=boom):
            harvested = bsc._harvest_show(show, args, tally, tmp_path)

        assert sorted(code for _s, code, _p in harvested) == ["S01E01", "S01E02"]
        assert tally.seasons_from_disk == 1
        assert tally.seasons_done == 1
        # Disk-shipped episodes count toward episodes_from_disk, NOT cache_hits —
        # keeping cache_hit_rate a meaningful quota metric.
        assert tally.episodes_from_disk == 2
        assert tally.cache_hits == 0

    def test_record_present_but_srts_gone_falls_back_to_harvest(self, bsc, tmp_path):
        """A coverage record with no SRTs on disk (e.g. a wiped cache) must NOT
        skip — it falls through to a normal harvest."""
        show = {"name": "Gone Show", "tmdb_id": 557, "seasons": 1}
        coverage_tracker.record(557, 1, total=1, covered=1)  # record, but no SRTs staged

        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": True,
                "refresh": False,
                "skip_window_days": 30,
            },
        )()
        tally = bsc.RunTally()
        called = []

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
            called.append((show_name, season))
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": 1,
                "episodes": [
                    {"code": "S01E01", "status": "not_found", "path": None, "source": None}
                ],
                "cache_dir": "/tmp",
            }

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally, tmp_path)

        assert called == [("Gone Show", 1)], "must re-harvest when SRTs are missing"
        assert tally.seasons_from_disk == 0

    def test_partial_srt_loss_falls_back_to_harvest(self, bsc, tmp_path):
        """A coverage record claims 2 episodes covered but only 1 SRT survives
        on disk (a partial wipe or interrupted sync). Shipping the 1 that
        remains would silently under-deliver a season previously marked
        complete, so this must fall through to a normal harvest, not ship
        the shortfall."""
        from app.matcher.subtitle_utils import corpus_dir_name

        show = {"name": "Partial Show", "tmdb_id": 558, "seasons": 1}
        data_dir = tmp_path / "data" / corpus_dir_name(show["tmdb_id"], show["name"])
        self._write_min_srt(data_dir / f"{show['name']} - S01E01.srt")
        # Prior attempt recorded 2 covered episodes; only 1 SRT survives.
        coverage_tracker.record(558, 1, total=2, covered=2)

        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": True,
                "refresh": False,
                "skip_window_days": 30,
            },
        )()
        tally = bsc.RunTally()
        called = []

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
            called.append((show_name, season))
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": 2,
                "episodes": [
                    {
                        "code": "S01E01",
                        "status": "cached",
                        "path": str(data_dir / f"{show_name} - S01E01.srt"),
                        "source": "cache",
                    },
                    {
                        "code": "S01E02",
                        "status": "downloaded",
                        "path": str(data_dir / f"{show_name} - S01E02.srt"),
                        "source": "addic7ed",
                    },
                ],
                "cache_dir": str(data_dir),
            }

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally, tmp_path)

        assert called == [("Partial Show", 1)], "must re-harvest on a partial SRT shortfall"
        assert tally.seasons_from_disk == 0

    def test_refresh_forces_reharvest_even_when_covered(self, bsc, tmp_path):
        """--refresh re-harvests a covered-on-disk season instead of shipping it."""
        from app.matcher.subtitle_utils import sanitize_filename

        show = {"name": "Disk Show", "tmdb_id": 556, "seasons": 1}
        data_dir = tmp_path / "data" / sanitize_filename(show["name"])
        self._write_min_srt(data_dir / f"{show['name']} - S01E01.srt")
        coverage_tracker.record(556, 1, total=1, covered=1)

        args = type(
            "Args",
            (),
            {
                "min_episodes_ratio": 0.5,
                "sleep": 0,
                "retry_low_coverage": True,
                "refresh": True,
                "skip_window_days": 30,
            },
        )()
        tally = bsc.RunTally()
        called = []

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
            called.append((show_name, season))
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": 1,
                "episodes": [
                    {
                        "code": "S01E01",
                        "status": "cached",
                        "path": str(data_dir / f"{show_name} - S01E01.srt"),
                        "source": "cache",
                    }
                ],
                "cache_dir": str(data_dir),
            }

        with patch.object(bsc, "download_subtitles", side_effect=fake_download):
            bsc._harvest_show(show, args, tally, tmp_path)

        assert called == [("Disk Show", 1)], "refresh must re-harvest via download_subtitles"
        assert tally.seasons_from_disk == 0


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

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
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
        # v3: manifest is keyed by str(tmdb_id); the canonical name is stored.
        assert "1" in release_manifest["shows"]
        assert release_manifest["shows"]["1"]["name"] == _SHOW
        assert release_manifest["shows"]["1"]["seasons"] == [1]

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
        assert (precomputed / "1" / "S01.npz").exists()
        assert (precomputed / "1" / "S01.index.json").exists()
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


@pytest.mark.unit
class TestStopReason:
    """``_stop_reason`` is the pure decision behind the run's quota guard.
    Returns None to continue, or a human-readable reason to stop."""

    def test_none_when_within_budget_and_above_floor(self, bsc):
        assert bsc._stop_reason(baseline=1000, remaining=800, max_downloads=900, floor=10) is None

    def test_stops_when_budget_spent(self, bsc):
        reason = bsc._stop_reason(baseline=1000, remaining=100, max_downloads=900, floor=10)
        assert reason is not None
        assert "budget" in reason.lower()

    def test_stops_when_at_floor_even_if_budget_remains(self, bsc):
        """An account that began the day partially used hits the floor long
        before the per-run budget. This is the case the login-only check missed."""
        reason = bsc._stop_reason(baseline=200, remaining=8, max_downloads=900, floor=10)
        assert reason is not None
        assert "quota" in reason.lower()

    def test_stops_when_remaining_exactly_at_floor(self, bsc):
        """Inclusive boundary: at the floor is already too low to risk a
        download that would be recorded as zero coverage if it fails."""
        reason = bsc._stop_reason(baseline=1000, remaining=10, max_downloads=900, floor=10)
        assert reason is not None
        assert "quota" in reason.lower()

    def test_floor_wins_when_both_limits_trip(self, bsc):
        """Both limits are satisfied here. The floor message must win: a
        nearly-empty account is the more urgent diagnosis, and the budget
        message would misdescribe it as a well-behaved stop."""
        reason = bsc._stop_reason(baseline=1000, remaining=5, max_downloads=100, floor=10)
        assert "quota" in reason.lower()
        assert "budget" not in reason.lower()

    def test_none_when_quota_unknown(self, bsc):
        """No OpenSubtitles credentials means no quota telemetry. Scrapers
        have no daily cap, so an unknown quota must not halt the run."""
        assert bsc._stop_reason(baseline=None, remaining=None, max_downloads=900, floor=10) is None

    def test_none_when_baseline_unknown_but_above_floor(self, bsc):
        assert bsc._stop_reason(baseline=None, remaining=500, max_downloads=900, floor=10) is None

    def test_stops_at_floor_when_baseline_unknown(self, bsc):
        reason = bsc._stop_reason(baseline=None, remaining=0, max_downloads=900, floor=10)
        assert reason is not None
        assert "quota" in reason.lower()


@pytest.mark.unit
class TestMainQuotaHalt:
    """Drive ``main()`` into the quota guard.

    ``TestStopReason`` covers the pure decision; this class covers the wiring
    around it: the per-show check, the try/except that ends the run, the
    mid-run re-baseline, and the exit-code matrix. The guard is the only thing
    standing between a scheduled build and the failure it was written for --
    a run that sails past zero quota and records every remaining season as 0%
    coverage, skip-listing it for a month.

    Assertions are on observable effects (return code, which shows were
    actually harvested, whether a tarball was written, what reached the log),
    not on mock call counts: a wiring regression that never reads the quota
    would still satisfy "the mock was called".
    """

    @staticmethod
    def _setup(
        bsc,
        tmp_path,
        monkeypatch,
        *,
        n_shows,
        quota_readings,
        credentialed=True,
        startup_probe=None,
    ):
        """Stage ``n_shows`` single-season shows and a scripted quota feed.

        ``quota_readings`` is consumed one entry per ``get_last_quota()`` call
        and clamps to its last value, because ``main`` calls it once per show
        AND once more for the final summary. Entries are the raw dict the real
        function returns (or None, for a run with no OpenSubtitles telemetry).

        ``credentialed`` selects which startup branch ``main`` takes, and
        ``startup_probe`` is what the up-front ``probe_os_quota`` returns. Both
        matter: in production the baseline is normally captured from that probe,
        and only a run whose probe FAILED (credentials present, ``user_info``
        unreachable, None returned) falls through to the loop's
        ``quota_baseline is None`` arm. A credential-free run has no client at
        all, so its quota stays None for the whole run and it can never halt.

        Returns the list that records every show ``download_subtitles`` was
        called for, in order -- that list is how "the run stopped early" is
        asserted.
        """
        cache_dir = tmp_path / "cache"
        harvested_shows: list[str] = []

        def fake_download(show_name, season, *, tmdb_id=None, use_precomputed=False):
            harvested_shows.append(show_name)
            data_dir = cache_dir / "data" / corpus_dir_name(tmdb_id, show_name)
            episodes = []
            for code, text in _EPISODES[:2]:
                srt_path = data_dir / f"{show_name} - {code}.srt"
                _write_srt(srt_path, text)
                episodes.append(
                    {
                        "code": code,
                        "status": "downloaded",
                        "path": str(srt_path),
                        "source": "opensubtitles_api",
                    }
                )
            return {
                "show_name": show_name,
                "season": season,
                "total_episodes": len(episodes),
                "episodes": episodes,
                "cache_dir": str(data_dir),
            }

        shows = [
            {"name": f"Show {i}", "tmdb_id": 9000 + i, "seasons": 1} for i in range(1, n_shows + 1)
        ]

        calls = {"n": 0}

        def fake_get_last_quota():
            idx = min(calls["n"], len(quota_readings) - 1)
            calls["n"] += 1
            return quota_readings[idx]

        def fake_config():
            return SimpleNamespace(
                tmdb_api_key="fake-tmdb-key",
                opensubtitles_api_key="fake-os-key" if credentialed else None,
                opensubtitles_username="user" if credentialed else None,
                opensubtitles_password="pass" if credentialed else None,
                subtitles_cache_path=str(cache_dir),
            )

        monkeypatch.setattr(bsc, "_ensure_db_schema", lambda: None)
        monkeypatch.setattr(bsc, "_bootstrap_config_from_env", lambda: None)
        monkeypatch.setattr(bsc, "_select_shows", lambda args: shows)
        monkeypatch.setattr(bsc, "download_subtitles", fake_download)
        monkeypatch.setattr(bsc, "get_last_quota", fake_get_last_quota)
        monkeypatch.setattr(bsc, "probe_os_quota", lambda config: startup_probe)
        monkeypatch.setattr(cfg_svc, "get_config_sync", fake_config)

        return harvested_shows

    @staticmethod
    def _argv(tarball, *, max_downloads, quota_floor):
        return [
            "build_subtitle_cache.py",
            "--output",
            str(tarball),
            "--min-episodes-ratio",
            "0.5",
            "--sleep",
            "0",
            "--content-version",
            "test-run",
            "--max-downloads",
            str(max_downloads),
            "--quota-floor",
            str(quota_floor),
        ]

    @staticmethod
    @contextmanager
    def _captured_log(bsc):
        """Collect WARNING+ loguru output. The script logs through loguru, which
        pytest's ``caplog`` (a stdlib logging handler) does not see."""
        sink = io.StringIO()
        handler_id = bsc.logger.add(sink, level="WARNING", format="{message}")
        try:
            yield sink
        finally:
            bsc.logger.remove(handler_id)

    def test_halts_at_quota_floor_and_exits_2(self, bsc, tmp_path, monkeypatch):
        """Quota drops to the floor after the first show: the run must stop
        there, still package show 1, and exit non-zero."""
        harvested = self._setup(
            bsc,
            tmp_path,
            monkeypatch,
            n_shows=3,
            # Baseline captured up front, as in production.
            startup_probe=900,
            # show 1 finishes with plenty left; show 2 finishes at 5, under the floor.
            quota_readings=[{"remaining": 900}, {"remaining": 5}],
        )
        tarball = tmp_path / "cache.tar.gz"
        monkeypatch.setattr(sys, "argv", self._argv(tarball, max_downloads=900, quota_floor=10))

        with self._captured_log(bsc) as sink:
            exit_code = bsc.main()

        assert exit_code == 2
        # The load-bearing assertion: the loop actually ended. Exit code 2 alone
        # would also be produced by a run that harvested everything and only
        # noticed the halt at the end.
        assert harvested == ["Show 1", "Show 2"], (
            f"run did not stop after the guard tripped; harvested {harvested}"
        )
        # "floor", not "quota": both halt reports end with "re-run after the daily
        # quota resets", so "quota" would not distinguish this from the budget stop.
        assert "floor" in sink.getvalue().lower()
        assert "halted early" in sink.getvalue().lower()

        # A halted run still publishes what it completed before the halt.
        assert tarball.exists()
        manifest = json.loads(tarball.with_name("manifest.json").read_text())
        assert list(manifest["shows"]) == ["9001"], (
            "expected only the show completed before the halt to be packaged"
        )

    def test_halt_with_nothing_packaged_exits_2_not_1(self, bsc, tmp_path, monkeypatch):
        """Halting on the very first show leaves no blocks. That must still
        report the halt and exit 2: exit 1 ("nothing to build") reads as a
        broken build, when the truth is a quota wall that clears on its own."""
        harvested = self._setup(
            bsc,
            tmp_path,
            monkeypatch,
            n_shows=2,
            # Credentials present but the up-front probe failed, so the baseline
            # is seeded by the loop's `quota_baseline is None` arm instead.
            startup_probe=None,
            quota_readings=[{"remaining": 3}],
        )
        tarball = tmp_path / "cache.tar.gz"
        monkeypatch.setattr(sys, "argv", self._argv(tarball, max_downloads=900, quota_floor=10))

        with self._captured_log(bsc) as sink:
            exit_code = bsc.main()

        assert exit_code == 2
        assert harvested == ["Show 1"]
        logged = sink.getvalue().lower()
        assert "halted early" in logged
        assert "nothing to publish" in logged
        assert not tarball.exists()

    def test_rebaseline_keeps_the_budget_live_after_a_quota_refill(
        self, bsc, tmp_path, monkeypatch
    ):
        """A long run crossing the daily reset sees ``remaining`` rise above the
        captured baseline. Without re-baselining, ``baseline - remaining`` goes
        negative and the budget branch never fires again -- the run would then
        draw the entire fresh allowance while the operator believes
        --max-downloads capped it.

        Feed: 600 -> 400 -> 5000 (refill) -> 4400. With the re-baseline, the
        budget is measured from 5000 and 600 spent trips the 500 cap at show 4.
        Without it, the budget is measured from 600, the subtraction is -3800,
        nothing ever trips, and all five shows run to a clean exit 0. So this
        test fails loudly if the re-baseline is removed.
        """
        harvested = self._setup(
            bsc,
            tmp_path,
            monkeypatch,
            n_shows=5,
            startup_probe=600,
            quota_readings=[
                {"remaining": 600},
                {"remaining": 400},
                {"remaining": 5000},
                {"remaining": 4400},
            ],
        )
        tarball = tmp_path / "cache.tar.gz"
        monkeypatch.setattr(sys, "argv", self._argv(tarball, max_downloads=500, quota_floor=10))

        with self._captured_log(bsc) as sink:
            exit_code = bsc.main()

        assert exit_code == 2
        assert harvested == ["Show 1", "Show 2", "Show 3", "Show 4"], (
            f"budget did not trip against the refilled baseline; harvested {harvested}"
        )
        # Budget, not floor: 4400 remaining is nowhere near the floor of 10.
        logged = sink.getvalue().lower()
        assert "budget" in logged
        assert "halted early" in logged

    def test_credential_free_run_is_never_halted(self, bsc, tmp_path, monkeypatch):
        """No OpenSubtitles credentials means no quota telemetry at all. The
        scrapers have no daily cap, so an unknown quota must let the run finish
        normally rather than halting it on a phantom limit."""
        harvested = self._setup(
            bsc,
            tmp_path,
            monkeypatch,
            n_shows=2,
            credentialed=False,
            quota_readings=[None],
        )
        tarball = tmp_path / "cache.tar.gz"
        monkeypatch.setattr(sys, "argv", self._argv(tarball, max_downloads=900, quota_floor=10))

        with self._captured_log(bsc) as sink:
            exit_code = bsc.main()

        assert exit_code == 0
        assert harvested == ["Show 1", "Show 2"]
        assert "halted early" not in sink.getvalue().lower()
        assert tarball.exists()
        manifest = json.loads(tarball.with_name("manifest.json").read_text())
        assert sorted(manifest["shows"]) == ["9001", "9002"]
