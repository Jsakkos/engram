"""Shared service layer for standalone testing of subtitle download, transcription, and matching.

Provides three independent operations that can be called from CLI or API:
1. download_subtitles - Download SRT files for a show/season via Addic7ed + TMDB
2. transcribe_chunk - Extract audio from an MKV and transcribe with Whisper
3. match_episodes - Match MKV file(s) against cached subtitles
"""

import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app import __version__
from app.matcher.addic7ed_client import Addic7edClient
from app.matcher.asr_provider import get_asr_provider
from app.matcher.os_api_retry import _RETRYABLE_EXCEPTIONS, os_api_call
from app.matcher.provider_scheduler import EpisodeJob, run_jobs
from app.matcher.srt_utils import extract_audio_chunk, get_video_duration
from app.matcher.subtitle_provider import LocalSubtitleProvider
from app.matcher.subtitle_utils import is_valid_srt_file, sanitize_filename
from app.matcher.tmdb_client import fetch_season_details, fetch_show_details, fetch_show_id
from app.matcher.tvsubtitles_client import TVSubtitlesClient

# OpenSubtitles best-practices require the User-Agent be in the form
# "AppName vX.Y.Z". A bare "Engram" (or worse, the upstream library default)
# misidentifies us to OS and risks being lumped in with unidentified clients
# for rate-limit purposes. __version__ is sourced from app/__init__.py.
_USER_AGENT = f"Engram v{__version__}"


# --- Cached OpenSubtitles API client + quota state -------------------------
# The OpenSubtitles bearer token is valid ~24h and is meant to be reused.
# `download_subtitles()` is called once per season; logging in each time
# hammers the `/login` endpoint (throttled harder than data endpoints) and
# triggers 429s long before the daily download quota is touched. We log in
# once per process and reuse the client. Quota tracking lives alongside
# because the library updates ``client.user_downloads_remaining`` as a side
# effect of ``login()``, ``user_info()``, and ``download()`` — reading the
# attribute is free, no extra API call.
#
# All this state lives on a single ``_OS`` dataclass instance instead of
# loose module globals. ``global`` declarations confuse static analyzers
# (CodeQL flagged the writes as "unused global variable" because it does
# not track read-then-write-across-calls through the ``global`` keyword);
# attribute access on a single object reads as a plain "is referenced" and
# also reduces top-of-module noise.
_OS_TOKEN_MAX_AGE: float = 12 * 60 * 60  # re-login after 12h, well within 24h


@dataclass
class _OSState:
    """Process-wide OpenSubtitles API client + quota state."""

    client: object | None = None
    login_time: float = 0.0
    failed: bool = False
    last_quota: dict | None = None
    last_logged_remaining: int | None = None


_OS = _OSState()
# Guards _get_os_client's check-then-login window. Two coroutines on the
# FastAPI side (or two threads via asyncio.to_thread) can otherwise both
# observe _OS.client is None and both call client.login(), consuming two
# /login quota slots and racing on the assignment to _OS.client.
# threading.Lock works in both sync and asyncio.to_thread contexts; we
# don't need an asyncio.Lock because the login itself is sync.
_OS_LOGIN_LOCK = threading.Lock()


def _snapshot_os_quota(client) -> None:
    """Read ``client.user_downloads_remaining`` and stash it for later display.

    Called after each season's API download block. Non-fatal on any error —
    the quota counter is informational only.
    """
    try:
        remaining = getattr(client, "user_downloads_remaining", None)
        if remaining is None:
            return
        remaining_int = int(remaining)
        _OS.last_quota = {"remaining": remaining_int, "as_of": time.monotonic()}
        # Log on first read, on a drop of >= 10 from the last LOGGED value
        # (not the previous snapshot — slow drips of 5-per-season would
        # never cross a snapshot-to-snapshot threshold), AND on any refill.
        # The refill branch matters at midnight: when the daily quota
        # resets (e.g. 50 -> 1000), the "drop" check sees 50 - 1000 = -950,
        # never >= 10, so without it the log line would go silent for the
        # rest of the run even though the counter is healthy.
        if (
            _OS.last_logged_remaining is None
            or _OS.last_logged_remaining - remaining_int >= 10
            or remaining_int > _OS.last_logged_remaining
        ):
            logger.info(f"OS API quota: {remaining_int} downloads remaining today")
            _OS.last_logged_remaining = remaining_int
    except Exception as exc:
        # exc_info=True per CLAUDE.md. The quota path is best-effort, so
        # this stays at DEBUG (won't spam production logs), but if a
        # programming error sneaks in (e.g., an unexpected client shape →
        # AttributeError) the traceback is the only thing that lets us
        # diagnose it.
        logger.debug(f"Could not snapshot OS quota (non-fatal): {exc}", exc_info=True)


