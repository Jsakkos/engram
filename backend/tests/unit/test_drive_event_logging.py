"""Regression tests: failures while handling a disc-insert event must be loud.

A DiscJob INSERT failure on disc insertion was once completely silent — it
propagated to the Sentinel's generic "Error in async callback" handler, which
logged it without a traceback and (before the InterceptHandler fix) misattributed
it. The disc-event path must log such failures with a stack trace at its own
layer rather than letting them vanish.
"""

import asyncio
import logging

import pytest

from app.services.job_manager import JobManager


@pytest.mark.asyncio
async def test_on_drive_event_logs_job_creation_failure(monkeypatch, caplog):
    """If job creation raises on disc insertion, the error is logged with a traceback."""
    jm = JobManager()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("INSERT failed: is_transcoding_enabled NOT NULL")

    monkeypatch.setattr(jm, "_create_job_for_disc", boom)

    caplog.set_level(logging.ERROR, logger="app.services.job_manager")

    # Must not propagate the exception out of the event handler...
    await jm._on_drive_event("E:", "inserted", "TEST_LABEL")

    # ...and must record it with the exception attached (exc_info / stack trace).
    failures = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info is not None]
    assert failures, "disc-insert job-creation failure was not logged with a traceback"
    assert any("INSERT failed" in str(r.exc_info[1]) for r in failures), (
        "logged error did not carry the originating exception"
    )


@pytest.mark.asyncio
async def test_identification_task_failure_is_logged(monkeypatch, caplog):
    """A failure in the fire-and-forget identify_disc task must not be swallowed."""
    jm = JobManager()

    async def boom(job_id):
        raise RuntimeError(f"identify boom for job {job_id}")

    monkeypatch.setattr(jm._identification, "identify_disc", boom)

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
