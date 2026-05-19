"""Shared fixtures for unit tests.

Patches async_session and the cached sync engine so no unit test touches
the real engram.db.
"""

import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

_unit_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

_unit_session_factory = sessionmaker(_unit_engine, class_=AsyncSession, expire_on_commit=False)

# Separate sync engine for get_config_sync() callers (organizer, analyst, etc.).
# StaticPool keeps the in-memory database alive across connections within the
# same process.
_unit_sync_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(autouse=True)
async def isolate_database(monkeypatch):
    """Patch async_session + sync engine so no unit test touches engram.db."""
    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    SQLModel.metadata.create_all(_unit_sync_engine)

    # Patch via direct module references to avoid name-shadowing in __init__.py
    import app.database as _db_mod

    _config_mod = importlib.import_module("app.services.config_service")
    _jm_mod = importlib.import_module("app.services.job_manager")
    _rc_mod = importlib.import_module("app.services.ripping_coordinator")

    monkeypatch.setattr(_db_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_config_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_jm_mod, "async_session", _unit_session_factory)
    # ripping_coordinator imports async_session at module level, so patch its
    # own reference too — patching app.database alone won't reach it.
    monkeypatch.setattr(_rc_mod, "async_session", _unit_session_factory)

    # Redirect the cached sync engine in config_service so get_config_sync()
    # uses the in-memory test database instead of connecting to engram.db.
    monkeypatch.setattr(_config_mod, "_sync_engine", _unit_sync_engine)
    monkeypatch.setattr(_config_mod, "_get_sync_engine", lambda: _unit_sync_engine)

    yield

    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    SQLModel.metadata.drop_all(_unit_sync_engine)
