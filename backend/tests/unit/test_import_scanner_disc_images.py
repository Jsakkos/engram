"""A backup folder or ISO is an import unit, not a folder to walk for MKVs."""

from app.core import import_scanner


def _bdmv(root, name):
    d = root / name
    (d / "BDMV" / "STREAM").mkdir(parents=True)
    (d / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"x" * 10)
    return d


def _video_ts(root, name):
    d = root / name
    (d / "VIDEO_TS").mkdir(parents=True)
    (d / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"x" * 10)
    return d


class TestDetection:
    def test_bdmv_folder_is_a_disc_image(self, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["Inception (2010)"]
        assert scan.disc_images[0].kind == "backup"

    def test_video_ts_folder_is_a_disc_image(self, tmp_path):
        _video_ts(tmp_path, "The Sweetest Thing")
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["The Sweetest Thing"]

    def test_iso_file_is_a_disc_image(self, tmp_path):
        (tmp_path / "Inception.iso").write_bytes(b"x" * 10)
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["Inception.iso"]
        assert scan.disc_images[0].kind == "iso"

    def test_picking_the_backup_folder_itself_yields_one_image(self, tmp_path):
        d = _bdmv(tmp_path, "Inception (2010)")
        scan = import_scanner.scan(d)
        assert len(scan.disc_images) == 1

    def test_nested_backups_are_all_found(self, tmp_path):
        _bdmv(tmp_path / "TV" / "Frasier (1993)" / "Season 01", "S01D01")
        _bdmv(tmp_path / "TV" / "Frasier (1993)" / "Season 01", "S01D02")
        scan = import_scanner.scan(tmp_path)
        assert sorted(d.name for d in scan.disc_images) == ["S01D01", "S01D02"]

    def test_a_tree_can_hold_both_kinds(self, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        loose = tmp_path / "Frasier" / "Season 01"
        loose.mkdir(parents=True)
        (loose / "Frasier - S01E01.mkv").write_bytes(b"x" * 10)
        scan = import_scanner.scan(tmp_path)
        assert len(scan.disc_images) == 1
        assert len(scan.units) == 1

    def test_m2ts_files_do_not_count_toward_the_file_budget(self, tmp_path):
        # The walk stops at the disc image, so a backup's thousands of stream
        # files cannot truncate a scan of the folder above it.
        d = _bdmv(tmp_path, "Big")
        for i in range(50):
            (d / "BDMV" / "STREAM" / f"{i:05d}.m2ts").write_bytes(b"x")
        scan = import_scanner.scan(tmp_path)
        assert scan.truncated is False
        assert scan.total_files == 0
