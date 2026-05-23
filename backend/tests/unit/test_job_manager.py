"""Unit tests for JobManager's mockable handlers.

Targets _on_title_ripped (per-title completion routing) and _rerun_matching
(re-match dispatch + DiscDB restore). The heavy _run_ripping orchestration is
intentionally left to integration tests. The matching/finalization collaborators
and websocket layer are stubbed.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory


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
