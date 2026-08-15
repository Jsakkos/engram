"""Unit tests for the mid-rip disc eject feature."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import app.database as _db
from app.core.extractor import (
    MakeMKVExtractor,
    RipResult,
    TitleCompletionDetector,
    _delete_partials_at_abort,
)
from app.models.disc_job import ContentType, DiscJob, JobState
from app.services.job_manager import job_manager
from app.services.matching_coordinator import (
    EJECTED_RIP_MESSAGE,
    RIP_FAILURE_ERROR_CODES,
)

# Reuse the fake-makemkvcon harness that already drives the full
# _run_ripping -> real MakeMKVExtractor chain, rather than duplicating it.
# The two fixtures are autouse in their home module; importing the names binds
# them here so they apply to this module's chain test too.
from tests.unit.test_rip_skip_boundary_chain import (  # noqa: F401
    _clean_db,
    _make_popen,
    _seed_job,
    _stub_matching_and_eject,
)


def test_rip_result_defaults_aborted_for_eject_false():
    """A normal RipResult is not an eject abort."""
    result = RipResult(success=True, output_files=[])
    assert result.aborted_for_eject is False


def test_eject_abort_marks_job_ejected_and_cancelled():
    """eject_abort flags the job in both sets so the existing rip loop breaks out.

    _cancelled_jobs is what the reader loop already checks to terminate the
    subprocess; _ejected_jobs is what the return path checks (first) to report
    an eject rather than a cancel.
    """
    extractor = MakeMKVExtractor()
    extractor.eject_abort(42)
    assert 42 in extractor._ejected_jobs
    assert 42 in extractor._cancelled_jobs


def test_eject_abort_is_isolated_per_job():
    """Ejecting one job must not disturb another drive's rip."""
    extractor = MakeMKVExtractor()
    extractor.eject_abort(1)
    assert 2 not in extractor._ejected_jobs
    assert 2 not in extractor._cancelled_jobs


class _FakeCompletion:
    """Stands in for the rip's completion detector.

    files_incomplete_at_abort normally reports files that grew since the last
    poll (still being written). The fake just returns a fixed list.
    """

    def __init__(self, incomplete: list[str]) -> None:
        self._incomplete = incomplete

    def files_incomplete_at_abort(self, sizes: dict[str, int]) -> list[str]:
        return list(self._incomplete)


def test_delete_partials_removes_growing_file_and_stub(tmp_path: Path):
    """The in-flight (growing) file and the named stub are deleted."""
    (tmp_path / "title_t00.mkv").write_bytes(b"complete")
    (tmp_path / "title_t01.mkv").write_bytes(b"partial")
    (tmp_path / "title_t02.mkv").write_bytes(b"stub")

    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["title_t01.mkv"]),
        extra_stub="title_t02.mkv",
        lock=None,
        reason="test",
    )

    assert (tmp_path / "title_t00.mkv").exists(), "finished title must be kept"
    assert not (tmp_path / "title_t01.mkv").exists()
    assert not (tmp_path / "title_t02.mkv").exists()


def test_delete_partials_tolerates_missing_files(tmp_path: Path):
    """A doomed file already gone must not raise or abort the remaining deletions."""
    (tmp_path / "keep.mkv").write_bytes(b"complete")
    (tmp_path / "doomed.mkv").write_bytes(b"partial")

    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["gone.mkv", "doomed.mkv"]),
        extra_stub=None,
        lock=None,
        reason="test",
    )

    assert (tmp_path / "keep.mkv").exists()
    assert not (tmp_path / "doomed.mkv").exists()


def _write(path: Path, size: int) -> None:
    """Create/overwrite an mkv of exactly *size* bytes."""
    path.write_bytes(b"\0" * size)


def _sizes(directory: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in directory.glob("*.mkv")}


def test_eject_cleanup_preserves_title_that_finished_since_last_poll(tmp_path: Path):
    """The eject preparer's fresh poll saves a title that finished mid-window.

    Regression guard for the stale-poll bug: a title can finish writing between
    two routine completion polls, so it has *grown* since the last recorded
    size without being in _completed yet (that needs STABLE_CHECKS_REQUIRED
    polls plus a successor). Without the final poll eject_abort takes before
    terminating, the cleanup condemns it, and after an eject the disc is gone
    so the title cannot be re-ripped.

    Driven through the real TitleCompletionDetector so the actual
    files_incomplete_at_abort comparison is exercised.
    """
    detector = TitleCompletionDetector()
    title = tmp_path / "title_t00.mkv"

    # Routine poll while the title is still being written.
    _write(title, 900)
    detector.poll(_sizes(tmp_path))

    # The title finishes writing. It grew since that poll and is NOT completed.
    _write(title, 1000)
    assert not detector.is_completed(title.name)

    # What eject_abort does before killing MakeMKV: one final poll.
    detector.poll(_sizes(tmp_path))

    _delete_partials_at_abort(tmp_path, detector, None, lock=None, reason="disc eject")

    assert title.exists(), "a title that finished before the eject must be preserved"


