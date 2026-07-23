"""Mid-rip identity correction re-match (spec 2026-07-22)."""

import asyncio

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_coordinator_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.identification_coordinator.async_session", _unit_session_factory
    )


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


async def _seed_job(**kwargs):
    kwargs.setdefault("content_type", ContentType.TV)
    kwargs.setdefault("detected_title", "Show A")
    kwargs.setdefault("identity_prompt_json", None)
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_A_S1D1",
            state=JobState.RIPPING,
            staging_path=kwargs.pop("staging", "/tmp/staging/repro"),
            **kwargs,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _add_title(job_id, index, state, output=None, **kwargs):
    async with _unit_session_factory() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=1380,
            state=state,
            output_filename=output,
            is_selected=True,
            **kwargs,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t


async def _get_job(job_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


async def _get_title(title_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.unit
async def test_dispatch_title_match_tracks_then_clears_task(monkeypatch, tmp_path):
    released = asyncio.Event()
    started = asyncio.Event()

    async def fake_match(job_id, title_id, file_path):
        started.set()
        await released.wait()

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))

    assert await job_manager._dispatch_title_match(job.id, t.id, f)
    await started.wait()
    task = job_manager._match_tasks.get(t.id)
    assert task is not None  # tracked while running

    released.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert t.id not in job_manager._match_tasks  # cleared on completion
