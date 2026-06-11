"""Regression tests: failures while handling a disc-insert event must be loud.

A DiscJob INSERT failure on disc insertion was once completely silent — it
propagated to the Sentinel's generic "Error in async callback" handler, which
logged it without a traceback and (before the InterceptHandler fix) misattributed
it. The contract now is:

- ``_on_drive_event`` lets job-creation failures propagate, so its direct API
  caller (``/simulate/trigger-real-scan``) still surfaces a 500 instead of a
  bogus success.
- The Sentinel's ``_notify`` backstop logs a failing callback with a traceback,
  keeping the physical-disc path observable.
"""

import asyncio
import logging

import pytest

from app.services.job_manager import JobManager


@pytest.mark.asyncio
async def test_on_drive_event_propagates_job_creation_failure(monkeypatch):
    """Job-creation failures must propagate to direct callers, not be swallowed."""
    jm = JobManager()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("INSERT failed: is_transcoding_enabled NOT NULL")

    monkeypatch.setattr(jm, "_create_job_for_disc", boom)

    with pytest.raises(RuntimeError, match="INSERT failed"):
        await jm._on_drive_event("E:", "inserted", "TEST_LABEL")


@pytest.mark.asyncio
async def test_sentinel_logs_failing_drive_callback(caplog):
    """The Sentinel backstop logs a failing drive-event callback with a traceback."""
    from app.core.sentinel import DriveMonitor

    dm = DriveMonitor()

    async def boom(_drive, _event, _label):
        raise RuntimeError("INSERT failed: is_transcoding_enabled NOT NULL")

    dm.set_async_callback(boom, asyncio.get_event_loop())

    caplog.set_level(logging.ERROR, logger="app.core.sentinel")
    await dm._notify("inserted", "E:", "TEST_LABEL")

    failures = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert any("INSERT failed" in str(r.exc_info[1]) for r in failures), (
        "Sentinel backstop did not log the failing callback with a traceback"
    )


@pytest.mark.asyncio
async def test_identification_task_failure_is_logged(monkeypatch, caplog):
    """A failure in the fire-and-forget identify_disc task must not be swallowed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    jm = JobManager()

    async def boom(job_id):
        raise RuntimeError(f"identify boom for job {job_id}")

    monkeypatch.setattr(jm._identification, "identify_disc", boom)
    # The unit DB's default AppConfig has setup_complete=False, which would park
    # the insert at the first-run gate before the identify task ever spawns.
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(staging_path="/tmp/staging", setup_complete=True)),
    )

    caplog.set_level(logging.ERROR, logger="app.services.job_manager")

    await jm._create_job_for_disc("E:", "TEST_LABEL")

    # Drain the spawned identification task and let its done-callback fire.
    task = next(iter(jm._active_jobs.values()))
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    failures = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert any("identify boom" in str(r.exc_info[1]) for r in failures), (
        "identification task failure was silently swallowed (no done-callback)"
    )


@pytest.mark.asyncio
async def test_drive_removal_broadcasts_even_when_cancellation_fails(monkeypatch, caplog):
    """Clients must learn the disc is gone even if job cancellation errors out."""
    import importlib

    jm_mod = importlib.import_module("app.services.job_manager")

    jm = JobManager()

    async def boom(_drive):
        raise RuntimeError("cancellation blew up mid-DB-write")

    removed_broadcasts: list[tuple[str, str]] = []

    async def fake_broadcast_removed(drive, label):
        removed_broadcasts.append((drive, label))

    monkeypatch.setattr(jm, "_cancel_jobs_for_drive", boom)
    monkeypatch.setattr(jm_mod.event_broadcaster, "broadcast_drive_removed", fake_broadcast_removed)

    caplog.set_level(logging.ERROR, logger="app.services.job_manager")

    await jm._on_drive_event("E:", "removed", "TEST_LABEL")

    # The removal broadcast fired despite the cancellation failure...
    assert removed_broadcasts == [("E:", "TEST_LABEL")]
    # ...and the cancellation failure was still logged with a traceback.
    assert any(r.exc_info for r in caplog.records if r.levelno == logging.ERROR)
