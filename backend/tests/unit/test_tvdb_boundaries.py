"""TMDB-identity boundaries for TheTVDB-numbered jobs (spec 2026-10-08)."""

import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from app.models.disc_job import ContentType, DiscJob, DiscTitle, TitleState
from app.services.matching_coordinator import MatchingCoordinator
from tests.unit.conftest import _unit_session_factory

CROSSWALK = json.dumps({"S01E04": "S01E02", "S01E05": "S01E03"})


async def _job(namespace: str = "tvdb", crosswalk: str | None = CROSSWALK) -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
            detected_season=1,
            episode_namespace=namespace,
            episode_crosswalk_json=crosswalk,
        )
        s.add(job)
        await s.commit()
        return job.id


async def _tvdb_job() -> int:
    return await _job("tvdb")


def _mapping(index, season, episode, episodes=None):
    return SimpleNamespace(
        index=index,
        season=season,
        episode=episode,
        episodes=episodes or [episode],
        title_type="Episode",
        source="discdb",
        episode_title="x",
    )


async def _noop(*_a, **_k):
    return None


def _bare_coordinator() -> MatchingCoordinator:
    coord = MatchingCoordinator.__new__(MatchingCoordinator)
    coord._discdb_mappings = {}
    coord._episode_runtimes = {}
    coord._episode_runtimes_locks = defaultdict(asyncio.Lock)
    coord._subtitle_ready = {}
    coord._subtitle_tasks = {}
    return coord


@pytest.mark.unit
class TestInboundHints:
    async def test_discdb_hint_translated_into_tvdb_numbering(self, monkeypatch):
        job_id = await _tvdb_job()
        coord = _bare_coordinator()
        coord._discdb_mappings = {job_id: [_mapping(3, 1, 2)]}  # TMDB S01E02
        monkeypatch.setattr(
            "app.services.matching_coordinator.ws_manager.broadcast_title_update", _noop
        )
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=3, duration_seconds=1300)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is True
            assert title.matched_episode == "S01E04"
            assert json.loads(title.match_details)["matched_episode"] == "S01E04"

    async def test_untranslatable_hint_is_dropped(self):
        job_id = await _tvdb_job()
        coord = _bare_coordinator()
        coord._discdb_mappings = {job_id: [_mapping(0, 1, 1)]}  # TMDB S01E01 = 3 TVDB parts
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=0, duration_seconds=1300)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is False
            assert title.matched_episode is None

    async def test_tmdb_job_passes_hint_through_unchanged(self, monkeypatch):
        job_id = await _job("tmdb", crosswalk=None)
        coord = _bare_coordinator()
        coord._discdb_mappings = {job_id: [_mapping(3, 1, 2)]}
        monkeypatch.setattr(
            "app.services.matching_coordinator.ws_manager.broadcast_title_update", _noop
        )
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=3, duration_seconds=1300)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is True
            assert title.matched_episode == "S01E02"
            assert title.state == TitleState.MATCHED

    async def test_multi_episode_hint_translated_and_parked_for_review(self, monkeypatch):
        job_id = await _tvdb_job()
        coord = _bare_coordinator()
        # TMDB S01E02-E03 (one combined title) -> TVDB S01E04-E05
        coord._discdb_mappings = {job_id: [_mapping(4, 1, 2, episodes=[2, 3])]}
        monkeypatch.setattr(
            "app.services.matching_coordinator.ws_manager.broadcast_title_update", _noop
        )
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=4, duration_seconds=2600)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is True
            assert title.matched_episode == "S01E04-E05"
            assert title.state == TitleState.REVIEW

    async def test_corrupt_crosswalk_drops_the_hint(self):
        job_id = await _job("tvdb", crosswalk="{not json")
        coord = _bare_coordinator()
        coord._discdb_mappings = {job_id: [_mapping(3, 1, 2)]}
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=3, duration_seconds=1300)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is False
            assert title.matched_episode is None


@pytest.mark.unit
async def test_match_single_file_binds_the_job_namespace():
    from app.core.episode_namespace import current_namespace

    job_id = await _tvdb_job()
    coord = _bare_coordinator()
    seen = {}

    async def fake_run(*a, **k):
        seen["ns"] = current_namespace()

    coord._run_match_single_file = fake_run
    await coord.match_single_file(job_id, 1, None)
    assert seen["ns"] == "tvdb"
    assert current_namespace() == "tmdb"


