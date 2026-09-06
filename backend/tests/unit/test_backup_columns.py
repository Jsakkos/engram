"""The backup columns exist, default correctly, and read safely on legacy rows."""

from app.models import AppConfig, DiscJob, JobState


class TestDiscJobBackupColumns:
    def test_new_job_has_no_source_spec(self):
        # None means "legacy or drive-sourced"; DiscSource.from_job falls back.
        assert DiscJob(drive_id="E:").source_spec is None

    def test_new_job_has_no_backup_path_or_status(self):
        job = DiscJob(drive_id="E:")
        assert job.backup_path is None
        assert job.backup_status is None


class TestAppConfigBackupFields:
    def test_backup_is_off_by_default(self):
        # Opt-in: an upgraded row with NULL must read as disabled, which is why
        # the column carries server_default 0 (see discord_notify_ripped).
        assert AppConfig().backup_before_rip is False

    def test_backup_path_defaults_empty(self):
        assert AppConfig().backup_path == ""

    def test_backing_up_has_its_own_timeout(self):
        # A 40 GB sequential copy cannot share the ripping phase ceiling.
        assert AppConfig().timeout_backing_up_seconds > 0


class TestJobState:
    def test_backing_up_exists_and_is_not_terminal(self):
        from app.models import TERMINAL_JOB_STATES

        assert JobState.BACKING_UP.value == "backing_up"
        assert JobState.BACKING_UP not in TERMINAL_JOB_STATES
