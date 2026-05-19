"""Shared service layer for standalone testing of subtitle download, transcription, and matching.

Provides three independent operations that can be called from CLI or API:
1. download_subtitles - Download SRT files for a show/season via Addic7ed + TMDB
2. transcribe_chunk - Extract audio from an MKV and transcribe with Whisper
3. match_episodes - Match MKV file(s) against cached subtitles
"""

import tempfile
import time
from pathlib import Path

from loguru import logger

from app import __version__
from app.matcher.addic7ed_client import Addic7edClient
from app.matcher.asr_provider import get_asr_provider
from app.matcher.opensubtitles_scraper import OpenSubtitlesClient
from app.matcher.srt_utils import extract_audio_chunk, get_video_duration
from app.matcher.subtitle_provider import LocalSubtitleProvider
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.tmdb_client import fetch_season_details, fetch_show_details, fetch_show_id

# OpenSubtitles best-practices require the User-Agent be in the form
# "AppName vX.Y.Z". A bare "Engram" (or worse, the upstream library default)
# misidentifies us to OS and risks being lumped in with unidentified clients
# for rate-limit purposes. __version__ is sourced from app/__init__.py.
_USER_AGENT = f"Engram v{__version__}"


def is_valid_srt_file(file_path: Path) -> bool:
    """
    Validate that a file is a real SRT subtitle file, not HTML or other garbage.

    Checks:
    1. File exists and is not empty
    2. First 500 bytes don't contain HTML markers
    3. Contains SRT timestamp format (00:00:00,000 --> 00:00:00,000)

    Returns:
        bool: True if valid SRT file, False otherwise
    """
    try:
        if not file_path.exists() or file_path.stat().st_size < 50:
            return False

        # Read first 500 bytes to check format
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            header = f.read(500).lower()

        # Check for HTML markers
        if any(marker in header for marker in ["<!doctype", "<html", "<head", "<body", "<div"]):
            logger.warning(f"Rejecting {file_path.name}: appears to be HTML, not SRT")
            return False

        # Check for SRT timestamp format
        if "-->" not in header:
            logger.warning(f"Rejecting {file_path.name}: no SRT timestamp markers found")
            return False

        return True

    except Exception as e:
        logger.warning(f"Error validating {file_path}: {e}")
        return False


# --- Cached OpenSubtitles API client ---------------------------------------
# The OpenSubtitles bearer token is valid ~24h and is meant to be reused.
# `download_subtitles()` is called once per season; logging in each time
# hammers the `/login` endpoint (throttled harder than data endpoints) and
# triggers 429s long before the daily download quota is touched. We log in
# once per process and reuse the client.
_OS_CLIENT: object | None = None
_OS_CLIENT_LOGIN_TIME: float = 0.0
_OS_CLIENT_FAILED: bool = False
_OS_TOKEN_MAX_AGE: float = 12 * 60 * 60  # re-login after 12h, well within 24h

# --- Daily download quota tracking ------------------------------------------
# The opensubtitlescom library updates ``client.user_downloads_remaining`` as
# a side effect of ``login()``, ``user_info()``, and ``download()`` (see
# .venv/.../opensubtitlescom/opensubtitles.py:105,124,305). Reading the
# attribute is free — no extra API call — so we snapshot it after each season
# bulk-download and surface the latest value via ``get_last_quota()`` for the
# build script's final summary.
_OS_LAST_QUOTA: dict | None = None
_OS_LAST_LOGGED_REMAINING: int | None = None


