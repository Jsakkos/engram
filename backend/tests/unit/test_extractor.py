"""Unit tests for MakeMKVExtractor pure parsing + helper functions.

Covers robot-mode output parsing, duration/size/resolution parsing, drive-spec
normalization, content-hash computation, and process bookkeeping — none of which
require launching a real makemkvcon. The subprocess-driven scan/rip orchestration
is left to integration tests.
"""

import hashlib
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import pytest

from app.core.extractor import (
    MakeMKVExtractor,
    _extract_created_mkv,
    _files_to_ignore,
    _find_linux_mount_point,
    _is_stalled,
    _safe_callback,
    _save_makemkv_log,
    _to_drive_spec,
    compute_content_hash,
    title_index_from_filename,
)


def _extractor() -> MakeMKVExtractor:
    return MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))


@pytest.mark.unit
class TestToDriveSpec:
    @pytest.mark.parametrize(
        "drive, expected",
        [
            ("E:", "dev:E:"),
            ("/dev/sr0", "dev:/dev/sr0"),
            ("disc:0", "disc:0"),
        ],
    )
    def test_normalization(self, drive, expected):
        assert _to_drive_spec(drive) == expected


@pytest.mark.unit
class TestExtractCreatedMkv:
    def test_extracts_basename_into_output_dir(self, tmp_path):
        line = 'something created "/disc/path/Movie_t01.mkv" done'
        assert _extract_created_mkv(line, tmp_path) == tmp_path / "Movie_t01.mkv"

    def test_returns_none_without_created_keyword(self, tmp_path):
        assert _extract_created_mkv('saved "Movie_t01.mkv"', tmp_path) is None

    def test_returns_none_without_mkv(self, tmp_path):
        assert _extract_created_mkv("created something else", tmp_path) is None

    def test_returns_none_when_no_quoted_name(self, tmp_path):
        assert _extract_created_mkv("created a .mkv but unquoted", tmp_path) is None


@pytest.mark.unit
class TestTitleIndexFromFilename:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("B1_t00.mkv", 0),
            ("E1_t03.mkv", 3),
            ("Show - Season 3_t12.mkv", 12),
            ("title_07.mkv", 7),
            ("title09.mkv", 9),
            ("weird_name.mkv", None),
            ("not_an_mkv_t01.txt", None),
        ],
    )
    def test_parses_makemkv_title_index(self, name, expected):
        assert title_index_from_filename(name) == expected


@pytest.mark.unit
class TestFilesToIgnore:
    """A subset re-rip must ignore pre-existing files for OTHER titles."""

    def _seed(self, tmp_path, *names):
        for n in names:
            (tmp_path / n).write_bytes(b"x")

    def test_full_rip_ignores_nothing(self, tmp_path):
        # title_indices=None → 'rip all' into a fresh dir; ignore nothing.
        self._seed(tmp_path, "B1_t00.mkv")
        assert _files_to_ignore(tmp_path, None) == set()

    def test_single_track_rerip_ignores_other_titles_files(self, tmp_path):
        # Re-ripping title 3 into a dir holding titles 0/1/2's finished files.
        self._seed(tmp_path, "B1_t00.mkv", "C1_t01.mkv", "D1_t02.mkv")
        assert _files_to_ignore(tmp_path, [3]) == {
            "B1_t00.mkv",
            "C1_t01.mkv",
            "D1_t02.mkv",
        }

    def test_target_titles_own_leftover_is_not_ignored(self, tmp_path):
        # A leftover partial of the title we ARE re-ripping must be re-detected
        # (and cleaned) normally, not shielded.
        self._seed(tmp_path, "B1_t00.mkv", "E1_t03.mkv")
        assert _files_to_ignore(tmp_path, [3]) == {"B1_t00.mkv"}

    def test_unparseable_preexisting_file_is_ignored(self, tmp_path):
        # A foreign file we can't map to a title index still predates our rip.
        self._seed(tmp_path, "stray.mkv")
        assert _files_to_ignore(tmp_path, [3]) == {"stray.mkv"}


