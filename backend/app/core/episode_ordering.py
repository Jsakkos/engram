"""Canonical->output episode-ordering projection (GitHub #200).

Engram's matcher resolves an episode's CANONICAL identity — TMDB aired order
``(season, episode)`` — and that value is the single internal identity used for
``DiscTitle.matched_episode``, job history, and the acoustic-fingerprint network
key. A user may, however, want their library filed in a *different* ordering
(DVD, digital, ...) the way Plex/Jellyfin allow per show.

This module is the **presentation-only** projection that maps a canonical
``(season, episode)`` to the output numbering of a chosen TMDB *episode group*.
It is strictly one-directional (canonical -> output), never inverted, and never
persisted back onto the canonical identity. It only ever reads cached TMDB data
via ``tmdb_client`` and never touches the database.

The season-derivation rule is the load-bearing subtlety. Real TMDB data
(Firefly's DVD group) names its first season group "Season 1" while assigning it
``order=1`` and giving "Specials" ``order=0`` — so the season number is read from
the group *name* (the human-curated, Plex-facing label), with the structured
``order`` field as a fallback. The within-group ``episode.order`` (0-based)
directly yields the output episode number.
"""

from __future__ import annotations

import re

from loguru import logger

from app.matcher import tmdb_client

ORDERING_AIRED = "aired"
ORDERING_ABSOLUTE = "absolute"
ORDERING_DVD = "dvd"
ORDERING_DIGITAL = "digital"
ORDERING_STORY_ARC = "story_arc"
ORDERING_PRODUCTION = "production"
ORDERING_TV = "tv"

# TMDB episode-group ``type`` enum -> our ordering string.
_TMDB_TYPE_TO_ORDERING = {
    1: ORDERING_AIRED,
    2: ORDERING_ABSOLUTE,
    3: ORDERING_DVD,
    4: ORDERING_DIGITAL,
    5: ORDERING_STORY_ARC,
    6: ORDERING_PRODUCTION,
    7: ORDERING_TV,
}
_ORDERING_TO_TMDB_TYPE = {v: k for k, v in _TMDB_TYPE_TO_ORDERING.items()}

# Orderings selectable in v1: aired + DVD only. Aired is the canonical default
# (identity, never fetches a group); DVD is the one alternative users actually
# ask for (Firefly et al.) and the only one the Config UI exposes. The remaining
# TMDB group types are recognized (see the type map above) but DEFERRED so the
# review-queue selector never offers more than these two — keeping the per-show
# selector in lock-step with the global Config dropdown:
#   - absolute (type 2): dissolves season boundaries; anime corpora mislabeled
#   - digital (4) / story_arc (5) / production (6) / tv (7): no demonstrated need
# Re-enable one by adding its constant back to this frozenset (and the Config UI).
ALLOWED_ORDERINGS = frozenset(
    {
        ORDERING_AIRED,
        ORDERING_DVD,
    }
)

_SEASON_NAME_RE = re.compile(
    r"(?:season|series|volume|vol\.?|part|book|chapter)\s*0*(\d+)", re.IGNORECASE
)
_LEADING_INT_RE = re.compile(r"\s*0*(\d+)")


def _derive_output_season(group: dict) -> int:
    """Return the output season number for a TMDB episode-group "group".

    Primary signal is the human-curated ``name`` ("Season 1" -> 1, "Specials"
    -> 0, the Plex/Jellyfin convention); the structured ``order`` field is the
    fallback when the name carries no number. Leading with ``order`` would be
    wrong: real data assigns "Season 1" ``order=1`` while "Specials" is
    ``order=0``.
    """
    name = (group.get("name") or "").strip()
    low = name.lower()
    if "special" in low or low in ("extras", "extra", "bonus"):
        return 0
    m = _SEASON_NAME_RE.search(low)
    if m:
        return int(m.group(1))
    m = _LEADING_INT_RE.match(name)
    if m:
        return int(m.group(1))
    order = group.get("order")
    return order if isinstance(order, int) else 1


def resolve_episode_group_id(show_id: str | None, ordering: str, api_key: str) -> str | None:
    """Return the TMDB episode-group id realising ``ordering`` for a show.

    ``aired`` (and any ordering with no canonical TMDB type) resolves to None —
    aired is TMDB's default and needs no group. When several groups share the
    target type, pick deterministically: most episodes, then most groups, then
    the lexicographically smallest id, so re-rips project identically.
    """
    if ordering == ORDERING_AIRED or not show_id or not api_key:
        return None
    target_type = _ORDERING_TO_TMDB_TYPE.get(ordering)
    if target_type is None:
        return None
    candidates = [
        g
        for g in tmdb_client.fetch_episode_groups(show_id, api_key)
        if g.get("type") == target_type
    ]
    if not candidates:
        return None
    # Deterministic: most episodes, then most groups (both descending via
    # negation), then the lexicographically smallest id — so the same show
    # always projects through the same group across re-rips.
    candidates.sort(
        key=lambda g: (
            -(g.get("episode_count") or 0),
            -(g.get("group_count") or 0),
            str(g.get("id") or ""),
        )
    )
    chosen = candidates[0]
    return str(chosen["id"]) if chosen.get("id") else None


