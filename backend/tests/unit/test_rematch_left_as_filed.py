"""A re-match skips tracks already filed into the library. The completion check then
holds the job in review and names their folders, unless nothing is left to act on."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

FILED = "/library/TV/Show/Season 01/Show - S01E05.mkv"
# The note renders folders with the OS separator, as real paths are.
FILED_DIR = str(Path(FILED).parent)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(always_review=False)),
    )


async def _seed(title_states: list[TitleState]) -> int:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D2",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Show",
            detected_season=2,
            staging_path="/tmp/staging/job",
            subtitle_status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for i, state in enumerate(title_states):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=i,
                    duration_seconds=2640,
                    state=state,
                    matched_episode=f"S02E{i + 1:02d}" if state == TitleState.MATCHED else None,
                    match_confidence=1.0 if state == TitleState.MATCHED else 0.0,
                    organized_to=FILED if state == TitleState.COMPLETED else None,
                )
            )
        await session.commit()
        return job.id


def _coord() -> FinalizationCoordinator:
    broadcaster = AsyncMock()
    coord = FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord.finalize_disc_job = AsyncMock()
    return coord


async def _run_completion(coord, job_id: int) -> DiscJob:
    async with _unit_session_factory() as session:
        await coord.check_job_completion(session, job_id)
        job = await session.get(DiscJob, job_id)
        await session.refresh(job)
        return job


@pytest.mark.unit
class TestLeftAsFiled:
    async def test_held_in_review_when_tracks_remain_to_organize(self):
        coord = _coord()
        job_id = await _seed([TitleState.COMPLETED, TitleState.MATCHED])
        coord.note_left_as_filed(job_id, [FILED])

        job = await _run_completion(coord, job_id)

        assert job.state == JobState.REVIEW_NEEDED
        assert FILED_DIR in job.review_reason
        coord.finalize_disc_job.assert_not_awaited()

    async def test_review_reason_keeps_filed_note_alongside_manual_assignment(self):
        coord = _coord()
        job_id = await _seed([TitleState.COMPLETED, TitleState.REVIEW])
        coord.note_left_as_filed(job_id, [FILED])

        job = await _run_completion(coord, job_id)

        assert job.state == JobState.REVIEW_NEEDED
        assert "need manual episode assignment" in job.review_reason
        assert FILED_DIR in job.review_reason

    async def test_completes_when_nothing_is_left_to_organize(self):
        """Same gate as always_review: nothing left to organize, so no review."""
        coord = _coord()
        job_id = await _seed([TitleState.COMPLETED, TitleState.COMPLETED])
        coord.note_left_as_filed(job_id, [FILED, FILED])

        job = await _run_completion(coord, job_id)

        assert job.state == JobState.COMPLETED

    async def test_reset_forgets_the_note(self):
        coord = _coord()
        job_id = await _seed([TitleState.COMPLETED, TitleState.MATCHED])
        coord.note_left_as_filed(job_id, [FILED])
        coord.reset_conflict_passes(job_id)

        await _run_completion(coord, job_id)

        coord.finalize_disc_job.assert_awaited_once()