def _snapshot_os_quota(client) -> None:
    """Read ``client.user_downloads_remaining`` and stash it for later display.

    Called after each season's API download block. Non-fatal on any error —
    the quota counter is informational only.
    """
    global _OS_LAST_QUOTA, _OS_LAST_LOGGED_REMAINING
    try:
        remaining = getattr(client, "user_downloads_remaining", None)
        if remaining is None:
            return
        remaining_int = int(remaining)
        _OS_LAST_QUOTA = {"remaining": remaining_int, "as_of": time.monotonic()}
        # Log on first read and whenever quota has dropped by >= 10 from the
        # *last logged value* (not the previous snapshot). Comparing against
        # the previous snapshot would silently swallow slow drips: dropping
        # 5-per-season for 20 seasons would never cross the threshold from
        # snapshot to snapshot.
        if _OS_LAST_LOGGED_REMAINING is None or _OS_LAST_LOGGED_REMAINING - remaining_int >= 10:
            logger.info(f"OS API quota: {remaining_int} downloads remaining today")
            _OS_LAST_LOGGED_REMAINING = remaining_int
    except Exception as exc:
        logger.debug(f"Could not snapshot OS quota (non-fatal): {exc}")


def get_last_quota() -> dict | None:
    """Public read-only accessor for the most recent OS download-quota snapshot.

    Returns a dict ``{"remaining": int, "as_of": float}`` or None if no API
    call has succeeded yet this process. Used by the build script's final
    summary so the user sees "downloads remaining today" at the end of a run.
    """
    return _OS_LAST_QUOTA


def _get_os_client(config) -> object | None:
    """Return a logged-in OpenSubtitles client, cached for the process.

    Logs in once (with 429-aware backoff) and reuses the token across all
    seasons/shows. Returns None on persistent failure so callers fall back to
    scrapers.
    """
    global _OS_CLIENT, _OS_CLIENT_LOGIN_TIME, _OS_CLIENT_FAILED

    if _OS_CLIENT_FAILED:
        return None

    if _OS_CLIENT is not None and (time.monotonic() - _OS_CLIENT_LOGIN_TIME) < _OS_TOKEN_MAX_AGE:
        return _OS_CLIENT

    try:
        from opensubtitlescom import OpenSubtitles as _OSApi
    except ImportError:
        logger.warning("opensubtitlescom package not installed — skipping API path")
        _OS_CLIENT_FAILED = True
        return None

    from app.matcher.os_api_retry import os_api_call

    client = _OSApi(_USER_AGENT, config.opensubtitles_api_key)
    try:
        login_response = os_api_call(
            client.login,
            config.opensubtitles_username,
            config.opensubtitles_password,
        )
    except Exception as e:
        logger.warning(
            f"OpenSubtitles API login failed after retries ({e}); "
            "using scrapers for the rest of this run"
        )
        _OS_CLIENT_FAILED = True
        return None

    try:
        remaining = login_response["user"]["allowed_downloads"]
        logger.info(f"OpenSubtitles API login OK — {remaining} downloads remaining today")
    except (KeyError, TypeError):
        logger.info("OpenSubtitles API login OK")
    _OS_CLIENT = client
    _OS_CLIENT_LOGIN_TIME = time.monotonic()
    # Seed the quota snapshot from the login response — gives the build
    # script's final summary a starting baseline even if no downloads happen
    # this run (e.g., the whole cache is already populated).
    _snapshot_os_quota(client)
    return client


