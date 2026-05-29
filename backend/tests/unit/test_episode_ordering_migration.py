"""Schema + model tests for the episode-ordering fields (GitHub #200).

Covers the three storage additions and their frozen-build migration path
(``_add_missing_columns`` + ``create_all``, since frozen builds skip Alembic):
- AppConfig.episode_ordering_preference (global default, server_default 'aired')
- ShowOrderingPreference table (per-show override, keyed by tmdb_id)
- DiscTitle.episode_ordering / episode_group_id (audit of what was applied)
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel


@pytest.fixture
async def migration_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def migration_factory(migration_engine):
    return sessionmaker(migration_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.unit
class TestModels:
    def test_app_config_default_ordering_is_aired(self):
        from app.models.app_config import AppConfig

        assert AppConfig().episode_ordering_preference == "aired"

    def test_show_ordering_preference_importable_with_fields(self):
        from app.models import ShowOrderingPreference

        pref = ShowOrderingPreference(tmdb_id=1437, ordering="dvd")
        assert pref.tmdb_id == 1437
        assert pref.ordering == "dvd"
        assert pref.episode_group_id is None

    def test_disc_title_has_audit_fields_defaulting_none(self):
        from app.models.disc_job import DiscTitle

        t = DiscTitle(job_id=1, title_index=0, duration_seconds=1)
        assert t.episode_ordering is None
        assert t.episode_group_id is None


@pytest.mark.unit
class TestFrozenBuildMigration:
    async def test_create_all_makes_show_ordering_table(self, migration_engine, migration_factory):
        """A fresh/frozen DB gets the per-show table from create_all."""
        from app.models import ShowOrderingPreference

        async with migration_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        async with migration_engine.connect() as conn:
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            assert "show_ordering_preferences" in {row[0] for row in result.fetchall()}

        # round-trips through the ORM
        async with migration_factory() as session:
            session.add(ShowOrderingPreference(tmdb_id=1437, ordering="dvd"))
            await session.commit()
            got = await session.get(ShowOrderingPreference, 1437)
            assert got.ordering == "dvd"

    async def test_add_missing_columns_upgrades_legacy_disc_titles(
        self, migration_engine, migration_factory
    ):
        """A legacy disc_titles missing the audit columns gets them (no Alembic)."""
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine
        try:
            async with migration_engine.begin() as conn:
                # Minimal legacy disc_titles lacking the new audit columns.
                await conn.execute(
                    text(
                        """
                        CREATE TABLE disc_titles (
                            id INTEGER PRIMARY KEY,
                            job_id INTEGER NOT NULL,
                            title_index INTEGER NOT NULL,
                            duration_seconds INTEGER NOT NULL,
                            matched_episode VARCHAR,
                            match_source VARCHAR
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO disc_titles "
                        "(job_id, title_index, duration_seconds, matched_episode, match_source) "
                        "VALUES (1, 0, 1320, 'S01E11', 'engram')"
                    )
                )

            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_titles")
                assert "episode_ordering" not in actual
                assert "episode_group_id" not in actual

            await db_mod._add_missing_columns()

            async with migration_engine.connect() as conn:
                actual = await db_mod._get_actual_columns(conn, "disc_titles")
                assert "episode_ordering" in actual
                assert "episode_group_id" in actual

            # Existing canonical data preserved; new audit columns default NULL.
            async with migration_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT matched_episode, episode_ordering, episode_group_id "
                            "FROM disc_titles WHERE id = 1"
                        )
                    )
                ).fetchone()
                assert row[0] == "S01E11"  # canonical untouched
                assert row[1] is None
                assert row[2] is None
        finally:
            db_mod.engine = original_engine

    async def test_add_missing_columns_upgrades_legacy_app_config(
        self, migration_engine, migration_factory
    ):
        """Legacy app_config without the ordering pref gets it defaulting to 'aired'."""
        import app.database as db_mod

        original_engine = db_mod.engine
        db_mod.engine = migration_engine
        try:
            async with migration_engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE TABLE app_config (
                            id INTEGER PRIMARY KEY,
                            tmdb_api_key VARCHAR DEFAULT ''
                        )
                        """
                    )
                )
                await conn.execute(text("INSERT INTO app_config (tmdb_api_key) VALUES ('legacy')"))

            await db_mod._add_missing_columns()

            async with migration_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT tmdb_api_key, episode_ordering_preference "
                            "FROM app_config WHERE id = 1"
                        )
                    )
                ).fetchone()
                assert row[0] == "legacy"  # existing secret preserved
                assert row[1] == "aired"  # server_default applied
        finally:
            db_mod.engine = original_engine
