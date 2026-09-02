"""Tests for degraded-season detection.

A "degraded" season is one where the measurement is not trustworthy: the
OpenSubtitles path did not serve AND the season is not fully retrieved.
Recording partial or 0% coverage for such a season skip-lists it for 30 days
on the strength of an infrastructure failure -- the defect that cost 664
seasons in June 2026.
"""

import types
from unittest.mock import Mock, patch

import pytest

import app.matcher.testing_service as ts
from app.matcher.testing_service import _is_degraded


@pytest.mark.unit
class TestIsDegraded:
    def test_degraded_when_os_failed_and_season_incomplete(self):
        """The mid-loop quota-exhaustion scenario: OpenSubtitles served the
        first episode before failing, so the raw ratio here is 1/2 (50%).
        Recording that as real coverage would skip-list the season for 30
        days on the strength of the OpenSubtitles outage that cut it short,
        not on the season's actual content. Under-flagging costs a 30-day
        blind spot; over-flagging costs one cheap retry next run -- so an
        incomplete season after an OS failure must be degraded."""
        episodes = [
            {"code": "S01E01", "status": "downloaded", "path": "/x.srt", "source": "addic7ed"},
            {"code": "S01E02", "status": "not_found", "path": None, "source": None},
        ]
        assert _is_degraded(os_failed=True, episodes=episodes) is True

    def test_not_degraded_when_os_failed_but_season_fully_retrieved(self):
        """A complete season is a complete season regardless of what happened
        to OpenSubtitles afterward -- there is no gap left for an outage to
        have hidden, so the measurement is trustworthy."""
        episodes = [
            {"code": "S01E01", "status": "downloaded", "path": "/x.srt", "source": "addic7ed"},
            {"code": "S01E02", "status": "cached", "path": "/y.srt", "source": "cache"},
        ]
        assert _is_degraded(os_failed=True, episodes=episodes) is False

    def test_not_degraded_when_os_healthy_and_nothing_found(self):
        """OpenSubtitles worked and genuinely had nothing. That is a real
        measurement and SHOULD be recorded, so the season gets skip-listed."""
        episodes = [{"code": "S01E01", "status": "not_found", "path": None, "source": None}]
        assert _is_degraded(os_failed=False, episodes=episodes) is False

    def test_degraded_when_os_failed_and_nothing_found(self):
        episodes = [
            {"code": "S01E01", "status": "not_found", "path": None, "source": None},
            {"code": "S01E02", "status": "not_found", "path": None, "source": None},
        ]
        assert _is_degraded(os_failed=True, episodes=episodes) is True

    def test_cached_episodes_count_as_retrieved(self):
        episodes = [{"code": "S01E01", "status": "cached", "path": "/x.srt", "source": "cache"}]
        assert _is_degraded(os_failed=True, episodes=episodes) is False

    def test_empty_episode_list_with_os_failed_is_degraded(self):
        assert _is_degraded(os_failed=True, episodes=[]) is True

    def test_empty_episode_list_with_os_healthy_is_not_degraded(self):
        assert _is_degraded(os_failed=False, episodes=[]) is False


def _mock_config(tmp_path):
    """Config with OpenSubtitles credentials configured, so download_subtitles
    enters the API block instead of skipping straight to scrapers."""
    cfg = Mock()
    cfg.subtitles_cache_path = str(tmp_path)
    cfg.opensubtitles_api_key = "key"
    cfg.opensubtitles_username = "user"
    cfg.opensubtitles_password = "pass"
    cfg.tmdb_api_key = "tmdb-key"
    return cfg


