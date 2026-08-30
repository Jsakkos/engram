"""Unit tests for the mid-rip disc eject feature."""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.database as _db
from app.core.extractor import (
    MakeMKVExtractor,
    RipResult,
    TitleCompletionDetector,
    _delete_partials_at_abort,
)
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
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


@pytest.fixture(autouse=True)
def _clear_eject_flags():
    """Never leak an eject flag onto the module-level extractor singleton.

    These tests call eject_abort on the real singleton, and job ids restart at
    1 for every in-memory database. A leaked id makes a LATER test's job of the
    same id look already-ejected: its rip stops instantly and the job jumps to
    review_needed without ever reaching RIPPING. The tiers currently run in
    separate pytest processes, which hides this, so the leak only bites whoever
    first runs the whole suite in one command.
    """
    yield
    job_manager._extractor._ejected_jobs.clear()
    job_manager._extractor._cancelled_jobs.clear()


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

# app.services.__init__ rebinds the name ``job_manager`` to the singleton, which
# shadows the submodule for a plain ``import ... as`` -- reach the module itself.
# Only needed for its ``state_machine`` singleton; ``eject_disc`` is stubbed on
# the sentinel module (see _install_eject_stubs).
jm_mod = sys.modules["app.services.job_manager"]


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


class _EjectStubs:
    """Recorder for the eject collaborators.

    ``calls`` is a single ordered log both the eject_abort and eject_disc stubs
    append to, so the abort-before-eject ordering can be asserted. Stubbing them
    independently would let a refactor that swapped the two lines pass silently,
    and the ordering is load-bearing: MakeMKV holds a handle on the drive, so a
    tray asked to open first almost always refuses.
    """

    def __init__(self, eject_ok: bool) -> None:
        self.eject_ok = eject_ok
        self.calls: list[str] = []
        self.abort_job_ids: list[int] = []
        self.notified: list[str] = []

    def eject_abort(self, job_id: int) -> None:
        self.calls.append("abort")
        self.abort_job_ids.append(job_id)

    def eject_disc(self, drive: str) -> bool:
        self.calls.append("eject")
        return self.eject_ok

    def notify_ejected(self, drive: str) -> None:
        self.notified.append(drive)


def _install_eject_stubs(monkeypatch, *, eject_ok: bool) -> _EjectStubs:
    """Stub eject_abort, eject_disc and notify_ejected onto one recorder.

    eject_disc is patched on the sentinel module, matching every other rip test
    in this tree. That reaches JobManager because its call sites import the name
    *inside* the method, so the attribute is resolved at call time. Keep the
    import function-local if you touch it, or this stub silently stops working
    and a unit test opens the physical tray.
    """
    stubs = _EjectStubs(eject_ok)
    # eject_abort and notify_ejected are instance attributes on the singletons,
    # so they are patched directly and are unaffected by the sentinel patch.
    monkeypatch.setattr(job_manager._extractor, "eject_abort", stubs.eject_abort)
    monkeypatch.setattr(job_manager._drive_monitor, "notify_ejected", stubs.notify_ejected)
    monkeypatch.setattr("app.core.sentinel.eject_disc", stubs.eject_disc)
    return stubs


@pytest.mark.unit
async def test_eject_while_ripping_stops_rip_and_does_not_cancel(monkeypatch):
    """The whole point of the feature: stop hammering the disc, keep the job.

    The in-flight _run_ripping task does the salvage itself, so the job must
    still be RIPPING when this returns; cancelling here would throw away the
    tracks that already ripped cleanly.
    """
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=True)

    result = await job_manager.eject_disc_for_job(job_id)

    assert stubs.abort_job_ids == [job_id]
    assert stubs.calls == ["abort", "eject"], (
        "MakeMKV must release the drive before the tray is asked to open"
    )
    assert stubs.notified == ["Z:"]
    assert result == {"ejected": True, "action": "rip_stopped"}
    assert await _job_state(job_id) == JobState.RIPPING


