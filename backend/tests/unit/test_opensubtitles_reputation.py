"""OpenSubtitles candidates are taken by reputation, not in API order.

The first result per episode used to win. For Babylon Berlin (German) that was
an anonymous upload machine-translated from Hungarian subtitles; its wording
drifted from the dialogue and cost the episode most of its matching votes.
"""

import types
from unittest.mock import Mock, patch

import pytest

import app.matcher.testing_service as ts
from tests.unit.test_testing_service_degraded import _VALID_SRT, _mock_config, _scheduler_run


def _candidate(file_id, downloads, *, trusted=False, machine=False):
    return types.SimpleNamespace(
        episode_number=2,
        season_number=1,
        release=None,
        file_id=file_id,
        download_count=downloads,
        from_trusted=trusted,
        machine_translated=machine,
        ai_translated=False,
    )


@pytest.mark.unit
class TestReputationRanking:
    def test_translated_last_then_trusted_then_downloads(self):
        ranked = ts._rank_by_reputation(
            [
                _candidate("machine", 900, machine=True),
                _candidate("few", 10),
                _candidate("trusted", 5, trusted=True),
                _candidate("many", 400),
            ]
        )
        assert [s.file_id for s in ranked] == ["trusted", "many", "few", "machine"]

    def test_download_loop_takes_the_best_ranked(self, tmp_path):
        (tmp_path / "data" / "999").mkdir(parents=True)
        response = types.SimpleNamespace(
            data=[_candidate("machine", 900, machine=True), _candidate("trusted", 40, trusted=True)]
        )
        os_client = Mock()
        downloaded = []

        def fake_os_api_call(func, *args, **kwargs):
            if func is os_client.search:
                return response
            if func is os_client.download_and_save:
                downloaded.append(args[0].file_id)
                raw = tmp_path / f"raw-{args[0].file_id}.srt"
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
            patch.object(ts, "_os_client_and_reason", return_value=(os_client, None)),
            patch.object(ts, "os_api_call", side_effect=fake_os_api_call),
            patch.object(ts, "run_jobs", return_value=_scheduler_run()),
        ):
            ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        assert downloaded == ["trusted"]
