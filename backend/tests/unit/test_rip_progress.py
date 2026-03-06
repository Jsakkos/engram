"""Unit tests for rip progress monitoring.

Tests filesystem-based progress calculation, PRGV percent parsing,
and progress callback logic.
"""

from dataclasses import dataclass

import pytest

from app.core.extractor import RipProgress

# --- Helpers ---


@dataclass
class FakeTitle:
    """Minimal stand-in for DiscTitle in progress calculations."""

    title_index: int
    file_size_bytes: int
    id: int = 0


def calc_filesystem_progress(sorted_titles, output_dir, total_job_bytes):
    """Replicate the filesystem progress calculation from _filesystem_progress_monitor."""
    total_done = 0
    current_title_num = 0
    for i, t in enumerate(sorted_titles):
        pattern = f"*_t{t.title_index:02d}.mkv"
        matches = list(output_dir.glob(pattern))
        if matches:
            fsize = matches[0].stat().st_size
            total_done += fsize
            if fsize < t.file_size_bytes:
                current_title_num = i + 1

    if total_job_bytes > 0:
        pct = min((total_done / total_job_bytes) * 100, 100.0)
    else:
        pct = 0.0
    return pct, current_title_num


def calc_prgv_percent(current, total, max_val):
    """Replicate the fixed PRGV percent calculation from extractor.py."""
    if max_val > 0:
        divisor = total if total > 0 else max_val
        return (current / divisor) * 100
    return 0.0


def calc_global_progress(progress: RipProgress, sorted_titles, total_job_bytes):
    """Replicate the progress callback's global percent calculation."""
    current_idx = progress.current_title
    cumulative_previous = 0
    active_title_size = 0

    if 0 <= (current_idx - 1) < len(sorted_titles):
        active_title_size = sorted_titles[current_idx - 1].file_size_bytes
        for i in range(current_idx - 1):
            cumulative_previous += sorted_titles[i].file_size_bytes

    current_title_bytes = int((progress.percent / 100.0) * active_title_size)
    total_bytes_done = cumulative_previous + current_title_bytes

    if total_job_bytes > 0:
        return (total_bytes_done / total_job_bytes) * 100.0
    return 0.0


# --- Filesystem progress tests ---


class TestFilesystemProgressCalculation:
    """Test the filesystem-based progress monitor logic."""

    def test_partial_file_reports_progress(self, tmp_path):
        """A growing file should produce non-zero progress."""
        titles = [FakeTitle(title_index=0, file_size_bytes=1_000_000)]
        # Create a partial file
        mkv = tmp_path / "title_t00.mkv"
        mkv.write_bytes(b"\x00" * 500_000)  # 50% done

        pct, current_num = calc_filesystem_progress(titles, tmp_path, 1_000_000)
        assert pct == pytest.approx(50.0, abs=0.1)
        assert current_num == 1  # Title 1 is still growing

    def test_completed_files_accumulate(self, tmp_path):
        """Multiple completed files should sum to correct total."""
        titles = [
            FakeTitle(title_index=0, file_size_bytes=100_000),
            FakeTitle(title_index=1, file_size_bytes=100_000),
            FakeTitle(title_index=2, file_size_bytes=100_000),
        ]
        total = 300_000

        # First two complete, third partial
        (tmp_path / "title_t00.mkv").write_bytes(b"\x00" * 100_000)
        (tmp_path / "title_t01.mkv").write_bytes(b"\x00" * 100_000)
        (tmp_path / "title_t02.mkv").write_bytes(b"\x00" * 50_000)

        pct, current_num = calc_filesystem_progress(titles, tmp_path, total)
        assert pct == pytest.approx(83.33, abs=0.1)
        assert current_num == 3  # Title 3 is still growing

    def test_zero_total_bytes_returns_zero(self, tmp_path):
        """If total_job_bytes is 0, progress should be 0 (not division error)."""
        titles = [FakeTitle(title_index=0, file_size_bytes=0)]
        pct, current_num = calc_filesystem_progress(titles, tmp_path, 0)
        assert pct == 0.0

    def test_no_files_yet_returns_zero(self, tmp_path):
        """Before MakeMKV creates any files, progress is 0."""
        titles = [FakeTitle(title_index=0, file_size_bytes=1_000_000)]
        pct, current_num = calc_filesystem_progress(titles, tmp_path, 1_000_000)
        assert pct == 0.0
        assert current_num == 0

    def test_all_complete_returns_100(self, tmp_path):
        """All files at expected size → 100%."""
        titles = [
            FakeTitle(title_index=0, file_size_bytes=100_000),
            FakeTitle(title_index=1, file_size_bytes=200_000),
        ]
        total = 300_000
        (tmp_path / "title_t00.mkv").write_bytes(b"\x00" * 100_000)
        (tmp_path / "title_t01.mkv").write_bytes(b"\x00" * 200_000)

        pct, _ = calc_filesystem_progress(titles, tmp_path, total)
        assert pct == pytest.approx(100.0, abs=0.1)