@pytest.mark.unit
async def test_failed_eject_still_stops_rip_and_skips_notify(monkeypatch):
    """A tray that refuses to open must not roll back the abort.

    notify_ejected must be skipped so the sentinel stays armed and still
    observes the disc when the user pops it out by hand.
    """
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=False)

    result = await job_manager.eject_disc_for_job(job_id)

    assert stubs.abort_job_ids == [job_id]
    assert stubs.calls == ["abort", "eject"]
    assert stubs.notified == []
    assert result == {"ejected": False, "action": "rip_stopped"}


@pytest.mark.unit
async def test_eject_while_identifying_cancels_the_job(monkeypatch):
    """Nothing has been ripped yet, so there is nothing to salvage."""
    job_id = await _seed_job_in_state(JobState.IDENTIFYING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=True)
    # A job driven to a terminal state fires Discord notification tasks that
    # leak a DB connection past teardown; drop the callbacks for this test.
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])

    cancelled: list[int] = []
    real_cancel = job_manager.cancel_job

    async def _spy_cancel(jid: int) -> None:
        cancelled.append(jid)
        await real_cancel(jid)

    monkeypatch.setattr(job_manager, "cancel_job", _spy_cancel)

    result = await job_manager.eject_disc_for_job(job_id)

    assert cancelled == [job_id]
    assert stubs.calls == ["eject"], "no rip is running, so there is no MakeMKV to abort"
    assert stubs.notified == ["Z:"]
    assert result == {"ejected": True, "action": "job_cancelled"}
    # The spy delegates to the real cancel, so the job really did go terminal.
    assert await _job_state(job_id) == JobState.FAILED


@pytest.mark.unit
async def test_eject_in_matching_raises(monkeypatch):
    """Past the rip the drive is no longer held, so there is nothing to eject."""
    job_id = await _seed_job_in_state(JobState.MATCHING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=True)

    with pytest.raises(ValueError, match="Cannot eject"):
        await job_manager.eject_disc_for_job(job_id)

    assert stubs.abort_job_ids == [], "a rejected eject must not touch MakeMKV"
    assert stubs.calls == [], "a rejected eject must not touch the drive at all"
    assert stubs.notified == []


# --- _route_unfinished_titles_after_eject -----------------------------------


async def _set_title_states(title_ids: list[int], states: list[TitleState]) -> None:
    """Force the seeded titles into *states*, positionally."""
    async with _db.async_session() as s:
        for tid, state in zip(title_ids, states, strict=True):
            title = await s.get(DiscTitle, tid)
            title.state = state
            s.add(title)
        await s.commit()


async def _title_rows(title_ids: list[int]) -> list[DiscTitle]:
    async with _db.async_session() as s:
        return [await s.get(DiscTitle, tid) for tid in title_ids]


@pytest.mark.unit
async def test_ejected_rip_routes_unfinished_titles_to_review_not_failed(tmp_path):
    """Both flavours of unfinished title must land in REVIEW as re-rippable.

    A never-started title (PENDING) and an interrupted one (RIPPING) are
    indistinguishable after the eject abort deletes the truncated file, and
    reconcile_stuck_titles would mark BOTH of them FAILED. FAILED is not
    re-rippable, so the disc the user is about to clean and reinsert would be
    unrecoverable -- the exact opposite of what this feature promises.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, title_ids = await _seed_job(staging, native_offset=0, count=2)
    await _set_title_states(title_ids, [TitleState.PENDING, TitleState.RIPPING])

    await job_manager._route_unfinished_titles_after_eject(job_id)

    for title in await _title_rows(title_ids):
        assert title.state == TitleState.REVIEW, (
            f"title {title.title_index} left {title.state.value}, not REVIEW"
        )
        details = json.loads(title.match_details)
        assert details["error"] == "rip_ejected"
        assert details["rerip_eligible"] is True
        assert details["message"]


@pytest.mark.unit
async def test_ejected_rip_leaves_finished_titles_alone(tmp_path):
    """A track that ripped cleanly before the eject must continue to matching.

    QUEUED means the rip finished and the title is waiting on a matching slot.
    Salvaging it into REVIEW would make the user re-rip work that already
    succeeded.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, title_ids = await _seed_job(staging, native_offset=0, count=1)
    await _set_title_states(title_ids, [TitleState.QUEUED])

    await job_manager._route_unfinished_titles_after_eject(job_id)

    (title,) = await _title_rows(title_ids)
    assert title.state == TitleState.QUEUED
    assert title.match_details is None


