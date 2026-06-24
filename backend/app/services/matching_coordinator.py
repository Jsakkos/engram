"""Matching Coordinator - Episode matching, subtitle download, and DiscDB assignment.

Extracted from JobManager to isolate matching concerns.
"""

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import UTC
from enum import StrEnum
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.curator import curator as episode_curator
from app.core.errors import MatchingError
from app.core.log_context import job_log_context
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.identity_prompts import prompt_kind
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import find_staging_file

logger = logging.getLogger(__name__)

# Stricter matcher parameters for the "deep re-match" conflict path: sample more
# audio chunks (vs the default 10) for more robust votes + a clearer score gap,
# and require more matched chunks before accepting (vs the default 2).
# Deep re-match depths must be lattice levels (see canonical_scan_points):
# canonical_scan_points snaps ANY requested depth to a lattice level, so a
# non-lattice constant silently realizes onto a different grid — requested !=
# realized — causing ladder dedup/exhaustion bookkeeping and pass counters to lie.
STRICT_SCAN_POINTS = 37
STRICT_MIN_VOTES = 4

# Duration pre-filter tolerances (minutes). DVD/Blu-ray episode tracks run LONGER
# than TMDB's nominal runtime: the physical track includes the "previously on"
# recap, full end credits, and "next time" preview that the broadcast-slot runtime
# figure omits. So the accept window is asymmetric — tight below an episode runtime,
# lenient above it — mirroring analyst.py's movie tolerances. A symmetric window
# centered on TMDB's underestimate wrongly rejected the season's longest real
# episodes (Gilmore Girls S01E09 "Rory's Dance": disc 49.8min vs TMDB 44min) and
# dumped them to Extras un-transcribed (bug report job 41).
EPISODE_DURATION_UNDER_TOLERANCE_MIN = 5
EPISODE_DURATION_OVER_TOLERANCE_MIN = 10


def _duration_matches_episode_runtime(title_minutes: float, runtimes: list[int]) -> bool:
    """True if a track's duration is plausibly an episode for this season.

    Asymmetric window: a track may fall up to ``UNDER`` minutes short of an episode
    runtime or run up to ``OVER`` minutes past it (DVD recap + credits padding).
    """
    return any(
        (rt - EPISODE_DURATION_UNDER_TOLERANCE_MIN)
        <= title_minutes
        <= (rt + EPISODE_DURATION_OVER_TOLERANCE_MIN)
        for rt in runtimes
    )


# ASR-preferred episode precedence: ASR always runs and is authoritative at or
# above this confidence. Only below it do we defer to a DiscDB episode mapping —
# DiscDB numbers episodes by physical disc order, not aired order, so it is a
# last-resort fallback, never a competitor to a usable ASR match.
DISCDB_FALLBACK_ASR_FLOOR = 0.5

# Season component of a matched_episode code ("S03E07" → 3). Used via
# ``.match`` — PREFIX-anchored only: non-episode values like "extra"/None don't
# parse and are ignored by the season-pin convergence rule, but a string that
# merely STARTS with an episode code would (matched_episode is matcher-emitted,
# so that's fine in practice). Tolerant of zero-padding, same as
# _same_episode_code below and finalization_coordinator's _EP_CODE_RE.
_SEASON_FROM_EP_CODE_RE = re.compile(r"[Ss](\d{1,3})[Ee]\d{1,3}")


def _same_episode_code(a: str | None, b: str | None) -> bool:
    """True if two codes denote the same SxxEyy, tolerant of zero-padding.

    The matcher emits canonical "S01E09" but user/discdb codes may be unpadded
    ("S1E9"), so compare the parsed (season, episode) integers rather than strings.
    """
    if not a or not b:
        return False
    pa = re.match(r"[Ss](\d{1,3})[Ee](\d{1,3})", a.strip())
    pb = re.match(r"[Ss](\d{1,3})[Ee](\d{1,3})", b.strip())
    if not pa or not pb:
        return a.strip().upper() == b.strip().upper()
    return (int(pa.group(1)), int(pa.group(2))) == (int(pb.group(1)), int(pb.group(2)))


# Module-level cache for fpcalc detection results.
# `detect_fpcalc()` is deterministic within a process (the binary doesn't appear
# or disappear mid-run), but each invocation runs up to 6 subprocess probes with
# 10s timeouts each. Without caching, a 22-title disc with semaphore=3 would
# trigger up to 66 parallel probe storms. `None` means "no path detected".
# Sentinel value `_UNSET` distinguishes "haven't tried yet" from "tried and got None".
_UNSET: object = object()
_fpcalc_path_cache: str | None | object = _UNSET
_ffmpeg_path_cache: str | None | object = _UNSET


async def _resolve_fpcalc_path(cfg_path: str | None) -> str | None:
    """Return the fpcalc binary path, preferring explicit config over auto-detect.

    Auto-detect results are cached at module level so a multi-title rip doesn't
    re-run the (blocking, multi-subprocess) probe for every title.
    """
    if cfg_path:
        return cfg_path
    global _fpcalc_path_cache
    if _fpcalc_path_cache is not _UNSET:
        return _fpcalc_path_cache  # type: ignore[return-value]
    from app.api.validation import detect_fpcalc

    detected = await asyncio.to_thread(detect_fpcalc)
    _fpcalc_path_cache = detected.path if detected.found else None
    return _fpcalc_path_cache  # type: ignore[return-value]


async def _resolve_ffmpeg_path(cfg_path: str | None) -> str | None:
    """Return the ffmpeg binary path (backs the chromaprint pre-decode fallback).

    Prefers explicit config over auto-detect; caches the detect result at module
    level so a multi-title rip doesn't re-probe per title (same rationale as
    `_resolve_fpcalc_path`).
    """
    if cfg_path:
        return cfg_path
    global _ffmpeg_path_cache
    if _ffmpeg_path_cache is not _UNSET:
        return _ffmpeg_path_cache  # type: ignore[return-value]
    from app.api.validation import detect_ffmpeg

    detected = await asyncio.to_thread(detect_ffmpeg)
    _ffmpeg_path_cache = detected.path if detected.found else None
    # NOTE: the cache is frozen for the process lifetime. If detection misses here
    # (ffmpeg absent at first probe), a later config change that sets ffmpeg_path
    # takes effect only after a restart — same limitation as _fpcalc_path_cache.
    return _ffmpeg_path_cache  # type: ignore[return-value]


# Maps DiscTitle.match_source (the internal label, e.g. "engram", "discdb",
# "ai_llm") onto FingerprintContribution.match_source, which is constrained to
# the documented set 'engram_asr' | 'engram_discdb' | 'bootstrap' | 'user_review'.
_MATCH_SOURCE_TO_CONTRIB: dict[str, str] = {
    "engram": "engram_asr",
    "engram_chromaprint": "engram_chromaprint_corroboration",
    "discdb": "engram_discdb",
    "ai_llm": "engram_asr",
    "user": "user_review",
}


class FileWaitResult(StrEnum):
    """Outcome of waiting for a ripped title file to finalize on disk."""

    READY = "ready"  # Complete, or stable at a plausible size — safe to match.
    TRUNCATED = "truncated"  # Stable far below the scanned size — an aborted rip.
    TIMEOUT = "timeout"  # Never stabilized within the timeout budget.


# A ripped file that has stopped growing for this long is treated as final by
# the ripper even if it is smaller than the disc-scan size estimate. Comfortably
# longer than MakeMKV's longest mid-title write pause (a few seconds while it
# retries a marginal sector) but a tiny fraction of the old size-proportional
# timeout that let a truncated title wedge a job for hours.
TRUNCATED_STABLE_GRACE_SECONDS = 90.0

# Fast path: once a stable file reaches this fraction of the scanned size we
# accept it as complete without waiting out the grace window.
READY_SIZE_RATIO = 0.85

# A file that stops growing below this fraction of the scanned size is judged a
# truncated/aborted rip (e.g. an uncorrectable disc read error) rather than a
# legitimately small title.
TRUNCATED_SIZE_RATIO = 0.5

INCOMPLETE_RIP_MESSAGE = (
    "Incomplete rip: the ripped file is far smaller than the disc-scan size "
    "estimate, which usually means an uncorrectable disc read error aborted the "
    "rip. Clean the disc and re-rip this title."
)

# Automatic re-rip attempt cap (Feature C). After this many auto/manual re-rips a
# title stays in review but stops auto-triggering on reinsert (rerip_eligible=False).
RERIP_MAX_ATTEMPTS = 2

# match_details["error"] codes that mean "the rip itself failed" — these titles
# are eligible for single-track re-rip after a clean & reinsert.
RIP_FAILURE_ERROR_CODES = frozenset({"incomplete_rip", "rip_stalled"})


