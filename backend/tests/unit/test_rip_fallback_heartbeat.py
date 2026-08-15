"""The per-title fallback rip must keep feeding the stale-job watchdog.

The filesystem progress monitor is the sole _note_activity heartbeat while a
job is RIPPING. Cancelling it before the fallback let reconcile_and_advance
cancel a perfectly healthy recovery rip after timeout_ripping_seconds.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

import app.database as _db
from app.core.extractor import RipResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager

# job_manager binds eject_disc at import time; patching the sentinel module
# alone no longer reaches its auto-eject call sites. `import app.services.
# job_manager as x` yields the singleton, so go through sys.modules.
_jm_module = sys.modules["app.services.job_manager"]


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


class _FallbackExtractor:
    """First ('all') pass produces nothing; the fallback writes a growing file."""

    def __init__(self, staging: Path):
        self.staging = staging

    def skip_title_index(self, job_id, idx):
        pass

    def unskip_title_index(self, job_id, idx):
        pass

    async def rip_titles(self, drive, output_dir, *, title_indices=None, job_id=None, **kw):
        if title_indices is None:
            return RipResult(success=True, output_files=[])
        # Fallback pass: grow a file for ~3s so the 1s progress monitor sees real
        # progress and — if it is running — fires _note_activity.
        p = self.staging / "TEST_t01.mkv"
        for i in range(1, 4):
            p.write_bytes(b"x" * 4096 * i)
            await asyncio.sleep(1.1)
        cb = kw.get("title_complete_callback")
        if cb:
            cb(1, p)
        return RipResult(success=True, output_files=[p])


async def _noop_match(job_id, title_id, file_path, *a, **kw):
    """Stand-in for matching_coordinator.match_single_file: this test only
    cares about the fs-monitor heartbeat, not the matching pipeline."""


async def test_fallback_rip_keeps_the_watchdog_clock_moving(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()

    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=1,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        s.add(
            DiscTitle(
                job_id=job.id,
                title_index=1,
                duration_seconds=1300,
                file_size_bytes=4096 * 3,
                state=TitleState.PENDING,
                is_selected=True,
            )
        )
        await s.commit()
        job_id = job.id

    ext = _FallbackExtractor(staging)
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr(job_manager, "_loop", asyncio.get_running_loop())
    # _run_ripping ejects at the end of the rip; drive "Z:" does not exist.
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)
    monkeypatch.setattr(_jm_module, "eject_disc", lambda *_a, **_k: None)
    # Matching is out of scope here and its real path waits (up to 600s) for
    # the file to look "done", which would leak a background task past this
    # test's teardown and corrupt later tests' DB state. Stub it out.
    monkeypatch.setattr(job_manager._matching, "match_single_file", _noop_match)

    beats: list[int] = []
    real_note = job_manager._note_activity

    def _spy(jid: int) -> None:
        beats.append(jid)
        real_note(jid)

    monkeypatch.setattr(job_manager, "_note_activity", _spy)

    await job_manager._run_ripping(job_id)

    # One beat is the pre-fallback clock reset. The point of this test is that
    # MORE arrive while the fallback rip is running — i.e. a live monitor.
    assert beats.count(job_id) > 1, (
        "the fallback rip produced no heartbeat; the stale-job watchdog will "
        "cancel any recovery pass longer than timeout_ripping_seconds"
    )
