"""Tests for fast-failing a rip that produces no output (issue #506).

Covers the two pure helpers added to the extractor and their wiring into the
MakeMKV command loop:

* ``_is_region_mismatch`` recognises MakeMKV's MSG:3032 region warning, so a
  region-locked disc gets an actionable message instead of "dirty or damaged".
* ``_should_abandon_zero_output_rip`` stops re-opening a disc that has already
  proven unreadable, instead of burning one full stall timeout per title.
"""

import pytest

from app.core.extractor import (
    REGION_MISMATCH_FAILURE_REASON,
    STALL_FAILURE_REASON,
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
