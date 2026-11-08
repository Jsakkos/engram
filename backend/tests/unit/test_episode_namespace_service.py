"""Deciding and switching a job's episode namespace (spec 2026-10-08)."""

import json

import pytest
from sqlmodel import select

from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState
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
    # In review by default: a numbering switch is only allowed there.
    kw.setdefault("state", JobState.REVIEW_NEEDED)
    kw.setdefault("detected_season", 1)
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
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


SEASON_2 = [{"episode_number": i, "name": f"Second {i}", "air_date": ""} for i in range(1, 14)]


@pytest.fixture
def per_season(monkeypatch):
    """Rosters keyed by season; a season absent from a side reads as a failed lookup."""
    state = {"tmdb": {1: TMDB, 2: SEASON_2}, "tvdb": {1: TVDB, 2: SEASON_2}, "tvdb_calls": []}

    async def fake_config():
        return AppConfig(tmdb_api_key="tok", tvdb_api_key="k")

    def fake_tvdb_roster(tvdb_id, season, api_key):
        state["tvdb_calls"].append(season)
        return state["tvdb"].get(season)

    monkeypatch.setattr("app.services.config_service.get_config", fake_config)
    monkeypatch.setattr(svc.tmdb_client, "fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(
        svc.tmdb_client, "fetch_season_episodes", lambda s, n, k: state["tmdb"].get(n)
    )
    monkeypatch.setattr(svc.tvdb_client, "fetch_season_roster", fake_tvdb_roster)
    return state


async def _seasonless_job(**kw) -> int:
    """A TV job whose season is unknown (flat import, "match across all seasons")."""
    return await _job(detected_season=None, **kw)


async def _prefer_tvdb() -> None:
    async with _unit_session_factory() as s:
        s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
        await s.commit()


