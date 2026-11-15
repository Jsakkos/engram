"""A crashed makemkvcon scan is named, and its output is kept (#901).

A user's backup import failed with "No titles found on disc" while the log said
only ``MakeMKV scan failed after 8.2s (exit code 3221225477):`` -- 3221225477 is
0xC0000005, a Windows access violation inside makemkvcon. The scan output was
discarded (``scan.log`` is written on success only and the error line printed
stderr, which robot mode leaves empty), so the diagnostics bundle had nothing.

The robot-mode fixtures below are shaped like real makemkvcon 1.18.3 output
(captured locally against a backup folder); the 2.0.0 start line follows the
same ``MSG:1005`` format with the version the reporter's bundle showed.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.services.identification_coordinator as idc
from app.core.disc_source import DiscSource
from app.core.errors import MakeMKVError, MakeMKVScanCrashError
from app.core.extractor import (
    MakeMKVExtractor,
    _build_scan_command,
    describe_crash_exit,
    makemkv_version_from_output,
)
from app.models import JobState
from tests.unit.test_identify_rip_first_gates import (
    _bare_coord,
    _make_analysis,
    _reload_job,
    _seed_identifying_job,
    gate_env,  # noqa: F401 (pytest fixture)
)

BACKUP = r"C:\mkv\Engram Imports\DISC_1"

V2_START_THEN_CRASH = (
    'MSG:1005,0,1,"MakeMKV v2.0.0 win(x64-release) started","%1 started",'
    '"MakeMKV v2.0.0 win(x64-release)"\n'
    'MSG:3006,16908288,1,"Opening files on harddrive at file://C:/mkv/Engram Imports/DISC_1",'
    '"Opening files on harddrive at %1","file://C:/mkv/Engram Imports/DISC_1"\n'
)
V1_START_THEN_CRASH = V2_START_THEN_CRASH.replace("v2.0.0", "v1.18.3")
ORDINARY_FAILURE = (
    'MSG:1005,0,1,"MakeMKV v1.18.3 win(x64-release) started","%1 started","x"\n'
    'MSG:5010,0,0,"Failed to open disc","Failed to open disc"\n'
)

ACCESS_VIOLATION = 3221225477  # 0xC0000005


class TestDescribeCrashExit:
    def test_windows_access_violation(self):
        assert describe_crash_exit(ACCESS_VIOLATION) == "access violation, 0xC0000005"

    def test_other_ntstatus_is_still_a_crash(self):
        # 0xC0000409 STATUS_STACK_BUFFER_OVERRUN: a crash we have no name for.
        assert describe_crash_exit(0xC0000409) == "Windows exception 0xC0000409"

    def test_signed_ntstatus_form(self):
        # Some Windows APIs surface the same NTSTATUS as a negative int32.
        assert describe_crash_exit(ACCESS_VIOLATION - 2**32) == "access violation, 0xC0000005"

    def test_posix_signal(self):
        # subprocess reports death-by-signal as -signum; SIGSEGV is 11.
        assert describe_crash_exit(-11) == "killed by signal 11 (SIGSEGV)"

    def test_shell_style_signal_exit(self):
        # A wrapper (e.g. a Flatpak launcher script) re-exits with 128 + signum.
        assert describe_crash_exit(139) == "killed by signal 11 (SIGSEGV)"

    @pytest.mark.parametrize(
        "code",
        [
            0,
            1,
            2,
            255,
            0xFFFFFFFF,  # exit(-1) on Windows: an ordinary error exit
            -1,
            0xC000013A,  # STATUS_CONTROL_C_EXIT: interrupted, not crashed
            -15,  # SIGTERM: Engram's own cancel, or a user kill
            -9,  # SIGKILL
            -2,  # SIGINT
            137,  # 128 + SIGKILL
        ],
    )
    def test_ordinary_exits_and_kills_are_not_crashes(self, code):
        assert describe_crash_exit(code) is None


def test_version_parsed_from_start_line():
    assert makemkv_version_from_output(V2_START_THEN_CRASH) == "2.0.0"
    assert makemkv_version_from_output("TCOUNT:0\n") is None


class TestBackupScanArgv:
    """MakeMKV opens a backup as ``file:<folder>``. Captured locally, 1.18.3
    resolves ``<folder>``, ``<folder>\\``, ``<folder>\\BDMV`` and
    ``<folder>\\BDMV\\index.bdmv`` to the same backup root, so the argv form is
    not what crashed #901; this pins the form Engram sends regardless."""

    def test_backup_source_is_the_folder_itself(self):
        cmd = _build_scan_command(
            "makemkvcon64.exe", DiscSource.for_backup(BACKUP).makemkv_arg, min_length=120
        )
        assert cmd == [
            "makemkvcon64.exe",
            "-r",
            "--noscan",
            "--minlength=120",
            "info",
            f"file:{BACKUP}",
        ]

    def test_import_spec_round_trips(self):
        # The import route stores f"file:{image.path}"; parse must not mangle the
        # drive-letter colon or add separators.
        assert DiscSource.parse(f"file:{BACKUP}").makemkv_arg == f"file:{BACKUP}"


