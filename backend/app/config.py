"""Server-level configuration from environment variables.

Only contains settings needed before the database is available:
database URL, server host/port, and debug mode. All fields have
defaults — no .env file is required.

All user-configurable settings (paths, API keys, feature flags)
live in the database via AppConfig — see models/app_config.py.
"""

import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def is_frozen() -> bool:
    """True for packaged (PyInstaller) builds.

    PyInstaller's bootloader sets both ``sys.frozen`` and ``sys._MEIPASS``, but
    they can diverge in the wild — some builds reach the bundled frontend (served
    off ``sys._MEIPASS`` in ``main.py``) yet report ``sys.frozen`` falsy, which
    made the updater wrongly show "dev mode". Treat either signal as
    authoritative so every "am I frozen?" decision agrees.
    """
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _default_database_url() -> str:
    """Return the default database URL, using ~/.engram/ for frozen (PyInstaller) builds."""
    if is_frozen():
        # Frozen build: store DB in a stable, user-writable location
        db_dir = Path.home() / ".engram"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "engram.db"
        return f"sqlite+aiosqlite:///{db_path}"
    # Development: store DB in the working directory (backend/)
    return "sqlite+aiosqlite:///./engram.db"


class Settings(BaseSettings):
    """Server infrastructure settings. Loaded from environment variables; optionally from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = _default_database_url()

    # SQLAlchemy engine echo — logs every emitted SQL statement. Off by default.
    # Deliberately decoupled from `debug`: the E2E backend must run with
    # DEBUG=true (the /api/simulate/* endpoints are gated behind require_debug),
    # but echoing every statement during a simulated rip (a session.get+commit
    # and two broadcasts every 0.1s per title) floods stdout and stalls the
    # single-worker event loop, which surfaced as flaky Playwright timeouts.
    # Set DB_ECHO=true to restore SQL logging for local debugging.
    db_echo: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # CORS (comma-separated origins, or leave empty for dev defaults)
    cors_origins: str = ""

    # Precomputed subtitle-vector cache: base URL of the GitHub Release that hosts
    # the artifact. The format-version tag and filenames are appended at runtime.
    # Overridable (e.g. to point at a test release) via the env var of the same name.
    precomputed_cache_base_url: str = "https://github.com/jsakkos/engram/releases/download"


settings = Settings()
