"""Episode-namespace API (spec 2026-10-08)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_session
from app.main import app
from app.models.disc_job import ContentType, DiscJob, JobState
from app.models.show_ordering import ShowOrderingPreference
from tests.unit.conftest import _unit_session_factory

_DIVERGENCE = {"season": 1, "tmdb": 24, "tvdb": 26}


@pytest.fixture
async def client():
    async def override():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _job(**kw) -> int:
    fields = dict(
        drive_id="E:",
        volume_label="JL",
        content_type=ContentType.TV,
        tmdb_id=1618,
        detected_title="Justice League",
        detected_season=1,
        state=JobState.REVIEW_NEEDED,
    )
    fields.update(kw)
    async with _unit_session_factory() as s:
        job = DiscJob(**fields)
        s.add(job)
        await s.commit()
        return job.id


async def _show_pref(**kw) -> None:
    async with _unit_session_factory() as s:
        s.add(ShowOrderingPreference(tmdb_id=1618, **kw))
        await s.commit()


@pytest.fixture
def roster_stubs(monkeypatch):
    """Keep the roster offline: a fixed season list, no ordering groups, no cache scan."""
    episodes = [{"episode_number": 1, "name": "Secret Origins", "runtime": 72}]
    state = {"episodes": episodes, "namespaces": []}

    def fake_season_episodes(show, season, key):
        from app.core.episode_namespace import current_namespace

        state["namespaces"].append(current_namespace())
        return state["episodes"]

    monkeypatch.setattr("app.core.episode_namespace.season_episodes", fake_season_episodes)
    monkeypatch.setattr(
        "app.core.episode_ordering.build_ordering_options",
        lambda *a, **k: {
            "available": False,
            "diverges": False,
            "current": a[-1] if a else "aired",
            "options": [
                {
                    "ordering": "aired",
                    "label": "Aired Order",
                    "tmdb_type": 1,
                    "diverges": False,
                    "projection": {},
                }
            ],
        },
    )
    monkeypatch.setattr("app.api.routes.reference_coverage", lambda *a, **k: {})
    return state


@pytest.mark.unit
class TestSwitchEndpoint:
    async def test_switch_to_tvdb_redownloads_and_rematches(self, client, monkeypatch):
        job_id = await _job(tvdb_divergence_json=json.dumps(_DIVERGENCE))
        calls = []

        async def fake_switch(jid, namespace):
            calls.append(("switch", namespace))

        async def fake_restart(jid, show, season, tmdb_id=None):
            calls.append(("restart", show, season, tmdb_id))

        async def fake_rerun(jid, source_preference=None):
            calls.append(("rerun",))

        monkeypatch.setattr(
            "app.services.episode_namespace_service.switch_job_namespace", fake_switch
        )
        from app.services.job_manager import job_manager

        monkeypatch.setattr(job_manager._matching, "restart_subtitle_download", fake_restart)
        monkeypatch.setattr(job_manager._matching, "forget_episode_runtimes", lambda jid: None)
        monkeypatch.setattr(job_manager, "rerun_matching", fake_rerun)

        resp = await client.post(
            f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "tvdb"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"job_id": job_id, "episode_namespace": "tvdb"}
        assert calls == [
            ("switch", "tvdb"),
            ("restart", "Justice League", 1, 1618),
            ("rerun",),
        ]

    async def test_tvdb_unavailable_is_503_and_nothing_runs(self, client, monkeypatch):
        from app.services import episode_namespace_service as svc
        from app.services.job_manager import job_manager

        job_id = await _job()

        async def unavailable(jid, namespace):
            raise svc.TvdbUnavailableError("down")

        async def must_not_run(*a, **k):
            raise AssertionError("re-match must not run when the switch failed")

        monkeypatch.setattr(svc, "switch_job_namespace", unavailable)
        monkeypatch.setattr(job_manager, "rerun_matching", must_not_run)
        resp = await client.post(
            f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "tvdb"}
        )
        assert resp.status_code == 503
        assert "TheTVDB" in resp.json()["detail"]

    async def test_unknown_namespace_is_422(self, client):
        job_id = await _job()
        resp = await client.post(
            f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "imdb"}
        )
        assert resp.status_code == 422

    async def test_unknown_job_is_404(self, client):
        resp = await client.post("/api/jobs/99999/episode-namespace", json={"namespace": "tvdb"})
        assert resp.status_code == 404

    @pytest.mark.parametrize("state", [JobState.IDENTIFYING, JobState.RIPPING, JobState.BACKING_UP])
    async def test_refused_while_disc_is_in_flight(self, client, monkeypatch, state):
        from app.services import episode_namespace_service as svc

        job_id = await _job(state=state)

        async def must_not_switch(jid, namespace):
            raise AssertionError("switch must not run mid-disc")

        monkeypatch.setattr(svc, "switch_job_namespace", must_not_switch)
        resp = await client.post(
            f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "tvdb"}
        )
        assert resp.status_code == 409
        assert "rip" in resp.json()["detail"].lower()


@pytest.mark.unit
async def test_dismiss_endpoint(client):
    resp = await client.post("/api/shows/1618/tvdb-suggestion/dismiss")
    assert resp.status_code == 200
    get = await client.get("/api/shows/1618/ordering")
    assert get.json()["tvdb_suggestion_dismissed"] is True
    assert get.json()["source"] == "default"


@pytest.mark.unit
class TestRosterNamespace:
    async def test_tmdb_job_reports_source_and_suggestion(self, client, roster_stubs):
        job_id = await _job(tvdb_divergence_json=json.dumps(_DIVERGENCE))
        resp = await client.get(f"/api/jobs/{job_id}/season-roster")
        body = resp.json()
        assert body["available"] is True
        assert body["episode_source"] == "tmdb"
        assert body["tvdb_suggestion"] == _DIVERGENCE
        # Divergence known: TheTVDB is offered beside the TMDB orderings.
        assert [o["ordering"] for o in body["ordering_options"]] == ["aired", "tvdb"]
        assert body["current_ordering"] == "aired"
        assert roster_stubs["namespaces"] == ["tmdb"]

    async def test_dismissed_suggestion_is_not_offered(self, client, roster_stubs):
        await _show_pref(tvdb_suggestion_dismissed=True)
        job_id = await _job(tvdb_divergence_json=json.dumps(_DIVERGENCE))
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["tvdb_suggestion"] is None

    async def test_plain_tmdb_job_has_no_tvdb_option(self, client, roster_stubs):
        job_id = await _job()
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["episode_source"] == "tmdb"
        assert body["tvdb_suggestion"] is None
        assert [o["ordering"] for o in body["ordering_options"]] == ["aired"]

    async def test_tvdb_job_roster(self, client, roster_stubs):
        await _show_pref(ordering="tvdb")
        job_id = await _job(episode_namespace="tvdb")
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["available"] is True
        assert body["episode_source"] == "tvdb"
        assert body["current_ordering"] == "tvdb"
        assert body["tvdb_suggestion"] is None
        assert [o["ordering"] for o in body["ordering_options"]].count("tvdb") == 1
        assert body["ordering_available"] is True
        # The roster was fetched under the job's namespace.
        assert roster_stubs["namespaces"] == ["tvdb"]

    async def test_tvdb_pref_with_tmdb_fallback_still_offers_tvdb(self, client, roster_stubs):
        await _show_pref(ordering="tvdb")
        job_id = await _job(
            episode_namespace="tmdb",
            episode_namespace_note="TheTVDB unavailable; matched with TMDB numbering",
        )
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["episode_source"] == "tmdb"
        assert body["current_ordering"] == "tvdb"
        assert [o["ordering"] for o in body["ordering_options"]].count("tvdb") == 1
        assert body["namespace_note"] == "TheTVDB unavailable; matched with TMDB numbering"

    async def test_empty_tvdb_roster_names_thetvdb(self, client, roster_stubs):
        roster_stubs["episodes"] = []
        job_id = await _job(episode_namespace="tvdb")
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["available"] is False
        assert body["reason"] == "Could not load season episodes from TheTVDB"

    async def test_empty_tmdb_roster_keeps_tmdb_reason(self, client, roster_stubs):
        roster_stubs["episodes"] = []
        job_id = await _job()
        body = (await client.get(f"/api/jobs/{job_id}/season-roster")).json()
        assert body["available"] is False
        assert body["reason"] == "Could not load season episodes from TMDB"

    async def test_coverage_scan_runs_in_the_job_namespace(self, client, roster_stubs, monkeypatch):
        seen = []

        def fake_coverage(*a, **k):
            from app.core.episode_namespace import current_namespace

            seen.append(current_namespace())
            return {}

        monkeypatch.setattr("app.api.routes.reference_coverage", fake_coverage)
        job_id = await _job(episode_namespace="tvdb")
        await client.get(f"/api/jobs/{job_id}/season-roster")
        assert seen == ["tvdb"]


@pytest.mark.unit
async def test_job_detail_carries_namespace_fields(client):
    job_id = await _job(
        episode_namespace="tvdb",
        episode_namespace_note="note",
        tvdb_divergence_json=json.dumps(_DIVERGENCE),
    )
    body = (await client.get(f"/api/jobs/{job_id}/detail")).json()
    assert body["episode_namespace"] == "tvdb"
    assert body["episode_namespace_note"] == "note"
    assert json.loads(body["tvdb_divergence_json"]) == _DIVERGENCE


@pytest.mark.unit
class TestManualSubtitleNamespace:
    """An uploaded subtitle for a TVDB job is classified and filed in TVDB numbering."""

    async def test_preview_and_commit_run_in_the_job_namespace(self, client, monkeypatch):
        seen = []

        def fake_classify(*a, **k):
            from app.core.episode_namespace import current_namespace

            seen.append(("preview", current_namespace()))
            return []

        def fake_commit(*a, **k):
            from app.core.episode_namespace import current_namespace

            seen.append(("commit", current_namespace()))
            return []

        monkeypatch.setattr("app.api.routes.classify_files", fake_classify)
        monkeypatch.setattr("app.api.routes.commit_files", fake_commit)
        job_id = await _job(episode_namespace="tvdb")

        r1 = await client.post(f"/api/jobs/{job_id}/subtitles/preview", json={"files": []})
        r2 = await client.post(f"/api/jobs/{job_id}/subtitles/commit", json={"files": []})
        assert r1.status_code == 200 and r2.status_code == 200
        assert seen == [("preview", "tvdb"), ("commit", "tvdb")]
