"""Regression guard for the database connection-pool configuration.

These tests pin the fix for the QueuePool exhaustion that struck multi-season
imports (7 Seinfeld seasons matching concurrently + a rip): SQLAlchemy's async
default pool of 5 + 10 = 15 connections was too small for the app's fan-out, and
the absence of a SQLite busy_timeout meant concurrent writers failed fast with
"database is locked" instead of queueing. See app/config.py:db_pool_size and
app/database.py:set_sqlite_pragma.
"""

import sqlite3

from app.config import settings
from app.database import engine, set_sqlite_pragma


def test_engine_pool_sized_above_async_default():
    """The pool must be sized from settings, comfortably above the 5+10 default."""
    pool = engine.sync_engine.pool
    assert pool.size() == settings.db_pool_size
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