@pytest.mark.unit
class TestSafeCallback:
    def test_invokes_callback(self):
        cb = Mock()
        _safe_callback(cb, 1, 2, label="x")
        cb.assert_called_once_with(1, 2)

    def test_swallows_exceptions(self):
        cb = Mock(side_effect=ValueError("boom"))
        # Must not raise.
        _safe_callback(cb, label="x")
        cb.assert_called_once()


@pytest.mark.unit
class TestSaveMakemkvLog:
    def test_writes_content(self, tmp_path):
        log = tmp_path / "logs" / "scan.log"
        _save_makemkv_log(log, "hello")
        assert log.read_text() == "hello"

    def test_oserror_is_swallowed(self, tmp_path):
        # Passing a directory as the log path makes write_text raise OSError.
        _save_makemkv_log(tmp_path, "hello")  # must not raise


@pytest.mark.unit
class TestFindLinuxMountPoint:
    def test_returns_mount_point(self):
        mounts = "/dev/sda1 / ext4 rw 0 0\n/dev/sr0 /media/disc iso9660 ro 0 0\n"
        with patch("builtins.open", mock_open(read_data=mounts)):
            assert _find_linux_mount_point("/dev/sr0") == Path("/media/disc")

    def test_returns_none_when_not_found(self):
        with patch("builtins.open", mock_open(read_data="/dev/sda1 / ext4 rw 0 0\n")):
            assert _find_linux_mount_point("/dev/sr0") is None

    def test_returns_none_on_read_error(self):
        with patch("builtins.open", side_effect=OSError("nope")):
            assert _find_linux_mount_point("/dev/sr0") is None


@pytest.mark.unit
class TestIsStalled:
    """The stall decision is liveness-based: a rip is stalled only when there has
    been no progress (file growth OR MakeMKV stdout) for `timeout` seconds. A small
    track that finished writing but is still emitting progress must NOT be stalled.
    """

    def test_recent_progress_is_not_stalled(self):
        # Last progress 10s ago, 120s timeout -> healthy.
        assert _is_stalled(now=1000.0, last_progress=990.0, timeout=120.0) is False

    def test_no_progress_past_timeout_is_stalled(self):
        # 150s since last progress, 120s timeout -> stalled.
        assert _is_stalled(now=1000.0, last_progress=850.0, timeout=120.0) is True

    def test_exactly_at_timeout_is_stalled(self):
        assert _is_stalled(now=1000.0, last_progress=880.0, timeout=120.0) is True


@pytest.mark.unit
class TestComputeContentHash:
    @staticmethod
    def _expected(sizes_by_name_sorted):
        md5 = hashlib.md5()
        for size in sizes_by_name_sorted:
            md5.update(struct.pack("<q", size))
        return md5.hexdigest().upper()

    @pytest.fixture(autouse=True)
    def _force_linux(self, monkeypatch):
        # These tests drive the mount-point (Linux) code path; on a Windows
        # runner compute_content_hash would otherwise take the drive-letter
        # branch and ignore the _find_linux_mount_point patch.
        monkeypatch.setattr("app.core.extractor.sys.platform", "linux")

    def test_bluray_hash(self, tmp_path):
        stream = tmp_path / "BDMV" / "STREAM"
        stream.mkdir(parents=True)
        (stream / "a.m2ts").write_bytes(b"x" * 100)
        (stream / "b.m2ts").write_bytes(b"x" * 200)
        with patch("app.core.extractor._find_linux_mount_point", return_value=tmp_path):
            result = compute_content_hash("/dev/sr0")
        assert result == self._expected([100, 200])

    def test_dvd_hash(self, tmp_path):
        video_ts = tmp_path / "VIDEO_TS"
        video_ts.mkdir()
        (video_ts / "VTS_01_1.VOB").write_bytes(b"x" * 50)
        with patch("app.core.extractor._find_linux_mount_point", return_value=tmp_path):
            result = compute_content_hash("/dev/sr0")
        assert result == self._expected([50])

    def test_not_mounted_returns_none(self):
        with patch("app.core.extractor._find_linux_mount_point", return_value=None):
            assert compute_content_hash("/dev/sr0") is None

    def test_no_disc_structure_returns_none(self, tmp_path):
        with patch("app.core.extractor._find_linux_mount_point", return_value=tmp_path):
            assert compute_content_hash("/dev/sr0") is None

    def test_empty_stream_dir_returns_none(self, tmp_path):
        (tmp_path / "BDMV" / "STREAM").mkdir(parents=True)
        with patch("app.core.extractor._find_linux_mount_point", return_value=tmp_path):
            assert compute_content_hash("/dev/sr0") is None

    def test_windows_branch_without_structure_returns_none(self):
        with patch("app.core.extractor.sys.platform", "win32"):
            assert compute_content_hash("Z:") is None


