import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.extractor import (
    MakeMKVExtractor,
    RipResult,
    TitleCompletionDetector,
    _build_rip_commands,
    should_abort_all_pass,
)


def test_skip_set_registration_and_clear():
    ext = MakeMKVExtractor()
    ext.skip_title_index(5, 3)
    ext.skip_title_index(5, 7)
    assert ext._skipped_indices[5] == {3, 7}

    ext.unskip_title_index(5, 3)
    assert ext._skipped_indices[5] == {7}

    # Unknown job / index is a no-op, never raises.
    ext.unskip_title_index(999, 1)
    ext.unskip_title_index(5, 999)
    assert ext._skipped_indices[5] == {7}


def test_build_rip_commands_all_selected_uses_all_pass():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", None)
    assert len(cmds) == 1
    title_index, cmd = cmds[0]
    assert title_index is None  # "all" pass has no single title index
    assert cmd[-1] == "/out"
    assert "all" in cmd


def test_build_rip_commands_subset_is_per_title_with_indices():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", [2, 4])
    assert [ti for ti, _ in cmds] == [2, 4]
    assert all(str(ti) in cmd for ti, cmd in cmds)


class _FakeStdout:
    """makemkvcon stdout: emits a few robot-mode lines, then EOF (no blocking)."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0) + "\n"
        return ""  # EOF — a clean natural finish


class _FakeProc:
    """A makemkvcon stub that finishes cleanly unless terminated first."""

    def __init__(self, lines: list[str]):
        self.returncode = None
        self.stdout = _FakeStdout(lines)
        self.stderr = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0  # natural completion
        return self.returncode


def test_ripresult_aborted_for_skip_defaults_false():
    assert RipResult(success=True, output_files=[]).aborted_for_skip is False


@pytest.mark.unit
class TestFilesIncompleteAtAbort:
    """On a killed 'all' pass, delete only the title being written; keep the
    ones that already finished — even those whose completion never fired because
    no successor started growing before the kill (the bystander-title bug)."""

    def test_preserves_finished_title_deletes_the_growing_one(self):
        det = TitleCompletionDetector()
        # t00 finished (polled twice at a stable 1000); t01 grew from 500 while
        # it was the title being written. Neither is promoted to _completed yet
        # (t00's "successor grew" gate never fired — exactly the abort gap).
        det.poll({"t00.mkv": 1000})
        det.poll({"t00.mkv": 1000, "t01.mkv": 500})
        assert det.is_completed("t00.mkv") is False

        # Abort snapshot: t00 unchanged (finished), t01 grew further (partial).
        doomed = det.files_incomplete_at_abort({"t00.mkv": 1000, "t01.mkv": 800})

        assert set(doomed) == {"t01.mkv"}

    def test_preserves_stable_last_title_when_nothing_is_growing(self):
        # The reviewer's gap: t00 finished, its successor never started. Nothing
        # is being written, so nothing should be deleted.
        det = TitleCompletionDetector()
        det.poll({"t00.mkv": 1000})
        det.poll({"t00.mkv": 1000})

        assert det.files_incomplete_at_abort({"t00.mkv": 1000}) == []

    def test_deletes_zero_byte_and_never_polled_stub(self):
        det = TitleCompletionDetector()
        det.poll({"t00.mkv": 1000})
        # t00 stub truncated to 0 bytes; t09 appeared but was never polled.
        doomed = det.files_incomplete_at_abort({"t00.mkv": 0, "t09.mkv": 4096})
        assert set(doomed) == {"t00.mkv", "t09.mkv"}

    def test_completed_and_ignored_files_are_always_preserved(self):
        det = TitleCompletionDetector(ignore={"pre_existing.mkv"})
        det.poll({"t00.mkv": 1000})
        det.poll({"t00.mkv": 1000, "t01.mkv": 500})
        # force-complete t00 so it is in _completed.
        det.poll({"t00.mkv": 1000}, force=True)
        assert det.is_completed("t00.mkv") is True

        doomed = det.files_incomplete_at_abort(
            {"t00.mkv": 1000, "pre_existing.mkv": 2000, "t01.mkv": 9999}
        )
        assert set(doomed) == {"t01.mkv"}  # completed + ignored preserved


@pytest.mark.unit
class TestShouldAbortAllPass:
    """A full-disc 'all' pass may only be interrupted when stopping costs
    nothing: MakeMKV just opened a title the user skipped, and every title
    still ahead of it is skipped too."""

    def test_no_abort_when_the_opened_title_is_wanted(self):
        # native -> scan index. User skipped scan index 3; MakeMKV opened 2.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {3}) is False

    def test_no_abort_when_a_wanted_title_still_lies_ahead(self):
        # The opened title IS skipped, but title 3 is still wanted. Killing the
        # process here would cost a full disc re-open to rip title 3 alone, and
        # would throw away whatever title 2 had written.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {2}) is False

    def test_aborts_when_the_opened_title_and_every_later_one_are_skipped(self):
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {2, 3}) is True

    def test_aborts_on_the_last_title_when_it_is_skipped(self):
        # The simplest instance of the rule, and the one the real caller hits
        # when the user skips the final track: nothing is ahead, so `remaining`
        # is empty and the subset check is vacuous.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(3, m, {3}) is True

    def test_an_earlier_wanted_title_does_not_block_the_abort(self):
        # Title 1 is wanted but already behind us -- it has been ripped, or
        # MakeMKV's selection profile filtered it out. Either way it must not
        # suppress an abort at title 2. This is why the rule is ordering-based
        # rather than seen-set based.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {2, 3}) is True

    def test_never_aborts_without_a_disc_title_map(self):
        # No map means the caller could not tell us what is on the disc, so we
        # cannot know whether a wanted title lies ahead. Fail safe: keep ripping.
        assert should_abort_all_pass(2, None, {2}) is False
        assert should_abort_all_pass(2, {}, {2}) is False

    def test_maps_native_numbering_to_the_scan_index_the_skip_set_uses(self):
        # Issue #517: MakeMKV's native title numbers can diverge from scan
        # order. The skip set is keyed on DiscTitle.title_index (scan order),
        # while the opened title is parsed from the output FILENAME (native).
        m = {0: 1, 1: 2, 2: 3}  # native -> scan
        # Skipped scan indices {2, 3}; MakeMKV opened native 1 (== scan 2).
        assert should_abort_all_pass(1, m, {2, 3}) is True
        # Same disc, only scan 2 skipped -> scan 3 is still wanted.
        assert should_abort_all_pass(1, m, {2}) is False

    def test_unknown_native_number_never_aborts(self):
        # A filename whose _tNN does not correspond to any scanned title.
        m = {1: 1, 2: 2}
        assert should_abort_all_pass(97, m, {1, 2}) is False


# Poll cadence the boundary tests run the reader loop at, in place of the
# production FS_POLL_INTERVAL of 3 s.
_TEST_POLL = 0.05


class _RippingProc:
    """A makemkvcon stub that writes real output files as it 'rips'.

    Each step sleeps, applies its filesystem effect, then returns one robot-mode
    line -- so the reader loop's ``FS_POLL_INTERVAL`` check runs *between* steps,
    against state the step has already written. Giving the step that OPENS a
    title a sleep longer than the poll interval therefore guarantees the poll
    lands while that title is still a zero-byte stub and its predecessor is at
    its final size: exactly the title boundary the production code detects.

    Once ``terminate()`` lands, stdout hits EOF and nothing further is written,
    mirroring a killed process.
    """

    def __init__(self, steps: list[tuple[float, tuple[Path, int] | None, str]]):
        self.returncode = None
        self.stdout = self
        self.stderr = None
        self.terminated = False
        self._steps = list(steps)

    # --- stdout half ---
    def readline(self) -> str:
        if self.terminated or not self._steps:
            return ""  # EOF
        delay, effect, line = self._steps.pop(0)
        time.sleep(delay)
        if effect is not None:
            path, size = effect
            path.write_bytes(b"\0" * size)
        return line + "\n"

    # --- process half ---
    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0  # natural completion
        return self.returncode


def _rip_steps(
    output_dir: Path, natives: list[int], *, final_size: int = 4096, open_size: int = 0
) -> list[tuple[float, tuple[Path, int] | None, str]]:
    """Steps for a sequential 'all' pass over *natives*, in disc order.

    Lines are the ones real MakeMKV rip logs actually contain (PRGC/PRGV);
    notably there is no "file created" line, because no real log has one.

    ``open_size`` is how many bytes the file already holds when the poll lands
    on it. Zero is the clean boundary. A non-zero value models MakeMKV having
    flushed part of the title before the poll -- the case where "grew since the
    last poll" can no longer recognize the file as unfinished.
    """
    steps: list[tuple[float, tuple[Path, int] | None, str]] = []
    for native in natives:
        path = output_dir / f"TEST_t{native:02d}.mkv"
        # Opening the next title IS the boundary. Sleep past the poll interval
        # first so the reader loop polls before any further bytes are written.
        steps.append((_TEST_POLL * 3, (path, open_size), 'PRGC:5017,0,"Saving to MKV file"'))
        for quarter in (1, 2, 3, 4):
            size = max(open_size, final_size * quarter // 4)
            steps.append((0.005, (path, size), f"PRGV:{quarter * 16384},65536,65536"))
        steps.append((0.005, None, 'PRGC:5057,0,"Analyzing seamless segments"'))
    return steps


@pytest.mark.unit
class TestAllPassSkipAbort:
    """A skip during a full-disc 'all' pass is honored at TITLE BOUNDARIES only:
    a new ``*_tNN.mkv`` appears in the output dir, and the pass stops there when
    that title and every later one are skipped. A skip that lands mid-title must
    never interrupt the title being written."""

    async def test_does_not_abort_mid_title_when_a_later_track_is_skipped(self, tmp_path):
        # The exact user-reported regression: while title 1 is being written the
        # user skips title 2, which has not been ripped yet. The pass MUST run
        # on -- title 1's rip is not thrown away, and title 2 is dropped later by
        # the caller once MakeMKV finishes writing it. The old rule terminated
        # makemkvcon on the very next stdout line.
        procs: list[_RippingProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _RippingProc(_rip_steps(tmp_path, [1, 2, 3]))
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(21, 2)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,  # full-disc 'all' pass
                stall_timeout=0,
                job_id=21,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is False
        assert procs and procs[0].terminated is False
        # Every title on the disc was written, including the skipped one.
        assert {p.name for p in tmp_path.glob("*.mkv")} == {
            "TEST_t01.mkv",
            "TEST_t02.mkv",
            "TEST_t03.mkv",
        }

    async def test_aborts_at_the_boundary_when_every_remaining_title_is_skipped(self, tmp_path):
        # Title 1 finished; MakeMKV opens title 2. Both 2 and 3 are skipped, so
        # no wanted title is left and stopping costs nothing.
        procs: list[_RippingProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _RippingProc(_rip_steps(tmp_path, [1, 2, 3]))
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(22, 2)
        ex.skip_title_index(22, 3)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=22,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is True
        assert result.success is True
        assert procs and procs[0].terminated is True
        # Title 3 was never opened -- the pass stopped one title early.
        assert not (tmp_path / "TEST_t03.mkv").exists()

    async def test_boundary_abort_preserves_the_finished_title_and_deletes_the_stub(self, tmp_path):
        # The point of evaluating the boundary immediately after a completion
        # poll. The abort deletes "anything that grew since the last poll", so a
        # stale reading would condemn title 1 -- which had just finished -- along
        # with title 2's stub. Both halves are asserted here: the finished title
        # survives intact, the stub is gone.
        def _fake_popen(cmd, **kwargs):
            return _RippingProc(_rip_steps(tmp_path, [1, 2, 3], final_size=8192))

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(23, 2)
        ex.skip_title_index(23, 3)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=23,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is True
        finished = tmp_path / "TEST_t01.mkv"
        assert finished.exists(), "the title that finished before the abort was deleted"
        assert finished.stat().st_size == 8192, "the finished title was truncated"
        assert not (tmp_path / "TEST_t02.mkv").exists(), "the just-opened stub was kept"

    async def test_boundary_abort_deletes_a_stub_that_had_already_written_bytes(self, tmp_path):
        # The terminate -> wait window. files_incomplete_at_abort condemns a file
        # only when it GREW since the last poll (or is zero-byte, or was never
        # polled). A stub that MakeMKV had already flushed bytes into, and that
        # does not grow between the boundary poll and the post-kill snapshot,
        # reads as `size == prev > 0` -- i.e. finished -- and would be preserved,
        # then force-promoted and handed to matching as a truncated MKV for a
        # track the user explicitly skipped. The abort must therefore delete the
        # stub it stopped on by NAME, not by inference.
        def _fake_popen(cmd, **kwargs):
            return _RippingProc(_rip_steps(tmp_path, [1, 2, 3], final_size=8192, open_size=2048))

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(25, 2)
        ex.skip_title_index(25, 3)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=25,
                disc_title_map={1: 1, 2: 2, 3: 3},
            )

        assert result.aborted_for_skip is True
        stub = tmp_path / "TEST_t02.mkv"
        # Guard the premise: if the stub were zero-byte or still growing, the
        # size-based rule would have caught it and this test would prove nothing.
        assert not stub.exists(), "a partially-written stub for a skipped track survived"
        assert result.output_files == [tmp_path / "TEST_t01.mkv"]
        assert (tmp_path / "TEST_t01.mkv").stat().st_size == 8192

    async def test_pre_existing_staging_output_does_not_trigger_a_spurious_abort(self, tmp_path):
        # A populated staging dir must not read as "MakeMKV just opened these".
        # Without seeding seen_native, the first boundary poll sees last run's
        # TEST_t09.mkv as brand new, takes it as the highest native, finds scan
        # index 9 skipped with nothing above it -- and kills the pass while
        # title 1 is mid-write. That is the very failure class this change fixes.
        (tmp_path / "TEST_t09.mkv").write_bytes(b"\0" * 16384)

        procs: list[_RippingProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _RippingProc(_rip_steps(tmp_path, [1, 2]))
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(26, 9)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=26,
                disc_title_map={1: 1, 2: 2, 9: 9},
            )

        assert result.aborted_for_skip is False
        assert procs and procs[0].terminated is False
        assert (tmp_path / "TEST_t01.mkv").exists()
        assert (tmp_path / "TEST_t02.mkv").exists()

    async def test_no_abort_without_a_disc_title_map(self, tmp_path):
        # Fail-safe: an all-pass whose caller did not supply the disc contents
        # can never conclude that nothing is left, so it always runs to the end.
        procs: list[_RippingProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _RippingProc(_rip_steps(tmp_path, [1, 2, 3]))
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(24, 2)
        ex.skip_title_index(24, 3)

        with (
            patch("app.core.extractor.FS_POLL_INTERVAL", _TEST_POLL),
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
        ):
            result = await ex.rip_titles(
                "/dev/sr0", tmp_path, title_indices=None, stall_timeout=0, job_id=24
            )

        assert result.aborted_for_skip is False
        assert procs and procs[0].terminated is False

    async def test_per_title_pass_is_not_aborted_by_skip_set(self, tmp_path):
        # In per-title mode the loop already drops skipped indices command-by-
        # command, so it must NOT take the all-pass boundary path.
        def _fake_popen(cmd, **kwargs):
            return _FakeProc(["PRGV:1,2,65536"])

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(13, 4)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[4, 6],  # per-title mode
                stall_timeout=0,
                job_id=13,
            )

        assert result.aborted_for_skip is False
