"""Tests for issue #19: Database migration system and OpenSubtitles cleanup.

TDD: These tests verify the schema migration system can detect mismatches,
preserve app_config data, recreate transient tables, and remove obsolete columns.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from app.models.app_config import AppConfig
from app.models.disc_job import DiscJob, DiscTitle


@pytest.fixture
async def migration_engine():
    """Create a fresh engine for migration testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def migration_factory(migration_engine):
    """Create a session factory for migration testing."""
    return sessionmaker(migration_engine, class_=AsyncSession, expire_on_commit=False)


class TestOpenSubtitlesCleanup:
    """OpenSubtitles fields should be removed from models."""

    def test_app_config_has_no_opensubtitles_username(self):
        """AppConfig should not have opensubtitles_username field."""
        assert "opensubtitles_username" not in AppConfig.model_fields

    def test_app_config_has_no_opensubtitles_password(self):
        """AppConfig should not have opensubtitles_password field."""
        assert "opensubtitles_password" not in AppConfig.model_fields

    def test_app_config_has_no_opensubtitles_api_key(self):
        """AppConfig should not have opensubtitles_api_key field."""
        assert "opensubtitles_api_key" not in AppConfig.model_fields

    def test_config_service_no_opensubtitles_sensitive_field(self):
        """config_service sensitive_fields should not reference opensubtitles_api_key."""
        import inspect as ins

        from app.services.config_service import update_config

        source = ins.getsource(update_config)
        assert "opensubtitles_api_key" not in source

    def test_matcher_config_has_no_opensubtitles_fields(self):
        """Matcher Config should not have open_subtitles fields."""
        from app.matcher.models import Config

        assert "open_subtitles_api_key" not in Config.model_fields
        assert "open_subtitles_username" not in Config.model_fields
        assert "open_subtitles_password" not in Config.model_fields
        assert "open_subtitles_user_agent" not in Config.model_fields


