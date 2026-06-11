"""Disk-backed ASR transcript cache (L2 store).

Whisper transcription of a 30 s audio chunk is expensive (~30-60 s on CPU).
The transcript text depends ONLY on (audio file, offset, duration, ASR model
identity) — it is completely independent of which show/season we later match
against.  Today ``EpisodeMatcher.transcriptions`` memoises results only in an
in-memory dict that is discarded on restart.  This module adds a SQLite layer
underneath that in-memory cache.

The cache lives at ``~/.engram/cache/transcripts.sqlite`` — a separate file
from ``tmdb_cache.sqlite`` so the two caches have independent lifecycle and
pruning strategies.  Like its sibling it is a regen-able artifact that does
NOT need Alembic migrations.

Cache key design
----------------
*file_key* = ``sha1("{resolved_path}|{st_size}|{st_mtime_ns}").hexdigest()``

Encoding size *and* mtime means that a re-rip of the same title produces a new
key, so stale transcripts simply never hit again — they age out via LRU prune.
``file_key_for(path)`` computes this once and the caller passes it to
``get()`` / ``put()`` so stat() is paid only once per chunk even when multiple
offsets are looked up for the same file.

LRU pruning
-----------
To cap unbounded growth (large-library users) the table is pruned to at most
``_MAX_ROWS`` rows ordered by ``last_used_at`` (oldest first).  The check runs
every ``_PUT_PRUNE_INTERVAL`` puts, not on every call.  A module-level counter
tracks puts; the check is fast (one SELECT COUNT(*) before the DELETE).

Fail-safe contract
------------------
Every public function catches all ``sqlite3.Error`` and ``OSError`` exceptions,
logs at WARNING level, and returns ``None`` (or no-ops for ``put``).  A broken
or corrupted cache degrades to "just transcribe again" — it must NEVER break
the calling matcher.

Corrupt-DB recovery
-------------------
If ``sqlite3.connect()`` / ``PRAGMA journal_mode`` / ``CREATE TABLE`` raises
``sqlite3.DatabaseError`` on the *first* connection in a process, the module
deletes the file and retries once.  This is this module's own recovery policy;
the companion ``tmdb_persistent_cache`` module does not implement the same
pattern.

Thread safety
-------------
Same strategy as ``tmdb_persistent_cache``: each thread gets its own
``sqlite3.Connection`` via ``threading.local()``.  WAL mode allows concurrent
readers/writers on the same file.  Schema creation is protected by
``_init_lock`` so threads don't race to ``CREATE TABLE``.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DB_PATH = Path("~/.engram/cache/transcripts.sqlite").expanduser()

# LRU pruning constants
_MAX_ROWS: int = 100_000
_PUT_PRUNE_INTERVAL: int = 200  # check row-count every N puts

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_local = threading.local()
_init_lock = threading.Lock()
_put_counter_lock = threading.Lock()
_put_counter: int = 0


class _SchemaState:
    """One-shot schema-init flag (same pattern as tmdb_persistent_cache).

    Wrapped in an object rather than a bare module global to avoid CodeQL's
    ``unused global variable`` false positive on ``global _initialized``
    read-then-write-across-calls.
    """

    initialized: bool = False


_schema = _SchemaState()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
  file_key     TEXT    NOT NULL,
  start_s      INTEGER NOT NULL,
  duration_s   INTEGER NOT NULL,
  model_key    TEXT    NOT NULL,
  text         TEXT    NOT NULL,
  created_at   INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL,
  PRIMARY KEY (file_key, start_s, duration_s, model_key)
);

CREATE INDEX IF NOT EXISTS idx_transcripts_lru ON transcripts(last_used_at);
"""

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local connection to the transcript cache DB.

    On first use per-process, creates the DB file (and parent directories) and
    applies the schema under ``_init_lock`` so concurrent threads don't race.
    Each subsequent call in the same thread reuses the open connection.

    If the DB file is corrupt (``sqlite3.DatabaseError`` during bootstrap),
    the file is deleted and recreated once.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    if not _schema.initialized:
        with _init_lock:
            if not _schema.initialized:
                _bootstrap_db()
                _schema.initialized = True

    conn = sqlite3.connect(CACHE_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    _local.conn = conn
    return conn


def _bootstrap_db() -> None:
    """Create the DB file + schema.  Called once per process under _init_lock."""
    try:
        CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Parent path is blocked (e.g. a file sits where a directory is needed).
        # Raise so _get_conn() propagates a sqlite3-compatible error that get/put
        # will catch and convert to None / no-op.
        raise sqlite3.OperationalError(
            f"transcript_store: cannot create cache directory: {exc}"
        ) from exc
    try:
        _create_schema()
    except sqlite3.DatabaseError:
        # Corrupt file — delete and retry once.
        logger.warning(f"transcript_store: corrupt DB at {CACHE_DB_PATH}; deleting and recreating")
        try:
            CACHE_DB_PATH.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"transcript_store: could not delete corrupt DB: {exc}")
        _create_schema()