class MatchingCoordinator:
    """Coordinates episode matching: subtitle download, audio fingerprinting, DiscDB assignment."""

    def __init__(
        self,
        event_broadcaster: EventBroadcaster,
        state_machine: JobStateMachine,
    ) -> None:
        self._broadcaster = event_broadcaster
        self._state_machine = state_machine

        # Shared state (moved from JobManager)
        self._discdb_mappings: dict[int, list] = {}
        self._episode_runtimes: dict[int, list[int]] = {}
        # Per-job lock guarding the runtimes cache population. Collapses the
        # cold-window stampede where many titles of one job fetch runtimes at
        # once (see _episode_runtimes_for_job). defaultdict creates each lock
        # synchronously on first access, so there is no create-time race on the
        # single event loop. asyncio.Lock() binds to the loop lazily (py3.11),
        # so constructing it here (no running loop) is safe.
        self._episode_runtimes_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._subtitle_ready: dict[int, asyncio.Event] = {}
        self._subtitle_tasks: dict[int, asyncio.Task] = {}
        self._match_semaphore: asyncio.Semaphore | None = None

        # Cross-coordinator callbacks
        self._check_job_completion: callable = None
        self._note_activity: callable | None = None

    def set_callbacks(self, *, check_job_completion, note_activity=None) -> None:
        """Set cross-coordinator callbacks."""
        self._check_job_completion = check_job_completion
        self._note_activity = note_activity

    def init_semaphore(self, concurrency: int) -> None:
        """Initialize the match semaphore with the given concurrency."""
        self._match_semaphore = asyncio.Semaphore(concurrency)

    async def clear_job_caches(self, job_id: int, _state) -> None:
        """Clear per-job caches to prevent memory leaks. Called on terminal states.

        ``_state`` is unused but kept to satisfy the JobStateMachine
        ``on_terminal_state`` callback signature.
        """
        self._episode_runtimes.pop(job_id, None)
        self._episode_runtimes_locks.pop(job_id, None)
        self._discdb_mappings.pop(job_id, None)
        self._subtitle_ready.pop(job_id, None)
        task = self._subtitle_tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

    def get_discdb_mappings(self, job_id: int) -> list:
        """Get DiscDB mappings for a job."""
        return self._discdb_mappings.get(job_id, [])

    def set_discdb_mappings(self, job_id: int, mappings: list) -> None:
        """Set DiscDB mappings for a job."""
        self._discdb_mappings[job_id] = mappings

    def start_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Start background subtitle download with tracking."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles(job_id, show_name, season, tmdb_id)
        )

    def start_subtitle_download_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int], tmdb_id: int | None = None
    ) -> None:
        """Start a background download spanning multiple seasons (unknown season)."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles_all_seasons(job_id, show_name, seasons, tmdb_id=tmdb_id)
        )

    async def restart_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Cancel any in-flight subtitle download and start a fresh one.

        Used after re-identification corrects the show title. Resets the
        in-memory event/task pair, clears stale `subtitle_status` and
        subtitle-related error_message in the DB, then kicks off a new download.
        """
        from sqlalchemy import update

        # Cancel a stale or in-flight task. Awaiting cancellation prevents the
        # old task's DB write from racing past the new one.
        old_task = self._subtitle_tasks.get(job_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return
            update_values: dict = {"subtitle_status": None, "subtitle_error_message": None}
            # Only wipe the catch-all error_message if it came from the subtitle
            # pipeline's exception path (the actionable "no subtitles" detail lives
            # on subtitle_error_message, cleared unconditionally above).
            if job.error_message and (
                job.error_message.startswith("Subtitle download")
                or job.error_message.startswith("Download error")
            ):
                update_values["error_message"] = None
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
            )
            await session.commit()

        # Clear the persistent UI banner immediately; the new task will emit
        # progress events as it runs.
        await ws_manager.broadcast_subtitle_event(job_id, "downloading", downloaded=0, total=0)

        self.start_subtitle_download(job_id, show_name, season, tmdb_id)

    async def try_discdb_assignment(self, job_id: int, title: "DiscTitle", session) -> bool:
        """Apply a DiscDB disc-order episode mapping as a post-ASR low-confidence fallback.

        Called only after ASR has already run and returned a confidence below
        DISCDB_FALLBACK_ASR_FLOOR. Returns True if a mapping was applied, False if
        no usable mapping exists (and the caller should proceed to REVIEW).
        """
        mappings = self._discdb_mappings.get(job_id)
        if not mappings:
            return False

        # Find the mapping for this title index
        mapping = None
        for m in mappings:
            if m.index == title.title_index:
                mapping = m
                break

        if not mapping or not mapping.season or not mapping.episode:
            return False

        if mapping.title_type not in ("Episode", "MainMovie"):
            return False

        # Provenance: "discdb" (TheDiscDB) or "network_disc" (disc-hash network).
        # Old persisted mappings predate the field; getattr keeps them on "discdb".
        source = getattr(mapping, "source", "discdb") or "discdb"
        origin = "disc network" if source == "network_disc" else "TheDiscDB"
        episode_code = f"S{mapping.season:02d}E{mapping.episode:02d}"
        logger.info(
            f"Job {job_id}: {origin} applying disc-order fallback mapping for title "
            f"{title.title_index}: {episode_code} ({mapping.episode_title!r})"
        )

        title.matched_episode = episode_code
        title.match_confidence = 0.99
        title.match_details = json.dumps(
            {
                "source": source,
                "episode_title": mapping.episode_title,
                "matched_episode": episode_code,
            }
        )
        title.match_source = source
        title.discdb_match_details = title.match_details
        title.state = TitleState.MATCHED
        session.add(title)
        await session.commit()

        await ws_manager.broadcast_title_update(
            job_id,
            title.id,
            TitleState.MATCHED.value,
            matched_episode=episode_code,
            match_confidence=0.99,
        )

        return True

    async def rematch_conflict(
        self,
        job_id: int,
        episode_code: str,
        num_points: int | None = None,
        min_vote_count: int | None = None,
    ) -> dict:
        """Re-run audio matching for every title currently claiming ``episode_code``.

        Used to break a same-episode collision: each contested title is re-matched
        (engram) with stricter parameters so the tie can resolve either way.
        Returns ``{"dispatched": [ids], "skipped": [{"title_id", "reason"}]}`` so
        callers can tell the user which titles could not be re-matched (e.g. their
        ripped file is no longer in staging).
        """
        async with async_session() as session:
            result = await session.execute(
                select(DiscTitle).where(DiscTitle.job_id == job_id).order_by(DiscTitle.title_index)
            )
            title_ids = [
                t.id
                for t in result.scalars().all()
                if t.matched_episode and t.matched_episode.upper() == episode_code.upper()
            ]

        dispatched: list[int] = []
        skipped: list[dict] = []
        for tid in title_ids:
            try:
                await self.rematch_single_title(
                    job_id,
                    tid,
                    source_preference="engram",
                    num_points=num_points,
                    min_vote_count=min_vote_count,
                )
                dispatched.append(tid)
            except Exception as e:
                # e.g. staging file missing (ValueError) or a transient DB/IO
                # error — skip this title rather than failing the whole conflict
                # re-match, and report it. Catching broadly (but NOT BaseException,
                # so asyncio.CancelledError still propagates) keeps the auto-
                # escalation caller from leaving its pass counter unset, which
                # would otherwise re-dispatch the same depth indefinitely.
                logger.warning(f"Conflict re-match: skipping title {tid} (job {job_id}): {e}")
                skipped.append({"title_id": tid, "reason": str(e)})
        return {"dispatched": dispatched, "skipped": skipped}

    async def rematch_single_title(
        self,
        job_id: int,
        title_id: int,
        source_preference: str | None = None,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        advisory: bool = False,
    ) -> None:
        """Re-match a single title with the specified source preference.

        source_preference:
            "discdb" — restore from stored discdb_match_details
            "engram" — clear match and re-run audio fingerprinting
            None — try discdb first if available, else engram

        ``num_points``/``min_vote_count`` override the matcher scan density and
        vote gate for the engram path (deep re-match); None keeps defaults.

        ``advisory`` marks a user-initiated re-match: the result is surfaced in
        review for manual confirmation and is NEVER auto-organized (the title is
        held in REVIEW with ``forced_review`` regardless of confidence). Only the
        manual per-track button sets this; pipeline/escalation/conflict callers
        leave it False so they keep auto-finalizing.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            title = await session.get(DiscTitle, title_id)
            if not job or not title or title.job_id != job_id:
                raise ValueError(f"Job {job_id} or title {title_id} not found")

            use_discdb = False
            if source_preference == "discdb":
                use_discdb = True
            elif source_preference is None and title.discdb_match_details:
                use_discdb = True

            if use_discdb and title.discdb_match_details:
                # Restore from stored DiscDB match details
                details = json.loads(title.discdb_match_details)
                title.match_details = title.discdb_match_details
                title.match_source = "discdb"
                title.match_confidence = 0.99

                # Restore episode code from stored details or in-memory mappings
                if "matched_episode" in details:
                    title.matched_episode = details["matched_episode"]
                else:
                    mappings = self._discdb_mappings.get(job_id, [])
                    for m in mappings:
                        if m.index == title.title_index and m.season and m.episode:
                            title.matched_episode = f"S{m.season:02d}E{m.episode:02d}"
                            break
                title.state = TitleState.MATCHED
                session.add(title)
                await session.commit()

                await ws_manager.broadcast_title_update(
                    job_id,
                    title.id,
                    TitleState.MATCHED.value,
                    matched_episode=title.matched_episode,
                    match_confidence=title.match_confidence,
                    match_source="discdb",
                )
                return

            # Engram re-match: validate staging file exists
            file_path = find_staging_file(job, title)
            if not file_path:
                raise ValueError(
                    f"Staging file not found for title {title_id} "
                    f"(output_filename={title.output_filename}, staging={job.staging_path})"
                )

            # Reset match fields
            title.state = TitleState.MATCHING
            title.matched_episode = None
            title.match_confidence = 0.0
            title.match_details = None
            title.match_source = None
            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(job_id, title.id, TitleState.MATCHING.value)

        # Reset the stale-job watchdog clock at dispatch so a (deep) re-match —
        # especially an auto-escalation that holds the job in MATCHING — isn't
        # force-advanced during the setup window before the first progress signal.
        if self._note_activity:
            self._note_activity(job_id)

        # Fire-and-forget: matching runs in background, progress via WebSocket
        match_task = asyncio.create_task(
            self.match_single_file(
                job_id, title_id, file_path, num_points, min_vote_count, advisory=advisory
            )
        )
        match_task.add_done_callback(
            lambda t, jid=job_id, tid=title_id: self.on_match_task_done(t, jid, tid)
        )

    async def match_single_file(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        advisory: bool = False,
    ) -> None:
        """Run matching for a single ripped file, tagging logs with the job id.

        Self-tags so every matching log line carries ``job=<id>`` regardless of
        how this is reached — task spawn, direct ``await`` from an API handler
        (deep re-match / conflict resolution), or via the injected callback used
        by the identification/finalization coordinators.

        ``advisory`` (manual per-track re-match) holds the result in REVIEW for
        confirmation instead of auto-organizing — see ``rematch_single_title``.
        """
        with job_log_context(job_id):
            await self._run_match_single_file(
                job_id, title_id, file_path, num_points, min_vote_count, advisory=advisory
            )

    async def _llm_fallback_without_subtitles(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        series_name: str | None,
        season: int | None,
    ) -> dict | None:
        """AI episode-matching fallback for the no-subtitles case.

        When ``ai_episode_matching_enabled`` is on (with a key) and the season is
        known, transcribe the ripped file and match the transcript against the
        TMDB synopsis — no reference subtitles required. Returns enriched
        ``match_details`` carrying an ``llm_suggestion`` for the Review UI, or
        ``None`` when AI matching is disabled/unconfigured, the show/season is
        unknown, or no suggestion was produced (the caller then keeps the plain
        manual-review path). Never raises — the whole body is guarded so a
        fallback failure can't leave the title stuck; control always falls
        through to the caller's manual-review hand-off.
        """
        if not series_name or not season:
            return None

        # The entire body is wrapped: get_config() and the MATCHING-transition
        # commit/broadcast sit above the LLM call, and an exception in any of
        # them must not escape to _run_match_single_file (whose failed-subtitle
        # branch has no handler) — that would leave the title stuck in MATCHING
        # with REVIEW never set and _check_job_completion never called.
        try:
            from app.services.config_service import get_config

            config = await get_config()
            if not config or not config.ai_episode_matching_enabled:
                return None
            if not config.ai_api_key:
                return None

            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): no reference subtitles — attempting AI "
                f"episode-matching fallback (ASR transcript + TMDB synopsis)."
            )

            # Surface activity while the (slow) full-file transcription runs.
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.MATCHING
                    session.add(title)
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id, title_id, TitleState.MATCHING.value
                    )

            # Bound concurrent Whisper runs the same way the main matcher does —
            # this gate sits *before* the normal semaphore acquire, so failed
            # titles would otherwise transcribe unbounded.
            acquired = False
            try:
                if self._match_semaphore is not None:
                    await self._match_semaphore.acquire()
                    acquired = True
                enriched = await episode_curator.suggest_episode_via_llm(
                    file_path=file_path,
                    series_name=series_name,
                    season=season,
                )
            finally:
                if acquired and self._match_semaphore is not None:
                    self._match_semaphore.release()
        except Exception as e:  # never let the fallback break the review hand-off
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): AI fallback failed: {e}",
                exc_info=True,
            )
            return None

        if enriched and enriched.get("llm_suggestion"):
            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): AI fallback produced a suggestion "
                f"(episode {enriched['llm_suggestion'].get('episode')!r}) for review."
            )
            return enriched
        return None

    async def _run_match_single_file(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        advisory: bool = False,
    ) -> None:
        """Run matching for a single ripped file.

        ``num_points``/``min_vote_count`` override the matcher's scan density and
        vote gate (deep re-match); None keeps defaults. ``advisory`` surfaces the
        result in review instead of auto-organizing (manual per-track re-match).
        """
        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): match task started for {file_path.name}"
        )

        # 1. Wait for subtitles to be ready before matching
        logger.debug(
            f"[MATCH] Title {title_id} (Job {job_id}): _match_single_file entered. "
            f"subtitle_ready event exists: {job_id in self._subtitle_ready}"
        )
        if job_id in self._subtitle_ready:
            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): waiting for subtitle download..."
            )
            try:
                await asyncio.wait_for(self._subtitle_ready[job_id].wait(), timeout=300)
                logger.info(f"[MATCH] Title {title_id} (Job {job_id}): subtitle event received")
            except TimeoutError:
                logger.warning(
                    f"[MATCH] Title {title_id} (Job {job_id}): subtitle download timed out "
                    f"after 300s"
                )
            except Exception as e:
                logger.error(
                    f"[MATCH] Title {title_id} (Job {job_id}): error waiting for subtitles: {e}"
                )

        # 2. Check subtitle status from database - BLOCK matching if failed
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            subtitle_status = job.subtitle_status if job else None
            detected_title = job.detected_title if job else None
            detected_season = job.detected_season if job else None

        # Gate matching based on subtitle status
        if subtitle_status == "failed":
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitle download failed. "
                f"No reference files available. Title needs manual episode assignment."
            )
            # Last resort before manual assignment: when AI episode matching is
            # enabled, transcribe the ripped file (ASR) and match the transcript
            # against the TMDB synopsis — this needs no reference subtitles. Any
            # result is a REVIEW suggestion only (never auto-organized).
            enriched = await self._llm_fallback_without_subtitles(
                job_id, title_id, file_path, detected_title, detected_season
            )
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.REVIEW
                    title.match_confidence = 0.0
                    if enriched:
                        title.match_details = json.dumps(enriched)
                    else:
                        title.match_details = json.dumps(
                            {
                                "error": "subtitle_download_failed",
                                "message": "Subtitle download failed, cannot auto-match. Manual episode assignment needed.",
                            }
                        )
                    session.add(title)
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title.id,
                        title.state.value,
                        matched_episode=None,
                        match_confidence=0.0,
                    )
                    await self._check_job_completion(session, job_id)
            return

        elif subtitle_status == "partial":
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitle download partially succeeded. "
                f"Matching will proceed with available reference files."
            )

        elif subtitle_status in ("completed", None):
            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitles ready, proceeding with matching"
            )

        else:
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): unknown subtitle status '{subtitle_status}', "
                f"attempting match anyway"
            )

        # 3. Wait for the file to be fully written before matching
        wait_result = await self._wait_for_file_ready(file_path, title_id, job_id)
        if await self._handle_file_wait_result(wait_result, job_id, title_id, file_path):
            return

        # 4. Duration pre-filter. Fetch episode runtimes WITHOUT a DB connection
        # held: the lookup can hit TMDB over the network, and holding a pooled
        # connection across that round-trip — across every title of every active
        # job — is what exhausted the connection pool. Mirror the rip path: read
        # the scalars under a brief session, release it, do the network work,
        # then reopen only to act on the result.
        try:
            job_tmdb_id: int | None = None
            job_detected_title: str | None = None
            job_detected_season: int | None = None
            title_duration: float | None = None
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                title = await session.get(DiscTitle, title_id)
                if job and title and job.detected_season:
                    job_tmdb_id = job.tmdb_id
                    job_detected_title = job.detected_title
                    job_detected_season = job.detected_season
                    title_duration = title.duration_seconds

            if job_detected_season:
                # No connection held across this (possibly networked) lookup.
                runtimes = await self._episode_runtimes_for_job(
                    job_id, job_tmdb_id, job_detected_title, job_detected_season
                )
                if runtimes and title_duration:
                    title_minutes = title_duration / 60
                    if not _duration_matches_episode_runtime(title_minutes, runtimes):
                        # Re-fetch under a fresh session: _handle_extras writes.
                        async with async_session() as session:
                            job = await session.get(DiscJob, job_id)
                            title = await session.get(DiscTitle, title_id)
                            if job and title:
                                handled = await self._handle_extras(
                                    job_id,
                                    title_id,
                                    title,
                                    job,
                                    file_path,
                                    title_minutes,
                                    runtimes,
                                    session,
                                )
                                if handled:
                                    return
        except Exception as e:
            # Surface the full traceback: a silently-failing TMDB runtime
            # fetch here disables automatic extras detection (the pre-filter
            # is the only thing that flags non-episode bonus tracks before
            # the matcher tries to force them onto an episode).
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): duration pre-filter failed: {e}. "
                f"Proceeding with matching normally.",
                exc_info=True,
            )

        # 5. Acquire semaphore to limit concurrent matching
        if self._match_semaphore is not None:
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): waiting for match semaphore...")
            await self._match_semaphore.acquire()
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): acquired match semaphore")

        # 6. Transition title to MATCHING
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if title:
                title.state = TitleState.MATCHING
                session.add(title)
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title.id,
                    title.state.value,
                    duration_seconds=title.duration_seconds,
                    file_size_bytes=title.file_size_bytes,
                )

            # Belt-and-suspenders: make sure the job itself reflects MATCHING the
            # moment a real per-title match begins (see _converge_job_to_matching).
            await self._converge_job_to_matching(session, job_id)

        # 7. Run matching
        try:
            await self._match_single_file_inner(
                job_id, title_id, file_path, num_points, min_vote_count, advisory=advisory
            )
        except Exception as e:
            logger.exception(
                f"[MATCH] Title {title_id} (Job {job_id}): error in _match_single_file_inner: {e}"
            )
            raise
        finally:
            if self._match_semaphore is not None:
                self._match_semaphore.release()
                logger.info(f"[MATCH] Title {title_id} (Job {job_id}): released match semaphore")

    async def _episode_runtimes_for_job(
        self,
        job_id: int,
        tmdb_id: int | None,
        detected_title: str | None,
        detected_season: int | None,
    ) -> list[int]:
        """Episode runtimes (minutes) for the job's season, cached per job.

        Takes scalars rather than a ``DiscJob`` so the caller can release its DB
        session before this (network) TMDB lookup: holding a pooled connection
        across the round-trip, across every title of every active job, is what
        exhausted the connection pool. Backs the duration pre-filter that flags
        non-episode bonus tracks as extras before the matcher forces them onto an
        episode.
        """
        if job_id in self._episode_runtimes:
            return self._episode_runtimes[job_id]

        # Collapse the cold-window stampede: in a multi-season import, every
        # title of a job hits this at once. The lock makes only the first caller
        # perform the (network) TMDB fetch; the rest await it and read the
        # populated cache via the double-check below. Because the caller already
        # released its DB session, a title waiting on this lock holds no pooled
        # connection — without that ordering the lock would merely convert a
        # connection-per-fetch storm into a connection-per-waiter storm.
        async with self._episode_runtimes_locks[job_id]:
            if job_id in self._episode_runtimes:
                return self._episode_runtimes[job_id]

            from app.matcher.tmdb_client import (
                fetch_season_episode_runtimes,
                fetch_show_id,
            )

            # Prefer the job's authoritative tmdb_id (e.g. set when the user
            # re-identified a same-name collision in review). Re-resolving by name
            # here would return the dominant same-name twin (Frasier 1993 #3452,
            # 24×23min) instead of the re-identified revival (#195241, 10 eps),
            # whose real episodes would then fail the duration filter and be
            # misclassified as extras. Sibling to PR #282's curator/chromaprint/LLM
            # threading.
            if tmdb_id:
                show_id = str(tmdb_id)
            else:
                show_id = await asyncio.to_thread(fetch_show_id, detected_title)
            if show_id:
                runtimes = await asyncio.to_thread(
                    fetch_season_episode_runtimes, show_id, detected_season
                )
            else:
                runtimes = []
            self._episode_runtimes[job_id] = runtimes
            return runtimes

    async def _converge_job_to_matching(self, session, job_id: int) -> None:
        """Ensure the parent job reflects MATCHING the moment a title starts matching.

        Belt-and-suspenders for the import-folder dashboard: the job-level MATCHING
        broadcast is normally emitted once by identify_from_staging (or the rip path)
        when it kicks off matching. If that transition was ever skipped or raced, the
        card would stay on the scanning radar while titles match in the background —
        the exact import-watch-folder symptom PR #307 fixed. Converging here, at the
        point real matching begins, makes "a title is matching ⇒ the job shows
        matching" a local invariant instead of relying solely on a remote one-shot.

        Acts ONLY on a job still in IDENTIFYING, so the REVIEW_NEEDED deep-rematch
        path and an already-MATCHING job are untouched (no duplicate broadcasts, no
        wrong transitions). Idempotent: the first title converges the job, the rest
        no-op.
        """
        job = await session.get(DiscJob, job_id)
        if job and job.state == JobState.IDENTIFYING:
            if await self._state_machine.transition(
                job, JobState.MATCHING, session, broadcast=False
            ):
                await ws_manager.broadcast_job_update(job_id, JobState.MATCHING.value)

    async def _maybe_pin_converged_season(self, session, job_id: int) -> None:
        """Pin ``job.detected_season`` once cross-season matching converges (B6).

        A gate-D unknown-season disc matches every title across ALL seasons
        (``detected_season is None`` → the matcher's season-unknown path),
        which is expensive: TF-IDF against every season's references, per
        title. Physical discs are season-coherent in practice, so once two
        MATCHED titles agree on a season — with zero MATCHED disagreement —
        the disc's season is effectively known. Pinning it makes subsequent
        title dispatches take the cheap single-season path (each title
        re-reads the job row in its own fresh session, both for the duration
        pre-filter and for the matcher call) and re-engages the chromaprint
        prepass (curator.match_single_file only runs the prepass when
        ``season`` is truthy — the season-unknown branch returns before it).
        Matches already in flight keep the season value they captured; they
        finish their cross-season scan undisturbed.

        Only MATCHED titles vote: REVIEW-state titles (low-confidence guesses)
        and unparseable ``matched_episode`` values ("extra", None) are never
        counted, on either side of the agree/disagree rule.

        CONSTRAINT — imports must never pin: a flat watch-folder import
        (``drive_id == "import"``) legitimately mixes episodes from MANY
        seasons in one job, and its early titles can easily land same-season
        by chance; pinning would then force every later title through the
        wrong single-season scan. The zero-disagreement rule protects discs
        (a genuinely mixed disc accrues a disagreeing MATCHED title and never
        reaches 0-disagree), but it cannot protect an import's first two
        titles — so imports are excluded outright.

        Concurrency: parallel titles can commit MATCHED near-simultaneously
        and both evaluate this. Double-pinning DIFFERENT seasons is
        impossible: the MATCHED set only grows during matching, and a pin
        requires every parsed MATCHED season to agree — a later evaluation
        sees a superset that still contains the first pin's agreeing titles,
        so conflicting evidence fails the zero-disagree rule instead. The
        worst race residue is a duplicate pin of the SAME season (an
        idempotent re-commit plus a duplicate broadcast).

        Also retires a ``kind="season"`` identity CTA in the same commit —
        the question answered itself. Blocking kinds (name/reidentify) are
        left alone: they shouldn't coexist with MATCHED titles (blocking
        prompts park titles QUEUED), but if one ever does, only the answer
        endpoints may clear it.
        """
        job = await session.get(DiscJob, job_id)
        if not job:
            return
        # Fresh read: this session loaded the job before the (long) match ran,
        # and a sibling title's pin commits in ANOTHER session — with
        # expire_on_commit=False, session.get alone would hand back that stale
        # snapshot and re-run a pin that already happened.
        await session.refresh(job)
        if job.detected_season is not None:
            return
        if job.drive_id == "import":
            return
        matched_titles = (
            (
                await session.execute(
                    select(DiscTitle).where(
                        DiscTitle.job_id == job_id,
                        DiscTitle.state == TitleState.MATCHED,
                    )
                )
            )
            .scalars()
            .all()
        )
        seasons: list[int] = []
        for t in matched_titles:
            m = _SEASON_FROM_EP_CODE_RE.match((t.matched_episode or "").strip())
            if m:
                seasons.append(int(m.group(1)))
        if len(seasons) < 2 or len(set(seasons)) != 1:
            return

        season = seasons[0]
        job.detected_season = season
        cleared = prompt_kind(job.identity_prompt_json) == "season"
        if cleared:
            job.identity_prompt_json = None
        session.add(job)
        await session.commit()
        logger.info(
            f"[MATCH] Job {job_id}: {len(seasons)} matched titles agree on season "
            f"{season} with zero disagreement — pinning detected_season so remaining "
            f"titles take the single-season path" + (" (season prompt retired)" if cleared else "")
        )
        # state=None → "unchanged" on the frontend merge; "" clears the CTA
        # (the enumerated-WS clear pattern), None leaves it untouched.
        await ws_manager.broadcast_job_update(
            job_id,
            None,
            detected_season=season,
            identity_prompt_json="" if cleared else None,
        )

    async def _match_single_file_inner(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
        advisory: bool = False,
    ) -> None:
        """Inner matching logic, called under the match semaphore.

        ``advisory`` (manual per-track re-match): a confident result is held in
        REVIEW with ``forced_review`` and the contribution enqueue is skipped, so
        nothing is auto-organized and an unconfirmed match never poisons the
        fingerprint corpus.
        """
        match_start = time.monotonic()

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            title = await session.get(DiscTitle, title_id)
            if not job or not title:
                logger.warning(
                    f"[MATCH] Title {title_id} (Job {job_id}): DB record not found, aborting"
                )
                return

            file_size_mb = 0
            try:
                file_size_mb = file_path.stat().st_size / 1024 / 1024
            except OSError:
                pass

            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): starting episode matching — "
                f"file={file_path.name} ({file_size_mb:.0f} MB), "
                f"series={job.detected_title!r}, season={job.detected_season}"
            )

            try:
                # Define progress callback
                loop = asyncio.get_running_loop()
                _json_dumps = json.dumps

                def on_progress(stage: str, percent: float, vote_data: list | None = None):
                    if self._note_activity:
                        try:
                            self._note_activity(job_id)
                        except Exception:
                            # Best-effort watchdog heartbeat; never let it disrupt matching.
                            pass
                    try:
                        details = None
                        if vote_data:
                            best = vote_data[0]
                            details = _json_dumps(
                                {
                                    "score": best["score"],
                                    "vote_count": best["vote_count"],
                                    "target_votes": best.get("target_votes", 5),
                                    "runner_ups": vote_data,
                                }
                            )
                        coro = ws_manager.broadcast_title_update(
                            job_id,
                            title_id,
                            TitleState.MATCHING.value,
                            match_stage=stage,
                            match_progress=percent,
                            match_details=details,
                        )
                        fut = asyncio.run_coroutine_threadsafe(coro, loop)

                        def _log_broadcast_error(f) -> None:
                            try:
                                f.result()
                            except Exception as exc:
                                logger.warning(
                                    f"[MATCH] Title {title_id}: progress broadcast failed: {exc}"
                                )

                        fut.add_done_callback(_log_broadcast_error)
                    except Exception as e:
                        logger.warning(f"[MATCH] Title {title_id}: progress callback error: {e}")

                # Attach a process-shared PackCache to the curator (idempotent).
                # EpisodeCurator._chromaprint_prepass reads getattr(self, "_pack_cache", None);
                # wiring it here ensures the cache survives across titles in the same process.
                if not hasattr(episode_curator, "_pack_cache"):
                    from app.services.fingerprint_pack_cache import PackCache

                    episode_curator._pack_cache = PackCache()

                # Run the episode matcher
                logger.info(
                    f"[MATCH] Title {title_id} (Job {job_id}): calling episode_curator.match_single_file for {file_path.name}"
                )
                # Queue-aware safety net: bound a single match by the per-track
                # ceiling (reuses timeout_matching_seconds; 0/None = no limit). The
                # clock starts here — i.e. only once a slot is acquired and matching
                # is underway — so a track merely waiting in the QUEUED line is never
                # penalized. On timeout, wait_for cancels the matcher coroutine, which
                # unwinds to _run_match_single_file's `finally` and releases the
                # semaphore so the next QUEUED track gets the freed slot. (If the
                # matcher offloaded blocking ASR to a thread, the slot still frees;
                # the orphaned thread runs to process exit — acceptable.)
                from app.services.config_service import get_config

                _cfg = await get_config()
                match_ceiling = _cfg.timeout_matching_seconds if _cfg else 0
                _match_coro = episode_curator.match_single_file(
                    file_path,
                    series_name=job.detected_title,
                    season=job.detected_season,
                    progress_callback=on_progress,
                    num_points=num_points,
                    min_vote_count=min_vote_count,
                    tmdb_id=job.tmdb_id,
                )
                try:
                    if match_ceiling and match_ceiling > 0:
                        result = await asyncio.wait_for(_match_coro, timeout=match_ceiling)
                    else:
                        result = await _match_coro
                except TimeoutError:
                    logger.warning(
                        f"[MATCH] Title {title_id} (Job {job_id}): match timed out after "
                        f"{match_ceiling}s — routing to review. The forced_review flag stops "
                        f"review-escalation from re-dispatching it into a timeout loop."
                    )
                    title.state = TitleState.REVIEW
                    # Merge, don't overwrite: a concurrent skip_title may have stamped
                    # its own forced_review reason while this match was running. Preserve
                    # it (setdefault) so the audit trail isn't rewritten to "timed out".
                    # Mirrors JobManager._forced_review_details.
                    _md: dict = {}
                    if title.match_details:
                        try:
                            parsed = json.loads(title.match_details)
                            if isinstance(parsed, dict):
                                _md = parsed
                        except (json.JSONDecodeError, TypeError):
                            _md = {}
                    _md["forced_review"] = True
                    _md.setdefault("reason", f"match timed out after {match_ceiling}s")
                    title.match_details = json.dumps(_md)
                    session.add(title)
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_id,
                        title.state.value,
                        match_details=title.match_details,
                    )
                    await self._check_job_completion(session, job_id)
                    return

                elapsed = time.monotonic() - match_start

                # Race guard: if the title was force-advanced or per-track-skipped
                # while this match was running, respect that decision — don't clobber
                # match_details (which would wipe the forced_review flag and let the
                # review-escalation re-dispatch the title, manifesting as
                # "Skip/Force does nothing").
                if title.match_details:
                    try:
                        existing = json.loads(title.match_details)
                    except (json.JSONDecodeError, TypeError):
                        existing = None
                    if isinstance(existing, dict) and existing.get("forced_review"):
                        logger.info(
                            f"[MATCH] Title {title_id} (Job {job_id}): force-advanced during "
                            f"match ({elapsed:.1f}s) — leaving title untouched."
                        )
                        await self._check_job_completion(session, job_id)
                        return

                # Update title with match result. These in-memory writes are
                # provisional: on the DiscDB-fallback path below, try_discdb_assignment
                # overwrites both fields with the DiscDB episode (conf 0.99) and commits
                # before returning, so no stale ASR value reaches the DB. The ordering
                # matters — keep DiscDB's pre-commit work (dict lookups + formatting,
                # which can't raise the caught exception types) failure-free so the outer
                # error handler can never persist this stale ASR code to REVIEW.
                title.matched_episode = result.episode_code
                title.match_confidence = result.confidence

                if result.needs_review or advisory:
                    # ASR-preferred precedence: a very low-confidence ASR result
                    # (not a deliberate manual re-match) defers to a DiscDB episode
                    # mapping when one exists, instead of going to review. DiscDB
                    # numbers by disc order (not aired order), so it is trusted only
                    # when ASR could not produce a usable match.
                    # Invariant: the curator always sets needs_review=True for a
                    # low-confidence result, so a confidence below the floor implies
                    # we are inside this branch. The fallback can therefore only
                    # auto-organize via a DiscDB mapping, never by confidence alone.
                    if not advisory and result.confidence < DISCDB_FALLBACK_ASR_FLOOR:
                        if await self.try_discdb_assignment(job_id, title, session):
                            logger.info(
                                f"[MATCH] Title {title_id} (Job {job_id}): ASR confidence "
                                f"{result.confidence:.2f} < {DISCDB_FALLBACK_ASR_FLOOR}; "
                                f"assigned episode from DiscDB mapping (disc-order fallback)."
                            )
                            await self._check_job_completion(session, job_id)
                            return

                    # A low-confidence result always goes to REVIEW — even when the
                    # matcher emits a best-guess episode code. Auto-organizing a
                    # borderline guess silently mis-files content (e.g. a bonus
                    # track that slipped past the duration pre-filter lands on the
                    # wrong episode instead of being flagged as an extra). The
                    # auto review-escalation deep re-matches it next; if it still
                    # can't resolve, the user decides. matched_episode is kept as
                    # the inspector's starting suggestion.
                    #
                    # ``advisory`` (manual per-track re-match) forces this branch
                    # even for a confident result: a user-initiated re-match must
                    # surface its candidate for confirmation, never auto-organize.
                    title.state = TitleState.REVIEW

                    _why = (
                        "advisory (manual re-match)"
                        if advisory and not result.needs_review
                        else "needs review"
                    )
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): {_why} — "
                        f"episode={result.episode_code}, confidence={result.confidence:.2f}, "
                        f"state={title.state.value}, elapsed={elapsed:.1f}s"
                    )
                else:
                    title.state = TitleState.MATCHED
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): matched (deferred) — "
                        f"episode={result.episode_code}, confidence={result.confidence:.2f}, "
                        f"elapsed={elapsed:.1f}s"
                    )

                    # Phase 1: extract chromaprint fingerprint (best-effort; failure does not block match)
                    try:
                        from datetime import datetime

                        from app.matcher.chromaprint_extractor import ChromaprintExtractor
                        from app.services.config_service import get_config

                        cfg = await get_config()
                        fpcalc_path = await _resolve_fpcalc_path(cfg.fpcalc_path)
                        if fpcalc_path:
                            ffmpeg_path = await _resolve_ffmpeg_path(cfg.ffmpeg_path)
                            extractor = ChromaprintExtractor(
                                fpcalc_path=fpcalc_path, ffmpeg_path=ffmpeg_path
                            )
                            fp_result = await extractor.extract(str(file_path))
                            title.chromaprint_blob = fp_result.to_blob()
                            title.chromaprint_extracted_at = datetime.now(UTC)
                        else:
                            logger.debug(
                                f"fpcalc not configured; skipping chromaprint extraction for title {title.id}"
                            )
                    except Exception as e:
                        # Graceful degradation: matching already succeeded; log and continue
                        logger.warning(
                            f"Chromaprint extraction failed for title {title.id}: {e}",
                            exc_info=True,
                        )

                    # Tag the source as chromaprint when the cascade accepted via
                    # chromaprint so the contribution enqueue maps to
                    # "engram_chromaprint_corroboration" rather than "engram_asr".
                    if (result.match_details or {}).get("chromaprint_accepted"):
                        title.match_source = "engram_chromaprint"

                    # Phase 1: enqueue contribution if extraction produced a fingerprint
                    if title.chromaprint_blob and title.matched_episode:
                        try:
                            import re as _re

                            from app.services.config_service import get_config as _get_config
                            from app.services.contribution_queue import ContributionQueue

                            _cfg = await _get_config()
                            if _cfg.contribution_pseudonym:
                                # Parse "S01E07" → (season, episode)
                                _m = _re.match(r"S(\d{1,2})E(\d{1,3})", title.matched_episode or "")
                                season_num = int(_m.group(1)) if _m else None
                                episode_num = int(_m.group(2)) if _m else None
                                disc_hash = None
                                if getattr(job, "content_hash", None):
                                    try:
                                        disc_hash = bytes.fromhex(job.content_hash)
                                    except (TypeError, ValueError):
                                        disc_hash = None
                                tmdb_id_val = 0
                                if getattr(job, "tmdb_id", None):
                                    try:
                                        tmdb_id_val = int(job.tmdb_id)
                                    except (TypeError, ValueError):
                                        tmdb_id_val = 0
                                if tmdb_id_val == 0:
                                    # Skip enqueue rather than poison Phase 2 with
                                    # un-attributable contributions. The chromaprint
                                    # is still stored on DiscTitle for diagnostic use.
                                    logger.debug(
                                        f"Skipping contribution for title {title.id}: "
                                        "no usable tmdb_id on parent job"
                                    )
                                else:
                                    # Map DiscTitle.match_source onto FingerprintContribution's
                                    # documented value set (engram_asr / engram_discdb /
                                    # bootstrap / user_review). The raw "engram" value used
                                    # internally for ASR matches is not a documented
                                    # contribution source.
                                    _contrib_source = _MATCH_SOURCE_TO_CONTRIB.get(
                                        title.match_source or "", "engram_asr"
                                    )
                                    await ContributionQueue().enqueue(
                                        session=session,
                                        title_id=title.id,
                                        chromaprint_blob=title.chromaprint_blob,
                                        tmdb_id=tmdb_id_val,
                                        season=season_num,
                                        episode=episode_num,
                                        match_confidence=float(title.match_confidence or 0.0),
                                        match_source=_contrib_source,
                                        disc_content_hash=disc_hash,
                                        pseudonym=_cfg.contribution_pseudonym,
                                        show_title=getattr(job, "tmdb_name", None)
                                        or getattr(job, "detected_title", None),
                                        contributions_enabled=_cfg.enable_fingerprint_contributions,
                                    )
                        except Exception as e:
                            logger.warning(
                                f"Failed to enqueue contribution for title {title.id}: {e}",
                                exc_info=True,
                            )

                if result.match_details:
                    try:
                        title.match_details = json.dumps(result.match_details)
                    except Exception as e:
                        logger.error(f"Failed to dump match_details: {e}")

                if advisory:
                    # Stamp the user-initiated re-match as a deliberate
                    # hand-to-human: ``forced_review`` makes _is_rematchable_review
                    # skip it, so the auto review/conflict escalation never
                    # re-dispatches the surfaced candidate behind the user's back.
                    try:
                        _md = json.loads(title.match_details) if title.match_details else {}
                        if not isinstance(_md, dict):
                            _md = {}
                    except (json.JSONDecodeError, TypeError):
                        _md = {}
                    _md["forced_review"] = True

                    # Warn right in review if this candidate is already organized by
                    # another track on the SAME disc — the "an extra got force-matched
                    # to an episode that already ripped" case the user hit. (Cross-job
                    # / library duplicates can't be known without organizing; they
                    # surface at confirm time via the organizer's FILE_EXISTS handling.)
                    if result.episode_code:
                        siblings = (
                            (
                                await session.execute(
                                    select(DiscTitle).where(
                                        DiscTitle.job_id == job_id,
                                        DiscTitle.id != title_id,
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if any(
                            s.state == TitleState.COMPLETED
                            and _same_episode_code(s.matched_episode, result.episode_code)
                            for s in siblings
                        ):
                            _md["error"] = "file_exists"
                            _md["message"] = (
                                f"{result.episode_code} is already organized by another "
                                "track on this disc — likely a duplicate or extra. "
                                "Reassign the episode or mark it as an Extra."
                            )

                    title.match_details = json.dumps(_md)

                # Only attribute the match to Engram when an episode match was
                # actually recorded. A title routed to REVIEW with no episode must
                # not carry the "ENGRAM" provider badge — that implies a confident
                # auto-match the matcher never made.
                # Preserve "engram_chromaprint" if already set above (chromaprint
                # cascade path) — it must not be overwritten with the generic label.
                if title.state == TitleState.MATCHED and title.match_source != "engram_chromaprint":
                    title.match_source = "engram"

                # Extract match stats for broadcast
                matches_found = 1
                matches_rejected = 0

                if title.match_details:
                    try:
                        details = json.loads(title.match_details)
                        runner_ups = details.get("runner_ups", [])
                        matches_found += len(runner_ups)
                        matches_rejected = len(
                            [r for r in runner_ups if r.get("confidence", 0) < 0.5]
                        )
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass

                session.add(title)
                await session.commit()

                # Broadcast update
                await self._broadcaster.broadcast_job_state_changed(job_id, job.state)
                await ws_manager.broadcast_title_update(
                    job_id,
                    title.id,
                    title.state.value,
                    matched_episode=title.matched_episode,
                    match_confidence=title.match_confidence,
                    duration_seconds=title.duration_seconds,
                    file_size_bytes=title.file_size_bytes,
                    matches_found=matches_found,
                    matches_rejected=matches_rejected,
                    match_details=title.match_details,
                )

                # Walk-away B6: this MATCHED commit may be the one that converges
                # an unknown-season disc — evaluate the season pin now so the next
                # dispatched title already takes the single-season path. Best-effort
                # in its own guard: the match above is committed, and a pin failure
                # must not trip the outer except into re-marking the title REVIEW.
                if title.state == TitleState.MATCHED:
                    try:
                        await self._maybe_pin_converged_season(session, job_id)
                    except Exception as e:
                        logger.warning(
                            f"[MATCH] Job {job_id}: season-pin evaluation failed: {e}",
                            exc_info=True,
                        )

                # Check if ALL titles are done
                await self._check_job_completion(session, job_id)

            except (MatchingError, OSError, ValueError):
                elapsed = time.monotonic() - match_start
                logger.exception(
                    f"[MATCH] Title {title_id} (Job {job_id}): matching error after "
                    f"{elapsed:.1f}s — {file_path.name}. Needs manual assignment."
                )
                title.state = TitleState.REVIEW
                session.add(title)
                await session.commit()
                await self._check_job_completion(session, job_id)

    async def _handle_extras(
        self,
        job_id,
        title_id,
        title,
        job,
        file_path,
        title_minutes,
        runtimes,
        session,
    ):
        """Handle extras based on policy. Returns True if title was handled."""
        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): duration {title_minutes:.0f}min "
            f"doesn't match any episode runtime {runtimes} "
            f"(window -{EPISODE_DURATION_UNDER_TOLERANCE_MIN}/"
            f"+{EPISODE_DURATION_OVER_TOLERANCE_MIN}min). "
            f"Detected as extra."
        )

        from app.services.config_service import get_config as get_db_config

        db_config = await get_db_config()
        extras_policy = db_config.extras_policy

        if extras_policy == "skip":
            logger.info(f"[MATCH] Title {title_id}: extras policy is 'skip', discarding.")
            title.state = TitleState.COMPLETED
            title.is_extra = True
            title.match_details = json.dumps(
                {
                    "auto_sorted": "extras",
                    "action": "skipped",
                    "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
                }
            )
            session.add(title)
            await session.commit()
            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                is_extra=title.is_extra,
                match_details=title.match_details,
            )
            await self._check_job_completion(session, job_id)
            return True

        if extras_policy == "ask":
            logger.info(f"[MATCH] Title {title_id}: extras policy is 'ask', sending to review.")
            title.state = TitleState.REVIEW
            title.is_extra = True
            title.match_details = json.dumps(
                {
                    "auto_sorted": "extras",
                    "action": "review",
                    "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
                }
            )
            session.add(title)
            await session.commit()
            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                is_extra=title.is_extra,
                match_details=title.match_details,
            )
            await self._check_job_completion(session, job_id)
            return True

        # Default: "keep" — defer organization to end-of-disc finalize.
        # The extra rides the normal MATCHED -> finalize path like any other
        # track: a cleanly-matched disc files it into Extras/ at finalize and
        # auto-completes, while a disc that goes to review shows it as an
        # ordinary, reassignable track (pre-labelled "extra"). Organizing here
        # would set COMPLETED early and freeze it in the review UI's read-only
        # "Processed" list — the bug this fixes.
        title.state = TitleState.MATCHED
        title.is_extra = True
        title.matched_episode = "extra"
        # Deliberate classification, not a low-confidence ASR guess. A high
        # confidence keeps the review page's first-unresolved-title focus from
        # landing on an already-decided extra instead of a real REVIEW title.
        title.match_confidence = 1.0
        title.match_details = json.dumps(
            {
                "auto_sorted": "extras",
                "action": "deferred",
                "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
            }
        )
        session.add(title)
        await session.commit()
        await ws_manager.broadcast_title_update(
            job_id,
            title.id,
            title.state.value,
            matched_episode=title.matched_episode,
            match_confidence=title.match_confidence,
            is_extra=title.is_extra,
            match_details=title.match_details,
        )
        await self._check_job_completion(session, job_id)
        return True

    async def _handle_file_wait_result(
        self,
        wait_result: FileWaitResult,
        job_id: int,
        title_id: int,
        file_path: Path,
    ) -> bool:
        """Act on a `_wait_for_file_ready` outcome.

        Returns True if the title was routed to review/failed and the caller
        must stop processing it; False to proceed with matching. Any value that
        is not TRUNCATED or TIMEOUT (e.g. a legacy ``True`` from older test
        stubs) is treated as READY.
        """
        if wait_result == FileWaitResult.TRUNCATED:
            # Route to REVIEW with the rip-failure code so the title is
            # re-rippable (Feature C) and the rest of the disc can finish.
            await self.route_rip_failure_to_review(
                job_id, title_id, "incomplete_rip", INCOMPLETE_RIP_MESSAGE
            )
            return True

        if wait_result == FileWaitResult.TIMEOUT:
            logger.error(
                f"[MATCH] Title {title_id} (Job {job_id}): file never became ready, "
                f"skipping match for {file_path.name}"
            )
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.FAILED
                    session.add(title)
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id, title_id, TitleState.FAILED.value
                    )
                await self._check_job_completion(session, job_id)
            return True

        return False

    async def _wait_for_file_ready(
        self,
        file_path: Path,
        title_id: int,
        job_id: int,
        timeout: float | None = None,
    ) -> FileWaitResult:
        """Wait until a ripped file is finalized on disk.

        Returns READY when the file is complete (or has stopped growing at a
        plausible size), TRUNCATED when it has clearly stopped far below the
        scanned size (an aborted rip), or TIMEOUT if it never stabilized.
        """
        from app.services.config_service import get_config

        config = await get_config()
        check_interval = config.ripping_file_poll_interval
        required_stable = config.ripping_stability_checks

        # Expected (disc-scan estimate) size from the DB.
        expected_size = 0
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if title and title.file_size_bytes:
                expected_size = title.file_size_bytes

        if timeout is None:
            if expected_size > 0:
                base_timeout = (expected_size / (1024 * 1024)) * 2
                timeout = max(config.ripping_file_ready_timeout, base_timeout)
            else:
                timeout = config.ripping_file_ready_timeout

        last_size = -1
        stable_count = 0  # fast-path only (size_ratio >= READY_SIZE_RATIO)
        start = time.monotonic()
        last_growth_time = start  # slow-path: updated whenever file size changes

        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): waiting for file to finish "
            f"writing: {file_path.name} (expected ~{expected_size / 1024 / 1024:.0f} MB, "
            f"timeout {timeout:.0f}s)"
        )

        def _readable() -> bool:
            try:
                with open(file_path, "rb") as _f:
                    _f.read(1)
                return True
            except PermissionError:
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): size stable but file "
                    f"still locked ({file_path.name}) — waiting..."
                )
                return False

        async def _broadcast(progress: float, actual: int) -> None:
            await ws_manager.broadcast_title_update(
                job_id,
                title_id,
                TitleState.QUEUED.value,
                match_stage="waiting_for_file",
                match_progress=progress,
                expected_size_bytes=expected_size,
                actual_size_bytes=actual,
            )

        while time.monotonic() - start < timeout:
            if not file_path.exists():
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file not yet on disk, "
                    f"waiting... ({file_path.name})"
                )
                await _broadcast(0.0, 0)
                await asyncio.sleep(check_interval)
                continue

            try:
                current_size = file_path.stat().st_size
            except OSError as e:
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): cannot stat file ({e}), retrying..."
                )
                await asyncio.sleep(check_interval)
                continue

            if current_size > 0 and current_size == last_size:
                stable_count += 1
                size_ratio = current_size / expected_size if expected_size > 0 else 1.0
                elapsed_idle = time.monotonic() - last_growth_time

                if size_ratio >= READY_SIZE_RATIO:
                    size_info = f"check {stable_count}/{required_stable}"
                else:
                    size_info = f"{elapsed_idle:.0f}s/{TRUNCATED_STABLE_GRACE_SECONDS:.0f}s idle"
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file size stable "
                    f"({current_size / 1024 / 1024:.0f} MB) — {size_info}"
                    + (
                        f" — {size_ratio * 100:.1f}% of expected {expected_size / 1024 / 1024:.0f} MB"
                        if expected_size > 0
                        else ""
                    )
                )

                # Fast path: complete-enough and briefly stable.
                if stable_count >= required_stable and size_ratio >= READY_SIZE_RATIO:
                    if await asyncio.to_thread(_readable):
                        logger.info(
                            f"[MATCH] Title {title_id} (Job {job_id}): file ready "
                            f"({current_size / 1024 / 1024:.0f} MB, stable for "
                            f"{stable_count * check_interval:.0f}s): {file_path.name}"
                        )
                        return FileWaitResult.READY
                    stable_count = 0

                # Slow path: file hasn't grown for TRUNCATED_STABLE_GRACE_SECONDS.
                # Uses elapsed_idle (time since last write) so intermittent slow
                # writes by MakeMKV don't restart the 90-second window from scratch.
                elif elapsed_idle >= TRUNCATED_STABLE_GRACE_SECONDS:
                    if await asyncio.to_thread(_readable):
                        if expected_size > 0 and size_ratio < TRUNCATED_SIZE_RATIO:
                            logger.warning(
                                f"[MATCH] Title {title_id} (Job {job_id}): file stopped at "
                                f"{current_size / 1024 / 1024:.0f} MB "
                                f"({size_ratio * 100:.1f}% of expected "
                                f"{expected_size / 1024 / 1024:.0f} MB), {elapsed_idle:.0f}s "
                                f"since last write — treating as a truncated/incomplete rip: "
                                f"{file_path.name}"
                            )
                            return FileWaitResult.TRUNCATED
                        logger.warning(
                            f"[MATCH] Title {title_id} (Job {job_id}): file stable at "
                            f"{current_size / 1024 / 1024:.0f} MB "
                            + (
                                f"({size_ratio * 100:.1f}% of expected) "
                                if expected_size > 0
                                else ""
                            )
                            + f"{elapsed_idle:.0f}s since last write — proceeding "
                            f"with match: {file_path.name}"
                        )
                        return FileWaitResult.READY
                    # File still locked; last_growth_time unchanged — just wait for
                    # the lock to clear without restarting the grace window.
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): grace window elapsed but "
                        f"file still locked ({file_path.name}) — waiting for lock to clear..."
                    )
            else:
                last_growth_time = time.monotonic()
                if stable_count > 0:
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size changed "
                        f"({last_size} -> {current_size}), resetting stability counter"
                    )
                stable_count = 0

            last_size = current_size

            if expected_size > 0:
                wait_progress = min(99.0, (current_size / expected_size) * 100.0)
            else:
                wait_progress = min(99.0, (stable_count / required_stable) * 100.0)
            await _broadcast(wait_progress, current_size)

            await asyncio.sleep(check_interval)

        elapsed = time.monotonic() - start
        logger.warning(
            f"[MATCH] Title {title_id} (Job {job_id}): timed out waiting for file "
            f"after {elapsed:.0f}s: {file_path.name}"
        )
        return FileWaitResult.TIMEOUT

    def on_match_task_done(self, task: asyncio.Task, job_id: int, title_id: int) -> None:
        """Handle matching task completion/failure."""
        if task.cancelled():
            logger.warning(f"[MATCH] Title {title_id} (Job {job_id}): task cancelled")
            asyncio.ensure_future(self._handle_match_failure(job_id, title_id, "Task cancelled"))
        elif exc := task.exception():
            logger.error(
                f"[MATCH] Title {title_id} (Job {job_id}): task failed: {exc}",
                exc_info=exc,
            )
            asyncio.ensure_future(self._handle_match_failure(job_id, title_id, str(exc)))

    async def route_rip_failure_to_review(
        self, job_id: int, title_id: int, error_code: str, message: str
    ) -> None:
        """Route a rip-level failure (truncated/stall) to REVIEW, not FAILED.

        Writes a structured ``match_details`` carrying the error code, the
        current attempt count, and a ``rerip_eligible`` flag (False once the
        retry cap is reached). Keeping rip failures in REVIEW holds the job in
        REVIEW_NEEDED so COMPLETED means every title succeeded (Feature C).
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            active_states = (
                TitleState.PENDING,
                TitleState.RIPPING,
                TitleState.QUEUED,
                TitleState.MATCHING,
            )
            if title and title.state in active_states:
                attempts = title.rerip_attempts or 0
                eligible = attempts < RERIP_MAX_ATTEMPTS
                detail_msg = message
                if not eligible:
                    detail_msg = (
                        f"{message} Automatic re-rip stopped after {attempts} attempt(s) — "
                        "clean or replace the disc and use Re-rip, or skip this title."
                    )
                title.state = TitleState.REVIEW
                title.match_details = json.dumps(
                    {
                        "error": error_code,
                        "message": detail_msg,
                        "rerip_eligible": eligible,
                        "rerip_attempts": attempts,
                    }
                )
                session.add(title)
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_id,
                    title.state.value,
                    match_details=title.match_details,
                )
            await self._check_job_completion(session, job_id)

    async def _handle_match_failure(self, job_id: int, title_id: int, error: str) -> None:
        """Clean up after a matching task fails unexpectedly."""
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            active_states = (
                TitleState.PENDING,
                TitleState.RIPPING,
                TitleState.QUEUED,
                TitleState.MATCHING,
            )
            if title and title.state in active_states:
                title.state = TitleState.REVIEW
                title.match_details = json.dumps(
                    {"error": "matching_task_failed", "message": error}
                )
                session.add(title)
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_id,
                    title.state.value,
                    match_details=title.match_details,
                )
            await self._check_job_completion(session, job_id)

    async def download_subtitles_all_seasons(
        self, job_id: int, show_name: str, seasons: list[int], tmdb_id: int | None = None
    ) -> None:
        """Download references for every candidate season (unknown season).

        Seasons already covered by the precomputed cache or prior downloads are cheap
        no-ops. Results aggregate into a single ``subtitle_status`` ("completed" if any
        season yielded references, else "failed") and one ``_subtitle_ready`` event, so
        the existing per-job matching gate works without change. Failure BLOCKS matching.
        """
        from sqlalchemy import update

        try:
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="downloading")
                )
                await session.commit()

            logger.info(
                f"Starting background subtitle download for {show_name} across "
                f"seasons {seasons} (season unknown)"
            )
            await ws_manager.broadcast_subtitle_event(job_id, "downloading", downloaded=0, total=0)

            from app.matcher.testing_service import download_subtitles

            canonical_name: str | None = None
            downloaded_total = 0
            failed_total = 0
            episode_total = 0
            for season in seasons:
                try:
                    result = await asyncio.to_thread(
                        download_subtitles, show_name, season, tmdb_id=tmdb_id
                    )
                except Exception as e:  # noqa: BLE001 — one season failing must not abort the rest
                    logger.warning(f"Subtitle download failed for {show_name} S{season}: {e}")
                    continue

                episodes = result.get("episodes") or []
                downloaded_total += sum(
                    1 for ep in episodes if ep["status"] in ("downloaded", "cached", "precomputed")
                )
                failed_total += sum(1 for ep in episodes if ep["status"] in ("not_found", "failed"))
                episode_total += len(episodes)
                # First non-None canonical name wins (deterministic across seasons).
                if result.get("show_name") and canonical_name is None:
                    canonical_name = result["show_name"]
                # Per-season progress so the UI isn't silent during a cold multi-season fetch.
                await ws_manager.broadcast_subtitle_event(
                    job_id,
                    "downloading",
                    downloaded=downloaded_total,
                    total=episode_total,
                    failed_count=failed_total,
                )

            status = "completed" if downloaded_total > 0 else "failed"
            error_msg = None
            if status == "failed":
                error_msg = (
                    f"No reference subtitles found for '{show_name}' in any season. Episode "
                    "matching can't run without them — add an OpenSubtitles API key in Settings, "
                    "drop .srt files into the show's cache folder, or assign episodes manually "
                    "in Review."
                )

            async with async_session() as session:
                update_values: dict = {
                    "subtitle_status": status,
                    "subtitle_error_message": error_msg,
                }
                if canonical_name and canonical_name != show_name:
                    logger.info(f"Updating job {job_id} title to canonical: {canonical_name}")
                    update_values["detected_title"] = canonical_name
                await session.execute(
                    update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
                )
                await session.commit()

            await ws_manager.broadcast_subtitle_event(
                job_id,
                status,
                downloaded=downloaded_total,
                total=episode_total,
                failed_count=failed_total,
            )

        except Exception as e:
            logger.exception(
                f"Unexpected error in all-season subtitle download for {show_name}: {e}"
            )
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(
                        subtitle_status="failed",
                        subtitle_error_message=f"Download error: {str(e)}",
                    )
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        finally:
            if job_id in self._subtitle_ready:
                self._subtitle_ready[job_id].set()

    async def download_subtitles(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Download subtitles in background. Failure BLOCKS matching."""
        from sqlalchemy import update

        try:
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="downloading")
                )
                await session.commit()

            logger.info(f"Starting background subtitle download for {show_name} S{season}")
            await ws_manager.broadcast_subtitle_event(job_id, "downloading", downloaded=0, total=0)

            from app.matcher.testing_service import download_subtitles

            result = await asyncio.to_thread(download_subtitles, show_name, season, tmdb_id=tmdb_id)

            episodes = result["episodes"]
            # The precomputed vector cache covered the whole season, so no SRTs
            # were downloaded — matching will read those vectors directly.
            using_precomputed = bool(episodes) and all(
                ep["status"] == "precomputed" for ep in episodes
            )
            downloaded = sum(
                1 for ep in episodes if ep["status"] in ("downloaded", "cached", "precomputed")
            )
            failed = sum(1 for ep in episodes if ep["status"] in ("not_found", "failed"))
            total = len(episodes)

            status = "completed" if failed == 0 else ("partial" if downloaded > 0 else "failed")

            error_msg = None
            if status == "failed":
                error_msg = (
                    f"No reference subtitles found for '{show_name}'. Episode matching "
                    "can't run without them — add an OpenSubtitles API key in Settings, "
                    "drop .srt files into the show's cache folder, or assign episodes "
                    "manually in Review."
                )

            if using_precomputed:
                logger.info(
                    f"Subtitle references for {show_name} S{season} served from "
                    f"precomputed vector cache ({total} episodes); skipped download"
                )
            else:
                logger.info(
                    f"Subtitle download complete for {show_name} S{season}: "
                    f"{status} ({downloaded} downloaded/cached, {failed} failed)"
                )

            async with async_session() as session:
                # Always assign subtitle_error_message: the actionable string on
                # failure, None on success/partial so a stale banner from a prior
                # attempt is cleared. Kept off the catch-all error_message.
                update_values = {"subtitle_status": status, "subtitle_error_message": error_msg}

                if result.get("show_name") and result["show_name"] != show_name:
                    logger.info(f"Updating job {job_id} title to canonical: {result['show_name']}")
                    update_values["detected_title"] = result["show_name"]

                await session.execute(
                    update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
                )
                await session.commit()

            await ws_manager.broadcast_subtitle_event(
                job_id,
                status,
                downloaded=downloaded,
                total=total,
                failed_count=failed,
            )

        except Exception as e:
            if isinstance(e, ValueError):
                logger.error(
                    f"Subtitle download ValueError for {show_name} S{season}: {e}", exc_info=True
                )
                subtitle_error = str(e)
            else:
                logger.exception(
                    f"Unexpected error in subtitle download for {show_name} S{season}: {e}"
                )
                subtitle_error = f"Download error: {str(e)}"

            # Route subtitle-pipeline failures to subtitle_error_message — the
            # clearable field the success/restart paths reset — NOT the catch-all
            # error_message. Writing here keeps the banner from surviving a later
            # successful re-download (Mad Men S3 "O Hristos xanastavronetai"
            # regression: a stale show-not-found error stuck on error_message).
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="failed", subtitle_error_message=subtitle_error)
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        finally:
            if job_id in self._subtitle_ready:
                self._subtitle_ready[job_id].set()