def test_delete_partials_condemns_title_grown_since_a_stale_poll(tmp_path: Path):
    """Characterizes WHY the final poll is required (do not delete this test).

    Same scenario as above minus the fresh poll: the detector's sizes are stale,
    the finished title reads as 'still growing', and it is destroyed. This is
    the failure the eject preparer exists to prevent.
    """
    detector = TitleCompletionDetector()
    title = tmp_path / "title_t00.mkv"

    _write(title, 900)
    detector.poll(_sizes(tmp_path))
    _write(title, 1000)  # finished, but never polled again

    _delete_partials_at_abort(tmp_path, detector, None, lock=None, reason="disc eject")

    assert not title.exists()


def test_eject_abort_runs_preparer_before_cancelling(tmp_path: Path):
    """eject_abort polls while MakeMKV still runs, then marks the job."""
    extractor = MakeMKVExtractor()
    calls: list[str] = []

    def _preparer() -> None:
        # The subprocess must still be alive at this point, so neither flag
        # may be set yet.
        assert 7 not in extractor._cancelled_jobs
        assert 7 not in extractor._ejected_jobs
        calls.append("prepared")

    extractor._eject_preparers[7] = _preparer
    extractor.eject_abort(7)

    assert calls == ["prepared"]
    assert 7 in extractor._ejected_jobs
    assert 7 in extractor._cancelled_jobs


def test_eject_abort_without_a_registered_preparer_is_safe():
    """Ejecting a job that is not currently ripping must not raise."""
    extractor = MakeMKVExtractor()
    extractor.eject_abort(99)
    assert 99 in extractor._ejected_jobs


async def _wait_for_size(path: Path, size: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size >= size:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"timed out waiting for {path} to reach {size} bytes")


