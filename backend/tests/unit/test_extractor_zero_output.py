"""Tests for fast-failing a rip that produces no output (issue #506).

Covers the two pure helpers added to the extractor and their wiring into the
MakeMKV command loop:

* ``_is_region_mismatch`` recognises MakeMKV's MSG:3032 region warning, so a
  region-locked disc gets an actionable message instead of "dirty or damaged".
* ``_should_abandon_zero_output_rip`` stops re-opening a disc that has already
  proven unreadable, instead of burning one full stall timeout per title.
"""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.extractor import (
    REGION_MISMATCH_FAILURE_REASON,
    STALL_FAILURE_REASON,
    MakeMKVExtractor,
    RipResult,
    _is_region_mismatch,
    _should_abandon_zero_output_rip,
)


@pytest.mark.unit
class TestRegionMismatchDetection:
    """MSG:3032 is MakeMKV's region-mismatch warning."""

    def test_detects_robot_mode_region_message(self):
        line = (
            'MSG:3032,0,2,"Region setting of drive ASUS:BW-16D1HT does not match '
            'the region of currently inserted disc, trying to work around..."'
        )
        assert _is_region_mismatch(line) is True

    def test_progress_line_is_not_a_region_mismatch(self):
        assert _is_region_mismatch("PRGV:14417,11915,65536") is False

    def test_other_msg_codes_are_not_region_mismatch(self):
        line = "MSG:5011,0,0,\"File '/output/title00.mkv' created successfully.\""
        assert _is_region_mismatch(line) is False

    def test_unrelated_code_containing_3032_is_not_matched(self):
        # A different message code that merely contains the digits must not match.
        assert _is_region_mismatch('MSG:13032,0,2,"Something else"') is False

    def test_region_reason_differs_from_generic_stall_reason(self):
        # The whole point of the change: the user must not be told the disc is
        # dirty when the real problem is the drive's region setting.
        assert REGION_MISMATCH_FAILURE_REASON != STALL_FAILURE_REASON
        assert "region" in REGION_MISMATCH_FAILURE_REASON.lower()


@pytest.mark.unit
class TestZeroOutputAbandonDecision:
    """When every attempt stalls and nothing is written, stop re-opening the disc.

    Abandoning is not lossy: each skipped title still routes to REVIEW as
    re-rippable, and the user has a manual re-rip path. So the threshold can be
    aggressive.
    """

    def test_does_not_abandon_below_the_stall_threshold(self):
        # One stall is not yet evidence the whole disc is unreadable.
        assert _should_abandon_zero_output_rip(stall_count=1, completed_outputs=0) is False

    def test_abandons_at_threshold_with_no_output(self):
        assert _should_abandon_zero_output_rip(stall_count=2, completed_outputs=0) is True

    def test_abandons_above_threshold_with_no_output(self):
        assert _should_abandon_zero_output_rip(stall_count=5, completed_outputs=0) is True

    def test_never_abandons_once_any_output_exists(self):
        # A disc that produced a file is partially readable. Stalls on later
        # titles are the "one bad title" case the per-title loop exists to
        # survive, so keep going.
        assert _should_abandon_zero_output_rip(stall_count=9, completed_outputs=1) is False

    def test_no_stalls_never_abandons(self):
        assert _should_abandon_zero_output_rip(stall_count=0, completed_outputs=0) is False


@pytest.mark.unit
class TestRipResultFailureReason:
    """RipResult carries the specific stall reason so the live per-title update
    and the History entry cannot disagree about why a rip failed."""

    def test_failure_reason_defaults_to_none(self):
        result = RipResult(success=True, output_files=[])
        assert result.failure_reason is None

    def test_failure_reason_round_trips(self):
        result = RipResult(
            success=False,
            output_files=[],
            stalled_titles=[1],
            failure_reason=REGION_MISMATCH_FAILURE_REASON,
        )
        assert result.failure_reason == REGION_MISMATCH_FAILURE_REASON


class _FakeStdout:
    """MakeMKV stdout that emits a few lines then hangs until terminated.

    The reader loop consumes this with ``iter(process.stdout.readline, "")``, so
    ``readline`` must return "" to signal EOF. Blocking until the stall watchdog
    kills the process is exactly the behaviour of a MakeMKV stuck at disc-open.
    """

    def __init__(self, lines: list[str], killed: threading.Event):
        self._lines = list(lines)
        self._killed = killed

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0) + "\n"
        self._killed.wait(timeout=10)
        return ""


class _FakeProc:
    """A makemkvcon that never writes a file and must be killed to stop."""

    def __init__(self, lines: list[str]):
        self._killed = threading.Event()
        self.returncode = None
        self.stdout = _FakeStdout(lines, self._killed)
        self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1
        self._killed.set()

    def wait(self):
        self._killed.wait(timeout=10)
        if self.returncode is None:
            self.returncode = 1
        return self.returncode


@pytest.mark.unit
class TestZeroOutputAbandonWiring:
    """End-to-end through the real command loop with makemkvcon stubbed."""

    async def test_abandons_remaining_titles_and_reports_them_stalled(self, tmp_path):
        """Four titles, every one stalling with no output: the loop must stop
        after ZERO_OUTPUT_STALL_LIMIT and still report all four as stalled."""
        spawned = []

        def _fake_popen(cmd, **kwargs):
            proc = _FakeProc(["PRGV:14417,11915,65536"])
            spawned.append(proc)
            return proc

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1, 2, 3],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=1,
            )

        # Only the first two commands ever ran; the rest were abandoned.
        assert len(spawned) == 2
        # All four titles are still accounted for as stalled, so none is left
        # stranded in RIPPING with no review entry.
        assert result.stalled_titles == [1, 2, 3, 4]
        assert sorted(idx for idx, _ in errors) == [1, 2, 3, 4]

    async def test_region_mismatch_sets_the_specific_reason(self, tmp_path):
        """A stall preceded by MSG:3032 reports the region cause, not 'dirty'."""

        def _fake_popen(cmd, **kwargs):
            return _FakeProc(
                [
                    'MSG:3032,0,2,"Region setting of drive ASUS:BW-16D1HT does not '
                    "match the region of currently inserted disc, trying to work "
                    'around..."',
                    "PRGV:14417,11915,65536",
                ]
            )

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=2,
            )

        assert result.failure_reason == REGION_MISMATCH_FAILURE_REASON
        assert all(reason == REGION_MISMATCH_FAILURE_REASON for _, reason in errors)

    async def test_plain_stall_keeps_the_generic_reason(self, tmp_path):
        """Without MSG:3032 the message is unchanged, so existing behaviour holds."""

        def _fake_popen(cmd, **kwargs):
            return _FakeProc(["PRGV:14417,11915,65536"])

        errors: list[tuple[int, str]] = []
        ex = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))

        with (
            patch("app.core.extractor.subprocess.Popen", side_effect=_fake_popen),
            patch("app.core.extractor.STALL_POLL_INTERVAL", 0.05),
        ):
            result = await ex.rip_titles(
                "/dev/sr0",
                tmp_path,
                title_indices=[0, 1],
                stall_timeout=0.2,
                title_error_callback=lambda idx, reason: errors.append((idx, reason)),
                job_id=3,
            )

        assert result.failure_reason is None
        assert all(reason == STALL_FAILURE_REASON for _, reason in errors)