def download_subtitles(show_name: str, season: int) -> dict:
    """Download SRT subtitle files for a show/season using both Addic7ed and OpenSubtitles.

    Strategy:
    1. Try Addic7ed first (faster, direct .srt downloads)
    2. For episodes not found on Addic7ed, try OpenSubtitles
    3. Track which scraper was used for each episode

    Args:
        show_name: Name of the TV show (e.g. "Breaking Bad")
        season: Season number

    Returns:
        Dict with show_name, season, total_episodes, episodes list, and cache_dir.
        Each episode dict includes 'source' field: "cache", "addic7ed", "opensubtitles", or None.
    """
    # Get TMDB show ID to determine episode count
    show_id = fetch_show_id(show_name)
    if not show_id:
        raise ValueError(f"Could not find show '{show_name}' on TMDB")

    # Fetch canonical details to get the correct show name (e.g., "Southpark6" -> "South Park")
    show_details = fetch_show_details(show_id)
    canonical_show_name = show_details.get("name") if show_details else show_name

    if canonical_show_name != show_name:
        logger.info(f"Using canonical show name '{canonical_show_name}' instead of '{show_name}'")

    episode_count = fetch_season_details(show_id, season)
    if episode_count == 0:
        raise ValueError(f"No episodes found for {canonical_show_name} Season {season} on TMDB")

    # Set up cache directory
    from app.services.config_service import get_config_sync

    config = get_config_sync()

    # Use config.subtitles_cache_path from DB
    cache_path = Path(config.subtitles_cache_path).expanduser()
    if not cache_path.is_absolute():
        cache_path = Path(__file__).parent.parent.parent / config.subtitles_cache_path

    # Use canonical name for cache directory
    safe_show_name = sanitize_filename(canonical_show_name)
    series_cache_dir = cache_path / "data" / safe_show_name
    series_cache_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading subtitles for '{canonical_show_name}' to: {series_cache_dir}")

    # --- OpenSubtitles.com REST API (preferred when credentials are configured) ---
    # Pre-download the whole season at once; falls back to scrapers per-episode on failure.
    api_srt_map: dict[int, Path] = {}

    # Skip the API entirely if every episode for this season is already cached on
    # disk — otherwise the unconditional `search()` below burns API rate limit on
    # resumed runs even when there's nothing left to download.
    from app.matcher.subtitle_utils import find_existing_subtitle

    cached_count = sum(
        1
        for ep in range(1, episode_count + 1)
        if find_existing_subtitle(str(series_cache_dir), safe_show_name, season, ep)
    )
    season_fully_cached = cached_count >= episode_count

    if season_fully_cached:
        logger.info(
            f"{canonical_show_name} S{season:02d}: all {episode_count} episodes "
            f"cached; skipping API"
        )

    if not season_fully_cached and (
        config.opensubtitles_api_key
        and config.opensubtitles_username
        and config.opensubtitles_password
    ):
        _os_client = _get_os_client(config)
        if _os_client is not None:
            try:
                import shutil

                response = _os_client.search(
                    parent_tmdb_id=show_id,
                    season_number=season,
                    languages="en",
                    type="episode",
                )
                seen_api_eps: set[int] = set()
                for subtitle in response.data or []:
                    ep_num = getattr(subtitle, "episode_number", None)
                    api_ep_season = getattr(subtitle, "season_number", None)
                    if ep_num and api_ep_season == season and ep_num not in seen_api_eps:
                        episode_code_api = f"S{season:02d}E{ep_num:02d}"
                        srt_target = series_cache_dir / f"{safe_show_name} - {episode_code_api}.srt"
                        if not srt_target.exists():
                            srt_file = _os_client.download_and_save(subtitle)
                            if srt_file and is_valid_srt_file(Path(srt_file)):
                                shutil.move(str(srt_file), srt_target)
                                api_srt_map[ep_num] = srt_target
                                seen_api_eps.add(ep_num)
                        else:
                            api_srt_map[ep_num] = srt_target
                            seen_api_eps.add(ep_num)
                logger.info(
                    f"OpenSubtitles API: {len(api_srt_map)}/{episode_count} subtitles "
                    f"for {canonical_show_name} S{season:02d}"
                )
                # Snapshot the daily download quota — the library has updated
                # `user_downloads_remaining` for free as a side effect of the
                # download_and_save() calls above.
                _snapshot_os_quota(_os_client)
            except Exception as e:
                logger.warning(f"OpenSubtitles API failed ({e}), falling back to scrapers")

    # Initialize scraper clients (used as fallback when API is unavailable or misses episodes)
    addic7ed_client = Addic7edClient()
    opensubtitles_client = OpenSubtitlesClient()
    episodes = []

    for episode in range(1, episode_count + 1):
        episode_code = f"S{season:02d}E{episode:02d}"
        srt_path = series_cache_dir / f"{safe_show_name} - {episode_code}.srt"

        # Check cache first - look for ANY naming variant
        from app.matcher.subtitle_utils import find_existing_subtitle

        existing_subtitle = find_existing_subtitle(
            str(series_cache_dir), safe_show_name, season, episode
        )

        if existing_subtitle:
            # Validate cached file is not HTML garbage
            if is_valid_srt_file(existing_subtitle):
                episodes.append(
                    {
                        "code": episode_code,
                        "status": "cached",
                        "path": str(existing_subtitle),
                        "source": "cache",
                    }
                )
                logger.debug(
                    f"Found cached subtitle for {episode_code}: {Path(existing_subtitle).name}"
                )
                continue
            else:
                # Delete invalid cached file and re-download
                logger.warning(
                    f"Cached file {existing_subtitle.name} is invalid (HTML?), deleting and re-downloading"
                )
                existing_subtitle.unlink(missing_ok=True)

        # Use REST API result if available for this episode
        if episode in api_srt_map:
            episodes.append(
                {
                    "code": episode_code,
                    "status": "downloaded",
                    "path": str(api_srt_map[episode]),
                    "source": "opensubtitles_api",
                }
            )
            continue

        # Try Addic7ed first (faster, direct .srt downloads)
        try:
            best_sub = addic7ed_client.get_best_subtitle(canonical_show_name, season, episode)
            if best_sub:
                result = addic7ed_client.download_subtitle(best_sub, srt_path)
                if result:
                    # Validate the downloaded file
                    if is_valid_srt_file(Path(result)):
                        episodes.append(
                            {
                                "code": episode_code,
                                "status": "downloaded",
                                "path": str(result),
                                "source": "addic7ed",
                            }
                        )
                        continue
                    else:
                        # Delete invalid file
                        logger.warning(
                            f"Downloaded invalid file for {episode_code} from Addic7ed, deleting"
                        )
                        Path(result).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Addic7ed failed for {episode_code}: {e}")

        # Fallback to OpenSubtitles if Addic7ed didn't work
        try:
            best_sub = opensubtitles_client.get_best_subtitle(canonical_show_name, season, episode)
            if best_sub:
                result = opensubtitles_client.download_subtitle(best_sub, srt_path)
                if result:
                    # Validate the downloaded file
                    if is_valid_srt_file(Path(result)):
                        episodes.append(
                            {
                                "code": episode_code,
                                "status": "downloaded",
                                "path": str(result),
                                "source": "opensubtitles",
                            }
                        )
                        continue
                    else:
                        # Delete invalid file
                        logger.warning(
                            f"Downloaded invalid file for {episode_code} from OpenSubtitles, deleting"
                        )
                        Path(result).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"OpenSubtitles failed for {episode_code}: {e}")

        # Both scrapers failed
        episodes.append(
            {
                "code": episode_code,
                "status": "not_found",
                "path": None,
                "source": None,
            }
        )

    return {
        "show_name": canonical_show_name,
        "season": season,
        "total_episodes": episode_count,
        "episodes": episodes,
        "cache_dir": str(series_cache_dir),
    }


