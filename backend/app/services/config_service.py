"""Configuration service for managing app settings.

Provides functions to get and update configuration stored in SQLite.
"""

import asyncio
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
        "staging_watch_enabled": True,  # Enable by default on Linux/macOS
    }


def _make_default_config() -> AppConfig:
    """Build a default AppConfig with platform-aware paths."""
    defaults = _platform_default_paths()
    logger.info(f"Created default configuration with platform paths: {defaults}")
    return AppConfig(**defaults)


async def get_config() -> AppConfig:
    """Get the current configuration, creating defaults if none exists."""
    async with async_session() as session:
        result = await session.execute(select(AppConfig).limit(1))
        config = result.scalar_one_or_none()

        if config is None:
            config = _make_default_config()
            session.add(config)
            await session.commit()
            await session.refresh(config)

        return config


_sync_engine = None


def _get_sync_engine():
    """Get or create a cached synchronous engine for sync DB access."""
    global _sync_engine
    if _sync_engine is None:
        from sqlmodel import create_engine

        from app.config import settings

        sync_db_url = settings.database_url.replace("+aiosqlite", "")
        _sync_engine = create_engine(sync_db_url)
    return _sync_engine


def get_config_sync() -> AppConfig:
    """Get configuration synchronously for non-async contexts."""
    from sqlmodel import Session, select

    with Session(_get_sync_engine()) as session:
        statement = select(AppConfig).limit(1)
        config = session.exec(statement).first()

        if config is None:
            config = _make_default_config()
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
        sensitive_fields = {"makemkv_key", "tmdb_api_key"}

        _nullable_fields = {"import_watch_path", "fingerprint_server_url"}
        for key, value in kwargs.items():
            if not hasattr(config, key):
                continue
            if value is None and key not in _nullable_fields:
                continue
            # Skip empty strings for sensitive fields (keep existing value)
            if key in sensitive_fields and isinstance(value, str) and not value.strip():
                continue
            setattr(config, key, value)

        await session.commit()
        await session.refresh(config)

        # Ensure paths exist
        await ensure_paths_exist(config)

        # If the TMDB key was rotated, blow away any results fetched with
        # the old key. The TMDB ``@lru_cache``-wrapped fetchers cache by
        # the function arguments alone (not the key), so a rotation
        # would silently keep returning stale results — including
        # successful lookups made with a now-revoked key — until process
        # restart. ConfigWizard is the common rotation entry point.
        if "tmdb_api_key" in kwargs:
            from app.matcher.tmdb_client import clear_caches

            clear_caches()

        # Bridge a changed MakeMKV key into MakeMKV's own settings.conf so
        # makemkvcon picks it up — Engram's config DB and MakeMKV's settings
        # file are otherwise unconnected. No-op for blank keys.
        if "makemkv_key" in kwargs and config.makemkv_key:
            from app.core.makemkv_registration import write_makemkv_settings

            await asyncio.to_thread(write_makemkv_settings, config.makemkv_key)

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
