"""Tests for issue #19: Database migration system and OpenSubtitles cleanup.

TDD: These tests verify the schema migration system can detect mismatches,
preserve app_config data, recreate transient tables, and remove obsolete columns.
"""

import pytest
from sqlalchemy import create_engine, text
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


class TestSchemaMigration:
    """Schema migration should detect and resolve mismatches."""

    async def test_migration_is_idempotent_on_correct_schema(self, migration_engine):
        """Running migration on a correct schema should be a no-op."""
        import app.database as db_mod

        # Create correct schema
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Running migration should not raise
        await db_mod._migrate_app_config(migration_engine)

        # Tables should still exist and be correct
        async with migration_engine.connect() as conn:
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = {row[0] for row in result.fetchall()}
            assert "app_config" in tables
            assert "disc_jobs" in tables
            assert "disc_titles" in tables

    async def test_migration_preserves_app_config_data(self, migration_engine, migration_factory):
        """Migration should preserve existing app_config values when schema changes."""
        import app.database as db_mod

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
        await db_mod._migrate_app_config(migration_engine)

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
        import app.database as db_mod

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
                    "created_at, updated_at) VALUES "
                    "('E:', 'TEST', 'ripping', 'tv', '0', 0, 0, 0, 0, 0, 0, 0, 1, "
                    "datetime('now'), datetime('now'))"
                )
            )
            await session.commit()

        # Run app_config migration
        await db_mod._migrate_app_config(migration_engine)

        # Disc tables should be untouched (data preserved)
        async with migration_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM disc_jobs"))
            count = result.scalar()
            assert count == 1

    async def test_migration_handles_missing_columns(self, migration_engine, migration_factory):
        """Migration should handle tables missing columns that the model expects."""
        import app.database as db_mod

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
        await db_mod._migrate_app_config(migration_engine)

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
                actual = await db_mod._get_actual_columns(conn, "disc_jobs")
                assert "classification_confidence" not in actual
                assert "content_hash" not in actual
                assert "discdb_slug" not in actual

            # Run the migration
            await db_mod._add_missing_columns()

            # Verify missing columns were added
            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_jobs")
                expected = db_mod._get_expected_columns("disc_jobs")
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

    async def test_add_missing_string_column_backfills_declared_default(
        self, migration_engine, migration_factory
    ):
        """A non-Optional string column added on upgrade must backfill existing
        rows with the model's declared default (''), never NULL.

        Regression for the 0.26.0 /api/config 500: SQLModel wraps str columns in
        AutoString (a TypeDecorator, not a String subclass), so the old
        type-based default logic fell through to no DEFAULT clause and existing
        app_config rows got NULL for discord_template_completed /
        discord_template_failed. ConfigResponse requires str, so GET /api/config
        raised a pydantic ValidationError -> HTTP 500 for every upgrading user.
        """
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            # Pre-0.26.0 app_config: a minimal older schema that predates the
            # Discord template columns (added in #508). disc tables come from the
            # model so unrelated reconciliation is a no-op.
            async with migration_engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE TABLE app_config (
                            id INTEGER PRIMARY KEY,
                            makemkv_key VARCHAR DEFAULT ''
                        )
                        """
                    )
                )
                await conn.run_sync(
                    lambda sync_conn: DiscJob.__table__.create(sync_conn, checkfirst=True)
                )
                await conn.run_sync(
                    lambda sync_conn: DiscTitle.__table__.create(sync_conn, checkfirst=True)
                )

            # Seed a config row through the older schema
            async with migration_factory() as session:
                await session.execute(
                    text("INSERT INTO app_config (makemkv_key) VALUES ('pre-026-key')")
                )
                await session.commit()

            # Sanity: the new columns really are missing beforehand
            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "app_config")
                assert "discord_template_completed" not in actual
                assert "discord_template_failed" not in actual

            await db_mod._add_missing_columns()

            # Existing row must have '' (the declared default), not NULL
            async with migration_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT discord_template_completed, discord_template_failed "
                            "FROM app_config LIMIT 1"
                        )
                    )
                ).fetchone()
                assert row is not None
                assert row[0] == "", f"expected '' got {row[0]!r}"
                assert row[1] == "", f"expected '' got {row[1]!r}"
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

    async def test_drop_extra_columns_from_disc_jobs(self, migration_engine, migration_factory):
        """Columns removed from the model must be dropped from existing databases.

        Regression: a column removed from the model (e.g. is_transcoding_enabled)
        is dropped via Alembic in dev, but frozen/PyInstaller builds ship no
        alembic.ini so that migration never runs. The stale NOT NULL column then
        lingered; because the ORM omits it on INSERT, every new disc job crashed
        with 'NOT NULL constraint failed: disc_jobs.is_transcoding_enabled'.
        """
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            # Current model schema, plus a leftover column the model no longer has
            async with migration_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                await conn.execute(
                    text(
                        "ALTER TABLE disc_jobs ADD COLUMN is_transcoding_enabled "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

            # Seed a row so we can prove data is preserved across the drop
            async with migration_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO disc_jobs (drive_id, volume_label, state, content_type, "
                        "current_speed, eta_seconds, progress_percent, current_title, "
                        "total_titles, subtitles_downloaded, subtitles_total, subtitles_failed, "
                        "disc_number, created_at, updated_at) VALUES "
                        "('E:', 'KEEP_ME', 'completed', 'tv', '0', 0, 0, 0, 0, 0, 0, 0, 1, "
                        "datetime('now'), datetime('now'))"
                    )
                )
                await session.commit()

            # Sanity: the extra column is present before reconciliation
            async with migration_engine.connect() as conn:
                assert "is_transcoding_enabled" in await db_mod._get_actual_columns(
                    conn, "disc_jobs"
                )

            await db_mod._drop_extra_columns()

            # Extra column gone; schema matches the model exactly
            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_jobs")
                expected = db_mod._get_expected_columns("disc_jobs")
                assert "is_transcoding_enabled" not in actual
                assert actual == expected

            # Existing data is preserved
            async with migration_factory() as session:
                row = (
                    await session.execute(
                        text("SELECT volume_label, state FROM disc_jobs WHERE id = 1")
                    )
                ).fetchone()
                assert row == ("KEEP_ME", "completed")

            # A model-shaped insert (which omits the dropped column) now succeeds
            async with migration_factory() as session:
                session.add(DiscJob(drive_id="F:", volume_label="NEW_DISC"))
                await session.commit()
        finally:
            db_mod.engine = original_engine

    async def test_drop_extra_columns_is_idempotent(self, migration_engine):
        """Running _drop_extra_columns on a correct schema should be a no-op."""
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            async with migration_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

            await db_mod._drop_extra_columns()

            async with migration_engine.connect() as conn:
                for table in ("disc_jobs", "disc_titles", "app_config"):
                    actual = await db_mod._get_actual_columns(conn, table)
                    expected = db_mod._get_expected_columns(table)
                    assert actual == expected
        finally:
            db_mod.engine = original_engine

    async def test_drop_extra_columns_is_best_effort_on_failure(self, migration_engine):
        """A column that can't be dropped must not block dropping the others.

        SQLite refuses DROP COLUMN on an indexed column. Each drop therefore
        needs its own transaction: batching them all in one transaction means
        the first failure invalidates it, so the remaining (droppable) columns
        are skipped and the commit on block-exit can even crash startup.
        """
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine

        try:
            async with migration_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                # Two columns the model no longer defines: one indexed
                # (undroppable in SQLite) and one plain (droppable).
                await conn.execute(text("ALTER TABLE disc_jobs ADD COLUMN legacy_indexed VARCHAR"))
                await conn.execute(text("ALTER TABLE disc_jobs ADD COLUMN legacy_plain VARCHAR"))
                await conn.execute(
                    text("CREATE INDEX ix_legacy_indexed ON disc_jobs(legacy_indexed)")
                )

            # Must not raise even though one column cannot be dropped
            await db_mod._drop_extra_columns()

            # The droppable column is gone despite the undroppable one failing
            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_jobs")
            assert "legacy_plain" not in actual
        finally:
            db_mod.engine = original_engine

    async def test_candidates_json_round_trips(self, migration_engine, migration_factory):
        """disc_jobs must persist candidates_json so the downstream wrong-show
        detector can suggest the same-name twin without re-querying TMDB.

        Regression guard for the same-name collision fix (Frasier 1993 vs 2023):
        same-name TMDB candidates are recorded at identify time and read at
        finalization. The column must exist in the model (so frozen-build
        _add_missing_columns adds it) and round-trip a JSON string.
        """
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # The model must expose candidates_json for the frozen-build reconciler
        # (_get_expected_columns derives from model metadata).
        import app.database as db_mod

        assert "candidates_json" in db_mod._get_expected_columns("disc_jobs")

        payload = (
            '[{"tmdb_id": 3452, "name": "Frasier", "year": "1993"}, '
            '{"tmdb_id": 195241, "name": "Frasier", "year": "2023"}]'
        )
        async with migration_factory() as session:
            session.add(
                DiscJob(drive_id="E:", volume_label="FRASIER_S1D1", candidates_json=payload)
            )
            await session.commit()

        async with migration_factory() as session:
            row = (
                await session.execute(text("SELECT candidates_json FROM disc_jobs LIMIT 1"))
            ).fetchone()
            assert row is not None
            assert row[0] == payload

    async def test_identity_prompt_json_round_trips(self, migration_engine, migration_factory):
        """disc_jobs must persist identity_prompt_json so the frontend can surface the
        non-blocking identity CTA for rip-first jobs (walk-away Phase B).

        Regression guard: the column must exist in the model (so frozen-build
        _add_missing_columns adds it), default to None, and round-trip a JSON string.
        """
        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # The model must expose identity_prompt_json for the frozen-build reconciler
        # (_get_expected_columns derives from model metadata).
        import app.database as db_mod

        assert "identity_prompt_json" in db_mod._get_expected_columns("disc_jobs")

        prompt = '{"kind": "season", "reason": "Could not detect season automatically"}'
        async with migration_factory() as session:
            session.add(
                DiscJob(drive_id="E:", volume_label="WALK_AWAY_S1D1", identity_prompt_json=prompt)
            )
            await session.commit()

        async with migration_factory() as session:
            row = (
                await session.execute(text("SELECT identity_prompt_json FROM disc_jobs LIMIT 1"))
            ).fetchone()
            assert row is not None
            assert row[0] == prompt

        # Default: null when not set (not an empty string or other sentinel)
        async with migration_factory() as session:
            job_no_prompt = DiscJob(drive_id="F:", volume_label="NO_PROMPT")
            session.add(job_no_prompt)
            await session.commit()
            await session.refresh(job_no_prompt)
            assert job_no_prompt.identity_prompt_json is None


class TestAlembicSelfHealing:
    """Regression tests for issue #459: _add_missing_columns() (the frozen-build
    fallback) and Alembic both run on every startup and converge toward the
    same SQLModel metadata. If a column an Alembic migration wants to add was
    already added out-of-band, ALTER TABLE ADD COLUMN fails with "duplicate
    column name" and, previously, that failure was only logged — leaving
    alembic_version stuck one revision behind forever and silently blocking
    every later migration on every subsequent startup.
    """

    def test_upgrade_heals_when_head_column_already_present(self, tmp_path):
        """A DB whose schema already matches head, but whose alembic_version
        is one revision behind, must self-heal to head instead of getting
        stuck retrying the same failing migration forever.
        """
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        import app.database as db_mod

        db_path = tmp_path / "self_heal.db"
        sync_engine = create_engine(f"sqlite:///{db_path}")

        # migrations/env.py reads settings.database_url directly (ignores any
        # connection passed via Config.attributes), so it must point at the
        # temp DB before *any* Alembic command runs, including the stamp used
        # to set up this scenario.
        original_url = db_mod.settings.database_url
        db_mod.settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        try:
            # Simulate a DB that already has the full current schema (as if
            # _add_missing_columns had already added every column, including
            # the one the still-pending head migration wants to add).
            SQLModel.metadata.create_all(sync_engine)

            alembic_cfg = Config(str(db_mod._ALEMBIC_INI))
            script = ScriptDirectory.from_config(alembic_cfg)
            head = script.get_current_head()
            down_revision = script.get_revision(head).down_revision or "base"

            # Stamp alembic_version one revision behind head, so re-running
            # the head migration's ADD COLUMN collides with the column that
            # create_all() already put there.
            command.stamp(alembic_cfg, down_revision)

            db_mod._run_alembic_upgrade()

            from alembic.runtime.migration import MigrationContext

            with sync_engine.connect() as conn:
                current_rev = MigrationContext.configure(conn).get_current_revision()
            assert current_rev == head
        finally:
            db_mod.settings.database_url = original_url
            sync_engine.dispose()

    def test_self_heal_reraises_non_duplicate_column_errors(self, tmp_path, monkeypatch):
        """An OperationalError unrelated to a duplicate column must propagate,
        not be swallowed by the self-heal loop as if it were a no-op revision.
        """
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy.exc import OperationalError

        import app.database as db_mod

        db_path = tmp_path / "propagate.db"
        sync_engine = create_engine(f"sqlite:///{db_path}")

        original_url = db_mod.settings.database_url
        db_mod.settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        try:
            SQLModel.metadata.create_all(sync_engine)

            alembic_cfg = Config(str(db_mod._ALEMBIC_INI))
            script = ScriptDirectory.from_config(alembic_cfg)
            head = script.get_current_head()
            down_revision = script.get_revision(head).down_revision or "base"
            command.stamp(alembic_cfg, down_revision)

            def fake_upgrade(_cfg, _rev):
                raise OperationalError("SELECT 1", {}, Exception("no such table: unrelated_table"))

            monkeypatch.setattr(command, "upgrade", fake_upgrade)

            with pytest.raises(OperationalError, match="no such table"):
                db_mod._upgrade_to_head_self_healing(alembic_cfg, sync_engine)
        finally:
            db_mod.settings.database_url = original_url
            sync_engine.dispose()
