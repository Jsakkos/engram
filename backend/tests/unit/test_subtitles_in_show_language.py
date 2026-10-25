"""References in the show's own language (#241, phase 2).

The season is fetched in the show's language first, into its own folder, and the
match-language decision counts what landed. English still downloads, unbound and
byte-identical, for tracks that only have English audio.
"""

import asyncio
import types
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import app.matcher.testing_service as ts
from app.core.match_language import current_match_language, match_language_context
from app.matcher.provider_scheduler import EpisodeJob, run_jobs
from app.models.disc_job import ContentType, DiscJob
from tests.unit.conftest import _unit_session_factory
from tests.unit.test_testing_service_degraded import _VALID_SRT, _mock_config, _scheduler_run
from tests.unit.test_tvdb_boundaries import _bare_coordinator, _noop


@pytest.mark.unit
@pytest.mark.parametrize(
    "language,param", [("en", "en"), ("de", "de"), ("pt", "pt-pt,pt-br"), ("zh", "zh-cn,zh-tw")]
)
def test_opensubtitles_language_parameter(language, param):
    assert ts.opensubtitles_languages(language) == param


def _download(tmp_path, language):
    """Run download_subtitles under ``language`` with one OpenSubtitles hit for E01."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    hit = types.SimpleNamespace(episode_number=1, season_number=1, release=None, file_id="f1")
    os_client = Mock()
    seen = {}

    def fake_os_api_call(func, *args, **kwargs):
        if func is os_client.search:
            seen["languages"] = kwargs["languages"]
            return types.SimpleNamespace(data=[hit])
        if func is os_client.download_and_save:
            raw = tmp_path / "raw.srt"
            raw.write_text(_VALID_SRT, encoding="utf-8")
            return str(raw)
        raise AssertionError(f"unexpected os_api_call target: {func}")

    def fake_run_jobs(jobs, workers):
        seen["jobs"] = jobs
        return _scheduler_run()

    with (
        match_language_context(language),
        patch.object(ts._OS, "failed", False),
        patch("app.services.config_service.get_config_sync", return_value=_mock_config(tmp_path)),
        patch.object(ts, "fetch_show_details", return_value={"name": "Babylon Berlin"}),
        patch.object(ts, "fetch_season_details", return_value=2),
        patch.object(ts, "fetch_season_episodes", return_value=[]),
        patch.object(ts, "_precomputed_skip_result", return_value=None),
        patch.object(ts, "_os_client_and_reason", return_value=(os_client, None)),
        patch.object(ts, "os_api_call", side_effect=fake_os_api_call),
        patch.object(ts, "run_jobs", side_effect=fake_run_jobs),
    ):
        result = ts.download_subtitles("Babylon Berlin", 1, tmdb_id=70523, use_precomputed=False)
    return result, seen


@pytest.mark.unit
class TestDownloadInTheBoundLanguage:
    def test_english_request_and_folder_unchanged(self, tmp_path):
        result, seen = _download(tmp_path, "en")
        assert seen["languages"] == "en"
        assert Path(result["episodes"][0]["path"]).parent.name == "70523"
        assert [list(j.pending_providers) for j in seen["jobs"]] == [["addic7ed", "tvsubtitles"]]
        assert [j.language for j in seen["jobs"]] == ["en"]

    def test_german_is_requested_and_lands_in_its_folder(self, tmp_path):
        result, seen = _download(tmp_path, "de")
        assert seen["languages"] == "de"
        assert Path(result["episodes"][0]["path"]).parent.name == "70523@de"
        assert [j.language for j in seen["jobs"]] == ["de"]
        assert not (tmp_path / "data" / "70523").exists()

    def test_tvsubtitles_skipped_for_a_language_it_lacks(self, tmp_path):
        _, seen = _download(tmp_path, "ja")
        assert [list(j.pending_providers) for j in seen["jobs"]] == [["addic7ed"]]


def _client():
    client = Mock()
    client.language_param.side_effect = lambda code: f"name-of-{code}"
    client.get_best_subtitle.return_value = None
    return client


def _job(language):
    return EpisodeJob(
        tmdb_id=70523,
        show_name="Babylon Berlin",
        season=1,
        episode=1,
        episode_code="S01E01",
        srt_target=Path("/dev/null"),
        pending_providers=deque(["addic7ed"]),
        language=language,
    )


@pytest.mark.unit
class TestSchedulerPassesTheLanguage:
    def test_english_call_unchanged(self):
        client = _client()
        run_jobs([_job("en")], workers={"addic7ed": client}, timeout=5)
        client.get_best_subtitle.assert_called_once_with("Babylon Berlin", 1, 1)

    def test_other_language_in_the_clients_own_terms(self):
        client = _client()
        run_jobs([_job("de")], workers={"addic7ed": client}, timeout=5)
        client.get_best_subtitle.assert_called_once_with(
            "Babylon Berlin", 1, 1, language="name-of-de"
        )


@pytest.mark.unit
def test_clients_name_languages_their_own_way():
    from app.matcher.addic7ed_client import Addic7edClient
    from app.matcher.tvsubtitles_client import TVSubtitlesClient

    assert Addic7edClient.language_param("de") == "German"
    assert TVSubtitlesClient.language_param("de") == "de"
    assert TVSubtitlesClient.supports_language("de")
    assert not TVSubtitlesClient.supports_language("ja")


async def _german_job() -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="BABYLON_BERLIN_S1D1",
            content_type=ContentType.TV,
            detected_title="Babylon Berlin",
            tmdb_id=70523,
            detected_season=1,
        )
        s.add(job)
        await s.commit()
        return job.id


def _episodes(status, n=2):
    return [
        {"code": f"S01E0{i}", "status": status, "path": None, "source": None}
        for i in range(1, n + 1)
    ]


@pytest.fixture
def flow(monkeypatch):
    """Coordinator download with the show's language, decision and downloads stubbed."""
    state = {"show_language": "de", "decided": "de", "calls": [], "german": "downloaded"}

    async def show_language(tmdb_id):
        return state["show_language"]

    async def decide_language(job_id):
        state["calls"].append(("decide", None))
        return state["decided"]

    def fake_download(show_name, season, **kwargs):
        language = current_match_language()
        state["calls"].append((language, kwargs["use_precomputed"]))
        if language == "de" and state["german"] == "raise":
            raise RuntimeError("OpenSubtitles down")
        status = state["german"] if language == "de" else "not_found"
        return {"episodes": _episodes(status), "show_name": show_name}

    monkeypatch.setattr("app.services.episode_namespace_service.decide_job_namespace", _noop)
    monkeypatch.setattr("app.services.match_language_service.show_language", show_language)
    monkeypatch.setattr(
        "app.services.match_language_service.decide_job_match_language", decide_language
    )
    monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
    monkeypatch.setattr(
        "app.services.matching_coordinator.ws_manager.broadcast_subtitle_event", _noop
    )
    return state


