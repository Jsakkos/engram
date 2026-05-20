"""Tests for the disk-backed TMDB cache layer."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.matcher import tmdb_persistent_cache


@pytest.mark.unit
class TestRoundtrip:
    def test_put_then_get_returns_payload(self):
        tmdb_persistent_cache.put("show_id:Breaking Bad", "1396", ttl_seconds=3600)
        assert tmdb_persistent_cache.get("show_id:Breaking Bad") == "1396"

    def test_put_replaces_existing_entry(self):
        tmdb_persistent_cache.put("show_id:X", "100", ttl_seconds=3600)
        tmdb_persistent_cache.put("show_id:X", "200", ttl_seconds=3600)
        assert tmdb_persistent_cache.get("show_id:X") == "200"

    def test_get_unknown_key_returns_none(self):
        assert tmdb_persistent_cache.get("nope") is None

    def test_dict_payload_roundtrips(self):
        payload = {"name": "Breaking Bad", "number_of_seasons": 5}
        tmdb_persistent_cache.put("show_details:1396", payload, ttl_seconds=3600)
        assert tmdb_persistent_cache.get("show_details:1396") == payload


@pytest.mark.unit
class TestTTL:
    def test_expired_entry_returns_none_and_is_deleted(self, monkeypatch):
        # Use a moving "now" — write at t=0, expire after 10s, then read at t=11.
        fake_time = {"value": 0.0}

        def fake_time_fn():
            return fake_time["value"]

        monkeypatch.setattr(tmdb_persistent_cache.time, "time", fake_time_fn)

        tmdb_persistent_cache.put("show_id:Y", "9", ttl_seconds=10)
        fake_time["value"] = 11.0
        assert tmdb_persistent_cache.get("show_id:Y") is None
        # Restore real time and confirm the row was deleted on access.
        monkeypatch.setattr(tmdb_persistent_cache.time, "time", time.time)
        assert tmdb_persistent_cache.get("show_id:Y") is None

    def test_fresh_entry_is_returned(self, monkeypatch):
        fake_time = {"value": 100.0}
        monkeypatch.setattr(tmdb_persistent_cache.time, "time", lambda: fake_time["value"])

        tmdb_persistent_cache.put("show_id:Z", "1", ttl_seconds=60)
        fake_time["value"] = 105.0  # 5s later, still fresh
        assert tmdb_persistent_cache.get("show_id:Z") == "1"


@pytest.mark.unit
class TestClear:
    def test_clear_drops_every_row(self):
        tmdb_persistent_cache.put("a", "1", ttl_seconds=3600)
        tmdb_persistent_cache.put("b", "2", ttl_seconds=3600)
        tmdb_persistent_cache.clear()
        assert tmdb_persistent_cache.get("a") is None
        assert tmdb_persistent_cache.get("b") is None

    def test_clear_is_idempotent_when_db_absent(self, monkeypatch, tmp_path):
        # Point CACHE_DB_PATH at a non-existent location and call clear() with
        # no prior _get_conn() — must not raise.
        tmdb_persistent_cache.close()
        monkeypatch.setattr(
            tmdb_persistent_cache, "CACHE_DB_PATH", tmp_path / "never_created.sqlite"
        )
        tmdb_persistent_cache.clear()  # should be a no-op


@pytest.mark.unit
class TestConcurrentReads:
    def test_many_threads_read_simultaneously(self):
        """The W4 scheduler runs one worker per provider; each reads TMDB
        metadata from its own thread via the shared connection. The connection
        is opened with check_same_thread=False; this smoke-tests that contract."""
        tmdb_persistent_cache.put("hot_key", "1234", ttl_seconds=3600)

        def reader():
            return tmdb_persistent_cache.get("hot_key")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: reader(), range(32)))
        assert all(r == "1234" for r in results)


@pytest.mark.unit
class TestCorruptRow:
    def test_corrupt_json_is_deleted_and_returns_none(self):
        # Write a row with a corrupt payload by reaching into the connection.
        conn = tmdb_persistent_cache._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO tmdb_cache (cache_key, payload, fetched_at, ttl_seconds) "
            "VALUES (?, ?, ?, ?)",
            ("bad_row", "not json {{", time.time(), 3600),
        )
        conn.commit()
        assert tmdb_persistent_cache.get("bad_row") is None
        # And the row should be gone, so a subsequent read on the same key
        # is a clean miss, not another expensive JSON parse attempt.
        row = conn.execute("SELECT 1 FROM tmdb_cache WHERE cache_key = ?", ("bad_row",)).fetchone()
        assert row is None
