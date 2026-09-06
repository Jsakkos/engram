"""Backup destination naming and the free-space preflight."""

import shutil
from pathlib import Path
from unittest.mock import patch

from app.core.backup_paths import backup_destination, has_room_for_backup
from app.models import ContentType, DiscJob


def _job(**kw) -> DiscJob:
    base = {"drive_id": "E:", "volume_label": "DISC_LABEL"}
    base.update(kw)
    return DiscJob(**base)


def _usage(free: int) -> shutil._ntuple_diskusage:
    return shutil._ntuple_diskusage(0, 0, free)


class TestBackupDestination:
    def test_movie_uses_name_and_year(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception", tmdb_year=2010)
        assert backup_destination(job, "/b") == Path("/b/Movies/Inception (2010)")

    def test_movie_without_year_omits_the_parenthetical(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception")
        assert backup_destination(job, "/b") == Path("/b/Movies/Inception")

    def test_tv_uses_show_season_and_disc(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            disc_number=2,
        )
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Season 01/Disc 2")

    def test_tv_prefers_the_discdb_disc_slug(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            discdb_disc_slug="S01D02",
        )
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Season 01/S01D02")

    def test_tv_without_a_season_still_files_under_the_show(self):
        job = _job(content_type=ContentType.TV, tmdb_name="Frasier", tmdb_year=1993)
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Disc 1")

    def test_unidentified_falls_back_to_the_volume_label(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="THE_SWEETEST_THING")
        assert backup_destination(job, "/b") == Path("/b/Unidentified/THE_SWEETEST_THING")

    def test_unidentified_without_a_label_uses_the_job_id(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="")
        job.id = 42
        assert backup_destination(job, "/b") == Path("/b/Unidentified/job-42")

    def test_path_separators_in_a_title_cannot_escape_the_root(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="../../etc/passwd")
        dest = backup_destination(job, "/b")
        assert Path("/b") in dest.parents

    def test_empty_root_is_rejected(self):
        assert backup_destination(_job(), "") is None


class TestHasRoomForBackup:
    def test_enough_space_passes(self, tmp_path):
        with patch("shutil.disk_usage", return_value=_usage(100 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=40 * 1024**3) is True

    def test_margin_is_applied(self, tmp_path):
        # 40 GB needed x 1.15 = 46 GB; 42 GB free is not enough.
        with patch("shutil.disk_usage", return_value=_usage(42 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=40 * 1024**3) is False

    def test_unreadable_destination_fails_closed(self, tmp_path):
        with patch("shutil.disk_usage", side_effect=OSError("gone")):
            assert has_room_for_backup(tmp_path, needed_bytes=1) is False

    def test_unknown_size_falls_back_to_a_full_bluray(self, tmp_path):
        # needed_bytes 0 means the scan gave no sizes. Assume a 50 GB BD-DL
        # rather than waving the check through.
        with patch("shutil.disk_usage", return_value=_usage(10 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=0) is False
