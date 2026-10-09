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


class _NonRetryable(Exception):
    """A failure retrying cannot fix (bad key, unknown series, malformed payload)."""


def _check(resp) -> None:
    """Raise _NonRetryable for a client error; let retryable statuses raise HTTPError."""
    code = resp.status_code
    if 400 <= code < 500 and code not in (408, 429):
        raise _NonRetryable(f"HTTP {code}")
    resp.raise_for_status()


def _login(api_key: str) -> str:
    resp = requests.post(f"{_BASE}/login", json={"apikey": api_key}, timeout=30)
    _check(resp)
    try:
        token = resp.json()["data"]["token"]
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        raise _NonRetryable(f"login response malformed: {e!r}") from e
    if not token:
        raise _NonRetryable("login response had no token")
    return token


def _token(api_key: str, *, force: bool = False) -> str:
    with _token_lock:
        if force or not _token_state.token or _token_state.key != api_key:
            _token_state.token = _login(api_key)
            _token_state.key = api_key
        return _token_state.token


def _get(path: str, params: dict, api_key: str) -> dict:
    """Authenticated GET with one re-login on 401 (token expiry). Raises on failure."""
    resp = None
    for force in (False, True):
        token = _token(api_key, force=force)
        resp = requests.get(
            f"{_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code != 401:
            break
    _check(resp)
    return resp.json()


def _parse_episodes(payload: dict, season: int) -> list[dict]:
    out = []
    for ep in payload["data"]["episodes"] or []:
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
    # int() rather than str.isdigit(): isdigit() accepts "²", which int() rejects.
    try:
        series = int(str(tvdb_id))
    except ValueError:
        series = -1
    if series < 0:
        logger.warning(f"Invalid TheTVDB series id: {tvdb_id!r}")
        return None
    cache_key = f"tvdb_roster:{series}:{season}"
    # The cache is an optimisation: a locked or corrupt cache DB must not turn
    # into "no TVDB data" (read) or discard a roster we already fetched (write).
    try:
        cached = tmdb_persistent_cache.get(cache_key)
    except Exception as e:  # noqa: BLE001 - see comment above
        logger.warning(f"TheTVDB roster cache read failed: {e}")
        cached = None
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
                try:
                    roster.extend(_parse_episodes(payload, season))
                    has_next = bool((payload.get("links") or {}).get("next"))
                except (AttributeError, TypeError, KeyError, ValueError) as e:
                    raise _NonRetryable(f"malformed episodes payload: {e!r}") from e
                if not has_next:
                    break
            roster.sort(key=lambda e: e["episode_number"])
            if roster:
                try:
                    tmdb_persistent_cache.put(
                        cache_key, roster, tmdb_persistent_cache.TTL_TVDB_ROSTER
                    )
                except Exception as e:  # noqa: BLE001 - see the cache-read comment
                    logger.warning(f"TheTVDB roster cache write failed: {e}")
            return roster or None
        except _NonRetryable as e:
            logger.warning(f"TheTVDB roster fetch failed for {series} S{season}: {e}")
            return None
        except (requests.RequestException, ConnectionError, TimeoutError, ValueError) as e:
            if attempt == _RETRIES:
                logger.warning(f"TheTVDB roster fetch failed for {series} S{season}: {e}")
                return None
            time.sleep(1.0 * (attempt + 1))
    return None
