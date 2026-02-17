"""Analyst - Disc Identification and Classification Engine.

Analyzes disc structure to determine content type (TV/Movie) using heuristics.
"""

import logging
import re
from dataclasses import dataclass

from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)


@dataclass
class TitleInfo:
    """Information about a single title on a disc."""

    index: int
    duration_seconds: int
    size_bytes: int
    chapter_count: int
    name: str = ""
    video_resolution: str = ""


@dataclass
class DiscAnalysisResult:
    """Result of analyzing a disc's content."""

    content_type: ContentType
    titles: list[TitleInfo]
    detected_name: str | None = None
    detected_season: int | None = None
    confidence: float = 0.0
    needs_review: bool = False
    review_reason: str | None = None


class DiscAnalyst:
    """Analyzes disc structure to classify content type."""

    def __init__(self, config=None):
        """Initialize analyst with optional configuration.

        Args:
            config: AppConfig instance. If None, loads from database.
        """
        self._config = config

    def _get_config(self):
        """Get config, loading from database if not provided."""
        if self._config is None:
            from app.services.config_service import get_config_sync
            self._config = get_config_sync()
        return self._config

    def analyze(self, titles: list[TitleInfo], volume_label: str = "") -> DiscAnalysisResult:
        """Analyze a list of titles to determine content type.

        Args:
            titles: List of title information from MakeMKV
            volume_label: The disc's volume label (e.g., "THE_OFFICE_S1")

        Returns:
            Analysis result with content type and confidence
        """
        logger.info(f"Analyzing disc: '{volume_label}' with {len(titles)} titles")

        if not titles:
            return DiscAnalysisResult(
                content_type=ContentType.UNKNOWN,
                titles=[],
                needs_review=True,
                review_reason="No titles found on disc",
            )

        # Log title durations for debugging
        durations_str = ", ".join([f"{t.duration_seconds//60}min" for t in titles[:10]])
        if len(titles) > 10:
            durations_str += f", ... ({len(titles)-10} more)"
        logger.info(f"Title durations: {durations_str}")


        # Try to extract show name, season, and disc from volume label
        detected_name, detected_season, detected_disc = self._parse_volume_label(volume_label)

        # If we found a season pattern (S01D02), it's very likely a TV show
        is_likely_tv = detected_season is not None
        if is_likely_tv:
            logger.info(f"Volume label indicates TV (season {detected_season})")

        # ALWAYS check for movie first (content overrides label)
        movie_result = self._detect_movie(titles)
        logger.info(f"Movie detection result: {movie_result}")
        
        if movie_result:
            # If we found a high confidence movie, return it immediately
            # This handles cases where label might be misleading (e.g. "TROPIC_THUNDER_S1")
            # but content is clearly a movie.
            if not movie_result.get("ambiguous"):
                logger.info(f"Movie detected with {movie_result['confidence']:.1%} confidence")
                return DiscAnalysisResult(
                    content_type=ContentType.MOVIE,
                    titles=titles,
                    detected_name=detected_name,
                    confidence=movie_result["confidence"],
                )
            
            # If ambiguous movie (e.g. multiple long titles), we'll hold onto it
            # and see if TV detection makes more sense (e.g. Sherlock episodes).
            logger.info(f"Ambiguous movie detected: {movie_result.get('reason')}")

        # Check for TV show (cluster of similar-duration titles)
        tv_result = self._detect_tv_show(titles)
        if tv_result:
            logger.info(
                f"TV show detected with {tv_result['confidence']:.1%} confidence "
                f"({tv_result['episode_count']} episodes)"
            )
            return DiscAnalysisResult(
                content_type=ContentType.TV,
                titles=titles,
                detected_name=detected_name,
                detected_season=detected_season,
                confidence=tv_result["confidence"],
            )

        # If we have an ambiguous movie result and NO TV result, return the ambiguous movie result
        if movie_result and movie_result.get("ambiguous"):
            return DiscAnalysisResult(
                content_type=ContentType.MOVIE,
                titles=titles,
                detected_name=detected_name,
                confidence=0.0,
                needs_review=True,
                review_reason=movie_result["reason"]
            )
        
        # If volume label indicates TV (has season pattern) but heuristics didn't detect it,
        # trust the volume label with moderate confidence
        if is_likely_tv:
            logger.info(f"Volume label indicates TV show (season {detected_season}), trusting label")
            return DiscAnalysisResult(
                content_type=ContentType.TV,
                titles=titles,
                detected_name=detected_name,
                detected_season=detected_season,
                confidence=0.7,  # Moderate confidence based on volume label
            )

        # Ambiguous - needs human review
        reason = self._get_ambiguity_reason(titles)
        logger.info(f"Unable to classify disc: {reason}")
        return DiscAnalysisResult(
            content_type=ContentType.UNKNOWN,
            titles=titles,
            detected_name=detected_name,
            detected_season=detected_season,
            needs_review=True,
            review_reason=reason,
        )

    def _detect_movie(self, titles: list[TitleInfo]) -> dict | None:
        """Detect if the disc contains a movie.

        """
        long_titles = [t for t in titles if t.duration_seconds >= self._get_config().analyst_movie_min_duration]
        logger.info(f"Found {len(long_titles)} movie-length titles (> {self._get_config().analyst_movie_min_duration}s)")

        if len(long_titles) == 1:
            # Single long title - high confidence movie
            main_title = long_titles[0]
            total_duration = sum(t.duration_seconds for t in titles)
            dominance = main_title.duration_seconds / total_duration if total_duration else 0

            # If there's only one movie-length title, classify as movie
            # even with low dominance (lots of bonus features)
            confidence = 0.9 if dominance >= self._get_config().analyst_movie_dominance_threshold else 0.75
            return {"confidence": confidence, "main_title": main_title}

        if len(long_titles) > 3:
            # Too many feature-length titles - likely multi-movie disc or compilation
            # Don't rip automatically, force human review
            return {
                "confidence": 0.0,
                "ambiguous": True,
                "reason": f"Found {len(long_titles)} feature-length titles. This may be a multi-movie disc or compilation. Please review and select which title(s) to rip."
            }

        if len(long_titles) >= 2:
            # 2-3 long titles found - could be theatrical vs extended cut
            # Force review to select correct version
            return {
                "confidence": 0.0,
                "ambiguous": True,
                "reason": "Multiple feature-length titles found. Please select correct version (theatrical, extended, etc.)."
            }
        
        # Fallback for 2 titles logic (removed simple logic in favor of ambiguity check)
        # if len(long_titles) == 2: ... (replaced by above)

        return None

    def _detect_tv_show(self, titles: list[TitleInfo]) -> dict | None:
        """Detect if the disc contains TV episodes.

        TV is detected if 3+ titles share a duration within ±2 minutes
        AND are within typical TV episode duration range (18-70 minutes).
        """
        if len(titles) < self._get_config().analyst_tv_min_cluster_size:
            return None

        # Don't classify as TV if there's a clear movie-length title
        # (even if movie detection failed due to low dominance)
        movie_length_titles = [t for t in titles if t.duration_seconds >= self._get_config().analyst_movie_min_duration]
        if movie_length_titles:
            logger.debug(
                f"Found {len(movie_length_titles)} movie-length title(s), "
                "skipping TV detection"
            )
            return None

        # Filter to only TV-length titles
        tv_length_titles = [
            t for t in titles
            if self._get_config().analyst_tv_min_duration <= t.duration_seconds <= self._get_config().analyst_tv_max_duration
        ]

        if len(tv_length_titles) < self._get_config().analyst_tv_min_cluster_size:
            return None

        # Group titles by approximate duration (within variance)
        clusters: list[list[TitleInfo]] = []

        for title in tv_length_titles:
            placed = False
            for cluster in clusters:
                # Check if this title fits in the cluster
                cluster_avg = sum(t.duration_seconds for t in cluster) / len(cluster)
                if abs(title.duration_seconds - cluster_avg) <= self._get_config().analyst_tv_duration_variance:
                    cluster.append(title)
                    placed = True
                    break

            if not placed:
                clusters.append([title])

        # Find the largest cluster
        largest_cluster = max(clusters, key=len) if clusters else []

        if len(largest_cluster) >= self._get_config().analyst_tv_min_cluster_size:
            confidence = min(0.95, 0.5 + len(largest_cluster) * 0.1)
            return {"confidence": confidence, "episode_count": len(largest_cluster)}

        return None

    def _parse_volume_label(self, label: str) -> tuple[str | None, int | None, int | None]:
        """Parse show name, season, and disc number from volume label.

        Examples:
            "THE_OFFICE_S1D2" -> ("The Office", 1, 2)
            "THE_OFFICE_S01D02" -> ("The Office", 1, 2)
            "FIREFLY_DISC1" -> ("Firefly", None, 1)
            "BREAKING_BAD_SEASON_2" -> ("Breaking Bad", 2, None)
        """
        if not label:
            return None, None, None

        # Clean up the label
        original = label.upper().replace("_", " ")
        label = original

        # Try to extract season AND disc from combined pattern (S01D02, S1D1, etc.)
        season_disc_match = re.search(r"S(\d+)\s*D(\d+)", label)
        if season_disc_match:
            season = int(season_disc_match.group(1))
            disc = int(season_disc_match.group(2))
            label = re.sub(r"S\d+\s*D\d+", "", label)
            logger.info(f"Parsed volume label '{original}': season={season}, disc={disc}")
        else:
            # Try to extract season number alone
            season = None
            disc = None
            
            season_patterns = [
                r"S(\d+)",
                r"SEASON\s*(\d+)",
                r"SERIES\s*(\d+)",
            ]

            for pattern in season_patterns:
                match = re.search(pattern, label)
                if match:
                    season = int(match.group(1))
                    label = re.sub(pattern, "", label)
                    break

            # Try to extract disc number
            disc_patterns = [
                r"D(\d+)",
                r"DISC\s*(\d+)",
                r"DISK\s*(\d+)",
            ]

            for pattern in disc_patterns:
                match = re.search(pattern, label)
                if match:
                    disc = int(match.group(1))
                    label = re.sub(pattern, "", label)
                    break

        # Remove common disc indicators that aren't disc numbers
        label = re.sub(r"\b(DVD|BLURAY|BD)\s*\d*\b", "", label)
        label = label.strip()

        # Convert to title case
        name = label.title() if label else None

        return name, season, disc

    def _get_ambiguity_reason(self, titles: list[TitleInfo]) -> str:
        """Generate a human-readable reason for ambiguity."""
        long_titles = [t for t in titles if t.duration_seconds >= self._get_config().analyst_movie_min_duration]

        if len(long_titles) >= 2:
            return f"Multiple long titles found ({len(long_titles)} titles > 80 min). Could be multi-movie disc or special features."

        if len(titles) < self._get_config().analyst_tv_min_cluster_size:
            return f"Only {len(titles)} title(s) found. Not enough to determine TV/Movie."

        durations = [t.duration_seconds // 60 for t in titles]
        return f"Inconsistent title durations ({min(durations)}-{max(durations)} min). Unable to classify."
