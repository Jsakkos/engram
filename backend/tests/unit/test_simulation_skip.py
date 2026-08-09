"""The simulated rip must drop a track the user skipped mid-rip."""

import asyncio

import pytest
from sqlalchemy import text

import app.database as _db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def test_simulated_rip_skips_a_track_skipped_mid_rip():
    async with async_session() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path="/tmp/none",
            total_titles=3,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        titles = []
        for idx in (1, 2, 3):
            t = DiscTitle(
                job_id=job.id,
                title_index=idx,
                duration_seconds=1300,
                file_size_bytes=1024,
                state=TitleState.PENDING,
                is_selected=True,
            )
            s.add(t)
            await s.commit()
            await s.refresh(t)
            titles.append(t)
            s.expunge(t)
        job_id = job.id
        last_id = titles[-1].id

    # Use the wired singleton: SimulationService requires an EventBroadcaster and
    # a JobStateMachine, and JobManager has already constructed it with both.
    sim = job_manager._simulation
    rip = asyncio.create_task(
        sim._simulate_ripping(job_id, titles, speed_multiplier=1, content_type=ContentType.TV)
    )
    # Skip the LAST track while the loop is still on the first one.
    await asyncio.sleep(0.3)
    assert await job_manager.skip_rip_title(job_id, last_id) is True
    await rip

    async with async_session() as s:
        t = await s.get(DiscTitle, last_id)
        assert t.state == TitleState.SKIPPED
        assert t.output_filename is None
