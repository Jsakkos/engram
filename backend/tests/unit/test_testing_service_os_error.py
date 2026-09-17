"""Tests for surfacing WHY OpenSubtitles did not serve a season (#654).

A season with no references used to tell the user to "add an OpenSubtitles API
key" even when the key was fine and the daily download quota had run out. The
result now carries ``os_error`` so the job message can name the cause, and a
spent quota no longer disables OpenSubtitles until the server restarts.
"""

from unittest.mock import Mock, patch

import pytest

import app.matcher.testing_service as ts
from tests.unit.test_testing_service_degraded import _mock_config, _scheduler_run

_QUOTA_MSG = (
    "Download limit reached. Please upgrade your account or wait for your quota to reset (~24hrs)"
)


def _run_download(tmp_path, *, os_client, os_call=None):
    patches = [
        patch(
            "app.services.config_service.get_config_sync",
            return_value=_mock_config(tmp_path),
        ),
        patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
        patch.object(ts, "fetch_season_details", return_value=1),
        patch.object(ts, "fetch_season_episodes", return_value=[]),
        patch.object(ts, "_precomputed_skip_result", return_value=None),
        patch.object(ts, "_get_os_client", return_value=os_client),
        patch.object(ts, "run_jobs", return_value=_scheduler_run()),
    ]
    if os_call is not None:
        patches.append(patch.object(ts, "os_api_call", side_effect=os_call))
    for p in patches:
        p.start()
    try:
        return ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.fixture(autouse=True)
def _fresh_os_state():
    with patch.object(ts, "_OS", ts._OSState()):
        yield


@pytest.mark.unit
class TestDownloadSubtitlesOsError:
    def test_mid_run_failure_text_is_returned(self, tmp_path):
        """The reporter's case: logged in fine, then the quota ran out."""
        client = Mock()
        client.user_downloads_remaining = 0

        def _call(fn, *args, **kwargs):
            if fn is client.user_info:
                return {}
            raise Exception(_QUOTA_MSG)

        result = _run_download(tmp_path, os_client=client, os_call=_call)

        assert result["os_error"] == _QUOTA_MSG
        assert result["degraded"] is True

    def test_login_time_failure_reason_is_returned(self, tmp_path):
        """Quota already spent at login: _get_os_client returns None, and the
        reason it recorded must still reach the job."""
        ts._OS.failed = True
        ts._OS.failure_reason = "daily download quota exhausted"

        result = _run_download(tmp_path, os_client=None)

        assert result["os_error"] == "daily download quota exhausted"

    def test_healthy_opensubtitles_has_no_error(self, tmp_path):
        client = Mock()
        client.user_downloads_remaining = 500

        def _call(fn, *args, **kwargs):
            return Mock(data=[])

        result = _run_download(tmp_path, os_client=client, os_call=_call)

        assert result["os_error"] is None


@pytest.mark.unit
class TestDescribeOsError:
    def test_collapses_whitespace(self):
        assert ts._describe_os_error(Exception("a\n  b\tc")) == "a b c"

    def test_caps_length(self):
        text = ts._describe_os_error(Exception("x" * 1000))
        assert len(text) == ts._OS_ERROR_MAX_CHARS
        assert text.endswith("...")

    def test_empty_message_falls_back_to_type_name(self):
        assert ts._describe_os_error(TimeoutError()) == "TimeoutError"


@pytest.mark.unit
class TestOsFailureExpiry:
    def _config(self):
        config = Mock()
        config.opensubtitles_api_key = "key"
        config.opensubtitles_username = "user"
        config.opensubtitles_password = "pass"
        return config

    @patch("opensubtitlescom.OpenSubtitles")
    def test_quota_lockout_records_reason_and_expires(self, mock_os_api):
        """A spent quota must not disable OpenSubtitles for the life of the
        server: the job message tells the user to retry once it resets."""
        spent = Mock()
        spent.user_downloads_remaining = 0
        mock_os_api.return_value = spent

        assert ts._get_os_client(self._config()) is None
        assert ts._OS.failed is True
        assert ts._OS.failure_reason == "daily download quota exhausted"
        assert ts._OS.retry_at is not None

        # Still inside the lockout: no second login.
        assert ts._get_os_client(self._config()) is None
        assert mock_os_api.call_count == 1

        # The lockout lapses and the quota has refilled.
        ts._OS.retry_at -= ts._OS_QUOTA_RETRY_SECONDS + 1
        refilled = Mock()
        refilled.user_downloads_remaining = 1000
        mock_os_api.return_value = refilled

        assert ts._get_os_client(self._config()) is refilled
        assert ts._OS.failed is False
        assert ts._OS.failure_reason is None
        assert ts._OS.retry_at is None

    @patch("opensubtitlescom.OpenSubtitles")
    def test_login_failure_stays_sticky_with_reason(self, mock_os_api):
        client = Mock()
        mock_os_api.return_value = client
        with patch.object(ts, "os_api_call", side_effect=Exception("401 Unauthorized")):
            assert ts._get_os_client(self._config()) is None

        assert ts._OS.failed is True
        assert ts._OS.failure_reason == "login failed: 401 Unauthorized"
        assert ts._OS.retry_at is None