@pytest.mark.unit
async def test_route_unfinished_after_eject_is_idempotent(tmp_path):
    """Two eject checks bracket the per-title fallback, so both can fire.

    A second pass must not re-write match_details: that would bump the
    re-rip attempt bookkeeping (eventually flipping rerip_eligible to False)
    for a disc that was only ejected once.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, title_ids = await _seed_job(staging, native_offset=0, count=1)
    await _set_title_states(title_ids, [TitleState.PENDING])

    await job_manager._route_unfinished_titles_after_eject(job_id)
    (first,) = await _title_rows(title_ids)
    first_details = first.match_details

    await job_manager._route_unfinished_titles_after_eject(job_id)
    (second,) = await _title_rows(title_ids)

    assert second.state == TitleState.REVIEW
    assert second.match_details == first_details


@pytest.mark.unit
async def test_live_eject_routes_an_unripped_title_to_review_not_failed(tmp_path):
    """Ordering guard driven through the REAL _run_ripping, not the helper.

    Calling the helper directly proves the helper; it says nothing about WHERE
    _run_ripping calls it. Routing must happen before the post-rip
    backfill/reconcile pass, because reconcile_stuck_titles marks a fileless
    PENDING/RIPPING title FAILED and the eject abort has already deleted the
    truncated in-flight file. Move the call after reconcile and title 3 -- the
    one the fake process never reaches -- lands FAILED here.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, title_ids = await _seed_job(staging, native_offset=0, count=3)

    write_log: list[int] = []
    procs: list = []
    job_manager._loop = asyncio.get_running_loop()
    extractor = job_manager._extractor
    title1 = staging / "TEST_t01.mkv"

    with (
        patch("app.core.extractor.FS_POLL_INTERVAL", 30.0),
        patch(
            "app.core.extractor.subprocess.Popen",
            side_effect=_make_popen(staging, [1, 2, 3], write_log, procs),
        ),
    ):
        task = asyncio.create_task(job_manager._run_ripping(job_id))
        await _wait_for_size(title1, 4096)
        await asyncio.to_thread(extractor.eject_abort, job_id)
        await asyncio.wait_for(task, timeout=60)

    assert len(procs) == 1, "the per-title fallback must be skipped after an eject"

    async with _db.async_session() as s:
        never_started = await s.get(DiscTitle, title_ids[2])
    assert never_started.state == TitleState.REVIEW, (
        f"a title unripped at eject landed {never_started.state.value}; "
        f"routing must run BEFORE reconcile_stuck_titles"
    )
    details = json.loads(never_started.match_details)
    assert details["error"] == "rip_ejected"
    assert details["rerip_eligible"] is True


