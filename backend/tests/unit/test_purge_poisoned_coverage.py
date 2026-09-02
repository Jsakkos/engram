"""Tests for the one-time poisoned-coverage purge."""

import time

import pytest

from app.matcher import coverage_tracker


@pytest.mark.unit
class TestPurge:
    def test_dry_run_deletes_nothing(self, ppc):
        coverage_tracker.record(100, 1, total=20, covered=0)
        n = ppc.purge(since=0.0, min_ratio=0.6, apply=False)
        assert n == 1
        assert coverage_tracker.get_show_coverage(100) != []

    def test_apply_deletes_low_ratio_rows_after_cutoff(self, ppc):
        coverage_tracker.record(101, 1, total=20, covered=0)
        n = ppc.purge(since=0.0, min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(101) == []

    def test_high_coverage_rows_are_preserved(self, ppc):
        """A season that succeeded during the bad window is still a real
        success: the outage suppressed results, it did not fabricate them."""
        coverage_tracker.record(102, 1, total=20, covered=20)
        ppc.purge(since=0.0, min_ratio=0.6, apply=True)
        assert coverage_tracker.get_show_coverage(102) != []

    def test_rows_before_cutoff_are_preserved(self, ppc):
        """Healthy-window data is the ground truth Phase 2 curation depends on."""
        coverage_tracker.record(103, 1, total=20, covered=0)
        future_cutoff = time.time() + 3600
        n = ppc.purge(since=future_cutoff, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(103) != []

    def test_idempotent(self, ppc):
        coverage_tracker.record(104, 1, total=20, covered=0)
        ppc.purge(since=0.0, min_ratio=0.6, apply=True)
        assert ppc.purge(since=0.0, min_ratio=0.6, apply=True) == 0

    def test_row_at_exactly_min_ratio_is_preserved(self, ppc):
        """The threshold comparison is strictly < min_ratio."""
        coverage_tracker.record(105, 1, total=20, covered=12)  # ratio == 0.6
        n = ppc.purge(since=0.0, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(105) != []

    def test_row_at_exactly_since_boundary_is_matched(self, ppc):
        """The cutoff comparison is inclusive: attempted_at >= since."""
        coverage_tracker.record(106, 1, total=20, covered=0)
        row = coverage_tracker.get_show_coverage(106)[0]
        n = ppc.purge(since=row["attempted_at"], min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(106) == []
