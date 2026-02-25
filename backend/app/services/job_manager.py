"""Job Manager - Orchestrates the disc processing workflow.

Coordinates between the Sentinel, Analyst, Extractor, and Curator modules.
"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.analyst import DiscAnalyst
from app.core.curator import curator as episode_curator
from app.core.errors import MatchingError
from app.core.extractor import MakeMKVExtractor, RipProgress
from app.core.organizer import movie_organizer
from app.core.sentinel import DriveMonitor
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine

logger = logging.getLogger(__name__)

# Create domain-specific event broadcaster
event_broadcaster = EventBroadcaster(ws_manager)

# Create job state machine
state_machine = JobStateMachine(event_broadcaster)


class SpeedCalculator:
    """Calculates transfer speed and ETA."""

    def __init__(self, total_bytes: int) -> None:
        self._total_bytes = total_bytes
        self._start_time = time.time()
        self._last_update = self._start_time
        self._bytes_history = deque(maxlen=10)
        self._time_history = deque(maxlen=10)
        self._current_speed: float = 0.0

    def update(self, current_bytes: int) -> None:
        now = time.time()
        if self._bytes_history and (now - self._last_update < 0.5):
            return

        self._bytes_history.append(current_bytes)
        self._time_history.append(now)

        if len(self._bytes_history) > 1:
            bytes_diff = self._bytes_history[-1] - self._bytes_history[0]
            time_diff = self._time_history[-1] - self._time_history[0]
            if time_diff > 0:
                self._current_speed = bytes_diff / time_diff

        self._last_update = now

    @property
    def speed_str(self) -> str:
        if self._current_speed == 0:
            return "0.0x (0.0 M/s)"
        mb_s = self._current_speed / (1024 * 1024)
        x_speed = mb_s / 4.5
        return f"{x_speed:.1f}x ({mb_s:.1f} M/s)"

    @property
    def eta_seconds(self) -> int:
        if self._current_speed == 0:
            return 0
        if self._bytes_history:
            current = self._bytes_history[-1]
            remaining = max(0, self._total_bytes - current)
            return int(remaining / self._current_speed)
        return 0


class JobManager:
    """Manages the lifecycle of disc processing jobs."""

    def __init__(self) -> None:
        self._drive_monitor = DriveMonitor()
        self._extractor = MakeMKVExtractor()
        self._analyst = DiscAnalyst()
        self._active_jobs: dict[int, asyncio.Task] = {}
        self._subtitle_tasks: dict[int, asyncio.Task] = {}
        self._subtitle_ready: dict[int, asyncio.Event] = {}
        self._episode_runtimes: dict[int, list[int]] = {}  # job_id → episode runtimes in minutes
        self._loop: asyncio.AbstractEventLoop | None = None
        self._match_semaphore: asyncio.Semaphore | None = None

    async def start(self) -> None:
        """Start the job manager and begin monitoring drives."""
        self._loop = asyncio.get_event_loop()

        # Set up drive monitor callback
        self._drive_monitor.set_async_callback(
            self._on_drive_event,
            self._loop,
        )
        self._drive_monitor.start()

        # Ensure required directories exist
        from app.services.config_service import ensure_paths_exist, get_config

        config = await get_config()
        await ensure_paths_exist(config)

        # Initialize matching concurrency limiter (guard against invalid DB values)
        concurrency = max(1, config.max_concurrent_matches)
        if concurrency != config.max_concurrent_matches:
            logger.warning(
                f"Invalid max_concurrent_matches={config.max_concurrent_matches} "
                f"in config, using {concurrency}"
            )
        self._match_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"Job manager started (max_concurrent_matches={concurrency})")

    async def stop(self) -> None:
        """Stop the job manager and clean up."""
        self._drive_monitor.stop()

        # Cancel all active jobs
        for job_id, task in self._active_jobs.items():
            task.cancel()
            logger.info(f"Cancelled job {job_id}")

        self._active_jobs.clear()
        logger.info("Job manager stopped")

    async def _on_drive_event(
        self,
        drive_letter: str,
        event: str,
        volume_label: str,
    ) -> None:
        """Handle drive insertion/removal events from the Sentinel."""
        logger.info(f"Drive event: {drive_letter} {event} (label: {volume_label})")

        if event == "inserted":
            await self._create_job_for_disc(drive_letter, volume_label)
        elif event == "removed":
            # Cancel any active job for this drive
            await self._cancel_jobs_for_drive(drive_letter)

        # Broadcast to WebSocket clients AFTER processing
        # This ensures that if a job was created, the client can fetch it immediately
        if event == "inserted":
            await event_broadcaster.broadcast_drive_inserted(drive_letter, volume_label)
        else:
            await event_broadcaster.broadcast_drive_removed(drive_letter, volume_label)

    async def _create_job_for_disc(self, drive_letter: str, volume_label: str) -> None:
        """Create a new job when a disc is inserted."""
        async with async_session() as session:
            # Check if there's already an active job for this drive
            result = await session.execute(
                select(DiscJob).where(
                    DiscJob.drive_id == drive_letter,
                    DiscJob.state.not_in(
                        [JobState.COMPLETED, JobState.FAILED, JobState.REVIEW_NEEDED]
                    ),
                )
            )
            existing_job = result.scalar_one_or_none()

            if existing_job:
                logger.info(f"Job already exists for drive {drive_letter}")
                return

            # Create staging directory for this job
            from app.services.config_service import get_config as get_db_config

            db_config = await get_db_config()
            staging_dir = (
                Path(db_config.staging_path).expanduser()
                / f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

            # Create new job
            job = DiscJob(
                drive_id=drive_letter,
                volume_label=volume_label,
                staging_path=str(staging_dir),
                state=JobState.IDENTIFYING,
            )

            session.add(job)
            await session.commit()
            await session.refresh(job)

            logger.info(f"Created job {job.id} for disc in {drive_letter}")

            # Start identification in background
            task = asyncio.create_task(self._identify_disc(job.id))
            self._active_jobs[job.id] = task

    async def _identify_disc(self, job_id: int) -> None:
        """Identify the disc contents using MakeMKV and the Analyst."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            try:
                # Scan disc with MakeMKV
                await event_broadcaster.broadcast_job_state_changed(job_id, JobState.IDENTIFYING)

                titles = await self._extractor.scan_disc(job.drive_id)

                if not titles:
                    await state_machine.transition_to_failed(
                        job, session, "No titles found on disc or MakeMKV error"
                    )
                    return

                # Attempt TMDB lookup for classification signal
                tmdb_signal = None
                detected_name, _, _ = DiscAnalyst._parse_volume_label(job.volume_label)
                if detected_name:
                    try:
                        from app.core.tmdb_classifier import classify_from_tmdb
                        from app.services.config_service import get_config_sync

                        config = get_config_sync()
                        if config.tmdb_api_key:
                            tmdb_signal = classify_from_tmdb(detected_name, config.tmdb_api_key)
                            if tmdb_signal:
                                logger.info(
                                    f"Job {job_id}: TMDB signal: "
                                    f"{tmdb_signal.content_type.value} "
                                    f"({tmdb_signal.confidence:.0%}) - "
                                    f"{tmdb_signal.tmdb_name}"
                                )
                    except Exception as e:
                        logger.warning(
                            f"Job {job_id}: TMDB lookup failed, using heuristics only: {e}"
                        )

                # Analyze disc content
                analysis = self._analyst.analyze(titles, job.volume_label, tmdb_signal=tmdb_signal)
                logger.info(f"Job {job_id} Analysis Result: {analysis}")

                # Update job with analysis results
                job.content_type = analysis.content_type
                job.detected_title = analysis.detected_name
                job.detected_season = analysis.detected_season
                job.total_titles = len(titles)
                job.updated_at = datetime.utcnow()

                # Extract disc number from volume label (e.g., "SHOW_S01D2" -> 2)
                import re

                disc_match = re.search(r"d(?:isc)?[_\s]*(\d+)", job.volume_label, re.IGNORECASE)
                if disc_match:
                    job.disc_number = int(disc_match.group(1))
                    logger.info(
                        f"Detected disc number: {job.disc_number} from volume label: {job.volume_label}"
                    )
                else:
                    job.disc_number = 1  # Default per user preference
                    logger.info("No disc number detected in volume label, defaulting to 1")

                # Clear any existing titles for this job (e.g. from a previous scan)
                from sqlalchemy import delete

                await session.execute(delete(DiscTitle).where(DiscTitle.job_id == job_id))

                # Save title information
                for title in titles:
                    disc_title = DiscTitle(
                        job_id=job_id,
                        title_index=title.index,
                        duration_seconds=title.duration_seconds,
                        file_size_bytes=title.size_bytes,
                        chapter_count=title.chapter_count,
                        video_resolution=title.video_resolution,
                    )
                    session.add(disc_title)

                # For TV discs, deselect "Play All" concatenation titles
                if analysis.content_type == ContentType.TV and analysis.play_all_title_indices:
                    await session.flush()  # Ensure titles have IDs
                    play_all_set = set(analysis.play_all_title_indices)
                    stmt = select(DiscTitle).where(DiscTitle.job_id == job_id)
                    db_titles_for_filter = (await session.execute(stmt)).scalars().all()

                    deselected = 0
                    for dt in db_titles_for_filter:
                        if dt.title_index in play_all_set:
                            dt.is_selected = False
                            deselected += 1
                            logger.info(
                                f"Job {job_id}: Deselected 'Play All' title {dt.title_index} "
                                f"({dt.duration_seconds // 60}min)"
                            )

                    if deselected:
                        logger.info(f"Job {job_id}: Deselected {deselected} 'Play All' title(s)")

                # Broadcast titles discovered with full metadata
                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                title_list = [
                    {
                        "id": dt.id,
                        "title_index": dt.title_index,
                        "duration_seconds": dt.duration_seconds,
                        "file_size_bytes": dt.file_size_bytes,
                        "chapter_count": dt.chapter_count,
                        "video_resolution": dt.video_resolution,
                    }
                    for dt in titles_result.scalars().all()
                ]
                await ws_manager.broadcast_titles_discovered(
                    job_id,
                    title_list,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                )

                # If no title could be determined (e.g. generic volume label like LOGICAL_VOLUME_ID),
                # block ripping and ask the user to supply a name.
                if not job.detected_title:
                    await state_machine.transition_to_review(
                        job,
                        session,
                        reason="Disc label unreadable. Please enter the title to continue.",
                        broadcast=False,
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=job.content_type.value if job.content_type else None,
                        total_titles=job.total_titles,
                        review_reason="Disc label unreadable. Please enter the title to continue.",
                    )
                    logger.info(
                        f"Job {job_id}: no title detected (volume label: '{job.volume_label}'), "
                        f"waiting for user to supply name"
                    )
                    return

                # Start subtitle download for ALL TV content (regardless of review status)
                if (
                    job.content_type == ContentType.TV
                    and job.detected_title
                    and job.detected_season
                ):
                    # Start background subtitle download with tracking
                    self._subtitle_ready[job_id] = asyncio.Event()
                    self._subtitle_tasks[job_id] = asyncio.create_task(
                        self._download_subtitles(job_id, job.detected_title, job.detected_season)
                    )
                    logger.info(
                        f"Job {job_id}: starting subtitle download for "
                        f"{job.detected_title} S{job.detected_season}"
                    )

                if analysis.needs_review:
                    # Special handling for Ambiguous Movies (Multiple Feature-Length Titles)
                    # "Rip First, Review Later" workflow
                    is_ambiguous_movie = (
                        job.content_type == ContentType.MOVIE
                        and "Multiple long titles found" in (analysis.review_reason or "")
                    )

                    if is_ambiguous_movie:
                        logger.info(
                            f"Job {job_id}: Ambiguous movie detected. Auto-ripping candidates for later review."
                        )

                        # Select all long titles (candidates) for ripping
                        # We need to re-fetch the titles we just added to session?
                        # We just added them to session but didn't commit?
                        # Wait, the code above adds them to session: `session.add(disc_title)`
                        # We can iterate over `titles` (the MakeMKV TitleInfo objects) and match by index?
                        # Or just query them back.

                        # Let's use the `titles` list we have from scan_disc and the session objects
                        # Actually, we haven't committed `disc_title` inserts yet.
                        # We can iterate through the session.new objects? or just use a query after flush.

                        # Commit first to save titles
                        await session.commit()

                        # Fetch back to update is_selected
                        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
                        db_titles = (await session.execute(statement)).scalars().all()

                        candidate_count = 0
                        for dt in db_titles:
                            # Heuristic: Select titles > 80 mins (same as Analyst)
                            if dt.duration_seconds and dt.duration_seconds >= 80 * 60:
                                dt.is_selected = True
                                candidate_count += 1
                                session.add(dt)

                        await session.commit()

                        # Broadcast title updates so UI shows them selected?
                        # Actually broadcast_titles_discovered is called above, but before selection.
                        # The UI might need an update?
                        # We can send a job update.

                        job.state = JobState.RIPPING
                        await session.commit()
                        await ws_manager.broadcast_job_update(
                            job_id,
                            job.state.value,
                            content_type=job.content_type.value,
                            detected_title=job.detected_title,
                        )

                        # Run ripping
                        await self._run_ripping(job_id)
                        return

                    await state_machine.transition_to_review(
                        job, session, reason=analysis.review_reason, broadcast=False
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        job.state.value,
                        content_type=job.content_type.value,
                        detected_title=job.detected_title,
                        detected_season=job.detected_season,
                        total_titles=job.total_titles,
                    )
                    logger.info(f"Job {job_id} needs review: {analysis.review_reason}")
                else:
                    # High-confidence detection - auto-start ripping
                    job.state = JobState.RIPPING
                    await session.commit()
                    await ws_manager.broadcast_job_update(
                        job_id,
                        job.state.value,
                        content_type=job.content_type.value,
                        detected_title=job.detected_title,
                        detected_season=job.detected_season,
                        total_titles=job.total_titles,
                    )

                    logger.info(
                        f"Job {job_id} identified as {analysis.content_type.value} "
                        f"(confidence: {analysis.confidence:.1%}) - auto-starting rip"
                    )

                    # Run ripping directly (no need to create a new task)
                    await self._run_ripping(job_id)
                    return  # Exit early since ripping handles the rest

            except Exception as e:
                logger.exception(f"Error identifying disc for job {job_id}")
                await state_machine.transition_to_failed(job, session, str(e))

    async def set_name_and_resume(
        self,
        job_id: int,
        name: str,
        content_type_str: str,
        season: int | None = None,
    ) -> None:
        """Set a user-provided name for an unlabeled disc and resume ripping.

        Called when a disc had no readable volume label and the user has entered
        the title manually via the NamePromptModal.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state != JobState.REVIEW_NEEDED:
                raise ValueError(f"Cannot set name on job in state: {job.state}")

            job.detected_title = name
            job.content_type = ContentType(content_type_str)
            if season is not None:
                job.detected_season = season
            job.state = JobState.RIPPING
            job.updated_at = datetime.utcnow()
            await session.commit()

            await ws_manager.broadcast_job_update(
                job_id,
                JobState.RIPPING.value,
                content_type=job.content_type.value,
                detected_title=job.detected_title,
                detected_season=job.detected_season,
            )

            logger.info(
                f"Job {job_id}: user set name to '{name}' ({content_type_str}), resuming rip"
            )

        task = asyncio.create_task(self._run_ripping(job_id))
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def start_ripping(self, job_id: int) -> None:
        """Start the ripping process for a job."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state not in (JobState.IDLE, JobState.REVIEW_NEEDED):
                raise ValueError(f"Cannot start job in state: {job.state}")

            job.state = JobState.RIPPING
            job.updated_at = datetime.utcnow()
            await session.commit()

            # Start ripping in background
            task = asyncio.create_task(self._run_ripping(job_id))
            task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
            self._active_jobs[job_id] = task

    async def _check_job_completion(self, session, job_id: int):
        """Check if all titles in a job are processed, and if so, finalize."""
        # Expire cached objects to ensure fresh reads from DB
        session.expire_all()

        job = await session.get(DiscJob, job_id)
        if not job:
            return

        # Check for any active titles
        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
        result = await session.execute(statement)
        titles = result.scalars().all()

        active_states = [TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING]
        active_titles = [t for t in titles if t.state in active_states]

        # ── Diagnostic: log ALL title states every time this is called ──
        state_summary = ", ".join(
            f"t{t.title_index}={t.state.value}" for t in sorted(titles, key=lambda x: x.title_index)
        )
        logger.info(
            f"[COMPLETION-CHECK] Job {job_id}: {len(active_titles)} active / {len(titles)} total — [{state_summary}]"
        )

        if active_titles:
            logger.debug(
                f"Job {job_id}: {len(active_titles)} still active "
                f"({', '.join(f'{t.id}:{t.state.value}' for t in active_titles[:5])})"
            )
            return

        # All titles are terminal (COMPLETED, FAILED, MATCHED, or REVIEW)
        logger.info(f"All titles for job {job_id} effectively processed. Finalizing...")

        has_matched = any(t.state == TitleState.MATCHED for t in titles)
        has_review = any(t.state == TitleState.REVIEW for t in titles)
        has_completed = any(t.state == TitleState.COMPLETED for t in titles)
        all_failed = all(t.state == TitleState.FAILED for t in titles)

        if has_matched:
            # Run conflict resolution and organization
            try:
                await self._finalize_disc_job(job_id)
            except Exception as e:
                logger.exception(f"Job {job_id}: _finalize_disc_job failed: {e}")
                await state_machine.transition_to_failed(
                    job, session, error_message=f"Finalization failed: {e}"
                )
        elif has_review:
            # No matched titles but some need review → send to review
            await state_machine.transition_to_review(
                job,
                session,
                reason=f"{sum(1 for t in titles if t.state == TitleState.REVIEW)} title(s) need manual episode assignment",
            )
        elif all_failed and not has_completed:
            # Only FAILED if ALL titles failed (actual errors)
            await state_machine.transition_to_failed(
                job, session, error_message="All titles failed to process"
            )
        else:
            # Some completed, some failed — still mark completed
            job.progress_percent = 100.0
            await state_machine.transition_to_completed(job, session)

    async def _finalize_disc_job(self, job_id: int):
        """
        Run conflict resolution with cascading reassignment and organize matches.

        1. Group MATCHED titles by episode code
        2. For conflicts, pick winner via ranked voting
        3. Losers try runner-up episodes (cascading reassignment, max 3 rounds)
        4. Organize all resolved winners
        5. Unresolvable losers → REVIEW (not FAILED)
        6. Set job state based on final title states
        """
        from app.core.organizer import tv_organizer

        logger.info(f"Running conflict resolution for Job {job_id}")

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
            titles = (await session.execute(statement)).scalars().all()

            # Helper: get source file path for a title
            def _find_source_file(title):
                # Use output_filename if available
                if title.output_filename:
                    p = Path(title.output_filename)
                    if p.exists():
                        return p
                # Fallback: glob staging dir
                staging_path = Path(job.staging_path)
                matches = list(staging_path.glob(f"*_t{title.title_index:02d}.mkv"))
                return matches[0] if matches else None

            # Helper: extract match metrics from a title
            def _get_metrics(t):
                score = 0.0
                vote_count = 0
                file_cov = 0.0
                runner_ups = []
                if t.match_details:
                    try:
                        details = json.loads(t.match_details)
                        score = details.get("score", 0.0)
                        vote_count = details.get("vote_count", 0)
                        file_cov = details.get("file_cov", 0.0)
                        runner_ups = details.get("runner_ups", [])
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        logger.debug(f"Could not parse match_details JSON: {e}")
                        pass
                if score == 0.0:
                    score = t.match_confidence
                return score, vote_count, file_cov, runner_ups

            # Iterative conflict resolution (max 3 rounds)
            for round_num in range(3):
                # Group MATCHED titles by episode code
                candidates = {}
                for t in titles:
                    if t.state == TitleState.MATCHED and t.matched_episode:
                        candidates.setdefault(t.matched_episode, []).append(t)

                # Find conflicts
                conflicts = {ep: tlist for ep, tlist in candidates.items() if len(tlist) > 1}
                if not conflicts:
                    logger.info(
                        f"Conflict resolution round {round_num + 1}: no conflicts remaining"
                    )
                    break

                logger.info(
                    f"Conflict resolution round {round_num + 1}: "
                    f"{len(conflicts)} episode(s) have conflicts"
                )

                reassigned_any = False
                for ep_code, title_list in conflicts.items():
                    logger.info(f"Conflict for {ep_code}: titles {[t.id for t in title_list]}")

                    # Rank candidates
                    ranked = []
                    for t in title_list:
                        score, vote_count, file_cov, runner_ups = _get_metrics(t)
                        ranked.append(
                            {
                                "title": t,
                                "score": score,
                                "vote_count": vote_count,
                                "file_coverage": file_cov,
                                "runner_ups": runner_ups,
                            }
                        )

                    ranked.sort(
                        key=lambda x: (x["vote_count"], x["score"], x["file_coverage"]),
                        reverse=True,
                    )

                    winner = ranked[0]
                    logger.info(
                        f"  Winner: Title {winner['title'].id} "
                        f"(votes={winner['vote_count']}, score={winner['score']:.3f})"
                    )

                    # Try to reassign losers to runner-up episodes
                    for cand in ranked[1:]:
                        loser = cand["title"]
                        reassigned = False

                        for ru in cand["runner_ups"]:
                            alt_ep = ru["episode"]
                            # Check if alt episode is unclaimed or this loser beats current claimant
                            current_claimants = candidates.get(alt_ep, [])
                            if not current_claimants:
                                # Unclaimed episode — reassign
                                loser.matched_episode = alt_ep
                                loser.match_confidence = ru["score"]
                                candidates.setdefault(alt_ep, []).append(loser)
                                reassigned = True
                                reassigned_any = True
                                logger.info(
                                    f"  Reassigned Title {loser.id}: {ep_code} -> {alt_ep} "
                                    f"(runner-up score={ru['score']:.3f})"
                                )
                                break
                            elif len(current_claimants) == 1:
                                # Check if loser beats current claimant
                                claimant = current_claimants[0]
                                claimant_score, _, _, _ = _get_metrics(claimant)
                                if ru["score"] > claimant_score:
                                    loser.matched_episode = alt_ep
                                    loser.match_confidence = ru["score"]
                                    candidates[alt_ep].append(loser)
                                    reassigned = True
                                    reassigned_any = True
                                    logger.info(
                                        f"  Reassigned Title {loser.id}: {ep_code} -> {alt_ep} "
                                        f"(beats claimant {claimant.id}: {ru['score']:.3f} > {claimant_score:.3f})"
                                    )
                                    break

                        if not reassigned:
                            # No viable alternative — mark for review
                            loser.state = TitleState.REVIEW
                            if loser.match_details:
                                try:
                                    details = json.loads(loser.match_details)
                                    details["conflict_reason"] = (
                                        f"Lost conflict for {ep_code}, no viable runner-up"
                                    )
                                    loser.match_details = json.dumps(details)
                                except (json.JSONDecodeError, KeyError, TypeError) as e:
                                    logger.debug(f"Could not update conflict details: {e}")
                                    pass
                            session.add(loser)
                            logger.info(
                                f"  Title {loser.id}: no viable alternative, marked for REVIEW"
                            )

                if not reassigned_any:
                    break  # Stable — no more reassignments possible

            # Organize all MATCHED winners
            for t in titles:
                if t.state != TitleState.MATCHED or not t.matched_episode:
                    continue

                source_file = _find_source_file(t)
                if not source_file:
                    logger.error(f"Could not find source file for title {t.title_index}")
                    t.state = TitleState.REVIEW
                    session.add(t)
                    continue

                logger.info(f"Organizing Title {t.id} ({source_file.name}) -> {t.matched_episode}")

                org_result = await asyncio.to_thread(
                    tv_organizer.organize,
                    source_file,
                    job.detected_title,
                    t.matched_episode,
                )

                if org_result["success"]:
                    t.state = TitleState.COMPLETED
                    # Store organization tracking info
                    t.organized_from = source_file.name
                    t.organized_to = (
                        str(org_result.get("final_path")) if org_result.get("final_path") else None
                    )
                    t.is_extra = False
                else:
                    t.state = TitleState.REVIEW
                    logger.error(f"Organize failed for Title {t.id}: {org_result['error']}")

                session.add(t)

                # Broadcast title update with organization paths
                await ws_manager.broadcast_title_update(
                    job_id,
                    t.id,
                    t.state.value,
                    matched_episode=t.matched_episode,
                    match_confidence=t.match_confidence,
                    organized_from=t.organized_from,
                    organized_to=t.organized_to,
                    output_filename=t.output_filename,
                    is_extra=t.is_extra,
                    match_details=t.match_details,
                )

            # Persist title states before determining job outcome —
            # if anything fails between here and the job transition,
            # the title states are still correctly saved.
            await session.commit()

            # Determine final job state
            has_review = any(t.state == TitleState.REVIEW for t in titles)
            has_completed = any(t.state == TitleState.COMPLETED for t in titles)

            if has_review:
                review_count = sum(1 for t in titles if t.state == TitleState.REVIEW)
                await state_machine.transition_to_review(
                    job, session, reason=f"{review_count} title(s) need manual episode assignment"
                )
            elif has_completed:
                job.progress_percent = 100.0
                from app.services.config_service import get_config as get_db_config

                db_config = await get_db_config()
                job.final_path = str(
                    Path(db_config.library_tv_path) / (job.detected_title or job.volume_label)
                )
                await state_machine.transition_to_completed(job, session)
            else:
                job.progress_percent = 100.0
                await state_machine.transition_to_completed(job, session)

            # Clean up staging directory if job completed successfully
            if job.state == JobState.COMPLETED:
                await self._cleanup_staging(job_id)

    def _on_task_done(self, task: asyncio.Task, job_id: int) -> None:
        """Callback for background tasks to log any unhandled exceptions."""
        if task.cancelled():
            logger.info(f"Job {job_id} task was cancelled")
        elif exc := task.exception():
            logger.error(f"Job {job_id} task failed with exception: {exc}", exc_info=exc)

    async def _cleanup_staging(self, job_id: int) -> None:
        """Clean up staging directory after successful job completion."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job or not job.staging_path:
                return

            staging_path = Path(job.staging_path)
            if not staging_path.exists():
                return

            try:
                # Remove all files and directory
                import shutil

                shutil.rmtree(staging_path)
                logger.info(f"Cleaned up staging directory: {staging_path}")
            except Exception as e:
                logger.warning(f"Failed to clean staging for job {job_id}: {e}")

    async def _run_ripping(self, job_id: int) -> None:
        """Execute the ripping process."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            # Calculate title count for initial update
            title_count = job.total_titles or 0

            await ws_manager.broadcast_job_update(
                job_id, JobState.RIPPING.value, current_title=1, total_titles=title_count
            )

            try:
                output_dir = Path(job.staging_path)

                # Calculate total size of selected titles
                total_job_bytes = 0
                title_sizes = {}

                # Fetch titles to get sizes
                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                disc_titles = titles_result.scalars().all()

                for t in disc_titles:
                    # If any titles are explicitly selected, skip unselected ones
                    # (This supports Pre-Rip selection workflow)
                    has_selection = any(dt.is_selected for dt in disc_titles if dt.is_selected)
                    if has_selection and not t.is_selected:
                        continue

                    total_job_bytes += t.file_size_bytes
                    title_sizes[t.title_index] = t.file_size_bytes

                # Filter disc_titles for ripping if selection exists
                titles_to_rip = disc_titles
                if any(dt.is_selected for dt in disc_titles):
                    titles_to_rip = [dt for dt in disc_titles if dt.is_selected]

                # Sort titles by index for mapping rip order to title records
                sorted_titles = sorted(titles_to_rip, key=lambda t: t.title_index)

                # Detach title objects from session so in-memory state changes
                # don't get committed alongside the job state update later.
                # Without this, the session.commit() after ripping finishes would
                # overwrite title states (e.g. MATCHED → RIPPING) with stale values.
                for t in disc_titles:
                    session.expunge(t)

                # Initialize calculator
                speed_calc = SpeedCalculator(total_job_bytes)

                # Track which titles have been set to RIPPING locally
                # (since we expunged title objects from the session)
                _titles_marked_ripping: set[int] = set()
                _last_title_idx: int | None = None
                _title_file_cache: dict[int, Path] = {}  # title_index → resolved Path

                # Progress callback
                async def progress_callback(progress: RipProgress) -> None:
                    nonlocal _last_title_idx
                    current_idx = progress.current_title

                    active_title_size = 0
                    cumulative_previous = 0
                    active_title = None

                    if 0 <= (current_idx - 1) < len(sorted_titles):
                        active_title = sorted_titles[current_idx - 1]
                        active_title_size = active_title.file_size_bytes
                        for i in range(current_idx - 1):
                            cumulative_previous += sorted_titles[i].file_size_bytes

                    current_title_bytes = int((progress.percent / 100.0) * active_title_size)
                    total_bytes_done = cumulative_previous + current_title_bytes

                    speed_calc.update(total_bytes_done)

                    global_percent = 0
                    if total_job_bytes > 0:
                        global_percent = (total_bytes_done / total_job_bytes) * 100.0

                    # When active title changes, transition previous title out of RIPPING.
                    # This ensures only one track shows as RIPPING at a time.
                    if _last_title_idx is not None and current_idx != _last_title_idx:
                        prev_list_idx = _last_title_idx - 1
                        if 0 <= prev_list_idx < len(sorted_titles):
                            prev_title = sorted_titles[prev_list_idx]
                            try:
                                async with async_session() as sess:
                                    prev_db = await sess.get(DiscTitle, prev_title.id)
                                    if prev_db and prev_db.state == TitleState.RIPPING:
                                        # Movies skip matching — go straight to MATCHED
                                        if job.content_type == ContentType.TV:
                                            new_state = TitleState.MATCHING
                                        else:
                                            new_state = TitleState.MATCHED
                                        prev_db.state = new_state
                                        sess.add(prev_db)
                                        await sess.commit()
                                        await ws_manager.broadcast_title_update(
                                            job_id,
                                            prev_db.id,
                                            new_state.value,
                                            expected_size_bytes=prev_title.file_size_bytes,
                                            actual_size_bytes=prev_title.file_size_bytes,
                                        )
                            except Exception:
                                logger.warning(
                                    f"Failed to transition title {prev_title.id} "
                                    f"out of RIPPING (Job {job_id})",
                                    exc_info=True,
                                )
                    _last_title_idx = current_idx

                    # Set active title to RIPPING state if not already
                    if active_title and active_title.id not in _titles_marked_ripping:
                        async with async_session() as session:
                            title_db = await session.get(DiscTitle, active_title.id)
                            if title_db and title_db.state == TitleState.PENDING:
                                title_db.state = TitleState.RIPPING
                                session.add(title_db)
                                await session.commit()
                                await ws_manager.broadcast_title_update(
                                    job_id,
                                    title_db.id,
                                    TitleState.RIPPING.value,
                                    duration_seconds=title_db.duration_seconds,
                                    file_size_bytes=title_db.file_size_bytes,
                                    expected_size_bytes=title_db.file_size_bytes,
                                    actual_size_bytes=0,
                                )
                        # Track locally — don't modify expunged ORM objects
                        _titles_marked_ripping.add(active_title.id)

                    # Broadcast per-title byte progress.
                    # Use actual file size on disk (ground truth) with cached path lookup.
                    # Fall back to calculated bytes from RipProgress.percent if file
                    # not found yet (MakeMKV may not have created it at rip start).
                    if active_title and active_title.file_size_bytes:
                        actual_bytes = current_title_bytes  # Fallback
                        tidx = active_title.title_index
                        try:
                            if tidx in _title_file_cache:
                                actual_bytes = _title_file_cache[tidx].stat().st_size
                            else:
                                matches = list(output_dir.glob(f"*_t{tidx:02d}.mkv"))
                                if matches:
                                    _title_file_cache[tidx] = matches[0]
                                    actual_bytes = matches[0].stat().st_size
                        except OSError:
                            pass  # Use calculated fallback
                        await ws_manager.broadcast_title_update(
                            job_id,
                            active_title.id,
                            TitleState.RIPPING.value,
                            expected_size_bytes=active_title.file_size_bytes,
                            actual_size_bytes=min(actual_bytes, active_title.file_size_bytes),
                        )

                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.RIPPING.value,
                        progress=global_percent,
                        speed=speed_calc.speed_str,
                        eta=speed_calc.eta_seconds,
                        current_title=progress.current_title,
                        total_titles=len(sorted_titles),
                    )

                # Define granular callback — called from extractor thread
                def on_title_complete(idx: int, path: Path):
                    logger.info(
                        f"[CALLBACK] Title complete: idx={idx} path={path.name} (Job {job_id})"
                    )
                    future = asyncio.run_coroutine_threadsafe(
                        self._on_title_ripped(job_id, idx, path, sorted_titles),
                        self._loop,
                    )

                    # Log errors from the coroutine (runs in the thread)
                    def _check_result(fut):
                        try:
                            fut.result(timeout=30)
                        except TimeoutError as e:
                            logger.error(
                                f"[CALLBACK] _on_title_ripped timed out for {path.name} (Job {job_id}): {e}"
                            )
                        except Exception as e:
                            logger.exception(
                                f"[CALLBACK] _on_title_ripped failed for "
                                f"{path.name} (Job {job_id}): {e}"
                            )

                    future.add_done_callback(_check_result)

                # Determine indices to pass to extractor
                # If we are ripping ALL detected titles, pass None to use "all" mode (faster/supported)
                # If we are ripping a SUBSET, pass the list (extractor will loop)
                rip_indices = [t.title_index for t in sorted_titles]
                if len(rip_indices) == len(disc_titles):
                    rip_indices = None

                # Run extraction
                result = await self._extractor.rip_titles(
                    job.drive_id,
                    output_dir,
                    title_indices=rip_indices,
                    progress_callback=lambda p: asyncio.create_task(progress_callback(p)),
                    title_complete_callback=on_title_complete,
                )

                if not result.success:
                    await state_machine.transition_to_failed(
                        job, session, error_message=result.error_message
                    )
                    return

                # Eject disc now that ripping is complete — disc is no longer needed
                try:
                    from app.core.sentinel import eject_disc

                    await asyncio.to_thread(eject_disc, job.drive_id)
                except (OSError, RuntimeError) as e:
                    logger.warning(f"Could not eject disc from {job.drive_id}: {e}")

                # For TV, we rely on the granular callbacks to handle moving/matching.
                # Once ripping returns, we update job state to MATCHING if still ripping?
                if job.content_type == ContentType.TV:
                    logger.info(
                        f"[RIP-DONE] Job {job_id}: rip_titles returned, "
                        f"{len(result.output_files)} files produced. "
                        f"Job state={job.state.value}. Running backfill..."
                    )
                    # Fallback: fire _on_title_ripped for any .mkv files that the
                    # filesystem polling didn't catch (e.g. timing edge cases).
                    await self._backfill_unmatched_titles(
                        job_id, Path(job.staging_path), sorted_titles
                    )

                    # Refresh job from DB — matching tasks may have already
                    # advanced the job past RIPPING (same stale-state issue
                    # that required expunging title objects at line 776-781).
                    session.expire(job)
                    await session.refresh(job)

                    if job.state == JobState.RIPPING:
                        succeeded = await state_machine.transition(
                            job, JobState.MATCHING, session, broadcast=False
                        )
                        if succeeded:
                            await ws_manager.broadcast_job_update(job_id, JobState.MATCHING.value)
                    else:
                        logger.info(
                            f"Job {job_id}: skipping RIPPING->MATCHING transition, "
                            f"job already in {job.state.value}"
                        )

                else:
                    # For movies, check if we have multiple ripped versions (Ambiguous Movie workflow)
                    # We can check how many titles were selected for ripping
                    ripped_titles = [t for t in disc_titles if t.is_selected]

                    # If multiple titles were ripped, we need user to select the correct one
                    # CAUTION: If user manually selected multiple titles in Pre-Rip flow (if we supported that),
                    # this would also trigger.
                    if len(ripped_titles) > 1:
                        await state_machine.transition_to_review(
                            job,
                            session,
                            reason="Multiple versions ripped. Please select the correct one.",
                            broadcast=False,
                        )
                        await ws_manager.broadcast_job_update(
                            job_id,
                            JobState.REVIEW_NEEDED.value,
                            error="Multiple versions ripped. Please select the correct one.",
                        )
                        logger.info(
                            f"Job {job_id}: Multiple movie versions ripped. Waiting for user selection."
                        )
                        return

                    # Single title flow (Standard Movie)
                    job.state = JobState.ORGANIZING
                    await session.commit()
                    await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

                    # Identify the file to organize
                    # If we have a single selected title, use its index to find the file
                    # File naming usually `*_tXX.mkv`

                    Path(job.staging_path)

                    # If we know exactly which title index we ripped (ripped_titles has 1 item)
                    # We can try to be specific to avoid "largest file" heuristics which might be wrong if extras are huge?
                    # But MakeMKV robot mode output filenames are not always predictable ID-wise.
                    # Generally `title_t00.mkv`.

                    # Let's rely on `movie_organizer`'s directory scan for the single-file case
                    # OR if we have `ripped_titles`, we can try to find that specific file.
                    # But `DiscTitle` doesn't store the output filename until we verify it exists.
                    # `extractor` output `output_files`. We didn't save them to `DiscTitle` yet?
                    # `_on_title_ripped` SAVES `output_filename` to `DiscTitle`.

                    # So `ripped_titles[0].output_filename` should be set?
                    # `_on_title_ripped` is called via callback. It might race with this code?
                    # We use `await self._extractor.rip_titles` which waits for process.
                    # And `title_complete_callback` fires `_on_title_ripped`.
                    # But `_on_title_ripped` is async.
                    # `rip_titles` implementation waits for callbacks?
                    # Looking at `extractor.py`: `title_complete_callback` is called synchronously from the thread?
                    # No, `job_manager` wraps it in `run_coroutine_threadsafe`.
                    # The `progress_callback` in `rip_titles` waits for queue.
                    # The `title_complete_callback` is called from `_check_for_completed_files`.

                    # We should refresh the session/titles to ensure we have the latest data (output_filenames).
                    # `session` here is open. `_on_title_ripped` uses its own session?
                    # `_on_title_ripped` uses `async with async_session()`.
                    # So we need to `session.refresh` the titles or re-query.

                    # If we only have 1 title, let's just let existing `movie_organizer` logic work
                    # (it finds largest file). It's robust enough for single-movie discs.

                    # Run organizer in thread to not block
                    organize_result = await asyncio.to_thread(
                        movie_organizer.organize,
                        Path(job.staging_path),
                        job.volume_label,
                        job.detected_title,
                    )

                    if organize_result["success"]:
                        job.final_path = str(organize_result["main_file"])
                        job.progress_percent = 100.0

                        # Transition all movie titles to COMPLETED
                        result = await session.execute(
                            select(DiscTitle).where(DiscTitle.job_id == job_id)
                        )
                        for t in result.scalars().all():
                            if t.state not in (TitleState.COMPLETED, TitleState.FAILED):
                                t.state = TitleState.COMPLETED
                                t.organized_from = t.output_filename
                                t.organized_to = str(organize_result.get("main_file", ""))
                                session.add(t)
                                await ws_manager.broadcast_title_update(
                                    job_id,
                                    t.id,
                                    TitleState.COMPLETED.value,
                                    organized_from=t.organized_from,
                                    organized_to=t.organized_to,
                                )
                        await session.commit()

                        await state_machine.transition_to_completed(job, session)
                        logger.info(f"Job {job_id} completed: {organize_result['main_file']}")
                    else:
                        await state_machine.transition_to_failed(
                            job, session, error_message=organize_result["error"]
                        )

            except asyncio.CancelledError:
                logger.info(f"Job {job_id} was cancelled")
                await state_machine.transition_to_failed(
                    job, session, error_message="Cancelled by user"
                )
            except Exception as e:
                logger.exception(f"Error ripping job {job_id}")
                await state_machine.transition_to_failed(job, session, error_message=str(e))

    async def _on_title_ripped(
        self, job_id: int, rip_index: int, path: Path, sorted_titles: list[DiscTitle]
    ) -> None:
        """Handle completion of a single title rip.

        Matches the ripped file to a DiscTitle using:
        1. Title index extracted from filename (e.g. B1_t03.mkv → index 3)
        2. Fallback: sequential rip_index mapped to sorted titles
        """
        import re as _re

        async with async_session() as session:
            title = None

            # Try to extract title index from MakeMKV filename pattern
            # Common patterns: B1_t00.mkv, title_00.mkv, title00.mkv
            idx_match = _re.search(r"t(\d+)\.mkv$", path.name, _re.IGNORECASE)
            if not idx_match:
                idx_match = _re.search(r"title[_]?(\d+)\.mkv$", path.name, _re.IGNORECASE)

            if idx_match:
                title_index = int(idx_match.group(1))
                # Find the DiscTitle with this title_index
                for st in sorted_titles:
                    if st.title_index == title_index:
                        title = await session.get(DiscTitle, st.id)
                        break
                if title:
                    logger.debug(
                        f"Mapped {path.name} to title_index={title_index} "
                        f"(Title DB id={title.id}, Job {job_id})"
                    )

            # Fallback: map by sequential rip order
            if not title and 0 <= (rip_index - 1) < len(sorted_titles):
                st = sorted_titles[rip_index - 1]
                title = await session.get(DiscTitle, st.id)
                logger.debug(
                    f"Fallback mapping: rip_index={rip_index} → "
                    f"title_index={st.title_index} (Title DB id={st.id}, Job {job_id})"
                )

            if not title:
                logger.warning(f"Could not map ripped file {path.name} to any title (Job {job_id})")
                return

            title.output_filename = str(path)
            # Only advance from PENDING → RIPPING; don't regress a title that
            # progress_callback already transitioned to MATCHING.
            if title.state == TitleState.PENDING:
                title.state = TitleState.RIPPING
            session.add(title)
            await session.commit()
            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                duration_seconds=title.duration_seconds,
                file_size_bytes=title.file_size_bytes,
                output_filename=title.output_filename,
            )

            logger.info(
                f"Title detected: {path.name} → title_index={title.title_index} "
                f"(Title {title.id}, Job {job_id}) — queuing for matching"
            )

            # If TV, queue matching task (will wait for file to finish writing)
            job = await session.get(DiscJob, job_id)
            if job and job.content_type == ContentType.TV:
                task = asyncio.create_task(self._match_single_file(job_id, title.id, path))
                task.add_done_callback(
                    lambda t, jid=job_id, tid=title.id: self._on_match_task_done(t, jid, tid)
                )

    async def _backfill_unmatched_titles(
        self, job_id: int, staging_dir: Path, sorted_titles: list[DiscTitle]
    ) -> None:
        """Scan staging dir for .mkv files not yet assigned to a title.

        This catches any files missed by the real-time filesystem polling
        (e.g. timing edge cases or the final file completing after the check).
        """
        import re as _re

        async with async_session() as session:
            # Get current title states
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            titles = result.scalars().all()
            assigned_indices = {t.title_index for t in titles if t.output_filename is not None}

            # Scan staging dir for .mkv files
            mkv_files = list(staging_dir.glob("*.mkv")) if staging_dir.exists() else []

            for mkv in mkv_files:
                # Extract title index from filename
                idx_match = _re.search(r"t(\d+)\.mkv$", mkv.name, _re.IGNORECASE)
                if not idx_match:
                    idx_match = _re.search(r"title[_]?(\d+)\.mkv$", mkv.name, _re.IGNORECASE)
                if not idx_match:
                    continue

                title_index = int(idx_match.group(1))
                if title_index in assigned_indices:
                    continue  # Already handled by real-time callback

                logger.info(
                    f"Backfill: found unmatched file {mkv.name} "
                    f"(title_index={title_index}, Job {job_id})"
                )
                await self._on_title_ripped(job_id, 0, mkv, sorted_titles)

    async def _wait_for_file_ready(
        self, file_path: Path, title_id: int, job_id: int, timeout: float | None = None
    ) -> bool:
        """Wait until a ripped file is fully written and ready for processing.

        MakeMKV creates output files immediately but writes to them over minutes.
        We poll the file size and require it to be stable (unchanged) for several
        consecutive checks before considering it complete.

        Returns True if file is ready, False on timeout.
        """
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
        # Assume minimum rip speed of 1 MB/s (slow disc), add 2x buffer for safety
        if timeout is None:
            if expected_size > 0:
                base_timeout = (expected_size / (1024 * 1024)) * 2  # 2 seconds per MB
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
                    TitleState.RIPPING.value,  # Fixed: Should be RIPPING while file is being written
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

                # Check if size is close to expected (allow 5% tolerance for metadata overhead)
                size_matches_expected = True
                if expected_size > 0:
                    size_ratio = current_size / expected_size
                    # MakeMKV scan estimates are approximate; allow 15% tolerance
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
                    # Extra check on Windows: MakeMKV may still hold the file open
                    # for writing even after size is stable, causing ffprobe EACCES.
                    # Try opening for read; if denied, wait another cycle.
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
                    # Size is stable but doesn't match expected - file still being written
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size stable but only "
                        f"{current_size / 1024 / 1024:.0f} MB of expected "
                        f"{expected_size / 1024 / 1024:.0f} MB ({size_ratio * 100:.1f}%) — still writing"
                    )
                    stable_count = 0  # Reset and keep waiting
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
                # Progress based on size (capped at 99%)
                wait_progress = min(99.0, (current_size / expected_size) * 100.0)
            else:
                # Fallback: stable_count goes from 0 to 3 explicitly
                wait_progress = min(99.0, (stable_count / required_stable) * 100.0)

            await ws_manager.broadcast_title_update(
                job_id,
                title_id,
                TitleState.RIPPING.value,  # Fixed: Should be RIPPING while file is being written
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

    def _on_match_task_done(self, task: asyncio.Task, job_id: int, title_id: int) -> None:
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
            active_states = (TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING)
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

    async def _match_single_file(self, job_id: int, title_id: int, file_path: Path) -> None:
        """Run matching for a single ripped file."""
        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): match task started for {file_path.name}"
        )

        # 1. Wait for subtitles to be ready before matching
        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): _match_single_file entered. "
            f"subtitle_ready event exists: {job_id in self._subtitle_ready}"
        )
        if job_id in self._subtitle_ready:
            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): waiting for subtitle download..."
            )
            try:
                # Log before wait
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): entering wait_for(subtitle_ready)"
                )
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

        # 1b. Check subtitle status from database - BLOCK matching if failed
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            subtitle_status = job.subtitle_status if job else None

        # Gate matching based on subtitle status
        if subtitle_status == "failed":
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitle download failed. "
                f"No reference files available. Title needs manual episode assignment."
            )
            # Mark title as REVIEW — file ripped fine, just can't auto-match
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
            return  # Exit early, don't proceed with matching

        elif subtitle_status == "partial":
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitle download partially succeeded. "
                f"Matching will proceed with available reference files."
            )
            # Continue with matching (some episodes have subtitles)

        elif subtitle_status in ("completed", None):
            logger.info(
                f"[MATCH] Title {title_id} (Job {job_id}): subtitles ready, proceeding with matching"
            )
            # Continue normally

        else:
            logger.warning(
                f"[MATCH] Title {title_id} (Job {job_id}): unknown subtitle status '{subtitle_status}', "
                f"attempting match anyway"
            )

        # 2. Wait for the file to be fully written before matching (MOVED UP)
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

        # 1c. Duration pre-filter: skip matching for tracks that don't match any
        # expected episode runtime from TMDB (likely extras/bonus content)
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
                                fetch_season_episode_runtimes, show_id, job.detected_season
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
                            logger.info(
                                f"[MATCH] Title {title_id} (Job {job_id}): duration {title_minutes:.0f}min "
                                f"doesn't match any episode runtime {runtimes} (±{tolerance}min). "
                                f"Moving to extras folder."
                            )
                            from app.core.organizer import organize_tv_extras

                            # Count existing extras for this job to determine index
                            extras_count = 0
                            all_titles = await session.execute(
                                select(DiscTitle).where(DiscTitle.job_id == job_id)
                            )
                            for t in all_titles.scalars():
                                if t.match_details:
                                    try:
                                        details = json.loads(t.match_details)
                                        if details.get("auto_sorted") == "extras":
                                            extras_count += 1
                                    except (json.JSONDecodeError, KeyError, TypeError):
                                        pass  # No details available, skip

                            extra_index = extras_count + 1

                            org_result = await asyncio.to_thread(
                                organize_tv_extras,
                                file_path,
                                job.detected_title,
                                job.detected_season,
                                None,  # library_path (uses default)
                                job.disc_number,
                                extra_index,
                            )
                            if org_result["success"]:
                                title.state = TitleState.COMPLETED
                                # Store organization tracking info for extras
                                title.organized_from = file_path.name
                                title.organized_to = (
                                    str(org_result.get("final_path"))
                                    if org_result.get("final_path")
                                    else None
                                )
                                title.is_extra = True
                                title.match_details = json.dumps(
                                    {
                                        "auto_sorted": "extras",
                                        "reason": f"Duration {title_minutes:.0f}min doesn't match episode runtimes",
                                    }
                                )
                            else:
                                title.state = TitleState.COMPLETED
                                title.match_details = json.dumps(
                                    {
                                        "auto_sorted": "extras",
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
                            return
                except Exception as e:
                    logger.warning(
                        f"[MATCH] Title {title_id} (Job {job_id}): duration pre-filter failed: {e}. "
                        f"Proceeding with matching normally."
                    )

        # (File ready check moved up to before duration pre-filter)

        # 3. Acquire semaphore to limit concurrent matching (Whisper ASR is GPU-heavy)
        if self._match_semaphore is not None:
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): waiting for match semaphore...")
            await self._match_semaphore.acquire()
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): acquired match semaphore")

        # 4. Transition title to MATCHING now that file is ready and slot acquired
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

        # 5. Run matching
        try:
            logger.debug(
                f"[MATCH] Title {title_id} (Job {job_id}): entering _match_single_file_inner"
            )
            await self._match_single_file_inner(job_id, title_id, file_path)
            logger.debug(
                f"[MATCH] Title {title_id} (Job {job_id}): returned from _match_single_file_inner"
            )
        except Exception as e:
            logger.exception(
                f"[MATCH] Title {title_id} (Job {job_id}): error in _match_single_file_inner: {e}"
            )
            raise  # Let _on_match_task_done trigger _handle_match_failure
        finally:
            if self._match_semaphore is not None:
                self._match_semaphore.release()
                logger.info(f"[MATCH] Title {title_id} (Job {job_id}): released match semaphore")

    async def _match_single_file_inner(self, job_id: int, title_id: int, file_path: Path) -> None:
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
                # Since matching runs in a thread, we must schedule the WebSocket update
                # on the main event loop using run_coroutine_threadsafe.
                loop = asyncio.get_running_loop()
                # Bind json.dumps locally to avoid free-variable scoping issues
                # when the callback runs in a worker thread (seen in production).
                _json_dumps = json.dumps

                def on_progress(stage: str, percent: float, vote_data: list | None = None):
                    try:
                        details = None
                        if vote_data:
                            # Build interim match_details with current standings
                            # Include ALL candidates in runner_ups so the UI can
                            # display the full voting leaderboard during matching.
                            best = vote_data[0] if vote_data else None
                            target = best.get("target_votes", 5) if best else 5
                            details = _json_dumps(
                                {
                                    "score": best["score"] if best else 0,
                                    "vote_count": best["vote_count"] if best else 0,
                                    "target_votes": target,
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
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    except Exception as e:
                        logger.warning(f"[MATCH] Title {title_id}: progress callback error: {e}")

                # Run the episode matcher (Whisper ASR or subtitle matching)
                logger.info(
                    f"[MATCH] Title {title_id} (Job {job_id}): calling episode_curator.match_single_file for {file_path.name}"
                )
                result = await episode_curator.match_single_file(
                    file_path,
                    series_name=job.detected_title,
                    season=job.detected_season,
                    progress_callback=on_progress,
                )
                logger.info(
                    f"[MATCH] Title {title_id} (Job {job_id}): episode_curator.match_single_file returned for {file_path.name}"
                )

                elapsed = time.monotonic() - match_start

                # Update title with match result
                title.matched_episode = result.episode_code
                title.match_confidence = result.confidence

                if result.needs_review:
                    if result.episode_code:
                        # Low-confidence match — still usable, mark as matched
                        title.state = TitleState.MATCHED
                    else:
                        # No match found — needs human episode assignment
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

                if result.match_details:
                    try:
                        title.match_details = json.dumps(result.match_details)
                    except Exception as e:
                        logger.error(f"Failed to dump match_details: {e}")

                # Extract match stats for broadcast
                matches_found = 1  # Primary match
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
                        pass  # No match details available

                session.add(title)
                await session.commit()

                # Broadcast update
                await event_broadcaster.broadcast_job_state_changed(job_id, job.state)
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

                # DEFERRED ORGANIZATION: We wait for all titles to be matched.
                # Logic moved to _finalize_disc_job.

                # Check if ALL titles are done to update parent job
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

    async def _download_subtitles(self, job_id: int, show_name: str, season: int) -> None:
        """Download subtitles in background. Failure BLOCKS matching."""
        from sqlalchemy import update

        try:
            # Set initial status — targeted update to avoid overwriting job state
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

            # Run in thread as it might be blocking
            result = await asyncio.to_thread(download_subtitles, show_name, season)

            # Count successes/failures
            downloaded = sum(
                1 for ep in result["episodes"] if ep["status"] in ("downloaded", "cached")
            )
            failed = sum(1 for ep in result["episodes"] if ep["status"] in ("not_found", "failed"))
            total = len(result["episodes"])

            # Determine final status
            status = "completed" if failed == 0 else ("partial" if downloaded > 0 else "failed")

            error_msg = None
            if status == "failed":
                error_msg = "Subtitle download failed: No subtitles found"

            logger.info(
                f"Subtitle download complete for {show_name} S{season}: "
                f"{status} ({downloaded} downloaded/cached, {failed} failed)"
            )

            # PERSIST STATUS — targeted update to avoid overwriting job state
            async with async_session() as session:
                update_values = {"subtitle_status": status}

                # Update to canonical name if it changed
                if result.get("show_name") and result["show_name"] != show_name:
                    logger.info(f"Updating job {job_id} title to canonical: {result['show_name']}")
                    update_values["detected_title"] = result["show_name"]

                if error_msg:
                    update_values["error_message"] = error_msg

                await session.execute(
                    update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
                )
                await session.commit()

            await ws_manager.broadcast_subtitle_event(
                job_id, status, downloaded=downloaded, total=total, failed_count=failed
            )

        except ValueError as e:
            logger.error(f"Subtitle download ValueError for {show_name} S{season}: {e}")
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="failed", error_message=str(e))
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        except Exception as e:
            logger.exception(
                f"Unexpected error in subtitle download for {show_name} S{season}: {e}"
            )
            async with async_session() as session:
                await session.execute(
                    update(DiscJob)
                    .where(DiscJob.id == job_id)
                    .values(subtitle_status="failed", error_message=f"Download error: {str(e)}")
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        finally:
            # ALWAYS set event (matching will check status before proceeding)
            if job_id in self._subtitle_ready:
                self._subtitle_ready[job_id].set()

    async def cancel_job(self, job_id: int) -> None:
        """Cancel a running job."""
        if job_id in self._active_jobs:
            self._active_jobs[job_id].cancel()
            del self._active_jobs[job_id]

        self._extractor.cancel()

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job and job.state not in (JobState.COMPLETED, JobState.FAILED):
                await state_machine.transition_to_failed(
                    job, session, error_message="Cancelled by user"
                )

    async def apply_review(
        self,
        job_id: int,
        title_id: int,
        episode_code: str | None = None,
        edition: str | None = None,
    ) -> None:
        """Apply a user's review decision for a title."""
        from app.core.organizer import movie_organizer, tv_organizer

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")

            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError("Title not found for this job")

            if episode_code:
                title.matched_episode = episode_code

            if edition:
                title.edition = edition

            title.match_confidence = 1.0  # User-confirmed
            session.add(title)
            await session.commit()

            # Handle Movie Workflow
            if job.content_type == ContentType.MOVIE:
                if episode_code == "skip":
                    title.state = TitleState.FAILED
                    session.add(title)
                    await session.commit()
                    return

                # For movies, we organize the selected title immediately
                # We assume the user selects the MAIN title(s) they want.

                # Update status
                job.state = JobState.ORGANIZING
                await session.commit()
                await event_broadcaster.broadcast_job_state_changed(job_id, job.state)

                # Organize
                if title.output_filename:
                    source_file = Path(title.output_filename)
                    if not source_file.exists():
                        logger.info(
                            f"Source file {source_file} not found. Triggering ripping for selected title."
                        )

                        # Set other titles to is_selected=False
                        # The user selected 'title', so we ensure only this one is selected
                        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
                        all_titles = (await session.execute(statement)).scalars().all()
                        for t in all_titles:
                            t.is_selected = t.id == title.id
                            session.add(t)

                        # Trigger ripping
                        await session.commit()

                        # We need to call start_ripping, but we are inside an async_session context.
                        # start_ripping creates its own session. We should schedule it outside or handle it carefully.
                        # JobManager methods usually handle their own sessions.
                        # We can just update state here and call _run_ripping as a task?
                        # Or better, return from here and call start_ripping?
                        # But apply_review is atomic.

                        # Let's update state to REVIEW_NEEDED -> RIPPING manually here?
                        # start_ripping checks for IDLE/REVIEW_NEEDED.

                        # Let's close this transaction and call start_ripping?
                        # We can't easily do that inside the context manager.

                        # Option: Update state to RIPPING here, then spawn the task.
                        job.state = JobState.RIPPING
                        job.updated_at = datetime.utcnow()
                        session.add(job)
                        await session.commit()
                        await event_broadcaster.broadcast_job_state_changed(job_id, job.state)

                        # Spawn ripping task
                        task = asyncio.create_task(self._run_ripping(job_id))
                        self._active_jobs[job_id] = task
                        return

                    else:
                        # File exists, proceed to organize (Post-Rip workflow)

                        # Clean up unselected ripped files (Ambiguous Movie workflow)
                        # If we ripped multiple versions, delete the ones not selected
                        cleanup_statement = select(DiscTitle).where(
                            DiscTitle.job_id == job_id,
                            DiscTitle.id != title_id,
                            DiscTitle.output_filename.isnot(None),
                        )
                        unselected_titles = (
                            (await session.execute(cleanup_statement)).scalars().all()
                        )

                        for unselected in unselected_titles:
                            try:
                                p = Path(unselected.output_filename)
                                if p.exists():
                                    p.unlink()
                                    logger.info(f"Deleted unselected file: {p}")

                                unselected.state = TitleState.FAILED
                                unselected.match_details = json.dumps(
                                    {"reason": "Unselected by user"}
                                )
                                session.add(unselected)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to delete unselected file {unselected.output_filename}: {e}"
                                )

                        # Pass edition to organizer if supported, or append to title?
                        # movie_organizer.organize signature: (file_path, volume_label, detected_title)
                        # We might need to update movie_organizer to accept edition, OR append it to detected_title temporarily

                        final_title = job.detected_title or job.volume_label
                        if edition and edition.lower() not in final_title.lower():
                            final_title = f"{final_title} {{edition-{edition}}}"

                        org_result = await asyncio.to_thread(
                            movie_organizer.organize,
                            source_file,
                            job.volume_label,
                            final_title,
                        )

                        if org_result["success"]:
                            title.state = TitleState.COMPLETED
                            job.progress_percent = 100.0
                            job.error_message = None
                            job.final_path = str(org_result["main_file"])
                            await state_machine.transition_to_completed(job, session)
                            logger.info(f"Organized movie: {org_result['main_file']}")
                        elif org_result.get("error_code") == "FILE_EXISTS":
                            title.state = TitleState.REVIEW
                            try:
                                # Preserve existing details if JSON
                                existing_details = (
                                    json.loads(title.match_details) if title.match_details else {}
                                )
                                existing_details.update(
                                    {"error": "file_exists", "message": str(org_result["error"])}
                                )
                                title.match_details = json.dumps(existing_details)
                            except (json.JSONDecodeError, TypeError):
                                # Fall back to creating new details JSON
                                title.match_details = json.dumps(
                                    {"error": "file_exists", "message": str(org_result["error"])}
                                )

                            await state_machine.transition_to_review(
                                job, session, reason="File already exists in library"
                            )
                            logger.warning(
                                f"Organization conflict for movie: {org_result['error']}"
                            )
                        else:
                            title.state = TitleState.FAILED
                            logger.error(f"Failed to organize movie: {org_result['error']}")
                            await state_machine.transition_to_failed(
                                job, session, error_message=org_result["error"]
                            )

                # Movie workflow complete — don't fall through to TV workflow
                return

            # Handle TV Workflow (Original Logic)
            # Check if all titles for this job are now resolved
            result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.matched_episode.is_(None),
                )
            )
            unresolved = result.scalars().all()

            if not unresolved:
                # All titles resolved - organize files to library
                job.state = JobState.ORGANIZING
                session.add(job)
                await session.commit()
                await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

                # Get all resolved titles for this job
                all_titles_result = await session.execute(
                    select(DiscTitle).where(
                        DiscTitle.job_id == job_id,
                        DiscTitle.matched_episode.isnot(None),
                    )
                )
                resolved_titles = all_titles_result.scalars().all()

                # Organize each file
                success_count = 0
                conflict_count = 0

                for disc_title in resolved_titles:
                    if disc_title.output_filename and disc_title.matched_episode != "skip":
                        source_file = Path(disc_title.output_filename)
                        if source_file.exists():
                            org_result = await asyncio.to_thread(
                                tv_organizer.organize,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                            )
                            if org_result["success"]:
                                success_count += 1
                                # Store organization tracking info
                                disc_title.organized_from = source_file.name
                                disc_title.organized_to = (
                                    str(org_result.get("final_path"))
                                    if org_result.get("final_path")
                                    else None
                                )
                                disc_title.is_extra = False
                                disc_title.state = TitleState.COMPLETED
                                logger.info(f"Organized: {org_result['final_path']}")
                            elif org_result.get("error_code") == "FILE_EXISTS":
                                conflict_count += 1
                                disc_title.state = TitleState.REVIEW
                                try:
                                    existing = (
                                        json.loads(disc_title.match_details)
                                        if disc_title.match_details
                                        else {}
                                    )
                                    existing.update(
                                        {
                                            "error": "file_exists",
                                            "message": str(org_result["error"]),
                                        }
                                    )
                                    disc_title.match_details = json.dumps(existing)
                                except (json.JSONDecodeError, TypeError):
                                    # Fall back to creating new details JSON
                                    disc_title.match_details = json.dumps(
                                        {
                                            "error": "file_exists",
                                            "message": str(org_result["error"]),
                                        }
                                    )
                                logger.warning(
                                    f"Organization conflict for TV: {org_result['error']}"
                                )
                            else:
                                logger.error(f"Failed to organize: {org_result['error']}")

                            # Broadcast title update with organization result
                            session.add(disc_title)
                            await session.commit()
                            await ws_manager.broadcast_title_update(
                                job_id,
                                disc_title.id,
                                disc_title.state.value,
                                matched_episode=disc_title.matched_episode,
                                match_confidence=disc_title.match_confidence,
                                organized_from=disc_title.organized_from,
                                organized_to=disc_title.organized_to,
                                output_filename=disc_title.output_filename,
                                is_extra=disc_title.is_extra,
                                match_details=disc_title.match_details,
                            )
                        else:
                            logger.warning(f"Source file not found: {source_file}")

                if conflict_count > 0:
                    await state_machine.transition_to_review(
                        job, session, reason=f"{conflict_count} files already exist in library"
                    )
                elif success_count > 0:
                    job.progress_percent = 100.0
                    job.error_message = None
                    from app.services.config_service import get_config as get_db_config

                    db_config = await get_db_config()
                    job.final_path = str(
                        Path(db_config.library_tv_path) / (job.detected_title or job.volume_label)
                    )
                    await state_machine.transition_to_completed(job, session)
                else:
                    await state_machine.transition_to_failed(
                        job, session, error_message="Failed to organize files"
                    )
            else:
                await session.commit()

    async def process_matched_titles(self, job_id: int) -> dict:
        """Process all matched titles for a job without waiting for unresolved ones.

        Organizes titles that already have matches while leaving unresolved titles
        in REVIEW_NEEDED state. Returns counts of processed, conflicted, and remaining titles.
        """
        from app.core.organizer import tv_organizer
        from app.services.config_service import get_config as get_db_config

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")

            # Query titles that have a match, are not skipped, and are not already terminal
            result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.matched_episode.isnot(None),
                    DiscTitle.matched_episode != "skip",
                    DiscTitle.state.not_in([TitleState.COMPLETED, TitleState.FAILED]),
                )
            )
            matched_titles = result.scalars().all()

            success_count = 0
            conflict_count = 0

            for disc_title in matched_titles:
                if not disc_title.output_filename:
                    continue

                source_file = Path(disc_title.output_filename)
                if not source_file.exists():
                    logger.warning(f"Source file not found: {source_file}")
                    continue

                org_result = await asyncio.to_thread(
                    tv_organizer.organize,
                    source_file,
                    job.detected_title or job.volume_label,
                    disc_title.matched_episode,
                )

                if org_result["success"]:
                    success_count += 1
                    disc_title.organized_from = source_file.name
                    disc_title.organized_to = (
                        str(org_result.get("final_path")) if org_result.get("final_path") else None
                    )
                    disc_title.is_extra = disc_title.matched_episode == "extra"
                    disc_title.state = TitleState.COMPLETED
                    logger.info(f"Organized: {org_result['final_path']}")
                elif org_result.get("error_code") == "FILE_EXISTS":
                    conflict_count += 1
                    disc_title.state = TitleState.REVIEW
                    try:
                        existing = (
                            json.loads(disc_title.match_details) if disc_title.match_details else {}
                        )
                        existing.update(
                            {
                                "error": "file_exists",
                                "message": str(org_result["error"]),
                            }
                        )
                        disc_title.match_details = json.dumps(existing)
                    except (json.JSONDecodeError, TypeError):
                        disc_title.match_details = json.dumps(
                            {
                                "error": "file_exists",
                                "message": str(org_result["error"]),
                            }
                        )
                    logger.warning(f"Organization conflict for TV: {org_result['error']}")
                else:
                    logger.error(f"Failed to organize: {org_result['error']}")
                    continue

                session.add(disc_title)
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    disc_title.id,
                    disc_title.state.value,
                    matched_episode=disc_title.matched_episode,
                    match_confidence=disc_title.match_confidence,
                    organized_from=disc_title.organized_from,
                    organized_to=disc_title.organized_to,
                    output_filename=disc_title.output_filename,
                    is_extra=disc_title.is_extra,
                    match_details=disc_title.match_details,
                )

            # Check if any unresolved titles remain
            unresolved_result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.state.not_in([TitleState.COMPLETED, TitleState.FAILED]),
                    DiscTitle.matched_episode.is_(None),
                )
            )
            unresolved = unresolved_result.scalars().all()

            if not unresolved and conflict_count == 0:
                job.progress_percent = 100.0
                job.error_message = None
                db_config = await get_db_config()
                job.final_path = str(
                    Path(db_config.library_tv_path) / (job.detected_title or job.volume_label)
                )
                await state_machine.transition_to_completed(job, session)
            else:
                # Keep in REVIEW_NEEDED — unresolved titles or conflicts remain
                await session.commit()

        return {
            "organized": success_count,
            "conflicts": conflict_count,
            "unresolved": len(unresolved),
        }

    # --- Simulation Methods ---

    async def simulate_disc_insert(self, params: dict) -> int:
        """Simulate a disc insertion for testing purposes."""
        from app.services.config_service import get_config as get_sim_config

        drive_id = params.get("drive_id", "E:")
        volume_label = params.get("volume_label", "SIMULATED_DISC")
        content_type_str = params.get("content_type", "tv")

        # Parse detected_title: remove season/disc suffix (e.g., "_S1D1", "_S01D01")
        default_title = volume_label.replace("_", " ").title()
        import re

        # Remove season/disc patterns like "S1D1", "S01D01", etc.
        default_title = re.sub(r"\s+S\d+D?\d*$", "", default_title, flags=re.IGNORECASE)
        detected_title = params.get("detected_title", default_title)
        detected_season = params.get("detected_season", 1)
        simulate_ripping = params.get("simulate_ripping", False)
        rip_speed_multiplier = params.get("rip_speed_multiplier", 10)
        title_params = params.get("titles", [])

        content_type = ContentType(content_type_str)

        # Default titles if none provided
        if not title_params:
            if content_type == ContentType.TV:
                title_params = [
                    {"duration_seconds": 1320 + i * 60, "file_size_bytes": 1024 * 1024 * 1024}
                    for i in range(8)
                ]
            else:
                title_params = [
                    {"duration_seconds": 7200, "file_size_bytes": 4 * 1024 * 1024 * 1024}
                ]

        async with async_session() as session:
            # Create job
            job = DiscJob(
                drive_id=drive_id,
                volume_label=volume_label,
                content_type=content_type,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
                state=JobState.IDENTIFYING,
                total_titles=len(title_params),
                staging_path=str(
                    Path((await get_sim_config()).staging_path)
                    / f"sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                ),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            # Create titles
            titles = []
            for i, tp in enumerate(title_params):
                title = DiscTitle(
                    job_id=job.id,
                    title_index=i,
                    duration_seconds=tp.get("duration_seconds", 1320),
                    file_size_bytes=tp.get("file_size_bytes", 1024 * 1024 * 1024),
                    chapter_count=tp.get("chapter_count", 5),
                )
                session.add(title)
                titles.append(title)
            await session.commit()
            for t in titles:
                await session.refresh(t)

            # Broadcast drive event
            await event_broadcaster.broadcast_drive_inserted(drive_id, volume_label)

            # Broadcast identifying
            await ws_manager.broadcast_job_update(
                job.id,
                JobState.IDENTIFYING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
                total_titles=len(title_params),
            )

            # Short delay to simulate scanning
            await asyncio.sleep(0.5)

            # Broadcast titles discovered
            title_list = [
                {
                    "id": t.id,
                    "title_index": t.title_index,
                    "duration_seconds": t.duration_seconds,
                    "file_size_bytes": t.file_size_bytes,
                    "chapter_count": t.chapter_count,
                }
                for t in titles
            ]
            await ws_manager.broadcast_titles_discovered(
                job.id,
                title_list,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
            )

            # Start subtitle download for TV content (needed for matching)
            # Use simulated subtitle download since this is a simulation
            if content_type == ContentType.TV and detected_title and detected_season:
                self._subtitle_ready[job.id] = asyncio.Event()
                self._subtitle_tasks[job.id] = asyncio.create_task(
                    self._simulate_subtitle_download(job.id, len(title_params), detected_title)
                )
                logger.info(
                    f"Job {job.id}: starting simulated subtitle download for {detected_title} S{detected_season}"
                )

            force_review = params.get("force_review_needed", False)
            if force_review:
                # Transition directly to review_needed (for E2E testing name prompt, etc.)
                job.state = JobState.REVIEW_NEEDED
                job.detected_title = None  # Clear title to trigger name prompt
                job.review_reason = params.get("review_reason", "Disc label unreadable")
                await session.commit()
                await ws_manager.broadcast_job_update(
                    job.id,
                    JobState.REVIEW_NEEDED.value,
                    content_type=content_type.value,
                    detected_title=None,
                    review_reason=job.review_reason,
                    total_titles=len(title_params),
                )
            elif simulate_ripping:
                # Start simulated ripping in background
                task = asyncio.create_task(
                    self._simulate_ripping(job.id, titles, rip_speed_multiplier, content_type)
                )
                task.add_done_callback(lambda t, jid=job.id: self._on_task_done(t, jid))
                self._active_jobs[job.id] = task
            else:
                # Just move to ripping state
                job.state = JobState.RIPPING
                await session.commit()
                await ws_manager.broadcast_job_update(
                    job.id,
                    JobState.RIPPING.value,
                    content_type=content_type.value,
                    detected_title=detected_title,
                    detected_season=detected_season if content_type == ContentType.TV else None,
                    total_titles=len(title_params),
                )

            return job.id

    async def simulate_disc_insert_realistic(self, params: dict) -> int:
        """
        Simulate disc insertion using real MKV files from staging.
        Uses 10-second ripping simulation per track with progress updates.
        """

        drive_id = params.get("drive_id", "E:")
        volume_label = params.get("volume_label", "REAL_DATA_DISC")
        content_type = ContentType(params.get("content_type", "tv"))
        detected_title = params.get("detected_title")
        detected_season = params.get("detected_season", 1)
        title_params = params.get("titles", [])
        staging_path = params.get("staging_path")
        rip_speed_multiplier = params.get("rip_speed_multiplier", 1)

        async with async_session() as session:
            # Create DiscJob first (before broadcasting, so fetch finds it)
            job = DiscJob(
                drive_id=drive_id,
                volume_label=volume_label,
                content_type=content_type,
                state=JobState.IDENTIFYING,
                detected_title=detected_title,
                detected_season=detected_season,
                staging_path=staging_path,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            # Broadcast drive event after job exists in DB
            await event_broadcaster.broadcast_drive_inserted(drive_id, volume_label)

            await ws_manager.broadcast_job_update(
                job.id,
                JobState.IDENTIFYING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
                total_titles=len(title_params),
            )

            # Create titles from real files
            titles = []
            for title_param in title_params:
                title = DiscTitle(
                    job_id=job.id,
                    title_index=title_param["title_index"],
                    duration_seconds=title_param["duration_seconds"],
                    file_size_bytes=title_param["file_size_bytes"],
                    chapter_count=title_param.get("chapter_count", 5),
                    is_selected=True,
                    output_filename=title_param.get("output_filename"),
                    state=TitleState.PENDING,
                )
                session.add(title)
                titles.append(title)

            await session.commit()

            # Move to ripping state
            job.state = JobState.RIPPING
            job.total_titles = len(titles)
            await session.commit()
            await ws_manager.broadcast_job_update(
                job.id,
                JobState.RIPPING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
                total_titles=len(titles),
            )

            # Refresh titles to get their IDs after commit
            for t in titles:
                await session.refresh(t)

            # Broadcast titles discovered (must include id for frontend)
            title_list = [
                {
                    "id": t.id,
                    "title_index": t.title_index,
                    "duration_seconds": t.duration_seconds,
                    "file_size_bytes": t.file_size_bytes,
                    "chapter_count": t.chapter_count,
                }
                for t in titles
            ]
            await ws_manager.broadcast_titles_discovered(
                job.id,
                title_list,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=detected_season if content_type == ContentType.TV else None,
            )

            # Start subtitle download for TV content
            if content_type == ContentType.TV and detected_title and detected_season:
                self._subtitle_ready[job.id] = asyncio.Event()
                self._subtitle_tasks[job.id] = asyncio.create_task(
                    self._simulate_subtitle_download(job.id, len(title_params), detected_title)
                )
                logger.info(
                    f"Job {job.id}: starting simulated subtitle download for {detected_title} S{detected_season}"
                )

            # Start realistic ripping simulation
            task = asyncio.create_task(
                self._simulate_realistic_ripping(job.id, titles, content_type, rip_speed_multiplier)
            )
            self._active_jobs[job.id] = task

            return job.id

    async def _simulate_realistic_ripping(
        self,
        job_id: int,
        titles: list[DiscTitle],
        content_type: ContentType,
        speed_multiplier: int = 1,
    ) -> None:
        """Simulate ripping with configurable speed per track and progress updates."""
        async with async_session() as session:
            for i, title in enumerate(titles):
                logger.info(f"[SIMULATE] Job {job_id}: starting realistic rip of title {i}")

                # Update title state to ripping
                title_db = await session.get(DiscTitle, title.id)
                if not title_db:
                    continue

                title_db.state = TitleState.RIPPING
                title_bytes = title_db.file_size_bytes or 0
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_db.id,
                    TitleState.RIPPING.value,
                    duration_seconds=title_db.duration_seconds,
                    file_size_bytes=title_db.file_size_bytes,
                    expected_size_bytes=title_bytes,
                    actual_size_bytes=0,
                )

                # Simulate ripping with progress updates (speed multiplier reduces time)
                base_steps = 20  # 20 steps at 0.5s = 10 seconds base
                steps = max(4, base_steps // max(1, speed_multiplier))
                sleep_time = 0.5 / max(1, speed_multiplier)
                step_size = title_bytes / steps if steps > 0 else 0
                for step in range(steps + 1):
                    await asyncio.sleep(sleep_time)

                    # Update job progress
                    job = await session.get(DiscJob, job_id)
                    if job:
                        job.progress_percent = ((i + (step / steps)) / len(titles)) * 100
                        job.current_title = i + 1
                        await session.commit()
                        await ws_manager.broadcast_job_update(
                            job_id,
                            JobState.RIPPING.value,
                            progress=job.progress_percent,
                            current_title=i + 1,
                        )

                    # Per-track byte progress
                    title_actual = int(step_size * step)
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        TitleState.RIPPING.value,
                        expected_size_bytes=title_bytes,
                        actual_size_bytes=min(title_actual, title_bytes),
                    )

                # Mark title as done ripping — movies skip matching
                post_rip_state = (
                    TitleState.MATCHING if content_type == ContentType.TV else TitleState.MATCHED
                )
                title_db.state = post_rip_state
                await session.commit()
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_db.id,
                    post_rip_state.value,
                    duration_seconds=title_db.duration_seconds,
                    file_size_bytes=title_db.file_size_bytes,
                )

                logger.info(f"[SIMULATE] Job {job_id}: completed realistic rip of title {i}")

            # Move to matching
            job = await session.get(DiscJob, job_id)
            if content_type == ContentType.TV:
                # Wait for subtitle download
                if job_id in self._subtitle_ready:
                    logger.info(f"[SIMULATE] Job {job_id}: waiting for subtitle download...")
                    try:
                        await asyncio.wait_for(self._subtitle_ready[job_id].wait(), timeout=30)
                        logger.info(f"[SIMULATE] Job {job_id}: subtitle download complete")
                        await session.refresh(job)
                    except TimeoutError:
                        logger.warning(f"[SIMULATE] Job {job_id}: subtitle download timed out")

                job.state = JobState.MATCHING
                await session.commit()
                await event_broadcaster.broadcast_job_state_changed(job_id, JobState.MATCHING)
                # Use the real matching simulation with runner_ups
                await self._simulate_matching(job_id, titles, session)
            else:
                job.state = JobState.ORGANIZING
                await session.commit()
                await event_broadcaster.broadcast_job_state_changed(job_id, JobState.ORGANIZING)
                await asyncio.sleep(0.5)
                job.progress_percent = 100.0
                # Transition all movie titles to COMPLETED before completing job
                for title in titles:
                    title_db = await session.get(DiscTitle, title.id)
                    if title_db and title_db.state not in (
                        TitleState.COMPLETED,
                        TitleState.FAILED,
                    ):
                        title_db.state = TitleState.COMPLETED
                        session.add(title_db)
                await session.commit()
                await state_machine.transition_to_completed(job, session)

    async def _simulate_ripping(
        self,
        job_id: int,
        titles: list[DiscTitle],
        speed_multiplier: int,
        content_type: ContentType,
    ) -> None:
        """Simulate the ripping process with realistic progress updates."""
        import random

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            job.state = JobState.RIPPING
            await session.commit()

            total_bytes = sum(t.file_size_bytes for t in titles)
            cumulative_bytes = 0

            for i, title in enumerate(titles):
                current_title = i + 1
                title_bytes = title.file_size_bytes
                steps = max(5, 20 // speed_multiplier)
                step_size = title_bytes / steps

                # Set title to RIPPING with expected size for per-track progress
                title_db = await session.get(DiscTitle, title.id)
                if title_db:
                    title_db.state = TitleState.RIPPING
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        TitleState.RIPPING.value,
                        duration_seconds=title_db.duration_seconds,
                        file_size_bytes=title_db.file_size_bytes,
                        expected_size_bytes=title_bytes,
                        actual_size_bytes=0,
                    )

                for step in range(steps):
                    await asyncio.sleep(0.1 / speed_multiplier)
                    cumulative_bytes += step_size
                    pct = min((cumulative_bytes / total_bytes) * 100, 100)

                    speed_val = random.uniform(3.0, 8.0)
                    remaining = total_bytes - cumulative_bytes
                    eta = int(remaining / (speed_val * 4.5 * 1024 * 1024)) if speed_val > 0 else 0

                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.RIPPING.value,
                        progress=pct,
                        speed=f"{speed_val:.1f}x ({speed_val * 4.5:.1f} M/s)",
                        eta=eta,
                        current_title=current_title,
                        total_titles=len(titles),
                    )

                    # Per-track byte progress
                    title_actual = int(step_size * (step + 1))
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title.id,
                        TitleState.RIPPING.value,
                        expected_size_bytes=title_bytes,
                        actual_size_bytes=min(title_actual, title_bytes),
                    )

                # Title done — movies skip matching
                title_db = await session.get(DiscTitle, title.id)
                if title_db:
                    title_db.output_filename = f"simulated_title_{title.title_index}.mkv"
                    post_rip_state = (
                        TitleState.MATCHING
                        if content_type == ContentType.TV
                        else TitleState.MATCHED
                    )
                    title_db.state = post_rip_state
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        post_rip_state.value,
                        duration_seconds=title_db.duration_seconds,
                        file_size_bytes=title_db.file_size_bytes,
                    )

            # Move to matching
            job = await session.get(DiscJob, job_id)
            if content_type == ContentType.TV:
                # Wait for subtitle download to complete before matching
                if job_id in self._subtitle_ready:
                    logger.info(f"[SIMULATE] Job {job_id}: waiting for subtitle download...")
                    try:
                        await asyncio.wait_for(self._subtitle_ready[job_id].wait(), timeout=10)
                        logger.info(f"[SIMULATE] Job {job_id}: subtitle download complete")
                        # Refresh job to get updated subtitle_status
                        await session.refresh(job)
                    except TimeoutError:
                        logger.warning(f"[SIMULATE] Job {job_id}: subtitle download timed out")

                job.state = JobState.MATCHING
                await session.commit()
                await event_broadcaster.broadcast_job_state_changed(job_id, JobState.MATCHING)
                # Simulate matching
                await self._simulate_matching(job_id, titles, session)
            else:
                job.state = JobState.ORGANIZING
                await session.commit()
                await event_broadcaster.broadcast_job_state_changed(job_id, JobState.ORGANIZING)
                await asyncio.sleep(0.5)
                job.progress_percent = 100.0
                # Transition all movie titles to COMPLETED before completing job
                for title in titles:
                    title_db = await session.get(DiscTitle, title.id)
                    if title_db and title_db.state not in (
                        TitleState.COMPLETED,
                        TitleState.FAILED,
                    ):
                        title_db.state = TitleState.COMPLETED
                        session.add(title_db)
                await session.commit()
                await state_machine.transition_to_completed(job, session)

    async def _simulate_subtitle_download(self, job_id: int, total: int, show_name: str) -> None:
        """Simulate subtitle download events. Fails completely for unknown shows."""
        import random

        from sqlalchemy import update

        # Set initial status to "downloading" — targeted update to avoid overwriting state
        async with async_session() as session:
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(subtitle_status="downloading")
            )
            await session.commit()

        # Simulate subtitle download progress
        downloaded = 0
        for _i in range(total):
            await asyncio.sleep(0.2)
            if random.random() > 0.1:
                downloaded += 1
            await ws_manager.broadcast_subtitle_event(
                job_id, "downloading", downloaded=downloaded, total=total
            )

        failed = total - downloaded
        status = "completed" if failed == 0 else "partial"

        # PERSIST STATUS IN DATABASE — targeted update to avoid overwriting state
        async with async_session() as session:
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(subtitle_status=status)
            )
            await session.commit()

        await ws_manager.broadcast_subtitle_event(
            job_id, status, downloaded=downloaded, total=total, failed_count=failed
        )

        # Always set the event
        if job_id in self._subtitle_ready:
            self._subtitle_ready[job_id].set()

    async def _simulate_matching(
        self,
        job_id: int,
        titles: list[DiscTitle],
        session: AsyncSession,
    ) -> None:
        """Simulate episode matching with random confidence levels."""
        import random

        # Check subtitle status - BLOCK matching if failed
        job = await session.get(DiscJob, job_id)
        subtitle_status = job.subtitle_status if job else None

        if subtitle_status == "failed":
            logger.error(
                f"[SIMULATE] Job {job_id}: BLOCKING matching - subtitle download failed. "
                f"Marking all titles as FAILED."
            )
            # Mark all titles as FAILED
            for title in titles:
                title_db = await session.get(DiscTitle, title.id)
                if title_db:
                    title_db.state = TitleState.FAILED
                    title_db.match_confidence = 0.0
                    title_db.match_details = json.dumps(
                        {
                            "error": "subtitle_download_failed",
                            "message": job.error_message
                            or "Subtitle download failed, cannot match without reference files",
                        }
                    )
                    await session.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        title_db.state.value,
                        matched_episode=None,
                        match_confidence=0.0,
                    )
            # Mark job as FAILED
            await state_machine.transition_to_failed(
                job,
                session,
                error_message="Subtitle download failed - cannot proceed with matching",
            )
            return

        logger.info(
            f"[SIMULATE] Job {job_id}: subtitle status '{subtitle_status}', proceeding with matching simulation"
        )

        needs_review = False
        for i, title in enumerate(titles):
            title_db = await session.get(DiscTitle, title.id)
            if not title_db:
                continue

            # Phase 1: Simulate transcribing/matching progress
            confidence = random.uniform(0.7, 1.0)
            season = 1
            job = await session.get(DiscJob, job_id)
            if job and job.detected_season:
                season = job.detected_season

            episode_code = f"S{season:02d}E{(i + 1):02d}"

            # Generate runner_ups upfront so we can show them during matching
            runner_ups = []
            num_candidates = random.randint(2, 4)
            for j in range(num_candidates):
                alt_episode = f"S{season:02d}E{(i + j + 1):02d}"
                alt_score = confidence if j == 0 else random.uniform(0.3, confidence - 0.1)
                runner_ups.append(
                    {
                        "episode": alt_episode,
                        "score": alt_score,
                        "vote_count": 0,
                    }
                )

            # Simulate voting rounds — candidates accumulate votes
            target_votes = 4
            for vote_round in range(target_votes):
                progress = ((vote_round + 1) / target_votes) * 100.0
                # Increment votes for each candidate
                for ru in runner_ups:
                    if random.random() < ru["score"]:
                        ru["vote_count"] = min(target_votes, ru["vote_count"] + 1)

                interim_details = json.dumps(
                    {
                        "score": confidence,
                        "vote_count": vote_round + 1,
                        "target_votes": target_votes,
                        "runner_ups": runner_ups,
                    }
                )
                await ws_manager.broadcast_title_update(
                    job_id,
                    title_db.id,
                    TitleState.MATCHING.value,
                    match_stage="matching",
                    match_progress=progress,
                    match_details=interim_details,
                )
                await asyncio.sleep(0.4)

            # Phase 2: Final match result
            title_db.matched_episode = episode_code
            title_db.match_confidence = confidence
            title_db.match_details = json.dumps(
                {
                    "score": confidence,
                    "vote_count": min(target_votes, int(confidence * target_votes)),
                    "file_cov": random.uniform(0.6, 0.95),
                    "runner_ups": runner_ups,
                }
            )

            if confidence >= 0.6:
                title_db.state = TitleState.COMPLETED
            else:
                title_db.state = TitleState.MATCHING
                needs_review = True

            await session.commit()
            await ws_manager.broadcast_title_update(
                job_id,
                title_db.id,
                title_db.state.value,
                matched_episode=title_db.matched_episode,
                match_confidence=title_db.match_confidence,
                match_details=title_db.match_details,
                duration_seconds=title_db.duration_seconds,
                file_size_bytes=title_db.file_size_bytes,
            )

        job = await session.get(DiscJob, job_id)
        if not job:
            logger.error(f"[SIMULATE] Job {job_id}: could not load job for completion")
            return

        logger.info(
            f"[SIMULATE] Job {job_id}: matching complete. "
            f"needs_review={needs_review}, job.state={job.state.value}"
        )

        if needs_review:
            await state_machine.transition_to_review(
                job, session, reason="Some episodes have low confidence matches", broadcast=False
            )
            await ws_manager.broadcast_job_update(job_id, job.state.value, progress=0)
        else:
            job.progress_percent = 100.0
            result = await state_machine.transition_to_completed(job, session)
            logger.info(
                f"[SIMULATE] Job {job_id}: transition_to_completed returned {result}, "
                f"job.state={job.state.value}"
            )

    async def advance_job(self, job_id: int) -> str:
        """Manually advance a job to the next state. Returns new state."""
        state_flow = {
            JobState.IDLE: JobState.IDENTIFYING,
            JobState.IDENTIFYING: JobState.RIPPING,
            JobState.RIPPING: JobState.MATCHING,
            JobState.MATCHING: JobState.ORGANIZING,
            JobState.ORGANIZING: JobState.COMPLETED,
            JobState.REVIEW_NEEDED: JobState.RIPPING,
        }

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            next_state = state_flow.get(job.state)
            if not next_state:
                raise ValueError(f"Cannot advance from state: {job.state}")

            job.state = next_state
            job.updated_at = datetime.utcnow()
            if next_state == JobState.COMPLETED:
                job.progress_percent = 100.0
            await session.commit()

            await ws_manager.broadcast_job_update(job_id, next_state.value)
            return next_state.value

    async def _cancel_jobs_for_drive(self, drive_letter: str) -> None:
        """Cancel jobs that need the disc; leave post-ripping jobs running."""
        # Only cancel jobs in states that require the physical disc
        # Note: RIPPING is excluded because MakeMKV might auto-eject at the end, causing a race condition.
        # If the user manually ejects during ripping, MakeMKV will fail with an I/O error anyway.
        disc_required_states = [JobState.IDLE, JobState.IDENTIFYING]
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(
                    DiscJob.drive_id == drive_letter,
                    DiscJob.state.in_(disc_required_states),
                )
            )
            jobs = result.scalars().all()

            for job in jobs:
                await self.cancel_job(job.id)

            if not jobs:
                logger.info(
                    f"Disc removed from {drive_letter} but no jobs need cancelling "
                    "(post-ripping jobs continue)"
                )


# Singleton instance
job_manager = JobManager()