class TestSchemaMigration:
    """Schema migration should detect and resolve mismatches."""

    async def test_migration_is_idempotent_on_correct_schema(self, migration_engine):
        """Running migration on a correct schema should be a no-op."""
        from app.database import _migrate_app_config

        # Create correct schema
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Running migration should not raise
        await _migrate_app_config(migration_engine)

        # Tables should still exist and be correct
        async with migration_engine.connect() as conn:
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = {row[0] for row in result.fetchall()}
            assert "app_config" in tables
            assert "disc_jobs" in tables
            assert "disc_titles" in tables

    async def test_migration_preserves_app_config_data(self, migration_engine, migration_factory):
        """Migration should preserve existing app_config values when schema changes."""
        from app.database import _migrate_app_config

        # Create schema with an extra obsolete column to trigger migration
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            await conn.execute(
                text("ALTER TABLE app_config ADD COLUMN obsolete_field VARCHAR DEFAULT ''")
            )

        # Insert config using ORM (fills all NOT NULL defaults)
        async with migration_factory() as session:
            config = AppConfig(
                makemkv_key="test-key-12345",
                tmdb_api_key="eyJtest",
                staging_path="/custom/staging",
                setup_complete=True,
            )
            session.add(config)
            await session.commit()

        # Run migration — should detect extra column and rebuild
        await _migrate_app_config(migration_engine)

        # Verify config data is preserved
        async with migration_factory() as session:
            result = await session.execute(
                text("SELECT makemkv_key, tmdb_api_key, staging_path FROM app_config LIMIT 1")
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "test-key-12345"
            assert row[1] == "eyJtest"
            assert row[2] == "/custom/staging"

    async def test_app_config_migration_does_not_touch_disc_tables(
        self, migration_engine, migration_factory
    ):
        """App config migration should not affect disc_jobs/disc_titles (Alembic handles those)."""
        from app.database import _migrate_app_config

        # Create schema and add extra column to disc_jobs
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Insert transient data
        async with migration_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO disc_jobs (drive_id, volume_label, state, content_type, "
                    "current_speed, eta_seconds, progress_percent, current_title, total_titles, "
                    "subtitles_downloaded, subtitles_total, subtitles_failed, disc_number, "
                    "is_transcoding_enabled, created_at, updated_at) VALUES "
                    "('E:', 'TEST', 'ripping', 'tv', '0', 0, 0, 0, 0, 0, 0, 0, 1, 0, "
                    "datetime('now'), datetime('now'))"
                )
            )
            await session.commit()

        # Run app_config migration
        await _migrate_app_config(migration_engine)

        # Disc tables should be untouched (data preserved)
        async with migration_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM disc_jobs"))
            count = result.scalar()
            assert count == 1

    async def test_migration_handles_extra_columns(self, migration_engine, migration_factory):
        """Migration should handle tables with extra columns (e.g. obsolete opensubtitles)."""
        from app.database import _migrate_app_config

        # Create schema with extra columns
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            await conn.execute(
                text("ALTER TABLE app_config ADD COLUMN opensubtitles_username VARCHAR DEFAULT ''")
            )
            await conn.execute(
                text("ALTER TABLE app_config ADD COLUMN opensubtitles_password VARCHAR DEFAULT ''")
            )
            await conn.execute(
                text("ALTER TABLE app_config ADD COLUMN opensubtitles_api_key VARCHAR DEFAULT ''")
            )

        # Insert config using ORM (fills all NOT NULL defaults)
        async with migration_factory() as session:
            config = AppConfig(
                makemkv_key="preserve-this-key",
                tmdb_api_key="preserve-this-token",
            )
            session.add(config)
            await session.commit()

        # Run migration
        await _migrate_app_config(migration_engine)

        # Config data should be preserved, obsolete columns removed
        async with migration_factory() as session:
            result = await session.execute(
                text("SELECT makemkv_key, tmdb_api_key FROM app_config LIMIT 1")
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "preserve-this-key"
            assert row[1] == "preserve-this-token"

            # Obsolete columns should be gone
            actual_cols = set()
            col_result = await session.execute(text("PRAGMA table_info('app_config')"))
            for col_row in col_result.fetchall():
                actual_cols.add(col_row[1])
            assert "opensubtitles_username" not in actual_cols
            assert "opensubtitles_password" not in actual_cols
            assert "opensubtitles_api_key" not in actual_cols

    async def test_migration_handles_missing_columns(self, migration_engine, migration_factory):
        """Migration should handle tables missing columns that the model expects."""
        from app.database import _migrate_app_config

        # Create a minimal app_config table missing many columns
        async with migration_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE app_config (
                        id INTEGER PRIMARY KEY,
                        makemkv_path VARCHAR DEFAULT '',
                        makemkv_key VARCHAR DEFAULT '',
                        staging_path VARCHAR DEFAULT '',
                        library_movies_path VARCHAR DEFAULT '',
                        library_tv_path VARCHAR DEFAULT '',
                        tmdb_api_key VARCHAR DEFAULT '',
                        setup_complete BOOLEAN DEFAULT 0
                    )
                """
                )
            )
            # Also create disc tables
            await conn.run_sync(
                lambda sync_conn: DiscJob.__table__.create(sync_conn, checkfirst=True)
            )
            await conn.run_sync(
                lambda sync_conn: DiscTitle.__table__.create(sync_conn, checkfirst=True)
            )

        # Insert config with existing columns
        async with migration_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO app_config (makemkv_key, tmdb_api_key, setup_complete) "
                    "VALUES ('old-key', 'old-token', 1)"
                )
            )
            await session.commit()

        # Run migration — should detect missing columns and rebuild
        await _migrate_app_config(migration_engine)

        # Preserved values should survive, new columns should have defaults
        async with migration_factory() as session:
            result = await session.execute(
                text(
                    "SELECT makemkv_key, tmdb_api_key, setup_complete, "
                    "max_concurrent_matches FROM app_config LIMIT 1"
                )
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "old-key"
            assert row[1] == "old-token"
            assert row[2] in (True, 1)
            assert row[3] == 2  # default value for max_concurrent_matches

    async def test_add_missing_columns_to_disc_jobs(self, migration_engine, migration_factory):
        """Upgrading from an older schema should add missing columns to disc_jobs.

        Regression test for #57: frozen builds had no migration path for disc_jobs,
        so users with an old database got 'no such column: classification_confidence'.
        """
        # Patch the module-level engine so _add_missing_columns uses our test engine
        import app.database as db_mod
        from app.database import _add_missing_columns, _get_actual_columns, _get_expected_columns

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            # Create an OLD disc_jobs table missing newer columns
            async with migration_engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE TABLE disc_jobs (
                            id INTEGER PRIMARY KEY,
                            drive_id VARCHAR NOT NULL,
                            volume_label VARCHAR DEFAULT '',
                            content_type VARCHAR DEFAULT 'unknown',
                            detected_title VARCHAR,
                            detected_season INTEGER,
                            is_transcoding_enabled BOOLEAN DEFAULT 0,
                            staging_path VARCHAR,
                            final_path VARCHAR,
                            state VARCHAR DEFAULT 'idle',
                            current_speed VARCHAR DEFAULT '0.0x',
                            eta_seconds INTEGER DEFAULT 0,
                            progress_percent FLOAT DEFAULT 0,
                            current_title INTEGER DEFAULT 0,
                            total_titles INTEGER DEFAULT 0,
                            subtitle_status VARCHAR,
                            subtitles_downloaded INTEGER DEFAULT 0,
                            subtitles_total INTEGER DEFAULT 0,
                            subtitles_failed INTEGER DEFAULT 0,
                            created_at DATETIME,
                            updated_at DATETIME,
                            completed_at DATETIME,
                            cleared_at DATETIME,
                            error_message VARCHAR,
                            review_reason VARCHAR,
                            titles_json VARCHAR,
                            disc_number INTEGER DEFAULT 1
                        )
                    """
                    )
                )
                # Also create disc_titles with full schema
                await conn.run_sync(
                    lambda sync_conn: DiscTitle.__table__.create(sync_conn, checkfirst=True)
                )

            # Insert a job row with the old schema
            async with migration_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO disc_jobs (drive_id, volume_label, state, content_type, "
                        "created_at, updated_at) VALUES "
                        "('E:', 'OLD_DISC', 'completed', 'tv', datetime('now'), datetime('now'))"
                    )
                )
                await session.commit()

            # Verify columns are missing before migration
            async with migration_engine.connect() as conn:
                actual = await _get_actual_columns(conn, "disc_jobs")
                assert "classification_confidence" not in actual
                assert "content_hash" not in actual
                assert "discdb_slug" not in actual

            # Run the migration
            await _add_missing_columns()

            # Verify missing columns were added
            async with migration_engine.connect() as conn:
                actual = await _get_actual_columns(conn, "disc_jobs")
                expected = _get_expected_columns("disc_jobs")
                assert expected - actual == set(), f"Still missing columns: {expected - actual}"

            # Verify existing data is preserved
            async with migration_factory() as session:
                result = await session.execute(
                    text("SELECT volume_label, state FROM disc_jobs WHERE id = 1")
                )
                row = result.fetchone()
                assert row is not None
                assert row[0] == "OLD_DISC"
                assert row[1] == "completed"

            # Verify new columns have correct defaults
            async with migration_factory() as session:
                result = await session.execute(
                    text(
                        "SELECT classification_confidence, classification_source, "
                        "is_ambiguous_movie FROM disc_jobs WHERE id = 1"
                    )
                )
                row = result.fetchone()
                assert row is not None
                assert row[0] == 0.0  # default
                assert row[1] == "heuristic"  # default
                assert row[2] in (False, 0)  # default

        finally:
            db_mod.engine = original_engine

    async def test_add_missing_columns_is_idempotent(self, migration_engine):
        """Running _add_missing_columns on a correct schema should be a no-op."""
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            # Create full schema
            async with migration_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

            # Should not raise or modify anything
            await db_mod._add_missing_columns()

            # Tables should still be correct
            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_jobs")
                expected = db_mod._get_expected_columns("disc_jobs")
                assert actual == expected
        finally:
            db_mod.engine = original_engine