@pytest.mark.unit
async def test_live_rip_registers_a_preparer_that_saves_a_finished_title(tmp_path):
    """Chain test: _run_ripping -> real extractor -> eject_abort -> cleanup.

    The helper-level tests above prove the cleanup's semantics and eject_abort's
    ordering, but both supply the preparer themselves. Nothing asserted that a
    real rip registers one, so deleting the registration left them all green
    while the unrecoverable data-loss bug came back. This closes that seam.

    FS_POLL_INTERVAL is raised above the length of the whole fake rip on
    purpose: the reader loop then never polls after title 1 finishes writing,
    so the ONLY fresh reading available to the cleanup is the one the eject
    preparer takes. Without the registration the detector has no record of
    title 1 at all, it is condemned as "never polled", and a finished title is
    destroyed off a disc that has already left the drive.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, _title_ids = await _seed_job(staging, native_offset=0, count=2)

    write_log: list[int] = []
    procs: list = []
    job_manager._loop = asyncio.get_running_loop()
    extractor = job_manager._extractor
    title1 = staging / "TEST_t01.mkv"

    with (
        patch("app.core.extractor.FS_POLL_INTERVAL", 30.0),
        patch(
            "app.core.extractor.subprocess.Popen",
            side_effect=_make_popen(staging, [1, 2], write_log, procs),
        ),
    ):
        task = asyncio.create_task(job_manager._run_ripping(job_id))
        await _wait_for_size(title1, 4096)

        assert job_id in extractor._eject_preparers, (
            "a live rip must register an eject preparer, or eject cleanup runs on stale sizes"
        )

        # to_thread mirrors the contract eject_abort's docstring states: it
        # blocks on disc I/O under the rip's lock.
        await asyncio.to_thread(extractor.eject_abort, job_id)
        # wait_for, not a bare `await task`: it bounds a hung rip so a
        # regression cannot wedge CI.
        await asyncio.wait_for(task, timeout=60)

    assert title1.exists(), "a title that finished before the eject was destroyed"
    assert title1.stat().st_size == 4096, "the preserved title must be intact"
    assert job_id not in extractor._eject_preparers, "the preparer must not outlive the rip"


def test_rip_ejected_is_a_registered_rip_failure_code():
    """Registration grants rerip_eligible and non-rematchable status.

    An unregistered REVIEW error code gets auto-escalated and has its
    match_details overwritten by the re-match pass, which would destroy the
    re-rip bookkeeping this feature depends on.
    """
    assert "rip_ejected" in RIP_FAILURE_ERROR_CODES


def test_rip_ejected_is_excluded_from_auto_rematch():
    """_NON_REMATCHABLE_REVIEW_ERRORS is derived from RIP_FAILURE_ERROR_CODES."""
    from app.services.finalization_coordinator import _NON_REMATCHABLE_REVIEW_ERRORS

    assert "rip_ejected" in _NON_REMATCHABLE_REVIEW_ERRORS


def test_ejected_rip_message_is_user_facing():
    """The review queue shows this verbatim, so it must explain the recovery."""
    assert "eject" in EJECTED_RIP_MESSAGE.lower()
    assert EJECTED_RIP_MESSAGE.strip() == EJECTED_RIP_MESSAGE


# --- JobManager.eject_disc_for_job -----------------------------------------

# `app.services/__init__.py` rebinds the `app.services.job_manager` package
# attribute to the singleton, so `import app.services.job_manager as x` hands
# back the instance. Reach the real module through sys.modules to patch its
# module-level names.
_jm_module = sys.modules["app.services.job_manager"]


async def _seed_job_in_state(state: JobState, *, drive_id: str = "Z:") -> int:
    """A minimal job row parked in *state*.

    Extends test_rip_skip_boundary_chain's `_seed_job` pattern (which always
    seeds RIPPING with titles); these tests only need the job row's state and
    drive_id, and two of them need states `_seed_job` cannot produce.
    """
    async with _db.async_session() as s:
        job = DiscJob(
            drive_id=drive_id,
            volume_label="EJECT_TEST",
            state=state,
            content_type=ContentType.TV,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        return job.id


async def _job_state(job_id: int) -> JobState:
    async with _db.async_session() as s:
        job = await s.get(DiscJob, job_id)
        return job.state


class _EjectSpy:
    """Records eject_abort calls in place of the real extractor method."""

    def __init__(self) -> None:
        self.job_ids: list[int] = []

    def __call__(self, job_id: int) -> None:
        self.job_ids.append(job_id)


def _install_eject_stubs(monkeypatch, *, eject_ok: bool) -> tuple[_EjectSpy, list[str]]:
    """Stub eject_abort, eject_disc and notify_ejected; return the two spies."""
    abort_spy = _EjectSpy()
    notified: list[str] = []
    monkeypatch.setattr(job_manager._extractor, "eject_abort", abort_spy)
    monkeypatch.setattr(_jm_module, "eject_disc", lambda drive: eject_ok)
    monkeypatch.setattr(
        job_manager._drive_monitor, "notify_ejected", lambda drive: notified.append(drive)
    )
    return abort_spy, notified


@pytest.mark.unit
async def test_eject_while_ripping_stops_rip_and_does_not_cancel(monkeypatch):
    """The whole point of the feature: stop hammering the disc, keep the job.

    The in-flight _run_ripping task does the salvage itself, so the job must
    still be RIPPING when this returns; cancelling here would throw away the
    tracks that already ripped cleanly.
    """
    job_id = await _seed_job_in_state(JobState.RIPPING)
    abort_spy, notified = _install_eject_stubs(monkeypatch, eject_ok=True)

    result = await job_manager.eject_disc_for_job(job_id)

    assert abort_spy.job_ids == [job_id]
    assert notified == ["Z:"]
    assert result == {"ejected": True, "action": "rip_stopped"}
    assert await _job_state(job_id) == JobState.RIPPING


@pytest.mark.unit
async def test_failed_eject_still_stops_rip_and_skips_notify(monkeypatch):
    """A tray that refuses to open must not roll back the abort.

    notify_ejected must be skipped so the sentinel stays armed and still
    observes the disc when the user pops it out by hand.
    """
    job_id = await _seed_job_in_state(JobState.RIPPING)
    abort_spy, notified = _install_eject_stubs(monkeypatch, eject_ok=False)

    result = await job_manager.eject_disc_for_job(job_id)

    assert abort_spy.job_ids == [job_id]
    assert notified == []
    assert result == {"ejected": False, "action": "rip_stopped"}


@pytest.mark.unit
async def test_eject_while_identifying_cancels_the_job(monkeypatch):
    """Nothing has been ripped yet, so there is nothing to salvage."""
    job_id = await _seed_job_in_state(JobState.IDENTIFYING)
    _abort_spy, notified = _install_eject_stubs(monkeypatch, eject_ok=True)
    # A job driven to a terminal state fires Discord notification tasks that
    # leak a DB connection past teardown; drop the callbacks for this test.
    monkeypatch.setattr(_jm_module.state_machine, "_on_terminal_callbacks", [])

    cancelled: list[int] = []
    real_cancel = job_manager.cancel_job

    async def _spy_cancel(jid: int) -> None:
        cancelled.append(jid)
        await real_cancel(jid)

    monkeypatch.setattr(job_manager, "cancel_job", _spy_cancel)

    result = await job_manager.eject_disc_for_job(job_id)

    assert cancelled == [job_id]
    assert notified == ["Z:"]
    assert result == {"ejected": True, "action": "job_cancelled"}
    # The spy delegates to the real cancel, so the job really did go terminal.
    assert await _job_state(job_id) == JobState.FAILED


@pytest.mark.unit
async def test_eject_in_matching_raises(monkeypatch):
    """Past the rip the drive is no longer held, so there is nothing to eject."""
    job_id = await _seed_job_in_state(JobState.MATCHING)
    abort_spy, notified = _install_eject_stubs(monkeypatch, eject_ok=True)

    with pytest.raises(ValueError, match="Cannot eject"):
        await job_manager.eject_disc_for_job(job_id)

    assert abort_spy.job_ids == [], "a rejected eject must not touch MakeMKV"
    assert notified == []
