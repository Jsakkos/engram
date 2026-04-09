"""Job Manager - Thin orchestrator for the disc processing workflow.

Coordinates between the Sentinel, and the extracted coordinators:
- IdentificationCoordinator: disc scanning, classification, DiscDB/TMDB lookup
- MatchingCoordinator: episode matching, subtitle download, DiscDB assignment
- FinalizationCoordinator: conflict resolution, organization, job completion
- CleanupService: staging cleanup, DiscDB export
- SimulationService: test/debug simulation endpoints
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.analyst import DiscAnalyst
from app.core.extractor import MakeMKVExtractor, RipProgress
from app.core.organizer import movie_organizer
from app.core.sentinel import DriveMonitor
from app.core.staging_watcher import StagingWatcher
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.cleanup_service import CleanupService
from app.services.event_broadcaster import EventBroadcaster
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.identification_coordinator import IdentificationCoordinator
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import MatchingCoordinator
from app.services.ripping_helpers import (
    SpeedCalculator,
    resolve_title_from_filename,
)
from app.services.simulation_service import SimulationService

logger = logging.getLogger(__name__)

# Create domain-specific event broadcaster
event_broadcaster = EventBroadcaster(ws_manager)

# Create job state machine
state_machine = JobStateMachine(event_broadcaster)


class JobManager:
    """Manages the lifecycle of disc processing jobs.

    Thin orchestrator that delegates to focused coordinators.
    """

    def __init__(self) -> None:
        self._drive_monitor = DriveMonitor()
        self._extractor = MakeMKVExtractor()
        self._analyst = DiscAnalyst()
        self._active_jobs: dict[int, asyncio.Task] = {}
        self._drive_locks: dict[str, asyncio.Lock] = {}
        self._last_job_created_at: dict[str, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._timed_cleanup_task: asyncio.Task | None = None
        self._staging_watcher: StagingWatcher | None = None

        # Create coordinators
        self._cleanup = CleanupService()
        self._matching = MatchingCoordinator(event_broadcaster, state_machine)
        self._finalization = FinalizationCoordinator(event_broadcaster, state_machine)
        self._identification = IdentificationCoordinator(
            self._analyst, self._extractor, event_broadcaster, state_machine
        )
        self._simulation = SimulationService(event_broadcaster, state_machine)

        # Wire cross-coordinator callbacks
        self._matching.set_callbacks(
            check_job_completion=self._finalization.check_job_completion,
        )
        self._identification.set_callbacks(
            get_discdb_mappings=self._matching.get_discdb_mappings,
            set_discdb_mappings=self._matching.set_discdb_mappings,
            start_subtitle_download=self._matching.start_subtitle_download,
            try_discdb_assignment=self._matching.try_discdb_assignment,
            match_single_file=self._matching.match_single_file,
            on_match_task_done=self._matching.on_match_task_done,
            check_job_completion=self._finalization.check_job_completion,
            run_ripping=self._run_ripping,
            finalize_disc_job=self._finalization.finalize_disc_job,
        )
        self._finalization.set_callbacks(
            run_ripping=self._run_ripping,
            on_task_done=self._on_task_done,
            active_jobs=self._active_jobs,
            match_single_file=self._matching.match_single_file,
        )
        self._simulation.set_callbacks(
            subtitle_ready=self._matching._subtitle_ready,
            subtitle_tasks=self._matching._subtitle_tasks,
            active_jobs=self._active_jobs,
            on_task_done=self._on_task_done,
        )

        # Register terminal job state callbacks
        state_machine.on_terminal_state(self._cleanup.on_job_terminal)
        state_machine.on_terminal_state(self._matching.clear_job_caches)

    async def start(self) -> None:
        """Start the job manager and begin monitoring drives."""
        self._loop = asyncio.get_event_loop()

        await self._cleanup_stale_jobs()
        await self._restore_discdb_mappings()

        self._drive_monitor.set_async_callback(
            self._on_drive_event,
            self._loop,
        )
        self._drive_monitor.start()

        from app.services.config_service import ensure_paths_exist, get_config

        config = await get_config()
        await ensure_paths_exist(config)

        # Initialize matching concurrency limiter
        concurrency = max(1, config.max_concurrent_matches)
        if concurrency != config.max_concurrent_matches:
            logger.warning(
                f"Invalid max_concurrent_matches={config.max_concurrent_matches} "
                f"in config, using {concurrency}"
            )
        self._matching.init_semaphore(concurrency)

        # Start timed staging cleanup if policy is "after_days"
        if config.staging_cleanup_policy == "after_days":
            self._timed_cleanup_task = asyncio.create_task(
                self._cleanup.run_timed_cleanup(config.staging_path, config.staging_cleanup_days)
            )

        # Start staging watcher if enabled
        if config.staging_watch_enabled and config.staging_path:
            self._staging_watcher = StagingWatcher(config.staging_path, config=config)
            self._staging_watcher.set_async_callback(self._on_staging_event, self._loop)
            self._staging_watcher.start()

        logger.info(f"Job manager started (max_concurrent_matches={concurrency})")

    async def _cleanup_stale_jobs(self) -> None:
        """Mark stale jobs as FAILED on startup."""
        stale_states = [
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        ]
        async with async_session() as session:
            result = await session.execute(select(DiscJob).where(DiscJob.state.in_(stale_states)))
            stale_jobs = result.scalars().all()

            if not stale_jobs:
                return

            for job in stale_jobs:
                old_state = job.state
                job.state = JobState.FAILED
                job.error_message = f"Server restarted while job was in {old_state.value} state"
                job.updated_at = datetime.now(UTC)
                logger.info(f"Cleaned up stale job {job.id} (was {old_state.value}, now FAILED)")

            await session.commit()
            logger.info(f"Cleaned up {len(stale_jobs)} stale job(s) from previous run")

    async def _restore_discdb_mappings(self) -> None:
        """Restore in-memory DiscDB mappings from database for active jobs."""
        from app.core.discdb_classifier import DiscDbTitleMapping

        active_states = [JobState.REVIEW_NEEDED, JobState.RIPPING, JobState.MATCHING]
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(
                    DiscJob.discdb_mappings_json.is_not(None),
                    DiscJob.state.in_(active_states),
                )
            )
            for job in result.scalars():
                mappings_data = json.loads(job.discdb_mappings_json)
                self._matching.set_discdb_mappings(
                    job.id, [DiscDbTitleMapping(**m) for m in mappings_data]
                )
                logger.info(f"Restored {len(mappings_data)} DiscDB mappings for job {job.id}")

    async def stop(self) -> None:
        """Stop the job manager and clean up."""
        self._drive_monitor.stop()
        if self._staging_watcher:
            self._staging_watcher.stop()

        for job_id, task in self._active_jobs.items():
            task.cancel()
            logger.info(f"Cancelled job {job_id}")

        self._active_jobs.clear()
        logger.info("Job manager stopped")

    # --- Drive/Staging Event Handlers ---

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
            await self._cancel_jobs_for_drive(drive_letter)

        if event == "inserted":
            await event_broadcaster.broadcast_drive_inserted(drive_letter, volume_label)
        else:
            await event_broadcaster.broadcast_drive_removed(drive_letter, volume_label)

    async def _on_staging_event(self, event: str, staging_dir: str, label: str) -> None:
        """Handle new staging directory detection from StagingWatcher."""
        logger.info(f"Staging event: {event} dir={staging_dir} label={label}")
        if event == "staging_ready":
            try:
                await self.create_job_from_staging(
                    staging_path=staging_dir,
                    volume_label=label,
                    content_type="unknown",
                    detected_title=None,
                    detected_season=None,
                )
            except Exception as e:
                logger.error(
                    f"Failed to create job from staging directory {staging_dir}: {e}",
                    exc_info=True,
                )

    # --- Job Creation ---

    async def _create_job_for_disc(self, drive_letter: str, volume_label: str) -> None:
        """Create a new job when a disc is inserted."""
        if drive_letter not in self._drive_locks:
            self._drive_locks[drive_letter] = asyncio.Lock()

        async with self._drive_locks[drive_letter]:
            async with async_session() as session:
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

                last_created = self._last_job_created_at.get(drive_letter, 0)
                if time.monotonic() - last_created < 15:
                    logger.info(
                        f"Skipping job creation for {drive_letter}: "
                        f"cooldown ({time.monotonic() - last_created:.0f}s since last)"
                    )
                    return

                from app.services.config_service import get_config as get_db_config

                db_config = await get_db_config()
                staging_dir = (
                    Path(db_config.staging_path).expanduser()
                    / f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )

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
                self._last_job_created_at[drive_letter] = time.monotonic()

                task = asyncio.create_task(self._identification.identify_disc(job.id))
                self._active_jobs[job.id] = task

    async def create_job_from_staging(
        self,
        staging_path: str,
        volume_label: str = "",
        content_type: str = "unknown",
        detected_title: str | None = None,
        detected_season: int | None = None,
    ) -> int:
        """Create a job from pre-ripped MKV files in a staging directory."""
        staging_dir = Path(staging_path)

        if not volume_label:
            volume_label = staging_dir.name.upper().replace(" ", "_")

        async with async_session() as session:
            job = DiscJob(
                drive_id="staging",
                volume_label=volume_label,
                staging_path=str(staging_dir),
                state=JobState.IDENTIFYING,
            )

            if content_type in ("tv", "movie"):
                job.content_type = ContentType(content_type)
            if detected_title:
                job.detected_title = detected_title
            if detected_season is not None:
                job.detected_season = detected_season

            session.add(job)
            await session.commit()
            await session.refresh(job)

            job_id = job.id
            logger.info(
                f"Created staging import job {job_id} from {staging_path} (label: {volume_label})"
            )

        await event_broadcaster.broadcast_drive_inserted("staging", volume_label)

        task = asyncio.create_task(self._identification.identify_from_staging(job_id))
        self._active_jobs[job_id] = task

        return job_id

    # --- Public API (delegated to coordinators) ---

    async def set_name_and_resume(
        self,
        job_id: int,
        name: str,
        content_type_str: str,
        season: int | None = None,
    ) -> None:
        """Set a user-provided name for an unlabeled disc and resume ripping."""
        await self._identification.set_name_and_resume(job_id, name, content_type_str, season)

        task = asyncio.create_task(self._run_ripping(job_id))
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def re_identify_job(
        self,
        job_id: int,
        title: str,
        content_type_str: str,
        season: int | None = None,
        tmdb_id: int | None = None,
    ) -> None:
        """Re-identify a job with user-corrected metadata."""
        result = await self._identification.re_identify(
            job_id, title, content_type_str, season, tmdb_id
        )

        if result["has_ripped"]:
            # Post-rip: re-run matching for existing files
            task = asyncio.create_task(self._rerun_matching(job_id))
        else:
            # Pre-rip: start ripping with corrected metadata
            task = asyncio.create_task(self._run_ripping(job_id))

        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def _rerun_matching(self, job_id: int, source_preference: str | None = None) -> None:
        """Re-run episode matching for already-ripped titles."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job or not job.staging_path:
                return

            titles_result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.is_selected == True,  # noqa: E712
                )
            )
            disc_titles = titles_result.scalars().all()

            staging = Path(job.staging_path)

            if source_preference == "discdb":
                # Restore all titles from stored DiscDB match details
                for title in disc_titles:
                    if title.discdb_match_details:
                        title.match_details = title.discdb_match_details
                        title.match_source = "discdb"
                        title.match_confidence = 0.99
                        title.state = TitleState.MATCHED
                        session.add(title)
                await session.commit()
            else:
                for title in disc_titles:
                    # Reset title state for re-matching
                    title.state = TitleState.MATCHING
                    title.matched_episode = None
                    title.match_confidence = None
                    title.match_details = None
                    session.add(title)

                await session.commit()

                # Start matching for each ripped file
                for title in disc_titles:
                    if title.output_filename:
                        file_path = Path(title.output_filename)
                        if not file_path.exists():
                            file_path = staging / file_path.name
                        if file_path.exists():
                            match_task = asyncio.create_task(
                                self._matching.match_single_file(job_id, title.id, file_path)
                            )
                            match_task.add_done_callback(
                                lambda t, jid=job_id, tid=title.id: (
                                    self._matching.on_match_task_done(t, jid, tid)
                                )
                            )

            logger.info(
                f"Job {job_id}: re-matching {len(disc_titles)} titles with corrected metadata"
            )

    async def start_ripping(self, job_id: int) -> None:
        """Start the ripping process for a job."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state not in (JobState.IDLE, JobState.REVIEW_NEEDED):
                raise ValueError(f"Cannot start job in state: {job.state}")

            job.state = JobState.RIPPING
            job.updated_at = datetime.now(UTC)
            await session.commit()

            task = asyncio.create_task(self._run_ripping(job_id))
            task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
            self._active_jobs[job_id] = task

    async def cancel_job(self, job_id: int) -> None:
        """Cancel a running job."""
        if job_id in self._active_jobs:
            self._active_jobs[job_id].cancel()
            del self._active_jobs[job_id]

        self._extractor.cancel(job_id)

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
        await self._finalization.apply_review(job_id, title_id, episode_code, edition)

    async def reassign_episode(
        self,
        job_id: int,
        title_id: int,
        episode_code: str,
        edition: str | None = None,
    ) -> None:
        """Manually reassign an episode for a title. Sets match_source='user'."""
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError(f"Title {title_id} not found for job {job_id}")

            title.matched_episode = episode_code
            title.match_confidence = 1.0
            title.match_source = "user"
            if edition is not None:
                title.edition = edition
            if title.state != TitleState.MATCHED:
                title.state = TitleState.MATCHED
            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                TitleState.MATCHED.value,
                matched_episode=episode_code,
                match_confidence=1.0,
            )

        logger.info(f"Job {job_id}: title {title_id} manually reassigned to {episode_code}")

    async def rematch_single_title(
        self, job_id: int, title_id: int, source_preference: str | None = None
    ) -> None:
        """Re-match a single title. Delegates to matching coordinator."""
        await self._matching.rematch_single_title(job_id, title_id, source_preference)

    async def process_matched_titles(self, job_id: int) -> dict:
        """Process all matched titles for a job."""
        return await self._finalization.process_matched_titles(job_id)

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
            job.updated_at = datetime.now(UTC)
            if next_state == JobState.COMPLETED:
                job.progress_percent = 100.0
            await session.commit()

            await ws_manager.broadcast_job_update(job_id, next_state.value)
            return next_state.value

    # --- Simulation (delegated) ---

    async def simulate_disc_insert(self, params: dict) -> int:
        """Simulate a disc insertion for testing purposes."""
        return await self._simulation.simulate_disc_insert(params)

    async def simulate_disc_insert_realistic(self, params: dict) -> int:
        """Simulate disc insertion using real MKV files from staging."""
        return await self._simulation.simulate_disc_insert_realistic(params)

    # --- Ripping ---

    async def _run_ripping(self, job_id: int) -> None:
        """Execute the ripping process."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            title_count = job.total_titles or 0

            await ws_manager.broadcast_job_update(
                job_id, JobState.RIPPING.value, current_title=1, total_titles=title_count
            )

            try:
                output_dir = Path(job.staging_path)

                # Calculate total size of selected titles
                total_job_bytes = 0
                title_sizes = {}

                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                disc_titles = titles_result.scalars().all()

                has_selection = any(dt.is_selected for dt in disc_titles)
                for t in disc_titles:
                    if has_selection and not t.is_selected:
                        continue

                    total_job_bytes += t.file_size_bytes
                    title_sizes[t.title_index] = t.file_size_bytes

                if not has_selection and job.content_type == ContentType.MOVIE and disc_titles:
                    longest = max(disc_titles, key=lambda t: t.duration_seconds or 0)
                    longest.is_selected = True
                    async with async_session() as select_session:
                        db_title = await select_session.get(DiscTitle, longest.id)
                        if db_title:
                            db_title.is_selected = True
                            await select_session.commit()
                    has_selection = True

                titles_to_rip = disc_titles
                if has_selection:
                    titles_to_rip = [dt for dt in disc_titles if dt.is_selected]

                # Safety net: transition deselected PENDING titles to terminal state
                deselected_ids = [
                    dt.id
                    for dt in disc_titles
                    if not dt.is_selected and dt.state == TitleState.PENDING
                ]
                if deselected_ids:
                    async with async_session() as cleanup_session:
                        for tid in deselected_ids:
                            dt = await cleanup_session.get(DiscTitle, tid)
                            if dt and dt.state == TitleState.PENDING:
                                dt.state = TitleState.COMPLETED
                                dt.is_extra = True
                                if not dt.match_details:
                                    dt.match_details = json.dumps(
                                        {"reason": "Deselected from ripping"}
                                    )
                                logger.info(
                                    f"Job {job_id}: Safety net — title {dt.title_index} "
                                    f"deselected+PENDING → COMPLETED/extra"
                                )
                        await cleanup_session.commit()

                sorted_titles = sorted(titles_to_rip, key=lambda t: t.title_index)

                for t in disc_titles:
                    session.expunge(t)

                speed_calc = SpeedCalculator(total_job_bytes)

                _titles_marked_ripping: set[int] = set()
                _last_title_idx: int | None = None
                _title_file_cache: dict[int, Path] = {}
                _progress_lock = asyncio.Lock()
                _background_tasks: set[asyncio.Task] = set()

                # Progress callback
                async def progress_callback(progress: RipProgress) -> None:
                    nonlocal _last_title_idx

                    async with _progress_lock:
                        logger.debug(
                            f"Job {job_id}: PRGV progress update — "
                            f"title={progress.current_title}, percent={progress.percent:.1f}%, "
                            f"total_titles={progress.total_titles}"
                        )
                        current_idx = progress.current_title

                        active_title_size = 0
                        active_title = None

                        if 0 <= (current_idx - 1) < len(sorted_titles):
                            active_title = sorted_titles[current_idx - 1]
                            active_title_size = active_title.file_size_bytes

                        current_title_bytes = int((progress.percent / 100.0) * active_title_size)

                        if _last_title_idx is not None and current_idx != _last_title_idx:
                            prev_list_idx = _last_title_idx - 1
                            if 0 <= prev_list_idx < len(sorted_titles):
                                prev_title = sorted_titles[prev_list_idx]
                                try:
                                    async with async_session() as sess:
                                        prev_db = await sess.get(DiscTitle, prev_title.id)
                                        if prev_db and prev_db.state == TitleState.RIPPING:
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
                            _titles_marked_ripping.add(active_title.id)

                        if active_title and active_title.file_size_bytes:
                            if active_title.id in _titles_marked_ripping:
                                actual_bytes = current_title_bytes
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
                                    pass
                                await ws_manager.broadcast_title_update(
                                    job_id,
                                    active_title.id,
                                    TitleState.RIPPING.value,
                                    expected_size_bytes=active_title.file_size_bytes,
                                    actual_size_bytes=min(
                                        actual_bytes, active_title.file_size_bytes
                                    ),
                                )

                # Title complete callback — called from extractor thread
                def on_title_complete(idx: int, path: Path):
                    logger.info(
                        f"[CALLBACK] Title complete: idx={idx} path={path.name} (Job {job_id})"
                    )
                    future = asyncio.run_coroutine_threadsafe(
                        self._on_title_ripped(job_id, idx, path, sorted_titles),
                        self._loop,
                    )

                    def _check_result(fut):
                        try:
                            fut.result(timeout=30)
                        except TimeoutError as e:
                            logger.error(
                                f"[CALLBACK] _on_title_ripped timed out for {path.name} "
                                f"(Job {job_id}): {e}"
                            )
                        except Exception as e:
                            logger.exception(
                                f"[CALLBACK] _on_title_ripped failed for "
                                f"{path.name} (Job {job_id}): {e}"
                            )

                    future.add_done_callback(_check_result)

                # Error callback — called from extractor thread on stall
                def on_title_error(cmd_idx: int, reason: str):
                    logger.warning(
                        f"[CALLBACK] Title error: cmd_idx={cmd_idx} reason={reason} (Job {job_id})"
                    )
                    asyncio.run_coroutine_threadsafe(
                        self._on_title_error(job_id, cmd_idx, reason, sorted_titles),
                        self._loop,
                    )

                from app.services.config_service import get_config

                rip_config = await get_config()

                rip_indices = [t.title_index for t in sorted_titles]
                stall_timeout = rip_config.ripping_stall_timeout if rip_config else 120.0

                if stall_timeout and stall_timeout > 0:
                    pass
                elif len(rip_indices) == len(disc_titles):
                    rip_indices = None

                def _fire_progress(p):
                    t = asyncio.create_task(progress_callback(p))
                    _background_tasks.add(t)
                    t.add_done_callback(_background_tasks.discard)

                # Filesystem-based progress monitor
                async def _filesystem_progress_monitor():
                    _prev_file_sizes: dict[int, int] = {}

                    while True:
                        await asyncio.sleep(2.0)
                        try:
                            async with _progress_lock:
                                total_done = 0
                                current_title_num = 0
                                file_sizes: dict[int, int] = {}
                                for t in sorted_titles:
                                    pattern = f"*_t{t.title_index:02d}.mkv"
                                    matches = list(output_dir.glob(pattern))
                                    if matches:
                                        file_sizes[t.id] = matches[0].stat().st_size

                                active_title_id = None
                                for t in sorted_titles:
                                    if t.id not in file_sizes:
                                        continue
                                    fsize = file_sizes[t.id]
                                    prev = _prev_file_sizes.get(t.id, 0)
                                    if fsize > prev and fsize > 0:
                                        active_title_id = t.id

                                for i, t in enumerate(sorted_titles):
                                    if t.id not in file_sizes:
                                        continue
                                    fsize = file_sizes[t.id]
                                    total_done += fsize

                                    if t.id == active_title_id:
                                        current_title_num = i + 1

                                        if t.id not in _titles_marked_ripping:
                                            try:
                                                async with async_session() as sess:
                                                    title_db = await sess.get(DiscTitle, t.id)
                                                    if title_db and title_db.state in (
                                                        TitleState.PENDING,
                                                        TitleState.RIPPING,
                                                    ):
                                                        title_db.state = TitleState.RIPPING
                                                        await sess.commit()
                                                        await ws_manager.broadcast_title_update(
                                                            job_id,
                                                            title_db.id,
                                                            TitleState.RIPPING.value,
                                                            duration_seconds=title_db.duration_seconds,
                                                            file_size_bytes=title_db.file_size_bytes,
                                                            expected_size_bytes=t.file_size_bytes,
                                                            actual_size_bytes=fsize,
                                                        )
                                            except Exception:
                                                logger.debug(
                                                    f"Job {job_id}: failed to set title {t.id} "
                                                    f"to RIPPING in fs monitor",
                                                    exc_info=True,
                                                )
                                            _titles_marked_ripping.add(t.id)

                                        await ws_manager.broadcast_title_update(
                                            job_id,
                                            t.id,
                                            TitleState.RIPPING.value,
                                            expected_size_bytes=t.file_size_bytes,
                                            actual_size_bytes=fsize,
                                        )
                                    elif (
                                        t.id in _titles_marked_ripping
                                        and active_title_id is not None
                                        and active_title_id != t.id
                                    ):
                                        new_state = (
                                            TitleState.MATCHING
                                            if job.content_type == ContentType.TV
                                            else TitleState.MATCHED
                                        )
                                        try:
                                            async with async_session() as sess:
                                                title_db = await sess.get(DiscTitle, t.id)
                                                if (
                                                    title_db
                                                    and title_db.state == TitleState.RIPPING
                                                ):
                                                    title_db.state = new_state
                                                    await sess.commit()
                                                    await ws_manager.broadcast_title_update(
                                                        job_id,
                                                        title_db.id,
                                                        new_state.value,
                                                        expected_size_bytes=t.file_size_bytes,
                                                        actual_size_bytes=fsize,
                                                    )
                                        except Exception:
                                            logger.debug(
                                                f"Job {job_id}: failed to transition title "
                                                f"{t.id} out of RIPPING in fs monitor",
                                                exc_info=True,
                                            )
                                        _titles_marked_ripping.discard(t.id)

                                _prev_file_sizes.update(file_sizes)

                            if total_job_bytes > 0:
                                pct = min((total_done / total_job_bytes) * 100, 100.0)
                                speed_calc.update(total_done)

                                try:
                                    async with async_session() as prog_session:
                                        db_job = await prog_session.get(DiscJob, job_id)
                                        if db_job:
                                            db_job.progress_percent = pct
                                            await prog_session.commit()
                                except Exception:
                                    logger.debug(
                                        f"Job {job_id}: failed to update progress in DB",
                                        exc_info=True,
                                    )

                                await ws_manager.broadcast_job_update(
                                    job_id,
                                    JobState.RIPPING.value,
                                    progress=pct,
                                    speed=speed_calc.speed_str,
                                    eta=speed_calc.eta_seconds,
                                    current_title=current_title_num or 1,
                                    total_titles=len(sorted_titles),
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.debug(
                                f"Job {job_id}: filesystem progress monitor error",
                                exc_info=True,
                            )

                monitor_task = asyncio.create_task(_filesystem_progress_monitor())
                _background_tasks.add(monitor_task)
                monitor_task.add_done_callback(_background_tasks.discard)

                # Run extraction
                try:
                    from app.core.discdb_exporter import get_makemkv_log_dir

                    result = await self._extractor.rip_titles(
                        job.drive_id,
                        output_dir,
                        title_indices=rip_indices,
                        progress_callback=_fire_progress,
                        title_complete_callback=on_title_complete,
                        stall_timeout=stall_timeout,
                        title_error_callback=on_title_error,
                        log_dir=get_makemkv_log_dir(job_id),
                        job_id=job_id,
                    )
                finally:
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except asyncio.CancelledError:
                        pass

                if not result.success and not result.stalled_titles:
                    await state_machine.transition_to_failed(
                        job, session, error_message=result.error_message
                    )
                    return

                # Fallback: mark stalled titles as FAILED
                if result.stalled_titles:
                    async with async_session() as stall_session:
                        for cmd_idx in result.stalled_titles:
                            list_idx = cmd_idx - 1
                            if 0 <= list_idx < len(sorted_titles):
                                stalled_title = sorted_titles[list_idx]
                                db_title = await stall_session.get(DiscTitle, stalled_title.id)
                                if db_title and db_title.state not in (
                                    TitleState.COMPLETED,
                                    TitleState.MATCHED,
                                    TitleState.FAILED,
                                ):
                                    db_title.state = TitleState.FAILED
                                    db_title.match_details = json.dumps(
                                        {"reason": "Ripping stalled — possible disc damage"}
                                    )
                                    logger.warning(
                                        f"Job {job_id}: title {db_title.title_index} "
                                        f"marked FAILED (ripping stall, fallback)"
                                    )
                                    await ws_manager.broadcast_title_update(
                                        job_id,
                                        db_title.id,
                                        TitleState.FAILED.value,
                                        error="Ripping stalled — possible disc damage",
                                    )
                        await stall_session.commit()

                # Eject disc
                try:
                    from app.core.sentinel import eject_disc

                    await asyncio.to_thread(eject_disc, job.drive_id)
                except (OSError, RuntimeError) as e:
                    logger.warning(f"Could not eject disc from {job.drive_id}: {e}")

                if job.content_type == ContentType.TV:
                    logger.info(
                        f"[RIP-DONE] Job {job_id}: rip_titles returned, "
                        f"{len(result.output_files)} files produced. "
                        f"Job state={job.state.value}. Running backfill..."
                    )
                    await self._backfill_unmatched_titles(
                        job_id, Path(job.staging_path), sorted_titles
                    )

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
                    # Movie post-ripping flow
                    ripped_titles = [t for t in disc_titles if t.is_selected]

                    if len(ripped_titles) > 1:
                        discdb_maps = self._matching.get_discdb_mappings(job_id)
                        main_movie_idx = None
                        for m in discdb_maps:
                            if m.title_type == "MainMovie":
                                main_movie_idx = m.index
                                break

                        if main_movie_idx is not None:
                            for dt in ripped_titles:
                                if dt.title_index == main_movie_idx:
                                    dt.is_selected = True
                                else:
                                    dt.is_selected = False
                                session.add(dt)
                            await session.commit()
                            ripped_titles = [
                                t for t in ripped_titles if t.title_index == main_movie_idx
                            ]
                            logger.info(
                                f"Job {job_id}: TheDiscDB auto-selected MainMovie "
                                f"title index {main_movie_idx}, skipping review"
                            )
                        else:
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
                                f"Job {job_id}: Multiple movie versions ripped. "
                                f"Waiting for user selection."
                            )
                            return

                    # Single title flow (Standard Movie)
                    job.state = JobState.ORGANIZING
                    await session.commit()
                    await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

                    Path(job.staging_path)

                    organize_result = await asyncio.to_thread(
                        movie_organizer.organize,
                        Path(job.staging_path),
                        job.volume_label,
                        job.detected_title,
                    )

                    if organize_result["success"]:
                        job.final_path = str(organize_result["main_file"])
                        job.progress_percent = 100.0

                        result = await session.execute(
                            select(DiscTitle).where(DiscTitle.job_id == job_id)
                        )
                        for t in result.scalars().all():
                            if t.state not in (
                                TitleState.COMPLETED,
                                TitleState.FAILED,
                            ):
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
                            job,
                            session,
                            error_message=organize_result["error"],
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
        """Handle completion of a single title rip."""
        async with async_session() as session:
            title = await resolve_title_from_filename(
                path, sorted_titles, rip_index, job_id, session
            )
            if not title:
                return

            title.output_filename = str(path)

            job = await session.get(DiscJob, job_id)
            if title.state in (TitleState.PENDING, TitleState.RIPPING):
                if job and job.content_type == ContentType.TV:
                    title.state = TitleState.MATCHING
                else:
                    title.state = TitleState.MATCHED

            session.add(title)
            await session.commit()
            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                duration_seconds=title.duration_seconds,
                file_size_bytes=title.file_size_bytes,
                output_filename=str(path),
            )

            logger.info(
                f"Title detected: {path.name} → title_index={title.title_index} "
                f"(Title {title.id}, Job {job_id}) — queuing for matching"
            )

            job = await session.get(DiscJob, job_id)
            if job and job.content_type == ContentType.TV:
                discdb_applied = await self._matching.try_discdb_assignment(job_id, title, session)
                if discdb_applied:
                    await self._finalization.check_job_completion(session, job_id)
                else:
                    task = asyncio.create_task(
                        self._matching.match_single_file(job_id, title.id, path)
                    )
                    task.add_done_callback(
                        lambda t, jid=job_id, tid=title.id: self._matching.on_match_task_done(
                            t, jid, tid
                        )
                    )

    async def _on_title_error(
        self,
        job_id: int,
        cmd_idx: int,
        reason: str,
        sorted_titles: list[DiscTitle],
    ) -> None:
        """Handle a title error (e.g., ripping stall)."""
        list_idx = cmd_idx - 1
        if not (0 <= list_idx < len(sorted_titles)):
            logger.error(
                f"Job {job_id}: title error cmd_idx={cmd_idx} out of range "
                f"(sorted_titles has {len(sorted_titles)} entries)"
            )
            return

        stalled_title = sorted_titles[list_idx]
        async with async_session() as session:
            db_title = await session.get(DiscTitle, stalled_title.id)
            if not db_title:
                return
            if db_title.state in (TitleState.COMPLETED, TitleState.MATCHED):
                return

            db_title.state = TitleState.FAILED
            db_title.match_details = json.dumps({"reason": reason})
            await session.commit()

            logger.warning(f"Job {job_id}: title {db_title.title_index} marked FAILED ({reason})")
            await ws_manager.broadcast_title_update(
                job_id,
                db_title.id,
                TitleState.FAILED.value,
                error=reason,
            )

    async def _backfill_unmatched_titles(
        self, job_id: int, staging_dir: Path, sorted_titles: list[DiscTitle]
    ) -> None:
        """Scan staging dir for .mkv files not yet assigned to a title."""
        import re as _re

        async with async_session() as session:
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            titles = result.scalars().all()
            assigned_indices = {t.title_index for t in titles if t.output_filename is not None}

            mkv_files = list(staging_dir.glob("*.mkv")) if staging_dir.exists() else []

            for mkv in mkv_files:
                idx_match = _re.search(r"t(\d+)\.mkv$", mkv.name, _re.IGNORECASE)
                if not idx_match:
                    idx_match = _re.search(r"title[_]?(\d+)\.mkv$", mkv.name, _re.IGNORECASE)
                if not idx_match:
                    continue

                title_index = int(idx_match.group(1))
                if title_index in assigned_indices:
                    continue

                logger.info(
                    f"Backfill: found unmatched file {mkv.name} "
                    f"(title_index={title_index}, Job {job_id})"
                )
                await self._on_title_ripped(job_id, 0, mkv, sorted_titles)

    def _on_task_done(self, task: asyncio.Task, job_id: int) -> None:
        """Callback for background tasks to log any unhandled exceptions."""
        if task.cancelled():
            logger.info(f"Job {job_id} task was cancelled")
        elif exc := task.exception():
            logger.error(f"Job {job_id} task failed with exception: {exc}", exc_info=exc)

    async def _cancel_jobs_for_drive(self, drive_letter: str) -> None:
        """Cancel jobs that need the disc; leave post-ripping jobs running."""
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

    # --- Convenience access for routes (subtitle download) ---

    async def _download_subtitles(self, job_id: int, show_name: str, season: int) -> None:
        """Download subtitles — exposed for test routes."""
        await self._matching.download_subtitles(job_id, show_name, season)


# Singleton instance
job_manager = JobManager()
