"""Curator - Episode Matching Integration.

Integrates with the local MKV episode matcher for audio fingerprint-based episode identification.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of matching a file to an episode."""

    file_path: Path
    episode_code: str | None  # e.g., "S01E01"
    episode_title: str | None
    confidence: float
    needs_review: bool
    match_details: dict | None = None


class EpisodeCurator:
    """Handles episode matching using the integrated MKV episode matcher."""

    # Confidence thresholds
    HIGH_CONFIDENCE_THRESHOLD = 0.7
    LOW_CONFIDENCE_THRESHOLD = 0.5

    def __init__(self) -> None:
        self._matcher = None
        self._initialized = False
        self._cache_dir: Path | None = None
        self._current_show: str | None = None

    def _ensure_initialized(self, show_name: str) -> bool:
        """Lazily initialize the matcher library for a specific show."""
        # Re-initialize if show name changed
        if self._initialized and self._current_show == show_name:
            return self._matcher is not None

        self._current_show = show_name

        try:
            # Import from local matcher package
            from app.matcher.episode_identification import EpisodeMatcher

            # Get cache directory from config (sync version for non-async context)
            from app.services.config_service import get_config_sync

            config = get_config_sync()
            if config and config.subtitles_cache_path:
                self._cache_dir = Path(config.subtitles_cache_path).expanduser()
            else:
                # Fallback to default Engram cache location
                self._cache_dir = Path.home() / ".engram" / "cache"

            self._cache_dir.mkdir(parents=True, exist_ok=True)

            self._matcher = EpisodeMatcher(
                cache_dir=self._cache_dir,
                show_name=show_name,
                min_confidence=self.LOW_CONFIDENCE_THRESHOLD,
            )
            self._initialized = True
            logger.info(
                f"Episode matcher initialized for show: {show_name} "
                f"(cache_dir={self._cache_dir})"
            )
            return True
        except ImportError as e:
            logger.warning(f"Episode matcher not available: {e}")
            self._initialized = True
            return False
        except Exception as e:
            logger.error(f"Failed to initialize episode matcher: {e}", exc_info=True)
            self._initialized = True
            return False

    async def match_files(
        self,
        files: list[Path],
        series_name: str | None = None,
        season: int | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[MatchResult]:
        """Match a list of MKV files to episodes.

        Args:
            files: List of MKV file paths to match
            series_name: Series name for reference subtitle lookup
            season: Season number for matching
            progress_callback: Optional callback(current, total)

        Returns:
            List of match results for each file
        """
        results = []
        total_files = len(files)

        # Series name is required for audio fingerprint matching
        if not series_name:
            logger.warning("No series name provided - falling back to filename parsing")
            for file_path in files:
                episode_code = self._parse_episode_from_filename(file_path.name)
                results.append(
                    MatchResult(
                        file_path=file_path,
                        episode_code=episode_code,
                        episode_title=None,
                        confidence=0.3 if episode_code else 0.0,
                        needs_review=True,
                    )
                )
            return results

        if not self._ensure_initialized(series_name):
            # Return unmatched results if matcher not available
            for i, file_path in enumerate(files):
                if progress_callback:
                    progress_callback(i + 1, total_files)
                results.append(
                    MatchResult(
                        file_path=file_path,
                        episode_code=None,
                        episode_title=None,
                        confidence=0.0,
                        needs_review=True,
                    )
                )
            return results

        for i, file_path in enumerate(files):
            try:
                result = await self.match_single_file(file_path, series_name, season)
                results.append(result)
            except Exception as e:
                logger.error(f"Error matching {file_path}: {e}")
                results.append(
                    MatchResult(
                        file_path=file_path,
                        episode_code=None,
                        episode_title=None,
                        confidence=0.0,
                        needs_review=True,
                    )
                )
            
            if progress_callback:
                progress_callback(i + 1, total_files)

        return results

    async def match_single_file(
        self,
        file_path: Path,
        series_name: str | None,
        season: int | None,
        progress_callback: Callable[..., None] | None = None,
    ) -> MatchResult:
        """Match a single file to an episode using audio fingerprinting."""
        logger.info(f"match_single_file called: {file_path.name}, series={series_name}, season={season}")
        
        if not file_path.exists():
            logger.error(f"File does not exist: {file_path}")
            # fall through to fallback logic? or return early?


        # Ensure matcher is initialized for this show
        if series_name:
            initialized = self._ensure_initialized(series_name)
            logger.info(
                f"Matcher initialized={initialized}, matcher={'available' if self._matcher else 'None'}"
            )

        if not self._matcher or not season:
            # Fall back to filename parsing if matcher unavailable or no season
            episode_code = self._parse_episode_from_filename(file_path.name)
            return MatchResult(
                file_path=file_path,
                episode_code=episode_code,
                episode_title=None,
                confidence=0.3 if episode_code else 0.0,
                needs_review=True,
            )

        try:
            # Run the matcher in a thread to not block async loop
            logger.debug(f"[Curator] Starting identifying_episode in thread for {file_path.name}")
            match = await asyncio.to_thread(
                self._matcher.identify_episode,
                file_path,
                self._cache_dir,
                season,
                progress_callback,
            )
            logger.debug(f"[Curator] identify_episode returned for {file_path.name}: {match}")
            
            if match and match.get('episode') is not None:
                episode_code = f"S{match['season']:02d}E{match['episode']:02d}"
                confidence = match.get('confidence', 0.0)
                needs_review = confidence < self.HIGH_CONFIDENCE_THRESHOLD
                
                logger.info(f"Matched {file_path.name} -> {episode_code} (confidence: {confidence:.2f})")
                
                # Include runner_ups in match_details for cascading conflict resolution
                details = match.get('match_details') or {}
                if match.get('runner_ups'):
                    details = dict(details)  # Copy to avoid mutating original
                    details['runner_ups'] = match['runner_ups']

                return MatchResult(
                    file_path=file_path,
                    episode_code=episode_code,
                    episode_title=None,  # Could fetch from TMDB
                    confidence=confidence,
                    needs_review=needs_review,
                    match_details=details,
                )
            else:
                # No match found - fall back to filename
                episode_code = self._parse_episode_from_filename(file_path.name)
                # Preserve stats if available
                details = match.get('match_details') if match else None
                
                return MatchResult(
                    file_path=file_path,
                    episode_code=episode_code,
                    episode_title=None,
                    confidence=0.3 if episode_code else 0.0,
                    needs_review=True,
                    match_details=details
                )
                
        except Exception as e:
            logger.error(f"Matcher error for {file_path}: {e}")
            # Fall back to filename parsing
            episode_code = self._parse_episode_from_filename(file_path.name)
            return MatchResult(
                file_path=file_path,
                episode_code=episode_code,
                episode_title=None,
                confidence=0.3 if episode_code else 0.0,
                needs_review=True,
            )

    def _parse_episode_from_filename(self, filename: str) -> str | None:
        """Try to parse episode code from filename.

        This is a fallback when audio fingerprinting is not available.
        """
        import re

        # Common patterns: S01E01, 1x01, etc.
        patterns = [
            r"S(\d+)E(\d+)",
            r"(\d+)x(\d+)",
            r"Season\s*(\d+)\s*Episode\s*(\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                season = int(match.group(1))
                episode = int(match.group(2))
                return f"S{season:02d}E{episode:02d}"

        return None

    def classify_results(
        self, results: list[MatchResult]
    ) -> tuple[list[MatchResult], list[MatchResult]]:
        """Classify results into high-confidence and needs-review.

        Returns:
            Tuple of (high_confidence_results, needs_review_results)
        """
        high_confidence = []
        needs_review = []

        for result in results:
            if result.confidence >= self.HIGH_CONFIDENCE_THRESHOLD and not result.needs_review:
                high_confidence.append(result)
            else:
                needs_review.append(result)

        return high_confidence, needs_review


# Singleton instance
curator = EpisodeCurator()