def _scan(stdout: str, returncode: int, source, log_dir: Path | None = None):
    ext = MakeMKVExtractor(Path("makemkvcon64.exe"), min_title_length=120)
    with patch("app.core.extractor.subprocess.Popen") as popen:
        popen.return_value.communicate.return_value = (stdout, "")
        popen.return_value.returncode = returncode
        return asyncio.run(ext.scan_disc(source, log_dir=log_dir))


class TestScanCrash:
    def test_crash_raises_named_error(self):
        with pytest.raises(MakeMKVScanCrashError) as exc:
            _scan(V1_START_THEN_CRASH, ACCESS_VIOLATION, DiscSource.for_backup(BACKUP))
        message = str(exc.value)
        assert "MakeMKV crashed" in message
        assert f"file:{BACKUP}" in message
        assert "0xC0000005" in message
        assert "—" not in message

    def test_crash_error_is_a_makemkv_error(self):
        assert issubclass(MakeMKVScanCrashError, MakeMKVError)

    def test_v2_backup_crash_names_the_known_upstream_bug(self):
        with pytest.raises(MakeMKVScanCrashError) as exc:
            _scan(V2_START_THEN_CRASH, ACCESS_VIOLATION, DiscSource.for_backup(BACKUP))
        message = str(exc.value)
        assert "MakeMKV 2.0.0" in message
        assert "1.18" in message

    def test_other_versions_get_no_upstream_hint(self):
        later = V2_START_THEN_CRASH.replace("v2.0.0", "v2.0.1")
        with pytest.raises(MakeMKVScanCrashError) as exc:
            _scan(later, ACCESS_VIOLATION, DiscSource.for_backup(BACKUP))
        assert "1.18" not in str(exc.value)

    def test_crash_before_start_line_gets_the_generic_message(self):
        # An immediate crash may print nothing, so no version is known.
        with pytest.raises(MakeMKVScanCrashError) as exc:
            _scan("", ACCESS_VIOLATION, DiscSource.for_backup(BACKUP))
        assert "1.18" not in str(exc.value)
        assert "scan.log" in str(exc.value)

    def test_cancelled_scan_is_not_reported_as_a_crash(self):
        # Even if the platform reports the stop as an exception code, a scan
        # Engram itself stopped must not be called a MakeMKV crash.
        ext = MakeMKVExtractor(Path("makemkvcon64.exe"), min_title_length=120)

        def communicate(*a, **k):
            ext._cancelled_jobs.add(7)
            return (V2_START_THEN_CRASH, "")

        with patch("app.core.extractor.subprocess.Popen") as popen:
            popen.return_value.communicate.side_effect = communicate
            popen.return_value.returncode = ACCESS_VIOLATION
            assert asyncio.run(ext.scan_disc("E:", job_id=7)) == ([], "")

    def test_drive_crash_gets_no_backup_hint(self):
        with pytest.raises(MakeMKVScanCrashError) as exc:
            _scan(V2_START_THEN_CRASH, ACCESS_VIOLATION, "E:")
        assert "backup" not in str(exc.value).lower()

    def test_crash_output_lands_in_scan_log(self, tmp_path):
        with pytest.raises(MakeMKVScanCrashError):
            _scan(V2_START_THEN_CRASH, ACCESS_VIOLATION, DiscSource.for_backup(BACKUP), tmp_path)
        log = (tmp_path / "scan.log").read_text(encoding="utf-8")
        assert "MakeMKV v2.0.0" in log
        assert "Opening files on harddrive" in log
        assert str(ACCESS_VIOLATION) in log
        assert "--minlength=120" in log  # the argv, so a reader can rerun it

    def test_ordinary_failure_still_returns_empty_but_keeps_the_log(self, tmp_path):
        assert _scan(ORDINARY_FAILURE, 1, "E:", tmp_path) == ([], "")
        assert "Failed to open disc" in (tmp_path / "scan.log").read_text(encoding="utf-8")

    def test_failure_log_line_carries_stdout(self, caplog):
        # stderr is empty in robot mode; the old line ended at a bare colon.
        with caplog.at_level("ERROR", logger="app.core.extractor"):
            _scan(ORDINARY_FAILURE, 1, "E:")
        assert any("Failed to open disc" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
async def test_identify_fails_with_the_crash_message(gate_env):  # noqa: F811
    job_id = await _seed_identifying_job("DISC_1")
    coord = _bare_coord(_make_analysis(None, None), [], "DISC_1")
    crash = MakeMKVScanCrashError("MakeMKV crashed while scanning file:X (access violation)")
    coord._extractor.scan_disc = AsyncMock(side_effect=crash)

    await coord.identify_disc(job_id)

    job = await _reload_job(job_id)
    assert job.state == JobState.FAILED
    assert job.error_message == str(crash)
    assert idc  # module imported for gate_env patching
