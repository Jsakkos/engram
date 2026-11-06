"""A detected Play-All is deselected before the rip starts (#634).

Runs identify_disc with the real analyst over the #634 bug-report scans, so the
pin covers detection and the pre-rip decision together: the Play-All row must
leave identification unselected, and the episodes must still rip.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.config_service as config_service
from app.matcher import tmdb_client
from app.models import JobState
from tests.unit.test_identify_rip_first_gates import (
    _bare_coord,
    _job_titles,
    _make_analysis,
    _reload_job,
    _seed_identifying_job,
    gate_env,  # noqa: F401 (pytest fixture)
)
from tests.unit.test_play_all_structural import _analyst, _scan, _tv_signal


@pytest.fixture
def keep_env(gate_env, monkeypatch):  # noqa: F811
    """The reporter's settings: extras kept, no backup; TMDB runtimes stubbed."""
    cfg = SimpleNamespace(extras_policy="keep", backup_before_rip=False, backup_path=None)

    async def fake_get_config():
        return cfg

    monkeypatch.setattr(config_service, "get_config", fake_get_config)
    monkeypatch.setattr(tmdb_client, "fetch_season_episode_runtimes", Mock(return_value=[]))


@pytest.mark.unit
class TestPlayAllSkippedBeforeRip:
    @pytest.mark.parametrize(
        ("scan", "label", "play_all"),
        [
            ("project_runway_disc1_dvd.txt", "PROJECT_RUNWAY_DISC1", 0),
            ("project_runway_disc3_dvd.txt", "PROJECT_RUNWAY_DISC3", 0),
        ],
    )
    async def test_play_all_deselected_episodes_rip(self, keep_env, scan, label, play_all):
        titles = _scan(scan)
        real = _analyst().analyze(titles, label, tmdb_signal=_tv_signal("Project Runway"))
        assert real.play_all_title_indices == [play_all]

        analysis = _make_analysis(real.content_type, "Project Runway", season=1, tmdb_id=1685)
        analysis.play_all_title_indices = real.play_all_title_indices
        job_id = await _seed_identifying_job(label)
        coord = _bare_coord(analysis, titles, label)
        coord._run_ripping = AsyncMock()

        await coord.identify_disc(job_id)

        assert (await _reload_job(job_id)).state == JobState.RIPPING
        coord._run_ripping.assert_awaited_once_with(job_id)
        rows = {t.title_index: t for t in await _job_titles(job_id)}
        assert rows[play_all].is_selected is False
        assert rows[play_all].is_extra is True
        assert all(t.is_selected for i, t in rows.items() if i != play_all)
