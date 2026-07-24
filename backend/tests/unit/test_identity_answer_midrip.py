"""Walk-away B5: identity-answer endpoints work while a job is still RIPPING.

Covers the coordinator's resume contract (set_name_and_resume / re_identify
accept state==RIPPING and return a resume_action), the JobManager side
(_apply_identity_resume_action: only "start_rip" may spawn a rip task — the
double-rip hazard pin), the post-rip movie routing (_resume_movie_post_rip
reuses the rip-end movie tail instead of episode matching), and the parked
QUEUED title release for mid-rip movie answers.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.identification_coordinator import IdentificationCoordinator
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

_NAME_PROMPT = json.dumps({"kind": "name", "reason": "Disc label unreadable"})
_REIDENTIFY_PROMPT = json.dumps({"kind": "reidentify", "reason": "Ambiguous match"})


@pytest.fixture(autouse=True)
def _patch_coordinator_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.identification_coordinator.async_session", _unit_session_factory
    )


@pytest.fixture
def ws_calls(monkeypatch):
    """Spy on broadcast_job_update; silences the real WS layer."""
    calls = []

    async def spy(job_id, state, **kwargs):
        calls.append((job_id, state, kwargs))

    monkeypatch.setattr(ws_manager, "broadcast_job_update", spy)
    return calls


@pytest.fixture(autouse=True)
def _quiet_title_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


async def _seed_job(
    state=JobState.RIPPING,
    content_type=ContentType.UNKNOWN,
    identity_prompt_json=_NAME_PROMPT,
    staging="/tmp/staging/b5",
    **kwargs,
):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="UNREADABLE",
            content_type=content_type,
            state=state,
            staging_path=staging,
            identity_prompt_json=identity_prompt_json,
            **kwargs,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _get_job(job_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


async def _add_title(job_id, index, state, output=None, is_selected=True):
    async with _unit_session_factory() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=1380,
            state=state,
            output_filename=output,
            is_selected=is_selected,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t


async def _get_title(title_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


def _coord(prefetch_calls=None):
    """Bare coordinator: __new__ skips the heavyweight __init__ wiring."""
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)

    async def fake_resolve_tmdb(job):
        return None

    async def fake_prefetch(job):
        if prefetch_calls is not None:
            prefetch_calls.append((job.id, job.detected_season))

    coord._resolve_missing_tmdb_id = fake_resolve_tmdb
    coord._start_tv_subtitle_prefetch = fake_prefetch
    coord._restart_subtitle_download = AsyncMock()
    coord._cancel_subtitle_download = AsyncMock()
    return coord


@pytest.mark.unit
class TestSetNameAndResumeMidRip:
    """Mid-rip answers: metadata + prompt clear + prefetch, NO state change."""

    async def test_tv_answer_updates_metadata_without_state_change(self, ws_calls):
        job = await _seed_job(state=JobState.RIPPING)
        prefetch_calls = []
        coord = _coord(prefetch_calls)

        result = await coord.set_name_and_resume(job.id, "Eureka", "tv", season=2)

        assert result == {"job_id": job.id, "resume_action": "dispatch_matches"}
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.RIPPING  # no state change
        assert refreshed.detected_title == "Eureka"
        assert refreshed.content_type == ContentType.TV
        assert refreshed.detected_season == 2
        assert refreshed.identity_prompt_json is None
        assert prefetch_calls == [(job.id, 2)]

        # ONE coherent broadcast: new metadata + the "" prompt clear.
        assert len(ws_calls) == 1
        _jid, state, kwargs = ws_calls[0]
        assert state == JobState.RIPPING.value
        assert kwargs["detected_title"] == "Eureka"
        assert kwargs["content_type"] == "tv"
        assert kwargs["identity_prompt_json"] == ""

    async def test_movie_answer_routes_to_title_release(self, ws_calls):
        job = await _seed_job(state=JobState.RIPPING)
        prefetch_calls = []
        coord = _coord(prefetch_calls)

        result = await coord.set_name_and_resume(job.id, "Inception", "movie")

        assert result["resume_action"] == "release_movie_titles"
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.RIPPING
        assert refreshed.identity_prompt_json is None
        assert prefetch_calls == []  # movies never prefetch subtitles

    async def test_midrip_answer_leaves_review_reason_untouched(self, ws_calls):
        """Convergence-race politeness: if B4 parked the job between our state
        read and commit, the review_reason must stay readable (the user
        answers again at review)."""
        job = await _seed_job(state=JobState.RIPPING, review_reason=None)
        coord = _coord()

        await coord.set_name_and_resume(job.id, "Eureka", "tv")

        assert (await _get_job(job.id)).review_reason is None
        # mid_rip branch never assigns review_reason — nothing to flush even
        # if a racing convergence committed one between read and commit.

    async def test_invalid_state_rejected(self, ws_calls):
        job = await _seed_job(state=JobState.MATCHING)
        coord = _coord()

        with pytest.raises(ValueError, match="Cannot set name"):
            await coord.set_name_and_resume(job.id, "Eureka", "tv")


@pytest.mark.unit
class TestSetNameAndResumePostRip:
    """Answer-after-convergence (B4) composes: staged files mean never re-rip."""

    async def test_tv_answer_with_ripped_files_goes_to_matching(self, ws_calls, tmp_path):
        (tmp_path / "disc_t00.mkv").write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED,
            staging=str(tmp_path),
            identity_prompt_json=None,  # convergence already converted it
            review_reason="Disc label unreadable",
        )
        coord = _coord()

        result = await coord.set_name_and_resume(job.id, "Eureka", "tv", season=1)

        assert result["resume_action"] == "dispatch_matches"
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.MATCHING  # never RIPPING again
        assert refreshed.review_reason is None
        assert ws_calls[0][1] == JobState.MATCHING.value

    async def test_movie_answer_with_ripped_files_routes_to_movie_resolution(
        self, ws_calls, tmp_path
    ):
        (tmp_path / "disc_t00.mkv").write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED,
            staging=str(tmp_path),
            identity_prompt_json=None,
            review_reason="Disc label unreadable",
        )
        coord = _coord()

        result = await coord.set_name_and_resume(job.id, "Inception", "movie")

        assert result["resume_action"] == "resolve_movie"
        # The movie-resolution task owns the state from here.
        assert (await _get_job(job.id)).state == JobState.REVIEW_NEEDED

    async def test_pre_rip_review_resume_unchanged(self, ws_calls, tmp_path):
        """No staged files → today's behavior: REVIEW_NEEDED → RIPPING + rip task."""
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED, staging=str(tmp_path), identity_prompt_json=None
        )
        coord = _coord()

        result = await coord.set_name_and_resume(job.id, "Eureka", "tv", season=3)

        assert result["resume_action"] == "start_rip"
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.RIPPING
        assert refreshed.review_reason is None