@pytest.mark.unit
async def test_subtitle_download_decides_and_binds_the_namespace(monkeypatch):
    from app.core.episode_namespace import current_namespace

    job_id = await _tvdb_job()
    coord = _bare_coordinator()
    coord._subtitle_ready[job_id] = asyncio.Event()
    decided = []

    async def fake_decide(jid):
        # The stored namespace wins: download binds what matching will read,
        # even when decide's return value disagrees (or decide failed).
        decided.append(jid)
        return "tmdb"

    seen = {}

    def fake_download(show_name, season, **kwargs):
        seen["ns"] = current_namespace()
        seen["kwargs"] = kwargs
        return {"episodes": [], "show_name": "X"}

    monkeypatch.setattr("app.services.episode_namespace_service.decide_job_namespace", fake_decide)
    monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
    monkeypatch.setattr(
        "app.services.matching_coordinator.ws_manager.broadcast_subtitle_event", _noop
    )

    await coord.download_subtitles(job_id, "X", 1, tmdb_id=1618)

    assert decided == [job_id]
    assert seen["ns"] == "tvdb"
    assert seen["kwargs"] == {"tmdb_id": 1618, "use_precomputed": False}
    assert coord._subtitle_ready[job_id].is_set()


@pytest.mark.unit
async def test_subtitle_download_keeps_precomputed_for_tmdb_jobs(monkeypatch):
    from app.core.episode_namespace import current_namespace

    job_id = await _job("tmdb", crosswalk=None)
    coord = _bare_coordinator()

    async def fake_decide(jid):
        return None

    seen = {}

    def fake_download(show_name, season, **kwargs):
        seen["ns"] = current_namespace()
        seen["kwargs"] = kwargs
        return {"episodes": [], "show_name": "X"}

    monkeypatch.setattr("app.services.episode_namespace_service.decide_job_namespace", fake_decide)
    monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
    monkeypatch.setattr(
        "app.services.matching_coordinator.ws_manager.broadcast_subtitle_event", _noop
    )

    await coord.download_subtitles(job_id, "X", 1, tmdb_id=1618)

    assert seen["ns"] == "tmdb"
    assert seen["kwargs"] == {"tmdb_id": 1618, "use_precomputed": True}


@pytest.mark.unit
@pytest.mark.parametrize("namespace, use_precomputed", [("tvdb", False), ("tmdb", True)])
async def test_all_seasons_download_binds_the_job_namespace(
    monkeypatch, namespace, use_precomputed
):
    from app.core.episode_namespace import current_namespace

    job_id = await _job(namespace)
    coord = _bare_coordinator()
    seen = []

    def fake_download(show_name, season, **kwargs):
        seen.append((season, current_namespace(), kwargs))
        return {"episodes": [], "show_name": "X"}

    monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
    monkeypatch.setattr(
        "app.services.matching_coordinator.ws_manager.broadcast_subtitle_event", _noop
    )

    await coord.download_subtitles_all_seasons(job_id, "X", [1, 2], tmdb_id=1618)

    assert seen == [
        (1, namespace, {"tmdb_id": 1618, "use_precomputed": use_precomputed}),
        (2, namespace, {"tmdb_id": 1618, "use_precomputed": use_precomputed}),
    ]


@pytest.mark.unit
class TestEpisodeRuntimesNamespace:
    async def test_tvdb_context_uses_namespace_runtimes(self, monkeypatch):
        from app.core.episode_namespace import namespace_context

        calls = {}

        def fake_season_runtimes(show_id, season, key):
            calls["tvdb"] = (show_id, season, key)
            return [22, 22]

        def fake_tmdb_runtimes(*a, **k):
            raise AssertionError("TMDB runtimes must not be used for a tvdb job")

        async def fake_get_config():
            return SimpleNamespace(tmdb_api_key="tok")

        monkeypatch.setattr("app.core.episode_namespace.season_runtimes", fake_season_runtimes)
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_season_episode_runtimes", fake_tmdb_runtimes
        )
        monkeypatch.setattr("app.services.config_service.get_config", fake_get_config)

        coord = _bare_coordinator()
        with namespace_context("tvdb"):
            runtimes = await coord._episode_runtimes_for_job(7, 1618, "JL", 1)
        assert runtimes == [22, 22]
        assert calls["tvdb"] == ("1618", 1, "tok")

    async def test_tmdb_context_uses_tmdb_runtimes(self, monkeypatch):
        def fake_season_runtimes(*a, **k):
            raise AssertionError("namespace runtimes must not be used for a tmdb job")

        monkeypatch.setattr("app.core.episode_namespace.season_runtimes", fake_season_runtimes)
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_season_episode_runtimes",
            lambda show_id, season: [44],
        )

        coord = _bare_coordinator()
        assert await coord._episode_runtimes_for_job(8, 1618, "JL", 1) == [44]

    async def test_forget_episode_runtimes_drops_the_cache(self):
        coord = _bare_coordinator()
        coord._episode_runtimes[9] = [22]
        coord.forget_episode_runtimes(9)
        assert 9 not in coord._episode_runtimes