@pytest.mark.unit
class TestDownloadSubtitlesMidRunDegradation:
    """End-to-end coverage for the June 2026 failure mode: OpenSubtitles logs
    in fine (so ``_OS.failed`` stays False) but then fails mid-run on a
    per-season search/download call -- the exact 406-after-retries path a
    quota exhaustion partway through a run takes. ``_OS.failed`` alone is
    blind to this; ``download_subtitles`` must also flag its own season."""

    def test_degraded_when_os_fails_midrun_and_scrapers_find_nothing(self, tmp_path):
        with (
            patch.object(ts._OS, "failed", False),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=1),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=Mock()),
            patch.object(ts, "os_api_call", side_effect=Exception("406 quota exceeded")),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert all(ep["status"] == "not_found" for ep in result["episodes"])
        assert result["degraded"] is True

    def test_not_degraded_when_scrapers_cover_every_remaining_episode(self, tmp_path):
        """Explicit "complete" staging: OS fails mid-run on a 2-episode season,
        but the scraper cascade supplies BOTH remaining episodes, so the
        season ends up fully retrieved and the measurement is trustworthy
        regardless of the earlier OS failure."""
        scraper_hits = {
            "S01E01": {
                "code": "S01E01",
                "status": "downloaded",
                "path": str(tmp_path / "Test Show - S01E01.srt"),
                "source": "addic7ed",
            },
            "S01E02": {
                "code": "S01E02",
                "status": "downloaded",
                "path": str(tmp_path / "Test Show - S01E02.srt"),
                "source": "tvsubtitles",
            },
        }
        with (
            patch.object(ts._OS, "failed", False),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=2),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=Mock()),
            patch.object(ts, "os_api_call", side_effect=Exception("406 quota exceeded")),
            patch.object(ts, "run_jobs", return_value=scraper_hits),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert [ep["status"] for ep in result["episodes"]] == ["downloaded", "downloaded"]
        assert result["degraded"] is False

    def test_degraded_when_scrapers_cover_only_some_remaining_episodes(self, tmp_path):
        """Explicit "incomplete" staging: OS fails mid-run on a 2-episode
        season and the scraper cascade recovers only ONE of the two
        remaining episodes. The season is still incomplete, so despite
        retrieving something it must be flagged degraded -- the exact
        mid-loop quota-exhaustion shape from the June 2026 incident."""
        scraper_hits = {
            "S01E01": {
                "code": "S01E01",
                "status": "downloaded",
                "path": str(tmp_path / "Test Show - S01E01.srt"),
                "source": "addic7ed",
            },
            # S01E02 intentionally absent -- scrapers found nothing for it,
            # so it falls through to the default not_found result.
        }
        with (
            patch.object(ts._OS, "failed", False),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=2),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=Mock()),
            patch.object(ts, "os_api_call", side_effect=Exception("406 quota exceeded")),
            patch.object(ts, "run_jobs", return_value=scraper_hits),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert [ep["status"] for ep in result["episodes"]] == ["downloaded", "not_found"]
        assert result["degraded"] is True

    def test_quota_snapshot_refreshed_after_midrun_os_failure(self, tmp_path):
        """The quota snapshot must be refreshed on the FAILURE path.

        The snapshot at the end of the API try block never runs when a call
        raises partway through, so without an explicit refresh in the except
        handler ``_OS.last_quota`` keeps its last healthy reading. The build
        script's quota guard then never fires, the run does not halt, and the
        operator is handed a "check your OpenSubtitles credentials" hint for
        what is really quota exhaustion.
        """
        client = Mock()
        client.user_downloads_remaining = 900

        def _fake_os_call(fn, *args, **kwargs):
            if fn is client.user_info:
                # user_info does not consume download quota; it is the probe
                # that refreshes the library's remaining-downloads attribute.
                client.user_downloads_remaining = 0
                return {"data": {"remaining_downloads": 0}}
            raise Exception("406 quota exceeded")

        with (
            patch.object(ts._OS, "failed", False),
            patch.object(ts._OS, "last_quota", {"remaining": 900, "as_of": 0.0}),
            patch.object(ts._OS, "last_logged_remaining", 900),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=1),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=client),
            patch.object(ts, "os_api_call", side_effect=_fake_os_call),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)
            # Read inside the patch context -- patch.object restores _OS on exit.
            quota = ts.get_last_quota()

        assert result["degraded"] is True
        assert quota is not None
        # Stale value was 900; the refresh must have picked up the real 0.
        assert quota["remaining"] == 0

    def test_quota_refresh_probe_failure_does_not_break_harvest(self, tmp_path):
        """Failing to MEASURE the quota must never fail the harvest.

        Here the re-probe itself raises (network down). ``download_subtitles``
        must still return a normal degraded result rather than propagating.
        """
        client = Mock()
        # A client shape that also makes the snapshot itself fail.
        client.user_downloads_remaining = "not-an-int"

        with (
            patch.object(ts._OS, "failed", False),
            patch.object(ts._OS, "last_quota", {"remaining": 900, "as_of": 0.0}),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=1),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=client),
            patch.object(ts, "os_api_call", side_effect=Exception("connection reset")),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert result["degraded"] is True
        assert all(ep["status"] == "not_found" for ep in result["episodes"])


