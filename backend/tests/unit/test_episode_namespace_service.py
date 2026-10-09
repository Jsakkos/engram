"""Deciding and switching a job's episode namespace (spec 2026-10-08)."""

import json

import pytest

from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob
from app.models.show_ordering import ShowOrderingPreference
from app.services import episode_namespace_service as svc
from tests.unit.conftest import _unit_session_factory

TMDB = [{"episode_number": i, "name": f"Ep {i}", "air_date": ""} for i in range(1, 25)]
TMDB[0]["name"] = "Secret Origins"
TVDB = [{"episode_number": i, "name": f"Ep {i - 2}", "air_date": ""} for i in range(1, 27)]
for i, part in enumerate(("(1)", "(2)", "(3)")):
    TVDB[i]["name"] = f"Secret Origins {part}"


@pytest.fixture
def stubs(monkeypatch):
    state = {"tvdb": TVDB}

    async def fake_config():
        return AppConfig(tmdb_api_key="tok", tvdb_api_key="k")

    monkeypatch.setattr("app.services.config_service.get_config", fake_config)
    monkeypatch.setattr(svc.tmdb_client, "fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(svc.tmdb_client, "fetch_season_episodes", lambda s, n, k: TMDB)
    monkeypatch.setattr(svc.tvdb_client, "fetch_season_roster", lambda t, n, api_key: state["tvdb"])
    return state


async def _job(**kw) -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
            detected_season=1,
            **kw,
        )
        s.add(job)
        await s.commit()
        return job.id


async def _get(job_id):
    async with _unit_session_factory() as s:
        return await s.get(DiscJob, job_id)


@pytest.mark.unit
class TestDecide:
    async def test_records_divergence_and_stays_tmdb(self, stubs):
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert json.loads(job.tvdb_divergence_json) == {"season": 1, "tmdb": 24, "tvdb": 26}

    async def test_show_preference_tvdb_starts_in_tvdb(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
            await s.commit()
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tvdb"
        job = await _get(job_id)
        assert job.episode_namespace == "tvdb"
        assert json.loads(job.episode_crosswalk_json)["S01E04"] == "S01E02"
        assert job.episode_namespace_note is None

    async def test_preference_tvdb_but_tvdb_down_falls_back_with_note(self, stubs):
        stubs["tvdb"] = None
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
            await s.commit()
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert "TheTVDB unavailable" in job.episode_namespace_note

    async def test_no_key_is_a_no_op(self, stubs, monkeypatch):
        async def no_key():
            return AppConfig(tmdb_api_key="tok", tvdb_api_key="")

        monkeypatch.setattr("app.services.config_service.get_config", no_key)
        monkeypatch.delenv("TVDB_API_KEY", raising=False)
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert (await _get(job_id)).tvdb_divergence_json is None

    async def test_movie_job_is_a_no_op(self, stubs):
        async with _unit_session_factory() as s:
            job = DiscJob(drive_id="E:", volume_label="M", content_type=ContentType.MOVIE)
            s.add(job)
            await s.commit()
            job_id = job.id
        assert await svc.decide_job_namespace(job_id) == "tmdb"

    async def test_unexpected_error_never_raises(self, stubs, monkeypatch):
        def boom(s, n, k):
            raise RuntimeError("tmdb exploded")

        monkeypatch.setattr(svc.tmdb_client, "fetch_season_episodes", boom)
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert (await _get(job_id)).episode_namespace == "tmdb"


@pytest.mark.unit
class TestSwitchAndDismiss:
    async def test_switch_to_tvdb_sets_job_and_show(self, stubs):
        job_id = await _job()
        await svc.switch_job_namespace(job_id, "tvdb")
        job = await _get(job_id)
        assert job.episode_namespace == "tvdb"
        assert job.episode_crosswalk_json
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.ordering == "tvdb" and pref.tvdb_id == 76290

    async def test_switch_to_tvdb_when_tvdb_down_raises_and_changes_nothing(self, stubs):
        stubs["tvdb"] = None
        job_id = await _job()
        with pytest.raises(svc.TvdbUnavailableError):
            await svc.switch_job_namespace(job_id, "tvdb")
        assert (await _get(job_id)).episode_namespace == "tmdb"

    async def test_switch_back_to_tmdb_clears_crosswalk_and_preference(self, stubs):
        job_id = await _job()
        await svc.switch_job_namespace(job_id, "tvdb")
        await svc.switch_job_namespace(job_id, "tmdb")
        job = await _get(job_id)
        assert job.episode_namespace == "tmdb" and job.episode_crosswalk_json is None
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.ordering == ""  # falls back to the global default

    async def test_dismiss_does_not_override_global_ordering(self, stubs):
        await svc.dismiss_tvdb_suggestion(1618)
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.tvdb_suggestion_dismissed is True
        assert pref.ordering == ""