@pytest.mark.unit
class TestReIdentifyMidRip:
    @pytest.fixture(autouse=True)
    def _no_year_lookup(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.identification_coordinator._resolve_show_year",
            lambda tmdb_id, signal: None,
        )

    async def test_tv_answer_updates_metadata_without_state_change(self, ws_calls):
        job = await _seed_job(
            state=JobState.RIPPING,
            identity_prompt_json=_REIDENTIFY_PROMPT,
            candidates_json='[{"tmdb_id": 1, "name": "Twin"}]',
        )
        prefetch_calls = []
        coord = _coord(prefetch_calls)

        result = await coord.re_identify(job.id, "Frasier", "tv", season=1, tmdb_id=195241)

        assert result["resume_action"] == "dispatch_matches"
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.RIPPING  # no state change
        assert refreshed.detected_title == "Frasier"
        assert refreshed.tmdb_id == 195241
        assert refreshed.candidates_json is None  # stale twins cleared
        assert refreshed.identity_prompt_json is None
        assert prefetch_calls == [(job.id, 1)]
        # The review-resume restart path must NOT also fire mid-rip.
        coord._restart_subtitle_download.assert_not_called()

        assert len(ws_calls) == 1
        _jid, state, kwargs = ws_calls[0]
        assert state == JobState.RIPPING.value
        assert kwargs["identity_prompt_json"] == ""

    async def test_movie_answer_routes_to_title_release(self, ws_calls):
        job = await _seed_job(state=JobState.RIPPING, identity_prompt_json=_REIDENTIFY_PROMPT)
        coord = _coord()

        result = await coord.re_identify(job.id, "Inception", "movie", tmdb_id=27205)

        assert result["resume_action"] == "release_movie_titles"
        assert (await _get_job(job.id)).state == JobState.RIPPING

    async def test_post_rip_tv_answer_still_reruns_matching(self, ws_calls, tmp_path):
        """Existing review-resume behavior pinned: has_ripped TV → MATCHING +
        rerun_matching + subtitle restart."""
        (tmp_path / "disc_t00.mkv").write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED, staging=str(tmp_path), identity_prompt_json=None
        )
        coord = _coord()

        result = await coord.re_identify(job.id, "Frasier", "tv", season=1, tmdb_id=195241)

        assert result["resume_action"] == "rerun_matching"
        assert result["has_ripped"] is True
        assert (await _get_job(job.id)).state == JobState.MATCHING
        coord._restart_subtitle_download.assert_awaited_once_with(job.id, "Frasier", 1, 195241)

    async def test_post_rip_movie_answer_routes_to_movie_resolution(self, ws_calls, tmp_path):
        """A movie answer with ripped files must NOT go to episode MATCHING."""
        (tmp_path / "disc_t00.mkv").write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED, staging=str(tmp_path), identity_prompt_json=None
        )
        coord = _coord()

        result = await coord.re_identify(job.id, "Inception", "movie", tmdb_id=27205)

        assert result["resume_action"] == "resolve_movie"
        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED  # not MATCHING
        coord._restart_subtitle_download.assert_not_called()

    async def test_invalid_state_rejected(self, ws_calls):
        job = await _seed_job(state=JobState.ORGANIZING)
        coord = _coord()

        with pytest.raises(ValueError, match="Cannot re-identify"):
            await coord.re_identify(job.id, "Frasier", "tv")


