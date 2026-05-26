"""Curator - Episode Matching Integration.

Integrates with the local MKV episode matcher for audio fingerprint-based episode identification.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.matcher.llm_episode_matcher import match_episode_via_llm

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
            from app.matcher.tmdb_client import fetch_show_details, fetch_show_id

            # Get cache directory from config (sync version for non-async context)
            from app.services.config_service import get_config_sync

            # Resolve canonical show name
            canonical_name = show_name
            try:
                # We need to run async TMDB calls in a sync context here or use sync versions?
                # tmdb_client functions are synchronous (requests based)
                tmdb_id = fetch_show_id(show_name)
                if tmdb_id:
                    details = fetch_show_details(tmdb_id)
                    if details and "name" in details:
                        canonical_name = details["name"]
                        logger.info(
                            f"Resolved '{show_name}' to canonical '{canonical_name}' for matching"
                        )
            except Exception as e:
                logger.warning(f"Failed to resolve canonical name for '{show_name}': {e}")

            config = get_config_sync()
            if config and config.subtitles_cache_path:
                self._cache_dir = Path(config.subtitles_cache_path).expanduser()
            else:
                # Fallback to default Engram cache location
                self._cache_dir = Path.home() / ".engram" / "cache"

            self._cache_dir.mkdir(parents=True, exist_ok=True)

            self._matcher = EpisodeMatcher(
                cache_dir=self._cache_dir,
                show_name=canonical_name,
                min_confidence=self.LOW_CONFIDENCE_THRESHOLD,
            )
            self._initialized = True
            logger.info(
                f"Episode matcher initialized for show: {show_name} (cache_dir={self._cache_dir})"
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

    def _fallback_result(
        self,
        file_path: Path,
        *,
        parse_filename: bool = True,
        match_details: dict | None = None,
    ) -> MatchResult:
        """Build an unmatched MatchResult that always needs review.

        When parse_filename is True, attempts to recover an episode code from the
        filename (confidence 0.3 if found, else 0.0). When False, the result is
        always fully unmatched (confidence 0.0).
        """
        episode_code = self._parse_episode_from_filename(file_path.name) if parse_filename else None
        return MatchResult(
            file_path=file_path,
            episode_code=episode_code,
            episode_title=None,
            confidence=0.3 if episode_code else 0.0,
            needs_review=True,
            match_details=match_details,
        )

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
                results.append(self._fallback_result(file_path))
            return results

        if not self._ensure_initialized(series_name):
            # Return unmatched results if matcher not available
            for i, file_path in enumerate(files):
                if progress_callback:
                    progress_callback(i + 1, total_files)
                results.append(self._fallback_result(file_path, parse_filename=False))
            return results

        for i, file_path in enumerate(files):
            try:
                result = await self.match_single_file(file_path, series_name, season)
                results.append(result)
            except Exception as e:
                logger.error(f"Error matching {file_path}: {e}")
                results.append(self._fallback_result(file_path, parse_filename=False))

            if progress_callback:
                progress_callback(i + 1, total_files)

        return results

    async def match_single_file(
        self,
        file_path: Path,
        series_name: str | None,
        season: int | None,
        progress_callback: Callable[..., None] | None = None,
        num_points: int | None = None,
        min_vote_count: int | None = None,
    ) -> MatchResult:
        """Match a single file to an episode using audio fingerprinting.

        ``num_points``/``min_vote_count`` override the matcher's scan density and
        minimum vote gate (used by the deep re-match path); None keeps defaults.
        """
        logger.info(
            f"match_single_file called: {file_path.name}, series={series_name}, season={season}"
        )

        if not file_path.exists():
            logger.error(f"File does not exist: {file_path}")
            # non-fatal: fallback handles it

        # Ensure matcher is initialized for this show
        if series_name:
            initialized = self._ensure_initialized(series_name)
            logger.info(
                f"Matcher initialized={initialized}, matcher={'available' if self._matcher else 'None'}"
            )

        if not self._matcher or not season:
            # Fall back to filename parsing if matcher unavailable or no season
            return self._fallback_result(file_path)

        try:
            # Run the matcher in a thread to not block async loop
            logger.debug(f"[Curator] Starting identifying_episode in thread for {file_path.name}")
            match = await asyncio.to_thread(
                self._matcher.identify_episode,
                file_path,
                self._cache_dir,
                season,
                progress_callback,
                num_points,
                min_vote_count,
            )
            logger.debug(f"[Curator] identify_episode returned for {file_path.name}: {match}")

            if match and match.get("episode") is not None:
                episode_code = f"S{match['season']:02d}E{match['episode']:02d}"
                confidence = match.get("confidence", 0.0)
                needs_review = confidence < self.HIGH_CONFIDENCE_THRESHOLD

                logger.info(
                    f"Matched {file_path.name} -> {episode_code} (confidence: {confidence:.2f})"
                )

                # Include runner_ups in match_details for cascading conflict resolution
                details = match.get("match_details") or {}
                if match.get("runner_ups"):
                    details = dict(details)  # Copy to avoid mutating original
                    details["runner_ups"] = match["runner_ups"]

                # LLM episode-matching fallback — only runs when the primary
                # match needs review, config is enabled, and the season is known.
                # Reuse the primary matcher's transcript if it took the
                # full-file fallback path (avoids re-running Whisper).
                if needs_review and season:
                    existing_transcript = match.get("transcript") if match else None
                    enriched = await self._maybe_add_llm_suggestion(
                        file_path=file_path,
                        series_name=series_name,
                        season=season,
                        match_details=details,
                        existing_transcript=existing_transcript,
                    )
                    if enriched is not None:
                        details = enriched

                return MatchResult(
                    file_path=file_path,
                    episode_code=episode_code,
                    episode_title=None,  # Could fetch from TMDB
                    confidence=confidence,
                    needs_review=needs_review,
                    match_details=details,
                )
            else:
                # No match found - fall back to filename, preserving stats if available
                details = match.get("match_details") if match else None
                fallback = self._fallback_result(file_path, match_details=details)
                if season:
                    existing_transcript = match.get("transcript") if match else None
                    enriched = await self._maybe_add_llm_suggestion(
                        file_path=file_path,
                        series_name=series_name,
                        season=season,
                        match_details=fallback.match_details or {},
                        existing_transcript=existing_transcript,
                    )
                    if enriched is not None:
                        fallback.match_details = enriched
                return fallback

        except Exception as e:
            logger.error(f"Matcher error for {file_path}: {e}")
            # Fall back to filename parsing
            return self._fallback_result(file_path)

    async def _maybe_add_llm_suggestion(
        self,
        *,
        file_path: Path,
        series_name: str,
        season: int,
        match_details: dict,
        existing_transcript: str | None = None,
    ) -> dict | None:
        """Run the LLM matcher when enabled and attach the suggestion to match_details.

        Returns the updated match_details dict, or None to keep the caller's dict.

        ``existing_transcript`` lets callers pass through a transcript the
        primary matcher already produced (via the full-file fallback path),
        avoiding a duplicate Whisper run when the matcher just transcribed.
        """
        from app.services.config_service import get_config

        config = await get_config()
        if not config or not getattr(config, "ai_episode_matching_enabled", False):
            return None
        if not config.ai_api_key:
            return None

        # Resolve TMDB show id (synchronous, run in thread)
        from app.matcher.tmdb_client import fetch_show_id

        tmdb_show_id = await asyncio.to_thread(fetch_show_id, series_name)
        if not tmdb_show_id:
            logger.info(f"LLM fallback: no TMDB show_id for {series_name!r}")
            return None

        if not self._matcher:
            return None

        if existing_transcript:
            transcript = existing_transcript
        else:
            transcript = await asyncio.to_thread(self._matcher.transcribe_full, file_path)
        if not transcript:
            return None

        try:
            suggestion = await match_episode_via_llm(
                transcript=transcript,
                show_name=series_name,
                season=season,
                tmdb_show_id=str(tmdb_show_id),
                ai_provider=config.ai_provider,
                ai_api_key=config.ai_api_key,
                tmdb_api_key=config.tmdb_api_key,
            )
        except Exception as e:
            logger.warning(f"LLM fallback raised: {e}", exc_info=True)
            return None

        if not suggestion:
            return None

        enriched = dict(match_details) if match_details else {}
        enriched["llm_suggestion"] = {
            "episode": suggestion.episode,
            "confidence": suggestion.confidence,
            "reasoning": suggestion.reasoning,
            "runner_up": (
                {
                    "episode": suggestion.runner_up.episode,
                    "confidence": suggestion.runner_up.confidence,
                }
                if suggestion.runner_up is not None
                else None
            ),
            "model": suggestion.model,
        }
        return enriched

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