def build_projection(group_detail: dict) -> dict[tuple[int, int], tuple[int, int]]:
    """Map canonical ``(season, episode)`` -> output ``(season', episode')``.

    Keyed on each episode's canonical ``season_number``/``episode_number`` (the
    aired-order identity); the value is the episode's position in its group:
    ``season'`` from the group name/order, ``episode'`` = ``episode.order + 1``.
    Episodes missing canonical numbers or order are skipped (left to fall back).
    """
    projection: dict[tuple[int, int], tuple[int, int]] = {}
    for group in group_detail.get("groups", []) or []:
        out_season = _derive_output_season(group)
        for ep in group.get("episodes", []) or []:
            c_season = ep.get("season_number")
            c_episode = ep.get("episode_number")
            order = ep.get("order")
            if c_season is None or c_episode is None or not isinstance(order, int):
                continue
            projection[(int(c_season), int(c_episode))] = (out_season, order + 1)
    return projection


def get_projection_map(
    show_id: str | None, ordering: str, api_key: str
) -> dict[tuple[int, int], tuple[int, int]] | None:
    """Resolve + build the projection for a show/ordering, or None if N/A.

    None means "no projection applies" (aired, no matching group, fetch failed),
    and callers should treat the canonical numbers as the output unchanged.
    Never raises — any failure degrades to None.
    """
    if ordering == ORDERING_AIRED or ordering not in ALLOWED_ORDERINGS:
        return None
    try:
        group_id = resolve_episode_group_id(show_id, ordering, api_key)
        if not group_id:
            return None
        detail = tmdb_client.fetch_episode_group(group_id, api_key)
        if not detail:
            return None
        return build_projection(detail)
    except Exception as e:  # defensive: projection must never break organizing
        logger.warning(f"Episode-ordering projection failed for show {show_id}/{ordering}: {e}")
        return None


def project_episode(
    show_id: str | None, ordering: str, season: int, episode: int, api_key: str
) -> tuple[int, int]:
    """Project a single canonical ``(season, episode)`` to the output ordering.

    Returns the input unchanged for the identity case (aired) and for every
    failure mode (no group, fetch failed, canonical pair absent). Never raises.
    """
    projection = get_projection_map(show_id, ordering, api_key)
    if not projection:
        return (season, episode)
    return projection.get((season, episode), (season, episode))


def build_ordering_options(
    show_id: str | None,
    season: int,
    roster_pairs: list[tuple[int, int]],
    matched_pairs: list[tuple[int, int]],
    api_key: str,
    current_ordering: str = ORDERING_AIRED,
) -> dict:
    """Describe the orderings available for a show, for the review-queue selector.

    Always offers "aired" first. Adds one option per v1-allowed episode-group
    type present on the show, each with: whether it diverges for the disc's
    ``matched_pairs`` and a ``projection`` mapping ``"SxxExx" -> "SxxExx"`` for
    the season's ``roster_pairs`` whose number actually changes (so the UI can
    show each candidate's number under that ordering).
    """
    options: list[dict] = [
        {
            "ordering": ORDERING_AIRED,
            "label": "Aired Order",
            "tmdb_type": 1,
            "diverges": False,
            "projection": {},
        }
    ]
    groups = tmdb_client.fetch_episode_groups(show_id, api_key) if (show_id and api_key) else []
    seen = {ORDERING_AIRED}
    for g in groups:
        ordering = _TMDB_TYPE_TO_ORDERING.get(g.get("type"))
        if ordering is None or ordering not in ALLOWED_ORDERINGS or ordering in seen:
            continue
        projection_map = get_projection_map(show_id, ordering, api_key)
        if not projection_map:
            continue
        seen.add(ordering)
        diverges = any(projection_map.get(p, p) != p for p in matched_pairs)
        projection: dict[str, str] = {}
        for s, e in roster_pairs:
            mapped = projection_map.get((s, e))
            if mapped and mapped != (s, e):
                projection[f"S{s:02d}E{e:02d}"] = f"S{mapped[0]:02d}E{mapped[1]:02d}"
        options.append(
            {
                "ordering": ordering,
                "label": g.get("name") or ordering.upper(),
                "tmdb_type": g.get("type"),
                "diverges": diverges,
                "projection": projection,
            }
        )
    return {
        "available": len(options) > 1,
        "diverges": any(o["diverges"] for o in options),
        "current": current_ordering,
        "options": options,
    }


def compute_divergence(
    show_id: str | None,
    ordering: str,
    canonical_pairs: list[tuple[int, int]],
    api_key: str,
) -> bool:
    """True iff applying ``ordering`` would remap at least one of the pairs.

    Used to decide whether to surface the ordering selector in review — we stay
    silent unless the chosen ordering actually changes a number on this disc.
    """
    projection = get_projection_map(show_id, ordering, api_key)
    if not projection:
        return False
    return any(projection.get(p, p) != p for p in canonical_pairs)
