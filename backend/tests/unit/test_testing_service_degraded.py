"""Tests for degraded-season detection.

A "degraded" season is one where the measurement is not trustworthy: the
OpenSubtitles path was unavailable AND nothing was retrieved. Recording 0%
coverage for such a season skip-lists it for 30 days on the strength of an
infrastructure failure -- the defect that cost 664 seasons in June 2026.
"""

from unittest.mock import Mock, patch

import pytest

import app.matcher.testing_service as ts
from app.matcher.testing_service import _is_degraded


@pytest.mark.unit
class TestIsDegraded:
    def test_not_degraded_when_episodes_retrieved(self):
        episodes = [
            {"code": "S01E01", "status": "downloaded", "path": "/x.srt", "source": "addic7ed"},
            {"code": "S01E02", "status": "not_found", "path": None, "source": None},
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

    def test_not_degraded_when_os_fails_midrun_but_scrapers_find_something(self, tmp_path):
        scraper_hit = {
            "code": "S01E01",
            "status": "downloaded",
            "path": str(tmp_path / "Test Show - S01E01.srt"),
            "source": "addic7ed",
        }
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
            patch.object(ts, "run_jobs", return_value={"S01E01": scraper_hit}),
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert result["episodes"] == [scraper_hit]
        assert result["degraded"] is False
