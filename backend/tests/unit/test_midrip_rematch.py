"""Mid-rip identity correction re-match (spec 2026-07-22)."""

import asyncio
import json

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


@pytest.mark.unit
async def test_rematch_ripped_resets_and_dispatches_matched_title(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(
        job.id,
        0,
        TitleState.MATCHED,
        output=str(f),
        matched_episode="S01E01",
        match_confidence=0.9,
        match_details=json.dumps({"reason": "old show"}),
    )

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    refreshed = await _get_title(t.id)
    assert refreshed.state == TitleState.QUEUED
    assert refreshed.matched_episode is None
    assert refreshed.match_confidence == 0.0
    assert refreshed.match_details is None
    assert t.id in dispatched


@pytest.mark.unit
async def test_rematch_ripped_leaves_ripping_titles_alone(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    still_ripping = await _add_title(job.id, 1, TitleState.RIPPING)  # no file yet

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert (await _get_title(still_ripping.id)).state == TitleState.RIPPING
    assert still_ripping.id not in dispatched


@pytest.mark.unit
async def test_rematch_ripped_cancels_inflight_match(monkeypatch, tmp_path):
    released = asyncio.Event()
    started = asyncio.Event()
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        # First dispatch blocks (old identity); re-dispatch is recorded.
        dispatched.append(title_id)
        if len(dispatched) == 1:
            started.set()
            await released.wait()

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))

    # Kick the first (old-identity) match; it parks in fake_match.
    await job_manager._dispatch_title_match(job.id, t.id, f)
    await started.wait()
    old_task = job_manager._match_tasks[t.id]

    # Release the parked task the instant it is cancelled so gather() returns.
    def _release_on_cancel():
        released.set()

    old_task.add_done_callback(lambda _t: _release_on_cancel())

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert old_task.cancelled() or old_task.done()
    assert dispatched.count(t.id) == 2  # re-dispatched after cancel


@pytest.mark.unit
async def test_rematch_ripped_skips_non_tv(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job(content_type=ContentType.MOVIE, detected_title="A Movie")
    f = tmp_path / "MOVIE_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.MATCHED, output=str(f))

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert dispatched == []
    assert (await _get_title(t.id)).state == TitleState.MATCHED