@pytest.mark.unit
class TestParseDiscInfo:
    SAMPLE = "\n".join(
        [
            'CINFO:1,6209,"ignored attr"',
            'CINFO:2,0,"INCEPTION"',
            'TINFO:0,2,0,"Inception"',
            'TINFO:0,9,0,"2:28:00"',
            'TINFO:0,10,0,"30.1 GB"',
            'TINFO:0,8,0,"24"',
            'TINFO:0,16,0,"00800.m2ts"',
            'TINFO:0,19,0,"1920x1080"',
            'TINFO:0,25,0,"1"',
            'TINFO:0,26,0,"1,2,3"',
            'TINFO:0,27,0,"Inception_t00.mkv"',
            'TINFO:0,28,0,"eng"',
            'TINFO:1,2,0,"Extra"',
            'TINFO:1,9,0,"0:05:00"',
            'TINFO:1,8,0,"notanumber"',  # ValueError path for chapters
            'TINFO:1,25,0,"bad"',  # ValueError path for segment count
        ]
    )

    def test_parses_disc_name_and_title_attrs(self):
        titles, disc_name = _extractor()._parse_disc_info(self.SAMPLE)
        assert disc_name == "INCEPTION"
        by_idx = {t.index: t for t in titles}
        assert set(by_idx) == {0, 1}

        t0 = by_idx[0]
        assert t0.name == "Inception"
        assert t0.duration_seconds == 2 * 3600 + 28 * 60
        assert t0.size_bytes == int(30.1 * 1000**3)
        assert t0.chapter_count == 24
        assert t0.source_filename == "00800.m2ts"
        assert t0.video_resolution == "1080p"
        assert t0.segment_count == 1
        assert t0.segment_map == "1,2,3"
        assert t0.disc_title == "Inception_t00.mkv"

    def test_invalid_numeric_attrs_are_ignored(self):
        titles, _ = _extractor()._parse_disc_info(self.SAMPLE)
        t1 = {t.index: t for t in titles}[1]
        assert t1.chapter_count == 0  # "notanumber" left default
        assert t1.segment_count == 0  # "bad" left default

    def test_empty_output(self):
        titles, disc_name = _extractor()._parse_disc_info("")
        assert titles == []
        assert disc_name == ""

    def test_cinfo_other_than_attr_2_is_ignored(self):
        titles, disc_name = _extractor()._parse_disc_info('CINFO:1,0,"foo"')
        assert disc_name == ""
        assert titles == []

    def test_scan_time_output_index_expression(self):
        """Pins the exact expression identification_coordinator.py uses when
        building a DiscTitle from a scanned TitleInfo — output_index is the
        native _tNN number parsed from the suggested filename (disc_title),
        or None when MakeMKV supplied no suggested filename (issue #517: this
        is what lets resolve_title_from_filename match ripped files correctly
        even when that native number doesn't equal the scan-order index).
        """
        titles, _ = _extractor()._parse_disc_info(self.SAMPLE)
        t0 = {t.index: t for t in titles}[0]
        output_index = title_index_from_filename(t0.disc_title) if t0.disc_title else None
        assert output_index == 0  # this sample's disc_title is "Inception_t00.mkv"

        t1 = {t.index: t for t in titles}[1]  # has no TINFO:1,27 line -> disc_title == ""
        output_index_t1 = title_index_from_filename(t1.disc_title) if t1.disc_title else None
        assert output_index_t1 is None


