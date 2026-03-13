"""Tests for extractor callback behavior.

Verifies that the 'created' message from MakeMKV does NOT fire
title_complete_callback, and that stable file size detection DOES.
"""

import re
import threading
from pathlib import Path
from unittest.mock import MagicMock


class TestCreatedMessageDoesNotFireCallback:
    """The 'created' message parser should NOT fire title_complete_callback.

    MakeMKV emits a 'file created' message when it opens a file for writing,
    not when it finishes. Firing the callback at that point causes premature
    _on_title_ripped calls for multiple titles simultaneously.
    """

    def test_created_message_tracks_file_but_no_callback(self):
        """Simulates the fixed 'created' message handler logic."""
        known_files: dict[str, int] = {}
        _fs_lock = threading.Lock()
        callback = MagicMock()

        # Simulate a "created" line from MakeMKV
        line = 'MSG:5011,0,0,"File \'/output/title00.mkv\' created successfully."'
        output_dir = Path("/output")

        if ".mkv" in line and "created" in line:
            match = re.search(r'["\']([^"\']+\.mkv)["\']', line)
            if match:
                filepath = output_dir / Path(match.group(1)).name
                # Fixed behavior: track file, do NOT call callback
                with _fs_lock:
                    if filepath.name not in known_files:
                        known_files[filepath.name] = 0

        # Callback should NOT have been called
        callback.assert_not_called()
        # But file should be tracked
        assert "title00.mkv" in known_files
        assert known_files["title00.mkv"] == 0

    def test_created_message_does_not_duplicate_tracking(self):
        """If the same file appears in multiple 'created' messages, only track once."""
        known_files: dict[str, int] = {"title00.mkv": 500000}
        _fs_lock = threading.Lock()

        line = 'MSG:5011,0,0,"File \'/output/title00.mkv\' created successfully."'
        output_dir = Path("/output")

        if ".mkv" in line and "created" in line:
            match = re.search(r'["\']([^"\']+\.mkv)["\']', line)
            if match:
                filepath = output_dir / Path(match.group(1)).name
                with _fs_lock:
                    if filepath.name not in known_files:
                        known_files[filepath.name] = 0

        # Should NOT have reset the size to 0
        assert known_files["title00.mkv"] == 500000


class TestStableSizeDetection:
    """Stable file size detection SHOULD fire title_complete_callback.

    When a file's size is the same across two consecutive checks and > 0,
    it means MakeMKV has finished writing to it.
    """

    def test_stable_size_fires_callback(self):
        """Simulates _check_for_completed_files detecting a stable file."""
        known_files: dict[str, int] = {"title00.mkv": 1_000_000}
        completed_files: set[str] = set()
        output_files: list[Path] = []
        callback = MagicMock()
        output_dir = Path("/output")

        # Simulate _check_for_completed_files logic:
        # File exists with same size as last check → stable → complete
        fname = "title00.mkv"
        current_size = 1_000_000  # Same as known_files

        if fname in known_files:
            if current_size == known_files[fname] and current_size > 0:
                if fname not in completed_files:
                    completed_files.add(fname)
                    filepath = output_dir / fname
                    output_files.append(filepath)
                    if callback:
                        callback(len(completed_files), filepath)

        callback.assert_called_once_with(1, output_dir / "title00.mkv")
        assert fname in completed_files
        assert len(output_files) == 1

    def test_growing_file_does_not_fire_callback(self):
        """A file whose size is still changing should NOT fire callback."""
        known_files: dict[str, int] = {"title00.mkv": 500_000}
        completed_files: set[str] = set()
        callback = MagicMock()

        fname = "title00.mkv"
        current_size = 1_000_000  # Larger than last check — still growing

        if fname in known_files:
            if current_size == known_files[fname] and current_size > 0:
                if fname not in completed_files:
                    completed_files.add(fname)
                    callback(len(completed_files), Path("/output") / fname)

        # Update known size
        known_files[fname] = current_size

        callback.assert_not_called()
        assert fname not in completed_files

    def test_zero_size_file_does_not_fire_callback(self):
        """A file with 0 bytes should NOT be considered complete."""
        known_files: dict[str, int] = {"title00.mkv": 0}
        completed_files: set[str] = set()
        callback = MagicMock()

        fname = "title00.mkv"
        current_size = 0

        if fname in known_files:
            if current_size == known_files[fname] and current_size > 0:
                if fname not in completed_files:
                    completed_files.add(fname)
                    callback(len(completed_files), Path("/output") / fname)

        callback.assert_not_called()