@pytest.mark.unit
class TestApplyIdentityResumeAction:
    """JobManager side of the answer: only "start_rip" spawns a rip task."""

    def _stub_matching(self, monkeypatch):
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        return dispatch

    async def test_midrip_answer_dispatches_without_second_rip(self, tmp_path, monkeypatch):
        """The double-rip hazard pin: a mid-rip set-name must dispatch parked
        QUEUED titles and must NOT spawn _run_ripping (the old wrapper did so
        unconditionally)."""
        job = await _seed_job(state=JobState.RIPPING, content_type=ContentType.TV)
        f = tmp_path / "show_t00.mkv"
        f.write_text("")
        queued = await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))
        dispatch = self._stub_matching(monkeypatch)
        run_ripping = AsyncMock()
        monkeypatch.setattr(job_manager, "_run_ripping", run_ripping)
        monkeypatch.setattr(
            job_manager._identification,
            "set_name_and_resume",
            AsyncMock(return_value={"job_id": job.id, "resume_action": "dispatch_matches"}),
        )

        await job_manager.set_name_and_resume(job.id, "Eureka", "tv", season=2)
        await asyncio.sleep(0)  # let the dispatched match task run

        run_ripping.assert_not_called()
        assert job.id not in job_manager._active_jobs  # no task registered
        dispatch.assert_awaited_once_with(job.id, queued.id, f)

    async def test_midrip_reidentify_dispatches_without_second_rip(self, tmp_path, monkeypatch):
        job = await _seed_job(state=JobState.RIPPING, content_type=ContentType.TV)
        f = tmp_path / "show_t00.mkv"
        f.write_text("")
        await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))
        dispatch = self._stub_matching(monkeypatch)
        run_ripping = AsyncMock()
        rerun = AsyncMock()
        monkeypatch.setattr(job_manager, "_run_ripping", run_ripping)
        monkeypatch.setattr(job_manager, "_rerun_matching", rerun)
        monkeypatch.setattr(
            job_manager._identification,
            "re_identify",
            AsyncMock(
                return_value={
                    "job_id": job.id,
                    "has_ripped": False,
                    "resume_action": "dispatch_matches",
                }
            ),
        )

        await job_manager.re_identify_job(job.id, "Eureka", "tv")
        await asyncio.sleep(0)

        run_ripping.assert_not_called()
        rerun.assert_not_called()
        dispatch.assert_awaited_once()

    async def test_release_movie_titles_flips_queued_to_matched(self, tmp_path, monkeypatch):
        job = await _seed_job(state=JobState.RIPPING, content_type=ContentType.MOVIE)
        q1 = await _add_title(job.id, 0, TitleState.QUEUED, output=str(tmp_path / "a.mkv"))
        q2 = await _add_title(job.id, 1, TitleState.QUEUED, output=str(tmp_path / "b.mkv"))
        still_ripping = await _add_title(job.id, 2, TitleState.RIPPING)

        await job_manager._apply_identity_resume_action(job.id, "release_movie_titles")

        assert (await _get_title(q1.id)).state == TitleState.MATCHED
        assert (await _get_title(q2.id)).state == TitleState.MATCHED
        # In-flight rip work is untouched — only identity-parked QUEUED flips.
        assert (await _get_title(still_ripping.id)).state == TitleState.RIPPING

    async def test_resolve_movie_spawns_resolution_task(self, monkeypatch):
        resume = AsyncMock()
        monkeypatch.setattr(job_manager, "_resume_movie_post_rip", resume)

        await job_manager._apply_identity_resume_action(42, "resolve_movie")
        await asyncio.sleep(0)

        resume.assert_awaited_once_with(42)
        task = job_manager._active_jobs.pop(42)
        _ = await task

    async def test_start_rip_spawns_rip_task(self, monkeypatch):
        run_ripping = AsyncMock()
        monkeypatch.setattr(job_manager, "_run_ripping", run_ripping)

        await job_manager._apply_identity_resume_action(43, "start_rip")
        await asyncio.sleep(0)

        run_ripping.assert_awaited_once_with(43)
        task = job_manager._active_jobs.pop(43)
        _ = await task

    async def test_zero_dispatch_in_matching_runs_completion_check(self, monkeypatch):
        """Post-rip resume with nothing left to dispatch (e.g. every title in
        REVIEW) must not strand the job in MATCHING."""
        job = await _seed_job(
            state=JobState.MATCHING, content_type=ContentType.TV, identity_prompt_json=None
        )
        await _add_title(job.id, 0, TitleState.REVIEW)
        completion = AsyncMock()
        monkeypatch.setattr(job_manager._finalization, "check_job_completion", completion)

        await job_manager._apply_identity_resume_action(job.id, "dispatch_matches")

        completion.assert_awaited_once()

    async def test_zero_dispatch_mid_rip_skips_completion_check(self, monkeypatch):
        """Mid-rip (still RIPPING) zero-dispatch is normal — titles dispatch as
        they rip now that the prompt is cleared; no completion check yet."""
        job = await _seed_job(state=JobState.RIPPING, content_type=ContentType.TV)
        completion = AsyncMock()
        monkeypatch.setattr(job_manager._finalization, "check_job_completion", completion)

        await job_manager._apply_identity_resume_action(job.id, "dispatch_matches")

        completion.assert_not_called()

    async def test_unknown_action_raises(self):
        with pytest.raises(ValueError, match="Unknown identity resume action"):
            await job_manager._apply_identity_resume_action(1, "bogus")


