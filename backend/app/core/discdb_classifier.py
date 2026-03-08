"""TheDiscDB-based disc classification and episode mapping.

Queries TheDiscDB's GraphQL API to identify discs by ContentHash or name,
providing high-confidence classification and pre-mapped episode assignments
that can skip audio fingerprinting entirely.

API: https://thediscdb.com/graphql/
Data: https://github.com/thediscdb/data (MIT license)
"""

import logging
import re
from dataclasses import dataclass, field

import requests

from app.core.analyst import TitleInfo
from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)

DISCDB_GRAPHQL_URL = "https://thediscdb.com/graphql/"

# GraphQL query for ContentHash lookup — returns the matching disc's titles
HASH_LOOKUP_QUERY = """
query LookupByHash($hash: String!) {
  mediaItems(
    where: {
      releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } }
    }
  ) {
    nodes {
      title
      type
      year
      slug
      externalids { tmdb imdb }
      releases {
        slug
        discs {
          contentHash
          slug
          titles {
            index
            duration
            size
            item {
              title
              type
              season
              episode
            }
          }
        }
      }
    }
  }
}
"""

# GraphQL query for name-based search
NAME_SEARCH_QUERY = """
query SearchByName($name: String!) {
  mediaItems(
    first: 5
    where: { title: { contains: $name } }
  ) {
    nodes {
      title
      type
      year
      slug
      externalids { tmdb imdb }
      releases {
        slug
        discs {
          contentHash
          slug
          titles {
            index
            duration
            size
            item {
              title
              type
              season
              episode
            }
          }
        }
      }
    }
  }
}
"""


@dataclass
class DiscDbTitleMapping:
    """Pre-mapped title-to-episode assignment from TheDiscDB."""

    index: int
    title_type: str  # "Episode", "MainMovie", "Extra", or ""
    episode_title: str = ""
    season: int | None = None
    episode: int | None = None
    duration_seconds: int = 0
    size_bytes: int = 0


@dataclass
class DiscDbSignal:
    """Signal from TheDiscDB about disc content."""

    content_type: ContentType
    confidence: float
    matched_title: str  # e.g., "Band of Brothers"
    matched_year: int | None = None
    title_mappings: list[DiscDbTitleMapping] = field(default_factory=list)
    content_hash: str | None = None
    source: str = "hash_match"  # "hash_match", "name_search"
    disc_slug: str | None = None  # e.g., "S01D01"
    tmdb_id: int | None = None

    def __repr__(self) -> str:
        return (
            f"DiscDbSignal(content_type={self.content_type.value}, "
            f"confidence={self.confidence:.0%}, matched_title={self.matched_title!r}, "
            f"source={self.source}, mappings={len(self.title_mappings)})"
        )


