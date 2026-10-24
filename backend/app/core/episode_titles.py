"""Episode titles for the ``{episode_title}`` filename placeholder (#826).

The title is looked up in the SAME numbering namespace as the episode code
(``season_episodes`` reads the TMDB or the TheTVDB roster depending on the
namespace bound around the call), so a TheTVDB-numbered job never reads a
TMDB title for a TVDB number. Callers pass the job's stored namespace; ``None``
keeps whatever is already bound.

A combined track (``S01E01-E02``) joins its episodes' titles with `` & ``, which
is legal in file names on Windows, Linux and macOS. Exact duplicates collapse, so
the two parts of a two-parter that share a name read once.

``season_episodes`` costs an HTTP request per call and TMDB can hang for its full
timeout, so rosters are cached briefly. A miss is cached too, for less time: a
TMDB outage then costs one timeout per season per minute rather than one per file.
"""

from __future__ import annotations

import threading
import time

from app.core.episode_codes import parse_episode_code
from app.core.episode_namespace import current_namespace, namespace_context, season_episodes

TITLE_SEPARATOR = " & "

_ROSTER_TTL_SECONDS = 600.0
_MISS_TTL_SECONDS = 60.0

_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def clear_roster_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _roster(tmdb_id: str, season: int, api_key: str) -> list[dict]:
    """The season roster in the bound namespace, cached (callers bind the namespace)."""
    key = (current_namespace(), tmdb_id, season)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and hit[0] > _now():
        return hit[1]
    roster = season_episodes(tmdb_id, season, api_key) or []
    ttl = _ROSTER_TTL_SECONDS if roster else _MISS_TTL_SECONDS
    with _cache_lock:
        _cache[key] = (_now() + ttl, roster)
    return roster


def _same_title(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def episode_title_for_code(
    code: str | None,
    tmdb_id: str | int | None,
    namespace: str | None,
    api_key: str,
) -> str:
    """The title(s) of the episode(s) ``code`` names, or ``""`` when unknown.

    Never raises for a missing show id, a non-episode code ("extra", "skip") or an
    unavailable roster: the placeholder is optional, so an unknown title degrades
    to a name without one instead of failing the organize. Episodes the roster
    lacks (or names empty) are skipped; the rest still read.
    """
    parsed = parse_episode_code(code)
    if parsed is None or not tmdb_id:
        return ""
    season, episodes = parsed
    with namespace_context(namespace or current_namespace()):
        roster = _roster(str(tmdb_id), season, api_key)
    by_number = {
        int(ep["episode_number"]): (ep.get("name") or "").strip()
        for ep in roster
        if ep.get("episode_number") is not None
    }
    titles: list[str] = []
    for number in episodes:
        title = by_number.get(number, "")
        if title and not any(_same_title(title, seen) for seen in titles):
            titles.append(title)
    return TITLE_SEPARATOR.join(titles)
