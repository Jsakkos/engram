"""Every disc job gets a staging directory of its own (#768).

The staging directory name was ``job_<YYYYmmdd_HHMMSS>``: second resolution and
nothing else. Mounting two ISOs and then starting Engram fires one ``inserted``
event per virtual drive in the same instant, so both jobs were handed the SAME
directory. Both MakeMKV rips wrote into it, and because the rip watcher treats
any new MKV in a job's staging directory as one of that job's titles, each job
matched (and filed) the other disc's episodes.
"""

import importlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.models import DiscJob
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

jm_mod = importlib.import_module("app.services.job_manager")

_FROZEN_NOW = datetime(2026, 10, 16, 9, 33, 26)


class _FrozenDatetime(datetime):
    """Every job in the test is created in the same wall-clock second."""

    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.replace(tzinfo=tz)


@pytest.fixture
def _isolate_create(monkeypatch):
    """Stub the identification spawn, config and disc probe around _create_job_for_disc."""
    monkeypatch.setattr(job_manager._identification, "identify_disc", AsyncMock(return_value=None))
    monkeypatch.setattr(job_manager, "_on_task_done", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(staging_path="/tmp/staging", setup_complete=True)),
    )
    # A distinct fingerprint per drive: two different discs, as in the report.
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: f"HASH-{drive}")
    monkeypatch.setattr(jm_mod, "datetime", _FrozenDatetime)
    job_manager._drive_locks.clear()
    job_manager._last_job_created_at.clear()
    job_manager._active_jobs.clear()


async def _all_jobs():
    async with _unit_session_factory() as session:
        return (await session.execute(select(DiscJob).order_by(DiscJob.id))).scalars().all()


@pytest.mark.asyncio
async def test_two_drives_in_the_same_second_get_distinct_staging_dirs(_isolate_create):
    """The #768 repro: two mounted ISOs announced together at startup."""
    await job_manager._create_job_for_disc("F:", "MXC_Disc_2")
    await job_manager._create_job_for_disc("H:", "MXC_S2_Disc_1")

    jobs = await _all_jobs()
    assert len(jobs) == 2
    paths = {job.staging_path for job in jobs}
    assert len(paths) == 2, f"jobs share a staging directory: {paths}"


@pytest.mark.asyncio
async def test_staging_dir_keeps_job_prefix_and_names_the_job(_isolate_create):
    """Timed cleanup and the orphan scan only consider ``job_*`` directories.

    The job id in the name is what makes it unique, and it also lets a user (or
    a diagnostics bundle) map a directory on disk back to its job.
    """
    await job_manager._create_job_for_disc("F:", "MXC_Disc_2")

    (job,) = await _all_jobs()
    name = job.staging_path.replace("\\", "/").rsplit("/", 1)[-1]
    assert name == f"job_20261016_093326_{job.id}"
