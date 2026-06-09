"""Unit tests for per-disc ContentHash dedup in JobManager.

Covers the pure _same_disc discriminator, the _compute_disc_hash retry wrapper,
and _create_job_for_disc's blocking decision: a same-labelled disc with a
DIFFERENT hash must be allowed through (the Breaking Bad S2 D1-vs-D2 bug),
while the same hash (or an unreadable hash) stays blocked.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

jm_mod = importlib.import_module("app.services.job_manager")


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


# --- _same_disc (pure) -----------------------------------------------------


def _job(content_hash, volume_label):
    return SimpleNamespace(content_hash=content_hash, volume_label=volume_label)


def test_same_disc_true_when_hashes_match():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "AAAA") is True


def test_same_disc_false_when_hashes_differ():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is False


def test_same_disc_falls_back_to_label_when_new_hash_missing():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", None) is True
    assert job_manager._same_disc(job, "OTHERLABEL", None) is False


def test_same_disc_falls_back_to_label_when_job_hash_missing():
    job = _job(None, "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is True
    assert job_manager._same_disc(job, "OTHERLABEL", "BBBB") is False


# --- _compute_disc_hash (retry) -------------------------------------------


@pytest.mark.asyncio
async def test_compute_disc_hash_returns_first_success(monkeypatch):
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "DEADBEEF")
    assert await job_manager._compute_disc_hash("F:") == "DEADBEEF"


@pytest.mark.asyncio
async def test_compute_disc_hash_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    calls = {"n": 0}

    def flaky(drive):
        calls["n"] += 1
        return None if calls["n"] < 2 else "CAFE"

    monkeypatch.setattr(jm_mod, "compute_content_hash", flaky)
    assert await job_manager._compute_disc_hash("F:") == "CAFE"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_compute_disc_hash_returns_none_when_never_ready(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: None)
    assert await job_manager._compute_disc_hash("F:") is None


# --- _create_job_for_disc dedup -------------------------------------------


async def _seed_active_job(*, drive, label, state, content_hash):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id=drive,
            volume_label=label,
            state=state,
            content_hash=content_hash,
            staging_path="/tmp/seed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _jobs_on_drive(drive):
    async with _unit_session_factory() as session:
        res = await session.execute(select(DiscJob).where(DiscJob.drive_id == drive))
        return res.scalars().all()


@pytest.fixture
def _isolate_create(monkeypatch):
    """Stub the identification spawn + config so _create_job_for_disc is unit-isolated."""
    monkeypatch.setattr(job_manager._identification, "identify_disc", AsyncMock(return_value=None))
    monkeypatch.setattr(job_manager, "_on_task_done", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(staging_path="/tmp/staging")),
    )
    # Default the disc fingerprint so the fixture is self-contained — without
    # this a future test could hit real disk I/O on a fake drive and silently
    # fall into the None-hash label-fallback path. Individual tests override it.
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "DEADBEEF")
    job_manager._drive_locks.clear()
    job_manager._last_job_created_at.clear()
    job_manager._active_jobs.clear()


@pytest.mark.asyncio
async def test_same_label_different_hash_creates_new_job(monkeypatch, _isolate_create):
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "BBBB")
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    jobs = await _jobs_on_drive("F:")
    assert len(jobs) == 2  # the genuinely-new Disc 2 was allowed through
    new = [j for j in jobs if j.state == JobState.IDENTIFYING]
    assert len(new) == 1 and new[0].content_hash == "BBBB"


@pytest.mark.asyncio
async def test_same_label_same_hash_blocks(monkeypatch, _isolate_create):
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "AAAA")
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    assert len(await _jobs_on_drive("F:")) == 1  # same disc lingering → blocked


@pytest.mark.asyncio
async def test_null_hash_same_label_blocks(monkeypatch, _isolate_create):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: None)
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    assert len(await _jobs_on_drive("F:")) == 1  # conservative fallback to label
