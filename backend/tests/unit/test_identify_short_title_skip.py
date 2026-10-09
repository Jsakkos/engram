"""Pre-rip short-title skip, wired through identify_disc.

With ``extras_policy == "skip"`` a TV disc's too-short tracks are marked SKIPPED
before the rip, in the same state a manual "skip the rip" produces, so the
dashboard shows them and the existing un-skip still works. The decision rule
itself is covered in test_episode_runtime.py; this file pins the wiring.
"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app.services.config_service as config_service
import app.services.identification_coordinator as idc
from app.core.analyst import TitleInfo
from app.matcher import tmdb_client
from app.models import JobState
from app.models.disc_job import ContentType, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory
from tests.unit.test_identify_rip_first_gates import (
    _bare_coord,
    _job_titles,
    _make_analysis,
    _reload_job,
    _seed_identifying_job,
    gate_env,  # noqa: F401 (pytest fixture)
)


def _ti(index: int, minutes: float) -> TitleInfo:
    return TitleInfo(
        index=index,
        duration_seconds=int(minutes * 60),
        size_bytes=1_000_000_000,
        chapter_count=5,
    )


# Three ~39-minute episodes, a 6-minute and a 10-minute featurette.
_DISC = [_ti(0, 38), _ti(1, 39), _ti(2, 40), _ti(3, 6), _ti(4, 10)]


@pytest.fixture
def short_env(gate_env, monkeypatch):  # noqa: F811
    """Configurable extras policy, stubbed TMDB runtimes, captured title updates."""
    cfg = SimpleNamespace(extras_policy="skip", backup_before_rip=False, backup_path=None)

    async def fake_get_config():
        return cfg

    monkeypatch.setattr(config_service, "get_config", fake_get_config)
    runtimes = Mock(return_value=[38, 39, 40, 38])
    monkeypatch.setattr(tmdb_client, "fetch_season_episode_runtimes", runtimes)

    title_updates: list[tuple[str, str | None]] = []

    async def record_title_update(job_id, title_id, state, **kwargs):
        title_updates.append((state, kwargs.get("match_details")))

    monkeypatch.setattr(idc.ws_manager, "broadcast_title_update", record_title_update)
    return SimpleNamespace(cfg=cfg, runtimes=runtimes, title_updates=title_updates)


async def _identify_tv(*, label="SHOW_S1D1", tmdb_id=1234, content_type=ContentType.TV, **kw):
    job_id = await _seed_identifying_job(label)
    analysis = _make_analysis(content_type, "Show", season=1, tmdb_id=tmdb_id, **kw)
    coord = _bare_coord(analysis, _DISC, label)
    await coord.identify_disc(job_id)
    return job_id, coord, analysis


@pytest.mark.unit
class TestShortTitleSkip:
    async def test_skip_policy_skips_short_tracks_before_rip(self, short_env):
        job_id, coord, _ = await _identify_tv()

        titles = await _job_titles(job_id)
        assert [t.state for t in titles] == [TitleState.PENDING] * 3 + [TitleState.SKIPPED] * 2
        assert [t.is_selected for t in titles] == [True, True, True, False, False]
        details = json.loads(titles[3].match_details)
        assert details["auto_skipped"] is True
        assert details["skipped"] is True
        assert "TMDB" in details["reason"]

        # Rip still proceeds, with only the episodes selected.
        assert (await _reload_job(job_id)).state == JobState.RIPPING
        coord._run_ripping.assert_awaited_once_with(job_id)
        short_env.runtimes.assert_called_once_with("1234", 1)
        assert [s for s, _ in short_env.title_updates] == ["skipped", "skipped"]

    @pytest.mark.parametrize("policy", ["keep", "ask"])
    async def test_other_policies_rip_everything(self, short_env, policy):
        short_env.cfg.extras_policy = policy
        job_id, _, _ = await _identify_tv()

        titles = await _job_titles(job_id)
        assert all(t.state == TitleState.PENDING and t.is_selected for t in titles)
        short_env.runtimes.assert_not_called()

    async def test_movie_disc_untouched(self, short_env):
        job_id, _, _ = await _identify_tv(label="SOME_MOVIE", content_type=ContentType.MOVIE)
        titles = await _job_titles(job_id)
        assert not any(t.state == TitleState.SKIPPED for t in titles)

    async def test_untrusted_identity_uses_disc_evidence_only(self, short_env):
        """An uncorroborated identity's runtimes could be the wrong show's: they
        are not fetched, and the disc's own episode group decides."""
        job_id = await _seed_identifying_job("SHOW_S1D1")
        analysis = _make_analysis(ContentType.TV, "Show", season=1, tmdb_id=1234)
        analysis.identity_unconfirmed = True
        coord = _bare_coord(analysis, _DISC, "SHOW_S1D1")
        await coord.identify_disc(job_id)

        short_env.runtimes.assert_not_called()
        titles = await _job_titles(job_id)
        skipped = [t for t in titles if t.state == TitleState.SKIPPED]
        assert [t.title_index for t in skipped] == [3, 4]
        assert "TMDB" not in json.loads(skipped[0].match_details)["reason"]

    async def test_gate_b_tmdb_lookup_failed_rips_everything(self, short_env):
        """Gate B (TV detected, TMDB lookup failed): the name is untrusted and the
        disc rips first with a name prompt, so nothing is auto-skipped."""
        job_id, coord, _ = await _identify_tv(tmdb_id=None)

        job = await _reload_job(job_id)
        assert json.loads(job.identity_prompt_json)["kind"] == "name"
        coord._run_ripping.assert_awaited_once_with(job_id)
        titles = await _job_titles(job_id)
        assert all(t.state == TitleState.PENDING and t.is_selected for t in titles)
        short_env.runtimes.assert_not_called()

    async def test_auto_skipped_track_can_be_unskipped(self, short_env, monkeypatch):
        """The manual un-skip path accepts an auto-skipped track unchanged."""
        job_id, _, _ = await _identify_tv()
        titles = await _job_titles(job_id)

        jm_module = sys.modules["app.services.job_manager"]
        monkeypatch.setattr(jm_module, "async_session", _unit_session_factory)
        monkeypatch.setattr(job_manager, "_extractor", Mock())

        assert await job_manager.unskip_rip_title(job_id, titles[3].id) is True
        restored = (await _job_titles(job_id))[3]
        assert restored.state == TitleState.PENDING
        assert restored.is_selected is True