@pytest.mark.asyncio
async def test_eject_spares_a_title_that_finished_just_before_the_abort(tmp_path):
    """A track with real output must not be told to re-rip itself.

    The extractor's final completion poll fires title_complete_callback for a
    title that finished just before the abort, and that callback reaches the DB
    asynchronously. So the row can still read RIPPING here while a complete
    .mkv already sits in staging. Routing on DB state alone would park a
    perfectly good rip in REVIEW telling the user to re-rip it.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    job_id, title_ids = await _seed_job(staging, native_offset=0, count=2)

    # Title 1 finished writing; its callback has not landed, so the row is
    # still RIPPING. Title 2 was never reached.
    (staging / "TEST_t01.mkv").write_bytes(b"x" * 4096)
    async with _db.async_session() as s:
        t1 = await s.get(DiscTitle, title_ids[0])
        t1.state = TitleState.RIPPING
        s.add(t1)
        await s.commit()

    await job_manager._route_unfinished_titles_after_eject(job_id)

    async with _db.async_session() as s:
        finished = await s.get(DiscTitle, title_ids[0])
        never_started = await s.get(DiscTitle, title_ids[1])

    assert finished.state == TitleState.RIPPING, (
        f"a title with complete output landed {finished.state.value}; it must be "
        f"left to the normal completion path, not parked in review"
    )
    assert finished.match_details is None
    assert never_started.state == TitleState.REVIEW
    assert json.loads(never_started.match_details)["error"] == "rip_ejected"


# --- _release_drive ---------------------------------------------------------


@pytest.mark.unit
async def test_release_drive_notifies_with_the_outcome(monkeypatch):
    from app.core.discord_notifier import RIP_OUTCOME_COMPLETE

    job_id = await _seed_job_in_state(JobState.RIPPING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    await job_manager._release_drive(job_id, "Z:", RIP_OUTCOME_COMPLETE)
    await asyncio.sleep(0)  # let the fire-and-forget task run

    assert sent == [(job_id, "ripped", {"rip_outcome": "Complete"})]


@pytest.mark.unit
async def test_release_drive_notifies_even_when_auto_eject_is_off(monkeypatch):
    """No tray opens, but the disc is still copied and the user still wants to
    know. The notify sits outside the eject branch for exactly this case."""
    from app.core.discord_notifier import RIP_OUTCOME_COMPLETE
    from app.services.config_service import update_config

    await update_config(auto_eject_enabled=False)
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    ejected = await job_manager._release_drive(job_id, "Z:", RIP_OUTCOME_COMPLETE)
    await asyncio.sleep(0)

    assert ejected is False
    assert stubs.calls == [], "auto-eject off must not open the tray"
    assert sent == [(job_id, "ripped", {"rip_outcome": "Complete"})]

    await update_config(auto_eject_enabled=True)


@pytest.mark.unit
async def test_release_drive_rearms_sentinel_on_a_falsy_eject_by_default(monkeypatch):
    """eject_disc returns False unconditionally on macOS and for a Linux drive
    id that fails the optical-drive regex. The automatic sites must still
    re-arm the sentinel there, or the next disc is never detected."""
    from app.core.discord_notifier import RIP_OUTCOME_COMPLETE

    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=False)
    monkeypatch.setattr(job_manager, "_send_discord_notification", AsyncMock())

    await job_manager._release_drive(job_id, "Z:", RIP_OUTCOME_COMPLETE)

    assert stubs.notified == ["Z:"]


@pytest.mark.unit
async def test_release_drive_skips_rearm_on_failed_eject_when_asked(monkeypatch):
    """The user-initiated path reports `ejected` back to the UI and must leave
    the sentinel armed so it sees the disc when popped by hand."""
    from app.core.discord_notifier import RIP_OUTCOME_STOPPED_EARLY

    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=False)
    monkeypatch.setattr(job_manager, "_send_discord_notification", AsyncMock())

    await job_manager._release_drive(
        job_id,
        "Z:",
        RIP_OUTCOME_STOPPED_EARLY,
        respect_auto_eject=False,
        rearm_on_failed_eject=False,
    )

    assert stubs.notified == []


# --- call-site coverage -----------------------------------------------------


@pytest.mark.unit
async def test_eject_while_ripping_sends_a_stopped_early_notification(monkeypatch):
    job_id = await _seed_job_in_state(JobState.RIPPING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    await job_manager.eject_disc_for_job(job_id)
    await asyncio.sleep(0)

    assert sent == [(job_id, "ripped", {"rip_outcome": "Stopped early"})]


@pytest.mark.unit
async def test_eject_while_identifying_sends_no_ripped_notification(monkeypatch):
    """Nothing was copied, so "Disc Ripped" would be a lie. The notify sits
    inside the RIPPING branch, below the IDENTIFYING fork."""
    job_id = await _seed_job_in_state(JobState.IDENTIFYING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    # cancel_job drives the job terminal, whose own (unrelated) "failed"
    # notification would otherwise land on this spy -- and leak a DB connection
    # past teardown. Drop the terminal callbacks, as the sibling identify test
    # does, so what remains on the spy is exactly what eject_disc_for_job sent.
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
    mock_send = AsyncMock()
    monkeypatch.setattr(job_manager, "_send_discord_notification", mock_send)

    await job_manager.eject_disc_for_job(job_id)
    await asyncio.sleep(0)

    mock_send.assert_not_called()
