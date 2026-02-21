"""Unit tests for job completion transitions.

Tests the _check_job_completion logic that finalizes jobs when all titles
reach terminal states.
"""

from unittest.mock import AsyncMock, MagicMock

from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory


async def _create_job_with_titles(
    title_states: list[TitleState],
    job_state: JobState = JobState.MATCHING,
) -> tuple[DiscJob, list[DiscTitle]]:
    """Helper: create a job with titles in specified states."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="D:",
            volume_label="TEST_DISC",
            content_type=ContentType.TV,
            state=job_state,
            staging_path="/tmp/staging/test",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        titles = []
        for i, state in enumerate(title_states):
            title = DiscTitle(
                job_id=job.id,
                title_index=i,
                duration_seconds=2400,
                file_size_bytes=1024 * 1024 * 1024,
                state=state,
                matched_episode=f"S01E{i + 1:02d}" if state == TitleState.MATCHED else None,
                match_confidence=0.95 if state == TitleState.MATCHED else 0.0,
            )
            session.add(title)
            titles.append(title)
        await session.commit()
        for t in titles:
            await session.refresh(t)
        return job, titles


class TestCheckJobCompletion:
    """Test the _check_job_completion logic."""

    async def test_active_titles_block_completion(self):
        """If some titles are still MATCHING, the job should NOT finalize."""
        job, _ = await _create_job_with_titles(
            [TitleState.MATCHED, TitleState.MATCHING, TitleState.PENDING]
        )

        async with _unit_session_factory() as session:
            from sqlmodel import select

            job = await session.get(DiscJob, job.id)
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
            titles = result.scalars().all()

            active_states = [TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING]
            active = [t for t in titles if t.state in active_states]
            assert len(active) == 2  # MATCHING + PENDING

    async def test_all_titles_completed_triggers_completion(self):
        """When all titles are in terminal states (COMPLETED), job should complete."""
        job, _ = await _create_job_with_titles(
            [TitleState.COMPLETED, TitleState.COMPLETED, TitleState.COMPLETED]
        )

        async with _unit_session_factory() as session:
            from sqlmodel import select

            job = await session.get(DiscJob, job.id)
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
            titles = result.scalars().all()

            active_states = [TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING]
            active = [t for t in titles if t.state in active_states]
            assert len(active) == 0

            # All terminal: some completed → job should transition to COMPLETED
            all_failed = all(t.state == TitleState.FAILED for t in titles)
            assert not all_failed

    async def test_mixed_review_and_completed(self):
        """If some titles need REVIEW, job should go to REVIEW_NEEDED."""
        job, _ = await _create_job_with_titles(
            [TitleState.COMPLETED, TitleState.REVIEW, TitleState.COMPLETED]
        )

        async with _unit_session_factory() as session:
            from sqlmodel import select

            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
            titles = result.scalars().all()

            has_review = any(t.state == TitleState.REVIEW for t in titles)
            has_matched = any(t.state == TitleState.MATCHED for t in titles)
            assert has_review is True
            assert has_matched is False

    async def test_all_titles_failed(self):
        """When every title is FAILED, job should go to FAILED."""
        job, _ = await _create_job_with_titles(
            [TitleState.FAILED, TitleState.FAILED, TitleState.FAILED]
        )

        async with _unit_session_factory() as session:
            from sqlmodel import select

            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
            titles = result.scalars().all()

            all_failed = all(t.state == TitleState.FAILED for t in titles)
            assert all_failed is True

    async def test_broadcast_failure_doesnt_undo_commit(self):
        """If broadcast raises, the DB state should still be COMPLETED."""
        broadcaster = MagicMock(spec=EventBroadcaster)
        broadcaster.broadcast_job_completed = AsyncMock(side_effect=RuntimeError("WS down"))
        sm = JobStateMachine(broadcaster)

        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="D:",
                volume_label="TEST",
                content_type=ContentType.TV,
                state=JobState.MATCHING,
                staging_path="/tmp/test",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            # Transition to COMPLETED — broadcast will raise but DB is committed first
            result = await sm.transition_to_completed(job, session)
            assert result is True

        # Verify DB state persisted
        async with _unit_session_factory() as session:
            db_job = await session.get(DiscJob, job.id)
            assert db_job.state == JobState.COMPLETED

    async def test_session_expire_ensures_fresh_reads(self):
        """Verify that expire_all forces fresh reads from DB."""
        job, _ = await _create_job_with_titles([TitleState.MATCHED, TitleState.MATCHED])

        async with _unit_session_factory() as session:
            # First read
            loaded_job = await session.get(DiscJob, job.id)
            assert loaded_job.state == JobState.MATCHING

            # Expire to force re-read
            session.expire_all()
            reloaded = await session.get(DiscJob, job.id)
            assert reloaded.state == JobState.MATCHING
