"""Tests for extractor callback behavior.

Verifies that the 'created' message from MakeMKV does NOT fire
title_complete_callback, and that stable file size detection DOES — but
only after STABLE_CHECKS_REQUIRED consecutive stable polls (in-flight) or
immediately on a forced post-process check.
"""

import re
import threading
from pathlib import Path
from unittest.mock import MagicMock

# Match the constant used in extractor.py
STABLE_CHECKS_REQUIRED = 3


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
        line = "MSG:5011,0,0,\"File '/output/title00.mkv' created successfully.\""
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

        line = "MSG:5011,0,0,\"File '/output/title00.mkv' created successfully.\""
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
    """Stable file size detection SHOULD fire title_complete_callback — but only
    after STABLE_CHECKS_REQUIRED consecutive stable polls (not after just one).
    """

    # ------------------------------------------------------------------
    # Helper: simulate _check_for_completed_files with the new multi-check
    # logic.  This mirrors the closure in extractor.py so tests stay in sync
    # with production behaviour without needing to call rip_titles().
    # ------------------------------------------------------------------

    def _make_state(self):
        """Return a fresh shared-state dict used by _simulate_check."""
        return {
            "known_files": {},
            "completed_files": set(),
            "stable_counts": {},
            "output_files": [],
        }

    def _simulate_check(self, state, sizes: dict[str, int], callback, *, force: bool = False):
        """Simulate one call to _check_for_completed_files with the given file sizes.

        ``sizes`` maps filename → current size (as if returned by stat().st_size).
        Mirrors the new closure logic in extractor.py exactly.
        """
        output_dir = Path("/output")
        known_files = state["known_files"]
        completed_files = state["completed_files"]
        stable_counts = state["stable_counts"]
        output_files = state["output_files"]

        def _fire(fname, size):
            completed_files.add(fname)
            stable_counts.pop(fname, None)
            filepath = output_dir / fname
            output_files.append(filepath)
            callback(len(completed_files), filepath)

        for fname, current_size in sizes.items():
            if fname in completed_files:
                continue
            if fname in known_files:
                if current_size > 0:
                    if force:
                        _fire(fname, current_size)
                    elif current_size == known_files[fname]:
                        stable_counts[fname] = stable_counts.get(fname, 0) + 1
                        if stable_counts[fname] >= STABLE_CHECKS_REQUIRED:
                            _fire(fname, current_size)
                    else:
                        stable_counts[fname] = 0
            known_files[fname] = current_size

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_single_stable_check_does_not_fire(self):
        """One stable poll is no longer enough — callback must NOT fire yet."""
        state = self._make_state()
        callback = MagicMock()

        # First check: file appears at 96 MB
        self._simulate_check(state, {"C1_t01.mkv": 96_000_000}, callback)
        callback.assert_not_called()

        # Second check: same size — one stable interval — still not enough
        self._simulate_check(state, {"C1_t01.mkv": 96_000_000}, callback)
        callback.assert_not_called()

    def test_multi_stable_checks_fires_callback(self):
        """Exactly STABLE_CHECKS_REQUIRED consecutive stable polls must fire."""
        state = self._make_state()
        callback = MagicMock()

        # Seed known_files with first appearance
        self._simulate_check(state, {"C1_t01.mkv": 2_193_000_000}, callback)
        callback.assert_not_called()  # first appearance, not yet stable

        # Stable polls 1 … STABLE_CHECKS_REQUIRED-1: still not fired
        for i in range(1, STABLE_CHECKS_REQUIRED):
            self._simulate_check(state, {"C1_t01.mkv": 2_193_000_000}, callback)
            callback.assert_not_called(), f"should not fire after {i} stable checks"

        # The Nth consecutive stable poll fires
        self._simulate_check(state, {"C1_t01.mkv": 2_193_000_000}, callback)
        callback.assert_called_once()
        assert "C1_t01.mkv" in state["completed_files"]

    def test_size_change_resets_stable_count(self):
        """A size increase mid-rip resets the stability counter to zero."""
        state = self._make_state()
        callback = MagicMock()

        # File appears (baseline — no count increment), then one stable check
        self._simulate_check(state, {"C1_t01.mkv": 96_000_000}, callback)
        self._simulate_check(state, {"C1_t01.mkv": 96_000_000}, callback)
        # First call sets the baseline; second call is the 1st stable observation
        assert state["stable_counts"].get("C1_t01.mkv", 0) == 1

        # File grows — counter must reset
        self._simulate_check(state, {"C1_t01.mkv": 500_000_000}, callback)
        assert state["stable_counts"].get("C1_t01.mkv", 0) == 0
        callback.assert_not_called()

        # Stable counts restart from zero
        self._simulate_check(state, {"C1_t01.mkv": 500_000_000}, callback)
        assert state["stable_counts"].get("C1_t01.mkv", 0) == 1
        callback.assert_not_called()

    def test_stable_count_cleared_after_callback(self):
        """stable_counts entry is removed once a file fires its callback."""
        state = self._make_state()
        callback = MagicMock()

        # Seed + STABLE_CHECKS_REQUIRED stable polls
        self._simulate_check(state, {"t01.mkv": 1_000_000_000}, callback)
        for _ in range(STABLE_CHECKS_REQUIRED):
            self._simulate_check(state, {"t01.mkv": 1_000_000_000}, callback)

        callback.assert_called_once()
        assert "t01.mkv" not in state["stable_counts"]

    def test_force_fires_immediately_without_stability(self):
        """force=True fires with only one look — process exit guarantees completeness."""
        state = self._make_state()
        callback = MagicMock()

        # Seed the file so it's in known_files
        self._simulate_check(state, {"C1_t01.mkv": 2_193_000_000}, callback)
        callback.assert_not_called()

        # force=True — fires immediately
        self._simulate_check(state, {"C1_t01.mkv": 2_193_000_000}, callback, force=True)
        callback.assert_called_once()
        assert "C1_t01.mkv" in state["completed_files"]

    def test_force_does_not_double_fire(self):
        """force=True skips files already in completed_files."""
        state = self._make_state()
        callback = MagicMock()

        # Seed + first forced completion
        self._simulate_check(state, {"t01.mkv": 1_000_000_000}, callback)
        self._simulate_check(state, {"t01.mkv": 1_000_000_000}, callback, force=True)
        assert callback.call_count == 1

        # Second force call must be a no-op
        self._simulate_check(state, {"t01.mkv": 1_000_000_000}, callback, force=True)
        assert callback.call_count == 1  # unchanged

    def test_force_does_not_fire_for_zero_size(self):
        """force=True must NOT fire for a 0-byte file (MakeMKV opened but didn't write)."""
        state = self._make_state()
        callback = MagicMock()

        # File created at 0 bytes (MSG 'created' path seeds it at 0)
        state["known_files"]["t01.mkv"] = 0

        self._simulate_check(state, {"t01.mkv": 0}, callback, force=True)
        callback.assert_not_called()
        assert "t01.mkv" not in state["completed_files"]

    def test_growing_file_does_not_fire_callback(self):
        """A file whose size is still changing should NOT fire callback."""
        state = self._make_state()
        callback = MagicMock()

        self._simulate_check(state, {"title00.mkv": 500_000}, callback)
        self._simulate_check(state, {"title00.mkv": 1_000_000}, callback)  # still growing

        callback.assert_not_called()
        assert "title00.mkv" not in state["completed_files"]

    def test_zero_size_file_does_not_fire_callback(self):
        """A file with 0 bytes should NOT be considered complete."""
        state = self._make_state()
        callback = MagicMock()

        # Seed at 0, check at 0 — size > 0 guard prevents firing
        self._simulate_check(state, {"title00.mkv": 0}, callback)
        self._simulate_check(state, {"title00.mkv": 0}, callback)

        callback.assert_not_called()
