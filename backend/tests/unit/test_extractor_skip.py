from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.extractor import MakeMKVExtractor, RipResult, _build_rip_commands


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
