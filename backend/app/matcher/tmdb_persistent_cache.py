"""Disk-backed TMDB cache.

The in-process ``@lru_cache`` on ``tmdb_client._fetch_*_cached`` dedupes
within a single Python run but cannot help across runs of the standalone
``scripts/build_subtitle_cache.py`` — every cold start re-pays the full
TMDB discovery + show/season metadata cost, observed at ~1000 TMDB calls
in a single day on a 300-show build.

This module adds a SQLite layer underneath the LRU. Public wrappers in
``tmdb_client`` check this cache before invoking the LRU-wrapped inner;
a SQLite hit skips both the LRU and the network. A miss falls through to
the network and writes back to SQLite on success.

The cache lives at ``~/.engram/cache/tmdb_cache.sqlite`` — a regen-able
artifact alongside the precomputed-subtitle cache, intentionally NOT in
``engram.db`` so it survives without Alembic migrations and so the
standalone script can use it without the FastAPI lifespan.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

CACHE_DB_PATH = Path("~/.engram/cache/tmdb_cache.sqlite").expanduser()

# TTLs reflect how often each TMDB endpoint's payload changes in practice.
# Names are essentially immutable; episode counts grow on ongoing seasons;
# discovery lists shift daily as vote_count accumulates.
TTL_SHOW_ID = 90 * 86400
TTL_SHOW_DETAILS = 7 * 86400
TTL_SEASON = 7 * 86400
TTL_DISCOVER = 86400
TTL_MOVIE = 90 * 86400  # a released movie's runtime is effectively immutable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tmdb_cache (
  cache_key   TEXT    PRIMARY KEY,
  payload     TEXT    NOT NULL,
  fetched_at  REAL    NOT NULL,
  ttl_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subtitle_coverage (
  tmdb_id          INTEGER NOT NULL,
  season           INTEGER NOT NULL,
  attempted_at     REAL    NOT NULL,
  total_episodes   INTEGER NOT NULL,
  covered_episodes INTEGER NOT NULL,
  coverage_ratio   REAL    NOT NULL,
  PRIMARY KEY (tmdb_id, season)
);
"""

_local = threading.local()
_init_lock = threading.Lock()


@dataclass
class _SchemaState:
    """One-shot schema-init flag.

    Wrapped in a dataclass — instead of a bare module global — because
    CodeQL's ``unused global variable`` checker doesn't track
    read-then-write-across-calls through the ``global`` keyword and
    flags a plain ``_initialized = False`` as dead code. Attribute access
    on a single object reads as a normal reference. Same pattern as
    ``testing_service._OS``.
    """

    initialized: bool = False


_schema = _SchemaState()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local connection to the cache DB.

    Python's ``sqlite3`` does NOT allow concurrent ``execute()`` calls on
    a single connection — even with ``check_same_thread=False`` — and
    raises ``InterfaceError: bad parameter or other API misuse``. Each
    thread therefore opens its own connection. WAL mode lets multiple
    connections read/write the same DB file safely.

    Schema creation is idempotent and runs once per process under
    ``_init_lock`` so threads don't race to ``CREATE TABLE``.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    if not _schema.initialized:
        with _init_lock:
            if not _schema.initialized:
                CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                bootstrap = sqlite3.connect(CACHE_DB_PATH, timeout=30)
                try:
                    bootstrap.execute("PRAGMA journal_mode=WAL")
                    bootstrap.executescript(_SCHEMA)
                    bootstrap.commit()
                finally:
                    bootstrap.close()
                _schema.initialized = True

    conn = sqlite3.connect(CACHE_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    _local.conn = conn
    return conn


def get_conn() -> sqlite3.Connection:
    """Public alias for the thread-local connection.

    Modules that share this SQLite file (currently ``coverage_tracker``)
    use this entry point so the connection-management strategy stays an
    internal detail of ``tmdb_persistent_cache`` — they don't reach into
    the underscore-prefixed ``_get_conn``.
    """
    return _get_conn()


def get(cache_key: str) -> Any | None:
    """Return the cached payload if present and fresh, otherwise None.

    Expired rows are deleted on access so the table doesn't grow
    unbounded for keys that rotate (e.g. discover pages).
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT payload, fetched_at, ttl_seconds FROM tmdb_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    payload, fetched_at, ttl_seconds = row
    if (time.time() - fetched_at) >= ttl_seconds:
        conn.execute("DELETE FROM tmdb_cache WHERE cache_key = ?", (cache_key,))
        conn.commit()
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # A corrupt row shouldn't poison every future lookup; drop it.
        logger.warning(f"Corrupt JSON in tmdb_cache for key={cache_key!r}; deleting row")
        conn.execute("DELETE FROM tmdb_cache WHERE cache_key = ?", (cache_key,))
        conn.commit()
        return None


def is_cached(cache_key: str) -> bool:
    """Return True iff a fresh (unexpired) entry exists for ``cache_key``.

    Read-only companion to :func:`get` for diagnostics. Unlike ``get`` it
    never creates the cache file and never deletes expired rows, so it is
    safe to call while assembling a bug report. Returns False when the DB
    doesn't exist yet.
    """
    if not CACHE_DB_PATH.exists():
        return False
    conn = _get_conn()
    row = conn.execute(
        "SELECT fetched_at, ttl_seconds FROM tmdb_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row is None:
        return False
    fetched_at, ttl_seconds = row
    return (time.time() - fetched_at) < ttl_seconds


def put(cache_key: str, payload: Any, ttl_seconds: int) -> None:
    """Insert or replace a cache entry. ``payload`` must be JSON-serializable."""
    serialized = json.dumps(payload, default=str)
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO tmdb_cache "
        "(cache_key, payload, fetched_at, ttl_seconds) VALUES (?, ?, ?, ?)",
        (cache_key, serialized, time.time(), ttl_seconds),
    )
    conn.commit()


def clear() -> None:
    """Drop every cached row. Called from ``tmdb_client.clear_caches()``
    after a TMDB API key rotation so stale entries from the revoked key
    don't satisfy lookups under the new credentials."""
    if not CACHE_DB_PATH.exists():
        return
    conn = _get_conn()
    conn.execute("DELETE FROM tmdb_cache")
    conn.commit()


def close() -> None:
    """Close the calling thread's connection and reset the schema-init flag
    so the next caller re-initialises against a potentially-new
    ``CACHE_DB_PATH``. Test fixtures use this when redirecting the cache
    to a tmp_path-scoped SQLite file between tests.

    Connections held by OTHER threads remain open (we can't safely reach
    into another thread's thread-local). In normal use this is harmless:
    the cache is regenerated at need and orphan connections drain when
    their owning thread exits.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    _schema.initialized = False
