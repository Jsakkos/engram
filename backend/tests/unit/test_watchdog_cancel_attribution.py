"""Issue #506: the stale-job watchdog must not masquerade as a user cancel.

``reconcile_and_advance`` cancels the in-flight rip task before force-advancing the
job's still-active titles. ``Task.cancel()`` only *schedules* CancelledError at the
cancelled coroutine's next await, so ``_run_ripping``'s handler runs concurrently
with the reconcile pass. Two defects fell out of that:

* the handler recorded ``"Cancelled by user"`` for an internal timeout, so a
  watchdog trip was indistinguishable from a genuine user cancel, and
* its unconditional FAILED write clobbered the REVIEW_NEEDED that reconcile had
  just computed. That clobber is unrecoverable rather than merely out-of-order:
  ``check_job_completion`` returns early on an already-terminal job, so once
  FAILED lands the intended outcome can never be re-derived. The user was left
  pointed at a review queue that the job state locked them out of.

The tests below drive the *real* concurrent path — the rip is parked mid-await when
the cancellation is delivered — rather than calling the two coroutines in a fixed
order, so they would catch a regression that merely reorders the writes.
"""

import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.discdb_exporter as exporter_mod
import app.core.sentinel as sentinel_mod
from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

# app.services.__init__ rebinds the name ``job_manager`` to the singleton, which
# shadows the submodule for a plain ``import ... as`` -- reach the module itself.
jm_mod = importlib.import_module("app.services.job_manager")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


@pytest.fixture
def rip_env(monkeypatch, tmp_path):
    """Neutralize the side-effecting steps in _run_ripping that are orthogonal to
    cancellation attribution: physical eject, the makemkv log dir, and the
    terminal-state callbacks (staging cleanup, Discord notification).

    Dropping ``_on_terminal_callbacks`` also keeps the jobs that legitimately end
    FAILED here from leaking a Discord ``create_task`` onto the StaticPool.
    """
    monkeypatch.setattr(sentinel_mod, "eject_disc", lambda drive_id: None)
    monkeypatch.setattr(exporter_mod, "get_makemkv_log_dir", lambda job_id: tmp_path)
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
    return tmp_path


