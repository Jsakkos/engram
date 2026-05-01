"""Tests for compute_content_hash() cross-platform behavior."""

import hashlib
import struct
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.core.extractor import _find_linux_mount_point, compute_content_hash

PROC_MOUNTS_SAMPLE = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec 0 0
/dev/sda1 / ext4 rw,relatime 0 0
/dev/sr0 /run/media/user/DISC1 iso9660 ro,relatime 0 0
/dev/sr1 /run/media/user/DISC2 iso9660 ro,relatime 0 0
"""


class TestFindLinuxMountPoint:
    def test_device_found(self):
        with patch("builtins.open", mock_open(read_data=PROC_MOUNTS_SAMPLE)):
            result = _find_linux_mount_point("/dev/sr0")
        assert result == Path("/run/media/user/DISC1")

    def test_second_device_found(self):
        with patch("builtins.open", mock_open(read_data=PROC_MOUNTS_SAMPLE)):
            result = _find_linux_mount_point("/dev/sr1")
        assert result == Path("/run/media/user/DISC2")

    def test_device_not_mounted(self):
        with patch("builtins.open", mock_open(read_data=PROC_MOUNTS_SAMPLE)):
            result = _find_linux_mount_point("/dev/sr2")
        assert result is None

    def test_proc_mounts_unreadable(self):
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = _find_linux_mount_point("/dev/sr0")
        assert result is None

    def test_empty_mounts(self):
        with patch("builtins.open", mock_open(read_data="")):
            result = _find_linux_mount_point("/dev/sr0")
        assert result is None


class TestComputeContentHashLinux:
    def _make_fake_file(self, name: str, size: int) -> MagicMock:
        f = MagicMock()
        f.name = name
        f.stat.return_value.st_size = size
        return f

    def _expected_hash(self, sizes: list[int]) -> str:
        md5 = hashlib.md5()
        for s in sizes:
            md5.update(struct.pack("<q", s))
        return md5.hexdigest().upper()

    @patch("app.core.extractor.sys")
    def test_returns_none_when_not_mounted(self, mock_sys):
        mock_sys.platform = "linux"
        with patch("app.core.extractor._find_linux_mount_point", return_value=None):
            assert compute_content_hash("/dev/sr0") is None

    @patch("app.core.extractor.sys")
    def test_bluray_hash(self, mock_sys):
        mock_sys.platform = "linux"
        mount = Path("/run/media/user/DISC1")
        fake_files = [
            self._make_fake_file("00100.m2ts", 1024),
            self._make_fake_file("00101.m2ts", 2048),
        ]

        with (
            patch("app.core.extractor._find_linux_mount_point", return_value=mount),
            patch.object(Path, "is_dir", side_effect=lambda p=None: True),
            patch.object(Path, "glob", return_value=iter(fake_files)),
        ):
            result = compute_content_hash("/dev/sr0")

        assert result == self._expected_hash([1024, 2048])

    @patch("app.core.extractor.sys")
    def test_dvd_hash(self, mock_sys):
        mock_sys.platform = "linux"
        mount = Path("/run/media/user/DVD")
        fake_files = [self._make_fake_file("VTS_01_1.VOB", 512)]

        def fake_is_dir(self):
            return "VIDEO_TS" in str(self) and "BDMV" not in str(self)

        with (
            patch("app.core.extractor._find_linux_mount_point", return_value=mount),
            patch.object(Path, "is_dir", fake_is_dir),
            patch.object(Path, "glob", return_value=iter(fake_files)),
        ):
            result = compute_content_hash("/dev/sr0")

        assert result == self._expected_hash([512])

    @patch("app.core.extractor.sys")
    def test_no_disc_structure_returns_none(self, mock_sys):
        mock_sys.platform = "linux"
        mount = Path("/run/media/user/DISC1")

        with (
            patch("app.core.extractor._find_linux_mount_point", return_value=mount),
            patch.object(Path, "is_dir", return_value=False),
        ):
            assert compute_content_hash("/dev/sr0") is None

    @patch("app.core.extractor.sys")
    def test_empty_disc_structure_returns_none(self, mock_sys):
        mock_sys.platform = "linux"
        mount = Path("/run/media/user/DISC1")

        with (
            patch("app.core.extractor._find_linux_mount_point", return_value=mount),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "glob", return_value=iter([])),
        ):
            assert compute_content_hash("/dev/sr0") is None


class TestComputeContentHashWindows:
    def _make_fake_file(self, name: str, size: int) -> MagicMock:
        f = MagicMock()
        f.name = name
        f.stat.return_value.st_size = size
        return f

    def _expected_hash(self, sizes: list[int]) -> str:
        md5 = hashlib.md5()
        for s in sizes:
            md5.update(struct.pack("<q", s))
        return md5.hexdigest().upper()

    @patch("app.core.extractor.sys")
    def test_bluray_hash_windows(self, mock_sys):
        mock_sys.platform = "win32"
        fake_files = [self._make_fake_file("00100.m2ts", 4096)]

        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "glob", return_value=iter(fake_files)),
        ):
            result = compute_content_hash("E:")

        assert result == self._expected_hash([4096])

    @patch("app.core.extractor.sys")
    @pytest.mark.parametrize("drive_input", ["E:", "E", "E:\\"])
    def test_drive_letter_variants(self, mock_sys, drive_input):
        mock_sys.platform = "win32"
        fake_files = [self._make_fake_file("00100.m2ts", 100)]

        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "glob", return_value=iter(fake_files)),
        ):
            result = compute_content_hash(drive_input)

        assert result is not None

    @patch("app.core.extractor.sys")
    def test_windows_does_not_call_find_mount_point(self, mock_sys):
        mock_sys.platform = "win32"

        with (
            patch("app.core.extractor._find_linux_mount_point") as mock_find,
            patch.object(Path, "is_dir", return_value=False),
        ):
            compute_content_hash("E:")

        mock_find.assert_not_called()


class TestPlatformDispatch:
    @patch("app.core.extractor.sys")
    def test_linux_calls_find_mount_point(self, mock_sys):
        mock_sys.platform = "linux"
        with patch("app.core.extractor._find_linux_mount_point", return_value=None) as mock_find:
            compute_content_hash("/dev/sr0")
        mock_find.assert_called_once_with("/dev/sr0")

    @patch("app.core.extractor.sys")
    def test_darwin_uses_linux_path(self, mock_sys):
        mock_sys.platform = "darwin"
        with patch("app.core.extractor._find_linux_mount_point", return_value=None) as mock_find:
            compute_content_hash("/dev/disk2")
        mock_find.assert_called_once_with("/dev/disk2")
