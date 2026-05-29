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


def _scan_points(
    video_duration: float, num_points: int, skip_initial: float, chunk_duration: int
) -> list[float]:
    """Evenly-spaced start times across the body of the file (mirrors identify_episode)."""
    usable_start = min(skip_initial, max(0.0, video_duration - chunk_duration))
    usable_end = max(usable_start, video_duration - chunk_duration)
    if num_points <= 1 or usable_end <= usable_start:
        return [usable_start]
    step = (usable_end - usable_start) / (num_points - 1)
    return [usable_start + i * step for i in range(num_points)]


async def identify_episode_chromaprint(
    *,
    matcher,
    video_file: str,
    season_number: int,
    chromaprint_matcher: ChromaprintMatcher,
    extractor,
    video_duration: float,
    num_points: int = 10,
    min_vote_count: int = 2,
    per_window_floor: float = 0.30,
):
    """Chromaprint-first episode identification reusing EpisodeMatcher voting machinery.

    Returns a dict shaped like EpisodeMatcher.identify_episode (season, episode,
    confidence, score, tier, match_details, runner_ups), or None on no usable votes.
    """
    from app.matcher.episode_identification import MatchCoverage, _attach_calibrated_confidence

    coverages: dict[str, MatchCoverage] = {}
    tiers: dict[str, str] = {}
    sig_acc: dict[str, dict[str, float]] = {}
    chunk_len = matcher.chunk_duration

    points = _scan_points(video_duration, num_points, matcher.skip_initial_duration, chunk_len)
    for start in points:
        try:
            wav = matcher.extract_audio_chunk(video_file, start, chunk_len)
            fp = await extractor.extract(str(wav))
        except Exception as e:  # noqa: BLE001 — best-effort per window
            logger.debug(f"chromaprint window {start:.0f}s skipped: {e}")
            continue
        cands = await chromaprint_matcher.classify_window(fp.hashes, top_k=3)
        if not cands:
            continue
        best = cands[0]
        if best.combined_score < per_window_floor:
            continue
        key = f"S{best.season:02d}E{best.episode:02d}"
        if key not in coverages:
            coverages[key] = MatchCoverage(key, video_duration, video_duration)
            tiers[key] = best.tier
            sig_acc[key] = {"overlap": 0.0, "temporal": 0.0, "rarity": 0.0, "n": 0.0}
        coverages[key].add_match(start, chunk_len, best.combined_score)
        acc = sig_acc[key]
        acc["overlap"] += best.hash_overlap_pct
        acc["temporal"] += best.temporal_coherence
        acc["rarity"] += best.rarity_weighted_score
        acc["n"] += 1

    if not coverages:
        return None

    results_summary = sorted(
        (
            {
                "episode_name": k,
                "episode": k,  # _attach_calibrated_confidence reads this key
                "score": c.ranked_voting_score,
                "vote_count": len(c.matched_chunks),
            }
            for k, c in coverages.items()
        ),
        key=lambda r: r["score"],
        reverse=True,
    )
    winner = results_summary[0]
    win_key = winner["episode_name"]
    if winner["vote_count"] < min_vote_count:
        return None

    acc = sig_acc[win_key]
    n = max(1.0, acc["n"])
    chromaprint_signal = {
        "hash_overlap": acc["overlap"] / n,
        "temporal_coherence": acc["temporal"] / n,
        "rarity_weighted_score": acc["rarity"] / n,
    }

    season = int(win_key[1:3])
    episode = int(win_key[4:6])
    best_match = {
        "season": season,
        "episode": episode,
        "score": winner["score"],
        "match_details": {
            "match_source": "chromaprint",
            "target_votes": len(points),
            "vote_count": winner["vote_count"],
            "chromaprint_signal": chromaprint_signal,
            "candidate_scores": {r["episode_name"]: r["score"] for r in results_summary},
        },
    }
    _attach_calibrated_confidence(
        best_match, results_summary, video_duration, chunk_len, chromaprint_signal
    )
    best_match["confidence"] = best_match.get("confidence", winner["score"])
    best_match["tier"] = tiers[win_key]
    best_match["runner_ups"] = [
        {"episode_name": r["episode_name"], "score": r["score"], "vote_count": r["vote_count"]}
        for r in results_summary[1:]
    ]
    return best_match