async def _seed(staging, *, subtitle_status=None, **title_kwargs):
    """A TV job parked in RIPPING with one selected title."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SINGIN_IN_THE_RAIN",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            detected_title="Some Show",
            detected_season=1,
            staging_path=str(staging),
            subtitle_status=subtitle_status,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        defaults = dict(
            job_id=job.id,
            title_index=0,
            duration_seconds=1380,
            state=TitleState.RIPPING,
            is_selected=True,
        )
        defaults.update(title_kwargs)
        title = DiscTitle(**defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


async def _reload(job_id, title_id=None):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        title = await session.get(DiscTitle, title_id) if title_id is not None else None
        return job, title


async def _start_hung_rip(job_id, monkeypatch) -> asyncio.Task:
    """Launch _run_ripping, register it the way start_ripping does, park it mid-rip.

    Parking the task at a real await is what makes these tests exercise the
    concurrent path: the cancellation is delivered while the rip is suspended, so
    its CancelledError handler and the reconcile pass interleave exactly as they
    do in production.
    """
    started = asyncio.Event()

    async def _hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()  # never resolves; only cancellation ends it

    monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_hang))
    task = asyncio.create_task(job_manager._run_ripping(job_id))
    job_manager._active_jobs[job_id] = task
    await started.wait()
    return task


async def test_watchdog_passes_stall_specific_failure_message(rip_env, monkeypatch):
    """Bug 1 at the source: the watchdog hands reconcile a reason that names the
    stall and its timeout, rather than one that later reads as a user cancel."""
    job, _title = await _seed(rip_env)
    captured = {}

    async def _spy(job_id, *, reason="forced", failure_message=None):
        captured["reason"] = reason
        captured["failure_message"] = failure_message
        return True

    monkeypatch.setattr(job_manager, "reconcile_and_advance", _spy)
    monkeypatch.setattr(job_manager, "_rip_task_alive", lambda jid: False)
    config = SimpleNamespace(
        timeout_identifying_seconds=0,
        timeout_ripping_seconds=1200,
        timeout_matching_seconds=0,
        timeout_organizing_seconds=0,
    )
    now = time.monotonic()
    job_manager._last_activity[job.id] = now - 5000  # well past the 1200s ceiling

    await job_manager._watchdog_check_job(job, config, now)

    assert captured["failure_message"] is not None
    assert "stalled" in captured["failure_message"].lower()
    assert "1200" in captured["failure_message"]
    assert captured["failure_message"] != "Cancelled by user"


async def test_reconcile_cancel_records_stall_reason_not_user_cancel(rip_env, monkeypatch):
    """(a) A watchdog-initiated cancel whose job ends up FAILED records the stall
    reason, never the user-cancel wording."""
    # No output file anywhere, so the title is force-advanced to FAILED and the
    # whole job lands FAILED -- the case where a job-level reason is user-visible.
    # subtitle_status="completed" keeps finalization off its "no reference
    # subtitles" review route, which would otherwise (correctly) explain the
    # wholesale match failure and park the job in REVIEW_NEEDED instead.
    job, title = await _seed(rip_env, subtitle_status="completed")
    task = await _start_hung_rip(job.id, monkeypatch)

    await job_manager.reconcile_and_advance(
        job.id,
        reason="stale timeout in ripping",
        failure_message="Ripping stalled - no progress after 1200s",
    )
    with pytest.raises(asyncio.CancelledError):
        await task

    refreshed, refreshed_title = await _reload(job.id, title.id)
    assert refreshed_title.state == TitleState.FAILED
    assert refreshed.state == JobState.FAILED
    assert refreshed.error_message != "Cancelled by user"
    assert refreshed.error_message == "Ripping stalled - no progress after 1200s"


async def test_reconcile_review_outcome_survives_the_cancelled_rip_task(rip_env, monkeypatch):
    """(c) Regression test for the race itself: titles force-advanced to REVIEW must
    leave the job in REVIEW_NEEDED, not FAILED.

    Before the fix the cancelled task's ``_fail_job`` won this race and stamped
    FAILED while reconcile was still mid-pass.
    """
    ripped = rip_env / "disc_t00.mkv"
    ripped.write_bytes(b"x")
    job, title = await _seed(rip_env, output_filename=str(ripped))
    task = await _start_hung_rip(job.id, monkeypatch)

    advanced = await job_manager.reconcile_and_advance(
        job.id,
        reason="stale timeout in ripping",
        failure_message="Ripping stalled - no progress after 1200s",
    )
    with pytest.raises(asyncio.CancelledError):
        await task

    refreshed, refreshed_title = await _reload(job.id, title.id)
    assert advanced is True
    assert refreshed_title.state == TitleState.REVIEW
    assert refreshed.state == JobState.REVIEW_NEEDED
    assert refreshed.error_message != "Cancelled by user"


async def test_user_cancel_still_records_cancelled_by_user(rip_env, monkeypatch):
    """(b) The genuine user path is untouched by the suppression."""
    job, _title = await _seed(rip_env)
    task = await _start_hung_rip(job.id, monkeypatch)

    await job_manager.cancel_job(job.id)
    with pytest.raises(asyncio.CancelledError):
        await task

    refreshed, _ = await _reload(job.id)
    assert refreshed.state == JobState.FAILED
    assert refreshed.error_message == "Cancelled by user"


async def test_user_cancel_of_job_with_review_titles_still_fails(rip_env, monkeypatch):
    """The caveat: suppression is scoped to reconcile-initiated cancels only.

    A user cancelling a job that already has REVIEW titles must still fail it -- a
    blanket "never overwrite a non-terminal outcome" guard would wrongly block this.
    """
    ripped = rip_env / "disc_t00.mkv"
    ripped.write_bytes(b"x")
    job, _title = await _seed(rip_env, output_filename=str(ripped))
    async with _unit_session_factory() as session:
        session.add(
            DiscTitle(
                job_id=job.id,
                title_index=1,
                duration_seconds=1380,
                state=TitleState.REVIEW,
                is_selected=True,
            )
        )
        await session.commit()
    task = await _start_hung_rip(job.id, monkeypatch)

    await job_manager.cancel_job(job.id)
    with pytest.raises(asyncio.CancelledError):
        await task

    refreshed, _ = await _reload(job.id)
    assert refreshed.state == JobState.FAILED
    assert refreshed.error_message == "Cancelled by user"