@pytest.mark.unit
class TestParseDuration:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1:30:45", 5445),
            ("30:45", 1845),
            ("90", 90),
            ("garbage", 0),
        ],
    )
    def test_parse(self, value, expected):
        assert _extractor()._parse_duration(value) == expected


@pytest.mark.unit
class TestParseSize:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("12.5 GB", int(12.5 * 1000**3)),
            ("500 MB", 500 * 1000**2),
            ("2 KB", 2000),
            ("100 B", 100),
            ("12 gb", 12 * 1000**3),
            ("garbage", 0),
        ],
    )
    def test_parse(self, value, expected):
        assert _extractor()._parse_size(value) == expected


@pytest.mark.unit
class TestParseResolution:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("3840x2160", "4K"),
            ("1920x1080 (16:9)", "1080p"),
            ("1280x720", "720p"),
            ("720x480", "480p"),
            ("", ""),
            ("no-numbers", "Unknown"),
        ],
    )
    def test_parse(self, value, expected):
        assert _extractor()._parse_resolution(value) == expected


@pytest.mark.unit
class TestDriveLock:
    def test_same_lock_for_equivalent_drive_specs(self):
        ex = _extractor()
        assert ex._get_drive_lock("F:") is ex._get_drive_lock("dev:F:")

    def test_different_drives_get_different_locks(self):
        ex = _extractor()
        assert ex._get_drive_lock("F:") is not ex._get_drive_lock("disc:0")


@pytest.mark.unit
class TestCancel:
    def test_cancel_marks_job_and_terminates_process(self):
        ex = _extractor()
        proc = Mock()
        ex._processes[5] = proc
        ex.cancel(5)
        assert 5 in ex._cancelled_jobs
        proc.terminate.assert_called_once()

    def test_cancel_without_process_is_safe(self):
        ex = _extractor()
        ex.cancel(99)  # must not raise
        assert 99 in ex._cancelled_jobs

    def test_cancel_swallows_terminate_error(self):
        ex = _extractor()
        proc = Mock()
        proc.terminate.side_effect = ProcessLookupError()
        ex._processes[7] = proc
        ex.cancel(7)  # must not raise
        assert 7 in ex._cancelled_jobs


@pytest.mark.unit
class TestMakemkvPathProperty:
    def test_override_is_returned(self):
        ex = MakeMKVExtractor(makemkv_path=Path("/custom/makemkvcon"))
        assert ex.makemkv_path == Path("/custom/makemkvcon")

    def test_lazy_loads_from_config(self):
        ex = MakeMKVExtractor()
        with patch(
            "app.services.config_service.get_config_sync",
            return_value=SimpleNamespace(makemkv_path="/usr/bin/makemkvcon"),
        ):
            assert ex.makemkv_path == Path("/usr/bin/makemkvcon")


@pytest.mark.unit
class TestScanDiscSubprocessParsing:
    """scan_disc with the subprocess stubbed — exercises the parse + error paths."""

    async def test_successful_scan_parses_titles(self):
        ex = _extractor()
        completed = subprocess.CompletedProcess(
            args=["makemkvcon"],
            returncode=0,
            stdout=TestParseDiscInfo.SAMPLE,
            stderr="",
        )
        with patch("app.core.extractor.subprocess.Popen") as popen:
            proc = popen.return_value
            proc.communicate.return_value = (completed.stdout, "")
            proc.returncode = 0
            titles, disc_name = await ex.scan_disc("/dev/sr0")
        assert disc_name == "INCEPTION"
        assert len(titles) == 2

    async def test_nonzero_returncode_returns_empty(self):
        ex = _extractor()
        with patch("app.core.extractor.subprocess.Popen") as popen:
            proc = popen.return_value
            proc.communicate.return_value = ("", "fatal error")
            proc.returncode = 1
            titles, disc_name = await ex.scan_disc("/dev/sr0")
        assert titles == []
        assert disc_name == ""
