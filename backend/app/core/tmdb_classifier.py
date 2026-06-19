"""TMDB-based content type classification.

Queries TMDB search API to determine if a title name matches
a TV show or movie, providing a strong signal for disc classification.
"""

import logging

import requests

from app.core.analyst import _title_tokens
from app.core.errors import ConfigurationError
from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)

TMDB_SEARCH_TV_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_SEARCH_MOVIE_URL = "https://api.themoviedb.org/3/search/movie"


class TmdbAuthError(ConfigurationError):
    """TMDB rejected the API key (HTTP 401/403).

    A rejected key is a configuration problem, so this extends
    ``ConfigurationError`` (per the ``EngramError`` hierarchy) — ``@handle_errors``
    and ``except EngramError`` guards catch it. Distinct from "no
    results"/transient failures (which degrade to ``None``): an auth failure means
    EVERY lookup will fail until the user fixes the key, so callers surface it to
    the user instead of silently falling back to heuristic-only classification
    (#243).
    """


# Human-readable causes shown verbatim on the job card / detail panel when
# classification proceeded without TMDB (#243 P3). Single source of truth —
# the frontend renders these strings as-is.
TMDB_DEGRADED_NOT_CONFIGURED = (
    "TMDB API key not configured — classification ran in heuristic-only mode. "
    "Configure your Read Access Token in Settings."
)
TMDB_DEGRADED_AUTH_FAILED = (
    "TMDB rejected the configured API key — classification ran in heuristic-only "
    "mode. Update your Read Access Token in Settings."
)

# Popularity threshold for high-confidence matches
HIGH_POPULARITY_THRESHOLD = 50

# Same-name collision detection (item 1). Flag a job for review only when two
# distinct same-name TMDB shows are BOTH plausibly real: the runner-up clears
# this popularity floor AND the top/second popularity ratio is small enough that
# popularity is not a confident pick. Dominant-twin cases (e.g. Frasier 1993 vs
# 2023 revival) intentionally fall through — they have no identify-time signal
# and are handled downstream (item 3). Tunable.
AMBIGUOUS_POPULARITY_FLOOR = 10.0
AMBIGUOUS_POPULARITY_RATIO = 4.0

# No-year backstop (item-3 layering). When the disc label carries NO year it
# cannot self-disambiguate same-name twins, so we proactively flag for review
# even for dominant-twin cases the materiality gate lets through — provided the
# runner-up clears this LOW floor (excludes the pop<3 noise tier). With a year in
# the label, popularity+year already disambiguate and this does not apply. Tunable.
AMBIGUOUS_NO_YEAR_FLOOR = 3.0


def _confidence_from_popularity(popularity: float, ambiguous: bool) -> float:
    """Map a TMDB popularity score to a classification confidence value."""
    if ambiguous:
        return 0.60
    if popularity > HIGH_POPULARITY_THRESHOLD:
        return 0.85
    return 0.70


def _name_similarity(query: str, candidate: str) -> float:
    """Compute similarity between query and candidate name tokens.

    Uses Jaccard similarity with fuzzy prefix matching for near-identical tokens
    (e.g., "Thunderbird" vs "Thunderbirds").
    """
    q_tok, c_tok = _title_tokens(query), _title_tokens(candidate)
    if not q_tok or not c_tok:
        return 0.0

    # Exact intersection
    exact_match = q_tok & c_tok
    q_unmatched = q_tok - exact_match
    c_unmatched = c_tok - exact_match

    # Fuzzy prefix matching for unmatched tokens
    # Handles inflectional variants like "Thunderbird"/"Thunderbirds", "Alien"/"Aliens"
    fuzzy_score = 0.0
    matched_c = set()
    for qt in q_unmatched:
        for ct in c_unmatched - matched_c:
            shorter, longer = sorted([qt, ct], key=len)
            if longer.startswith(shorter) and len(longer) - len(shorter) <= 2:
                fuzzy_score += 0.8
                matched_c.add(ct)
                break

    # Reduce union for fuzzy-matched pairs (they're "almost" the same token)
    union_size = len(q_tok | c_tok) - len(matched_c)
    return (len(exact_match) + fuzzy_score) / union_size