@pytest.mark.unit
class TestResumeMoviePostRip:
    """A post-rip movie answer reuses the rip-end movie tail (organize +
    complete / feature resolution), never episode matching."""

    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        import importlib

        jm_mod = importlib.import_module("app.services.job_manager")
        monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)

    async def test_single_title_organizes_and_completes(self, tmp_path, monkeypatch):
        import importlib

        jm_mod = importlib.import_module("app.services.job_manager")
        out_file = tmp_path / "Inception (2010).mkv"
        organize_calls = []

        def fake_organize(output_dir, volume_label, detected_title):
            organize_calls.append((Path(output_dir), volume_label, detected_title))
            return {"success": True, "main_file": out_file}

        monkeypatch.setattr(jm_mod.movie_organizer, "organize", fake_organize)

        ripped = tmp_path / "disc_t00.mkv"
        ripped.write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED,
            content_type=ContentType.MOVIE,
            staging=str(tmp_path),
            identity_prompt_json=None,
            detected_title="Inception",
        )
        # Parked QUEUED by the identity gate; the movie tail completes it.
        title = await _add_title(job.id, 0, TitleState.QUEUED, output=str(ripped))

        await job_manager._resume_movie_post_rip(job.id)

        refreshed = await _get_job(job.id)
        assert refreshed.state == JobState.COMPLETED
        assert refreshed.final_path == str(out_file)
        assert organize_calls == [(tmp_path, "UNREADABLE", "Inception")]
        assert (await _get_title(title.id)).state == TitleState.COMPLETED

    async def test_multi_title_goes_through_feature_resolution(self, tmp_path, monkeypatch):
        import importlib

        jm_mod = importlib.import_module("app.services.job_manager")
        organize = Mock(side_effect=AssertionError("must not organize when review is needed"))
        monkeypatch.setattr(jm_mod.movie_organizer, "organize", organize)
        resolve = AsyncMock(return_value=True)  # sent to review
        monkeypatch.setattr(job_manager, "_resolve_multi_title_movie", resolve)

        f1, f2 = tmp_path / "a_t00.mkv", tmp_path / "b_t01.mkv"
        f1.write_text("")
        f2.write_text("")
        job = await _seed_job(
            state=JobState.REVIEW_NEEDED,
            content_type=ContentType.MOVIE,
            staging=str(tmp_path),
            identity_prompt_json=None,
        )
        await _add_title(job.id, 0, TitleState.MATCHED, output=str(f1))
        await _add_title(job.id, 1, TitleState.MATCHED, output=str(f2))

        await job_manager._resume_movie_post_rip(job.id)

        resolve.assert_awaited_once()
        organize.assert_not_called()
