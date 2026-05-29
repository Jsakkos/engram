"""Matching Coordinator - Episode matching, subtitle download, and DiscDB assignment.

Extracted from JobManager to isolate matching concerns.
"""

import asyncio
import json
import logging
import time
from datetime import UTC
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.curator import curator as episode_curator
from app.core.errors import MatchingError
from app.core.log_context import job_log_context
from app.database import async_session
from app.models import DiscJob
from app.models.disc_job import DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import find_staging_file

logger = logging.getLogger(__name__)

# Stricter matcher parameters for the "deep re-match" conflict path: sample more
# audio chunks (vs the default 10) for more robust votes + a clearer score gap,
# and require more matched chunks before accepting (vs the default 2).
STRICT_SCAN_POINTS = 25
STRICT_MIN_VOTES = 4

# Module-level cache for fpcalc detection results.
# `detect_fpcalc()` is deterministic within a process (the binary doesn't appear
# or disappear mid-run), but each invocation runs up to 6 subprocess probes with
# 10s timeouts each. Without caching, a 22-title disc with semaphore=3 would
# trigger up to 66 parallel probe storms. `None` means "no path detected".
# Sentinel value `_UNSET` distinguishes "haven't tried yet" from "tried and got None".
_UNSET: object = object()
_fpcalc_path_cache: str | None | object = _UNSET


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

    def start_subtitle_download(self, job_id: int, show_name: str, season: int) -> None:
        """Start background subtitle download with tracking."""
        self._subtitle_ready[job_id] = asyncio.Event()
        self._subtitle_tasks[job_id] = asyncio.create_task(
            self.download_subtitles(job_id, show_name, season)
        )

    async def restart_subtitle_download(self, job_id: int, show_name: str, season: int) -> None:
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

        self.start_subtitle_download(job_id, show_name, season)

    async def try_discdb_assignment(self, job_id: int, title: "DiscTitle", session) -> bool:
        """Try to assign episode info from TheDiscDB mappings, skipping fingerprinting.

        Returns True if assignment was made, False to fall back to audio matching.
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

        episode_code = f"S{mapping.season:02d}E{mapping.episode:02d}"
        logger.info(
            f"Job {job_id}: TheDiscDB pre-assigned title {title.title_index} "
            f"→ {episode_code} ({mapping.episode_title!r}) — skipping audio matching"
        )

        title.matched_episode = episode_code
        title.match_confidence = 0.99
        title.match_details = json.dumps(
            {
                "source": "discdb",
                "episode_title": mapping.episode_title,
                "matched_episode": episode_code,
            }
        )
        title.match_source = "discdb"
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
    ) -> None:
        """Re-match a single title with the specified source preference.

        source_preference:
            "discdb" — restore from stored discdb_match_details
            "engram" — clear match and re-run audio fingerprinting
            None — try discdb first if available, else engram

        ``num_points``/``min_vote_count`` override the matcher scan density and
        vote gate for the engram path (deep re-match); None keeps defaults.
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
            self.match_single_file(job_id, title_id, file_path, num_points, min_vote_count)
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
    ) -> None:
        """Run matching for a single ripped file, tagging logs with the job id.

        Self-tags so every matching log line carries ``job=<id>`` regardless of
        how this is reached — task spawn, direct ``await`` from an API handler
        (deep re-match / conflict resolution), or via the injected callback used
        by the identification/finalization coordinators.
        """
        with job_log_context(job_id):
            await self._run_match_single_file(
                job_id, title_id, file_path, num_points, min_vote_count
            )

    async def _run_match_single_file(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
    ) -> None:
        """Run matching for a single ripped file.

        ``num_points``/``min_vote_count`` override the matcher's scan density and
        vote gate (deep re-match); None keeps defaults.
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

        # Gate matching based on subtitle status
        if subtitle_status == "failed":
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitle download failed. "
                f"No reference files available. Title needs manual episode assignment."
            )
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title:
                    title.state = TitleState.REVIEW
                    title.match_confidence = 0.0
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
        file_ready = await self._wait_for_file_ready(file_path, title_id, job_id)
        if not file_ready:
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
                await self._check_job_completion(session, job_id)
            return

        # 4. Duration pre-filter
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            title = await session.get(DiscTitle, title_id)
            if job and title and job.detected_season:
                try:
                    if job_id not in self._episode_runtimes:
                        from app.matcher.tmdb_client import (
                            fetch_season_episode_runtimes,
                            fetch_show_id,
                        )

                        show_id = await asyncio.to_thread(fetch_show_id, job.detected_title)
                        if show_id:
                            runtimes = await asyncio.to_thread(
                                fetch_season_episode_runtimes,
                                show_id,
                                job.detected_season,
                            )
                            self._episode_runtimes[job_id] = runtimes
                        else:
                            self._episode_runtimes[job_id] = []

                    runtimes = self._episode_runtimes.get(job_id, [])
                    if runtimes and title.duration_seconds:
                        title_minutes = title.duration_seconds / 60
                        tolerance = 5  # minutes
                        matches_any = any(abs(title_minutes - rt) <= tolerance for rt in runtimes)
                        if not matches_any:
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

        # 7. Run matching
        try:
            await self._match_single_file_inner(
                job_id, title_id, file_path, num_points, min_vote_count
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

    async def _match_single_file_inner(
        self,
        job_id: int,
        title_id: int,
        file_path: Path,
        num_points: int | None = None,
        min_vote_count: int | None = None,
    ) -> None:
        """Inner matching logic, called under the match semaphore."""
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
                result = await episode_curator.match_single_file(
                    file_path,
                    series_name=job.detected_title,
                    season=job.detected_season,
                    progress_callback=on_progress,
                    num_points=num_points,
                    min_vote_count=min_vote_count,
                )

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

                # Update title with match result
                title.matched_episode = result.episode_code
                title.match_confidence = result.confidence

                if result.needs_review:
                    # A low-confidence result always goes to REVIEW — even when the
                    # matcher emits a best-guess episode code. Auto-organizing a
                    # borderline guess silently mis-files content (e.g. a bonus
                    # track that slipped past the duration pre-filter lands on the
                    # wrong episode instead of being flagged as an extra). The
                    # auto review-escalation deep re-matches it next; if it still
                    # can't resolve, the user decides. matched_episode is kept as
                    # the inspector's starting suggestion.
                    title.state = TitleState.REVIEW

                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): needs review — "
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
                            extractor = ChromaprintExtractor(fpcalc_path=fpcalc_path)
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
            f"doesn't match any episode runtime {runtimes} (±5min). "
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

        # Default: "keep" — organize to extras folder
        from app.core.organizer import organize_tv_extras

        # Count existing extras for this job to determine index
        extras_count = 0
        all_titles = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
        for t in all_titles.scalars():
            if t.match_details:
                try:
                    details = json.loads(t.match_details)
                    if details.get("auto_sorted") == "extras":
                        extras_count += 1
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

        extra_index = extras_count + 1

        org_result = await asyncio.to_thread(
            organize_tv_extras,
            file_path,
            job.detected_title,
            job.detected_season,
            None,
            job.disc_number,
            extra_index,
            title.title_index,
        )
        if org_result["success"]:
            title.state = TitleState.COMPLETED
            title.organized_from = file_path.name
            title.organized_to = (
                str(org_result.get("final_path")) if org_result.get("final_path") else None
            )
            title.is_extra = True
            title.match_details = json.dumps(
                {
                    "auto_sorted": "extras",
                    "action": "kept",
                    "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
                }
            )
        else:
            title.state = TitleState.COMPLETED
            # The duration pre-filter already classified this as an extra; the
            # only thing that failed is the file move (e.g. destination exists
            # from a previous rip). Tag it so the UI still shows EXTRA — without
            # this, a re-run hides the chip on every collided extra.
            title.is_extra = True
            title.match_details = json.dumps(
                {
                    "auto_sorted": "extras",
                    "action": "kept",
                    "organize_error": org_result["error"],
                }
            )
            logger.warning(
                f"[MATCH] Title {title_id}: extras organize failed: {org_result['error']}"
            )
        session.add(title)
        await session.commit()
        await ws_manager.broadcast_title_update(
            job_id,
            title.id,
            title.state.value,
            organized_from=title.organized_from,
            organized_to=title.organized_to,
            output_filename=title.output_filename,
            is_extra=title.is_extra,
            match_details=title.match_details,
        )
        await self._check_job_completion(session, job_id)
        return True

    async def _wait_for_file_ready(
        self,
        file_path: Path,
        title_id: int,
        job_id: int,
        timeout: float | None = None,
    ) -> bool:
        """Wait until a ripped file is fully written and ready for processing."""
        from app.services.config_service import get_config

        config = await get_config()
        check_interval = config.ripping_file_poll_interval
        required_stable = config.ripping_stability_checks

        # Get expected size from DB
        expected_size = 0
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if title and title.file_size_bytes:
                expected_size = title.file_size_bytes

        # Calculate dynamic timeout based on file size
        if timeout is None:
            if expected_size > 0:
                base_timeout = (expected_size / (1024 * 1024)) * 2
                timeout = max(config.ripping_file_ready_timeout, base_timeout)
            else:
                timeout = config.ripping_file_ready_timeout

        last_size = -1
        stable_count = 0
        start = time.monotonic()

        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): waiting for file to finish "
            f"writing: {file_path.name} (expected ~{expected_size / 1024 / 1024:.0f} MB, "
            f"timeout {timeout:.0f}s)"
        )

        while time.monotonic() - start < timeout:
            if not file_path.exists():
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file not yet on disk, "
                    f"waiting... ({file_path.name})"
                )
                await asyncio.sleep(check_interval)
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_id,
                    TitleState.RIPPING.value,
                    match_stage="waiting_for_file",
                    match_progress=0.0,
                    expected_size_bytes=expected_size,
                    actual_size_bytes=0,
                )
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

                size_matches_expected = True
                if expected_size > 0:
                    size_ratio = current_size / expected_size
                    size_matches_expected = size_ratio >= 0.85

                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file size stable "
                    f"({current_size / 1024 / 1024:.0f} MB) — check {stable_count}/{required_stable}"
                    + (
                        f" — size {size_ratio * 100:.1f}% of expected {expected_size / 1024 / 1024:.0f} MB"
                        if expected_size > 0
                        else ""
                    )
                )

                if stable_count >= required_stable and size_matches_expected:
                    try:
                        with open(file_path, "rb") as _f:
                            _f.read(1)
                    except PermissionError:
                        logger.debug(
                            f"[MATCH] Title {title_id} (Job {job_id}): size stable but "
                            f"file still locked ({file_path.name}) — waiting..."
                        )
                        stable_count = 0
                        await asyncio.sleep(check_interval)
                        continue
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): file ready "
                        f"({current_size / 1024 / 1024:.0f} MB, stable for "
                        f"{stable_count * check_interval:.0f}s): {file_path.name}"
                    )
                    return True
                elif stable_count >= required_stable and not size_matches_expected:
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size stable but only "
                        f"{current_size / 1024 / 1024:.0f} MB of expected "
                        f"{expected_size / 1024 / 1024:.0f} MB ({size_ratio * 100:.1f}%) — still writing"
                    )
                    stable_count = 0
            else:
                if stable_count > 0:
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size changed "
                        f"({last_size} -> {current_size}), resetting stability counter"
                    )
                stable_count = 0

            last_size = current_size

            # Broadcast wait progress
            if expected_size > 0:
                wait_progress = min(99.0, (current_size / expected_size) * 100.0)
            else:
                wait_progress = min(99.0, (stable_count / required_stable) * 100.0)

            await ws_manager.broadcast_title_update(
                job_id,
                title_id,
                TitleState.RIPPING.value,
                match_stage="waiting_for_file",
                match_progress=wait_progress,
                expected_size_bytes=expected_size,
                actual_size_bytes=current_size,
            )

            await asyncio.sleep(check_interval)

        elapsed = time.monotonic() - start
        logger.warning(
            f"[MATCH] Title {title_id} (Job {job_id}): timed out waiting for file "
            f"after {elapsed:.0f}s: {file_path.name}"
        )
        return False

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

    async def _handle_match_failure(self, job_id: int, title_id: int, error: str) -> None:
        """Clean up after a matching task fails unexpectedly."""
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            active_states = (
                TitleState.PENDING,
                TitleState.RIPPING,
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

    async def download_subtitles(self, job_id: int, show_name: str, season: int) -> None:
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

            result = await asyncio.to_thread(download_subtitles, show_name, season)

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
                logger.error(f"Subtitle download ValueError for {show_name} S{season}: {e}")
                error_message = str(e)
            else:
                logger.exception(
                    f"Unexpected error in subtitle download for {show_name} S{season}: {e}"
                )
                error_message = f"Download error: {str(e)}"

            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="failed", error_message=error_message)
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        finally:
            if job_id in self._subtitle_ready:
                self._subtitle_ready[job_id].set()
