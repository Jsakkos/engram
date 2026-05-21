import abc
import contextlib
import shutil
import signal
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from app import __version__
from app.matcher.config_manager import get_config_manager
from app.matcher.models import EpisodeInfo, SubtitleFile
from app.matcher.os_api_retry import os_api_call
from app.matcher.subtitle_utils import parse_season_episode_numbers, sanitize_filename

# CompositeSubtitleProvider returns early once it has at least this many
# cached subtitles, skipping slower download providers.
_MIN_CACHED_SUBTITLES_TO_SKIP_DOWNLOAD = 3

# NOTE: A generic ``retry_with_backoff`` decorator previously lived in this
# module. The OpenSubtitles call sites now all route through
# ``os_api_retry.os_api_call`` for consistent 429 / Retry-After handling.

# OpenSubtitles best-practices require the User-Agent be in the form
# "AppName vX.Y.Z". Mirrors testing_service._USER_AGENT — kept separate
# only to avoid a cross-module import dependency between two matcher
# files that share a parent module.
_USER_AGENT = f"Engram v{__version__}"


def parse_season_episode(filename: str) -> EpisodeInfo | None:
    """Parse season and episode from filename using regex."""
    parsed = parse_season_episode_numbers(filename)
    if parsed is None:
        return None
    season, episode = parsed
    return EpisodeInfo(series_name="", season=season, episode=episode)


@contextlib.contextmanager
def _alarm_timeout(timeout: int, label: str) -> Iterator[None]:
    """Raise TimeoutError if the wrapped block runs longer than `timeout`.

    Uses SIGALRM and is a no-op on platforms without it (e.g. Windows).
    """

    def timeout_handler(signum, frame):
        raise TimeoutError(f"{label} operation timed out after {timeout}s")

    has_alarm = hasattr(signal, "SIGALRM")
    if has_alarm:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
    try:
        yield
    finally:
        if has_alarm:
            signal.alarm(0)


class SubtitleProvider(abc.ABC):
    @abc.abstractmethod
    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        pass


