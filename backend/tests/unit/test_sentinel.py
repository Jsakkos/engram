"""Unit tests for the Sentinel drive monitor.

Covers the platform-independent surface:
- DriveMonitor._check_drives debounce (events fire only after 2 consecutive
  polls confirm a state change) via patched dispatch functions.
- The Linux /sys/block readers with mocked filesystem I/O.

The ctypes Windows paths are guarded so they only run on win32.
"""

import sys
from unittest.mock import AsyncMock, mock_open, patch

import pytest

import app.core.sentinel as sentinel
from app.core.sentinel import DriveMonitor


def _make_monitor() -> tuple[DriveMonitor, AsyncMock]:
    """Build a DriveMonitor with a stubbed _notify so we can assert on events."""
    monitor = DriveMonitor()
    notify = AsyncMock()
    monitor._notify = notify
    return monitor, notify


@pytest.mark.unit
class TestCheckDrivesDebounce:
    """_check_drives must debounce: 2 consecutive polls before firing."""

    async def test_insertion_requires_two_polls(self):
        monitor, notify = _make_monitor()
        monitor._drive_states = {"/dev/sr0": False}

        with (
            patch.object(sentinel, "get_optical_drives", return_value=["/dev/sr0"]),
            patch.object(sentinel, "is_disc_present", return_value=True),
            patch.object(sentinel, "get_volume_label", return_value="MOVIE_2020"),
        ):
            # First poll: state differs but debounce not satisfied -> no event.
            await monitor._check_drives()
            notify.assert_not_called()
            assert monitor._pending_changes["/dev/sr0"] == 1
            assert monitor._drive_states["/dev/sr0"] is False

            # Second poll: confirmed -> fire "inserted".
            await monitor._check_drives()
            notify.assert_awaited_once_with("inserted", "/dev/sr0", "MOVIE_2020")
            assert monitor._drive_states["/dev/sr0"] is True
            assert "/dev/sr0" not in monitor._pending_changes

    async def test_removal_requires_two_polls(self):
        monitor, notify = _make_monitor()
        monitor._drive_states = {"/dev/sr0": True}

        with (
            patch.object(sentinel, "get_optical_drives", return_value=["/dev/sr0"]),
            patch.object(sentinel, "is_disc_present", return_value=False),
            patch.object(sentinel, "get_volume_label", return_value=""),
        ):
            await monitor._check_drives()
            notify.assert_not_called()

            await monitor._check_drives()
            notify.assert_awaited_once_with("removed", "/dev/sr0", "")
            assert monitor._drive_states["/dev/sr0"] is False

    async def test_flicker_resets_debounce(self):
        """A single anomalous poll followed by a return to prior state fires nothing."""
        monitor, notify = _make_monitor()
        monitor._drive_states = {"/dev/sr0": True}

        present_values = iter([False, True])  # flicker absent, then back present

        def fake_present(_drive):
            return next(present_values)

        with (
            patch.object(sentinel, "get_optical_drives", return_value=["/dev/sr0"]),
            patch.object(sentinel, "is_disc_present", side_effect=fake_present),
            patch.object(sentinel, "get_volume_label", return_value="LABEL"),
        ):
            await monitor._check_drives()  # sees False -> pending=1
            assert monitor._pending_changes["/dev/sr0"] == 1
            await monitor._check_drives()  # sees True again -> reset, no event

        notify.assert_not_called()
        assert "/dev/sr0" not in monitor._pending_changes
        assert monitor._drive_states["/dev/sr0"] is True

    async def test_stable_state_no_event(self):
        monitor, notify = _make_monitor()
        monitor._drive_states = {"/dev/sr0": True}

        with (
            patch.object(sentinel, "get_optical_drives", return_value=["/dev/sr0"]),
            patch.object(sentinel, "is_disc_present", return_value=True),
            patch.object(sentinel, "get_volume_label", return_value="LABEL"),
        ):
            await monitor._check_drives()
            await monitor._check_drives()

        notify.assert_not_called()

    async def test_unknown_drive_defaults_to_empty(self):
        """A drive not yet tracked defaults to 'no disc' as previous state."""
        monitor, notify = _make_monitor()
        monitor._drive_states = {}

        with (
            patch.object(sentinel, "get_optical_drives", return_value=["/dev/sr1"]),
            patch.object(sentinel, "is_disc_present", return_value=True),
            patch.object(sentinel, "get_volume_label", return_value="DISC"),
        ):
            await monitor._check_drives()  # previous defaults False, current True
            await monitor._check_drives()

        notify.assert_awaited_once_with("inserted", "/dev/sr1", "DISC")


