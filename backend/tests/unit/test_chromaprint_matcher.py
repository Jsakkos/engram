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


@pytest.mark.asyncio
async def test_identify_episode_chromaprint_votes_winner(monkeypatch, tmp_path):
    """A matcher whose windows all classify to (1,1) yields a (1,1) result with chromaprint_signal."""
    from app.matcher.chromaprint_matcher import identify_episode_chromaprint

    class FakeMatcher:
        chunk_duration = 30
        skip_initial_duration = 90

        def extract_audio_chunk(self, mkv, start, duration=None):
            return tmp_path / f"chunk_{start}.wav"

    class FakeExtractor:
        async def extract(self, wav_path):
            from app.matcher.chromaprint_extractor import ChromaprintResult

            return ChromaprintResult(
                hashes=list(range(100, 340)), duration_seconds=30.0, fpcalc_version="t"
            )

    cm = ChromaprintMatcher(
        tmdb_id=42,
        server_url="https://s",
        pack_cache=type("C", (), {"has": lambda self, t: True, "load": lambda self, t: _pack()})(),
    )

    result = await identify_episode_chromaprint(
        matcher=FakeMatcher(),
        video_file=str(tmp_path / "v.mkv"),
        season_number=1,
        chromaprint_matcher=cm,
        extractor=FakeExtractor(),
        video_duration=1800.0,
        num_points=6,
    )
    assert result is not None
    assert result["season"] == 1 and result["episode"] == 1
    assert result["match_details"]["match_source"] == "chromaprint"
    assert "chromaprint_signal" in result["match_details"]
    assert result["tier"] == "canonical"


@pytest.mark.asyncio
async def test_identify_episode_chromaprint_none_when_no_votes(monkeypatch, tmp_path):
    """When every window classifies below the floor / to nothing, returns None."""
    from app.matcher.chromaprint_matcher import identify_episode_chromaprint

    class FakeMatcher:
        chunk_duration = 30
        skip_initial_duration = 90

        def extract_audio_chunk(self, mkv, start, duration=None):
            return tmp_path / f"chunk_{start}.wav"

    class FakeExtractor:
        async def extract(self, wav_path):
            from app.matcher.chromaprint_extractor import ChromaprintResult

            # hashes that match NO episode in the pack -> no candidates
            return ChromaprintResult(
                hashes=list(range(5_000_000, 5_000_240)), duration_seconds=30.0, fpcalc_version="t"
            )

    cm = ChromaprintMatcher(
        tmdb_id=42,
        server_url="https://s",
        pack_cache=type("C", (), {"has": lambda self, t: True, "load": lambda self, t: _pack()})(),
    )

    result = await identify_episode_chromaprint(
        matcher=FakeMatcher(),
        video_file=str(tmp_path / "v.mkv"),
        season_number=1,
        chromaprint_matcher=cm,
        extractor=FakeExtractor(),
        video_duration=1800.0,
        num_points=6,
    )
    assert result is None


@pytest.mark.asyncio
async def test_identify_episode_chromaprint_filters_wrong_season(tmp_path):
    """A window matching an episode in a DIFFERENT season is filtered out -> None."""
    from app.matcher.chromaprint_matcher import identify_episode_chromaprint
    from app.services.fingerprint_pack_cache import DecodedPack

    s2_pack = DecodedPack(tmdb_id=42, n_episodes=1)
    s2_pack.episodes = {(2, 1): set(range(100, 340))}  # season 2 only
    s2_pack.df_map = {}

    class FakeMatcher:
        chunk_duration = 30
        skip_initial_duration = 90

        def extract_audio_chunk(self, mkv, start, duration=None):
            return tmp_path / f"chunk_{start}.wav"

    class FakeExtractor:
        async def extract(self, wav_path):
            from app.matcher.chromaprint_extractor import ChromaprintResult

            return ChromaprintResult(
                hashes=list(range(100, 340)), duration_seconds=30.0, fpcalc_version="t"
            )

    cm = ChromaprintMatcher(
        tmdb_id=42,
        server_url="https://s",
        pack_cache=type("C", (), {"has": lambda self, t: True, "load": lambda self, t: s2_pack})(),
    )

    # Searching season 1, but the only matching episode is season 2 -> filtered -> None.
    result = await identify_episode_chromaprint(
        matcher=FakeMatcher(),
        video_file=str(tmp_path / "v.mkv"),
        season_number=1,
        chromaprint_matcher=cm,
        extractor=FakeExtractor(),
        video_duration=1800.0,
        num_points=6,
    )
    assert result is None


@pytest.mark.asyncio
async def test_identify_episode_chromaprint_min_vote_count(tmp_path):
    """A single scan point (1 vote) with min_vote_count=2 -> None."""
    from app.matcher.chromaprint_matcher import identify_episode_chromaprint

    class FakeMatcher:
        chunk_duration = 30
        skip_initial_duration = 90

        def extract_audio_chunk(self, mkv, start, duration=None):
            return tmp_path / f"chunk_{start}.wav"

    class FakeExtractor:
        async def extract(self, wav_path):
            from app.matcher.chromaprint_extractor import ChromaprintResult

            return ChromaprintResult(
                hashes=list(range(100, 340)), duration_seconds=30.0, fpcalc_version="t"
            )

    cm = ChromaprintMatcher(
        tmdb_id=42,
        server_url="https://s",
        pack_cache=type("C", (), {"has": lambda self, t: True, "load": lambda self, t: _pack()})(),
    )

    result = await identify_episode_chromaprint(
        matcher=FakeMatcher(),
        video_file=str(tmp_path / "v.mkv"),
        season_number=1,
        chromaprint_matcher=cm,
        extractor=FakeExtractor(),
        video_duration=1800.0,
        num_points=1,
        min_vote_count=2,
    )
    assert result is None
