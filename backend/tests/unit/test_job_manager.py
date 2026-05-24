"""Unit tests for JobManager's mockable handlers.

Targets _on_title_ripped (per-title completion routing) and _rerun_matching
(re-match dispatch + DiscDB restore). The heavy _run_ripping orchestration is
intentionally left to integration tests. The matching/finalization collaborators
and websocket layer are stubbed.
"""

import asyncio
import importlib
import json
from unittest.mock import AsyncMock

import pytest

from app.api.websocket import manager as ws_manager
from app.core.extractor import RipResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

# Resolve the actual module (not the JobManager singleton, which shadows the
# submodule name in the app.services package namespace) — matches conftest.
jm_mod = importlib.import_module("app.services.job_manager")


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


async def _seed(content_type=ContentType.TV, staging="/tmp/staging", **title_kwargs):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
            state=JobState.RIPPING,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        defaults = dict(
            job_id=job.id, title_index=0, duration_seconds=1380, state=TitleState.RIPPING
        )
        defaults.update(title_kwargs)
        title = DiscTitle(**defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


async def _get_title(title_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.unit
class TestOnTitleRipped:
    async def test_movie_title_becomes_matched_without_dispatch(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.MOVIE)
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        path = tmp_path / "movie_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHED
        assert t.output_filename == str(path)
        dispatch.assert_not_called()

    async def test_tv_title_dispatches_match_when_no_discdb(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV)
        monkeypatch.setattr(
            job_manager._matching, "try_discdb_assignment", AsyncMock(return_value=False)
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)  # let the dispatched task run

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHING
        dispatch.assert_awaited_once()

    async def test_tv_title_with_discdb_checks_completion(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV)
        monkeypatch.setattr(
            job_manager._matching, "try_discdb_assignment", AsyncMock(return_value=True)
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        completion = AsyncMock()
        monkeypatch.setattr(job_manager._finalization, "check_job_completion", completion)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])

        completion.assert_awaited_once()
        dispatch.assert_not_called()

    async def test_unresolvable_file_is_noop(self, tmp_path):
        job, title = await _seed(content_type=ContentType.TV)
        path = tmp_path / "unrelated.mkv"
        path.write_text("")

        # No sorted_titles to map to → resolve returns None → early return.
        await job_manager._on_title_ripped(job.id, 1, path, [])

        t = await _get_title(title.id)
        assert t.state == TitleState.RIPPING  # unchanged


@pytest.mark.unit
class TestRerunMatching:
    async def test_discdb_preference_restores_matches(self):
        job, title = await _seed(
            content_type=ContentType.TV,
            is_selected=True,
            discdb_match_details=json.dumps({"matched_episode": "S01E04"}),
        )

        await job_manager._rerun_matching(job.id, source_preference="discdb")

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHED
        assert t.matched_episode == "S01E04"
        assert t.match_source == "discdb"
        assert t.match_confidence == 0.99

    async def test_engram_resets_and_dispatches(self, tmp_path, monkeypatch):
        f = tmp_path / "show_t00.mkv"
        f.write_text("")
        job, title = await _seed(
            content_type=ContentType.TV,
            staging=str(tmp_path),
            is_selected=True,
            output_filename=str(f),
            state=TitleState.MATCHED,
            matched_episode="S01E01",
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

        await job_manager._rerun_matching(job.id)
        await asyncio.sleep(0)

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHING
        assert t.matched_episode is None
        dispatch.assert_awaited_once()

    async def test_missing_job_is_noop(self):
        # Must not raise for an unknown job id.
        await job_manager._rerun_matching(999999)


@pytest.fixture
def rip_env(monkeypatch, tmp_path):
    """Neutralize the side-effecting steps in _run_ripping that are orthogonal
    to DB-session scoping: physical eject, the makemkv log directory, and the
    terminal-state callbacks (staging cleanup / cache clearing)."""
    import app.core.discdb_exporter as exporter_mod
    import app.core.sentinel as sentinel_mod

    monkeypatch.setattr(sentinel_mod, "eject_disc", lambda drive_id: None)
    monkeypatch.setattr(exporter_mod, "get_makemkv_log_dir", lambda job_id: tmp_path)
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
    return tmp_path


def _mock_rip(monkeypatch, result, on_call=None):
    """Replace the real (multi-hour, makemkvcon) rip with an instant no-op."""

    async def _run(*args, **kwargs):
        if on_call is not None:
            on_call()
        return result

    monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_run))


@pytest.mark.unit
class TestRunRippingSessionScoping:
    async def test_setup_session_closed_before_rip(self, rip_env, monkeypatch):
        """The setup session must be released before the long rip is awaited —
        no JobManager DB session may be held while rip_titles is in flight."""
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        open_sessions = {"count": 0}

        class _TrackingSession:
            def __init__(self):
                self._real = _unit_session_factory()

            async def __aenter__(self):
                open_sessions["count"] += 1
                return await self._real.__aenter__()

            async def __aexit__(self, *exc):
                result = await self._real.__aexit__(*exc)
                open_sessions["count"] -= 1
                return result

        monkeypatch.setattr(jm_mod, "async_session", _TrackingSession)

        captured = {}
        _mock_rip(
            monkeypatch,
            RipResult(success=True, output_files=[]),
            on_call=lambda: captured.__setitem__("open_at_rip", open_sessions["count"]),
        )

        await job_manager._run_ripping(job.id)

        assert captured.get("open_at_rip") == 0, (
            f"Expected 0 open JobManager sessions when rip_titles was awaited, "
            f"got {captured.get('open_at_rip')!r} (None means rip_titles was never reached)"
        )

    async def test_tv_path_transitions_to_matching(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(
            monkeypatch,
            RipResult(success=True, output_files=[rip_env / "show_t00.mkv"]),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.MATCHING

    async def test_movie_single_title_completes(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.MOVIE, staging=str(rip_env), is_selected=True
        )
        out_file = rip_env / "movie.mkv"
        monkeypatch.setattr(
            jm_mod.movie_organizer,
            "organize",
            lambda *a, **k: {"success": True, "main_file": out_file},
        )
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[out_file]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.COMPLETED
        assert refreshed.final_path == str(out_file)

    async def test_rip_failure_fails_job(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        _mock_rip(
            monkeypatch,
            RipResult(success=False, output_files=[], error_message="disc read error"),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.FAILED
        assert refreshed.error_message == "disc read error"

    async def test_fail_job_helper_missing_job_is_noop(self):
        # Must not raise for an unknown job id.
        await job_manager._fail_job(999999, "boom")

    async def test_stalled_titles_marked_failed_and_job_continues(self, rip_env, monkeypatch):
        # A rip that reports stalled titles must NOT fail the job: the stalled
        # title is marked FAILED and the job proceeds to MATCHING.
        job, title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True, title_index=0
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(
            monkeypatch,
            RipResult(success=False, output_files=[], stalled_titles=[1]),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed_job = await session.get(DiscJob, job.id)
            refreshed_title = await session.get(DiscTitle, title.id)
        assert refreshed_title.state == TitleState.FAILED
        assert refreshed_job.state == JobState.MATCHING

    async def test_cancelled_rip_fails_job_and_reraises(self, rip_env, monkeypatch):
        # Cancelling the rip must fail the job AND re-raise so the task is
        # actually marked cancelled (asyncio convention).
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )

        async def _cancel(*args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_cancel))

        with pytest.raises(asyncio.CancelledError):
            await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.FAILED
        assert refreshed.error_message == "Cancelled by user"
