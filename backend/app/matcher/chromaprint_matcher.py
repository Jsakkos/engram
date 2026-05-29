"""Chromaprint query side (Phase 3).

Per-window classifier with two backends behind one interface:
- LocalPackBackend: queries a decoded on-disk pack (shows the user owns).
- RemoteIdentifyBackend: GET /v1/identify for shows without a local pack.

The title-level orchestration (identify_episode_chromaprint) reuses the existing
EpisodeMatcher windowed-voting machinery — appended in a later task.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Protocol

import httpx
from loguru import logger

from app.matcher.chromaprint_scoring import (
    combined_window_score,
    hash_overlap_pct,
    rarity_weighted_overlap,
    temporal_coherence,
)
from app.services.fingerprint_pack_cache import DecodedPack
from app.services.zstd_varint_codec import encode_zstd_varint


@dataclass
class WindowCandidate:
    tmdb_id: int
    season: int
    episode: int
    tier: str
    hash_overlap_pct: float
    temporal_coherence: float
    rarity_weighted_score: float
    combined_score: float
    offset_seconds: float | None = None


class ChromaprintMatcherBackend(Protocol):
    async def classify_window(
        self, query_hashes: list[int], *, top_k: int = 5
    ) -> list[WindowCandidate]: ...


class LocalPackBackend:
    """Score a window against every episode in a decoded local pack."""

    def __init__(self, pack: DecodedPack) -> None:
        self.pack = pack

    async def classify_window(
        self, query_hashes: list[int], *, top_k: int = 5
    ) -> list[WindowCandidate]:
        out: list[WindowCandidate] = []
        for (season, episode), ref_set in self.pack.episodes.items():
            overlap = hash_overlap_pct(query_hashes, ref_set)
            if overlap == 0.0:
                continue
            temporal = temporal_coherence(query_hashes, ref_set)
            rarity = rarity_weighted_overlap(
                query_hashes, ref_set, self.pack.df_map, self.pack.n_episodes
            )
            out.append(
                WindowCandidate(
                    tmdb_id=self.pack.tmdb_id,
                    season=season,
                    episode=episode,
                    tier="canonical",
                    hash_overlap_pct=overlap,
                    temporal_coherence=temporal,
                    rarity_weighted_score=rarity,
                    combined_score=combined_window_score(overlap, temporal, rarity),
                )
            )
        out.sort(key=lambda c: c.combined_score, reverse=True)
        return out[:top_k]


class RemoteIdentifyBackend:
    """Query GET /v1/identify for a window."""

    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")

    async def classify_window(
        self, query_hashes: list[int], *, top_k: int = 5
    ) -> list[WindowCandidate]:
        blob = encode_zstd_varint(query_hashes)
        fp = base64.urlsafe_b64encode(blob).decode().rstrip("=")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.server_url}/v1/identify", params={"fp": fp, "k": top_k}
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.info(f"Remote identify failed: {e}")
            return []
        out: list[WindowCandidate] = []
        for c in data.get("candidates", []):
            overlap = float(c.get("hash_overlap_pct", 0.0))
            rarity = float(c.get("rarity_weighted_score", 0.0))
            # Server folds temporal into its own ranking and does not return it; use overlap+rarity here.
            out.append(
                WindowCandidate(
                    tmdb_id=int(c["tmdb_id"]),
                    season=int(c["season"]),
                    episode=int(c["episode"]),
                    tier=str(c.get("tier", "canonical")),
                    hash_overlap_pct=overlap,
                    temporal_coherence=0.0,
                    rarity_weighted_score=rarity,
                    combined_score=combined_window_score(overlap, 0.0, rarity),
                    offset_seconds=c.get("offset_seconds"),
                )
            )
        return out


class ChromaprintMatcher:
    """Owns backend selection for one show (tmdb_id)."""

    def __init__(
        self, *, tmdb_id: int, server_url: str, pack_cache, allow_remote_fallthrough: bool = False
    ) -> None:
        self.tmdb_id = tmdb_id
        self.server_url = server_url
        self.pack_cache = pack_cache
        self.allow_remote_fallthrough = allow_remote_fallthrough
        self._local: LocalPackBackend | None = None
        self._remote = RemoteIdentifyBackend(server_url)

    def select_backend(self) -> ChromaprintMatcherBackend:
        if self.pack_cache is not None and self.pack_cache.has(self.tmdb_id):
            pack = self.pack_cache.load(self.tmdb_id)
            if pack is not None:
                self._local = LocalPackBackend(pack)
                return self._local
        return self._remote

    async def classify_window(
        self, query_hashes: list[int], *, top_k: int = 5
    ) -> list[WindowCandidate]:
        backend = self.select_backend()
        cands = await backend.classify_window(query_hashes, top_k=top_k)
        if not cands and isinstance(backend, LocalPackBackend) and self.allow_remote_fallthrough:
            return await self._remote.classify_window(query_hashes, top_k=top_k)
        return cands
