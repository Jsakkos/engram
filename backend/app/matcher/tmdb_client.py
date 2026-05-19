# tmdb_client.py
import re
from collections.abc import Callable
from typing import Any, TypeVar

import requests
from loguru import logger

from app.matcher.retry import retry_with_backoff

F = TypeVar("F", bound=Callable[..., Any])

BASE_IMAGE_URL = "https://image.tmdb.org/t/p/original"

# TMDB v4 read-access tokens are long JWTs; v3 keys are short hex strings.
_V4_TOKEN_MIN_LEN = 40


def retry_network_operation(max_retries: int = 3, base_delay: float = 1.0) -> Callable[[F], F]:
    """Decorator for retrying network operations (caps delay at 30s)."""
    return retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=30.0,
        exceptions=(requests.RequestException, ConnectionError, TimeoutError),
    )


def _tmdb_auth(api_key: str) -> tuple[dict, dict]:
    """Build (headers, params) for TMDB auth based on key type.

    v4 read-access tokens use a Bearer header; v3 keys use an api_key param.
    """
    headers: dict = {}
    params: dict = {}
    if len(api_key) > _V4_TOKEN_MIN_LEN:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        params["api_key"] = api_key
    return headers, params


def _tmdb_get_json(url: str, api_key: str, query_params: dict | None = None) -> dict | None:
    """Perform an authenticated TMDB GET and return parsed JSON.

    Returns None if the request fails (logs the error). Raises nothing —
    callers supply their own default return value.
    """
    headers, params = _tmdb_auth(api_key)
    if query_params:
        params.update(query_params)
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDB request failed for {url}: {e}")
        return None


def _strip_the_prefix(name: str) -> list[str]:
    """Variation with a leading 'The ' removed."""
    if name.lower().startswith("the "):
        return [name[4:].strip()]
    return []


def _punctuation_variants(name: str) -> list[str]:
    """Variations swapping common punctuation forms."""
    variants = []
    if ":" in name:
        variants.append(name.replace(":", " -"))
        variants.append(name.replace(":", ""))
    if " - " in name:
        variants.append(name.replace(" - ", ": "))
    if "&" in name:
        variants.append(name.replace("&", "and"))
    elif " and " in name.lower():
        variants.append(re.sub(r"\band\b", "&", name, flags=re.IGNORECASE))
    return variants


def _remove_common_words(name: str) -> list[str]:
    """Variations with common collection words removed."""
    variants = []
    for word in ("Season", "Complete", "Series", "Collection"):
        if word.lower() in name.lower():
            cleaned = re.sub(rf"\s*\b{word}\b\s*", " ", name, flags=re.IGNORECASE).strip()
            if cleaned and cleaned != name:
                variants.append(cleaned)
    return variants


