"""Tests for the persistent coverage tracker."""

import time

import pytest

from app.matcher import coverage_tracker


@pytest.mark.unit
class TestRecord:
    def test_record_inserts_row(self):
        coverage_tracker.record(tmdb_id=1396, season=1, total=7, covered=7)
        skip, prev = coverage_tracker.should_skip(1396, 1, min_ratio=0.6)
        # Full coverage; should NOT trigger skip.
        assert skip is False
        assert prev is None

    def test_record_replaces_existing(self):
        coverage_tracker.record(1396, 1, 10, 1)  # 10% coverage
        coverage_tracker.record(1396, 1, 10, 9)  # bumped to 90%
        skip, _ = coverage_tracker.should_skip(1396, 1, min_ratio=0.6)
        assert skip is False  # ratio is now well above threshold

    def test_record_handles_zero_total(self):
        coverage_tracker.record(99999, 1, total=0, covered=0)
        # 0 ratio is below any sane threshold → should skip.
        skip, prev = coverage_tracker.should_skip(99999, 1, min_ratio=0.6)
        assert skip is True
        assert prev["coverage_ratio"] == 0.0


@pytest.mark.unit
class TestShouldSkip:
    def test_unrecorded_show_not_skipped(self):
        skip, prev = coverage_tracker.should_skip(42424242, 1, min_ratio=0.6)
        assert skip is False
        assert prev is None

    def test_below_threshold_within_window_is_skipped(self):
        coverage_tracker.record(1, 1, total=10, covered=1)  # 10%
        skip, prev = coverage_tracker.should_skip(1, 1, min_ratio=0.6, skip_window_days=30)
        assert skip is True
        assert prev["coverage_ratio"] == pytest.approx(0.1)
        assert prev["total_episodes"] == 10
        assert prev["covered_episodes"] == 1

    def test_above_threshold_not_skipped(self):
        coverage_tracker.record(2, 1, total=10, covered=8)  # 80%
        skip, _ = coverage_tracker.should_skip(2, 1, min_ratio=0.6)
        assert skip is False

    def test_outside_skip_window_not_skipped(self, monkeypatch):
        """A row older than skip_window_days should be considered eligible
        for retry — the corpus may have grown, or a new provider may have
        coverage the original attempt missed."""
        # Pretend the row was written 40 days ago.
        forty_days_ago = time.time() - 40 * 86400
        monkeypatch.setattr(coverage_tracker.time, "time", lambda: forty_days_ago)
        coverage_tracker.record(3, 1, total=10, covered=1)
        # Restore real clock; should_skip should see it as stale.
        monkeypatch.undo()
        skip, _ = coverage_tracker.should_skip(3, 1, min_ratio=0.6, skip_window_days=30)
        assert skip is False


@pytest.mark.unit
class TestClear:
    def test_clear_all(self):
        coverage_tracker.record(10, 1, 5, 0)
        coverage_tracker.record(20, 2, 5, 0)
        coverage_tracker.clear()
        assert coverage_tracker.should_skip(10, 1, 0.6) == (False, None)
        assert coverage_tracker.should_skip(20, 2, 0.6) == (False, None)

    def test_clear_one_show(self):
        coverage_tracker.record(100, 1, 5, 0)
        coverage_tracker.record(100, 2, 5, 0)
        coverage_tracker.record(200, 1, 5, 0)
        coverage_tracker.clear(tmdb_id=100)
        assert coverage_tracker.should_skip(100, 1, 0.6) == (False, None)
        assert coverage_tracker.should_skip(100, 2, 0.6) == (False, None)
        # Other show survives.
        skip, _ = coverage_tracker.should_skip(200, 1, 0.6)
        assert skip is True

    def test_clear_one_season(self):
        coverage_tracker.record(300, 1, 5, 0)
        coverage_tracker.record(300, 2, 5, 0)
        coverage_tracker.clear(tmdb_id=300, season=1)
        assert coverage_tracker.should_skip(300, 1, 0.6) == (False, None)
        skip, _ = coverage_tracker.should_skip(300, 2, 0.6)
        assert skip is True

    def test_clear_season_without_show_raises(self):
        with pytest.raises(ValueError):
            coverage_tracker.clear(season=1)
