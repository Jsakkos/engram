import pytest
from sqlalchemy import text

import app.database as _db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
def _no_discord_task(monkeypatch):
    """These tests drive jobs to COMPLETED/FAILED, whose terminal hook
    fire-and-forgets a Discord notification via asyncio.create_task. That
    detached task opens an async_session on the StaticPool in-memory DB and can
    outlive the test's event loop, leaking a connection and intermittently
    locking the shared DB for a later test. Stub the sender so completion here is
    side-effect-free. (_send_discord_notification is resolved on self at call
    time, so patching the instance attribute is honored even though
    _notify_discord_on_terminal is a pre-registered terminal callback.)
    """

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(job_manager, "_send_discord_notification", _noop)


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _job_with_titles(states):
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="T",
            state=JobState.MATCHING,
            content_type=ContentType.MOVIE,
            staging_path="/tmp/x",
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        for i, st in enumerate(states):
            s.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=i,
                    duration_seconds=100,
                    state=st,
                    is_selected=(st != TitleState.SKIPPED),
                    matched_episode=("S01E01" if st == TitleState.MATCHED else None),
                )
            )
        await s.commit()
        return job.id


async def _final_state(job_id):
    async with async_session() as s:
        return (await s.get(DiscJob, job_id)).state


async def test_all_skipped_completes():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.SKIPPED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.COMPLETED


async def test_skipped_plus_completed_completes():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.COMPLETED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.COMPLETED


async def test_skipped_plus_failed_is_failed():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.FAILED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.FAILED