def generate_name_variations(name: str) -> list[str]:
    """Generate search query variations for a show/movie name.

    Handles underscores, season indicators, punctuation, "The" prefix, etc.
    Used by fetch_show_id, fetch_movie_id, and tmdb_classifier.

    Args:
        name: Raw name parsed from volume label

    Returns:
        List of alternative search strings to try (deduplicated, excluding original)
    """
    variations = []
    current = name

    # 1. Try without "The" prefix
    variations.extend(_strip_the_prefix(current))

    # 2. Try punctuation variations
    variations.extend(_punctuation_variants(current))

    # 3. Try removing common words
    variations.extend(_remove_common_words(current))

    # Underscore/dot/dash normalization
    normalized = current.replace("_", " ").replace(".", " ")
    if normalized != current:
        variations.append(normalized)
        current = normalized

    # Remove season/disc indicators (S1, S1D1, Season 1, etc.)
    patterns_to_remove = [
        r"\s+S\d+D\d+",
        r"\s+S\d+",
        r"\s+Season\s+\d+",
        r"\s+Disc\s+\d+",
        r"\s+D\d+",
    ]

    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, "", current, flags=re.IGNORECASE)
        if cleaned != current and cleaned.strip():
            variations.append(cleaned.strip())
            current = cleaned.strip()

    # Remove year/parenthetical content
    cleaned = re.sub(r"\s*\(\d{4}\)", "", current).strip()
    if cleaned != current and cleaned:
        variations.append(cleaned)
        current = cleaned

    cleaned = re.sub(r"\s*\([^)]+\)", "", current).strip()
    if cleaned != current and cleaned:
        variations.append(cleaned)

    # Remove subtitle after dash
    if " - " in current:
        before_dash = current.split(" - ")[0].strip()
        if before_dash and before_dash != current:
            variations.append(before_dash)

    # Remove common suffixes
    suffixes_to_try = [
        r"\s+Complete\s+Series$",
        r"\s+The\s+Complete\s+Series$",
        r"\s+US$",
        r"\s+UK$",
        r"\s+\(US\)$",
        r"\s+\(UK\)$",
    ]

    for suffix in suffixes_to_try:
        for var in [name] + variations[:]:
            cleaned = re.sub(suffix, "", var, flags=re.IGNORECASE).strip()
            if cleaned and cleaned not in variations and cleaned != name:
                variations.append(cleaned)

    # Word-based fallback variations for clean names
    if len(variations) == 0:
        words = name.split()
        if len(words) > 1:
            without_first = " ".join(words[1:])
            if without_first and len(without_first) > 2:
                variations.append(without_first)
            without_last = " ".join(words[:-1])
            if without_last and len(without_last) > 2 and without_last != without_first:
                variations.append(without_last)

    # Deduplicate
    seen = {name}
    unique_variations = []
    for v in variations:
        if v and v not in seen and len(v) > 2:
            seen.add(v)
            unique_variations.append(v)

    variations = unique_variations

    # Handle "NameNumber" (e.g. Southpark6 -> Southpark)
    name_num_match = re.match(r"^(.+?)(\d+)$", current)
    if name_num_match:
        name_part, num_part = name_num_match.groups()
        if len(name_part) > 2:
            name_part = name_part.strip()
            variations.append(name_part)
            variations.append(f"{name_part} {num_part}")

            if " " not in name_part and 6 <= len(name_part) <= 20:
                for i in range(2, len(name_part) - 1):
                    variations.append(f"{name_part[:i]} {name_part[i:]}")

    # Brute force split (e.g. Southpark -> South Park)
    if " " not in current and 6 <= len(current) <= 20:
        for i in range(2, len(current) - 1):
            split_var = f"{current[:i]} {current[i:]}"
            variations.append(split_var)

    return variations


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_show_id(show_name: str) -> str | None:
    """
    Fetch the TMDb ID for a given show name with fuzzy fallback.

    Args:
        show_name (str): The name of the show.

    Returns:
        str: The TMDb ID of the show, or None if not found.
    """
    # Try to get API key from Engram settings first, then fallback to matcher config
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    api_key = config.tmdb_api_key

    if not api_key:
        logger.warning("TMDB API key not configured in Engram settings")
        return None

    logger.debug(
        f"Searching TMDB for '{show_name}' using API key ending in ...{api_key[-4:] if len(api_key) > 4 else '****'}"
    )

    url = "https://api.themoviedb.org/3/search/tv"

    variations = generate_name_variations(show_name)

    headers, params = _tmdb_auth(api_key)
    params["query"] = show_name

    # Try exact match first
    response = requests.get(url, headers=headers, params=params, timeout=30)

    results = []
    if response.status_code == 200:
        results = response.json().get("results", [])
        logger.debug(f"TMDB search for '{show_name}': {len(results)} results")

        if results:
            logger.debug(
                f"Top result: {results[0].get('name')} ({results[0].get('first_air_date')}) ID: {results[0].get('id')}"
            )
            best_match = results[0]
            logger.info(
                f"Matched '{show_name}' to TMDB: '{best_match['name']}' (ID: {best_match['id']})"
            )
            return str(best_match["id"])

        # Try common variations if exact match fails
        for variation in variations:
            if variation != show_name and variation:  # Skip if same or empty
                variation_params = params.copy()
                variation_params["query"] = variation

                response = requests.get(url, headers=headers, params=variation_params, timeout=30)
                if response.status_code == 200:
                    results = response.json().get("results", [])
                    if results:
                        best_match = results[0]
                        logger.info(
                            f"Matched '{show_name}' (via '{variation}') to TMDB: "
                            f"'{best_match['name']}' (ID: {best_match['id']})"
                        )
                        return str(best_match["id"])

    # Fallback: Fuzzy match against popular shows.
    # Handles cases like "Southpark" -> "South Park" (missing spaces).
    try:
        popular_shows = fetch_popular_shows(page=1)

        # Build map of name -> id
        popular_map = {s["name"]: s["id"] for s in popular_shows}
        popular_names = list(popular_map.keys())

        import difflib

        # Try matching the original name and variations
        candidates = [show_name] + variations
        for candidate in candidates:
            matches = difflib.get_close_matches(candidate, popular_names, n=1, cutoff=0.8)
            if matches:
                match_name = matches[0]
                match_id = popular_map[match_name]
                logger.info(
                    f"Fuzzy matched '{show_name}' to popular show: '{match_name}' (ID: {match_id})"
                )
                return str(match_id)

    except Exception as e:
        logger.warning(f"Error during popular show fuzzy match: {e}")

    num_variations = len([v for v in variations if v != show_name and v]) + 1
    logger.warning(
        f"Could not find show '{show_name}' on TMDB (tried {num_variations} variations). API Key valid: {bool(api_key)}"
    )
    if not results and response.status_code == 200:
        logger.debug(f"TMDB Response: {response.text[:500]}")
    return None


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_show_details(show_id: int) -> dict | None:
    """
    Fetch show details from TMDB by ID.

    Args:
        show_id: The TMDB show ID

    Returns:
        dict: Show details including 'name', 'number_of_seasons', etc.
        None: If request fails or API key not configured
    """
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    api_key = config.tmdb_api_key

    if not api_key:
        logger.warning("TMDB API key not configured")
        return None

    url = f"https://api.themoviedb.org/3/tv/{show_id}"
    return _tmdb_get_json(url, api_key)


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_popular_shows(page: int = 1) -> list[dict]:
    """
    Fetch popular TV shows from TMDB.

    Args:
        page (int): Page number (default: 1)

    Returns:
        list[dict]: List of show objects (id, name, etc.)
    """
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    if not config.tmdb_api_key:
        logger.warning("TMDB API key not configured")
        return []

    # Sanitize API key
    api_key = config.tmdb_api_key.strip()
    url = "https://api.themoviedb.org/3/tv/popular"

    data = _tmdb_get_json(url, api_key, {"language": "en-US", "page": page})
    if data is None:
        return []
    return data.get("results", [])


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_season_details(show_id: str, season_number: int) -> int:
    """
    Fetch the total number of episodes for a given show and season from the TMDb API.

    Args:
        show_id (str): The ID of the show on TMDb.
        season_number (int): The season number to fetch details for.

    Returns:
        int: The total number of episodes in the season, or 0 if the API request failed.
    """
    logger.info(f"Fetching season details for Season {season_number}...")
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    tmdb_api_key = config.tmdb_api_key

    if not tmdb_api_key:
        logger.warning("TMDB API key not configured")
        return 0

    url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_number}"
    season_data = _tmdb_get_json(url, tmdb_api_key)
    if season_data is None:
        return 0
    return len(season_data.get("episodes", []))


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_season_episode_runtimes(show_id: str, season_number: int) -> list[int]:
    """
    Fetch episode runtimes for a given show and season from the TMDB API.

    Args:
        show_id: The TMDB show ID.
        season_number: The season number to fetch runtimes for.

    Returns:
        list[int]: Episode runtimes in minutes, or empty list if the request failed.
    """
    logger.info(f"Fetching episode runtimes for show {show_id} Season {season_number}...")
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    tmdb_api_key = config.tmdb_api_key
    if not tmdb_api_key:
        logger.warning("TMDB API key not configured")
        return []

    url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_number}"
    season_data = _tmdb_get_json(url, tmdb_api_key)
    if season_data is None:
        return []
    episodes = season_data.get("episodes", [])
    runtimes = [ep.get("runtime", 0) or 0 for ep in episodes]
    logger.info(f"Got {len(runtimes)} episode runtimes for Season {season_number}: {runtimes}")
    return runtimes


