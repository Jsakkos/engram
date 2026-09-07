"""makemkvcon backup wrapper."""

import asyncio
from pathlib import Path

import pytest

from app.core import extractor as extractor_module
from app.core.extractor import (
    BackupResult,
    MakeMKVExtractor,
    _build_backup_command,
    _parse_backup_progress,
)


def _extractor() -> MakeMKVExtractor:
    return MakeMKVExtractor(makemkv_path=Path("mmk"))


@pytest.mark.unit
class TestBuildBackupCommand:
    def test_uses_the_disc_index_and_decrypt_flag(self):
        cmd = _build_backup_command("mmk", "disc:0", "/b/out.partial")
        assert cmd == [
            "mmk",
            "-r",
            "--progress=-same",
            "--decrypt",
            "backup",
            "disc:0",
            "/b/out.partial",
        ]

    def test_refuses_a_dev_source(self):
        # makemkvcon backup accepts only disc:N. Catching it here beats a
        # confusing MakeMKV error 40 minutes into a job.
        with pytest.raises(ValueError):
            _build_backup_command("mmk", "dev:E:", "/b/out.partial")

    def test_refuses_a_file_source(self):
        # Backing a backup up again is never what the caller meant.
        with pytest.raises(ValueError):
            _build_backup_command("mmk", "file:/b/x", "/b/out.partial")


@pytest.mark.unit
class TestParseBackupProgress:
    def test_reads_a_prgv_line(self):
        # PRGV:current,total,max; `total` is the OVERALL bar, `current` the
        # current sub-operation's, both on the fixed `max` scale. See the
        # verbatim-log guard in test_rip_progress.py
        # (TestMakeMKVRobotFormat::test_prgv_is_value_over_fixed_max_not_current_over_total),
        # where a real line reads PRGV:36541,6084,65536: 55.8% through the title
        # in hand, 9.3% through the disc.
        assert _parse_backup_progress("PRGV:65536,32768,65536") == pytest.approx(50.0)

    def test_the_current_operation_bar_is_ignored(self):
        # `current` sawtooths back to 0 for every sub-operation of one backup,
        # so reporting it as the backup's progress would run the bar backwards.
        assert _parse_backup_progress("PRGV:0,32768,65536") == pytest.approx(50.0)
        assert _parse_backup_progress("PRGV:65536,32768,65536") == pytest.approx(50.0)

    def test_zero_max_is_ignored(self):
        assert _parse_backup_progress("PRGV:0,0,0") is None

    def test_non_progress_line_is_ignored(self):
        assert _parse_backup_progress('MSG:1005,0,1,"Backup done"') is None

    def test_a_total_above_max_is_clamped(self):
        assert _parse_backup_progress("PRGV:0,70000,65536") == pytest.approx(100.0)