def _create_schema() -> None:
    """Open a bootstrap connection, apply WAL + schema, close immediately."""
    bootstrap = sqlite3.connect(CACHE_DB_PATH, timeout=30)
    try:
        bootstrap.execute("PRAGMA journal_mode=WAL")
        bootstrap.executescript(_SCHEMA)
        bootstrap.commit()
    finally:
        bootstrap.close()


def close() -> None:
    """Close the calling thread's connection and reset the schema-init flag.

    Test fixtures call this when redirecting ``CACHE_DB_PATH`` to a tmp_path
    SQLite file between tests, exactly as ``tmdb_persistent_cache.close()``
    is used in the global ``_isolate_tmdb_persistent_cache`` conftest fixture.

    Connections held by OTHER threads are left open — they drain when their
    owning thread exits.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    _schema.initialized = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def file_key_for(path: str | Path) -> str | None:
    """Compute a stable cache key for *path*.

    Resolves the path, stats it, and returns
    ``sha1("{resolved}|{st_size}|{st_mtime_ns}").hexdigest()``.

    Returns ``None`` (never raises) if the file is missing or unstatable.
    Re-rips of the same title produce a new ``mtime_ns`` → new key, so stale
    entries simply age out via LRU rather than being served.
    """
    try:
        p = Path(path).resolve()
        st = p.stat()
        raw = f"{p}|{st.st_size}|{st.st_mtime_ns}"
        return hashlib.sha1(raw.encode()).hexdigest()
    except (OSError, ValueError) as exc:
        logger.debug(f"transcript_store.file_key_for({path!r}): {exc}")
        return None


def get(file_key: str, start_s: int, duration_s: int, model_key: str) -> str | None:
    """Return the cached transcript text, or ``None`` on a miss.

    *Distinguishes* a cached empty string (``""`` — silent audio) from a cache
    miss (``None``).  On a hit, bumps ``last_used_at`` so active entries survive
    LRU pruning longer.
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT text FROM transcripts "
            "WHERE file_key=? AND start_s=? AND duration_s=? AND model_key=?",
            (file_key, start_s, duration_s, model_key),
        ).fetchone()
        if row is None:
            return None
        # Bump LRU timestamp on every hit.
        now = int(time.time())
        conn.execute(
            "UPDATE transcripts SET last_used_at=? "
            "WHERE file_key=? AND start_s=? AND duration_s=? AND model_key=?",
            (now, file_key, start_s, duration_s, model_key),
        )
        conn.commit()
        return row[0]
    except sqlite3.Error as exc:
        logger.warning(f"transcript_store.get: sqlite error: {exc}")
        return None


def put(file_key: str, start_s: int, duration_s: int, model_key: str, text: str) -> None:
    """Insert or replace a transcript entry.

    Also increments the put counter and triggers an LRU prune every
    ``_PUT_PRUNE_INTERVAL`` puts so the table doesn't grow without bound.
    """
    global _put_counter
    try:
        conn = _get_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO transcripts "
            "(file_key, start_s, duration_s, model_key, text, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_key, start_s, duration_s, model_key, text, now, now),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning(f"transcript_store.put: sqlite error: {exc}")
        return

    # Increment counter outside the try so a DB error above still doesn't
    # corrupt the counter; prune is best-effort anyway.
    with _put_counter_lock:
        _put_counter += 1
        should_prune = (_put_counter % _PUT_PRUNE_INTERVAL) == 0

    if should_prune:
        _prune()


def _prune() -> None:
    """Delete the oldest rows (by last_used_at) until at most _MAX_ROWS remain."""
    try:
        conn = _get_conn()
        (count,) = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        excess = count - _MAX_ROWS
        if excess <= 0:
            return
        conn.execute(
            "DELETE FROM transcripts WHERE rowid IN ("
            "  SELECT rowid FROM transcripts ORDER BY last_used_at ASC LIMIT ?"
            ")",
            (excess,),
        )
        conn.commit()
        logger.debug(f"transcript_store: pruned {excess} rows (was {count}, cap {_MAX_ROWS})")
    except sqlite3.Error as exc:
        logger.warning(f"transcript_store._prune: sqlite error: {exc}")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def reset_module_state_for_tests() -> None:
    """Close the current connection and reset all in-process mutable state.

    Intended for use in test fixtures.  Callers are expected to redirect
    ``CACHE_DB_PATH`` via ``monkeypatch.setattr`` *before or after* this call;
    the redirect takes effect on the next ``get()``/``put()`` that opens a
    new connection.

    Example (pytest)::

        @pytest.fixture(autouse=True)
        def _isolate_transcript_store(tmp_path, monkeypatch):
            import app.matcher.transcript_store as ts
            ts.close()
            monkeypatch.setattr(ts, "CACHE_DB_PATH", tmp_path / "transcripts.sqlite")
            yield
            ts.close()
    """
    global _put_counter
    close()
    with _put_counter_lock:
        _put_counter = 0
