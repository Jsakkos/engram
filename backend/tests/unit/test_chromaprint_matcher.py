"""ChromaprintMatcher backends: local pack ranking, remote URL/JSON mapping, selection."""

import pytest

from app.matcher.chromaprint_matcher import (
    ChromaprintMatcher,
    LocalPackBackend,
    RemoteIdentifyBackend,
)
from app.services.fingerprint_pack_cache import DecodedPack


def _pack() -> DecodedPack:
    p = DecodedPack(tmdb_id=42, n_episodes=2)
    p.episodes = {(1, 1): set(range(100, 340)), (1, 2): set(range(900, 1140))}
    p.df_map = {}
    return p


@pytest.mark.asyncio
async def test_local_backend_ranks_correct_episode():
    backend = LocalPackBackend(_pack())
    query = list(range(100, 340))  # exactly episode (1,1)
    cands = await backend.classify_window(query, top_k=2)
    assert cands[0].season == 1 and cands[0].episode == 1
    assert cands[0].hash_overlap_pct > 0.9


@pytest.mark.asyncio
async def test_remote_backend_builds_url_and_maps_json(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "candidates": [
                    {
                        "tmdb_id": 42,
                        "season": 1,
                        "episode": 5,
                        "offset_seconds": None,
                        "hash_overlap_pct": 0.88,
                        "rarity_weighted_score": 0.7,
                        "tier": "canonical",
                    }
                ]
            }

        def raise_for_status(self):
            pass

    async def fake_get(self, url, params=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    backend = RemoteIdentifyBackend("https://server")
    cands = await backend.classify_window([1, 2, 3], top_k=5)
    assert "/v1/identify" in captured["url"]
    assert captured["params"]["k"] == 5
    assert cands[0].episode == 5 and cands[0].tier == "canonical"


def test_select_backend_prefers_local_when_pack_present():
    pack = _pack()

    class FakeCache:
        def has(self, tmdb_id):
            return True

        def load(self, tmdb_id):
            return pack

    m = ChromaprintMatcher(tmdb_id=42, server_url="https://s", pack_cache=FakeCache())
    assert isinstance(m.select_backend(), LocalPackBackend)


def test_select_backend_remote_when_no_pack():
    class FakeCache:
        def has(self, tmdb_id):
            return False

        def load(self, tmdb_id):
            return None

    m = ChromaprintMatcher(tmdb_id=42, server_url="https://s", pack_cache=FakeCache())
    assert isinstance(m.select_backend(), RemoteIdentifyBackend)
