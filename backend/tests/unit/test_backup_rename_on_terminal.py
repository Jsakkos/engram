"""Reconciling a disc backup's folder with a post-backup identity correction."""

from app.core.backup_paths import reconcile_backup_location
from app.models import AppConfig, ContentType, DiscJob


def _job(**kw) -> DiscJob:
    base = {"drive_id": "E:", "volume_label": "DISC_LABEL"}
    base.update(kw)
    return DiscJob(**base)


def _config(**kw) -> AppConfig:
    base = {"backup_path": "/b"}
    base.update(kw)
    return AppConfig(**base)


class TestReconcileBackupLocation:
    def test_renames_when_identity_changed(self, tmp_path):
        root = tmp_path
        old_dir = root / "TV" / "Wrong Show" / "Season 01" / "Disc 1"
        old_dir.mkdir(parents=True)
        dummy = old_dir / "backup.mkv"
        dummy.write_text("data")

        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Correct Show",
            tmdb_year=2001,
            detected_season=1,
            disc_number=1,
            backup_path=str(old_dir),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        expected = root / "TV" / "Correct Show" / "Season 01" / "Disc 1"
        assert result == expected
        assert expected.is_dir()
        assert (expected / "backup.mkv").exists()
        assert not old_dir.exists()

    def test_noop_when_destination_matches(self, tmp_path):
        root = tmp_path
        dest_dir = root / "TV" / "Correct Show" / "Season 01" / "Disc 1"
        dest_dir.mkdir(parents=True)
        dummy = dest_dir / "backup.mkv"
        dummy.write_text("data")

        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Correct Show",
            detected_season=1,
            disc_number=1,
            backup_path=str(dest_dir),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        assert result is None
        assert dest_dir.is_dir()
        assert (dest_dir / "backup.mkv").exists()

    def test_noop_when_target_exists(self, tmp_path):
        root = tmp_path
        old_dir = root / "TV" / "Wrong Show" / "Season 01" / "Disc 1"
        old_dir.mkdir(parents=True)
        (old_dir / "backup.mkv").write_text("old data")

        new_dir = root / "TV" / "Correct Show" / "Season 01" / "Disc 1"
        new_dir.mkdir(parents=True)
        (new_dir / "backup.mkv").write_text("existing data")

        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Correct Show",
            detected_season=1,
            disc_number=1,
            backup_path=str(old_dir),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        assert result is None
        assert old_dir.is_dir()
        assert (old_dir / "backup.mkv").read_text() == "old data"
        assert new_dir.is_dir()
        assert (new_dir / "backup.mkv").read_text() == "existing data"

    def test_noop_when_stored_path_missing(self, tmp_path):
        root = tmp_path
        missing = root / "TV" / "Wrong Show" / "Season 01" / "Disc 1"

        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Correct Show",
            detected_season=1,
            disc_number=1,
            backup_path=str(missing),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        assert result is None

    def test_prunes_emptied_parents_but_not_the_root(self, tmp_path):
        root = tmp_path
        old_dir = root / "TV" / "Wrong Show" / "Season 01" / "Disc 1"
        old_dir.mkdir(parents=True)
        (old_dir / "backup.mkv").write_text("data")

        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Correct Show",
            detected_season=1,
            disc_number=1,
            backup_path=str(old_dir),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        assert result is not None
        assert not (root / "TV" / "Wrong Show" / "Season 01").exists()
        assert not (root / "TV" / "Wrong Show").exists()
        assert root.exists()
        assert (root / "TV").exists()

    def test_reshapes_when_content_type_changed(self, tmp_path):
        """A TV disc corrected to a movie must move across the whole layout.

        The most common correction is a wrong show name, but the classifier can
        also be wrong about the KIND of disc, and then the backup does not just
        get renamed: TV/Show/Season/Disc has to become Movies/Name (Year). This
        works because backup_destination re-derives from the CURRENT
        content_type rather than from whatever shape the stored path has.
        """
        root = tmp_path
        old_dir = root / "TV" / "Wrong Show" / "Season 01" / "Disc 1"
        old_dir.mkdir(parents=True)
        (old_dir / "backup.mkv").write_text("data")

        job = _job(
            content_type=ContentType.MOVIE,
            tmdb_name="Correct Movie",
            tmdb_year=2001,
            backup_path=str(old_dir),
        )
        config = _config(backup_path=str(root))

        result = reconcile_backup_location(job, config)

        assert result is not None
        # Assert the shape, not the exact folder string: the movie folder format
        # is user-configurable, so hardcoding it here would couple this test to
        # a default rather than to the behavior under test.
        assert result.parent == root / "Movies"
        assert (result / "backup.mkv").read_text() == "data"
        assert not old_dir.exists()
        assert not (root / "TV" / "Wrong Show").exists()


class TestTerminalHookRegistration:
    """The helper is well covered; this guards the wiring that invokes it.

    Without this, deleting the on_terminal_state registration in a refactor
    leaves every other test green while the feature silently stops running.
    """

    def test_reconcile_hook_is_registered(self):
        # Importing the module constructs the JobManager singleton, whose
        # __init__ registers the terminal callbacks on the module-level
        # state machine. Import the names directly: `import ... as mod` binds
        # the job_manager SINGLETON rather than the module here.
        from app.services.job_manager import state_machine

        names = {getattr(cb, "__name__", "") for cb in state_machine._on_terminal_callbacks}
        assert "_reconcile_backup_on_terminal" in names
