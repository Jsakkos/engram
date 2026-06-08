"""Configuration service for managing app settings.

Provides functions to get and update configuration stored in SQLite.
"""

import asyncio
import logging
import sys
import threading
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
# Guards the lazy build of _sync_engine. get_config_sync() is called from
# asyncio.to_thread workers, so two threads can both observe `_sync_engine is
# None` on first call and each build an engine — leaking the loser (an
# up-to-100-connection pool). The lock makes the build happen exactly once.
_sync_engine_lock = threading.Lock()


def _build_sync_engine(sync_db_url: str):
    """Build a synchronous engine configured like the async engine.

    The synchronous twin of the async-pool fix (PR #356). ``get_config_sync()``
    is called from worker threads across hot paths — the matcher's TMDB lookups,
    the curator, the organizer, and per-job subtitle downloads. Under a
    multi-season import those threads can brush SQLAlchemy's default sync pool
    ceiling (pool_size 5 + max_overflow 10 = 15), and without a SQLite
    ``busy_timeout`` a writer that loses the race for SQLite's single write lock
    fails fast with "database is locked" instead of waiting. So this mirrors the
    async engine in ``app/database.py``:

    * Pool sizing from settings — but only for file-backed databases. SQLite
      in-memory URLs resolve to ``SingletonThreadPool``/``StaticPool`` (a single
      shared connection), which reject ``max_overflow``/``pool_timeout`` with a
      ``TypeError``. The async engine sidesteps this because
      ``create_async_engine`` defaults to ``AsyncAdaptedQueuePool`` even for
      in-memory; the sync ``create_engine`` path does not. Production always uses
      a file DB, but a dev pointing ``DATABASE_URL`` at ``:memory:`` must not crash.
    * ``set_sqlite_pragma`` as the connect hook — registered unconditionally so
      every connection (file or memory) gets WAL + ``busy_timeout``.
    * ``check_same_thread=False`` to match the async engine: pooled sync
      connections are handed between ``asyncio.to_thread`` workers, so a
      connection may be used on a different thread than the one that opened it.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from sqlmodel import create_engine

    from app.config import settings
    from app.database import set_sqlite_pragma

    db_path = make_url(sync_db_url).database
    is_memory = not db_path or db_path == ":memory:"

    connect_args = {"check_same_thread": False}
    if is_memory:
        sync_engine = create_engine(sync_db_url, echo=settings.db_echo, connect_args=connect_args)
    else:
        sync_engine = create_engine(
            sync_db_url,
            echo=settings.db_echo,
            connect_args=connect_args,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
        )

    event.listens_for(sync_engine, "connect")(set_sqlite_pragma)
    return sync_engine


def _get_sync_engine():
    """Get or create a cached synchronous engine for sync DB access.

    Double-checked locking: the outer check keeps the steady-state path lock-free,
    while the lock ensures concurrent first callers (asyncio.to_thread workers)
    build the engine exactly once rather than racing and leaking duplicates.
    """
    global _sync_engine
    if _sync_engine is None:
        with _sync_engine_lock:
            if _sync_engine is None:
                from app.config import settings

                sync_db_url = settings.database_url.replace("+aiosqlite", "")
                _sync_engine = _build_sync_engine(sync_db_url)
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


def read_allow_lan_sync() -> bool:
    """Read only the LAN toggle, before init_db()'s reconcilers have run.

    Called from ``resolve_startup_host`` at process start — *before* the lifespan
    runs ``init_db()``. A full ``AppConfig`` SELECT (as ``get_config_sync`` does)
    hydrates every model column, so it trips on any column added since the user's
    DB was created (frozen builds skip Alembic and only reconcile schema later,
    inside ``init_db``). Read the single column we need via raw SQL, tolerant of a
    missing column or table → ``False`` (the safe default; the LAN toggle is off
    by default anyway).
    """
    from sqlalchemy import text

    try:
        with _get_sync_engine().connect() as conn:
            row = conn.execute(text("SELECT allow_lan_access FROM app_config LIMIT 1")).first()
    except Exception:  # noqa: BLE001 — startup must never crash on a config read
        return False
    return bool(row[0]) if row and row[0] is not None else False


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
        # Special handling for sensitive fields: don't overwrite with empty strings.
        # The frontend already omits a blank secret (the `optional()` helper in
        # ConfigWizard), but the backend must independently protect every secret
        # so no client — or future code path — can blank a stored credential by
        # sending "". Keep this set in sync with the redacted fields in
        # GET /api/config.
        sensitive_fields = {
            "makemkv_key",
            "tmdb_api_key",
            "ai_api_key",
            "opensubtitles_api_key",
            "opensubtitles_password",
            "discdb_api_key",
        }

        _nullable_fields = {
            "import_watch_path",
            "fingerprint_server_url",
            "fingerprint_disclosure_accepted_at",
        }
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
