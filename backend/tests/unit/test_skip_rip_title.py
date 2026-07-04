from pathlib import Path

import pytest
from sqlalchemy import text

import app.database as _db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


def test_skipped_state_exists_and_is_distinct():
    assert TitleState.SKIPPED == "skipped"
    assert TitleState.SKIPPED not in (TitleState.COMPLETED, TitleState.FAILED)


def async_session():
    # Resolve dynamically so the unit conftest's monkeypatch of
    # app.database.async_session (in-memory engine) is honored; a module-level
    # `from app.database import async_session` would bind the real engine and
    # diverge from the session job_manager uses.
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _make_job(state=JobState.RIPPING, title_state=TitleState.PENDING):
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=state,
            content_type=ContentType.TV,
            staging_path="/tmp/none",
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        title = DiscTitle(
            job_id=job.id, title_index=3, duration_seconds=1200, state=title_state, is_selected=True
        )
        s.add(title)
        # A second, always-active guard title so skipping `title` never leaves the
        # job all-terminal — that would fire job completion (and its terminal-state
        # hooks) inside the skip, an unwanted side effect for these unit tests. The
        # all-skipped completion path is covered directly in test_completion_skipped.
        guard = DiscTitle(
            job_id=job.id,
            title_index=99,
            duration_seconds=1200,
            state=TitleState.PENDING,
            is_selected=True,
        )
        s.add(guard)
        await s.commit()
        await s.refresh(title)
        return job.id, title.id


async def test_skip_rip_pending_title_marks_skipped():
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    ok = await job_manager.skip_rip_title(job_id, title_id)
    assert ok is True
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.SKIPPED
        assert t.is_selected is False
    assert 3 in job_manager._extractor._skipped_indices.get(job_id, set())


async def test_skip_rip_rejects_ripping_title():
    job_id, title_id = await _make_job(title_state=TitleState.RIPPING)
    ok = await job_manager.skip_rip_title(job_id, title_id)
    assert ok is False


async def test_unskip_restores_pending():
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    await job_manager.skip_rip_title(job_id, title_id)
    ok = await job_manager.unskip_rip_title(job_id, title_id)
    assert ok is True
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.PENDING
        assert t.is_selected is True
    assert 3 not in job_manager._extractor._skipped_indices.get(job_id, set())


async def test_on_title_ripped_deletes_skipped_file(tmp_path):
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    await job_manager.skip_rip_title(job_id, title_id)

    fake = tmp_path / "TEST_t03.mkv"
    fake.write_bytes(b"x" * 1024)

    async with async_session() as s:
        title = await s.get(DiscTitle, title_id)
        sorted_titles = [title]

    await job_manager._on_title_ripped(job_id, 1, Path(fake), sorted_titles)

    assert not fake.exists()
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.SKIPPED
