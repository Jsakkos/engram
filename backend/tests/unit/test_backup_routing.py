"""Identification hands a disc to BACKING_UP only when it should."""

from app.models import AppConfig, JobState
from app.services.identification_coordinator import next_state_after_identify


class TestNextStateAfterIdentify:
    def test_backup_disabled_goes_straight_to_ripping(self):
        cfg = AppConfig(backup_before_rip=False, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.RIPPING

    def test_backup_enabled_goes_to_backing_up(self):
        cfg = AppConfig(backup_before_rip=True, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.BACKING_UP

    def test_backup_enabled_without_a_root_still_rips(self):
        # No root means nothing to write to; do not enter a phase that can only
        # immediately fall back.
        cfg = AppConfig(backup_before_rip=True, backup_path="")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.RIPPING

    def test_an_import_job_never_backs_up(self):
        # It already is a backup.
        cfg = AppConfig(backup_before_rip=True, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="import") is JobState.RIPPING

    def test_a_missing_config_rips(self):
        assert next_state_after_identify(None, drive_id="E:") is JobState.RIPPING