@retry_network_operation(max_retries=3, base_delay=1.0)
def get_number_of_seasons(show_id: str) -> int:
    """
    Retrieves the number of seasons for a given TV show from the TMDB API.

    Parameters:
    - show_id (int): The ID of the TV show.

    Returns:
    - num_seasons (int): The number of seasons for the TV show.

    Raises:
    - requests.HTTPError: If there is an error while making the API request.
    """
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    tmdb_api_key = config.tmdb_api_key
    url = f"https://api.themoviedb.org/3/tv/{show_id}"

    headers, params = _tmdb_auth(tmdb_api_key)

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    show_data = response.json()
    num_seasons = show_data.get("number_of_seasons", 0)
    logger.info(f"Found {num_seasons} seasons")
    return num_seasons


@retry_network_operation(max_retries=3, base_delay=1.0)
def fetch_movie_id(movie_name: str) -> str | None:
    """Fetch the TMDB ID for a given movie name with variation fallback.

    Args:
        movie_name: The name of the movie.

    Returns:
        The TMDB ID of the movie, or None if not found.
    """
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    api_key = config.tmdb_api_key

    if not api_key:
        logger.warning("TMDB API key not configured")
        return None

    url = "https://api.themoviedb.org/3/search/movie"
    variations = generate_name_variations(movie_name)

    headers, params = _tmdb_auth(api_key)
    params["query"] = movie_name

    response = requests.get(url, headers=headers, params=params, timeout=30)

    results = []
    if response.status_code == 200:
        results = response.json().get("results", [])
        if results:
            best_match = results[0]
            logger.info(
                f"Matched movie '{movie_name}' to TMDB: "
                f"'{best_match.get('title')}' (ID: {best_match['id']})"
            )
            return str(best_match["id"])

        # Try variations if exact match fails
        for variation in variations:
            if variation != movie_name and variation:
                variation_params = params.copy()
                variation_params["query"] = variation

                response = requests.get(url, headers=headers, params=variation_params, timeout=30)
                if response.status_code == 200:
                    results = response.json().get("results", [])
                    if results:
                        best_match = results[0]
                        logger.info(
                            f"Matched movie '{movie_name}' (via '{variation}') to TMDB: "
                            f"'{best_match.get('title')}' (ID: {best_match['id']})"
                        )
                        return str(best_match["id"])

    logger.warning(f"Could not find movie '{movie_name}' on TMDB")
    return None
