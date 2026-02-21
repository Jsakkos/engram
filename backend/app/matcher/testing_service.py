"""Shared service layer for standalone testing of subtitle download, transcription, and matching.

Provides three independent operations that can be called from CLI or API:
1. download_subtitles - Download SRT files for a show/season via Addic7ed + TMDB
2. transcribe_chunk - Extract audio from an MKV and transcribe with Whisper
3. match_episodes - Match MKV file(s) against cached subtitles
"""

import tempfile
from pathlib import Path

from loguru import logger

from app.matcher.addic7ed_client import Addic7edClient

# from app.matcher.core.config_manager import get_config_manager # REMOVED
from app.matcher.core.providers.asr import get_asr_provider
from app.matcher.core.providers.subtitles import LocalSubtitleProvider
from app.matcher.core.utils import extract_audio_chunk, get_video_duration
from app.matcher.opensubtitles_scraper import OpenSubtitlesClient
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.tmdb_client import fetch_season_details, fetch_show_details, fetch_show_id


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

    # Initialize both scrapers
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
