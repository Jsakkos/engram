"""Per-window chromaprint scoring — the Python twin of the server's src/db_identify.ts.

Definitions are authoritative and MUST match the TypeScript implementation
(golden parity vectors enforce this). See the Phase 3 plan "Shared scoring
definitions" section.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

W_RARITY = 0.5
W_OVERLAP = 0.3
W_TEMPORAL = 0.2


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def hash_overlap_pct(query: list[int], ref_set: Iterable[int] | set[int]) -> float:
    """Fraction of query hashes present in ref_set (exact-equality membership)."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    matches = sum(1 for h in query if h in refs)
    return matches / len(query)


def temporal_coherence(
    query: list[int], ref_set: Iterable[int] | set[int], min_run: int = 3
) -> float:
    """Fraction of ordered query hashes inside contiguous member-runs of length >= min_run."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    run_len = 0
    qualifying = 0
    for h in query:
        if h in refs:
            run_len += 1
        else:
            if run_len >= min_run:
                qualifying += run_len
            run_len = 0
    if run_len >= min_run:
        qualifying += run_len
    return qualifying / len(query)


def rarity_weighted_overlap(
    query: list[int],
    ref_set: Iterable[int] | set[int],
    df_map: Mapping[int, int] | None,
    n_episodes: int,
) -> float:
    """IDF-weighted overlap fraction; falls back to plain overlap when df is unavailable."""
    if not query:
        return 0.0
    refs = ref_set if isinstance(ref_set, (set, frozenset)) else set(ref_set)
    if not df_map or n_episodes <= 0:
        return hash_overlap_pct(query, refs)

    def idf(h: int) -> float:
        return math.log((n_episodes + 1) / (df_map.get(h, 1) + 1)) + 1.0

    num = 0.0
    den = 0.0
    for h in query:
        w = idf(h)
        den += w
        if h in refs:
            num += w
    return num / den if den > 0 else 0.0


def combined_window_score(overlap: float, temporal: float, rarity: float) -> float:
    return _clamp01(W_RARITY * rarity + W_OVERLAP * overlap + W_TEMPORAL * temporal)
