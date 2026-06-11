"""Tests for the disk-backed ASR transcript cache (transcript_store.py).

Fixture strategy mirrors ``test_tmdb_persistent_cache.py``:
- An autouse fixture redirects ``CACHE_DB_PATH`` to a per-test tmp_path file
  via monkeypatch + ``reset_module_state_for_tests()``, so tests NEVER touch
  ``~/.engram/cache/``.
- The fixture also resets the put-counter and bootstrap-warning latch so every
  test starts from a clean state.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.matcher.transcript_store as ts

# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_transcript_store(tmp_path, monkeypatch):
    """Redirect transcript_store to a per-test SQLite file in tmp_path."""
    monkeypatch.setattr(ts, "CACHE_DB_PATH", tmp_path / "transcripts.sqlite")
    ts.reset_module_state_for_tests()
    yield
    ts.reset_module_state_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MK = "faster-whisper_small_cpu_int8"  # a representative model_key


def _put(fk: str, start: int = 0, dur: int = 30, mk: str = _MK, text: str = "hello") -> None:
    ts.put(fk, start, dur, mk, text)


def _get(fk: str, start: int = 0, dur: int = 30, mk: str = _MK) -> str | None:
    return ts.get(fk, start, dur, mk)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoundtrip:
    def test_put_then_get_returns_text(self, tmp_path):
        fk = "abc123"
        ts.put(fk, 0, 30, _MK, "hello world")
        assert ts.get(fk, 0, 30, _MK) == "hello world"

    def test_miss_returns_none(self):
        assert ts.get("no_such_key", 0, 30, _MK) is None

    def test_cached_empty_string_returns_empty_not_none(self, tmp_path):
        """Silent audio produces "" — must be distinguished from a cache miss."""
        fk = "silent_file_key"
        ts.put(fk, 0, 30, _MK, "")
        result = ts.get(fk, 0, 30, _MK)
        assert result == ""
        assert result is not None

    def test_put_replaces_existing_entry(self):
        fk = "replace_me"
        ts.put(fk, 0, 30, _MK, "first")
        ts.put(fk, 0, 30, _MK, "second")
        assert ts.get(fk, 0, 30, _MK) == "second"

    def test_different_start_s_are_independent_entries(self):
        fk = "multi_offset"
        ts.put(fk, 0, 30, _MK, "zero")
        ts.put(fk, 30, 30, _MK, "thirty")
        assert ts.get(fk, 0, 30, _MK) == "zero"
        assert ts.get(fk, 30, 30, _MK) == "thirty"

    def test_different_model_keys_are_independent(self):
        fk = "model_diff"
        ts.put(fk, 0, 30, "model_a", "text-a")
        ts.put(fk, 0, 30, "model_b", "text-b")
        assert ts.get(fk, 0, 30, "model_a") == "text-a"
        assert ts.get(fk, 0, 30, "model_b") == "text-b"

    def test_different_duration_s_are_independent(self):
        fk = "dur_diff"
        ts.put(fk, 0, 30, _MK, "30s-text")
        ts.put(fk, 0, 60, _MK, "60s-text")
        assert ts.get(fk, 0, 30, _MK) == "30s-text"
        assert ts.get(fk, 0, 60, _MK) == "60s-text"


# ---------------------------------------------------------------------------
# Numeric key normalisation tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNumericKeyNormalisation:
    def test_float_duration_put_int_get_hits(self):
        """put() with float duration, get() with truncated int → cache hit."""
        fk = "float_dur"
        ts.put(fk, 0, 1372.416, _MK, "float stored")
        # 1372.416 rounds to 1372 — both ints and the rounded float should hit.
        assert ts.get(fk, 0, 1372, _MK) == "float stored"
        assert ts.get(fk, 0, 1372.0, _MK) == "float stored"

    def test_float_duration_put_float_get_hits(self):
        """put() and get() with the same float duration → cache hit."""
        fk = "float_same"
        ts.put(fk, 0, 1372.416, _MK, "roundtrip")
        assert ts.get(fk, 0, 1372.416, _MK) == "roundtrip"

    def test_float_start_s_normalised(self):
        """put() with float start_s, get() with equivalent int → hit."""
        fk = "float_start"
        ts.put(fk, 30.7, 30, _MK, "offset text")
        assert ts.get(fk, 31, 30, _MK) == "offset text"

    def test_float_duration_slightly_different_rounds_to_same(self):
        """Floats that round to the same integer resolve to the same row."""
        fk = "float_rounding"
        ts.put(fk, 0, 30.4, _MK, "text")
        assert ts.get(fk, 0, 30.6, _MK) is None  # rounds to 31, not 30
        assert ts.get(fk, 0, 30.3, _MK) == "text"  # rounds to 30


# ---------------------------------------------------------------------------
# file_key=None short-circuit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoneFileKey:
    def test_get_with_none_file_key_returns_none(self):
        """get() must return None silently when file_key is None."""
        result = ts.get(None, 0, 30, _MK)
        assert result is None

    def test_put_with_none_file_key_does_not_raise(self):
        """put() must be a no-op when file_key is None."""
        try:
            ts.put(None, 0, 30, _MK, "text")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"put(None, ...) raised unexpectedly: {exc}")

    def test_put_with_none_file_key_writes_nothing(self):
        """After put(None, ...), the DB should have no rows."""
        ts.put(None, 0, 30, _MK, "text")
        conn = ts._get_conn()
        (count,) = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        assert count == 0

    def test_get_with_none_does_not_touch_db(self, monkeypatch):
        """get(None, ...) short-circuits before opening a DB connection."""
        opened = []
        original_get_conn = ts._get_conn

        def tracking_get_conn():
            opened.append(True)
            return original_get_conn()

        monkeypatch.setattr(ts, "_get_conn", tracking_get_conn)
        ts.get(None, 0, 30, _MK)
        assert not opened, "_get_conn should not be called when file_key is None"


# ---------------------------------------------------------------------------
# file_key_for tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileKeyFor:
    def test_same_file_same_key(self, tmp_path):
        f = tmp_path / "audio.mkv"
        f.write_bytes(b"x" * 1000)
        k1 = ts.file_key_for(f)
        k2 = ts.file_key_for(f)
        assert k1 is not None
        assert k1 == k2

    def test_key_is_40_char_hex(self, tmp_path):
        f = tmp_path / "audio.mkv"
        f.write_bytes(b"data")
        k = ts.file_key_for(f)
        assert k is not None
        assert len(k) == 40
        assert all(c in "0123456789abcdef" for c in k)

    def test_different_content_same_name_different_key(self, tmp_path):
        """Changing file content (different size) → different key."""
        f = tmp_path / "audio.mkv"
        f.write_bytes(b"original_content")
        k1 = ts.file_key_for(f)
        # Write more bytes so st_size definitely changes.
        f.write_bytes(b"original_content_plus_extra_bytes")
        k2 = ts.file_key_for(f)
        assert k1 != k2

    def test_touch_mtime_gives_different_key(self, tmp_path):
        """Touching mtime (re-rip same size) gives a new key."""
        f = tmp_path / "audio.mkv"
        f.write_bytes(b"x" * 100)
        k1 = ts.file_key_for(f)
        # Advance mtime by 2 seconds to ensure ns-level difference.
        new_mtime = f.stat().st_mtime + 2
        import os

        os.utime(f, (new_mtime, new_mtime))
        k2 = ts.file_key_for(f)
        assert k1 != k2

    def test_missing_file_returns_none(self, tmp_path):
        missing = tmp_path / "does_not_exist.mkv"
        assert ts.file_key_for(missing) is None

    def test_null_char_in_path_returns_none(self):
        """file_key_for must not raise on paths containing embedded null bytes."""
        assert ts.file_key_for("foo\x00bar") is None


# ---------------------------------------------------------------------------
# get bumps last_used_at
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLastUsedAt:
    def test_get_bumps_last_used_at(self, monkeypatch):
        """A cache hit must update last_used_at so LRU order reflects access."""
        fake_time = {"value": 1_000_000}
        monkeypatch.setattr(ts.time, "time", lambda: fake_time["value"])

        fk = "lru_bump"
        ts.put(fk, 0, 30, _MK, "text")

        # Advance clock and perform a get — last_used_at must reflect the new time.
        fake_time["value"] = 2_000_000
        ts.get(fk, 0, 30, _MK)

        conn = ts._get_conn()
        row = conn.execute(
            "SELECT last_used_at FROM transcripts "
            "WHERE file_key=? AND start_s=0 AND duration_s=30 AND model_key=?",
            (fk, _MK),
        ).fetchone()
        assert row is not None
        assert row[0] == 2_000_000

    def test_prune_keeps_recently_accessed_entry(self, monkeypatch):
        """get() bumps last_used_at so the accessed entry survives pruning."""
        # Override constants: cap = 2, check every put.
        monkeypatch.setattr(ts, "_MAX_ROWS", 2)
        monkeypatch.setattr(ts, "_PUT_PRUNE_INTERVAL", 1)

        fake_time = {"value": 1000}
        monkeypatch.setattr(ts.time, "time", lambda: fake_time["value"])

        # Put entry A at t=1000.
        ts.put("A", 0, 30, _MK, "a")

        # Advance time and put entry B.
        fake_time["value"] = 2000
        ts.put("B", 0, 30, _MK, "b")

        # Access A at t=3000 — bumps its last_used_at past B.
        fake_time["value"] = 3000
        ts.get("A", 0, 30, _MK)

        # Put entry C at t=4000 — triggers prune; should evict B (oldest last_used_at).
        fake_time["value"] = 4000
        ts.put("C", 0, 30, _MK, "c")

        # A and C survive; B is evicted.
        assert ts.get("A", 0, 30, _MK) == "a"
        assert ts.get("C", 0, 30, _MK) == "c"
        assert ts.get("B", 0, 30, _MK) is None


# ---------------------------------------------------------------------------
# LRU prune tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLRUPrune:
    def test_prune_removes_oldest_rows(self, monkeypatch):
        """With cap=3 and interval=1, inserting 5 rows prunes down to 3 oldest."""
        monkeypatch.setattr(ts, "_MAX_ROWS", 3)
        monkeypatch.setattr(ts, "_PUT_PRUNE_INTERVAL", 1)

        fake_time = {"value": 1000}
        monkeypatch.setattr(ts.time, "time", lambda: fake_time["value"])

        for i in range(5):
            fake_time["value"] = 1000 + i
            ts.put(f"key{i}", 0, 30, _MK, f"text{i}")

        # key0 and key1 had lowest last_used_at and should be pruned.
        assert ts.get("key0", 0, 30, _MK) is None
        assert ts.get("key1", 0, 30, _MK) is None
        # key2, key3, key4 survive.
        assert ts.get("key2", 0, 30, _MK) == "text2"
        assert ts.get("key3", 0, 30, _MK) == "text3"
        assert ts.get("key4", 0, 30, _MK) == "text4"

    def test_prune_not_triggered_until_interval(self, monkeypatch):
        """With interval=5, prune fires only on every 5th put."""
        monkeypatch.setattr(ts, "_MAX_ROWS", 2)
        monkeypatch.setattr(ts, "_PUT_PRUNE_INTERVAL", 5)

        fake_time = {"value": 1000}
        monkeypatch.setattr(ts.time, "time", lambda: fake_time["value"])

        # Insert 4 rows — prune should NOT fire yet (counter at 4, not divisible by 5).
        for i in range(4):
            fake_time["value"] = 1000 + i
            ts.put(f"k{i}", 0, 30, _MK, f"t{i}")

        conn = ts._get_conn()
        (count,) = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        assert count == 4  # no prune yet

        # 5th put triggers prune — now capped at 2.
        fake_time["value"] = 1004
        ts.put("k4", 0, 30, _MK, "t4")
        (count,) = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        assert count == 2

    def test_prune_is_no_op_when_under_cap(self, monkeypatch):
        """_prune() is a no-op when row count is at or below _MAX_ROWS."""
        monkeypatch.setattr(ts, "_MAX_ROWS", 100)
        monkeypatch.setattr(ts, "_PUT_PRUNE_INTERVAL", 1)

        for i in range(5):
            ts.put(f"safe{i}", 0, 30, _MK, "x")

        conn = ts._get_conn()
        (count,) = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        assert count == 5  # no rows deleted


# ---------------------------------------------------------------------------
# Fail-safe / corruption tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailSafe:
    def test_corrupt_db_file_recovers_on_first_connect(self, tmp_path, monkeypatch):
        """A corrupt DB is deleted and recreated; get/put work after recovery."""
        corrupt_path = tmp_path / "bad.sqlite"
        corrupt_path.write_bytes(b"THIS IS NOT A SQLITE DATABASE FILE")
        monkeypatch.setattr(ts, "CACHE_DB_PATH", corrupt_path)
        ts.reset_module_state_for_tests()

        # Should not raise — module recovers by deleting and recreating.
        ts.put("fk", 0, 30, _MK, "hello")
        assert ts.get("fk", 0, 30, _MK) == "hello"

    def test_get_does_not_raise_on_sqlite_error(self, monkeypatch):
        """If the underlying connection raises sqlite3.Error, get() returns None."""

        def bad_get_conn():
            raise sqlite3.OperationalError("simulated DB error")

        monkeypatch.setattr(ts, "_get_conn", bad_get_conn)
        result = ts.get("fk", 0, 30, _MK)
        assert result is None

    def test_put_does_not_raise_on_sqlite_error(self, monkeypatch):
        """If the underlying connection raises sqlite3.Error, put() no-ops."""

        def bad_get_conn():
            raise sqlite3.OperationalError("simulated DB error")

        monkeypatch.setattr(ts, "_get_conn", bad_get_conn)
        try:
            ts.put("fk", 0, 30, _MK, "text")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"put() raised unexpectedly: {exc}")

    def test_mkdir_failure_get_returns_none(self, tmp_path, monkeypatch):
        """When the cache directory cannot be created (a regular file blocks mkdir),
        get() returns None and put() does not raise — the fail-safe degrades gracefully."""
        # Create a regular file at the path where the cache directory would go.
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"I am a file, not a directory")
        blocked_db = blocker / "transcripts.sqlite"

        monkeypatch.setattr(ts, "CACHE_DB_PATH", blocked_db)
        ts.reset_module_state_for_tests()

        assert ts.get("fk", 0, 30, _MK) is None
        # put() must not raise even though the directory cannot be created.
        ts.put("fk", 0, 30, _MK, "text")

    def test_file_key_for_does_not_raise_on_missing_file(self, tmp_path):
        result = ts.file_key_for(tmp_path / "ghost.mkv")
        assert result is None

    def test_degraded_mode_warns_once_then_latches(self, monkeypatch):
        """First bootstrap failure sets the warning latch; subsequent calls do not reset it.

        We verify the latch mechanics directly (rather than capturing loguru output,
        which does not propagate to stdlib caplog by default).  The latch is the
        mechanism that causes subsequent failures to log at DEBUG instead of WARNING.
        """
        call_count = [0]

        def failing_get_conn():
            call_count[0] += 1
            raise sqlite3.OperationalError("simulated persistent failure")

        monkeypatch.setattr(ts, "_get_conn", failing_get_conn)

        # Before any failure the latch is off.
        assert ts._bootstrap_warned is False

        # First failure sets the latch.
        ts.get("fk", 0, 30, _MK)
        assert ts._bootstrap_warned is True

        # Subsequent failures leave it set (no reset between calls in degraded mode).
        ts.get("fk", 0, 30, _MK)
        ts.get("fk", 0, 30, _MK)
        assert ts._bootstrap_warned is True

        # All 3 calls still attempted the DB (no early bail-out on failure).
        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThreadSafety:
    def test_concurrent_put_and_get(self):
        """Multiple threads doing put/get on distinct keys must not error."""
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                fk = f"thread_{thread_id}"
                for i in range(10):
                    ts.put(fk, i * 30, 30, _MK, f"text_{thread_id}_{i}")
                for i in range(10):
                    result = ts.get(fk, i * 30, 30, _MK)
                    assert result == f"text_{thread_id}_{i}", (
                        f"Thread {thread_id} offset {i}: expected text_{thread_id}_{i!r}, "
                        f"got {result!r}"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(16)))

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_put_shared_key(self):
        """Multiple threads writing the same key must not raise (last-write wins)."""
        errors: list[Exception] = []

        def writer(thread_id: int) -> None:
            try:
                ts.put("shared_key", 0, 30, _MK, f"value_{thread_id}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(writer, range(32)))

        assert not errors
        # The key must be readable (some value must have won).
        result = ts.get("shared_key", 0, 30, _MK)
        assert result is not None
        assert result.startswith("value_")


# ---------------------------------------------------------------------------
# close() / reset_for_tests() helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCloseAndReset:
    def test_close_allows_db_path_redirect(self, tmp_path, monkeypatch):
        """After close(), a new CACHE_DB_PATH takes effect on next get/put."""
        ts.put("original", 0, 30, _MK, "data")

        new_db = tmp_path / "fresh.sqlite"
        monkeypatch.setattr(ts, "CACHE_DB_PATH", new_db)
        ts.reset_module_state_for_tests()

        # The new DB is empty — the "original" key should not be found.
        assert ts.get("original", 0, 30, _MK) is None

    def test_reset_module_state_resets_counter(self, tmp_path, monkeypatch):
        """reset_module_state_for_tests() zeros the put counter."""
        # Manually bump the counter.
        with ts._put_counter_lock:
            ts._put_counter = 99
        new_db = tmp_path / "reset.sqlite"
        monkeypatch.setattr(ts, "CACHE_DB_PATH", new_db)
        ts.reset_module_state_for_tests()
        with ts._put_counter_lock:
            assert ts._put_counter == 0

    def test_reset_clears_bootstrap_warned_latch(self, monkeypatch):
        """reset_module_state_for_tests() resets the warning latch so the next
        failure emits a WARNING again (recovery possible after env fix)."""

        def failing_get_conn():
            raise sqlite3.OperationalError("persistent failure")

        monkeypatch.setattr(ts, "_get_conn", failing_get_conn)
        # Trigger a failure to set the latch.
        ts.get("fk", 0, 30, _MK)
        assert ts._bootstrap_warned is True

        # Reset should clear the latch.
        ts.reset_module_state_for_tests()
        assert ts._bootstrap_warned is False
