"""Database setup with SQLModel and async SQLite."""

import logging
from collections.abc import AsyncGenerator

import sqlalchemy
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.config import settings

# Import all models so their tables are registered with SQLModel.metadata
from app.models import AppConfig, DiscJob  # noqa: F401

logger = logging.getLogger(__name__)

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
    """Initialize the database, creating all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Run schema migration to handle column additions, removals, and table changes
    await _migrate_schema(engine)

    logger.info("Database initialized successfully")


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


async def _migrate_schema(target_engine: AsyncEngine | None = None) -> None:
    """Compare live schema against SQLModel models and resolve mismatches.

    - **app_config**: Preserve data — read existing rows, drop/recreate table,
      restore values (mapping by column name). Users never lose API keys.
    - **disc_jobs / disc_titles**: Transient data — drop and recreate cleanly.
    - Idempotent: no-op when schema already matches.
    """
    eng = target_engine or engine

    async with eng.begin() as conn:
        # Check which tables exist
        result = await conn.execute(sa_text("SELECT name FROM sqlite_master WHERE type='table'"))
        existing_tables = {row[0] for row in result.fetchall()}

        # --- app_config migration (preserve data) ---
        if "app_config" in existing_tables:
            actual_cols = await _get_actual_columns(conn, "app_config")
            expected_cols = _get_expected_columns("app_config")

            if actual_cols != expected_cols:
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
                        # Start with a default AppConfig to fill in all NOT NULL columns
                        config = AppConfig()
                        # Overlay old values for columns that still exist
                        for key, value in old_data.items():
                            if key == "id":
                                continue  # Let auto-increment handle id
                            if key in new_fields and value is not None:
                                setattr(config, key, value)
                        # Insert via raw SQL using all model fields
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

        # --- disc_jobs / disc_titles migration (drop and recreate) ---
        transient_tables = ["disc_titles", "disc_jobs"]  # titles first (FK dependency)
        for table_name in transient_tables:
            if table_name in existing_tables:
                actual_cols = await _get_actual_columns(conn, table_name)
                expected_cols = _get_expected_columns(table_name)

                if actual_cols != expected_cols:
                    logger.info(f"Schema mismatch in {table_name} — dropping and recreating")
                    await conn.execute(sa_text(f"DROP TABLE {table_name}"))
                    table_obj = SQLModel.metadata.tables[table_name]
                    await conn.run_sync(
                        lambda sync_conn, t=table_obj: t.create(sync_conn, checkfirst=True)
                    )


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
