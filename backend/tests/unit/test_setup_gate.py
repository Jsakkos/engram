"""Unit tests for the first-run setup gate on disc insertion (P12).

With ``setup_complete=False`` a disc insert must NOT start the identify/rip
pipeline into unconfirmed default paths: the disc is parked in memory, a
``parked_discs`` broadcast tells the dashboard, and completing setup replays
the insert without requiring an eject/reinsert. The simulation insert path
bypasses ``_create_job_for_disc``, so the gate is tested directly here.
"""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.models import DiscJob
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

jm_mod = importlib.import_module("app.services.job_manager")


def _config(setup_complete: bool) -> SimpleNamespace:
    return SimpleNamespace(staging_path="/tmp/staging", setup_complete=setup_complete)


async def _jobs_on_drive(drive: str) -> list[DiscJob]:
    async with _unit_session_factory() as session:
        res = await session.execute(select(DiscJob).where(DiscJob.drive_id == drive))
        return list(res.scalars().all())


@pytest.fixture
def _isolate(monkeypatch):
    """Stub the identification spawn + disc probe; reset per-drive runtime state."""
    monkeypatch.setattr(job_manager._identification, "identify_disc", AsyncMock(return_value=None))
    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value=None))
    job_manager._drive_locks.clear()
    job_manager._last_job_created_at.clear()
    job_manager._active_jobs.clear()
    job_manager._parked_discs.clear()


@pytest.fixture
def _parked_broadcasts(monkeypatch):
    """Record every parked_discs broadcast (the dashboard banner feed)."""
    calls: list[list[dict]] = []

    async def record(discs):
        calls.append(discs)

    monkeypatch.setattr(jm_mod.event_broadcaster, "broadcast_parked_discs", record)
    return calls


@pytest.mark.asyncio
async def test_insert_parks_disc_when_setup_incomplete(monkeypatch, _isolate, _parked_broadcasts):
    """No job, no scan — the disc is parked and the dashboard is told."""
    monkeypatch.setattr(
        "app.services.config_service.get_config", AsyncMock(return_value=_config(False))
    )

    await job_manager._create_job_for_disc("E:", "INCEPTION_2010")

    assert await _jobs_on_drive("E:") == []
    assert job_manager._active_jobs == {}
    assert job_manager.parked_discs == [{"drive_id": "E:", "volume_label": "INCEPTION_2010"}]
    assert _parked_broadcasts[-1] == [{"drive_id": "E:", "volume_label": "INCEPTION_2010"}]


@pytest.mark.asyncio
async def test_insert_creates_job_when_setup_complete(monkeypatch, _isolate, _parked_broadcasts):
    """Existing installs (setup_complete=True) see zero behavior change."""
    monkeypatch.setattr(
        "app.services.config_service.get_config", AsyncMock(return_value=_config(True))
    )

    await job_manager._create_job_for_disc("E:", "INCEPTION_2010")
    await asyncio.sleep(0)  # let the stubbed identify task settle

    assert len(await _jobs_on_drive("E:")) == 1
    assert job_manager.parked_discs == []
    assert _parked_broadcasts == []


@pytest.mark.asyncio
async def test_drive_removed_unparks_disc(monkeypatch, _isolate, _parked_broadcasts):
    """Ejecting a parked disc clears it from the parked set and re-broadcasts."""
    monkeypatch.setattr(
        "app.services.config_service.get_config", AsyncMock(return_value=_config(False))
    )
    await job_manager._create_job_for_disc("E:", "INCEPTION_2010")

    await job_manager._on_drive_event("E:", "removed", "")

    assert job_manager.parked_discs == []
    assert _parked_broadcasts[-1] == []


@pytest.mark.asyncio
async def test_resume_parked_discs_replays_insert(monkeypatch, _isolate, _parked_broadcasts):
    """Completing setup picks up the parked disc — no eject/reinsert needed."""
    cfg = _config(False)
    monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
    await job_manager._create_job_for_disc("E:", "INCEPTION_2010")
    assert await _jobs_on_drive("E:") == []

    cfg.setup_complete = True
    await job_manager.resume_parked_discs()
    await asyncio.sleep(0)  # let the stubbed identify task settle

    jobs = await _jobs_on_drive("E:")
    assert len(jobs) == 1
    assert jobs[0].volume_label == "INCEPTION_2010"
    assert job_manager.parked_discs == []
    assert _parked_broadcasts[-1] == []


@pytest.mark.asyncio
async def test_resume_parked_discs_noop_when_nothing_parked(_isolate, _parked_broadcasts):
    """Every settings save from a configured install hits this path — keep it silent."""
    await job_manager.resume_parked_discs()

    assert _parked_broadcasts == []


@pytest.mark.asyncio
async def test_config_update_with_setup_complete_resumes_parked(monkeypatch):
    """PUT /api/config flipping setup_complete=true releases parked discs."""
    from app.api.routes import ConfigUpdate, update_config

    monkeypatch.setattr("app.services.config_service.ensure_paths_exist", AsyncMock())
    resume = AsyncMock()
    monkeypatch.setattr(job_manager, "resume_parked_discs", resume)

    await update_config(ConfigUpdate(setup_complete=True))

    resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_update_without_setup_complete_does_not_resume(monkeypatch):
    """An ordinary settings save must not touch the parked-disc machinery."""
    from app.api.routes import ConfigUpdate, update_config

    monkeypatch.setattr("app.services.config_service.ensure_paths_exist", AsyncMock())
    resume = AsyncMock()
    monkeypatch.setattr(job_manager, "resume_parked_discs", resume)

    await update_config(ConfigUpdate(max_concurrent_matches=2))

    resume.assert_not_awaited()
