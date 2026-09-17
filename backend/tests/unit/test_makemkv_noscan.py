"""Every makemkvcon invocation passes ``--noscan`` (#652).

Without it, each makemkvcon process probes the media in EVERY drive, including
one another job is already ripping. That probe blocks behind the busy drive, so
with two drives the second job's rip sits at 0.0x until the stall timeout ejects
its disc. The per-drive source locks cannot help: the contention is inside
MakeMKV, across drives that hold different locks.

A switch must precede the command verb (``makemkvcon [switches] Command
[Parameters]``), so each test also pins the flag's position.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.disc_source import NOSCAN, _run_drive_listing
from app.core.extractor import (
    MakeMKVExtractor,
    _build_backup_command,
    _build_rip_commands,
    _build_scan_command,
)


def _assert_noscan_before(cmd: list[str], verb: str) -> None:
    assert NOSCAN in cmd, f"{verb} argv is missing {NOSCAN}: {cmd}"
    assert cmd.index(NOSCAN) < cmd.index(verb), f"{NOSCAN} must precede {verb!r}: {cmd}"


def test_the_flag_is_makemkvs_spelling():
    assert NOSCAN == "--noscan"


@pytest.mark.unit
class TestNoscanOnEveryCall:
    def test_scan(self):
        cmd = _build_scan_command("mmk", "dev:E:")
        assert cmd == ["mmk", "-r", NOSCAN, "info", "dev:E:"]

    def test_rip_all_pass(self):
        [(_, cmd)] = _build_rip_commands("mmk", "dev:E:", "/out", None)
        _assert_noscan_before(cmd, "mkv")

    def test_rip_per_title(self):
        cmds = _build_rip_commands("mmk", "dev:E:", "/out", [1, 3])
        for _, cmd in cmds:
            _assert_noscan_before(cmd, "mkv")

    def test_backup(self):
        _assert_noscan_before(_build_backup_command("mmk", "disc:0", "/b/x.partial"), "backup")

    def test_drive_listing(self):
        with patch("app.core.disc_source.subprocess.run") as run:
            run.return_value = MagicMock(stdout="")
            _run_drive_listing("mmk")
        _assert_noscan_before(run.call_args.args[0], "info")

    def test_version_probe(self):
        from app.api.validation import _probe_makemkv_version

        with patch("app.api.validation.subprocess.run") as run:
            run.return_value = MagicMock(stdout="", stderr="")
            _probe_makemkv_version("C:/MakeMKV/makemkvcon64.exe")
        _assert_noscan_before(run.call_args.args[0], "info")

    @pytest.mark.asyncio
    async def test_scan_disc_runs_the_built_command(self):
        # Guards against the scan path drifting back to an inline argv that
        # bypasses _build_scan_command.
        ex = MakeMKVExtractor(makemkv_path=Path("mmk"))
        with patch("app.core.extractor.subprocess.Popen") as popen:
            proc = popen.return_value
            proc.communicate.return_value = ("", "")
            proc.returncode = 0
            await ex.scan_disc("dev:E:")
        assert popen.call_args.args[0] == _build_scan_command("mmk", "dev:E:")
