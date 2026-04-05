"""Unit tests for Linux optical drive detection in sentinel.py.

Tests the Linux-specific implementations with mocked /sys/block
filesystem and subprocess calls. Also verifies Windows behavior
is unchanged.
"""

from unittest.mock import MagicMock, mock_open, patch

from app.core import sentinel


class TestGetOpticalDrivesLinux:
    """Tests for _get_optical_drives_linux."""

    def test_finds_sr_devices(self):
        with patch("glob.glob", return_value=["/sys/block/sr0", "/sys/block/sr1"]):
            drives = sentinel._get_optical_drives_linux()
        assert drives == ["/dev/sr0", "/dev/sr1"]

    def test_no_optical_drives(self):
        with patch("glob.glob", return_value=[]):
            drives = sentinel._get_optical_drives_linux()
        assert drives == []

    def test_single_drive(self):
        with patch("glob.glob", return_value=["/sys/block/sr0"]):
            drives = sentinel._get_optical_drives_linux()
        assert drives == ["/dev/sr0"]

    def test_sorted_output(self):
        with patch(
            "glob.glob", return_value=["/sys/block/sr2", "/sys/block/sr0", "/sys/block/sr1"]
        ):
            drives = sentinel._get_optical_drives_linux()
        assert drives == ["/dev/sr0", "/dev/sr1", "/dev/sr2"]


class TestGetVolumeLabelLinux:
    """Tests for _get_volume_label_linux."""

    def test_returns_label_from_blkid(self):
        mock_result = MagicMock(returncode=0, stdout="MY_DISC_LABEL\n")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            label = sentinel._get_volume_label_linux("/dev/sr0")

        assert label == "MY_DISC_LABEL"
        mock_run.assert_called_once_with(
            ["blkid", "-s", "LABEL", "-o", "value", "/dev/sr0"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_returns_empty_on_failure(self):
        mock_result = MagicMock(returncode=2, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            label = sentinel._get_volume_label_linux("/dev/sr0")
        assert label == ""

    def test_returns_empty_when_blkid_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            label = sentinel._get_volume_label_linux("/dev/sr0")
        assert label == ""

    def test_returns_empty_on_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("blkid", 10)):
            label = sentinel._get_volume_label_linux("/dev/sr0")
        assert label == ""


class TestIsDiscPresentLinux:
    """Tests for _is_disc_present_linux."""

    def test_disc_present_nonzero_size(self):
        with patch("builtins.open", mock_open(read_data="4194304\n")):
            assert sentinel._is_disc_present_linux("/dev/sr0") is True

    def test_no_disc_zero_size(self):
        with patch("builtins.open", mock_open(read_data="0\n")):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False

    def test_no_device_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False

    def test_permission_denied(self):
        with patch("builtins.open", side_effect=PermissionError):
            assert sentinel._is_disc_present_linux("/dev/sr0") is False


class TestEjectDiscLinux:
    """Tests for _eject_disc_linux."""

    def test_successful_eject(self):
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            assert sentinel._eject_disc_linux("/dev/sr0") is True

    def test_eject_failure(self):
        mock_result = MagicMock(returncode=1, stderr="device busy")
        with patch("subprocess.run", return_value=mock_result):
            assert sentinel._eject_disc_linux("/dev/sr0") is False

    def test_eject_command_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert sentinel._eject_disc_linux("/dev/sr0") is False

    def test_eject_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("eject", 30)):
            assert sentinel._eject_disc_linux("/dev/sr0") is False


class TestPlatformDispatch:
    """Tests that the public API dispatches correctly by platform."""

    def test_get_optical_drives_dispatches_linux(self):
        with (
            patch.object(sentinel, "sys") as mock_sys,
            patch.object(
                sentinel, "_get_optical_drives_linux", return_value=["/dev/sr0"]
            ) as mock_linux,
        ):
            mock_sys.platform = "linux"
            drives = sentinel.get_optical_drives()
        assert drives == ["/dev/sr0"]
        mock_linux.assert_called_once()

    def test_get_optical_drives_dispatches_windows(self):
        with (
            patch.object(sentinel, "sys") as mock_sys,
            patch.object(sentinel, "_get_optical_drives_windows", return_value=["D:"]) as mock_win,
        ):
            mock_sys.platform = "win32"
            drives = sentinel.get_optical_drives()
        assert drives == ["D:"]
        mock_win.assert_called_once()

    def test_get_optical_drives_unsupported_platform(self):
        with patch.object(sentinel, "sys") as mock_sys:
            mock_sys.platform = "darwin"
            drives = sentinel.get_optical_drives()
        assert drives == []

    def test_is_disc_present_dispatches_linux(self):
        with (
            patch.object(sentinel, "sys") as mock_sys,
            patch.object(sentinel, "_is_disc_present_linux", return_value=True) as mock_linux,
        ):
            mock_sys.platform = "linux"
            result = sentinel.is_disc_present("/dev/sr0")
        assert result is True
        mock_linux.assert_called_once_with("/dev/sr0")

    def test_eject_disc_dispatches_linux(self):
        with (
            patch.object(sentinel, "sys") as mock_sys,
            patch.object(sentinel, "_eject_disc_linux", return_value=True) as mock_linux,
        ):
            mock_sys.platform = "linux"
            result = sentinel.eject_disc("/dev/sr0")
        assert result is True
        mock_linux.assert_called_once_with("/dev/sr0")


class TestMakeMKVDriveFormat:
    """Tests that Linux drive IDs work with MakeMKV's dev: format."""

    def test_linux_drive_id_produces_valid_makemkv_spec(self):
        """Verify /dev/sr0 becomes dev:/dev/sr0 for MakeMKV."""
        drive = "/dev/sr0"
        # This is the logic from extractor.py _scan_disc_unlocked
        if not drive.startswith("disc:"):
            drive_spec = f"dev:{drive}"
        else:
            drive_spec = drive
        assert drive_spec == "dev:/dev/sr0"

    def test_windows_drive_id_produces_valid_makemkv_spec(self):
        """Verify E: becomes dev:E: for MakeMKV."""
        drive = "E:"
        if not drive.startswith("disc:"):
            drive_spec = f"dev:{drive}"
        else:
            drive_spec = drive
        assert drive_spec == "dev:E:"
