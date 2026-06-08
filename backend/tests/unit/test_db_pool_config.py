"""Regression guard for the database connection-pool configuration.

These tests pin the fix for the QueuePool exhaustion that struck multi-season
imports (7 Seinfeld seasons matching concurrently + a rip): SQLAlchemy's async
default pool of 5 + 10 = 15 connections was too small for the app's fan-out, and
the absence of a SQLite busy_timeout meant concurrent writers failed fast with
"database is locked" instead of queueing. See app/config.py:db_pool_size and
app/database.py:set_sqlite_pragma.
"""

import sqlite3

import sqlalchemy

from app.config import settings
from app.database import engine, set_sqlite_pragma
from app.services.config_service import _build_sync_engine


def test_engine_pool_sized_above_async_default():
    """The pool must be sized from settings, comfortably above the 5+10 default."""
    pool = engine.sync_engine.pool
    assert pool.size() == settings.db_pool_size
    # _max_overflow is a private SQLAlchemy attr (no public accessor for the
    # overflow ceiling exists); if a future upgrade renames it this assertion
    # turns into an AttributeError — verify on SQLAlchemy bumps.
    assert pool._max_overflow == settings.db_max_overflow

    # The whole point of the fix: the ceiling must exceed the old default of 15
    # so a multi-season import's concurrent checkouts don't time out.
    assert settings.db_pool_size + settings.db_max_overflow > 15


def test_busy_timeout_pragma_applied():
    """set_sqlite_pragma must set a non-zero busy_timeout so writers wait, not error."""
    conn = sqlite3.connect(":memory:")
    try:
        set_sqlite_pragma(conn, None)
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 30000
    finally:
        conn.close()


def test_pragma_sets_synchronous_normal():
    """Co-pinned: WAL's companion synchronous=NORMAL stays applied."""
    conn = sqlite3.connect(":memory:")
    try:
        set_sqlite_pragma(conn, None)
        # synchronous: 0=OFF, 1=NORMAL, 2=FULL
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert synchronous == 1
    finally:
        conn.close()


def test_sync_engine_pool_sized_above_async_default(tmp_path):
    """The SYNC engine (config_service._build_sync_engine) must match the async pool.

    The synchronous twin of the async-pool fix: get_config_sync() is called from
    worker threads across hot paths (matcher TMDB lookups, curator, organizer,
    subtitle downloads). A multi-season import can brush the old 5+10=15 default
    ceiling. Pin the same settings-driven sizing for the file-backed (production)
    engine.
    """
    db_file = (tmp_path / "pool.db").as_posix()
    eng = _build_sync_engine(f"sqlite:///{db_file}")
    try:
        pool = eng.pool
        assert pool.size() == settings.db_pool_size
        # _max_overflow is a private SQLAlchemy attr (no public accessor); the
        # same caveat as the async test applies on SQLAlchemy bumps.
        assert pool._max_overflow == settings.db_max_overflow
        assert settings.db_pool_size + settings.db_max_overflow > 15
    finally:
        eng.dispose()


def test_sync_engine_registers_pragma_hook(tmp_path):
    """The sync engine must wire set_sqlite_pragma so its connections get
    busy_timeout (otherwise its writers fail fast with 'database is locked'
    instead of waiting for SQLite's single writer lock — the whole point of the
    fix). Without the connect hook a sync connection has busy_timeout=0.
    """
    db_file = (tmp_path / "pool.db").as_posix()
    eng = _build_sync_engine(f"sqlite:///{db_file}")
    try:
        assert sqlalchemy.event.contains(eng, "connect", set_sqlite_pragma)
    finally:
        eng.dispose()


def test_sync_engine_in_memory_does_not_crash_and_keeps_pragma():
    """In-memory SQLite uses SingletonThreadPool, which rejects QueuePool sizing
    kwargs (TypeError). The builder must NOT pass them for a memory URL — yet must
    STILL register the pragma hook so even an in-memory DB gets busy_timeout. This
    guards a dev who sets DATABASE_URL=sqlite:///:memory: from a startup crash on
    the first get_config_sync() call.
    """
    eng = _build_sync_engine("sqlite:///:memory:")
    try:
        # No QueuePool sizing on a non-QueuePool — but the connect hook stays wired.
        assert sqlalchemy.event.contains(eng, "connect", set_sqlite_pragma)
    finally:
        eng.dispose()
