"""Unit tests for rip progress monitoring.

Covers the filesystem-based progress calculation the rip monitor relies on, plus
regression guards documenting MakeMKV's robot-mode PRGC/PRGV format — the reason
per-title progress is derived from output-file sizes, not from PRGC/PRGV.
"""

import re
from dataclasses import dataclass

import pytest

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


# --- MakeMKV robot-format regression guards ---


class TestMakeMKVRobotFormat:
    """Lock the real robot-mode PRGC/PRGV format (verbatim from a saved rip.log).

    Guards against reviving the old misparse, where PRGC's leading field was
    treated as a 0-based title index and PRGV percent was computed as
    ``current / total`` (both produced nonsense — see extractor.py). Per-title
    progress is owned by the filesystem monitor, not these codes.
    """

    def test_prgc_leading_field_is_a_message_code_not_a_title_index(self):
        # Verbatim PRGC lines from ~/.engram/logs/makemkv/<job>/rip.log.
        # Format is PRGC:code,id,"name" — code is a message code, id is 0.
        lines = [
            'PRGC:5018,0,"Scanning CD-ROM devices"',
            'PRGC:5017,0,"Saving to MKV file"',
            'PRGC:3103,0,"Processing titles"',
        ]
        for line in lines:
            m = re.match(r"PRGC:(\d+),(\d+),", line)
            assert m, line
            code, id_field = int(m.group(1)), int(m.group(2))
            # A real disc never has thousands of titles — this is a message code,
            # so the old `prgc_current_title = code + 1` was always garbage.
            assert code >= 1000
            assert id_field == 0

    def test_prgv_is_value_over_fixed_max_not_current_over_total(self):
        # Verbatim PRGV line: current=36541, total=6084, max=65536.
        line = "PRGV:36541,6084,65536"
        m = re.match(r"PRGV:\s*(\d+),\s*(\d+),\s*(\d+)", line)
        assert m
        current, total, max_val = (int(m.group(1)), int(m.group(2)), int(m.group(3)))

        # `max` is MakeMKV's fixed progress scale; each bar is value/max.
        assert max_val == 65536
        assert current / max_val * 100 == pytest.approx(55.76, abs=0.1)

        # The removed code computed current/total, which exceeds 100% — the bug.
        assert (current / total) * 100 > 100