@pytest.mark.unit
class TestNotify:
    """_notify dispatches to both sync and async callbacks."""

    async def test_sync_callback_invoked(self):
        calls = []
        monitor = DriveMonitor(callback=lambda d, e, label: calls.append((d, e, label)))

        await monitor._notify("inserted", "/dev/sr0", "LBL")

        assert calls == [("/dev/sr0", "inserted", "LBL")]

    async def test_async_callback_invoked(self):
        monitor = DriveMonitor()
        async_cb = AsyncMock()
        monitor._async_callback = async_cb

        await monitor._notify("removed", "/dev/sr0", "")

        async_cb.assert_awaited_once_with("/dev/sr0", "removed", "")

    async def test_async_callback_error_swallowed(self):
        """An exception in the async callback must not propagate."""
        monitor = DriveMonitor()
        monitor._async_callback = AsyncMock(side_effect=RuntimeError("cb boom"))

        # Should not raise.
        await monitor._notify("inserted", "/dev/sr0", "LBL")


@pytest.mark.unit
class TestLinuxReaders:
    """Linux /sys/block readers with mocked filesystem I/O."""

    def test_get_optical_drives_linux(self):
        with patch.object(sentinel.glob, "glob", return_value=["/sys/block/sr1", "/sys/block/sr0"]):
            drives = sentinel._get_optical_drives_linux()

        # Sorted, mapped to /dev/ paths.
        assert drives == ["/dev/sr0", "/dev/sr1"]

    def test_get_optical_drives_linux_none(self):
        with patch.object(sentinel.glob, "glob", return_value=[]):
            assert sentinel._get_optical_drives_linux() == []

    def test_is_disc_present_linux_true(self):
        with patch("builtins.open", mock_open(read_data="2048\n")):
            assert sentinel._is_disc_present_linux("/dev/sr0") is True

    def test_is_disc_present_linux_zero_size(self):
        with patch("builtins.open", mock_open(read_data="0\n")):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False

    def test_is_disc_present_linux_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False

    def test_is_disc_present_linux_bad_value(self):
        with patch("builtins.open", mock_open(read_data="notanumber")):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False

    def test_get_volume_label_linux_success(self):
        completed = type("R", (), {"returncode": 0, "stdout": "  MY_DISC \n"})()
        with patch.object(sentinel.subprocess, "run", return_value=completed):
            assert sentinel._get_volume_label_linux("/dev/sr0") == "MY_DISC"

    def test_get_volume_label_linux_nonzero_return(self):
        completed = type("R", (), {"returncode": 2, "stdout": "ignored"})()
        with patch.object(sentinel.subprocess, "run", return_value=completed):
            assert sentinel._get_volume_label_linux("/dev/sr0") == ""

    def test_get_volume_label_linux_blkid_missing(self):
        with patch.object(sentinel.subprocess, "run", side_effect=FileNotFoundError):
            assert sentinel._get_volume_label_linux("/dev/sr0") == ""

    def test_eject_disc_linux_success(self):
        completed = type("R", (), {"returncode": 0, "stderr": ""})()
        with patch.object(sentinel.subprocess, "run", return_value=completed):
            assert sentinel._eject_disc_linux("/dev/sr0") is True

    def test_eject_disc_linux_failure(self):
        completed = type("R", (), {"returncode": 1, "stderr": "busy"})()
        with patch.object(sentinel.subprocess, "run", return_value=completed):
            assert sentinel._eject_disc_linux("/dev/sr0") is False


@pytest.mark.unit
class TestDispatch:
    """Public dispatch functions route by platform."""

    def test_get_optical_drives_dispatch_linux(self):
        with (
            patch.object(sentinel.sys, "platform", "linux"),
            patch.object(sentinel, "_get_optical_drives_linux", return_value=["/dev/sr0"]),
        ):
            assert sentinel.get_optical_drives() == ["/dev/sr0"]

    def test_dispatch_other_platform_returns_empty(self):
        with patch.object(sentinel.sys, "platform", "darwin"):
            assert sentinel.get_optical_drives() == []
            assert sentinel.get_volume_label("x") == ""
            assert sentinel.is_disc_present("x") is False
            assert sentinel.eject_disc("x") is False


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes paths")
class TestWindowsReaders:
    """ctypes Windows paths — only exercised on win32."""

    def test_is_disc_present_windows(self):
        fake_kernel = type("K", (), {})()
        fake_kernel.GetVolumeInformationW = lambda *a, **k: 1
        with patch.object(sentinel.ctypes, "windll", type("W", (), {"kernel32": fake_kernel})()):
            assert sentinel._is_disc_present_windows("E:") is True
