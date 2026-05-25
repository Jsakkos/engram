"""Tests for stuck-job recovery, force-advance, per-track skip, and match attribution.

Covers the four fixes:
- Issue 1: reconcile_stuck_titles recovers titles orphaned in PENDING/RIPPING.
- Issue 2: a no-match title goes to REVIEW with match_source unset (not "engram").
- Issue 3/4: reconcile_and_advance / skip_title drive a stuck job to a resting state.

The unit-test conftest (isolate_database) patches ``async_session`` per module to an
in-memory SQLite DB. To share that DB, these helpers reach ``async_session`` through
the module object (``db.async_session``) so the monkeypatch is honored at call time.
Scenarios are built so finalization never organizes into the real library — stuck or
unmatched titles route to REVIEW, holding the job in REVIEW_NEEDED.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.database as db
from app.models import AppConfig, DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager


async def _make_job(
    staging: Path,
    *,
    content_type: ContentType = ContentType.TV,
    state: JobState = JobState.MATCHING,
) -> int:
    async with db.async_session() as session:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST_DISC",
            content_type=content_type,
            state=state,
            staging_path=str(staging),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _add_title(
    job_id: int,
    index: int,
    state: TitleState,
    *,
    output: str | None = None,
    matched_episode: str | None = None,
    selected: bool = True,
) -> int:
    async with db.async_session() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=2700,
            state=state,
            is_selected=selected,
            output_filename=output,
            matched_episode=matched_episode,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t.id


async def _title(title_id: int) -> DiscTitle:
    async with db.async_session() as session:
        return await session.get(DiscTitle, title_id)


async def _job(job_id: int) -> DiscJob:
    async with db.async_session() as session:
        return await session.get(DiscJob, job_id)


# --- Issue 1: reconcile_stuck_titles ---------------------------------------


@pytest.mark.asyncio
async def test_reconcile_stuck_no_file_fails(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.RIPPING)
    tid = await _add_title(job_id, 0, TitleState.RIPPING)  # no file anywhere

    await job_manager.reconcile_stuck_titles(job_id)

    assert (await _title(tid)).state == TitleState.FAILED


@pytest.mark.asyncio
async def test_reconcile_stuck_movie_with_file_matched(tmp_path):
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.MOVIE, state=JobState.RIPPING)
    tid = await _add_title(job_id, 0, TitleState.RIPPING, output=str(f))

    await job_manager.reconcile_stuck_titles(job_id)

    t = await _title(tid)
    assert t.state == TitleState.MATCHED
    assert t.output_filename == str(f)


@pytest.mark.asyncio
async def test_reconcile_stuck_tv_with_file_queues_match(tmp_path, monkeypatch):
    f = tmp_path / "disc_t03.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.RIPPING)
    tid = await _add_title(job_id, 3, TitleState.RIPPING, output=str(f))

    called = {}

    async def fake_discdb(jid, title, session):
        return False

    async def fake_match(jid, title_id, path):
        called["match"] = (jid, title_id, str(path))

    monkeypatch.setattr(job_manager._matching, "try_discdb_assignment", fake_discdb)
    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    await job_manager.reconcile_stuck_titles(job_id)
    await asyncio.sleep(0.05)  # let the queued match task run

    assert (await _title(tid)).state == TitleState.MATCHING
    assert called.get("match") == (job_id, tid, str(f))


# --- Issue 2: match attribution --------------------------------------------


@pytest.mark.asyncio
async def test_no_match_leaves_review_without_engram_source(tmp_path, monkeypatch):
    f = tmp_path / "disc_t05.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 5, TitleState.MATCHING, output=str(f))

    async def no_match(*args, **kwargs):
        return SimpleNamespace(
            episode_code=None,
            confidence=0.0,
            needs_review=True,
            match_details={"matches_found": 0, "matches_rejected": 10},
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", no_match
    )
    # Isolate the gating assertion from finalization.
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    t = await _title(tid)
    assert t.state == TitleState.REVIEW
    assert t.match_source is None  # NOT "engram"


@pytest.mark.asyncio
async def test_successful_match_sets_engram_source(tmp_path, monkeypatch):
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHING, output=str(f))

    async def good_match(*args, **kwargs):
        return SimpleNamespace(
            episode_code="S01E01",
            confidence=0.92,
            needs_review=False,
            match_details={"score": 0.92, "vote_count": 9},
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", good_match
    )
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    t = await _title(tid)
    assert t.state == TitleState.MATCHED
    assert t.match_source == "engram"


# --- Issue 3/4: force-advance + skip ---------------------------------------


@pytest.mark.asyncio
async def test_advance_sends_stuck_to_review(tmp_path):
    f = tmp_path / "disc_t05.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    await _add_title(job_id, 0, TitleState.MATCHED, matched_episode="S01E01")
    stuck = await _add_title(job_id, 5, TitleState.MATCHING, output=str(f))

    assert await job_manager.reconcile_and_advance(job_id, reason="test") is True

    assert (await _title(stuck)).state == TitleState.REVIEW
    assert (await _job(job_id)).state == JobState.REVIEW_NEEDED


@pytest.mark.asyncio
async def test_advance_no_file_fails_track(tmp_path):
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.RIPPING)
    stuck = await _add_title(job_id, 0, TitleState.RIPPING)  # no file

    assert await job_manager.reconcile_and_advance(job_id, reason="test") is True

    assert (await _title(stuck)).state == TitleState.FAILED


@pytest.mark.asyncio
async def test_advance_rejects_terminal(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.COMPLETED)
    assert await job_manager.reconcile_and_advance(job_id) is False


@pytest.mark.asyncio
async def test_skip_title_to_review(tmp_path):
    f = tmp_path / "disc_t02.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 2, TitleState.MATCHING, output=str(f))

    assert await job_manager.skip_title(job_id, tid) is True

    assert (await _title(tid)).state == TitleState.REVIEW
    assert (await _job(job_id)).state == JobState.REVIEW_NEEDED


@pytest.mark.asyncio
async def test_skip_title_already_resolved_returns_false(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHED)

    assert await job_manager.skip_title(job_id, tid) is False


# --- watchdog config helper ------------------------------------------------


def test_phase_timeout_reads_config():
    cfg = AppConfig()
    assert job_manager._phase_timeout(cfg, JobState.IDENTIFYING) == cfg.timeout_identifying_seconds
    assert job_manager._phase_timeout(cfg, JobState.RIPPING) == cfg.timeout_ripping_seconds
    assert job_manager._phase_timeout(cfg, JobState.MATCHING) == cfg.timeout_matching_seconds
    assert job_manager._phase_timeout(cfg, JobState.ORGANIZING) == cfg.timeout_organizing_seconds
    # Resting / untimed states have no ceiling.
    assert job_manager._phase_timeout(cfg, JobState.REVIEW_NEEDED) is None
    assert job_manager._phase_timeout(cfg, JobState.IDLE) is None
