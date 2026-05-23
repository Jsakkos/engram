"""Tests for MakeMKV subprocess lifecycle: terminate/kill escalation and shutdown drain.

Guards against orphaned makemkvcon processes — the #1 documented operational
hazard. No real subprocesses are spawned; processes are MagicMock stand-ins.
"""

import subprocess
from unittest.mock import MagicMock

from app.core.extractor import MakeMKVExtractor, _terminate_proc


def _fake_proc(pid: int = 1234) -> MagicMock:
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = pid
    proc.wait.return_value = 0
    return proc


class TestTerminateProc:
    def test_graceful_exit_does_not_kill(self):
        proc = _fake_proc()
        _terminate_proc(proc, timeout=0.01)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        proc.kill.assert_not_called()

    def test_escalates_to_kill_on_timeout(self):
        proc = _fake_proc()
        # First wait (after terminate) times out; second wait (after kill) returns.
        proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="makemkvcon", timeout=0.01), 0]
        _terminate_proc(proc, timeout=0.01)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        assert proc.wait.call_count == 2

    def test_already_dead_process_is_swallowed(self):
        proc = _fake_proc()
        proc.terminate.side_effect = ProcessLookupError()
        _terminate_proc(proc, timeout=0.01)  # must not raise
        proc.kill.assert_not_called()


class TestExtractorShutdown:
    async def test_drains_all_processes_with_escalation(self):
        extractor = MakeMKVExtractor()
        graceful = _fake_proc(pid=1)
        stubborn = _fake_proc(pid=2)
        stubborn.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=0.01), 0]
        extractor._processes = {1: graceful, 2: stubborn}

        await extractor.shutdown(grace=0.01)

        graceful.terminate.assert_called_once()
        graceful.kill.assert_not_called()
        stubborn.terminate.assert_called_once()
        stubborn.kill.assert_called_once()
        assert extractor._processes == {}
        assert extractor._cancelled_jobs == set()

    async def test_no_processes_is_noop(self):
        extractor = MakeMKVExtractor()
        await extractor.shutdown(grace=0.01)  # must not raise
        assert extractor._processes == {}


class TestCancelIsNonBlocking:
    def test_cancel_flags_and_terminates_without_waiting(self):
        extractor = MakeMKVExtractor()
        proc = _fake_proc()
        extractor._processes = {7: proc}
        extractor.cancel(7)
        assert 7 in extractor._cancelled_jobs
        proc.terminate.assert_called_once()
        proc.wait.assert_not_called()
        proc.kill.assert_not_called()

    def test_cancel_unknown_job_is_safe(self):
        extractor = MakeMKVExtractor()
        extractor.cancel(999)  # must not raise
        assert 999 in extractor._cancelled_jobs
