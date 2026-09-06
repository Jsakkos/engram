"""The backup columns exist, default correctly, and read safely on legacy rows."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import AppConfig, DiscJob, JobState


class TestDiscJobBackupColumns:
    def test_new_job_has_no_source_spec(self):
        # None means "legacy or drive-sourced"; DiscSource.from_job falls back.
        assert DiscJob(drive_id="E:").source_spec is None

    def test_new_job_has_no_backup_path_or_status(self):
        job = DiscJob(drive_id="E:")
        assert job.backup_path is None
        assert job.backup_status is None

    def test_new_job_has_no_backup_status_reason(self):
        assert DiscJob(drive_id="E:").backup_status_reason is None


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


class TestSchemaConvergence:
    """The new columns survive the real init_db() path, not just model construction.

    Frozen builds skip Alembic entirely and converge through
    _add_missing_columns(), so the model defaults have to be right on their own.
    This is the risk the feature actually carries for an upgrading user.

    Isolation: app.database reads settings.database_url once at import time into
    a module-level `engine`, and app.database.init_db()'s Alembic path
    (_run_alembic_upgrade) opens its OWN sync engine from settings.database_url
    directly, so setting the DATABASE_URL env var alone would not redirect
    either. Alembic's migrations/env.py is re-executed from disk on every
    upgrade call (it is not cached in sys.modules across calls), so it also
    re-reads settings.database_url fresh each time. Patching both
    app.config.settings.database_url (monkeypatch, auto-restored) and
    app.database.engine / app.database.async_session (manual, restored in
    finally, matching the pattern in test_episode_ordering_migration.py) to a
    fresh engine over a scratch file under tmp_path routes every one of these
    paths at a throwaway database and never touches backend/engram.db.
    """

    @pytest.mark.asyncio
    async def test_a_row_written_and_read_through_init_db_sees_the_new_columns(
        self, tmp_path, monkeypatch
    ):
        import app.database as db_mod
        from app.config import settings

        db_path = tmp_path / "scratch-backup-columns.db"
        scratch_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        monkeypatch.setattr(settings, "database_url", scratch_url)

        scratch_engine = create_async_engine(scratch_url, connect_args={"check_same_thread": False})
        scratch_session_factory = sessionmaker(
            scratch_engine, class_=AsyncSession, expire_on_commit=False
        )

        original_engine = db_mod.engine
        original_session_factory = db_mod.async_session
        db_mod.engine = scratch_engine
        db_mod.async_session = scratch_session_factory
        try:
            await db_mod.init_db()

            async with scratch_session_factory() as session:
                job = DiscJob(drive_id="E:", volume_label="SCRATCH_DISC")
                session.add(job)
                await session.commit()
                job_id = job.id

                config = AppConfig()
                session.add(config)
                await session.commit()

            # Fresh session, so this is a genuine read back through the schema
            # init_db() converged, not the in-memory object we just built.
            async with scratch_session_factory() as session:
                reread_job = await session.get(DiscJob, job_id)
                assert reread_job is not None
                assert reread_job.volume_label == "SCRATCH_DISC"
                assert reread_job.source_spec is None
                assert reread_job.backup_path is None
                assert reread_job.backup_status is None
                assert reread_job.backup_status_reason is None

                reread_config = await session.get(AppConfig, config.id)
                assert reread_config is not None
                assert reread_config.backup_before_rip is False
                assert reread_config.backup_path == ""
                assert reread_config.timeout_backing_up_seconds == 7200
        finally:
            db_mod.engine = original_engine
            db_mod.async_session = original_session_factory
            await scratch_engine.dispose()
