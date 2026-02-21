"""Shared fixtures for unit tests.

Patches async_session everywhere so no unit test touches engram.db.
"""

import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

_unit_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

_unit_session_factory = sessionmaker(_unit_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def isolate_database(monkeypatch):
    """Patch async_session everywhere so no unit test touches engram.db."""
    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Patch via direct module references to avoid name-shadowing in __init__.py
    import app.database as _db_mod

    _config_mod = importlib.import_module("app.services.config_service")
    _jm_mod = importlib.import_module("app.services.job_manager")
    _rc_mod = importlib.import_module("app.services.ripping_coordinator")

    monkeypatch.setattr(_db_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_config_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_jm_mod, "async_session", _unit_session_factory)
    # ripping_coordinator imports async_session locally; patch the source module
    # so that any `from app.database import async_session` gets the patched one.

    yield

    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