# --- PRGV parsing tests ---


class TestPRGVParsing:
    """Test the fixed PRGV percent calculation."""

    def test_single_title_percent(self):
        """Single-title rip: current/total gives per-title %."""
        # PRGV:5000,10000,10000 → total == max, so 50%
        pct = calc_prgv_percent(current=5000, total=10000, max_val=10000)
        assert pct == pytest.approx(50.0)

    def test_all_mode_uses_total_not_max(self):
        """All-mode rip: uses total (per-title) not max (overall)."""
        # PRGV:2500,10000,80000
        # Old buggy code: 2500/80000 = 3.1%
        # Fixed code: 2500/10000 = 25%
        pct = calc_prgv_percent(current=2500, total=10000, max_val=80000)
        assert pct == pytest.approx(25.0)

    def test_zero_total_falls_back_to_max(self):
        """If total is 0, fall back to max_val."""
        pct = calc_prgv_percent(current=5000, total=0, max_val=10000)
        assert pct == pytest.approx(50.0)

    def test_zero_max_returns_zero(self):
        """If max_val is 0, return 0 (avoid division by zero)."""
        pct = calc_prgv_percent(current=5000, total=10000, max_val=0)
        assert pct == 0.0


# --- Progress callback calculation tests ---


class TestProgressCallbackCalculation:
    """Test the global progress calculation in the progress callback."""

    def test_nonzero_percent_produces_nonzero_global(self):
        """If PRGV reports 50% on title 1, global_percent should be > 0."""
        titles = [
            FakeTitle(title_index=0, file_size_bytes=500_000),
            FakeTitle(title_index=1, file_size_bytes=500_000),
        ]
        total_bytes = 1_000_000
        progress = RipProgress(percent=50.0, current_title=1, total_titles=2)

        global_pct = calc_global_progress(progress, titles, total_bytes)
        # 50% of title 1 (500KB) = 250KB / 1MB total = 25%
        assert global_pct == pytest.approx(25.0, abs=0.1)

    def test_second_title_includes_previous(self):
        """Progress on title 2 should include completed title 1."""
        titles = [
            FakeTitle(title_index=0, file_size_bytes=400_000),
            FakeTitle(title_index=1, file_size_bytes=600_000),
        ]
        total_bytes = 1_000_000
        progress = RipProgress(percent=50.0, current_title=2, total_titles=2)

        global_pct = calc_global_progress(progress, titles, total_bytes)
        # title 1 done (400K) + 50% of title 2 (300K) = 700K / 1M = 70%
        assert global_pct == pytest.approx(70.0, abs=0.1)

    def test_all_titles_complete_reaches_100(self):
        """After all titles report 100%, global_percent should be ~100%."""
        titles = [
            FakeTitle(title_index=0, file_size_bytes=500_000),
            FakeTitle(title_index=1, file_size_bytes=500_000),
        ]
        total_bytes = 1_000_000
        progress = RipProgress(percent=100.0, current_title=2, total_titles=2)

        global_pct = calc_global_progress(progress, titles, total_bytes)
        assert global_pct == pytest.approx(100.0, abs=0.1)

    def test_zero_total_bytes_returns_zero(self):
        """If total_job_bytes is 0, progress should be 0."""
        titles = [FakeTitle(title_index=0, file_size_bytes=0)]
        progress = RipProgress(percent=50.0, current_title=1, total_titles=1)

        global_pct = calc_global_progress(progress, titles, 0)
        assert global_pct == 0.0
