"""Tests for the one-time poisoned-coverage purge."""

import time

import pytest

from app.matcher import coverage_tracker

# Existing cases predate the upper bound and exercise the lower one; a
# far-future ceiling keeps them testing what they were written to test.
_FAR_FUTURE = 4_000_000_000.0


@pytest.mark.unit
class TestPurge:
    def test_dry_run_deletes_nothing(self, ppc):
        coverage_tracker.record(100, 1, total=20, covered=0)
        n = ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=False)
        assert n == 1
        assert coverage_tracker.get_show_coverage(100) != []

    def test_apply_deletes_low_ratio_rows_after_cutoff(self, ppc):
        coverage_tracker.record(101, 1, total=20, covered=0)
        n = ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(101) == []

    def test_high_coverage_rows_are_preserved(self, ppc):
        """A season that succeeded during the bad window is still a real
        success: the outage suppressed results, it did not fabricate them."""
        coverage_tracker.record(102, 1, total=20, covered=20)
        ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert coverage_tracker.get_show_coverage(102) != []

    def test_rows_before_cutoff_are_preserved(self, ppc):
        """Healthy-window data is the ground truth Phase 2 curation depends on."""
        coverage_tracker.record(103, 1, total=20, covered=0)
        future_cutoff = time.time() + 3600
        n = ppc.purge(since=future_cutoff, until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(103) != []

    def test_idempotent(self, ppc):
        coverage_tracker.record(104, 1, total=20, covered=0)
        ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=True) == 0

    def test_row_at_exactly_min_ratio_is_preserved(self, ppc):
        """The threshold comparison is strictly < min_ratio."""
        coverage_tracker.record(105, 1, total=20, covered=12)  # ratio == 0.6
        n = ppc.purge(since=0.0, until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(105) != []

    def test_row_at_exactly_since_boundary_is_matched(self, ppc):
        """The cutoff comparison is inclusive: attempted_at >= since."""
        coverage_tracker.record(106, 1, total=20, covered=0)
        row = coverage_tracker.get_show_coverage(106)[0]
        n = ppc.purge(since=row["attempted_at"], until=_FAR_FUTURE, min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(106) == []


@pytest.mark.unit
class TestPurgeUpperBound:
    """The upper bound is what keeps this script from undoing the repair it
    was written to support. Post-outage low-coverage rows are HONEST: they are
    the skip-list that stops dead seasons burning quota on every nightly run.
    An unbounded purge deletes exactly those."""

    def test_row_after_until_is_preserved(self, ppc):
        """A recent, honestly-measured zero-coverage season must survive."""
        coverage_tracker.record(200, 1, total=20, covered=0)
        row = coverage_tracker.get_show_coverage(200)[0]
        # Window closes an hour before this row was written.
        n = ppc.purge(since=0.0, until=row["attempted_at"] - 3600, min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(200) != []

    def test_row_at_exactly_until_boundary_is_preserved(self, ppc):
        """The upper comparison is strictly exclusive: attempted_at < until."""
        coverage_tracker.record(201, 1, total=20, covered=0)
        row = coverage_tracker.get_show_coverage(201)[0]
        n = ppc.purge(since=0.0, until=row["attempted_at"], min_ratio=0.6, apply=True)
        assert n == 0
        assert coverage_tracker.get_show_coverage(201) != []

    def test_row_inside_window_is_still_matched(self, ppc):
        """Sanity twin: the bound narrows the purge, it does not disable it."""
        coverage_tracker.record(202, 1, total=20, covered=0)
        row = coverage_tracker.get_show_coverage(202)[0]
        n = ppc.purge(since=0.0, until=row["attempted_at"] + 3600, min_ratio=0.6, apply=True)
        assert n == 1
        assert coverage_tracker.get_show_coverage(202) == []

    def test_default_window_spans_collapse_to_repair(self, ppc):
        """The shipped defaults must span the whole broken-harvester era.

        The poisoned era ends when the repair LANDS, not when the two worst
        outage days end: the harvester stayed broken in between, so any run in
        that period could record an infrastructure failure as a content fact
        (the 2026-07-06 run retrieved 1 episode of 291, a 0.3% rate against a
        94.3% healthy baseline). Runs after the repair record ``degraded``
        instead of a false zero, so the bound closes there and not before.
        """
        assert ppc.DEFAULT_CUTOFF == "2026-06-11"  # the collapse
        assert ppc.DEFAULT_UNTIL == "2026-09-01"  # the repair
        since = ppc._parse_utc_date(ppc.DEFAULT_CUTOFF)
        until = ppc._parse_utc_date(ppc.DEFAULT_UNTIL)
        assert since < until
        # Not merely the two worst days: the later poisoned runs (06-21,
        # 06-22, 07-06) must fall inside the window too.
        assert until - since > 2 * 86400
        assert since < ppc._parse_utc_date("2026-07-06") < until

    def test_utc_date_parsing_is_timezone_independent(self, ppc):
        """``attempted_at`` is a UTC epoch; a local-time cutoff would shift the
        window by the machine's UTC offset, so a laptop and the nightly server
        would disagree about which rows are in scope."""
        assert ppc._parse_utc_date("2026-06-11") == 1781136000.0
