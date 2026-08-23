"""A TV disc whose every track was classified as bonus content is held for review.

Nothing matching an episode means the match failed for the whole disc — far more
often than it means the disc really is a bonus disc. Left alone, such a disc files
every track into Extras/ and reports COMPLETED, so the mistake is invisible until
someone browses the library weeks later.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)


async def _seed(title_states: list[TitleState], *, as_extras: bool = False) -> int:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SEGMENT_SHOW_S1_D6",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Segment Show",
            detected_season=1,
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
                    duration_seconds=1340,
                    state=state,
                    matched_episode=(
                        ("extra" if as_extras else f"S01E{i + 1:02d}")
                        if state == TitleState.MATCHED
                        else None
                    ),
                    match_confidence=1.0 if state == TitleState.MATCHED else 0.0,
                    is_extra=as_extras and state == TitleState.MATCHED,
                )
            )
        await session.commit()
        return job.id


def _coord() -> tuple[FinalizationCoordinator, Mock]:
    broadcaster = AsyncMock()
    coord = FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord.finalize_disc_job = AsyncMock()
    return coord, broadcaster


async def _run_completion(coord, job_id: int) -> JobState:
    async with _unit_session_factory() as session:
        await coord.check_job_completion(session, job_id)
        job = await session.get(DiscJob, job_id)
        await session.refresh(job)
        return job.state


@pytest.mark.unit
class TestAllExtrasDiscIsHeld:
    """The floor, independent of the always_review toggle: a TV disc where every
    track was classified as bonus content and nothing matched an episode is a
    matching failure wearing a success costume — the jobs 5-10 signature."""

    async def test_all_extras_disc_parks_in_review(self):
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED] * 7, as_extras=True)

        state = await _run_completion(coord, job_id)

        assert state == JobState.REVIEW_NEEDED
        coord.finalize_disc_job.assert_not_awaited()

    async def test_reason_names_the_likely_cause(self):
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED] * 3, as_extras=True)

        await _run_completion(coord, job_id)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert "bonus content" in (job.review_reason or "")

    async def test_a_disc_with_one_real_episode_still_organizes(self):
        """Extras beside real episodes are the normal case the pre-filter exists
        for — that disc must keep auto-organizing."""
        coord, _ = _coord()
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, await _seed([TitleState.MATCHED], as_extras=True))
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=9,
                    duration_seconds=1340,
                    state=TitleState.MATCHED,
                    matched_episode="S01E05",
                    match_confidence=0.95,
                )
            )
            await session.commit()
            job_id = job.id

        await _run_completion(coord, job_id)

        coord.finalize_disc_job.assert_awaited_once()

    async def test_movie_disc_is_untouched_by_the_rule(self):
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED], as_extras=True)
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            job.content_type = ContentType.MOVIE
            session.add(job)
            await session.commit()

        await _run_completion(coord, job_id)

        coord.finalize_disc_job.assert_awaited_once()