@pytest.mark.unit
class TestBackupDisc:
    @pytest.mark.asyncio
    async def test_success_renames_partial_to_final(self, tmp_path, monkeypatch):
        dest = tmp_path / "Inception (2010)"

        def fake_run(cmd, on_line, job_id):
            partial = tmp_path / "Inception (2010).partial"
            (partial / "BDMV").mkdir(parents=True)
            on_line("PRGV:65536,65536,65536")
            return 0, "PRGV:65536,65536,65536\n"

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = _extractor()
        result = await ex.backup_disc("disc:0", dest, job_id=1)

        assert result.success is True
        assert result.dest == dest
        assert dest.exists()
        assert not (tmp_path / "Inception (2010).partial").exists()

    @pytest.mark.asyncio
    async def test_failure_keeps_the_partial(self, tmp_path, monkeypatch):
        # A partial backup of a dying disc has salvage value; the user decides
        # whether to discard it, not Engram.
        dest = tmp_path / "Scratched"

        def fake_run(cmd, on_line, job_id):
            partial = tmp_path / "Scratched.partial"
            (partial / "BDMV").mkdir(parents=True)
            return 1, 'MSG:5010,0,0,"Failed to read disc"\n'

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = _extractor()
        result = await ex.backup_disc("disc:0", dest, job_id=1)

        assert result.success is False
        assert result.error_message
        assert (tmp_path / "Scratched.partial").exists()
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_failure_reports_the_makemkv_message(self, tmp_path, monkeypatch):
        def fake_run(cmd, on_line, job_id):
            (tmp_path / "S.partial").mkdir(parents=True)
            return 1, 'MSG:5010,0,0,"Failed to read disc"\n'

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", tmp_path / "S", job_id=1)

        assert result.error_message == "Failed to read disc"

    @pytest.mark.asyncio
    async def test_progress_callback_receives_percentages(self, tmp_path, monkeypatch):
        seen = []

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            on_line("PRGV:65536,16384,65536")
            on_line("PRGV:65536,65536,65536")
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = _extractor()
        await ex.backup_disc("disc:0", tmp_path / "X", progress_callback=seen.append, job_id=1)

        assert seen == [pytest.approx(25.0), pytest.approx(100.0)]

    @pytest.mark.asyncio
    async def test_a_raising_progress_callback_does_not_fail_the_backup(
        self, tmp_path, monkeypatch
    ):
        def boom(_pct):
            raise RuntimeError("callback exploded")

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            on_line("PRGV:16384,65536,65536")
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc(
            "disc:0", tmp_path / "X", progress_callback=boom, job_id=1
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_a_non_disc_source_fails_immediately(self, tmp_path, monkeypatch):
        def fake_run(cmd, on_line, job_id):  # pragma: no cover - must not run
            raise AssertionError("MakeMKV must not be launched for a dev: source")

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("E:", tmp_path / "X", job_id=1)

        assert result.success is False
        assert "disc:N" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_an_existing_destination_is_not_clobbered(self, tmp_path, monkeypatch):
        dest = tmp_path / "Already"
        (dest / "BDMV").mkdir(parents=True)
        (dest / "BDMV" / "keep.txt").write_text("original", encoding="utf-8")

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "Already.partial").mkdir(parents=True)
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", dest, job_id=1)

        assert result.success is True
        assert result.already_existed is True
        assert (dest / "BDMV" / "keep.txt").read_text(encoding="utf-8") == "original"

    @pytest.mark.asyncio
    async def test_an_uncreatable_destination_parent_fails_cleanly(self, tmp_path, monkeypatch):
        # dest.parent needs to be a directory but a plain FILE already occupies
        # that path, so mkdir(parents=True) raises OSError/NotADirectoryError on
        # both Windows and POSIX. backup_disc must report this like every other
        # failure mode instead of letting the exception escape.
        blocker = tmp_path / "blocker"
        blocker.write_text("occupying the path", encoding="utf-8")
        dest = blocker / "subdir" / "Movie"

        def fake_run(cmd, on_line, job_id):  # pragma: no cover - must not run
            raise AssertionError("MakeMKV must not be launched when the dest parent can't exist")

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", dest, job_id=1)

        assert result.success is False
        assert result.error_message

    @pytest.mark.asyncio
    async def test_the_log_is_written_next_to_the_scan_and_rip_logs(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            return 0, "PRGV:1,1,1\n"

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        await _extractor().backup_disc("disc:0", tmp_path / "X", log_dir=log_dir, job_id=1)

        assert (log_dir / "backup.log").read_text(encoding="utf-8") == "PRGV:1,1,1\n"

    @pytest.mark.asyncio
    async def test_a_cancelled_backup_reports_cancellation(self, tmp_path, monkeypatch):
        ex = _extractor()

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            # Stand in for cancel() terminating the subprocess mid-copy.
            ex.cancel(job_id)
            return 1, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await ex.backup_disc("disc:0", tmp_path / "X", job_id=7)

        assert result.success is False
        assert "cancel" in (result.error_message or "").lower()
        # The partial survives a cancel too: the user may resume or salvage it.
        assert (tmp_path / "X.partial").exists()

    @pytest.mark.asyncio
    async def test_a_stale_partial_is_moved_aside_before_the_run(self, tmp_path, monkeypatch):
        # Debris from a previous failed attempt at the same disc must not sit
        # in MakeMKV's way: its behaviour against a non-empty target is
        # unspecified.
        stale = tmp_path / "X.partial"
        (stale / "BDMV").mkdir(parents=True)
        (stale / "BDMV" / "old.m2ts").write_text("stale bytes", encoding="utf-8")

        seen_partial_at_launch = []

        def fake_run(cmd, on_line, job_id):
            seen_partial_at_launch.append((tmp_path / "X.partial").exists())
            (tmp_path / "X.partial").mkdir(parents=True)
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", tmp_path / "X", job_id=1)

        assert seen_partial_at_launch == [False]
        assert result.success is True
        previous = tmp_path / "X.partial.previous"
        assert (previous / "BDMV" / "old.m2ts").read_text(encoding="utf-8") == "stale bytes"

    @pytest.mark.asyncio
    async def test_an_existing_partial_previous_is_replaced_by_the_newer_stale_partial(
        self, tmp_path, monkeypatch
    ):
        # At most one stale partial is preserved, so a disc that fails
        # repeatedly does not accumulate unbounded debris.
        older_previous = tmp_path / "X.partial.previous"
        (older_previous / "BDMV").mkdir(parents=True)
        (older_previous / "BDMV" / "ancient.m2ts").write_text("ancient", encoding="utf-8")

        newer_stale = tmp_path / "X.partial"
        (newer_stale / "BDMV").mkdir(parents=True)
        (newer_stale / "BDMV" / "recent.m2ts").write_text("recent", encoding="utf-8")

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", tmp_path / "X", job_id=1)

        assert result.success is True
        previous = tmp_path / "X.partial.previous"
        assert (previous / "BDMV" / "recent.m2ts").read_text(encoding="utf-8") == "recent"
        assert not (previous / "BDMV" / "ancient.m2ts").exists()

    @pytest.mark.asyncio
    async def test_no_stale_partial_leaves_partial_previous_untouched(self, tmp_path, monkeypatch):
        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        result = await _extractor().backup_disc("disc:0", tmp_path / "X", job_id=1)

        assert result.success is True
        assert not (tmp_path / "X.partial.previous").exists()


class _FakeProc:
    """Minimal subprocess.Popen stand-in for the real _run_backup_process."""

    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0
        self.pid = 4242
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


@pytest.mark.unit
class TestRunBackupProcessRegistration:
    def test_the_process_is_registered_for_cancel_and_popped_after(self, monkeypatch):
        ex = _extractor()
        seen_registered = []
        proc = _FakeProc(["PRGV:1,2,4\n"])
        monkeypatch.setattr(extractor_module.subprocess, "Popen", lambda *a, **k: proc)

        def on_line(_line):
            # cancel() reaches the live process only if it was registered.
            seen_registered.append(ex._processes.get(9))

        returncode, output = ex._run_backup_process(["mmk"], on_line, 9)

        assert seen_registered == [proc]
        assert returncode == 0
        assert output == "PRGV:1,2,4\n"
        # Registry cleared so a later cancel cannot hit a dead process.
        assert 9 not in ex._processes

    def test_cancel_terminates_the_backup_process(self, monkeypatch):
        ex = _extractor()
        proc = _FakeProc(["PRGV:1,2,4\n", "PRGV:2,3,4\n"])
        monkeypatch.setattr(extractor_module.subprocess, "Popen", lambda *a, **k: proc)

        def on_line(_line):
            ex.cancel(9)

        returncode, _output = ex._run_backup_process(["mmk"], on_line, 9)

        assert proc.terminated is True
        assert returncode != 0


@pytest.mark.unit
class TestBackupResult:
    def test_defaults(self):
        r = BackupResult(success=True)
        assert r.dest is None
        assert r.error_message is None
        assert r.already_existed is False


class TestDestinationLocking:
    """Two drives backing up the same disc must not race on one .partial.

    A backup destination comes from content identity with nothing drive-specific
    in it, so two jobs in two drives compute the SAME destination while taking
    DIFFERENT source locks. The loser's stale-partial recovery cannot tell
    debris from a live copy, and would rename a running makemkvcon's output away.
    """

    def test_the_same_destination_shares_one_lock(self, tmp_path):
        ex = _extractor()
        assert ex._get_dest_lock(tmp_path / "X") is ex._get_dest_lock(tmp_path / "X")

    def test_different_destinations_do_not_contend(self, tmp_path):
        ex = _extractor()
        assert ex._get_dest_lock(tmp_path / "X") is not ex._get_dest_lock(tmp_path / "Y")

    def test_a_destination_lock_is_not_a_source_lock(self, tmp_path):
        # Distinct namespaces: a backup whose destination happens to sit on
        # drive E: must not contend with the optical drive at E:.
        ex = _extractor()
        assert ex._get_dest_lock(tmp_path / "X") is not ex._get_source_lock("E:")

    @pytest.mark.asyncio
    async def test_a_second_job_waits_for_the_destination(self, tmp_path, monkeypatch):
        dest = tmp_path / "Inception (2010)"
        started = []

        def fake_run(self_or_cmd, *args, **kwargs):
            started.append(dest)
            (tmp_path / "Inception (2010).partial").mkdir(parents=True, exist_ok=True)
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", fake_run)
        ex = _extractor()
        # Hold the destination lock; the backup must not proceed past it.
        held = ex._get_dest_lock(dest)
        await held.acquire()
        try:
            task = asyncio.create_task(ex.backup_disc("disc:0", dest, job_id=1))
            await asyncio.sleep(0.05)
            assert started == [], "backup ran while another job held the destination"
        finally:
            held.release()
        assert await asyncio.wait_for(task, timeout=5)
