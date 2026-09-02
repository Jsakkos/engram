"""Tests for degraded-season detection.

A "degraded" season is one where the measurement is not trustworthy: the
OpenSubtitles path was unavailable AND nothing was retrieved. Recording 0%
coverage for such a season skip-lists it for 30 days on the strength of an
infrastructure failure -- the defect that cost 664 seasons in June 2026.
"""

import pytest

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
