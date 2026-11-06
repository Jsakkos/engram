"""Manual import accepts MP4/M4V/TS/M2TS, and a pick inside a disc backup is the backup."""

import pytest

from app.core import import_scanner
from app.core.media_formats import (
    is_importable_video,
    library_suffix,
    list_video_files,
    media_type_for,
)


def _touch(p, size=10):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


def _bdmv(root, name):
    d = root / name
    _touch(d / "BDMV" / "STREAM" / "00001.m2ts")
    _touch(d / "BDMV" / "STREAM" / "00002.m2ts")
    (d / "BDMV" / "PLAYLIST").mkdir(parents=True)
    return d


class TestMediaFormats:
    @pytest.mark.parametrize("name", ["a.mkv", "a.mp4", "a.m4v", "a.ts", "a.m2ts", "A.MP4"])
    def test_accepted(self, name):
        assert is_importable_video(name)

    @pytest.mark.parametrize("name", ["a.vob", "a.avi", "a.iso", "a.srt", "a", "mp4"])
    def test_rejected(self, name):
        assert not is_importable_video(name)

    def test_library_suffix_keeps_the_source_container(self):
        assert library_suffix("x/Show_t00.mkv") == ".mkv"
        assert library_suffix("x/Episode.MP4") == ".mp4"
        assert library_suffix("x/rec.ts") == ".ts"

    def test_library_suffix_falls_back_to_mkv(self):
        assert library_suffix("x/no_extension") == ".mkv"
        assert library_suffix("x/odd.bin") == ".mkv"

    def test_media_types(self):
        assert media_type_for("a.mkv") == "video/x-matroska"
        assert media_type_for("a.MP4") == "video/mp4"
        assert media_type_for("a.m2ts") == "video/mp2t"
        assert media_type_for("a.unknown") == "video/x-matroska"

    def test_list_video_files_is_flat_sorted_and_filtered(self, tmp_path):
        _touch(tmp_path / "b.mp4")
        _touch(tmp_path / "a.mkv")
        _touch(tmp_path / "notes.txt")
        _touch(tmp_path / "sub" / "c.mkv")
        assert [p.name for p in list_video_files(tmp_path)] == ["a.mkv", "b.mp4"]

    def test_list_video_files_on_missing_dir(self, tmp_path):
        assert list_video_files(tmp_path / "missing") == []


class TestScannerAcceptsNewFormats:
    def test_folder_of_mixed_containers_is_one_unit(self, tmp_path):
        season = tmp_path / "Psych" / "Season 1"
        for name in ("e1.mkv", "e2.mp4", "e3.M4V", "e4.ts", "e5.m2ts"):
            _touch(season / name)
        _touch(season / "e6.avi")  # not supported
        _touch(season / "VTS_01_1.VOB")  # loose VOBs are not episodes

        scan = import_scanner.scan(tmp_path / "Psych")

        assert len(scan.units) == 1
        assert sorted(f.name for f in scan.units[0].files) == [
            "e1.mkv",
            "e2.mp4",
            "e3.M4V",
            "e4.ts",
            "e5.m2ts",
        ]
        assert scan.units[0].season == 1

    def test_single_mp4_file_pick(self, tmp_path):
        f = _touch(tmp_path / "Frasier" / "episode.mp4")
        scan = import_scanner.scan(f)
        assert len(scan.units) == 1
        assert scan.units[0].files == [f]
        assert scan.units[0].show_name == "Frasier"

    def test_single_unsupported_file_pick_is_empty(self, tmp_path):
        f = _touch(tmp_path / "Frasier" / "episode.avi")
        scan = import_scanner.scan(f)
        assert scan.units == [] and scan.disc_images == []

    def test_loose_m2ts_outside_a_backup_is_a_file(self, tmp_path):
        # A clip copied out of a disc (no BDMV above it) is the user's file to file.
        f = _touch(tmp_path / "Movie" / "00001.m2ts")
        scan = import_scanner.scan(tmp_path / "Movie")
        assert scan.disc_images == []
        assert scan.units[0].files == [f]


class TestPickInsideBackupRedirects:
    """BDMV/STREAM clips are fragments; the backup holding them is the import."""

    @pytest.mark.parametrize(
        "pick",
        [
            ("BDMV",),
            ("BDMV", "STREAM"),
            ("BDMV", "STREAM", "00001.m2ts"),
        ],
    )
    def test_redirects_to_backup_root(self, tmp_path, pick):
        backup = _bdmv(tmp_path, "Inception (2010)")
        scan = import_scanner.scan(backup.joinpath(*pick))

        assert scan.units == []
        assert len(scan.disc_images) == 1
        image = scan.disc_images[0]
        assert image.path == backup
        assert image.name == "Inception (2010)"
        assert image.kind == "backup"
        assert image.total_bytes == 20

    def test_lowercase_bdmv_folder_redirects(self, tmp_path):
        backup = tmp_path / "Disc"
        _touch(backup / "bdmv" / "stream" / "00001.m2ts")
        scan = import_scanner.scan(backup / "bdmv" / "stream")
        assert [d.path for d in scan.disc_images] == [backup]

    def test_video_ts_pick_redirects(self, tmp_path):
        backup = tmp_path / "Old DVD"
        _touch(backup / "VIDEO_TS" / "VTS_01_1.VOB")
        scan = import_scanner.scan(backup / "VIDEO_TS")
        assert [d.path for d in scan.disc_images] == [backup]

    def test_enclosing_disc_backup_none_outside_a_backup(self, tmp_path):
        f = _touch(tmp_path / "Show" / "Season 1" / "e1.m2ts")
        assert import_scanner.enclosing_disc_backup(f) is None
