"""Backup destination naming and the free-space preflight."""

import types
from pathlib import Path
from unittest.mock import patch

from app.core.backup_paths import backup_destination, has_room_for_backup
from app.models import AppConfig, ContentType, DiscJob


def _job(**kw) -> DiscJob:
    base = {"drive_id": "E:", "volume_label": "DISC_LABEL"}
    base.update(kw)
    return DiscJob(**base)


def _config(**kw) -> AppConfig:
    base = {"backup_path": "/b"}
    base.update(kw)
    return AppConfig(**base)


def _usage(free: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(total=0, used=0, free=free)


class TestBackupDestination:
    def test_movie_uses_name_and_year(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception", tmdb_year=2010)
        assert backup_destination(job, _config()) == Path("/b/Movies/Inception (2010)")

    def test_movie_without_year_omits_the_parenthetical(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception")
        assert backup_destination(job, _config()) == Path("/b/Movies/Inception")

    def test_tv_uses_show_season_and_disc(self):
        # The default naming_tv_show_format is "{show}" (no year, matching the
        # Organizer's own default), so the backup mirrors that exactly.
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            disc_number=2,
        )
        assert backup_destination(job, _config()) == Path("/b/TV/Frasier/Season 01/Disc 2")

    def test_tv_prefers_the_discdb_disc_slug(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            discdb_disc_slug="S01D02",
        )
        assert backup_destination(job, _config()) == Path("/b/TV/Frasier/Season 01/S01D02")

    def test_tv_without_a_season_still_files_under_the_show(self):
        job = _job(content_type=ContentType.TV, tmdb_name="Frasier", tmdb_year=1993)
        assert backup_destination(job, _config()) == Path("/b/TV/Frasier/Disc 1")

    def test_tv_show_naming_format_with_year_is_honored(self):
        # A user who has opted the show folder into carrying the year sees
        # that reflected in the backup shelf too.
        job = _job(content_type=ContentType.TV, tmdb_name="Frasier", tmdb_year=1993)
        config = _config(naming_tv_show_format="{show} ({year})")
        assert backup_destination(job, config) == Path("/b/TV/Frasier (1993)/Disc 1")

    def test_unidentified_falls_back_to_the_volume_label(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="THE_SWEETEST_THING")
        assert backup_destination(job, _config()) == Path("/b/Unidentified/THE_SWEETEST_THING")

    def test_unidentified_without_a_label_uses_the_job_id(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="")
        job.id = 42
        assert backup_destination(job, _config()) == Path("/b/Unidentified/job-42")

    def test_path_separators_in_a_title_cannot_escape_the_root(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="../../etc/passwd")
        dest = backup_destination(job, _config())
        assert Path("/b") in dest.parents

    def test_empty_root_is_rejected(self):
        assert backup_destination(_job(), _config(backup_path="")) is None

    def test_custom_movie_naming_format_is_used(self):
        # A format clearly different from the default ("{title} ({year})") so
        # this is a pointed regression guard: the backup path must come from
        # the Organizer's format_movie_folder, not a hardcoded shape.
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception", tmdb_year=2010)
        config = _config(naming_movie_format="{title} [{year}]")
        assert backup_destination(job, config) == Path("/b/Movies/Inception [2010]")

    def test_custom_season_naming_format_is_used(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            disc_number=2,
        )
        config = _config(naming_season_format="S{season:02d}")
        assert backup_destination(job, config) == Path("/b/TV/Frasier/S01/Disc 2")

    def test_movie_title_that_sanitizes_to_empty_falls_back_to_job_id(self):
        # "???" is entirely made of Windows-illegal characters, so it sanitizes
        # to "". That must not collapse to a bare "Unknown" folder (two such
        # discs would collide); it must disambiguate by job id instead.
        job = _job(content_type=ContentType.MOVIE, tmdb_name="???")
        job.id = 7
        assert backup_destination(job, _config()) == Path("/b/Movies/job-7")

    def test_tv_disc_with_no_resolvable_name_lands_in_unidentified(self):
        # No tmdb_name/detected_title means `name` is falsy, so the TV branch
        # never triggers regardless of detected_season/disc_number: those are
        # deliberately dropped and the disc files under Unidentified by its
        # volume label (or job id) instead.
        job = _job(
            content_type=ContentType.TV,
            volume_label="SHOW_S1D1",
            detected_season=1,
            disc_number=1,
        )
        assert backup_destination(job, _config()) == Path("/b/Unidentified/SHOW_S1D1")

    def test_a_disc_genuinely_labelled_unknown_keeps_that_label(self):
        # A real volume label of "Unknown" sanitizes to "Unknown" (no illegal
        # characters), so it must be used as-is rather than treated as if
        # sanitization had emptied it out.
        job = _job(content_type=ContentType.UNKNOWN, volume_label="Unknown")
        assert backup_destination(job, _config()) == Path("/b/Unidentified/Unknown")


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