class LocalSubtitleProvider(SubtitleProvider):
    """Provider that scans a local directory for subtitle files."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "data"

    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        """Get all subtitle files for a specific show and season."""
        show_dir = self.cache_dir / sanitize_filename(show_name)
        if not show_dir.exists():
            return []

        subtitles = []
        # Case insensitive glob
        files = list(show_dir.glob("*.srt")) + list(show_dir.glob("*.SRT"))

        for f in files:
            info = parse_season_episode(f.name)
            if info:
                if info.season == season:
                    info.series_name = show_name
                    subtitles.append(SubtitleFile(path=f, episode_info=info))

        # Deduplicate by path
        seen = set()
        unique_subs = []
        for sub in subtitles:
            if sub.path not in seen:
                seen.add(sub.path)
                unique_subs.append(sub)

        return unique_subs


def _convert_downloads_to_subtitle_files(
    downloaded: dict[str, Path], show_name: str
) -> list[SubtitleFile]:
    """Convert a {episode_code: srt_path} mapping to SubtitleFile objects.

    Episode codes are parsed for season/episode; unparseable codes are skipped.
    """
    subtitles = []
    for episode_code, srt_path in downloaded.items():
        parsed = parse_season_episode_numbers(episode_code)
        if parsed:
            ep_season, ep_num = parsed
            subtitles.append(
                SubtitleFile(
                    path=srt_path,
                    language="en",
                    episode_info=EpisodeInfo(
                        series_name=show_name, season=ep_season, episode=ep_num
                    ),
                )
            )
    return subtitles


class Addic7edProvider(SubtitleProvider):
    """Provider that downloads subtitles from Addic7ed.com via web scraping."""

    def __init__(self):
        cm = get_config_manager()
        self.config = cm.load()

    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        """Download subtitles for a show/season from Addic7ed."""
        try:
            from app.matcher.addic7ed_client import get_subtitles_addic7ed
        except ImportError as e:
            logger.error(f"Addic7ed client not available: {e}")
            return []

        logger.info(f"Downloading subtitles from Addic7ed for {show_name} S{season:02d}")

        try:
            downloaded = get_subtitles_addic7ed(
                show_name=show_name,
                seasons={season},
                cache_dir=self.config.cache_dir,
                max_retries=2,
            )

            subtitles = _convert_downloads_to_subtitle_files(downloaded, show_name)
            logger.info(f"Downloaded {len(subtitles)} subtitles from Addic7ed")
            return subtitles

        except Exception as e:
            logger.error(f"Addic7ed download failed: {e}")
            return []


class OpenSubtitlesProvider(SubtitleProvider):
    """Provider that downloads subtitles using OpenSubtitles.com."""

    def __init__(self):
        cm = get_config_manager()
        self.config = cm.load()
        self.client = None
        self.network_timeout = 30  # seconds
        self._authenticate()

    def _authenticate(self):
        # OpenSubtitles API fields were removed in v0.2.0 — this provider
        # is retained for backwards compatibility but will no-op without keys.
        api_key = getattr(self.config, "open_subtitles_api_key", None)
        if not api_key:
            logger.warning("OpenSubtitles API key not configured — provider disabled")
            return

        try:
            try:
                from opensubtitlescom import OpenSubtitles as OpenSubtitlesClient
            except ImportError:
                logger.warning(
                    "opensubtitlescom package not installed - OpenSubtitles API provider disabled"
                )
                self.client = None
                return

            # OS best-practices: User-Agent must be "AppName vX.Y.Z". The
            # prior "Oz 1.0.0" placeholder was a leftover from upstream and
            # silently misidentified the app. Override via config only if a
            # deployment has registered a custom UA with OpenSubtitles.
            user_agent = getattr(self.config, "open_subtitles_user_agent", None) or _USER_AGENT
            self.client = OpenSubtitlesClient(user_agent, api_key)
            username = getattr(self.config, "open_subtitles_username", None)
            password = getattr(self.config, "open_subtitles_password", None)
            if username and password:
                # Route through os_api_call so a transient 429 here gets the
                # same retry treatment as login/search/download elsewhere —
                # otherwise a single rate-limit response silently disables
                # the entire provider for the rest of the job.
                os_api_call(self.client.login, username, password)
                logger.debug("Logged in to OpenSubtitles")
            else:
                logger.debug("Initialized OpenSubtitles (no login)")
        except Exception as e:
            # CLAUDE.md: always log with exc_info=True inside except blocks.
            # A bare str(e) hides AttributeError / ImportError surprises that
            # may sneak through ``os_api_call``'s retry loop.
            logger.error(f"Failed to initialize OpenSubtitles: {e}", exc_info=True)
            self.client = None

    def _search_with_retry(
        self,
        query: str | None = None,
        languages: str = "en",
        parent_tmdb_id: int | None = None,
        season_number: int | None = None,
        type: str | None = None,
    ):
        """Search for subtitles with 429-aware retry."""
        if not self.client:
            raise RuntimeError("OpenSubtitles client not initialized")

        # Retry cadence: 4 attempts with 1s base delay → 3 sleeps of
        # 1s, 2s, 4s (the 4th attempt re-raises without sleeping). Tight
        # schedule — sufficient for transient network errors; a genuine
        # 429 still gets four chances.
        with _alarm_timeout(self.network_timeout, "Search"):
            return os_api_call(
                self.client.search,
                query=query,
                languages=languages,
                parent_tmdb_id=parent_tmdb_id,
                season_number=season_number,
                type=type,
                max_attempts=4,
                base_delay=1.0,
            )

    def _download_with_retry(self, subtitle):
        """Download subtitle file with 429-aware retry.

        Uses 6 attempts with 3s base delay → 5 sleeps of 3s, 6s, 12s, 24s, 48s
        (the 6th attempt re-raises without sleeping). Generous because the
        OpenSubtitles download quota check can be flaky and we'd rather wait
        than burn the daily quota on transient failures.
        """
        if not self.client:
            raise RuntimeError("OpenSubtitles client not initialized")

        with _alarm_timeout(self.network_timeout, "Download"):
            return os_api_call(
                self.client.download_and_save,
                subtitle,
                max_attempts=6,
                base_delay=3.0,
            )

    def _resolve_show_name(self, show_name: str, tmdb_id: int | None) -> str:
        """Resolve the show name to search with, preferring TMDB's name.

        Falls back to the original name on any lookup failure.
        """
        if not tmdb_id:
            return show_name

        logger.info(f"Using manual TMDB ID: {tmdb_id} for {show_name}")
        try:
            from app.matcher.tmdb_client import fetch_show_details

            show_data = fetch_show_details(tmdb_id)
            if show_data:
                resolved = show_data.get("name", show_name)
                logger.info(f"TMDB lookup: Using '{resolved}' instead of '{show_name}'")
                return resolved
            logger.warning(f"Failed to lookup TMDB ID {tmdb_id}")
        except Exception as e:
            logger.error(f"Error looking up TMDB ID {tmdb_id}: {e}")
        return show_name

    @staticmethod
    def _extract_episode_number(subtitle, season: int) -> tuple[int | None, str | None]:
        """Determine the episode number for a subtitle result.

        Prefers the API-provided season/episode metadata, falling back to
        parsing the subtitle filename.

        Returns:
            (episode_number, None) on success, or (None, skip_reason) where
            skip_reason is "season" or "parse" indicating why it was skipped.
        """
        api_season = getattr(subtitle, "season_number", None)
        api_episode = getattr(subtitle, "episode_number", None)

        # Get filename from files list or top level
        sub_filename = subtitle.file_name
        if not sub_filename and subtitle.files:
            # files is a list of dicts based on debug output
            if isinstance(subtitle.files[0], dict):
                sub_filename = subtitle.files[0].get("file_name", "")
            else:
                # Fallback if it somehow changes to object
                sub_filename = getattr(subtitle.files[0], "file_name", "")

        logger.debug(
            f"Subtitle: api_season={api_season}, api_episode={api_episode}, filename={sub_filename}"
        )

        if api_season and api_episode:
            if api_season != season:
                logger.debug(f"  Skipping: API season {api_season} != requested season {season}")
                return None, "season"
            logger.debug(f"  Using API metadata: S{api_season:02d}E{api_episode:02d}")
            return api_episode, None

        # Fallback to parsing filename
        info = parse_season_episode(sub_filename or "")
        if not info or info.season != season:
            logger.debug(
                f"  Skipping: Failed to parse or season mismatch in filename: {sub_filename}"
            )
            return None, "parse"
        logger.debug(f"  Parsed from filename: S{info.season:02d}E{info.episode:02d}")
        return info.episode, None

    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        """Get subtitles for a show/season by downloading them."""
        if not self.client:
            logger.error("OpenSubtitles client not available")
            return []

        # Check for manual TMDB ID first and get correct show name
        search_show_name = self._resolve_show_name(show_name, tmdb_id)

        logger.info(f"Searching OpenSubtitles for {search_show_name} S{season:02d}")

        # Prepare cache directory
        cache_dir = self.config.cache_dir / "data" / sanitize_filename(show_name)
        cache_dir.mkdir(parents=True, exist_ok=True)

        downloaded_subtitles = []

        try:
            # Search by TMDB ID if available, otherwise fall back to query search
            if tmdb_id:
                logger.debug(
                    f"Searching OpenSubtitles by parent_tmdb_id={tmdb_id}, season={season}"
                )
                response = self._search_with_retry(
                    query=None,
                    parent_tmdb_id=tmdb_id,
                    season_number=season,
                    type="episode",
                )
            else:
                # Fallback to query-based search
                query = f"{search_show_name} S{season:02d}"
                logger.debug(f"Searching OpenSubtitles by query: {query}")
                response = self._search_with_retry(query=query, type="episode")

            if not response.data:
                search_desc = (
                    f"TMDB ID {tmdb_id} S{season:02d}"
                    if tmdb_id
                    else f"query '{search_show_name} S{season:02d}'"
                )
                logger.warning(f"No subtitles found for {search_desc}")
                return []

            logger.info(f"Found {len(response.data)} potential subtitles")

            seen_episodes = set()
            logger.debug(f"Starting subtitle download loop for season {season}")

            subtitles_checked = 0
            subtitles_skipped_season = 0
            subtitles_skipped_parse = 0

            for subtitle in response.data:
                subtitles_checked += 1

                ep_num, skip_reason = self._extract_episode_number(subtitle, season)
                if skip_reason == "season":
                    subtitles_skipped_season += 1
                    continue
                if skip_reason == "parse":
                    subtitles_skipped_parse += 1
                    continue

                if ep_num in seen_episodes:
                    continue

                # Download with retry
                try:
                    logger.info(f"Downloading subtitle for S{season:02d}E{ep_num:02d}")
                    srt_file = self._download_with_retry(subtitle)

                    # Move to cache
                    target_name = f"{show_name} - S{season:02d}E{ep_num:02d}.srt"
                    target_path = cache_dir / target_name

                    shutil.move(srt_file, target_path)

                    downloaded_subtitles.append(
                        SubtitleFile(
                            path=target_path,
                            language="en",
                            episode_info=EpisodeInfo(
                                series_name=show_name, season=season, episode=ep_num
                            ),
                        )
                    )
                    seen_episodes.add(ep_num)

                except Exception as e:
                    logger.error(f"Failed to download/save subtitle: {e}")

            logger.debug(
                f"Subtitle download loop complete: checked={subtitles_checked}, "
                f"skipped_season={subtitles_skipped_season}, skipped_parse={subtitles_skipped_parse}, "
                f"downloaded={len(downloaded_subtitles)}"
            )
            return downloaded_subtitles

        except Exception as e:
            logger.error(f"OpenSubtitles search failed: {e}")
            return []


class CompositeSubtitleProvider(SubtitleProvider):
    def __init__(self, providers: list[SubtitleProvider]):
        self.providers = providers

    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        results = []

        # Try each provider in order, but prioritize cached results
        for i, provider in enumerate(self.providers):
            provider_results = provider.get_subtitles(show_name, season, video_files, tmdb_id)

            # If this is the local provider and we have results, prefer them
            if isinstance(provider, LocalSubtitleProvider) and provider_results:
                logger.info(
                    f"Found {len(provider_results)} cached subtitles for {show_name} S{season:02d}"
                )
                results.extend(provider_results)
                # Return early if we have enough cached subtitles
                if len(provider_results) >= _MIN_CACHED_SUBTITLES_TO_SKIP_DOWNLOAD:
                    logger.info("Using cached subtitles, skipping download")
                    return results
            else:
                # For non-local providers, only use if we don't have cached results
                if not results:
                    logger.info(f"No cached subtitles found, trying provider {i + 1}")
                    results.extend(provider_results)
                else:
                    logger.info(
                        "Skipping additional providers since cached subtitles are available"
                    )
                    break

        return results