class TmdbSignal:
    """Signal from TMDB about content type."""

    __slots__ = (
        "content_type",
        "confidence",
        "tmdb_id",
        "tmdb_name",
        "ambiguous_identity",
        "candidates",
        "all_candidates",
    )

    def __init__(
        self,
        content_type: ContentType,
        confidence: float,
        tmdb_id: int | None = None,
        tmdb_name: str | None = None,
        ambiguous_identity: bool = False,
        candidates: list[dict] | None = None,
        all_candidates: list[dict] | None = None,
    ):
        self.content_type = content_type
        self.confidence = confidence
        self.tmdb_id = tmdb_id
        self.tmdb_name = tmdb_name
        self.ambiguous_identity = ambiguous_identity
        # `candidates`: same-name twins that tripped the materiality gate (a
        # proactive, identify-time review prompt). `all_candidates`: EVERY
        # same-name twin (>=2), recorded regardless of the gate so a downstream
        # wrong-show detector can suggest the right one even for dominant twins
        # (e.g. Frasier 1993 vs 2023) that the gate intentionally lets through.
        self.candidates = candidates
        self.all_candidates = all_candidates

    def __repr__(self) -> str:
        return (
            f"TmdbSignal(content_type={self.content_type.value}, "
            f"confidence={self.confidence:.0%}, tmdb_id={self.tmdb_id}, "
            f"tmdb_name={self.tmdb_name!r}, ambiguous_identity={self.ambiguous_identity}, "
            f"candidates={self.candidates!r}, all_candidates={self.all_candidates!r})"
        )


def _build_auth(api_key: str) -> tuple[dict, dict]:
    """Build headers and base params for TMDB auth.

    Returns:
        (headers, params) tuple
    """
    headers = {}
    params = {}
    if len(api_key) > 40:  # v4 JWT token
        headers["Authorization"] = f"Bearer {api_key}"
    else:  # v3 API key
        params["api_key"] = api_key
    return headers, params


def _search_tmdb(
    url: str,
    query: str,
    headers: dict,
    base_params: dict,
    timeout: float,
) -> tuple[dict | None, list[dict]]:
    """Search a TMDB endpoint; return (best-matching result, all raw results).

    Prefers results whose name closely matches the query over raw popularity.
    The raw list lets callers detect same-name collisions.
    """
    params = {**base_params, "query": query}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        if response.status_code in (401, 403):
            # Bad/expired key: every subsequent lookup will fail the same way.
            # Raise instead of degrading to "no results" so callers can name the
            # real cause to the user (#243).
            raise TmdbAuthError(f"TMDB rejected the API key (HTTP {response.status_code})")
        if response.status_code == 200:
            results = response.json().get("results", [])
            if not results:
                return None, []
            if len(results) == 1:
                return results[0], results
            best = results[0]
            best_name = best.get("name", best.get("title", ""))
            best_sim = _name_similarity(query, best_name)
            for r in results[1:5]:
                r_name = r.get("name", r.get("title", ""))
                r_sim = _name_similarity(query, r_name)
                if r_sim > best_sim:
                    best, best_sim = r, r_sim
            return best, results
    except (requests.RequestException, ConnectionError, TimeoutError):
        pass
    return None, []


def _collect_same_name_candidates(query: str, results: list[dict]) -> list[dict] | None:
    """Return ALL distinct same-name shows (>= 2), popularity-sorted, else None.

    "Same-name" = normalized name equals the query's (>= 0.95 similarity). This is
    pure collection with NO materiality gate — it records the ambiguity (e.g.
    Frasier 1993 #3452 + 2023 revival #195241) so downstream consumers can suggest
    the right twin even for dominant-twin cases the gate intentionally lets pass.
    A franchise like Doctor Who legitimately has 3+ same-name shows; all appear.
    """
    same = []
    seen_ids = set()
    for r in results:
        rid = r.get("id")
        if rid is None or rid in seen_ids:
            continue
        name = r.get("name", r.get("original_name", ""))
        if _name_similarity(query, name) >= 0.95:
            seen_ids.add(rid)
            same.append(r)
    if len(same) < 2:
        return None
    same.sort(key=lambda r: r.get("popularity", 0.0), reverse=True)
    return [
        {
            "tmdb_id": r["id"],
            "name": r.get("name", r.get("original_name", "")),
            "year": (r.get("first_air_date") or "")[:4],
            "popularity": round(r.get("popularity", 0.0), 1),
        }
        for r in same
    ]


