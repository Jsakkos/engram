"""Database setup with SQLModel and async SQLite."""

import logging
from collections.abc import AsyncGenerator

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
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

    # Run lightweight column migrations for existing databases
    await _migrate_add_columns()

    logger.info("Database initialized successfully")


async def _migrate_add_columns() -> None:
    """Add new columns to existing tables. Idempotent — ignores if column exists."""
    migrations = [
        "ALTER TABLE app_config ADD COLUMN max_concurrent_matches INTEGER NOT NULL DEFAULT 2",
        "ALTER TABLE disc_titles ADD COLUMN video_resolution VARCHAR",
        "ALTER TABLE disc_titles ADD COLUMN edition VARCHAR",
        # Phase 4: Analyst configuration thresholds
        "ALTER TABLE app_config ADD COLUMN analyst_movie_min_duration INTEGER NOT NULL DEFAULT 4800",
        "ALTER TABLE app_config ADD COLUMN analyst_tv_duration_variance INTEGER NOT NULL DEFAULT 120",
        "ALTER TABLE app_config ADD COLUMN analyst_tv_min_cluster_size INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE app_config ADD COLUMN analyst_tv_min_duration INTEGER NOT NULL DEFAULT 1080",
        "ALTER TABLE app_config ADD COLUMN analyst_tv_max_duration INTEGER NOT NULL DEFAULT 4200",
        "ALTER TABLE app_config ADD COLUMN analyst_movie_dominance_threshold REAL NOT NULL DEFAULT 0.6",
        # Phase 4: Ripping coordination settings
        "ALTER TABLE app_config ADD COLUMN ripping_file_poll_interval REAL NOT NULL DEFAULT 5.0",
        "ALTER TABLE app_config ADD COLUMN ripping_stability_checks INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE app_config ADD COLUMN ripping_file_ready_timeout REAL NOT NULL DEFAULT 600.0",
        # Phase 4: Sentinel monitoring
        "ALTER TABLE app_config ADD COLUMN sentinel_poll_interval REAL NOT NULL DEFAULT 2.0",
    ]
    async with engine.begin() as conn:
        for stmt in migrations:
            try:
                await conn.execute(sqlalchemy.text(stmt))
                logger.info(f"Migration applied: {stmt}")
            except sqlalchemy.exc.OperationalError:
                # Column already exists — expected for non-first runs
                logger.debug(f"Migration skipped (already applied): {stmt}")
                pass


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
