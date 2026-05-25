"""Unit tests for FinalizationCoordinator's conflict resolution + completion routing.

The escalation ladder and conflict helpers are covered by
test_auto_conflict_escalation.py; this file targets finalize_disc_job's
ranking/reassignment loop and organize routing, plus check_job_completion's
decision branches. The organizer and websocket layers are stubbed.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    # finalize_disc_job opens its own session; point it at the test DB.
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


@pytest.fixture
def mock_organize(monkeypatch):
    """Stub tv_organizer.organize to a success result by default."""
    import app.core.organizer as org

    m = Mock(return_value={"success": True, "final_path": "/lib/tv/Show/ep.mkv"})
    monkeypatch.setattr(org.tv_organizer, "organize", m)
    return m


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed_job(
    titles,
    staging,
    *,
    content_type=ContentType.TV,
    state=JobState.MATCHING,
    match_details_by_idx=None,
) -> int:
    """Seed a job with the given (title_index, episode, output_filename, title_state) titles."""
    md = match_details_by_idx or {}
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
            state=state,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx, ep, outfn, tstate in titles:
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    duration_seconds=1380,
                    matched_episode=ep,
                    match_confidence=0.8,
                    state=tstate,
                    output_filename=outfn,
                    match_details=md.get(idx),
                )
            )
        await session.commit()
        return job.id


async def _load(job_id):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        return job, {t.title_index: t for t in titles}


@pytest.mark.unit
class TestFinalizeDiscJob:
    async def test_no_conflict_organizes_all_and_completes(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E01", str(f0), TitleState.MATCHED),
                (1, "S01E02", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert job.state == JobState.COMPLETED
        assert all(t.state == TitleState.COMPLETED for t in titles.values())
        assert mock_organize.call_count == 2

    async def test_conflict_reassigns_loser_via_runner_up(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E05", str(f0), TitleState.MATCHED),
                (1, "S01E05", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
            match_details_by_idx={
                0: json.dumps({"score": 0.9, "vote_count": 10, "file_cov": 0.9, "runner_ups": []}),
                1: json.dumps(
                    {
                        "score": 0.5,
                        "vote_count": 2,
                        "file_cov": 0.5,
                        "runner_ups": [{"episode": "S01E06", "score": 0.8, "confidence": 0.85}],
                    }
                ),
            },
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        # Lower-voted title is bumped to its runner-up episode and both organize.
        assert titles[0].matched_episode == "S01E05"
        assert titles[1].matched_episode == "S01E06"
        assert titles[1].match_confidence == 0.85
        assert all(t.state == TitleState.COMPLETED for t in titles.values())
        assert job.state == JobState.COMPLETED

    async def test_conflict_without_runner_up_defers_to_review(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E05", str(f0), TitleState.MATCHED),
                (1, "S01E05", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
            match_details_by_idx={
                0: json.dumps({"vote_count": 10, "score": 0.9, "runner_ups": []}),
                1: json.dumps({"vote_count": 2, "score": 0.5, "runner_ups": []}),
            },
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[1].state == TitleState.REVIEW
        # Winner is held (not organized) while the disc has an unresolved title.
        assert titles[0].state == TitleState.MATCHED
        assert job.state == JobState.REVIEW_NEEDED
        mock_organize.assert_not_called()

    async def test_missing_source_file_marks_review(self, tmp_path, mock_organize):
        job_id = await _seed_job(
            [(0, "S01E01", None, TitleState.MATCHED)],
            staging=str(tmp_path),  # empty dir, no glob match
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[0].state == TitleState.REVIEW
        assert job.state == JobState.REVIEW_NEEDED
        mock_organize.assert_not_called()

    async def test_organize_failure_marks_review(self, tmp_path, monkeypatch):
        import app.core.organizer as org

        monkeypatch.setattr(
            org.tv_organizer,
            "organize",
            Mock(return_value={"success": False, "error": "disk full"}),
        )
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed_job(
            [(0, "S01E01", str(f0), TitleState.MATCHED)], staging=str(tmp_path)
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[0].state == TitleState.REVIEW
        assert job.state == JobState.REVIEW_NEEDED


@pytest.mark.unit
class TestCheckJobCompletion:
    async def test_active_title_returns_without_finalizing(self, tmp_path):
        job_id = await _seed_job([(0, "S01E01", None, TitleState.RIPPING)], staging=str(tmp_path))
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.MATCHING  # unchanged
        coord.finalize_disc_job.assert_not_called()

    async def test_review_title_transitions_to_review(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, "S01E01", None, TitleState.MATCHED),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        coord.finalize_disc_job.assert_not_called()

    async def test_all_matched_invokes_finalize(self, tmp_path):
        job_id = await _seed_job([(0, "S01E01", None, TitleState.MATCHED)], staging=str(tmp_path))
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        coord.finalize_disc_job.assert_awaited_once_with(job_id)

    async def test_all_failed_transitions_to_failed(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, None, None, TitleState.FAILED),
                (1, None, None, TitleState.FAILED),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.FAILED
        coord.finalize_disc_job.assert_not_called()

    async def test_conflict_escalation_short_circuits_finalize(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, "S01E05", None, TitleState.MATCHED),
                (1, "S01E05", None, TitleState.MATCHED),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async def fake_rematch(jid, ep, num_points=None, min_vote_count=None):
            return {"dispatched": [1], "skipped": []}

        coord._rematch_conflict = fake_rematch

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        # Escalation dispatched a re-match, so finalization is deferred.
        coord.finalize_disc_job.assert_not_called()
        assert coord._conflict_passes.get(job_id) == 25
