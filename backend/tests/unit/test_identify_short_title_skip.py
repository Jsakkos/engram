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
    cfg = SimpleNamespace(
        extras_policy="skip",
        backup_before_rip=False,
        backup_path=None,
        analyst_movie_min_duration=80 * 60,
    )

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

    async def test_tvdb_numbered_show_uses_disc_evidence_only(self, short_env):
        """A show on TheTVDB numbering: TMDB runtimes describe another episode
        split, so they are not fetched and the disc's own evidence decides."""
        from app.models.show_ordering import ShowOrderingPreference

        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1234, ordering="tvdb"))
            await s.commit()
        job_id, _, _ = await _identify_tv()

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


# A 2h feature, a 20-minute making-of and two short featurettes.
_MOVIE_DISC = [_ti(0, 121), _ti(1, 20), _ti(2, 5), _ti(3, 3)]


async def _identify_movie(disc=None, *, discdb=None, **kw):
    job_id = await _seed_identifying_job("SOME_MOVIE")
    analysis = _make_analysis(ContentType.MOVIE, "Some Movie", season=None, tmdb_id=99, **kw)
    if kw.get("needs_review") and kw.get("review_reason") is None:
        analysis.is_ambiguous_movie = True
    coord = _bare_coord(analysis, disc or _MOVIE_DISC, "SOME_MOVIE")
    if discdb is not None:
        coord._get_discdb_mappings = lambda _job_id: discdb
    await coord.identify_disc(job_id)
    return job_id, coord


@pytest.mark.unit
class TestMovieExtrasSkip:
    """#773: "skip" applies to movie discs, and never to a feature candidate."""

    async def test_skip_policy_skips_titles_shorter_than_any_feature(self, short_env):
        job_id, coord = await _identify_movie()

        titles = await _job_titles(job_id)
        assert [t.state for t in titles] == [TitleState.PENDING] + [TitleState.SKIPPED] * 3
        assert [t.is_selected for t in titles] == [True, False, False, False]
        details = json.loads(titles[1].match_details)
        assert details["auto_skipped"] is True and details["skipped"] is True
        assert "feature" in details["reason"]
        coord._run_ripping.assert_awaited_once_with(job_id)
        short_env.runtimes.assert_not_called()

    @pytest.mark.parametrize("policy", ["keep", "ask"])
    async def test_other_policies_rip_everything(self, short_env, policy):
        short_env.cfg.extras_policy = policy
        job_id, _ = await _identify_movie()

        titles = await _job_titles(job_id)
        assert all(t.state == TitleState.PENDING and t.is_selected for t in titles)

    async def test_every_feature_length_cut_is_kept(self, short_env):
        job_id, _ = await _identify_movie([_ti(0, 121), _ti(1, 146), _ti(2, 95), _ti(3, 4)])

        titles = await _job_titles(job_id)
        assert [t.state for t in titles] == [TitleState.PENDING] * 3 + [TitleState.SKIPPED]

    async def test_short_film_with_no_feature_length_title_is_untouched(self, short_env):
        """With nothing over the feature floor, which title is the film is a
        post-rip question; nothing is skipped up front."""
        job_id, _ = await _identify_movie([_ti(0, 62), _ti(1, 8)])

        titles = await _job_titles(job_id)
        assert not any(t.state == TitleState.SKIPPED for t in titles)

    async def test_discdb_main_movie_decides(self, short_env):
        """TheDiscDB's MainMovie tag is the strongest signal: even a long
        documentary that is not tagged is skipped."""
        discdb = [SimpleNamespace(index=1, title_type="MainMovie")]
        job_id, _ = await _identify_movie([_ti(0, 96), _ti(1, 121), _ti(2, 5)], discdb=discdb)

        titles = await _job_titles(job_id)
        assert [t.state for t in titles] == [
            TitleState.SKIPPED,
            TitleState.PENDING,
            TitleState.SKIPPED,
        ]
        assert "TheDiscDB" in json.loads(titles[0].match_details)["reason"]

    async def test_ambiguous_movie_still_skips_short_titles(self, short_env):
        job_id, coord = await _identify_movie(
            [_ti(0, 121), _ti(1, 146), _ti(2, 5)], needs_review=True
        )

        titles = await _job_titles(job_id)
        assert [t.state for t in titles] == [TitleState.PENDING] * 2 + [TitleState.SKIPPED]
        coord._run_ripping.assert_awaited_once_with(job_id)

    async def test_unsettled_content_type_is_untouched(self, short_env):
        """A disc parked before the rip because movie-vs-TV is unclear may turn
        out to be TV, where those short titles are episodes."""
        job_id, _ = await _identify_movie(
            needs_review=True, review_reason="TMDB says TV, heuristic says movie"
        )

        titles = await _job_titles(job_id)
        assert not any(t.state == TitleState.SKIPPED for t in titles)
        assert (await _reload_job(job_id)).state == JobState.REVIEW_NEEDED
