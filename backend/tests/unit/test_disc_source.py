"""Unit tests for the DiscSource value object."""

from unittest.mock import patch

import pytest

from app.core.disc_source import DiscSource, SourceKind, parse_drive_listing, resolve_disc_index


class TestParse:
    def test_bare_drive_letter_is_a_drive(self):
        s = DiscSource.parse("E:")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_dev_prefixed_drive_passes_through(self):
        s = DiscSource.parse("dev:E:")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_linux_device_is_a_drive(self):
        s = DiscSource.parse("/dev/sr0")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:/dev/sr0"

    def test_disc_index_is_a_drive(self):
        s = DiscSource.parse("disc:0")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "disc:0"

    def test_file_spec_is_a_backup(self, tmp_path):
        s = DiscSource.parse(f"file:{tmp_path}")
        assert s.kind is SourceKind.BACKUP
        assert s.spec == f"file:{tmp_path}"

    def test_iso_spec_is_an_iso(self, tmp_path):
        iso = tmp_path / "disc.iso"
        s = DiscSource.parse(f"iso:{iso}")
        assert s.kind is SourceKind.ISO
        assert s.spec == f"iso:{iso}"

    def test_windows_path_in_file_spec_survives_the_colon(self):
        # "file:D:\\backups\\x" must not be split on the drive-letter colon.
        s = DiscSource.parse("file:D:\\backups\\Inception (2010)")
        assert s.kind is SourceKind.BACKUP
        assert s.value == "D:\\backups\\Inception (2010)"

    def test_empty_spec_is_rejected(self):
        with pytest.raises(ValueError):
            DiscSource.parse("")


class TestPhysicality:
    def test_drive_is_physical(self):
        assert DiscSource.parse("E:").is_physical is True

    def test_backup_is_not_physical(self):
        assert DiscSource.parse("file:/backups/x").is_physical is False

    def test_iso_is_not_physical(self):
        assert DiscSource.parse("iso:/backups/x.iso").is_physical is False


class TestLockKey:
    def test_drive_forms_share_one_lock_key(self):
        assert DiscSource.parse("E:").lock_key == DiscSource.parse("dev:E:").lock_key

    def test_trailing_separator_does_not_split_the_lock(self):
        assert DiscSource.parse("dev:E:\\").lock_key == DiscSource.parse("E:").lock_key

    def test_a_backup_does_not_share_a_drive_lock(self):
        drive = DiscSource.parse("E:")
        backup = DiscSource.parse("file:E:\\backups\\x")
        assert backup.lock_key != drive.lock_key

    def test_two_backups_at_the_same_path_share_a_lock(self):
        a = DiscSource.parse("file:/backups/x")
        b = DiscSource.parse("file:/backups/x")
        assert a.lock_key == b.lock_key


class TestFromJob:
    def test_source_spec_wins_when_present(self):
        job = _FakeJob(drive_id="E:", source_spec="file:/backups/x")
        s = DiscSource.from_job(job)
        assert s.kind is SourceKind.BACKUP
        assert s.value == "/backups/x"

    def test_legacy_row_falls_back_to_drive_id(self):
        job = _FakeJob(drive_id="E:", source_spec=None)
        s = DiscSource.from_job(job)
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_import_drive_id_without_source_spec_is_rejected(self):
        # A manual-import job has no MakeMKV source at all; asking for one is a bug.
        job = _FakeJob(drive_id="import", source_spec=None)
        with pytest.raises(ValueError):
            DiscSource.from_job(job)


class _FakeJob:
    def __init__(self, drive_id: str, source_spec: str | None):
        self.drive_id = drive_id
        self.source_spec = source_spec


# Real makemkvcon -r info disc:9999 output shape. DRV lines are:
# DRV:index,visible,enabled,flags,"drive name","disc name","device"
_LISTING = (
    'DRV:0,2,999,1,"BD-RE HL-DT-ST BH16NS40 1.05","THE_SWEETEST_THING","E:"\n'
    'DRV:1,0,999,0,"","",""\n'
    'DRV:2,2,999,1,"HL-DT-ST DVDRAM GH24","INCEPTION","F:"\n'
    "TCOUNT:0\n"
)


class TestParseDriveListing:
    def test_maps_device_to_index(self):
        assert parse_drive_listing(_LISTING) == {"E:": 0, "F:": 2}

    def test_ignores_empty_drive_slots(self):
        assert "" not in parse_drive_listing(_LISTING)

    def test_empty_output_maps_nothing(self):
        assert parse_drive_listing("") == {}

    def test_malformed_line_is_skipped_not_raised(self):
        assert parse_drive_listing('DRV:garbage\nDRV:0,2,999,1,"n","d","E:"\n') == {"E:": 0}


class TestResolveDiscIndex:
    @pytest.mark.asyncio
    async def test_resolves_a_known_drive(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("E:", makemkv_path="mmk") == "disc:0"

    @pytest.mark.asyncio
    async def test_case_insensitive_on_windows_letters(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("e:", makemkv_path="mmk") == "disc:0"

    @pytest.mark.asyncio
    async def test_unknown_drive_returns_none(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("Z:", makemkv_path="mmk") is None

    @pytest.mark.asyncio
    async def test_subprocess_failure_returns_none(self):
        with patch("app.core.disc_source._run_drive_listing", side_effect=OSError("boom")):
            assert await resolve_disc_index("E:", makemkv_path="mmk") is None