def get_last_quota() -> dict | None:
    """Public read-only accessor for the most recent OS download-quota snapshot.

    Returns a dict ``{"remaining": int, "as_of": float}`` or None if no API
    call has succeeded yet this process. Used by the build script's final
    summary so the user sees "downloads remaining today" at the end of a run.
    """
    return _OS.last_quota


def probe_os_quota(config) -> int | None:
    """Log in (cached) and return remaining daily OpenSubtitles downloads.

    Public wrapper over ``_get_os_client`` for callers that only want to
    display quota up front (e.g. the cache build script's startup banner).
    Returns the remaining download count, or None when OpenSubtitles is
    unavailable — missing package/credentials, login failure, or quota
    already exhausted. The login + ``/infos/user`` probe does NOT consume
    download quota, and this is best-effort: it never raises.
    """
    if _get_os_client(config) is None:
        return None
    quota = get_last_quota()
    return quota.get("remaining") if quota else None


def _get_os_client(config) -> object | None:
    """Return a logged-in OpenSubtitles client, cached for the process.

    Logs in once (with 429-aware backoff) and reuses the token across all
    seasons/shows. Returns None on persistent failure so callers fall back to
    scrapers. Thread-safe via ``_OS_LOGIN_LOCK``: concurrent callers wait on
    the lock and observe the resulting client on the second check.
    """
    # Fast path (no lock): a logged-in client with a fresh token.
    if _OS.failed:
        return None
    if _OS.client is not None and (time.monotonic() - _OS.login_time) < _OS_TOKEN_MAX_AGE:
        return _OS.client

    with _OS_LOGIN_LOCK:
        # Double-check after acquiring the lock — another thread may have
        # completed the login (or flipped `failed`) while we were waiting.
        if _OS.failed:
            return None
        if _OS.client is not None and (time.monotonic() - _OS.login_time) < _OS_TOKEN_MAX_AGE:
            return _OS.client

        try:
            from opensubtitlescom import OpenSubtitles as _OSApi
        except ImportError:
            logger.warning("opensubtitlescom package not installed — skipping API path")
            _OS.failed = True
            return None

        # Construct AND login inside the same try so a malformed config
        # (e.g., missing opensubtitles_api_key attribute → AttributeError)
        # is caught and gracefully degraded to scrapers, matching the
        # original pre-refactor contract. Constructing outside the try
        # would propagate that AttributeError to the caller unhandled.
        try:
            client = _OSApi(_USER_AGENT, config.opensubtitles_api_key)
            os_api_call(
                client.login,
                config.opensubtitles_username,
                config.opensubtitles_password,
            )
        except Exception as e:
            logger.warning(
                f"OpenSubtitles API login failed after retries ({e}); "
                "using scrapers for the rest of this run",
                exc_info=True,
            )
            _OS.failed = True
            return None

        # The login response only carries ``allowed_downloads`` — the daily
        # CAP (e.g. 1000 for VIP), NOT how many remain. Trusting it makes the
        # build believe quota is full when it may already be exhausted, then
        # 406 ("quota exceeded") on every per-season download while logging a
        # reassuring "1000 remaining". One ``/infos/user`` call up front (it
        # does NOT consume download quota) yields the true ``remaining_downloads``
        # and, as a side effect, updates ``client.user_downloads_remaining``.
        try:
            os_api_call(client.user_info)
        except _RETRYABLE_EXCEPTIONS as e:
            # Non-fatal: if the probe itself fails we proceed with whatever
            # the library seeded (the cap) rather than blocking the run.
            logger.debug(f"OS user-info probe failed (non-fatal): {e}", exc_info=True)
        remaining = getattr(client, "user_downloads_remaining", None)

        if remaining is not None and remaining <= 0:
            # Quota is spent for today. Skip OpenSubtitles for the rest of the
            # run instead of paying a search + 406 + retry on every season —
            # the daily bucket won't refill for hours. Falls straight through
            # to the scrapers (Addic7ed / TVsubtitles).
            logger.warning(
                f"OpenSubtitles API: daily download quota exhausted "
                f"({remaining} remaining) — skipping OpenSubtitles for this run; "
                "falling back to scrapers (Addic7ed/TVsubtitles)"
            )
            _OS.failed = True
            _snapshot_os_quota(client)
            return None

        if remaining is not None:
            logger.info(f"OpenSubtitles API login OK — {remaining} downloads remaining today")
        else:
            logger.info("OpenSubtitles API login OK")
        _OS.client = client
        _OS.login_time = time.monotonic()
        # Seed the quota snapshot from the (now accurate) client attribute —
        # gives the build script's final summary a starting baseline even if
        # no downloads happen this run (e.g., the whole cache is already populated).
        _snapshot_os_quota(client)
        return client


