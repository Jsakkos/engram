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


def _default_database_url() -> str:
    """Return the default database URL, using ~/.engram/ for frozen (PyInstaller) builds."""
    if getattr(sys, "frozen", False):
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

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False


settings = Settings()
