"""Configuration service for managing app settings.

Provides functions to get and update configuration stored in SQLite.
"""

import logging
import sys
from pathlib import Path

from sqlmodel import select

from app.database import async_session
from app.models.app_config import AppConfig

logger = logging.getLogger(__name__)


def _platform_default_paths() -> dict[str, str]:
    """Return platform-aware default paths for first-run config."""
    home = Path.home()
    if sys.platform == "win32":
        base = home / "Engram"
        return {
            "staging_path": str(base / "Staging"),
            "library_movies_path": str(base / "Movies"),
            "library_tv_path": str(base / "TV"),
        }
    base = home / "engram"
    return {
        "staging_path": str(base / "staging"),
        "library_movies_path": str(base / "movies"),
        "library_tv_path": str(base / "tv"),
    }


async def get_config() -> AppConfig:
    """Get the current configuration, creating defaults if none exists."""
    async with async_session() as session:
        result = await session.execute(select(AppConfig).limit(1))
        config = result.scalar_one_or_none()

        if config is None:
            # Create default config with platform-aware paths
            defaults = _platform_default_paths()
            config = AppConfig(**defaults)
            session.add(config)
            await session.commit()
            await session.refresh(config)
            logger.info(f"Created default configuration with platform paths: {defaults}")

        return config


def get_config_sync() -> AppConfig:
    """Get configuration synchronously for non-async contexts."""
    from sqlmodel import Session, create_engine, select

    from app.config import settings

    # Create a synchronous engine for this specific operation
    # This is a bit expensive but safe for occasional use in background threads
    # Transform 'sqlite+aiosqlite:///...' to 'sqlite:///...'
    sync_db_url = settings.database_url.replace("+aiosqlite", "")
    engine = create_engine(sync_db_url)

    with Session(engine) as session:
        statement = select(AppConfig).limit(1)
        config = session.exec(statement).first()

        if config is None:
            config = AppConfig()
            session.add(config)
            session.commit()
            session.refresh(config)

        return config


async def update_config(**kwargs) -> AppConfig:
    """Update configuration with provided values.

    Args:
        **kwargs: Field names and values to update

    Returns:
        Updated AppConfig instance
    """
    async with async_session() as session:
        result = await session.execute(select(AppConfig).limit(1))
        config = result.scalar_one_or_none()

        if config is None:
            config = AppConfig()
            session.add(config)

        # Update provided fields
        # Special handling for sensitive fields: don't overwrite with empty strings
        sensitive_fields = {"makemkv_key", "tmdb_api_key", "opensubtitles_api_key"}

        for key, value in kwargs.items():
            if not hasattr(config, key):
                continue
            if value is None:
                continue
            # Skip empty strings for sensitive fields (keep existing value)
            if key in sensitive_fields and isinstance(value, str) and not value.strip():
                continue
            setattr(config, key, value)

        await session.commit()
        await session.refresh(config)

        # Ensure paths exist
        await ensure_paths_exist(config)

        logger.info(f"Updated configuration: {list(kwargs.keys())}")
        return config


async def ensure_paths_exist(config: AppConfig) -> None:
    """Create configured directories if they don't exist."""
    paths_to_create = [
        config.staging_path,
        config.library_movies_path,
        config.library_tv_path,
        config.subtitles_cache_path,
    ]

    for path_str in paths_to_create:
        if path_str:
            path = Path(path_str)
            if not path.is_absolute():
                # Make relative paths absolute based on backend directory
                path = Path(__file__).parent.parent / path_str

            try:
                path.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Ensured directory exists: {path}")
            except Exception as e:
                logger.warning(f"Could not create directory {path}: {e}")
