"""Which episode numbering a job uses: TMDB (canonical) or TheTVDB (spec 2026-10-08).

The namespace rides in a ContextVar so the matcher's 13 ``corpus_dir_name``
call sites and its precomputed-pack reads pick it up without a parameter being
threaded through every signature. ``asyncio.to_thread`` copies the context
into the worker thread, which is the same mechanism that carries loguru's
``job=<id>`` tag (``app/core/log_context.py``). Code that runs in a thread it
did not spawn through ``to_thread`` (long-lived scheduler workers) sees the
default, "tmdb"; none of those workers computes cache paths.

Titles are compared through ``normalize_title``, which KEEPS a trailing part
number (canonicalized to digits, so "(1)", "Part I" and ", Part One" all read
as ``1``). Stripping it would collapse "In Blackest Night (1)" and "(2)" into
one ambiguous title and lose both from the crosswalk.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass

from loguru import logger

from app.core.episode_codes import format_episode_code, parse_episode_code

NAMESPACE_TMDB = "tmdb"
NAMESPACE_TVDB = "tvdb"
NAMESPACES = frozenset({NAMESPACE_TMDB, NAMESPACE_TVDB})

_namespace_var: ContextVar[str] = ContextVar("episode_namespace", default=NAMESPACE_TMDB)


def current_namespace() -> str:
    return _namespace_var.get()


@contextlib.contextmanager
def namespace_context(namespace: str | None):
    """Bind ``namespace`` for the enclosed code (None means "tmdb")."""
    value = namespace or NAMESPACE_TMDB
    if value not in NAMESPACES:
        raise ValueError(f"unknown episode namespace: {value!r}")
    token = _namespace_var.set(value)
    try:
        yield
    finally:
        _namespace_var.reset(token)


def corpus_dir_suffix() -> str:
    """Suffix for the reference-cache folder, so TVDB-numbered references never
    share a folder with the TMDB-numbered ones (or the published pack)."""
    return "@tvdb" if current_namespace() == NAMESPACE_TVDB else ""


_PART_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_PART_ROMAN = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}
# A trailing part marker: either a bare "(N)", or a separator (whitespace,
# colon, comma, hyphen) followed by "Part"/"Pt." and a digit, roman numeral or
# number word, optionally parenthesized. A roman numeral WITHOUT "Part" is not
# a marker ("Rocky IV" is a title), and "Part" must be its own word ("Parting
# Shot" is a title).
_PAREN_NUM_RE = re.compile(r"\s*\(\s*(\d+)\s*\)\s*$")
_PART_WORD_RE = re.compile(
    r"[\s:,\-]+\(?\s*(?:part\s+|pt\.?\s*)([a-z0-9]+)\s*\)?\s*$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _part_number(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _PART_WORDS.get(token) or _PART_ROMAN.get(token)


def normalize_title(name: str) -> str:
    """Comparison key for an episode title: a-z0-9 base plus any part number.

    "Secret Origins (1)", "Secret Origins: Part I" and "Secret Origins, Part
    One" all become ``secretorigins1``; "Secret Origins" stays
    ``secretorigins``. Case and punctuation never matter.
    """
    text = (name or "").strip().lower()
    part: int | None = None
    match = _PAREN_NUM_RE.search(text)
    if match:
        part = int(match.group(1))
    else:
        match = _PART_WORD_RE.search(text)
        if match:
            part = _part_number(match.group(1))
            if part is None:
                match = None
    if match:
        text = text[: match.start()]
    base = _NON_ALNUM_RE.sub("", text)
    return f"{base}{part}" if part is not None else base


@dataclass(frozen=True)
class Divergence:
    season: int
    tmdb_count: int
    tvdb_count: int

    def to_json(self) -> str:
        return json.dumps({"season": self.season, "tmdb": self.tmdb_count, "tvdb": self.tvdb_count})


def detect_divergence(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> Divergence | None:
    """TheTVDB numbers the season differently from TMDB, or None.

    Divergent when the episode counts differ, or when both lists hold the same
    titles (by ``normalize_title``) in a different order. A title spelled
    differently by the two catalogues is deliberately NOT divergence: that
    would nag every show.
    """
    if not tmdb_eps or not tvdb_eps:
        return None
    div = Divergence(season, len(tmdb_eps), len(tvdb_eps))
    if len(tmdb_eps) != len(tvdb_eps):
        return div
    a = [normalize_title(e.get("name", "")) for e in tmdb_eps]
    b = [normalize_title(e.get("name", "")) for e in tvdb_eps]
    if Counter(a) == Counter(b) and a != b:
        return div
    return None


def build_crosswalk(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> dict[str, str]:
    """TVDB code -> TMDB code for episodes that pair 1:1, else absent.

    Pairs by ``normalize_title`` first (part numbers included, so "(1)" and
    "(2)" are distinct titles). A title held by more than one episode on either
    side is ambiguous and excluded from both passes. Episodes still unpaired
    then pair by air date when exactly one of each side shares it. The split
    Justice League pilot (TVDB "Secret Origins (1)/(2)/(3)" against TMDB's one
    "Secret Origins", all on the same date) therefore stays unmapped.
    """

    def code(ep: dict) -> str:
        return f"S{season:02d}E{int(ep['episode_number']):02d}"

    tmdb_by_title: dict[str, list[dict]] = {}
    for ep in tmdb_eps:
        tmdb_by_title.setdefault(normalize_title(ep.get("name", "")), []).append(ep)
    tvdb_by_title: dict[str, list[dict]] = {}
    for ep in tvdb_eps:
        tvdb_by_title.setdefault(normalize_title(ep.get("name", "")), []).append(ep)

    crosswalk: dict[str, str] = {}
    used_tmdb: set[str] = set()
    for title, tv in tvdb_by_title.items():
        tm = tmdb_by_title.get(title, [])
        if title and len(tv) == 1 and len(tm) == 1:
            crosswalk[code(tv[0])] = code(tm[0])
            used_tmdb.add(code(tm[0]))

    ambiguous_titles = {
        t
        for t in set(tmdb_by_title) | set(tvdb_by_title)
        if len(tmdb_by_title.get(t, [])) > 1 or len(tvdb_by_title.get(t, [])) > 1
    }

    def by_date(eps: list[dict], taken: set[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for ep in eps:
            if code(ep) in taken or normalize_title(ep.get("name", "")) in ambiguous_titles:
                continue
            if ep.get("air_date"):
                out.setdefault(ep["air_date"], []).append(ep)
        return out

    tm_dates = by_date(tmdb_eps, used_tmdb)
    tv_dates = by_date(tvdb_eps, set(crosswalk))
    for date, tv in tv_dates.items():
        tm = tm_dates.get(date, [])
        if len(tv) == 1 and len(tm) == 1:
            crosswalk[code(tv[0])] = code(tm[0])
    return crosswalk


def _translate(code: str | None, mapping: dict[str, str]) -> str | None:
    parsed = parse_episode_code(code)
    if parsed is None:
        return None
    season, episodes = parsed
    out: list[int] = []
    out_season: int | None = None
    for ep in episodes:
        mapped = mapping.get(f"S{season:02d}E{ep:02d}")
        if mapped is None:
            return None
        m = parse_episode_code(mapped)
        if m is None or (out_season is not None and m[0] != out_season):
            return None
        out_season = m[0]
        out.extend(m[1])
    if out_season is None:
        return None
    return format_episode_code(out_season, out)


def _load_crosswalk(crosswalk_json: str | None) -> dict[str, str] | None:
    """Parse a persisted crosswalk; None when absent or corrupt.

    Callers (contribution, export, matching) treat None as "not translatable",
    so a damaged column omits a title instead of crashing the job.
    """
    if not crosswalk_json:
        return None
    try:
        mapping = json.loads(crosswalk_json)
    except (TypeError, ValueError):
        return None
    return mapping if isinstance(mapping, dict) else None


def to_tmdb_code(namespace: str | None, crosswalk_json: str | None, code: str | None) -> str | None:
    """A stored code as the TMDB code it denotes, or None when not 1:1."""
    if (namespace or NAMESPACE_TMDB) == NAMESPACE_TMDB:
        return code
    mapping = _load_crosswalk(crosswalk_json)
    return _translate(code, mapping) if mapping is not None else None


def from_tmdb_code(
    namespace: str | None, crosswalk_json: str | None, code: str | None
) -> str | None:
    """A TMDB code (e.g. a DiscDB hint) in the job's namespace, or None when not 1:1."""
    if (namespace or NAMESPACE_TMDB) == NAMESPACE_TMDB:
        return code
    mapping = _load_crosswalk(crosswalk_json)
    if mapping is None:
        return None
    return _translate(code, {tm: tv for tv, tm in mapping.items()})


