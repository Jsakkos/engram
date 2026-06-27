"""Tests for headless first-run seeding of allow_lan_access."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.models import AppConfig


@pytest_asyncio.fixture
async def mem_engine():
    """In-memory async SQLite with the full app schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_headless_seeds_allow_lan_on_empty_table(mem_engine, monkeypatch):
    """ENGRAM_HEADLESS=1 + empty app_config seeds allow_lan_access=True."""
    monkeypatch.setenv("ENGRAM_HEADLESS", "1")
    import app.database as db_module

    session_factory = sessionmaker(mem_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_session", session_factory)

    await db_module._seed_headless_defaults()

    async with session_factory() as session:
        row = (
            await session.execute(text("SELECT allow_lan_access FROM app_config LIMIT 1"))
        ).first()
    assert row is not None
    assert bool(row[0]) is True


@pytest.mark.asyncio
async def test_headless_does_not_overwrite_existing_config(mem_engine, monkeypatch):
    """ENGRAM_HEADLESS=1 + existing row with allow_lan=False leaves it unchanged."""
    monkeypatch.setenv("ENGRAM_HEADLESS", "1")
    import app.database as db_module

    session_factory = sessionmaker(mem_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_session", session_factory)

    # Pre-insert a row with allow_lan_access=False (simulates existing install)
    async with session_factory() as session:
        session.add(AppConfig(allow_lan_access=False))
        await session.commit()

    await db_module._seed_headless_defaults()

    async with session_factory() as session:
        row = (
            await session.execute(text("SELECT allow_lan_access FROM app_config LIMIT 1"))
        ).first()
    assert bool(row[0]) is False  # existing setting preserved


@pytest.mark.asyncio
async def test_non_headless_does_not_seed(mem_engine, monkeypatch):
    """Without ENGRAM_HEADLESS=1, _seed_headless_defaults is a no-op."""
    monkeypatch.delenv("ENGRAM_HEADLESS", raising=False)
    import app.database as db_module

    session_factory = sessionmaker(mem_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "async_session", session_factory)

    await db_module._seed_headless_defaults()

    async with session_factory() as session:
        row = (await session.execute(text("SELECT id FROM app_config LIMIT 1"))).first()
    assert row is None  # no row seeded for non-headless builds