def transcribe_chunk(
    video_path: str | Path,
    start_time: float | None = None,
    duration: float = 30,
) -> dict:
    """Extract an audio chunk from a video and transcribe it with Whisper.

    Args:
        video_path: Path to the MKV/video file
        start_time: Start time in seconds (default: 50% of video duration)
        duration: Length of chunk in seconds (default: 30)

    Returns:
        Dict with video info, transcription text, segments, and language.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_duration = get_video_duration(video_path)
    if video_duration <= 0:
        raise ValueError(f"Could not determine video duration for: {video_path}")

    if start_time is None:
        start_time = video_duration * 0.50

    # Clamp start_time so the chunk doesn't exceed video length
    if start_time + duration > video_duration:
        start_time = max(0, video_duration - duration)

    # Extract audio chunk to a temp file
    temp_dir = Path(tempfile.gettempdir()) / "engram_test_chunks"
    temp_dir.mkdir(exist_ok=True, parents=True)
    chunk_path = temp_dir / f"{video_path.stem}_{start_time:.0f}.wav"

    try:
        extract_audio_chunk(video_path, start_time, duration, chunk_path)

        # Get ASR provider and transcribe directly via the underlying model
        asr = get_asr_provider()
        asr.load()

        # Access the underlying FasterWhisperModel for full transcription output
        model = asr._model
        result = model.transcribe(chunk_path)

        return {
            "video_path": str(video_path),
            "video_duration": round(video_duration, 2),
            "chunk_start": round(start_time, 2),
            "duration": duration,
            "raw_text": result.get("raw_text", ""),
            "cleaned_text": result.get("text", ""),
            "language": result.get("language", "en"),
            "segments": result.get("segments", []),
        }
    finally:
        if chunk_path.exists():
            chunk_path.unlink()


def match_episodes(
    video_paths: list[str | Path],
    show_name: str,
    season: int,
) -> list[dict]:
    """Match MKV files against cached subtitle files.

    Requires subtitles to already be downloaded in the cache directory.

    Args:
        video_paths: List of paths to MKV/video files
        show_name: Name of the TV show
        season: Season number

    Returns:
        List of dicts, one per video file, with match results and candidates.
    """
    from app.matcher.core.matcher import MultiSegmentMatcher
    from app.services.config_service import get_config_sync

    config = get_config_sync()

    # Use config.subtitles_cache_path from DB
    cache_path = Path(config.subtitles_cache_path).expanduser()
    if not cache_path.is_absolute():
        cache_path = Path(__file__).parent.parent.parent / config.subtitles_cache_path

    # RESOLVE CANONICAL NAME:
    # "Southpark6" subtitles are saved under "South Park".
    # We must resolve the name to find them.
    from app.matcher.tmdb_client import fetch_show_details, fetch_show_id

    canonical_show_name = show_name
    try:
        show_id = fetch_show_id(show_name)
        if show_id:
            details = fetch_show_details(show_id)
            if details:
                canonical_show_name = details.get("name", show_name)
                logger.info(
                    f"Resolved '{show_name}' to canonical '{canonical_show_name}' for matching"
                )
    except Exception as e:
        logger.warning(f"Failed to resolve canonical name for '{show_name}': {e}")

    safe_show_name = sanitize_filename(canonical_show_name)

    # Load cached subtitles via LocalSubtitleProvider
    provider = LocalSubtitleProvider(cache_dir=cache_path)
    reference_subs = provider.get_subtitles(safe_show_name, season)

    if not reference_subs:
        raise ValueError(
            f"No cached subtitles found for '{show_name}' season {season}. "
            f"Run subtitle download first."
        )

    # Build matcher with ASR provider
    asr = get_asr_provider()
    asr.load()
    matcher = MultiSegmentMatcher(asr_provider=asr)

    results = []
    for vp in video_paths:
        vp = Path(vp)
        if not vp.exists():
            results.append(
                {
                    "video_path": str(vp),
                    "error": "File not found",
                    "matched_episode": None,
                    "confidence": 0.0,
                    "candidates": [],
                    "subtitles_used": len(reference_subs),
                }
            )
            continue

        try:
            match_result = matcher.match(vp, reference_subs)

            if match_result:
                # Collect all candidate info by re-examining — we use the match result
                results.append(
                    {
                        "video_path": str(vp),
                        "matched_episode": match_result.episode_info.s_e_format,
                        "confidence": round(match_result.confidence, 4),
                        "series_name": match_result.episode_info.series_name or show_name,
                        "candidates": [],
                        "subtitles_used": len(reference_subs),
                    }
                )
            else:
                results.append(
                    {
                        "video_path": str(vp),
                        "matched_episode": None,
                        "confidence": 0.0,
                        "candidates": [],
                        "subtitles_used": len(reference_subs),
                    }
                )
        except Exception as e:
            logger.error(f"Matching failed for {vp}: {e}")
            results.append(
                {
                    "video_path": str(vp),
                    "error": str(e),
                    "matched_episode": None,
                    "confidence": 0.0,
                    "candidates": [],
                    "subtitles_used": len(reference_subs),
                }
            )

    return results