def _parse_duration(duration_str: str) -> int:
    """Parse TheDiscDB duration string like '1:13:14' to seconds."""
    parts = duration_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def _graphql_request(query: str, variables: dict, timeout: float = 10.0) -> dict | None:
    """Execute a GraphQL query against TheDiscDB API."""
    try:
        response = requests.post(
            DISCDB_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.warning(f"TheDiscDB API returned {response.status_code}")
            return None
        data = response.json()
        if "errors" in data:
            logger.warning(f"TheDiscDB GraphQL errors: {data['errors']}")
            return None
        return data.get("data")
    except (requests.RequestException, ConnectionError, TimeoutError) as e:
        logger.warning(f"TheDiscDB API request failed: {e}")
        return None


def _disc_type_to_content_type(media_type: str) -> ContentType:
    """Convert TheDiscDB media type to our ContentType."""
    media_type_lower = media_type.lower()
    if media_type_lower == "series":
        return ContentType.TV
    if media_type_lower == "movie":
        return ContentType.MOVIE
    return ContentType.UNKNOWN


def _build_signal_from_match(
    node: dict,
    matched_disc: dict,
    content_hash: str | None,
    source: str,
) -> DiscDbSignal:
    """Build a DiscDbSignal from a matched mediaItem node and disc."""
    content_type = _disc_type_to_content_type(node.get("type", ""))

    # Build title mappings from the matched disc
    mappings = []
    for title in matched_disc.get("titles", []):
        item = title.get("item") or {}
        season = None
        episode = None
        if item.get("season"):
            try:
                season = int(item["season"])
            except (ValueError, TypeError):
                pass
        if item.get("episode"):
            try:
                episode = int(item["episode"])
            except (ValueError, TypeError):
                pass

        mappings.append(
            DiscDbTitleMapping(
                index=title.get("index", 0),
                title_type=item.get("type", ""),
                episode_title=item.get("title", ""),
                season=season,
                episode=episode,
                duration_seconds=_parse_duration(title.get("duration", "0:00:00")),
                size_bytes=title.get("size", 0),
            )
        )

    # Extract TMDB ID if available
    tmdb_id = None
    ext_ids = node.get("externalids")
    if ext_ids and ext_ids.get("tmdb"):
        try:
            tmdb_id = int(ext_ids["tmdb"])
        except (ValueError, TypeError):
            pass

    confidence = 0.98 if source == "hash_match" else 0.70

    return DiscDbSignal(
        content_type=content_type,
        confidence=confidence,
        matched_title=node.get("title", ""),
        matched_year=node.get("year"),
        title_mappings=mappings,
        content_hash=content_hash,
        source=source,
        disc_slug=matched_disc.get("slug"),
        tmdb_id=tmdb_id,
    )


def _find_matching_disc(
    nodes: list[dict],
    content_hash: str,
) -> tuple[dict, dict] | None:
    """Find the specific disc matching the content hash within the results."""
    for node in nodes:
        for release in node.get("releases", []):
            for disc in release.get("discs", []):
                if disc.get("contentHash", "").upper() == content_hash.upper():
                    return node, disc
    return None


def _find_best_disc_by_durations(
    nodes: list[dict],
    titles: list[TitleInfo],
) -> tuple[dict, dict, float] | None:
    """Find the disc whose duration pattern best matches the scanned titles.

    Returns (node, disc, match_score) or None.
    """
    if not titles:
        return None

    scanned_durations = sorted([t.duration_seconds for t in titles], reverse=True)
    best_match = None
    best_score = 0.0

    for node in nodes:
        for release in node.get("releases", []):
            for disc in release.get("discs", []):
                disc_titles = disc.get("titles", [])
                if not disc_titles:
                    continue

                db_durations = sorted(
                    [_parse_duration(t.get("duration", "0:00:00")) for t in disc_titles],
                    reverse=True,
                )

                # Compare title count and duration patterns
                if len(scanned_durations) != len(db_durations):
                    continue

                # Calculate duration match score
                total_diff = 0
                total_duration = 0
                for s_dur, d_dur in zip(scanned_durations, db_durations, strict=True):
                    total_diff += abs(s_dur - d_dur)
                    total_duration += max(s_dur, d_dur)

                if total_duration == 0:
                    continue

                # Score: 1.0 = perfect match, lower = worse
                score = 1.0 - (total_diff / total_duration)
                if score > best_score:
                    best_score = score
                    best_match = (node, disc, score)

    if best_match and best_match[2] >= 0.90:
        return best_match
    return None


def classify_from_discdb(
    volume_label: str,
    titles: list[TitleInfo],
    content_hash: str | None = None,
    timeout: float = 10.0,
) -> DiscDbSignal | None:
    """Query TheDiscDB for disc identification.

    Tries three strategies in priority order:
    1. ContentHash exact match (highest confidence)
    2. Name search + duration fingerprint matching
    3. Name search alone (lowest confidence)

    Args:
        volume_label: Disc volume label for name-based fallback
        titles: Scanned title info from MakeMKV
        content_hash: MD5 content hash from MakeMKV (if available)
        timeout: Network timeout per request

    Returns:
        DiscDbSignal if a match is found, None otherwise
    """
    # Strategy 1: ContentHash lookup (highest confidence)
    if content_hash:
        logger.info(f"TheDiscDB: looking up ContentHash {content_hash}")
        data = _graphql_request(HASH_LOOKUP_QUERY, {"hash": content_hash}, timeout)
        if data:
            nodes = data.get("mediaItems", {}).get("nodes", [])
            if nodes:
                result = _find_matching_disc(nodes, content_hash)
                if result:
                    node, disc = result
                    signal = _build_signal_from_match(node, disc, content_hash, "hash_match")
                    logger.info(f"TheDiscDB: hash match -> {signal}")
                    return signal

    # Strategy 2 & 3: Name-based search
    parsed_name = _parse_name_from_label(volume_label)
    if not parsed_name:
        logger.info(f"TheDiscDB: could not parse name from '{volume_label}'")
        return None

    logger.info(f"TheDiscDB: searching by name '{parsed_name}'")
    data = _graphql_request(NAME_SEARCH_QUERY, {"name": parsed_name}, timeout)
    if not data:
        return None

    nodes = data.get("mediaItems", {}).get("nodes", [])
    if not nodes:
        logger.info(f"TheDiscDB: no results for '{parsed_name}'")
        return None

    # Strategy 2: Name search + duration fingerprint
    duration_match = _find_best_disc_by_durations(nodes, titles)
    if duration_match:
        node, disc, score = duration_match
        signal = _build_signal_from_match(node, disc, content_hash, "name_search")
        signal.confidence = min(0.90, 0.70 + score * 0.20)  # 0.70-0.90 based on match quality
        logger.info(f"TheDiscDB: duration fingerprint match (score={score:.2f}) -> {signal}")
        return signal

    # Strategy 3: Name search only — return the first result's first disc
    node = nodes[0]
    releases = node.get("releases", [])
    if releases:
        discs = releases[0].get("discs", [])
        if discs:
            signal = _build_signal_from_match(node, discs[0], content_hash, "name_search")
            signal.confidence = 0.50  # Low confidence — name match only
            logger.info(f"TheDiscDB: name-only match -> {signal}")
            return signal

    return None


def _parse_name_from_label(volume_label: str) -> str | None:
    """Extract a searchable name from a volume label.

    Examples:
        "BAND_OF_BROTHERS_S1D1" -> "Band of Brothers"
        "INCEPTION_2010" -> "Inception"
        "THE_OFFICE_S1" -> "The Office"
    """
    if not volume_label:
        return None

    name = volume_label.replace("_", " ").replace("-", " ")

    # Remove common suffixes: S01D01, S1D1, DISC1, D1, etc.
    name = re.sub(r"\s*S\d+D\d+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*S\d+\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*D(?:ISC)?\s*\d+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*(?:CD|DVD|BD|BLURAY|BLU[\s-]?RAY)\s*\d*\s*$", "", name, flags=re.IGNORECASE)

    # Remove year at end (but keep it separate)
    name = re.sub(r"\s*(?:19|20)\d{2}\s*$", "", name)

    name = name.strip()
    if not name:
        return None

    # Title case for better search matching
    return name.title()
