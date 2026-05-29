"""PackCache: decode the server pack format, honor ETag/304, expire on TTL."""

import base64
import json

import pytest
import zstandard

from app.services.fingerprint_pack_cache import PackCache


def _make_pack(tmdb_id: int) -> bytes:
    header = {
        "wire_format_version": 1,
        "pack_format_version": 2,
        "tmdb_id": tmdb_id,
        "n_episodes": 2,
        "generated_at": 1700000000,
    }
    from app.services.zstd_varint_codec import encode_zstd_varint

    e1 = base64.b64encode(encode_zstd_varint([1, 2, 3])).decode()
    e2 = base64.b64encode(encode_zstd_varint([2, 3, 4])).decode()
    lines = [
        json.dumps(header),
        json.dumps({"season": 1, "episode": 1, "fingerprint_b64": e1}),
        json.dumps({"season": 1, "episode": 2, "fingerprint_b64": e2}),
        json.dumps({"kind": "df", "n_episodes": 2, "df": [[1, 1], [2, 2], [3, 2], [4, 1]]}),
    ]
    return zstandard.ZstdCompressor().compress("\n".join(lines).encode())


def test_load_decodes_episodes_and_df(tmp_path):
    cache = PackCache(base_dir=tmp_path)
    cache.path(55).write_bytes(_make_pack(55))
    pack = cache.load(55)
    assert pack is not None
    assert (1, 1) in pack.episodes
    assert pack.episodes[(1, 1)] == {1, 2, 3}
    assert pack.df_map[2] == 2
    assert pack.n_episodes == 2


def test_has_false_when_absent(tmp_path):
    assert PackCache(base_dir=tmp_path).has(999) is False


@pytest.mark.asyncio
async def test_ensure_writes_on_200_and_keeps_on_304(tmp_path, monkeypatch):
    cache = PackCache(base_dir=tmp_path)
    blob = _make_pack(77)

    class FakeResp:
        def __init__(self, status, content=b"", etag=None):
            self.status_code = status
            self.content = content
            self.headers = {"ETag": etag} if etag else {}

    calls = {"n": 0}

    async def fake_get(self, url, headers=None):
        calls["n"] += 1
        if headers and headers.get("If-None-Match") == '"v1"':
            return FakeResp(304)
        return FakeResp(200, blob, '"v1"')

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    assert await cache.ensure(77, "https://server") is True
    assert cache.has(77)
    assert await cache.ensure(77, "https://server") is True
    assert calls["n"] == 2


def test_has_false_when_ttl_expired(tmp_path):
    import json as _json
    import time as _time

    cache = PackCache(base_dir=tmp_path, ttl_seconds=10)
    cache.path(33).write_bytes(b"x")  # file present
    # Manifest entry downloaded 1 hour ago -> older than ttl -> stale.
    (tmp_path / "manifest.json").write_text(
        _json.dumps({"33": {"etag": '"v"', "downloaded_at": _time.time() - 3600}})
    )
    assert cache.has(33) is False


def test_load_returns_none_on_corrupt_file(tmp_path):
    cache = PackCache(base_dir=tmp_path)
    cache.path(44).write_bytes(b"not a zstd frame at all")
    assert cache.load(44) is None