def _detect_same_name_candidates(query: str, results: list[dict]) -> list[dict] | None:
    """Return same-name collision candidates when the materiality gate fires, else None.

    The gate fires only when the top two distinct same-name shows are BOTH plausibly
    real: runner-up popularity >= AMBIGUOUS_POPULARITY_FLOOR AND top/second popularity
    ratio <= AMBIGUOUS_POPULARITY_RATIO. Dominant-twin cases (e.g. Frasier 1993 vs the
    2023 revival) intentionally fall through here — they're caught downstream.
    """
    candidates = _collect_same_name_candidates(query, results)
    if not candidates:
        return None
    top, second = candidates[0]["popularity"], candidates[1]["popularity"]
    if second < AMBIGUOUS_POPULARITY_FLOOR:
        return None
    # `second <= 0` is a division-by-zero rail kept intentionally: the floor check
    # above covers it for the default floor, but the constants are tunable.
    if second <= 0 or (top / second) > AMBIGUOUS_POPULARITY_RATIO:
        return None
    return candidates


def should_flag_no_year(candidates: list[dict] | None, has_year: bool) -> bool:
    """Whether a no-year disc with a real same-name twin should be flagged for review.

    Backstop to the materiality gate: a label with no year can't pick between
    same-name twins (e.g. Frasier 1993 vs the 2023 revival), so flag when a twin
    exists and the runner-up clears the low no-year floor. ``candidates`` is the
    popularity-sorted same-name list (TmdbSignal.all_candidates); index 1 is the
    runner-up. Suppressed when a year is present (popularity+year disambiguate).
    """
    if has_year or not candidates or len(candidates) < 2:
        return False
    return candidates[1].get("popularity", 0.0) >= AMBIGUOUS_NO_YEAR_FLOOR


def _maybe_flag_tv_ambiguity(signal: TmdbSignal, query: str, tv_results: list[dict]) -> TmdbSignal:
    """Record same-name twins on a TV signal; flag for review only when the gate fires."""
    if signal.content_type != ContentType.TV:
        return signal
    # Always record the full twin list (used by the downstream wrong-show detector),
    # independent of whether the materiality gate decides to flag proactively.
    signal.all_candidates = _collect_same_name_candidates(query, tv_results)
    candidates = _detect_same_name_candidates(query, tv_results)
    if candidates:
        signal.ambiguous_identity = True
        signal.candidates = candidates
        logger.info(
            f"TMDB: same-name collision for '{query}' — candidates "
            + ", ".join(f"{c['name']} ({c['year']}, id={c['tmdb_id']})" for c in candidates)
        )
    return signal


