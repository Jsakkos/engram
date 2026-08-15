"""_run_ripping must hand the extractor the disc's native->scan title map.

Without it should_abort_all_pass can never conclude that nothing is left to
rip, so a user who skips every trailing track saves no time at all.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

import app.database as _db
from app.core.extractor import RipResult
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


class _RecordingExtractor:
    """Captures rip_titles kwargs; rips nothing."""

    def __init__(self):
        self.kwargs: list[dict] = []

    def skip_title_index(self, job_id, idx):
        pass

    def unskip_title_index(self, job_id, idx):
        pass

    async def rip_titles(self, drive, output_dir, **kw):
        self.kwargs.append(kw)
        return RipResult(success=True, output_files=[Path("x.mkv")])


async def _seed(staging: Path, native_offset: int) -> int:
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=3,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        for idx in (1, 2, 3):
            s.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    output_index=idx + native_offset,
                    duration_seconds=1300,
                    file_size_bytes=4096,
                    state=TitleState.PENDING,
                    is_selected=True,
                )
            )
        await s.commit()
        return job.id


async def _seed_colliding_natives(staging: Path) -> int:
    """Titles 1 and 2 both resolve to native number 1: title 1 has no
    ``output_index`` (falls back to its ``title_index`` of 1), and title 2's
    real ``output_index`` genuinely is 1. Title 3 is unaffected."""
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=3,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        for idx, output_index in ((1, None), (2, 1), (3, None)):
            s.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    output_index=output_index,
                    duration_seconds=1300,
                    file_size_bytes=4096,
                    state=TitleState.PENDING,
                    is_selected=True,
                )
            )
        await s.commit()
        return job.id


async def test_all_pass_receives_the_native_to_scan_map(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    # output_index is MakeMKV's native number; here it is scan-1 (a disc whose
    # native numbering starts at 0), the divergence from issue #517.
    job_id = await _seed(staging, native_offset=-1)

    ext = _RecordingExtractor()
    monkeypatch.setattr(job_manager, "_extractor", ext)
    # _run_ripping ejects at the end of the rip; drive "Z:" does not exist.
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    await job_manager._run_ripping(job_id)

    assert ext.kwargs, "rip_titles was never called"
    first = ext.kwargs[0]
    assert first["title_indices"] is None, "every title selected -> all-pass"
    assert first["disc_title_map"] == {0: 1, 1: 2, 2: 3}


async def test_per_title_pass_gets_no_map(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id = await _seed(staging, native_offset=0)

    # Deselect one title so the rip is a per-title pass, where the skip-set is
    # consulted command-by-command and the boundary rule does not apply.
    async with async_session() as s:
        rows = (await s.execute(text("SELECT id FROM disc_titles ORDER BY title_index"))).all()
        t = await s.get(DiscTitle, rows[-1][0])
        t.is_selected = False
        await s.commit()

    ext = _RecordingExtractor()
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    await job_manager._run_ripping(job_id)

    assert ext.kwargs[0]["title_indices"] == [1, 2]
    assert ext.kwargs[0]["disc_title_map"] is None


async def test_colliding_native_numbers_disable_the_map(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id = await _seed_colliding_natives(staging)

    ext = _RecordingExtractor()
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)

    await job_manager._run_ripping(job_id)

    assert ext.kwargs, "rip_titles was never called"
    first = ext.kwargs[0]
    assert first["title_indices"] is None, "every title selected -> all-pass"
    assert first["disc_title_map"] is None, "colliding natives must disable the map, not corrupt it"