@pytest.mark.unit
class TestDecideSeasonless:
    """#876: the all-seasons download decides over the seasons it downloads."""

    async def test_disagreeing_catalogues_record_the_suggestion(self, per_season):
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        job = await _get(job_id)
        # Same shape a single-season job stores, naming the season that diverges.
        assert json.loads(job.tvdb_divergence_json) == {"season": 1, "tmdb": 24, "tvdb": 26}
        assert job.episode_namespace in (None, "tmdb")

    async def test_divergence_in_a_later_season_is_found(self, per_season):
        per_season["tvdb"] = {1: TMDB, 2: SEASON_2[:-1]}
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        div = json.loads((await _get(job_id)).tvdb_divergence_json)
        assert div == {"season": 2, "tmdb": 13, "tvdb": 12}

    async def test_agreeing_catalogues_record_nothing(self, per_season):
        per_season["tvdb"] = {1: TMDB, 2: SEASON_2}
        job_id = await _seasonless_job(tvdb_divergence_json='{"season": 1}')
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        job = await _get(job_id)
        assert job.tvdb_divergence_json is None
        assert job.episode_namespace in (None, "tmdb")

    async def test_tvdb_preference_crosswalks_every_season(self, per_season):
        await _prefer_tvdb()
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tvdb"
        job = await _get(job_id)
        assert job.episode_namespace == "tvdb"
        crosswalk = json.loads(job.episode_crosswalk_json)
        assert crosswalk["S01E04"] == "S01E02"
        assert crosswalk["S02E05"] == "S02E05"
        assert job.episode_namespace_note is None

    async def test_partial_lookup_never_flips_to_tvdb(self, per_season):
        """One season missing on TheTVDB: the crosswalk would not cover it, so the
        job stays on TMDB (with the note) rather than going half-TVDB."""
        per_season["tvdb"] = {1: TVDB}
        await _prefer_tvdb()
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        job = await _get(job_id)
        assert job.episode_namespace == "tmdb"
        assert job.episode_crosswalk_json is None
        assert "TheTVDB unavailable" in job.episode_namespace_note

    async def test_partial_lookup_records_no_suggestion(self, per_season):
        per_season["tvdb"] = {1: TVDB}
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        assert (await _get(job_id)).tvdb_divergence_json is None

    async def test_tvdb_outage_stays_tmdb_after_one_round_trip(self, per_season):
        per_season["tvdb"] = {}
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id, [1, 2, 3]) == "tmdb"
        job = await _get(job_id)
        assert job.tvdb_divergence_json is None
        assert per_season["tvdb_calls"] == [1], "an outage must not cost a call per season"

    async def test_no_seasons_is_a_no_op(self, per_season):
        job_id = await _seasonless_job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert await svc.decide_job_namespace(job_id, []) == "tmdb"
        assert per_season["tvdb_calls"] == []

    async def test_known_season_ignores_the_seasons_argument(self, per_season):
        job_id = await _job()
        await svc.decide_job_namespace(job_id, [1, 2])
        assert per_season["tvdb_calls"] == [1]

    async def test_season_assigned_during_the_fetch_discards_the_result(
        self, per_season, monkeypatch
    ):
        job_id = await _seasonless_job()

        async def assign_then_fetch(tmdb_id, seasons, config, tvdb_id):
            # The user picks a season while the rosters are in flight.
            async with _unit_session_factory() as s:
                job = await s.get(DiscJob, job_id)
                job.detected_season = 1
                await s.commit()
            return 76290, {1: (TMDB, TVDB), 2: (SEASON_2, SEASON_2)}

        monkeypatch.setattr(svc, "_season_rosters", assign_then_fetch)
        assert await svc.decide_job_namespace(job_id, [1, 2]) == "tmdb"
        assert (await _get(job_id)).tvdb_divergence_json is None


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

    async def test_revert_to_tmdb_dismisses_the_suggestion(self, stubs):
        """The re-download after a revert re-detects the divergence; without the
        dismissal the suggestion would reappear straight away."""
        job_id = await _job()
        await svc.switch_job_namespace(job_id, "tvdb")
        await svc.switch_job_namespace(job_id, "tmdb")
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.tvdb_suggestion_dismissed is True

    async def test_decide_skips_tvdb_when_dismissed_and_not_preferred(self, stubs, monkeypatch):
        async with _unit_session_factory() as s:
            s.add(
                ShowOrderingPreference(tmdb_id=1618, ordering="dvd", tvdb_suggestion_dismissed=True)
            )
            await s.commit()

        fetched = []

        async def recording_rosters(*a, **k):
            # decide_job_namespace swallows exceptions, so record rather than raise.
            fetched.append(a)
            return None, None, None

        monkeypatch.setattr(svc, "_rosters", recording_rosters)
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert fetched == [], "no TheTVDB round-trip when no suggestion can be shown"
        assert (await _get(job_id)).episode_namespace == "tmdb"

    async def test_decide_still_fetches_when_dismissed_but_tvdb_preferred(self, stubs):
        async with _unit_session_factory() as s:
            s.add(
                ShowOrderingPreference(
                    tmdb_id=1618, ordering="tvdb", tvdb_id=76290, tvdb_suggestion_dismissed=True
                )
            )
            await s.commit()
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tvdb"

    async def test_switch_clears_discdb_match_details_of_the_job_only(self, stubs):
        """Those details hold codes in the old numbering; a re-match rebuilds them."""
        job_id = await _job()
        other_id = await _job()
        details = json.dumps({"source": "discdb", "matched_episode": "S01E02"})
        async with _unit_session_factory() as s:
            s.add(
                DiscTitle(
                    job_id=job_id,
                    title_index=0,
                    duration_seconds=1300,
                    discdb_match_details=details,
                )
            )
            s.add(
                DiscTitle(
                    job_id=job_id,
                    title_index=1,
                    duration_seconds=1300,
                    discdb_match_details=details,
                )
            )
            s.add(
                DiscTitle(
                    job_id=other_id,
                    title_index=0,
                    duration_seconds=1300,
                    discdb_match_details=details,
                )
            )
            await s.commit()

        await svc.switch_job_namespace(job_id, "tvdb")

        async with _unit_session_factory() as s:
            mine = (
                (await s.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            other = (
                (await s.execute(select(DiscTitle).where(DiscTitle.job_id == other_id)))
                .scalars()
                .all()
            )
        assert [t.discdb_match_details for t in mine] == [None, None]
        assert [t.discdb_match_details for t in other] == [details]

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


@pytest.mark.unit
async def test_switch_refused_once_the_job_left_review(stubs):
    job_id = await _job(state=JobState.ORGANIZING)
    with pytest.raises(svc.NotInReviewError):
        await svc.switch_job_namespace(job_id, "tvdb")
    assert (await _get(job_id)).episode_namespace == "tmdb"