def season_episodes(tmdb_show_id: str, season: int, tmdb_api_key: str) -> list[dict]:
    """The season roster in the CURRENT namespace (sync; call off the event loop).

    Under TMDB: the TMDB roster. Under TVDB: the TheTVDB roster, or ``[]`` (with
    a warning) when it cannot be fetched. There is deliberately NO fallback to
    TMDB there: a TVDB job only exists when its roster was fetched at decision
    time (and the roster is cached for 7 days), so an empty result means a
    mid-job outage. Falling back would silently mix TMDB numbers into a TVDB job
    (LLM candidates, runtimes, download count); failing visibly is better.
    Imports stay inside the function so tests can patch the client modules by
    dotted path.
    """
    from app.matcher import tmdb_client

    if current_namespace() == NAMESPACE_TVDB:
        from app.matcher import tvdb_client
        from app.services.config_service import get_config_sync

        try:
            key = tvdb_client.resolve_api_key(get_config_sync())
            if not key:
                logger.warning("TheTVDB roster unavailable for a TVDB job: no TheTVDB API key")
                return []
            tvdb_id = tmdb_client.fetch_tvdb_id(str(tmdb_show_id), tmdb_api_key)
            if not tvdb_id:
                logger.warning(
                    f"TheTVDB roster unavailable for a TVDB job: no TheTVDB id for "
                    f"TMDB show {tmdb_show_id}"
                )
                return []
            roster = tvdb_client.fetch_season_roster(tvdb_id, season, api_key=key)
            if not roster:
                logger.warning(
                    f"TheTVDB returned no roster for show {tvdb_id} season {season}; "
                    "not falling back to TMDB numbering"
                )
                return []
            return roster
        except Exception as e:  # noqa: BLE001 - any TVDB-side miss yields an empty roster
            logger.warning(f"TheTVDB roster unavailable for a TVDB job: {e}")
            return []
    return tmdb_client.fetch_season_episodes(str(tmdb_show_id), season, tmdb_api_key)


def season_runtimes(tmdb_show_id: str, season: int, tmdb_api_key: str) -> list[int]:
    return [int(e.get("runtime") or 0) for e in season_episodes(tmdb_show_id, season, tmdb_api_key)]


def season_episode_count(tmdb_show_id: str, season: int, tmdb_api_key: str) -> int:
    return len(season_episodes(tmdb_show_id, season, tmdb_api_key))