def classify_from_tmdb(
    name: str,
    api_key: str,
    timeout: float = 5.0,
    prefer_content_type: ContentType | None = None,
) -> TmdbSignal | None:
    """Query TMDB for both TV and movie matches, return strongest signal.

    Args:
        name: Parsed show/movie name from volume label
        api_key: TMDB API key (v3 or v4 token)
        timeout: Network timeout in seconds per request
        prefer_content_type: When the caller already knows the disc's namespace
            (e.g. a season-bearing, label-TV box set), the matching result in that
            namespace wins outright. A fuzzy cross-namespace hit — a same-named
            movie for a TV box set ("Fargo" the film vs. the series), or a movie
            that the popularity tiebreak would otherwise pick — must not outrank
            the correct namespace. ``None`` keeps the prior name/popularity logic.

    Returns:
        TmdbSignal if a match is found, None if lookup fails or no results
    """
    if not name or not api_key:
        return None

    headers, base_params = _build_auth(api_key)

    # The query that actually produced results: the original name, or the
    # variation that matched after the original returned nothing. Similarity is
    # scored against THIS, not the over-specified original — otherwise a box-set
    # title ("Avatar: The Last Airbender Book One: Water") under-credits the clean
    # series match ("Avatar: The Last Airbender") versus a fuzzy movie, because
    # the dropped subtitle tokens dilute both names equally.
    matched_query = name

    # Search both TV and movie endpoints
    tv_result, tv_results = _search_tmdb(TMDB_SEARCH_TV_URL, name, headers, base_params, timeout)
    movie_result, _ = _search_tmdb(TMDB_SEARCH_MOVIE_URL, name, headers, base_params, timeout)

    # If neither returned results, try name variations
    if not tv_result and not movie_result:
        from app.matcher.tmdb_client import generate_name_variations

        variations = generate_name_variations(name)
        for variation in variations:
            tv_result, tv_results = _search_tmdb(
                TMDB_SEARCH_TV_URL, variation, headers, base_params, timeout
            )
            movie_result, _ = _search_tmdb(
                TMDB_SEARCH_MOVIE_URL, variation, headers, base_params, timeout
            )
            if tv_result or movie_result:
                matched_query = variation
                logger.info(f"TMDB matched via variation '{variation}' (original: '{name}')")
                break

    if not tv_result and not movie_result:
        logger.info(f"TMDB: no results for '{name}'")
        return None

    # Compare results
    tv_pop = tv_result.get("popularity", 0) if tv_result else 0
    movie_pop = movie_result.get("popularity", 0) if movie_result else 0

    if tv_result and movie_result:
        # A caller that knows the namespace settles the TV-vs-movie contest before
        # similarity/popularity ever run: the preferred namespace's match wins as
        # long as it exists (both do here). Cross-namespace noise can't displace it.
        if prefer_content_type == ContentType.TV:
            logger.info(
                f"TMDB: preferring TV namespace for '{matched_query}' "
                f"(label-known TV; movie match suppressed)"
            )
            return _maybe_flag_tv_ambiguity(_make_tv_signal(tv_result), matched_query, tv_results)
        if prefer_content_type == ContentType.MOVIE:
            logger.info(
                f"TMDB: preferring movie namespace for '{matched_query}' "
                f"(label-known movie; TV match suppressed)"
            )
            return _make_movie_signal(movie_result)

        # Check name similarity to the matched query
        tv_name = tv_result.get("name", tv_result.get("original_name", ""))
        movie_name = movie_result.get("title", movie_result.get("original_title", ""))
        tv_sim = _name_similarity(matched_query, tv_name)
        movie_sim = _name_similarity(matched_query, movie_name)

        # If one is a much closer name match, prefer it regardless of popularity
        sim_diff = abs(tv_sim - movie_sim)
        if sim_diff >= 0.2:
            if tv_sim > movie_sim:
                return _maybe_flag_tv_ambiguity(
                    _make_tv_signal(tv_result), matched_query, tv_results
                )
            else:
                return _make_movie_signal(movie_result)

        # Similar name quality — compare popularity
        if tv_pop > 0 and movie_pop > 0:
            ratio = max(tv_pop, movie_pop) / min(tv_pop, movie_pop)
            if ratio < 2:
                # Close popularity — ambiguous, use the higher one but lower confidence
                if tv_pop >= movie_pop:
                    return _maybe_flag_tv_ambiguity(
                        _make_tv_signal(tv_result, ambiguous=True), matched_query, tv_results
                    )
                else:
                    return _make_movie_signal(movie_result, ambiguous=True)

        if tv_pop >= movie_pop:
            return _maybe_flag_tv_ambiguity(_make_tv_signal(tv_result), matched_query, tv_results)
        else:
            return _make_movie_signal(movie_result)

    if tv_result:
        return _maybe_flag_tv_ambiguity(_make_tv_signal(tv_result), matched_query, tv_results)

    return _make_movie_signal(movie_result)


def _make_tv_signal(result: dict, ambiguous: bool = False) -> TmdbSignal:
    """Build a TV TmdbSignal from a TMDB search result."""
    popularity = result.get("popularity", 0)
    confidence = _confidence_from_popularity(popularity, ambiguous)
    name = result.get("name", result.get("original_name", ""))
    logger.info(f"TMDB: TV match '{name}' (id={result['id']}, popularity={popularity:.1f})")
    return TmdbSignal(
        content_type=ContentType.TV,
        confidence=confidence,
        tmdb_id=result["id"],
        tmdb_name=name,
    )


def _make_movie_signal(result: dict, ambiguous: bool = False) -> TmdbSignal:
    """Build a MOVIE TmdbSignal from a TMDB search result."""
    popularity = result.get("popularity", 0)
    confidence = _confidence_from_popularity(popularity, ambiguous)
    name = result.get("title", result.get("original_title", ""))
    logger.info(f"TMDB: Movie match '{name}' (id={result['id']}, popularity={popularity:.1f})")
    return TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=confidence,
        tmdb_id=result["id"],
        tmdb_name=name,
    )
