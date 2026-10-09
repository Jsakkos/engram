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


class _SessionTracker:
    """Wraps the unit session factory, counting sessions currently open."""

    def __init__(self):
        self.open = 0

    def __call__(self):
        tracker = self
        inner = _unit_session_factory()

        class _Ctx:
            async def __aenter__(self):
                tracker.open += 1
                return await inner.__aenter__()

            async def __aexit__(self, *exc):
                try:
                    return await inner.__aexit__(*exc)
                finally:
                    tracker.open -= 1

        return _Ctx()


@pytest.fixture
def tracked(stubs, monkeypatch):
    tracker = _SessionTracker()
    monkeypatch.setattr(svc, "async_session", tracker)
    seen = {"config": 0, "rosters": 0}

    async def fake_config():
        assert tracker.open == 0, "session open during get_config"
        seen["config"] += 1
        return AppConfig(tmdb_api_key="tok", tvdb_api_key="k")

    async def fake_rosters(tmdb_id, season, config, tvdb_id):
        assert tracker.open == 0, "session open during _rosters"
        seen["rosters"] += 1
        return 76290, TMDB, TVDB

    monkeypatch.setattr("app.services.config_service.get_config", fake_config)
    monkeypatch.setattr(svc, "_rosters", fake_rosters)
    return seen


async def _pref(tmdb_id=1618):
    async with _unit_session_factory() as s:
        return await s.get(ShowOrderingPreference, tmdb_id)


@pytest.mark.unit
class TestSessionDiscipline:
    async def test_decide_holds_no_session_across_fetch(self, tracked):
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert tracked["config"] == 1 and tracked["rosters"] == 1

    async def test_switch_to_tvdb_holds_no_session_across_fetch(self, tracked):
        job_id = await _job()
        job = await svc.switch_job_namespace(job_id, "tvdb")
        assert job.episode_namespace == "tvdb"
        assert tracked["config"] == 1 and tracked["rosters"] == 1


@pytest.mark.unit
class TestDecideEdges:
    async def test_stores_tvdb_id_on_existing_pref(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering=""))
            await s.commit()
        job_id = await _job()
        await svc.decide_job_namespace(job_id)
        assert (await _pref()).tvdb_id == 76290

    async def test_self_heals_tvdb_job_without_tvdb_pref(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering=""))
            await s.commit()
        job_id = await _job(episode_namespace="tvdb", episode_crosswalk_json='{"a": "b"}')
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert job.episode_namespace == "tmdb" and job.episode_crosswalk_json is None

    async def test_tvdb_down_clears_crosswalk(self, stubs):
        stubs["tvdb"] = None
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
            await s.commit()
        job_id = await _job(episode_namespace="tvdb", episode_crosswalk_json='{"a": "b"}')
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert job.episode_crosswalk_json is None
        assert "TheTVDB unavailable" in job.episode_namespace_note


@pytest.mark.unit
class TestSwitchEdges:
    async def test_failed_switch_creates_no_pref(self, stubs):
        stubs["tvdb"] = None
        job_id = await _job()
        with pytest.raises(svc.TvdbUnavailableError):
            await svc.switch_job_namespace(job_id, "tvdb")
        assert await _pref() is None

    async def test_switch_to_tmdb_keeps_dvd_pref(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="dvd"))
            await s.commit()
        job_id = await _job(episode_namespace="tvdb")
        await svc.switch_job_namespace(job_id, "tmdb")
        assert (await _pref()).ordering == "dvd"
        assert (await _get(job_id)).episode_namespace == "tmdb"

    async def test_switch_to_tvdb_clears_divergence(self, stubs):
        job_id = await _job(tvdb_divergence_json='{"season": 1}')
        await svc.switch_job_namespace(job_id, "tvdb")
        assert (await _get(job_id)).tvdb_divergence_json is None

    async def test_bad_namespace(self, stubs):
        job_id = await _job()
        with pytest.raises(ValueError):
            await svc.switch_job_namespace(job_id, "bogus")

    async def test_non_tv_job(self, stubs):
        async with _unit_session_factory() as s:
            job = DiscJob(drive_id="E:", volume_label="M", content_type=ContentType.MOVIE)
            s.add(job)
            await s.commit()
            job_id = job.id
        with pytest.raises(ValueError):
            await svc.switch_job_namespace(job_id, "tvdb")

    async def test_no_season(self, stubs):
        async with _unit_session_factory() as s:
            job = DiscJob(
                drive_id="E:", volume_label="T", content_type=ContentType.TV, tmdb_id=1618
            )
            s.add(job)
            await s.commit()
            job_id = job.id
        with pytest.raises(ValueError):
            await svc.switch_job_namespace(job_id, "tvdb")


@pytest.mark.unit
class TestDismissEdges:
    async def test_dismiss_existing_pref_keeps_ordering(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="dvd"))
            await s.commit()
        await svc.dismiss_tvdb_suggestion(1618)
        pref = await _pref()
        assert pref.ordering == "dvd" and pref.tvdb_suggestion_dismissed is True
