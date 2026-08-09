"""Chain test spanning ``_run_ripping`` -> the real ``MakeMKVExtractor`` -> the
all-pass boundary skip-abort.

The feature has three layers, and until now each was covered only by a test
that faked the layer next to it:

* ``tests/unit/test_extractor_skip.py`` drives the real extractor but fakes
  ``subprocess.Popen`` directly, calling ``rip_titles(..., disc_title_map=...)``
  itself -- it never goes through ``job_manager``.
* ``tests/unit/test_rip_disc_title_map.py`` drives the real
  ``job_manager._run_ripping`` but fakes the *extractor*, so it proves what
  kwargs get passed, never whether the extractor actually acts on them.

Nothing exercised the whole chain end to end, and an inert map is invisible
to a files-on-disk check: with the boundary abort disabled, a full-disc 'all'
pass still writes every selected title's file, exactly like the working case.
The only difference is whether MakeMKV (here, the fake process) was ever
asked to write the titles that follow a should-have-aborted boundary --  so
these tests assert on the fake's write log, not merely on which files survive
to the end.
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

import app.database as _db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager

# `app.services/__init__.py` does `from app.services.job_manager import
# job_manager`, which -- because the imported name shadows the submodule's own
# name -- rebinds the `app.services.job_manager` package attribute from the
# module to the singleton instance. `import app.services.job_manager as x`
# resolves via that (now-shadowed) attribute, not sys.modules, so it would
# hand back the instance instead of the module. Go through sys.modules
# directly to get the real module object for patching its names.
job_manager_module = sys.modules["app.services.job_manager"]

# Poll cadence the boundary tests run the extractor's reader loop at, in place
# of the production FS_POLL_INTERVAL of 3s. Mirrors test_extractor_skip.py.
_TEST_POLL = 0.03


def async_session():
    return _db.async_session()


@pytest.fixture(autouse=True)
async def _clean_db():
    await _db.init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


@pytest.fixture(autouse=True)
def _stub_matching_and_eject(monkeypatch):
    """Real _run_ripping dispatches TV matching and (optionally) ejects the
    disc after every rip. Neither is what this test is about, so both are
    stubbed the same way test_midrip_rematch.py stubs them."""

    async def _no_op_match(job_id, title_id, file_path):
        return None

    monkeypatch.setattr(job_manager._matching, "match_single_file", _no_op_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *_a, **_k: None)


async def _seed_job(staging: Path, *, native_offset: int, count: int = 4) -> tuple[int, list[int]]:
    """A TV job with *count* selected, PENDING titles.

    ``output_index = title_index + native_offset`` when ``native_offset`` is
    not None for a given row -- callers pass a plain int for the uniform-
    offset discs (0 = native == scan, -1 = the #517 divergence).  Returns the
    job id and the DiscTitle ids in title_index order.
    """
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path=str(staging),
            total_titles=count,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        title_ids = []
        for idx in range(1, count + 1):
            t = DiscTitle(
                job_id=job.id,
                title_index=idx,
                output_index=idx + native_offset,
                duration_seconds=1300,
                file_size_bytes=4096,
                state=TitleState.PENDING,
                is_selected=True,
            )
            s.add(t)
            await s.commit()
            await s.refresh(t)
            title_ids.append(t.id)
        return job.id, title_ids


class _WriteLoggingProc:
    """A makemkvcon stub that writes real output files as it 'rips', and
    records the native title number of every title it is asked to write.

    Modeled on ``_RippingProc`` / ``_rip_steps`` in test_extractor_skip.py.
    Each step sleeps, applies its filesystem effect, then returns one
    robot-mode line, so the reader loop's ``FS_POLL_INTERVAL`` check runs
    *between* steps against state already written -- giving the poll a real
    chance to land on a title boundary. ``terminate()`` stops further writes,
    mirroring a killed process.
    """

    def __init__(self, steps: list[tuple[float, object, str]]):
        self.returncode = None
        self.stdout = self
        self.stderr = None
        self.terminated = False
        self._steps = list(steps)

    def readline(self) -> str:
        if self.terminated or not self._steps:
            return ""  # EOF
        delay, effect, line = self._steps.pop(0)
        time.sleep(delay)
        if effect is not None:
            effect()  # applies the filesystem write (and, for the open step, logs it)
        return line + "\n"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _rip_steps(
    output_dir: Path,
    natives: list[int],
    write_log: list[int],
    *,
    final_size: int = 4096,
) -> list[tuple[float, object, str]]:
    """Steps for a sequential 'all' pass over *natives*, in disc order.

    Each effect is a zero-arg callable applied at the moment the step
    executes (in the extractor's worker thread), not when the step list is
    built -- so the open-step's write_log append happens only if the fake
    process actually reaches that title, proof it was asked to write it
    regardless of whether the resulting file survives to the end of the rip.
    """
    steps: list[tuple[float, object, str]] = []
    for native in natives:
        path = output_dir / f"TEST_t{native:02d}.mkv"

        def _open(p=path, n=native):
            write_log.append(n)
            p.write_bytes(b"")

        # Sleep past the poll interval before opening, so the reader loop's
        # periodic FS check lands between titles -- the only place the
        # boundary is evaluated.
        steps.append((_TEST_POLL * 3, _open, 'PRGC:5017,0,"Saving to MKV file"'))
        for quarter in (1, 2, 3, 4):
            size = final_size * quarter // 4

            def _grow(p=path, s=size):
                p.write_bytes(b"\0" * s)

            steps.append((0.005, _grow, f"PRGV:{quarter * 16384},65536,65536"))
        steps.append((0.005, None, 'PRGC:5057,0,"Analyzing seamless segments"'))
    return steps


async def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {path}")


def _make_popen(staging: Path, natives: list[int], write_log: list[int], procs: list):
    def _fake_popen(cmd, **kwargs):
        proc = _WriteLoggingProc(_rip_steps(staging, natives, write_log))
        procs.append(proc)
        return proc

    return _fake_popen


async def _titles_by_index(job_id: int) -> dict[int, DiscTitle]:
    async with async_session() as s:
        result = await s.execute(
            text("SELECT id FROM disc_titles WHERE job_id = :jid ORDER BY title_index"),
            {"jid": job_id},
        )
        ids = [row[0] for row in result.all()]
        out = {}
        for i, tid in enumerate(ids, start=1):
            t = await s.get(DiscTitle, tid)
            out[i] = t
        return out


@pytest.mark.unit
class TestRipSkipBoundaryChain:
    async def test_trailing_skip_stops_before_the_never_wanted_title(self, tmp_path):
        """Skip the last two titles while the first is being written: the pass
        finishes titles 1-2, opens-and-aborts at 3, and never asks for 4."""
        staging = tmp_path / "staging"
        staging.mkdir()
        job_id, title_ids = await _seed_job(staging, native_offset=0)

        write_log: list[int] = []
        procs: list[_WriteLoggingProc] = []
        job_manager._loop = asyncio.get_running_loop()

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch(
                "app.core.extractor.subprocess.Popen",
                side_effect=_make_popen(staging, [1, 2, 3, 4], write_log, procs),
            ),
        ):
            task = asyncio.create_task(job_manager._run_ripping(job_id))
            await _wait_for_file(staging / "TEST_t01.mkv")
            assert await job_manager.skip_rip_title(job_id, title_ids[2])  # title 3
            assert await job_manager.skip_rip_title(job_id, title_ids[3])  # title 4
            # wait_for, not a bare `await task`: it bounds a hung rip so a
            # regression cannot wedge CI, and a bare await of a name reads as
            # effect-free to static analysis (CodeQL py/ineffectual-statement).
            await asyncio.wait_for(task, timeout=60)

        assert len(procs) == 1, "the per-title fallback pass must not have run"
        assert 4 not in write_log, "the fake was asked to write the never-wanted title 4"
        assert {1, 2} <= set(write_log)

        titles = await _titles_by_index(job_id)
        assert titles[1].output_filename is not None
        assert (staging / "TEST_t01.mkv").exists()
        assert (staging / "TEST_t01.mkv").stat().st_size == 4096
        assert titles[3].state == TitleState.SKIPPED
        assert titles[4].state == TitleState.SKIPPED

    async def test_middle_skip_does_not_cost_the_rest_of_the_disc(self, tmp_path):
        """Skipping title 3 alone, with title 4 still wanted, must not abort
        the pass -- every title including 4 gets written."""
        staging = tmp_path / "staging"
        staging.mkdir()
        job_id, title_ids = await _seed_job(staging, native_offset=0)

        write_log: list[int] = []
        procs: list[_WriteLoggingProc] = []
        job_manager._loop = asyncio.get_running_loop()

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch(
                "app.core.extractor.subprocess.Popen",
                side_effect=_make_popen(staging, [1, 2, 3, 4], write_log, procs),
            ),
        ):
            task = asyncio.create_task(job_manager._run_ripping(job_id))
            await _wait_for_file(staging / "TEST_t01.mkv")
            assert await job_manager.skip_rip_title(job_id, title_ids[2])  # title 3 only
            # wait_for, not a bare `await task`: it bounds a hung rip so a
            # regression cannot wedge CI, and a bare await of a name reads as
            # effect-free to static analysis (CodeQL py/ineffectual-statement).
            await asyncio.wait_for(task, timeout=60)

        assert len(procs) == 1
        assert set(write_log) == {1, 2, 3, 4}, "a skipped middle track cost the rest of the disc"

        titles = await _titles_by_index(job_id)
        assert titles[4].output_filename is not None
        assert titles[3].state == TitleState.SKIPPED

    async def test_offset_disc_517_aborts_on_the_correct_title(self, tmp_path):
        """MakeMKV's native numbers are one behind scan order (issue #517):
        title N writes as native N-1. The boundary must still land correctly."""
        staging = tmp_path / "staging"
        staging.mkdir()
        job_id, title_ids = await _seed_job(staging, native_offset=-1)

        write_log: list[int] = []
        procs: list[_WriteLoggingProc] = []
        job_manager._loop = asyncio.get_running_loop()

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch(
                "app.core.extractor.subprocess.Popen",
                side_effect=_make_popen(staging, [0, 1, 2, 3], write_log, procs),
            ),
        ):
            task = asyncio.create_task(job_manager._run_ripping(job_id))
            await _wait_for_file(staging / "TEST_t00.mkv")
            assert await job_manager.skip_rip_title(job_id, title_ids[2])  # title 3 (native 2)
            assert await job_manager.skip_rip_title(job_id, title_ids[3])  # title 4 (native 3)
            # wait_for, not a bare `await task`: it bounds a hung rip so a
            # regression cannot wedge CI, and a bare await of a name reads as
            # effect-free to static analysis (CodeQL py/ineffectual-statement).
            await asyncio.wait_for(task, timeout=60)

        assert len(procs) == 1
        # Native 3 (scan title 4) was never opened; natives 0 and 1 (scan
        # titles 1 and 2) were fully written.
        assert 3 not in write_log
        assert {0, 1} <= set(write_log)

        titles = await _titles_by_index(job_id)
        assert titles[1].output_filename is not None
        assert titles[2].output_filename is not None
        assert titles[3].state == TitleState.SKIPPED
        assert titles[4].state == TitleState.SKIPPED

    async def test_negative_control_inert_map_writes_the_full_disc(self, tmp_path, monkeypatch):
        """Force the map inert (mirrors what commit 24687814 fixed): with no
        boundary information, the pass must run to completion even though the
        last two titles are skipped early. This is the assertion that would
        have failed before that commit -- files-on-disk alone can't tell it
        apart from the working case, so it also anchors that the write log is
        actually measuring what the docstring above claims."""
        staging = tmp_path / "staging"
        staging.mkdir()
        job_id, title_ids = await _seed_job(staging, native_offset=0)

        # Force a native-number collision so job_manager's own collision guard
        # (see the guard added alongside this test) disables disc_title_map,
        # exactly the "map absent" condition this test exists to catch.
        monkeypatch.setattr(job_manager_module, "expected_native_index", lambda t: 1)

        write_log: list[int] = []
        procs: list[_WriteLoggingProc] = []
        job_manager._loop = asyncio.get_running_loop()

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch(
                "app.core.extractor.subprocess.Popen",
                side_effect=_make_popen(staging, [1, 2, 3, 4], write_log, procs),
            ),
        ):
            task = asyncio.create_task(job_manager._run_ripping(job_id))
            await _wait_for_file(staging / "TEST_t01.mkv")
            assert await job_manager.skip_rip_title(job_id, title_ids[2])
            assert await job_manager.skip_rip_title(job_id, title_ids[3])
            # wait_for, not a bare `await task`: it bounds a hung rip so a
            # regression cannot wedge CI, and a bare await of a name reads as
            # effect-free to static analysis (CodeQL py/ineffectual-statement).
            await asyncio.wait_for(task, timeout=60)

        assert len(procs) == 1
        assert set(write_log) == {1, 2, 3, 4}, "an inert map must never abort the pass"