def _precomputed_skip_result(cache_path: Path, show_name: str, season: int) -> dict | None:
    """Build a 'skip download' result when the precomputed cache covers the season.

    Returns None when the cache doesn't cover ``show_name`` S``season``. The result
    is sized from the cache's own episode index (no TMDB call), so it works even
    when TMDB is unreachable — the whole point of the precomputed cache.
    """
    from app.matcher.episode_identification import precomputed_episode_codes

    codes = precomputed_episode_codes(cache_path, show_name, season)
    if not codes:
        return None

    logger.info(
        f"{show_name} S{season:02d}: covered by precomputed vector cache; "
        f"skipping subtitle download"
    )
    series_cache_dir = cache_path / "data" / sanitize_filename(show_name)
    return {
        "show_name": show_name,
        "season": season,
        "total_episodes": len(codes),
        "episodes": [
            {"code": code, "status": "precomputed", "source": "precomputed"} for code in codes
        ],
        "cache_dir": str(series_cache_dir),
    }


def download_subtitles(show_name: str, season: int, *, use_precomputed: bool = True) -> dict:
    """Download SRT subtitle files for a show/season.

    Strategy:
    1. Bulk-fetch the season via the OpenSubtitles.com REST API when credentials
       are configured (fast path).
    2. For episodes still missing, fan out across the threaded provider scheduler
       (Addic7ed + TVsubtitles) so providers' rate-limit cooldowns
       overlap instead of serializing.

    Args:
        show_name: Name of the TV show (e.g. "Breaking Bad")
        season: Season number
        use_precomputed: When True (default), skip downloading entirely if the
            precomputed vector cache already covers this show+season — matching
            reads those vectors directly, so the SRTs would be unused. The cache
            builder passes False so rebuilds always re-harvest.

    Returns:
        Dict with show_name, season, total_episodes, episodes list, and cache_dir.
        Each episode dict includes 'source' field: "precomputed", "cache",
        "opensubtitles_api", "addic7ed", "tvsubtitles", or None.
    """
    # Resolve the cache path up front so the precomputed fast path needs no network.
    from app.services.config_service import get_config_sync

    config = get_config_sync()
    cache_path = Path(config.subtitles_cache_path).expanduser()
    if not cache_path.is_absolute():
        cache_path = Path(__file__).parent.parent.parent / config.subtitles_cache_path

    # Fast path (no network): the precomputed vector cache already covers this
    # season. Tried BEFORE any TMDB call so an offline or failed TMDB lookup can't
    # fail a job the cache would have matched (matching reads the vectors directly
    # and ignores SRTs). The raw name is tried first, mirroring the matcher's own
    # offline fallback — manifest keys are canonical names, so a hit means this
    # name *is* the canonical key the cache was built under.
    if use_precomputed:
        skip = _precomputed_skip_result(cache_path, show_name, season)
        if skip is not None:
            return skip

    # Get TMDB show ID to determine episode count
    show_id = fetch_show_id(show_name)
    if not show_id:
        raise ValueError(f"Could not find show '{show_name}' on TMDB")

    # Fetch canonical details to get the correct show name (e.g., "Southpark6" -> "South Park")
    show_details = fetch_show_details(show_id)
    canonical_show_name = show_details.get("name") if show_details else show_name

    if canonical_show_name != show_name:
        logger.info(f"Using canonical show name '{canonical_show_name}' instead of '{show_name}'")
        # Retry the precomputed fast path under the canonical name, for discs whose
        # label differs from the cache's canonical key.
        if use_precomputed:
            skip = _precomputed_skip_result(cache_path, canonical_show_name, season)
            if skip is not None:
                return skip

    episode_count = fetch_season_details(show_id, season)
    if episode_count == 0:
        raise ValueError(f"No episodes found for {canonical_show_name} Season {season} on TMDB")

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

                # Route search/download through os_api_call so a transient
                # 429 anywhere in the bulk-download path retries with the
                # same backoff used by subtitle_provider.py — without this
                # wrapping, the 12+ hour build script would fall through to
                # the legacy scrapers on the very first rate-limit response
                # at any of ~1800 season call sites.
                response = os_api_call(
                    _os_client.search,
                    parent_tmdb_id=show_id,
                    season_number=season,
                    languages="en",
                    type="episode",
                    max_attempts=4,
                    base_delay=1.0,
                )
                seen_api_eps: set[int] = set()
                for subtitle in response.data or []:
                    ep_num = getattr(subtitle, "episode_number", None)
                    api_ep_season = getattr(subtitle, "season_number", None)
                    if ep_num and api_ep_season == season and ep_num not in seen_api_eps:
                        episode_code_api = f"S{season:02d}E{ep_num:02d}"
                        srt_target = series_cache_dir / f"{safe_show_name} - {episode_code_api}.srt"
                        if not srt_target.exists():
                            # Shorter cadence on download than on search — a
                            # 429 on download usually means the daily quota
                            # is exhausted (not the per-minute limit), and
                            # retrying same-day for 90s+ doesn't help; we'd
                            # rather fall back to scrapers and move on.
                            srt_file = os_api_call(
                                _os_client.download_and_save,
                                subtitle,
                                max_attempts=2,
                                base_delay=5.0,
                            )
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
                logger.warning(
                    f"OpenSubtitles API failed ({e}), falling back to scrapers",
                    exc_info=True,
                )

    # Per-episode triage: separate cache hits + API hits from the residual
    # work the scheduler will fan out across scrapers.
    from app.matcher.subtitle_utils import find_existing_subtitle

    pre_resolved: dict[int, dict] = {}
    residual_jobs: list[EpisodeJob] = []

    for episode in range(1, episode_count + 1):
        episode_code = f"S{season:02d}E{episode:02d}"
        srt_path = series_cache_dir / f"{safe_show_name} - {episode_code}.srt"

        existing_subtitle = find_existing_subtitle(
            str(series_cache_dir), safe_show_name, season, episode
        )
        if existing_subtitle:
            if is_valid_srt_file(existing_subtitle):
                pre_resolved[episode] = {
                    "code": episode_code,
                    "status": "cached",
                    "path": str(existing_subtitle),
                    "source": "cache",
                }
                continue
            logger.warning(
                f"Cached file {existing_subtitle.name} is invalid (HTML?), "
                "deleting and re-downloading"
            )
            existing_subtitle.unlink(missing_ok=True)

        if episode in api_srt_map:
            pre_resolved[episode] = {
                "code": episode_code,
                "status": "downloaded",
                "path": str(api_srt_map[episode]),
                "source": "opensubtitles_api",
            }
            continue

        residual_jobs.append(
            EpisodeJob(
                tmdb_id=int(show_id),
                show_name=canonical_show_name,
                season=season,
                episode=episode,
                episode_code=episode_code,
                srt_target=srt_path,
                pending_providers=deque(["addic7ed", "tvsubtitles"]),
            )
        )

    # Fan out the residual work across provider workers. While Addic7ed
    # sits in its 3s cooldown, the TVsubtitles worker can be mid-flight on
    # a different episode — total wall-time falls from the sum of
    # per-provider times toward their max.
    scheduler_results: dict[str, dict] = {}
    if residual_jobs:
        workers = {
            "addic7ed": Addic7edClient(),
            "tvsubtitles": TVSubtitlesClient(),
        }
        scheduler_results = run_jobs(residual_jobs, workers)

    # Re-assemble episode results in episode order.
    episodes = []
    for episode in range(1, episode_count + 1):
        episode_code = f"S{season:02d}E{episode:02d}"
        if episode in pre_resolved:
            episodes.append(pre_resolved[episode])
        else:
            episodes.append(
                scheduler_results.get(
                    episode_code,
                    {
                        "code": episode_code,
                        "status": "not_found",
                        "path": None,
                        "source": None,
                    },
                )
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
