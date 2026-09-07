"""The Extractor accepts a DiscSource, not just a drive letter."""

from pathlib import Path

import pytest

from app.core.disc_source import DiscSource
from app.core.extractor import MakeMKVExtractor, _to_source_spec


def _extractor() -> MakeMKVExtractor:
    return MakeMKVExtractor(makemkv_path=Path("mmk"))


@pytest.mark.unit
class TestToSourceSpec:
    def test_bare_drive_gets_a_dev_prefix(self):
        assert _to_source_spec("E:") == "dev:E:"

    def test_disc_index_passes_through(self):
        assert _to_source_spec("disc:0") == "disc:0"

    def test_file_spec_passes_through(self):
        assert _to_source_spec("file:/backups/x") == "file:/backups/x"

    def test_disc_source_object_is_accepted(self):
        assert _to_source_spec(DiscSource.for_backup("/backups/x")) == "file:/backups/x"

    def test_iso_is_handed_to_makemkv_as_a_file_source(self):
        assert _to_source_spec(DiscSource.parse("iso:/b/x.iso")) == "file:/b/x.iso"

    def test_an_unrecognized_spec_is_rejected(self):
        # The old _to_drive_spec silently prefixed dev: to anything, which
        # turned a typo into a confusing MakeMKV error much later.
        with pytest.raises(ValueError):
            _to_source_spec("not a drive or a path")


@pytest.mark.unit
class TestSourceLocking:
    def test_drive_forms_share_one_lock(self):
        ex = _extractor()
        assert ex._get_source_lock("E:") is ex._get_source_lock("dev:E:")

    def test_a_backup_does_not_take_the_drive_lock(self):
        # Regression guard: extracting from a backup must not block the optical
        # drive at the same letter from scanning the next disc.
        ex = _extractor()
        assert ex._get_source_lock("E:") is not ex._get_source_lock("file:E:\\backups\\x")

    def test_the_same_backup_path_shares_one_lock(self):
        ex = _extractor()
        assert ex._get_source_lock("file:/b/x") is ex._get_source_lock("file:/b/x")
