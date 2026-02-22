"""TMDB-based content type classification.

Queries TMDB search API to determine if a title name matches
a TV show or movie, providing a strong signal for disc classification.
"""

import logging

import requests

from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)

TMDB_SEARCH_TV_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_SEARCH_MOVIE_URL = "https://api.themoviedb.org/3/search/movie"

# Popularity threshold for high-confidence matches
HIGH_POPULARITY_THRESHOLD = 50


class TmdbSignal:
    """Signal from TMDB about content type."""

    __slots__ = ("content_type", "confidence", "tmdb_id", "tmdb_name")

    def __init__(
        self,
        content_type: ContentType,
        confidence: float,
        tmdb_id: int | None = None,
        tmdb_name: str | None = None,
    ):
        self.content_type = content_type
        self.confidence = confidence
        self.tmdb_id = tmdb_id
        self.tmdb_name = tmdb_name

    def __repr__(self) -> str:
        return (
            f"TmdbSignal(content_type={self.content_type.value}, "
            f"confidence={self.confidence:.0%}, tmdb_id={self.tmdb_id}, "
            f"tmdb_name={self.tmdb_name!r})"
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
) -> dict | None:
    """Search a TMDB endpoint and return the top result.

    Returns:
        Top result dict with 'id', 'name'/'title', 'popularity', or None
    """
    params = {**base_params, "query": query}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return results[0]
    except (requests.RequestException, ConnectionError, TimeoutError):
        pass
    return None


def classify_from_tmdb(
    name: str,
    api_key: str,
    timeout: float = 5.0,
) -> TmdbSignal | None:
    """Query TMDB for both TV and movie matches, return strongest signal.

    Args:
        name: Parsed show/movie name from volume label
        api_key: TMDB API key (v3 or v4 token)
        timeout: Network timeout in seconds per request

    Returns:
        TmdbSignal if a match is found, None if lookup fails or no results
    """
    if not name or not api_key:
        return None

    headers, base_params = _build_auth(api_key)

    # Search both TV and movie endpoints
    tv_result = _search_tmdb(TMDB_SEARCH_TV_URL, name, headers, base_params, timeout)
    movie_result = _search_tmdb(TMDB_SEARCH_MOVIE_URL, name, headers, base_params, timeout)

    # If neither returned results, try name variations
    if not tv_result and not movie_result:
        from app.matcher.tmdb_client import generate_name_variations

        variations = generate_name_variations(name)
        for variation in variations:
            tv_result = _search_tmdb(TMDB_SEARCH_TV_URL, variation, headers, base_params, timeout)
            movie_result = _search_tmdb(
                TMDB_SEARCH_MOVIE_URL, variation, headers, base_params, timeout
            )
            if tv_result or movie_result:
                logger.info(f"TMDB matched via variation '{variation}' (original: '{name}')")
                break

    if not tv_result and not movie_result:
        logger.info(f"TMDB: no results for '{name}'")
        return None

    # Compare results
    tv_pop = tv_result.get("popularity", 0) if tv_result else 0
    movie_pop = movie_result.get("popularity", 0) if movie_result else 0

    if tv_result and movie_result:
        # Both matched — compare popularity
        if tv_pop > 0 and movie_pop > 0:
            ratio = max(tv_pop, movie_pop) / min(tv_pop, movie_pop)
            if ratio < 2:
                # Close popularity — ambiguous, use the higher one but lower confidence
                if tv_pop >= movie_pop:
                    return _make_tv_signal(tv_result, ambiguous=True)
                else:
                    return _make_movie_signal(movie_result, ambiguous=True)

        if tv_pop >= movie_pop:
            return _make_tv_signal(tv_result)
        else:
            return _make_movie_signal(movie_result)

    if tv_result:
        return _make_tv_signal(tv_result)

    return _make_movie_signal(movie_result)


def _make_tv_signal(result: dict, ambiguous: bool = False) -> TmdbSignal:
    """Build a TV TmdbSignal from a TMDB search result."""
    popularity = result.get("popularity", 0)
    if ambiguous:
        confidence = 0.60
    elif popularity > HIGH_POPULARITY_THRESHOLD:
        confidence = 0.85
    else:
        confidence = 0.70
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
    if ambiguous:
        confidence = 0.60
    elif popularity > HIGH_POPULARITY_THRESHOLD:
        confidence = 0.85
    else:
        confidence = 0.70
    name = result.get("title", result.get("original_title", ""))
    logger.info(f"TMDB: Movie match '{name}' (id={result['id']}, popularity={popularity:.1f})")
    return TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=confidence,
        tmdb_id=result["id"],
        tmdb_name=name,
    )
