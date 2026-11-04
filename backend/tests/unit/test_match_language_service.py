"""Deciding a job's match language (#241, phase 2)."""

import pytest

from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob
from app.services import match_language_service as svc
from tests.unit.conftest import _unit_session_factory


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    state = {"details": {"name": "Babylon Berlin", "original_language": "de"}}

    async def fake_config():
        return AppConfig(tmdb_api_key="tok", subtitles_cache_path=str(tmp_path))

    monkeypatch.setattr("app.services.config_service.get_config", fake_config)
    monkeypatch.setattr(svc.tmdb_client, "fetch_show_details", lambda tmdb_id: state["details"])
    state["data"] = tmp_path / "data"
    return state


def _references(folder, *codes):
    folder.mkdir(parents=True, exist_ok=True)
    for code in codes:
        (folder / f"Babylon Berlin - {code}.srt").write_text("1\n", "utf-8")


async def _job(**kw) -> int:
    kw.setdefault("content_type", ContentType.TV)
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="BABYLON_BERLIN_S1D1",
            detected_title="Babylon Berlin",
            tmdb_id=70523,
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
    async def test_english_show_matches_in_english(self, stubs):
        stubs["details"] = {"original_language": "en"}
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "en"
        job = await _get(job_id)
        assert (job.content_language, job.match_language) == ("en", "en")

    async def test_no_references_in_the_show_language_means_english(self, stubs):
        _references(stubs["data"] / "70523", "S01E01", "S01E02")
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "en"
        job = await _get(job_id)
        assert (job.content_language, job.match_language) == ("de", "en")

    async def test_references_in_the_show_language_decide_it(self, stubs):
        _references(stubs["data"] / "70523@de", "S01E01", "S01E02")
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "de"
        assert (await _get(job_id)).match_language == "de"

    async def test_one_reference_is_too_few(self, stubs):
        _references(stubs["data"] / "70523@de", "S01E01", "S02E01")
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "en"

    async def test_tvdb_job_reads_its_own_language_folder(self, stubs):
        _references(stubs["data"] / "70523@de", "S01E01", "S01E02")
        job_id = await _job(episode_namespace="tvdb")
        assert await svc.decide_job_match_language(job_id) == "en"
        _references(stubs["data"] / "70523@tvdb@de", "S01E01", "S01E02")
        assert await svc.decide_job_match_language(job_id) == "de"

    async def test_tmdb_failure_leaves_the_job_alone(self, stubs):
        stubs["details"] = None
        job_id = await _job(match_language="de", content_language="de")
        assert await svc.decide_job_match_language(job_id) == "de"
        job = await _get(job_id)
        assert (job.content_language, job.match_language) == ("de", "de")

    async def test_unknown_language_means_english(self, stubs):
        stubs["details"] = {"original_language": "xx"}
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "en"
        job = await _get(job_id)
        assert (job.content_language, job.match_language) == (None, "en")

    async def test_movie_job_is_not_decided(self, stubs):
        job_id = await _job(content_type=ContentType.MOVIE)
        assert await svc.decide_job_match_language(job_id) == "en"
        assert (await _get(job_id)).content_language is None

    async def test_never_raises(self, stubs, monkeypatch):
        def boom(tmdb_id):
            raise RuntimeError("TMDB down")

        monkeypatch.setattr(svc.tmdb_client, "fetch_show_details", boom)
        job_id = await _job()
        assert await svc.decide_job_match_language(job_id) == "en"
