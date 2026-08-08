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
    nothing: MakeMKV just opened a title the user skipped, and every title it
    has not opened yet is skipped too."""

    def test_no_abort_when_the_opened_title_is_wanted(self):
        # native -> scan index. User skipped scan index 3; MakeMKV opened 2.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {3}) is False

    def test_no_abort_when_a_wanted_title_still_lies_ahead(self):
        # The opened title IS skipped, but title 3 is still wanted. Killing the
        # process here would cost a full disc re-open to rip title 3 alone, and
        # would throw away whatever title 2 had written.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {2}) is False

    def test_aborts_when_the_opened_title_and_every_later_one_are_skipped(self):
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(2, m, {1, 2}, {2, 3}) is True

    def test_aborts_on_the_last_title_when_it_is_skipped(self):
        # The simplest instance of the rule, and the one the real caller hits
        # when the user skips the final track: MakeMKV opened the last title on
        # the disc, so `remaining` is empty and the subset check is vacuous.
        m = {1: 1, 2: 2, 3: 3}
        assert should_abort_all_pass(3, m, {1, 2, 3}, {3}) is True

    def test_never_aborts_without_a_disc_title_map(self):
        # No map means the caller could not tell us what is on the disc, so we
        # cannot know whether a wanted title lies ahead. Fail safe: keep ripping.
        assert should_abort_all_pass(2, None, {2}, {2}) is False
        assert should_abort_all_pass(2, {}, {2}, {2}) is False

    def test_maps_native_numbering_to_the_scan_index_the_skip_set_uses(self):
        # Issue #517: MakeMKV's native title numbers can diverge from scan
        # order. The skip set is keyed on DiscTitle.title_index (scan order),
        # while the opened title is parsed from the output FILENAME (native).
        m = {0: 1, 1: 2, 2: 3}  # native -> scan
        # Skipped scan indices {2, 3}; MakeMKV opened native 1 (== scan 2).
        assert should_abort_all_pass(1, m, {0, 1}, {2, 3}) is True
        # Same disc, only scan 2 skipped -> scan 3 is still wanted.
        assert should_abort_all_pass(1, m, {0, 1}, {2}) is False

    def test_unknown_native_number_never_aborts(self):
        # A filename whose _tNN does not correspond to any scanned title.
        m = {1: 1, 2: 2}
        assert should_abort_all_pass(97, m, {1, 97}, {1, 2}) is False


@pytest.mark.unit
class TestAllPassSkipAbort:
    """A full-disc 'all' pass cannot drop one title from a running makemkvcon,
    so a skip requested during the pass must abort it — the caller then re-rips
    the remaining not-skipped titles individually (issue #538)."""

    async def test_all_pass_aborts_when_a_title_is_skipped(self, tmp_path):
        procs: list[_FakeProc] = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc(["PRGV:1,2,65536", "PRGV:3,4,65536", "PRGV:5,6,65536"])
            procs.append(proc)
            return proc

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        # User skipped a title while the single 'all' pass is running.
        ex.skip_title_index(11, 2)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,  # full-disc 'all' pass
                stall_timeout=0,  # no stall watchdog for this test
                job_id=11,
            )

        assert result.aborted_for_skip is True
        # The pass is aborted, not failed — already-ripped titles are kept and
        # the caller re-rips the rest per-title.
        assert result.success is True
        assert procs and procs[0].terminated is True

    async def test_all_pass_abort_deletes_incomplete_output(self, tmp_path):
        # A partial file the interrupted title was mid-write on must be removed
        # so it is never handed to matching (it is re-ripped per-title instead).
        partial = tmp_path / "title_t00.mkv"
        partial.write_bytes(b"x" * 4096)

        def _fake_popen(cmd, **kwargs):
            return _FakeProc(["PRGV:1,2,65536", "PRGV:3,4,65536"])

        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
        ex.skip_title_index(12, 5)

        with patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=None,
                stall_timeout=0,
                job_id=12,
            )

        assert result.aborted_for_skip is True
        assert not partial.exists()

    async def test_per_title_pass_is_not_aborted_by_skip_set(self, tmp_path):
        # In per-title mode the loop already drops skipped indices command-by-
        # command, so it must NOT take the all-pass abort path.
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