async def _run(state):
    job_id = await _german_job()
    coord = _bare_coordinator()
    coord._subtitle_ready[job_id] = asyncio.Event()
    await coord.download_subtitles(job_id, "Babylon Berlin", 1, tmdb_id=70523)
    async with _unit_session_factory() as s:
        job = await s.get(DiscJob, job_id)
    assert coord._subtitle_ready[job_id].is_set()
    return job


@pytest.mark.unit
class TestCoordinatorFlow:
    async def test_show_language_first_then_decision_then_english(self, flow):
        job = await _run(flow)
        assert flow["calls"] == [("de", False), ("decide", None), ("en", True)]
        # Gated on the German references, although English found none.
        assert job.subtitle_status == "completed"

    async def test_english_show_downloads_once_as_before(self, flow):
        flow["show_language"] = "en"
        flow["decided"] = "en"
        job = await _run(flow)
        assert flow["calls"] == [("decide", None), ("en", True)]
        assert job.subtitle_status == "failed"

    async def test_failed_german_download_still_downloads_english(self, flow):
        flow["german"] = "raise"
        flow["decided"] = "en"
        job = await _run(flow)
        assert flow["calls"] == [("de", False), ("decide", None), ("en", True)]
        assert job.subtitle_status == "failed"

    async def test_too_few_german_references_report_english(self, flow):
        flow["german"] = "not_found"
        flow["decided"] = "en"
        job = await _run(flow)
        assert job.subtitle_status == "failed"
        assert flow["calls"][-1] == ("en", True)
