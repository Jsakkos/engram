"""TheTVDB v4 client: the secondary episode-numbering source (spec 2026-10-08).

Only what the episode namespace needs: log in with the project key and fetch a
season's episodes in TheTVDB's *official* order. Everything degrades to None
("no TVDB data"); callers then behave exactly as they did before TVDB existed.

Licensing: Engram uses TheTVDB's free licensed tier, which requires a visible
attribution link wherever TVDB metadata is shown (see TvdbAttribution.tsx).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import requests
from loguru import logger

from app.matcher import tmdb_persistent_cache

_BASE = "https://api4.thetvdb.com/v4"
_MAX_PAGES = 20  # a season never needs more; bounds a misbehaving `links.next`
_RETRIES = 2


@dataclass
class _TokenState:
    token: str | None = None
    key: str | None = None


_token_state = _TokenState()
_token_lock = threading.Lock()


def resolve_api_key(config) -> str:
    """The key to use: the config override, else the built-in TVDB_API_KEY env var."""
    override = (getattr(config, "tvdb_api_key", "") or "").strip()
    if override:
        return override
    return (os.environ.get("TVDB_API_KEY") or "").strip()


def _login(api_key: str) -> str | None:
    resp = requests.post(f"{_BASE}/login", json={"apikey": api_key}, timeout=30)
    resp.raise_for_status()
    token = (resp.json().get("data") or {}).get("token")
    return token or None


def _token(api_key: str, *, force: bool = False) -> str | None:
    with _token_lock:
        if force or not _token_state.token or _token_state.key != api_key:
            _token_state.token = _login(api_key)
            _token_state.key = api_key
        return _token_state.token


def _get(path: str, params: dict, api_key: str) -> dict:
    """Authenticated GET with one re-login on 401. Raises on failure."""
    token = _token(api_key)
    resp = requests.get(
        f"{_BASE}{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )
    if resp.status_code == 401:
        token = _token(api_key, force=True)
        resp = requests.get(
            f"{_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()


def _parse_episodes(payload: dict, season: int) -> list[dict]:
    episodes = ((payload.get("data") or {}).get("episodes")) or []
    out = []
    for ep in episodes:
        if ep.get("seasonNumber") != season or ep.get("number") is None:
            continue
        out.append(
            {
                "episode_number": int(ep["number"]),
                "name": ep.get("name") or "",
                "runtime": ep.get("runtime") or 0,
                "overview": ep.get("overview") or "",
                "air_date": ep.get("aired") or "",
            }
        )
    return out


def fetch_season_roster(tvdb_id, season: int, *, api_key: str) -> list[dict] | None:
    """Episodes of ``season`` in TheTVDB official order, or None on any failure.

    Same dict shape as ``tmdb_client.fetch_season_episodes`` (plus ``air_date``)
    so callers can swap sources without branching on shape.
    """
    if not api_key:
        return None
    if not str(tvdb_id).isdigit():
        logger.warning(f"Invalid TheTVDB series id: {tvdb_id!r}")
        return None
    series = int(tvdb_id)
    cache_key = f"tvdb_roster:{series}:{season}"
    cached = tmdb_persistent_cache.get(cache_key)
    if cached is not None:
        return cached

    for attempt in range(_RETRIES + 1):
        try:
            roster: list[dict] = []
            for page in range(_MAX_PAGES):
                payload = _get(
                    f"/series/{series}/episodes/official",
                    {"season": season, "page": page},
                    api_key,
                )
                roster.extend(_parse_episodes(payload, season))
                if not ((payload.get("links") or {}).get("next")):
                    break
            roster.sort(key=lambda e: e["episode_number"])
            if roster:
                tmdb_persistent_cache.put(cache_key, roster, tmdb_persistent_cache.TTL_TVDB_ROSTER)
            return roster or None
        except (requests.RequestException, ConnectionError, TimeoutError, ValueError) as e:
            if attempt == _RETRIES:
                logger.warning(f"TheTVDB roster fetch failed for {series} S{season}: {e}")
                return None
            time.sleep(1.0 * (attempt + 1))
    return None
