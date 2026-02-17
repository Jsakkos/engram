"""Server-level configuration from environment variables.

Only contains settings needed before the database is available:
database URL, server host/port, and debug mode. All fields have
defaults — no .env file is required.

All user-configurable settings (paths, API keys, feature flags)
live in the database via AppConfig — see models/app_config.py.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server infrastructure settings. Loaded from environment variables; optionally from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./engram.db"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False


settings = Settings()
