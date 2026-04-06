"""Database setup with SQLModel and async SQLite."""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

import sqlalchemy
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.config import settings

# Import all models so their tables are registered with SQLModel.metadata
from app.models import AppConfig, DiscJob  # noqa: F401

logger = logging.getLogger(__name__)

# Path to Alembic config (relative to backend/)
_ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    connect_args={"check_same_thread": False},  # Needed for SQLite
)


@sqlalchemy.event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


# Async session factory
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Initialize the database, creating all tables and running migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Run Alembic migrations and stamp version if needed
    _run_alembic_upgrade()

    # Legacy migration for app_config data preservation (Alembic handles schema,
    # but this preserves API keys/settings across breaking schema changes)
    await _migrate_app_config(engine)

    logger.info("Database initialized successfully")


def _run_alembic_upgrade() -> None:
    """Run Alembic upgrade to head, stamping if this is a fresh database."""
    if not _ALEMBIC_INI.exists():
        logger.debug("Alembic config not found (frozen build?), skipping migrations")
        return

    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config(str(_ALEMBIC_INI))

        # Check if alembic_version table exists (i.e., Alembic has been initialized)
        from sqlalchemy import create_engine, inspect

        sync_url = settings.database_url.replace("+aiosqlite", "")
        sync_engine = create_engine(sync_url)
        with sync_engine.connect() as conn:
            inspector = inspect(conn)
            has_version_table = "alembic_version" in inspector.get_table_names()

        if not has_version_table:
            # First time: stamp as current (tables already created by create_all)
            command.stamp(alembic_cfg, "head")
            logger.info("Alembic: stamped existing database at head")
        else:
            # Run any pending migrations
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic: migrations up to date")

        sync_engine.dispose()
    except Exception as e:
        logger.warning(f"Alembic migration failed (non-fatal): {e}", exc_info=True)


def _get_expected_columns(table_name: str) -> set[str]:
    """Get expected column names from the SQLModel metadata for a table."""
    table = SQLModel.metadata.tables.get(table_name)
    if table is None:
        return set()
    return {col.name for col in table.columns}


async def _get_actual_columns(conn, table_name: str) -> set[str]:
    """Get actual column names from the database for a table."""
    result = await conn.execute(sa_text(f"PRAGMA table_info('{table_name}')"))
    rows = result.fetchall()
    return {row[1] for row in rows}  # column name is at index 1


async def _migrate_app_config(target_engine: AsyncEngine | None = None) -> None:
    """Preserve app_config data across schema changes.

    Reads existing config rows, drops/recreates the table with the correct
    schema, and restores values by column name. This ensures users never
    lose API keys or settings when the AppConfig model changes.

    Idempotent: no-op when schema already matches.
    """
    eng = target_engine or engine

    async with eng.begin() as conn:
        # Check which tables exist
        result = await conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table'"))
        existing_tables = {row[0] for row in result.fetchall()}

        if "app_config" not in existing_tables:
            return

        actual_cols = await _get_actual_columns(conn, "app_config")
        expected_cols = _get_expected_columns("app_config")

        if actual_cols == expected_cols:
            return

        extra = actual_cols - expected_cols
        missing = expected_cols - actual_cols
        logger.info(
            f"Schema mismatch in app_config — "
            f"extra: {extra or 'none'}, missing: {missing or 'none'}"
        )

        # 1. Read existing config data
        rows = (await conn.execute(sa_text("SELECT * FROM app_config"))).fetchall()
        col_result = await conn.execute(sa_text("PRAGMA table_info('app_config')"))
        old_col_names = [row[1] for row in col_result.fetchall()]

        # 2. Drop old table
        await conn.execute(sa_text("DROP TABLE app_config"))

        # 3. Recreate with correct schema
        await conn.run_sync(
            lambda sync_conn: AppConfig.__table__.create(sync_conn, checkfirst=True)
        )

        # 4. Restore data using ORM to pick up column defaults
        if rows:
            new_fields = set(AppConfig.model_fields.keys())
            for row in rows:
                old_data = dict(zip(old_col_names, row, strict=False))
                config = AppConfig()
                for key, value in old_data.items():
                    if key == "id":
                        continue
                    if key in new_fields and value is not None:
                        setattr(config, key, value)
                insert_data = {}
                for field_name in new_fields:
                    if field_name == "id":
                        continue
                    insert_data[field_name] = getattr(config, field_name)
                cols_str = ", ".join(insert_data.keys())
                placeholders = ", ".join(f":{k}" for k in insert_data.keys())
                await conn.execute(
                    sa_text(f"INSERT INTO app_config ({cols_str}) VALUES ({placeholders})"),
                    insert_data,
                )
                logger.info(f"Restored app_config row with {len(insert_data)} fields")


async def reset_db() -> None:
    """Drop all tables and recreate them. Development only."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Database reset complete")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    async with async_session() as session:
        yield session