@pytest.mark.unit
class TestDownloadSubtitlesLoginFailureDegradation:
    """Sibling coverage for the OTHER arm of the ``or`` in download_subtitles'
    return dict: OpenSubtitles fails at LOGIN time (``_OS.failed=True``), so
    ``_get_os_client`` returns None and the API block is skipped entirely --
    ``os_failed_this_season`` never gets set. If a future edit simplified the
    return expression to just ``os_failed_this_season`` (a plausible move,
    since it is the local and newer variable), this exact scenario would
    silently report ``degraded=False``. This test pins the ``_OS.failed`` arm
    directly so that regression can't hide behind the mid-run tests above."""

    def test_degraded_when_os_failed_at_login_and_scrapers_find_nothing(self, tmp_path):
        with (
            patch.object(ts._OS, "failed", True),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=1),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=None),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert all(ep["status"] == "not_found" for ep in result["episodes"])
        assert result["degraded"] is True


_VALID_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello world, this is a padding test line.\n\n"


@pytest.mark.unit
class TestDownloadSubtitlesOpenSubtitlesCredit:
    """Coverage for the by_source misattribution bug: the OpenSubtitles bulk
    path moves a freshly-downloaded SRT to exactly the path
    ``find_existing_subtitle`` looks at, so the per-episode triage loop --
    which checks the filesystem BEFORE checking ``api_srt_map`` -- saw the
    file and reported every OpenSubtitles download as a cache hit. Run
    summaries under-reported OpenSubtitles usage by ~99%, hiding the June
    2026 quota wall."""

    def test_fresh_download_credited_to_opensubtitles_not_cache(self, tmp_path):
        """S01E02 is downloaded fresh via the OpenSubtitles bulk path this
        run. It must be reported as status='downloaded', source
        ='opensubtitles_api' -- NOT status='cached', source='cache', even
        though the file now sits on disk at the same path
        find_existing_subtitle checks."""
        series_cache_dir = tmp_path / "data" / "999"
        series_cache_dir.mkdir(parents=True)

        subtitle = types.SimpleNamespace(episode_number=2, season_number=1, release=None)
        search_response = types.SimpleNamespace(data=[subtitle])
        os_client = Mock()

        def fake_os_api_call(func, *args, **kwargs):
            if func is os_client.search:
                return search_response
            if func is os_client.download_and_save:
                raw = tmp_path / "raw_download.srt"
                raw.write_text(_VALID_SRT, encoding="utf-8")
                return str(raw)
            raise AssertionError(f"unexpected os_api_call target: {func}")

        with (
            patch.object(ts._OS, "failed", False),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=2),
            patch.object(ts, "fetch_season_episodes", return_value=[]),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=os_client),
            patch.object(ts, "os_api_call", side_effect=fake_os_api_call),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        by_code = {ep["code"]: ep for ep in result["episodes"]}
        assert by_code["S01E02"]["status"] == "downloaded"
        assert by_code["S01E02"]["source"] == "opensubtitles_api"

    def test_preexisting_file_still_credited_to_cache(self, tmp_path):
        """S01E01 already has a valid SRT on disk before this run starts (no
        download happened for it this run). It must stay status='cached',
        source='cache' -- the fix must not over-credit OpenSubtitles for
        files it didn't touch this run."""
        series_cache_dir = tmp_path / "data" / "999"
        series_cache_dir.mkdir(parents=True)
        (series_cache_dir / "Test Show - S01E01.srt").write_text(_VALID_SRT, encoding="utf-8")

        # Distinct dialogue from _VALID_SRT so the cross-episode dedup guard
        # (_reject_content_duplicates) doesn't mistake this for a mislabeled
        # copy of S01E01 and rewrite it back to not_found.
        distinct_srt = "1\n00:00:03,000 --> 00:00:04,000\nA completely different episode line.\n\n"

        subtitle = types.SimpleNamespace(episode_number=2, season_number=1, release=None)
        search_response = types.SimpleNamespace(data=[subtitle])
        os_client = Mock()

        def fake_os_api_call(func, *args, **kwargs):
            if func is os_client.search:
                return search_response
            if func is os_client.download_and_save:
                raw = tmp_path / "raw_download.srt"
                raw.write_text(distinct_srt, encoding="utf-8")
                return str(raw)
            raise AssertionError(f"unexpected os_api_call target: {func}")

        with (
            patch.object(ts._OS, "failed", False),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=_mock_config(tmp_path),
            ),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=2),
            patch.object(ts, "fetch_season_episodes", return_value=[]),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_get_os_client", return_value=os_client),
            patch.object(ts, "os_api_call", side_effect=fake_os_api_call),
            patch.object(ts, "run_jobs", return_value={}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        by_code = {ep["code"]: ep for ep in result["episodes"]}
        assert by_code["S01E01"]["status"] == "cached"
        assert by_code["S01E01"]["source"] == "cache"
        assert by_code["S01E02"]["status"] == "downloaded"
        assert by_code["S01E02"]["source"] == "opensubtitles_api"
