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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.contribution_correction import NewTarget

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.analyst import DiscAnalyst
from app.core.extractor import (
    STALL_FAILURE_REASON,
    MakeMKVExtractor,
    compute_content_hash,
)
from app.core.log_context import with_job_log_context
from app.core.organizer import movie_organizer
from app.core.security import sanitize_log_value
from app.core.sentinel import DriveMonitor
from app.database import async_session
from app.models import TERMINAL_JOB_STATES, DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.cleanup_service import CleanupService
from app.services.event_broadcaster import EventBroadcaster
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.identification_coordinator import (
    NO_TITLE_REVIEW_REASON,
    IdentificationCoordinator,
)
from app.services.identity_prompts import BLOCKING_KINDS, ResumeAction
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import (
    INCOMPLETE_RIP_MESSAGE,
    RIP_FAILURE_ERROR_CODES,
    STRICT_MIN_VOTES,
    STRICT_SCAN_POINTS,
    MatchingCoordinator,
)
from app.services.ripping_helpers import (
    SpeedCalculator,
    expected_native_index,
    resolve_title_from_filename,
)
from app.services.simulation_service import SimulationService
from app.services.transcription_prewarm import TranscriptionPrewarmer

# Per-disc ContentHash is computable the instant a disc mounts (validated on
# real hardware: +0.00s after mount, 4/4 inserts). The retry exists only for a
# cold disc that is mounted-but-not-yet-readable and is essentially never used.
_DISC_HASH_RETRY_ATTEMPTS = 3
_DISC_HASH_RETRY_DELAY = 0.5  # seconds between attempts

logger = logging.getLogger(__name__)

# match_details keys that describe a PRIOR match attempt's review reason. A fresh
# user assignment supersedes them, so they must be dropped on reassignment (else a
# stale "file_exists" keeps the Inspector badge lit and "forced_review" wrongly
# marks the user's pick non-rematchable).
_REVIEW_REASON_KEYS = ("error", "message", "forced_review")

# Fallback review_reason when a blocking identity prompt is malformed or carries
# no usable reason text (walk-away Phase B). Reuses the staging-import no-title
# literal (shared constant — identification_coordinator owns it); it matches NO
# classifyPromptJob substring, so the job is resolved on the review page (no
# auto-modal) — same UX as that established path.
_FALLBACK_IDENTITY_REVIEW_REASON = NO_TITLE_REVIEW_REASON


def _strip_review_flags(match_details: str | None) -> str | None:
    """Remove stale review-reason keys from a match_details JSON string.

    Returns the cleaned JSON, ``None`` if nothing meaningful remains, or the
    original value unchanged when it is missing/unparseable.
    """
    if not match_details:
        return match_details
    try:
        parsed = json.loads(match_details)
    except (json.JSONDecodeError, TypeError):
        return match_details
    if not isinstance(parsed, dict):
        return match_details
    cleaned = {k: v for k, v in parsed.items() if k not in _REVIEW_REASON_KEYS}
    return json.dumps(cleaned) if cleaned else None


def _is_auto_rerippable(title: "DiscTitle") -> bool:
    """True if a REVIEW title is a rip failure still eligible for an auto re-rip."""
    if not title.match_details:
        return False
    try:
        details = json.loads(title.match_details)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(details, dict):
        return False
    return bool(details.get("rerip_eligible")) and details.get("error") in RIP_FAILURE_ERROR_CODES


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
        # Per staging-path lock guarding create_job_from_staging's check->insert,
        # mirroring _drive_locks for the disc path. create_job_from_staging is
        # reachable from the manual import endpoints (POST /api/import/start and
        # POST /api/staging/import) and simulation, so concurrent calls for the
        # same path must not both pass the dedup guard.
        self._staging_locks: dict[str, asyncio.Lock] = {}
        self._last_job_created_at: dict[str, float] = {}
        # Discs detected before first-run setup completed (P12): drive → label.
        # In-memory only — the sentinel re-fires "inserted" for discs already in
        # the drive on startup, so a restart mid-setup re-parks them.
        self._parked_discs: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._timed_cleanup_task: asyncio.Task | None = None
        # Stale-job watchdog: monotonic timestamp of the last progress signal per job.
        self._last_activity: dict[int, float] = {}
        self._watchdog_task: asyncio.Task | None = None
        # Title ids with a live (spawned, not yet finished) match task. Guards
        # _dispatch_title_match against double-spawning a title's match: the
        # QUEUED→MATCHING flip happens only post-semaphore in match_single_file,
        # so "still QUEUED" alone can't distinguish an undispatched title from
        # one whose task is parked waiting for a slot. Entries are removed by
        # the task's done callback (runs on success, failure, and cancel).
        self._inflight_match_dispatch: set[int] = set()

        # Create coordinators
        self._cleanup = CleanupService()
        self._matching = MatchingCoordinator(event_broadcaster, state_machine)
        self._finalization = FinalizationCoordinator(event_broadcaster, state_machine)
        self._identification = IdentificationCoordinator(
            self._analyst, self._extractor, event_broadcaster, state_machine
        )
        self._simulation = SimulationService(event_broadcaster, state_machine)
        # Transcript prewarmer: fills the persistent ASR cache while a job sits
        # in review, so the user's eventual re-match is near-instant. It draws
        # chunks through the SAME match semaphore as live matching (acquired
        # per chunk), so real matches always preempt it between chunks.
        self._prewarmer = TranscriptionPrewarmer(
            semaphore_provider=lambda: self._matching._match_semaphore
        )

        # Wire cross-coordinator callbacks
        self._matching.set_callbacks(
            check_job_completion=self._finalization.check_job_completion,
            note_activity=self._note_activity,
        )
        self._identification.set_callbacks(
            get_discdb_mappings=self._matching.get_discdb_mappings,
            set_discdb_mappings=self._matching.set_discdb_mappings,
            start_subtitle_download=self._matching.start_subtitle_download,
            start_subtitle_download_all_seasons=self._matching.start_subtitle_download_all_seasons,
            restart_subtitle_download=self._matching.restart_subtitle_download,
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
            rematch_conflict=self._matching.rematch_conflict,
            rematch_title=self._matching.rematch_single_title,
            note_activity=self._note_activity,
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
        state_machine.on_terminal_state(self._finalization.on_terminal_clear_conflicts)
        state_machine.on_terminal_state(self._prewarmer.on_job_terminal)
        # Walk-away Phase C: enqueue a whole-disc layout contribution when a job
        # COMPLETES (FAILED jobs never contribute). Best-effort — a raised enqueue
        # is caught here and never crashes the terminal-state dispatch.
        state_machine.on_terminal_state(self._enqueue_disc_contribution_on_terminal)
        state_machine.on_terminal_state(self._notify_discord_on_terminal)

        # Reset the watchdog activity clock whenever a job changes phase.
        state_machine.on_transition(self._note_activity_on_transition)
        # Prewarm transcripts whenever a job parks in review. Registering on the
        # state machine is the single chokepoint every review-parking path flows
        # through (finalization's check_job_completion, the ambiguous-movie
        # post-rip review, FILE_EXISTS conflicts, ...) and fires only AFTER the
        # REVIEW_NEEDED transition committed. Pre-rip reviews (e.g. name prompt)
        # also land here, but with no files on disk the task no-ops cheaply.
        state_machine.on_transition(self._start_prewarm_on_review)

    async def start(self) -> None:
        """Start the job manager and begin monitoring drives."""
        self._loop = asyncio.get_event_loop()

        await self._cleanup_stale_jobs()
        await self._restore_discdb_mappings()
        await self._recover_organizing_jobs()

        self._drive_monitor.set_async_callback(
            self._on_drive_event,
            self._loop,
        )
        self._drive_monitor.start()

        from app.services.config_service import ensure_paths_exist, get_config

        config = await get_config()
        await ensure_paths_exist(config)

        # Resolve the effective ASR device ONCE, here, after (optionally) registering the
        # CUDA runtime — every other call site reads this decision via detect_asr_device(),
        # so the badge, the semaphore, and the model loader can't disagree. GPU is used only
        # when the user enabled it AND an NVIDIA GPU is present AND the cuDNN/cuBLAS libs are
        # installed and register cleanly; otherwise we stay on CPU.
        from app.matcher.asr_models import (
            gpu_detected,
            resolve_asr_runtime,
            set_asr_device,
        )
        from app.matcher.cuda_runtime import register_cuda_runtime

        asr_device = "cpu"
        if config.enable_gpu_acceleration and gpu_detected():
            if register_cuda_runtime():
                asr_device = "cuda"
            else:
                logger.warning(
                    "GPU acceleration is enabled but the CUDA runtime libraries are not "
                    "installed; falling back to CPU. Re-enable GPU in Settings to download them."
                )
        set_asr_device(asr_device)

        # Initialize matching concurrency limiter from REAL ASR capacity, so the
        # dashboard's MATCHING count can't exceed what can actually be transcribing.
        _asr_runtime = resolve_asr_runtime(asr_device, config.max_concurrent_matches)
        self._matching.init_semaphore(_asr_runtime.workers)

        # Start timed staging cleanup if policy is "after_days"
        if config.staging_cleanup_policy == "after_days":
            self._timed_cleanup_task = asyncio.create_task(
                self._cleanup.run_timed_cleanup(config.staging_path, config.staging_cleanup_days)
            )

        # Start the stale-job watchdog
        if config.watchdog_enabled:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        logger.info(
            f"Job manager started (asr_device={_asr_runtime.device}, "
            f"asr_workers={_asr_runtime.workers}, cpu_threads={_asr_runtime.cpu_threads}, "
            f"requested={config.max_concurrent_matches})"
        )

    async def _cleanup_stale_jobs(self) -> None:
        """Mark stale jobs as FAILED on startup.

        ORGANIZING is intentionally excluded: a job interrupted mid-move has its
        files partly in the library and is recoverable. ``_recover_organizing_jobs``
        (run right after this) re-drives those jobs instead of failing them.
        """
        stale_states = [
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
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
                job.identity_prompt_json = None  # answer is moot on a terminal row
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

    async def _recover_organizing_jobs(self) -> None:
        """Resume jobs left mid-move in ORGANIZING after a restart.

        ``_cleanup_stale_jobs`` deliberately leaves ORGANIZING jobs alone so this
        can claim them. A TV job re-runs the idempotent ``finalize_disc_job`` (its
        organize loop skips titles that already COMPLETED and re-resolves source
        files), spawned as a background task so a slow NAS move doesn't block
        startup. Movie organizing has no standalone idempotent re-organize entry
        point, so a stranded movie is failed (the prior cleanup behavior).
        """
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(DiscJob.state == JobState.ORGANIZING)
            )
            # Capture (id, content_type) before the session closes.
            jobs = [(j.id, j.content_type) for j in result.scalars()]

        for job_id, content_type in jobs:
            if content_type == ContentType.TV:
                logger.info(
                    f"Recovering job {job_id} stranded in ORGANIZING: re-running finalization"
                )
                task = asyncio.create_task(
                    with_job_log_context(job_id, self._recover_organizing_tv(job_id))
                )
                task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
                self._active_jobs[job_id] = task
            else:
                logger.warning(
                    f"Recovering movie job {job_id} stranded in ORGANIZING: no idempotent "
                    "re-organize path; marking FAILED"
                )
                await self._fail_job(
                    job_id, "Server restarted while organizing; please re-run the job"
                )

    async def _recover_organizing_tv(self, job_id: int) -> None:
        """Re-drive an idempotent TV finalization; fail the job if it errors."""
        try:
            await self._finalization.finalize_disc_job(job_id)
        except Exception as e:
            logger.exception(f"Recovery of ORGANIZING job {job_id} failed: {e}")
            await self._fail_job(job_id, f"Organize recovery failed: {e}")

    async def stop(self) -> None:
        """Stop the job manager and clean up."""
        self._drive_monitor.stop()

        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                # Expected: we just cancelled the watchdog loop above.
                pass
            self._watchdog_task = None

        for job_id, task in self._active_jobs.items():
            task.cancel()
            logger.info(f"Cancelled job {job_id}")

        self._active_jobs.clear()

        # Stop any background transcript prewarming.
        self._prewarmer.cancel_all()

        # Drain any MakeMKV subprocesses so none survive shutdown as orphans.
        await self._extractor.shutdown()

        logger.info("Job manager stopped")

    # --- Drive/Staging Event Handlers ---

    async def _on_drive_event(
        self,
        drive_letter: str,
        event: str,
        volume_label: str,
    ) -> None:
        """Handle drive insertion/removal events from the Sentinel.

        Job-creation failures are allowed to propagate: the Sentinel's ``_notify``
        backstop logs them with a traceback, and the direct API caller
        (``/simulate/trigger-real-scan``) still surfaces a 500 rather than a bogus
        success. Swallowing here would hide both.
        """
        # drive_letter and volume_label can be caller/disc-controlled, so sanitize
        # them before logging to prevent CR/LF log forging (py/log-injection).
        safe_drive = sanitize_log_value(drive_letter)
        safe_label = sanitize_log_value(volume_label)
        logger.info(f"Drive event: {safe_drive} {event} (label: {safe_label})")

        if event == "inserted":
            # Create the job before broadcasting so clients see it on first fetch.
            await self._create_job_for_disc(drive_letter, volume_label)
            await event_broadcaster.broadcast_drive_inserted(drive_letter, volume_label)
        elif event == "removed":
            # A parked disc (inserted before setup completed) that gets ejected
            # is simply forgotten — and the dashboard banner cleared.
            if self._parked_discs.pop(drive_letter, None) is not None:
                await event_broadcaster.broadcast_parked_discs(self.parked_discs)
            # A cancellation failure must not suppress the removal broadcast —
            # otherwise clients are stuck believing the disc is still present.
            try:
                await self._cancel_jobs_for_drive(drive_letter)
            except Exception:
                logger.error(f"Failed to cancel jobs for {safe_drive}", exc_info=True)
            await event_broadcaster.broadcast_drive_removed(drive_letter, volume_label)

    # --- First-run setup gate (P12) ---

    @property
    def parked_discs(self) -> list[dict[str, str]]:
        """Discs detected while first-run setup was incomplete (pipeline parked).

        Serialized for the ``parked_discs`` WebSocket broadcast and the
        ``GET /api/parked-discs`` banner seed.
        """
        return [
            {"drive_id": drive, "volume_label": label}
            for drive, label in self._parked_discs.items()
        ]

    async def resume_parked_discs(self) -> None:
        """Start the pipeline for discs parked behind the setup gate.

        Called when the setup wizard completes (``PUT /api/config`` with
        ``setup_complete=true``) so a disc already in the drive starts
        processing without an eject/reinsert. No-op when nothing is parked —
        which is every settings save from an already-configured install.

        Calls ``_create_job_for_disc`` directly rather than replaying through
        ``_on_drive_event``: clients already received the ``drive_event`` when
        the disc was first inserted (and parked), so re-broadcasting it would
        be a duplicate. The new job announces itself via the IDENTIFYING
        ``job_update`` that identification fires immediately.
        """
        if not self._parked_discs:
            return
        parked = dict(self._parked_discs)
        self._parked_discs.clear()
        await event_broadcaster.broadcast_parked_discs(self.parked_discs)
        for drive_letter, volume_label in parked.items():
            try:
                await self._create_job_for_disc(drive_letter, volume_label)
            except Exception:
                # Parity with the sentinel's _notify backstop: log loudly, keep
                # resuming the remaining drives.
                logger.error(
                    f"Failed to resume parked disc in {sanitize_log_value(drive_letter)}",
                    exc_info=True,
                )

    # --- Job Creation ---

    async def _compute_disc_hash(self, drive_letter: str) -> str | None:
        """Best-effort per-disc ContentHash at insert time.

        Retries briefly to cover a disc that is mounted but not yet fully
        readable; real-disc testing showed the hash is ready ~instantly after
        mount, so the retry is rarely exercised. Runs off-thread so the disc
        I/O never blocks the event loop.
        """
        for attempt in range(_DISC_HASH_RETRY_ATTEMPTS):
            content_hash = await asyncio.to_thread(compute_content_hash, drive_letter)
            if content_hash:
                return content_hash
            if attempt < _DISC_HASH_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_DISC_HASH_RETRY_DELAY)
        return None

    @staticmethod
    def _same_disc(job: DiscJob, volume_label: str, new_hash: str | None) -> bool:
        """True if `job` is the same physical disc as the one just inserted.

        Prefers the per-disc ContentHash (a different hash means a different
        disc). Falls back to volume-label equality when either fingerprint is
        absent — conservative: a same-labelled disc with no readable hash is
        treated as the same disc, so we never spawn a duplicate job.
        """
        if new_hash and job.content_hash:
            return job.content_hash == new_hash
        return job.volume_label == volume_label

    async def _create_job_for_disc(self, drive_letter: str, volume_label: str) -> None:
        """Create a new job when a disc is inserted."""
        from app.services.config_service import get_config as get_db_config

        db_config = await get_db_config()

        # First-run setup gate (P12): until the wizard is completed, never start
        # the identify/rip pipeline — staging/library paths, the MakeMKV license,
        # and the TMDB token are all unconfirmed defaults. Park the disc instead;
        # completing setup replays the insert via resume_parked_discs(), so no
        # eject/reinsert is needed. Placed before the drive lock and the disc
        # fingerprint probe so an unconfigured install does zero extra disc I/O.
        # Cost: configured installs now pay one config read per disc insert —
        # negligible at physical-disc frequency (the body needed the config for
        # the staging path anyway; it's fetched once here and reused below).
        if not db_config.setup_complete:
            self._parked_discs[drive_letter] = volume_label
            logger.info(
                f"Setup not complete; parking disc in {sanitize_log_value(drive_letter)} "
                f"(label: {sanitize_log_value(volume_label)}) instead of starting the pipeline"
            )
            await event_broadcaster.broadcast_parked_discs(self.parked_discs)
            return

        if drive_letter not in self._drive_locks:
            self._drive_locks[drive_letter] = asyncio.Lock()

        async with self._drive_locks[drive_letter]:
            # Fingerprint the inserted disc so dedup can tell two same-labelled
            # discs apart (e.g. season Disc 1 vs Disc 2 both 'BREAKINGBADS2').
            # The probe runs off-thread; its brief retry wait (~1s worst case for
            # a cold disc) happens under the per-drive lock, but the only thing
            # that contends that lock is the insert sentinel — and the sentinel is
            # what triggered this call, so nothing else is waiting on it here.
            new_hash = await self._compute_disc_hash(drive_letter)
            # Feature C: a reinsert of the SAME disc (hash match) with re-rippable
            # titles re-rips just those titles instead of spawning a new job.
            rerip = await self._find_rerip_job(new_hash)
            if rerip is not None:
                rerip_job_id, rerip_title_ids = rerip
                logger.info(
                    f"Disc reinserted (hash match) for job {rerip_job_id}; re-ripping "
                    f"{len(rerip_title_ids)} failed title(s) instead of creating a new job."
                )
                task = asyncio.create_task(
                    with_job_log_context(
                        rerip_job_id, self.rerip_titles(rerip_job_id, rerip_title_ids)
                    )
                )
                task.add_done_callback(lambda t, jid=rerip_job_id: self._on_task_done(t, jid))
                self._active_jobs[rerip_job_id] = task
                return
            async with async_session() as session:
                # Disc-required jobs (the disc is physically in the drive) always
                # block a new job. Ripping EJECTS the disc + calls notify_ejected()
                # *before* the RIPPING->MATCHING transition, so MATCHING/ORGANIZING
                # jobs no longer hold the drive — a genuinely new disc must be allowed
                # through. We still block a MATCHING/ORGANIZING job whose volume_label
                # matches the inserted disc: that's the SAME disc lingering after a
                # reported-but-incomplete eject, and a new job for it would duplicate
                # the in-flight one. (COMPLETED/FAILED/REVIEW_NEEDED are absent here,
                # so they stay non-blocking, as before.)
                disc_required_states = (
                    JobState.IDLE,
                    JobState.IDENTIFYING,
                    JobState.RIPPING,
                )
                post_eject_states = (JobState.MATCHING, JobState.ORGANIZING)

                result = await session.execute(
                    select(DiscJob).where(
                        DiscJob.drive_id == drive_letter,
                        DiscJob.state.in_(disc_required_states + post_eject_states),
                    )
                )
                # .all() (not scalar_one_or_none): an old MATCHING job and a new
                # RIPPING job can now legitimately coexist on one drive.
                active_jobs = result.scalars().all()

                blocking_job = next(
                    (
                        j
                        for j in active_jobs
                        if j.state in disc_required_states
                        or (
                            j.state in post_eject_states
                            and self._same_disc(j, volume_label, new_hash)
                        )
                    ),
                    None,
                )
                if blocking_job is not None:
                    logger.info(
                        f"Job {blocking_job.id} already occupies drive "
                        f"{sanitize_log_value(drive_letter)} (state={blocking_job.state.value}); "
                        f"skipping new job for label={sanitize_log_value(volume_label)}"
                    )
                    return

                last_created = self._last_job_created_at.get(drive_letter, 0)
                if time.monotonic() - last_created < 15:
                    logger.info(
                        f"Skipping job creation for {drive_letter}: "
                        f"cooldown ({time.monotonic() - last_created:.0f}s since last)"
                    )
                    return

                staging_dir = (
                    Path(db_config.staging_path).expanduser()
                    / f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )

                job = DiscJob(
                    drive_id=drive_letter,
                    volume_label=volume_label,
                    staging_path=str(staging_dir),
                    state=JobState.IDENTIFYING,
                    content_hash=new_hash,
                )

                session.add(job)
                await session.commit()
                await session.refresh(job)

                logger.info(f"Created job {job.id} for disc in {drive_letter}")

                task = asyncio.create_task(
                    with_job_log_context(job.id, self._identification.identify_disc(job.id))
                )
                task.add_done_callback(lambda t, jid=job.id: self._on_task_done(t, jid))
                self._active_jobs[job.id] = task
                # Stamp the cooldown only after the task is scheduled, so a failure
                # to spawn it doesn't silently block retries for the next 15s.
                self._last_job_created_at[drive_letter] = time.monotonic()

    async def _find_rerip_job(self, new_hash: str | None) -> tuple[int, list[int]] | None:
        """Find a REVIEW_NEEDED job for this disc with auto-re-rippable titles.

        Returns ``(job_id, [title_id])`` when the inserted disc's ContentHash
        matches a settled job holding rip-failed titles still eligible for an
        automatic re-rip; ``None`` for a different/unfingerprintable disc, a job
        still actively matching, or no eligible titles (Feature C).
        """
        if not new_hash:
            return None
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(
                    DiscJob.content_hash == new_hash,
                    DiscJob.state == JobState.REVIEW_NEEDED,
                )
            )
            jobs = sorted(result.scalars().all(), key=lambda j: j.id, reverse=True)
            for job in jobs:
                titles_res = await session.execute(
                    select(DiscTitle).where(
                        DiscTitle.job_id == job.id,
                        DiscTitle.state == TitleState.REVIEW,
                    )
                )
                eligible = [t.id for t in titles_res.scalars().all() if _is_auto_rerippable(t)]
                if eligible:
                    return job.id, eligible
        return None

    async def create_job_from_staging(
        self,
        staging_path: str,
        volume_label: str = "",
        content_type: str = "unknown",
        detected_title: str | None = None,
        detected_season: int | None = None,
        destination_mode: str = "library",
        drive_id: str = "staging",
        import_manifest: dict | None = None,
    ) -> int:
        """Create a job from pre-ripped MKV files in a staging directory."""
        from sqlmodel import select as sa_select

        staging_dir = Path(staging_path)

        if not volume_label:
            volume_label = staging_dir.name.upper().replace(" ", "_")

        # Hold a per-path lock across the dedup check and insert so two concurrent
        # callers (e.g. two API calls) for the same path can't both pass the guard
        # and insert duplicate jobs, mirroring _drive_locks on the disc path.
        # Widened slightly by this dedup change: retries of a path with only FAILED
        # rows now reach the check->insert that the guard used to short.
        if str(staging_dir) not in self._staging_locks:
            self._staging_locks[str(staging_dir)] = asyncio.Lock()

        async with self._staging_locks[str(staging_dir)]:
            async with async_session() as session:
                # Guard: don't create a duplicate job for the same staging directory.
                # A staging path can be re-submitted (re-import, or after a server
                # restart), so a *terminal-failed* prior job (cancelled, or auto-failed
                # by restart recovery) must NOT permanently wedge re-import; only an
                # active or review-pending job should dedup. Use .first() because a
                # path may now accumulate multiple FAILED rows across retries.
                existing = await session.execute(
                    sa_select(DiscJob).where(
                        DiscJob.staging_path == str(staging_dir),
                        DiscJob.state != JobState.FAILED,
                    )
                )
                if existing.scalars().first() is not None:
                    safe_path = str(staging_dir).replace("\n", "").replace("\r", "")
                    logger.info("Job already exists for staging path %s, skipping", safe_path)
                    return -1

                job = DiscJob(
                    drive_id=drive_id,
                    volume_label=volume_label,
                    staging_path=str(staging_dir),
                    state=JobState.IDENTIFYING,
                    destination_mode=destination_mode,
                )

                if content_type in ("tv", "movie"):
                    job.content_type = ContentType(content_type)
                if detected_title:
                    job.detected_title = detected_title
                if detected_season is not None:
                    job.detected_season = detected_season
                if import_manifest is not None:
                    job.import_manifest_json = json.dumps(import_manifest)

                session.add(job)
                await session.commit()
                await session.refresh(job)

                job_id = job.id
                safe_path = str(staging_path).replace("\n", "").replace("\r", "")
                safe_label = str(volume_label).replace("\n", "").replace("\r", "")
                # destination_mode is user-supplied via POST /api/import/start; strip
                # CR/LF so it can't forge log lines (matches safe_path/safe_label).
                safe_dest = str(destination_mode).replace("\n", "").replace("\r", "")
                logger.info(
                    "Created %s job %s from %s (label: %s, destination: %s)",
                    "import" if drive_id == "import" else "staging",
                    job_id,
                    safe_path,
                    safe_label,
                    safe_dest,
                )

        await event_broadcaster.broadcast_drive_inserted(drive_id, volume_label)

        task = asyncio.create_task(
            with_job_log_context(job_id, self._identification.identify_from_staging(job_id))
        )
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
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
        """Set a user-provided name for an unlabeled disc and resume the pipeline.

        The coordinator decides the ``resume_action`` (see
        ``IdentificationCoordinator.set_name_and_resume``); this wrapper spawns
        a rip task ONLY for ``"start_rip"`` — a mid-rip or post-rip answer must
        never start a second rip (the double-rip hazard, walk-away B5).
        """
        # The real pipeline owns the ASR from here; it reuses whatever the
        # prewarmer already cached. cancel_for_job prevents future chunks;
        # the in-flight thread still finishes its current chunk (~60 s).
        self._prewarmer.cancel_for_job(job_id)
        result = await self._identification.set_name_and_resume(
            job_id, name, content_type_str, season
        )
        await self._apply_identity_resume_action(job_id, result["resume_action"])

    async def re_identify_job(
        self,
        job_id: int,
        title: str,
        content_type_str: str,
        season: int | None = None,
        tmdb_id: int | None = None,
    ) -> None:
        """Re-identify a job with user-corrected metadata.

        Same resume contract as :meth:`set_name_and_resume`: the coordinator
        returns a ``resume_action`` and only ``"start_rip"`` spawns a rip task.
        """
        # A real match (or re-rip) starts now — stop background prewarming.
        # cancel_for_job prevents future chunks; the in-flight thread still
        # finishes its current chunk (~60 s).
        self._prewarmer.cancel_for_job(job_id)
        result = await self._identification.re_identify(
            job_id, title, content_type_str, season, tmdb_id
        )
        await self._apply_identity_resume_action(job_id, result["resume_action"])

    async def _apply_identity_resume_action(self, job_id: int, action: ResumeAction) -> None:
        """Run the JobManager side of an identity answer (walk-away B5).

        ``"start_rip"``/``"rerun_matching"``/``"resolve_movie"`` spawn a
        background task registered in ``_active_jobs``. ``"dispatch_matches"``
        and ``"release_movie_titles"`` act inline on already-running work — a
        mid-rip answer leaves the live rip task as the registered owner, so no
        task is spawned (spawning ``_run_ripping`` here is the double-rip
        hazard the coordinator contract exists to prevent).
        """
        if action == "dispatch_matches":
            dispatched = await self.dispatch_pending_matches(job_id)
            if dispatched == 0:
                # Post-rip resume (REVIEW_NEEDED → MATCHING) with nothing left
                # to dispatch (e.g. every title already terminal/REVIEW): run
                # the completion check so the job doesn't strand in MATCHING.
                # Mid-rip (still RIPPING) zero-dispatch is normal — titles are
                # dispatched as they rip now that the prompt is cleared.
                async with async_session() as session:
                    job = await session.get(DiscJob, job_id)
                    if job and job.state == JobState.MATCHING:
                        await self._finalization.check_job_completion(session, job_id)
            return
        if action == "release_movie_titles":
            await self._release_parked_movie_titles(job_id)
            return

        coro_factory = {
            "start_rip": self._run_ripping,
            "rerun_matching": self._rerun_matching,
            "resolve_movie": self._resume_movie_post_rip,
        }.get(action)
        if coro_factory is None:
            raise ValueError(f"Unknown identity resume action: {action!r}")
        task = asyncio.create_task(with_job_log_context(job_id, coro_factory(job_id)))
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def _release_parked_movie_titles(self, job_id: int) -> None:
        """Flip identity-parked QUEUED titles to MATCHED after a mid-rip non-TV answer.

        Mirrors the non-TV branch of ``_on_title_ripped``: movies skip episode
        matching, so titles the identity gate parked in QUEUED become MATCHED
        the moment the answer resolves the job to a movie. The still-running
        rip's movie tail (``_finalize_ripped_movie``) completes them at rip end.
        """
        released: list[int] = []
        async with async_session() as session:
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            for t in result.scalars().all():
                if t.state == TitleState.QUEUED:
                    t.state = TitleState.MATCHED
                    session.add(t)
                    released.append(t.id)
            await session.commit()
        for title_id in released:
            await ws_manager.broadcast_title_update(job_id, title_id, TitleState.MATCHED.value)
        if released:
            logger.info(
                f"Job {sanitize_log_value(job_id)}: released {len(released)} "
                f"identity-parked title(s) to MATCHED (movie answer)"
            )

    async def _resume_movie_post_rip(self, job_id: int) -> None:
        """Finish a ripped job that an identity answer just resolved to a movie.

        Walk-away B5: reuses the rip-end movie tail (TheDiscDB MainMovie /
        feature-vs-extras resolution, then organize + complete) instead of the
        TV ``_rerun_matching`` path, which would episode-match a movie.
        """
        try:
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job or not job.staging_path:
                    return
                output_dir = Path(job.staging_path)
                volume_label = job.volume_label
                detected_title = job.detected_title
            # Recover strays exactly like the rip-end flow; with the prompt
            # cleared and a non-TV content type, recovered titles go MATCHED.
            await self.reconcile_stuck_titles(job_id)
            await self._finalize_ripped_movie(job_id, output_dir, volume_label, detected_title)
        except Exception as e:
            logger.exception(f"Error resuming movie job {sanitize_log_value(job_id)} post-rip")
            await self._fail_job(job_id, str(e))

    async def _rerun_matching(self, job_id: int, source_preference: str | None = None) -> None:
        """Re-run episode matching for already-ripped titles."""
        # The real match owns the GPU now; it reuses what the prewarmer cached.
        # cancel_for_job prevents future chunks; the in-flight thread still
        # finishes its current chunk (~60 s).
        self._prewarmer.cancel_for_job(job_id)
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
                        details = json.loads(title.discdb_match_details)
                        title.match_details = title.discdb_match_details
                        title.match_source = "discdb"
                        title.match_confidence = 0.99
                        # Restore episode code from stored details
                        if "matched_episode" in details:
                            title.matched_episode = details["matched_episode"]
                        title.state = TitleState.MATCHED
                        session.add(title)
                await session.commit()
            else:
                for title in disc_titles:
                    # Reset title state for re-matching → QUEUED (enqueued; the
                    # QUEUED→MATCHING flip happens once a match slot is acquired).
                    title.state = TitleState.QUEUED
                    title.matched_episode = None
                    title.match_confidence = 0.0
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
                            # match_single_file self-tags the job log context.
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

            task = asyncio.create_task(with_job_log_context(job_id, self._run_ripping(job_id)))
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
            if job and job.state not in TERMINAL_JOB_STATES:
                await state_machine.transition_to_failed(
                    job, session, error_message="Cancelled by user"
                )

    # --- Stale-job watchdog + force-progress engine ---

    def _note_activity(self, job_id: int) -> None:
        """Record that a job made progress just now (watchdog activity clock)."""
        self._last_activity[job_id] = time.monotonic()

    def _note_activity_on_transition(self, job_id: int, state: JobState) -> None:
        """on_transition observer: reset the clock on phase change, drop it on terminal."""
        if state in (JobState.COMPLETED, JobState.FAILED):
            self._last_activity.pop(job_id, None)
        else:
            self._last_activity[job_id] = time.monotonic()

    def _start_prewarm_on_review(self, job_id: int, state: JobState) -> None:
        """on_transition observer: prewarm transcripts when a job parks in review."""
        if state == JobState.REVIEW_NEEDED:
            self._prewarmer.kickoff(job_id)

    async def _enqueue_disc_contribution_on_terminal(self, job_id: int, state: JobState) -> None:
        """on_terminal_state hook: enqueue a whole-disc contribution on COMPLETED.

        FAILED jobs do not contribute. Loads the job + its titles in a fresh
        session and hands them to the disc-contribution enqueue. Best-effort: any
        failure is logged and swallowed (the enqueue itself is also defensive), so
        a contribution can never break the terminal-state dispatch or completion.
        """
        if state != JobState.COMPLETED:
            return
        try:
            from app.services.config_service import get_config
            from app.services.disc_contribution_queue import enqueue_disc_contribution

            config = await get_config()
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if job is None:
                    return
                titles = (
                    (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                    .scalars()
                    .all()
                )
                await enqueue_disc_contribution(
                    session,
                    job,
                    list(titles),
                    contributions_enabled=config.enable_fingerprint_contributions,
                    pseudonym=config.contribution_pseudonym,
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"Job {job_id}: disc contribution enqueue failed: {e}", exc_info=True)

    async def _notify_discord_on_terminal(self, job_id: int, state: JobState) -> None:
        """on_terminal_state hook: schedule a Discord notification and return immediately.

        Fire-and-forget via create_task so a slow or unreachable webhook never
        delays job finalization — the 10s httpx timeout stays off the critical path.
        """
        asyncio.create_task(self._send_discord_notification(job_id, state))

    async def _send_discord_notification(self, job_id: int, state: JobState) -> None:
        """Send the Discord embed. Runs as a background task; all errors are swallowed."""
        try:
            from app.core.discord_notifier import (
                DEFAULT_TEMPLATE_COMPLETED,
                DEFAULT_TEMPLATE_FAILED,
                build_template_context,
                notify_discord,
                render_discord_template,
            )
            from app.services.config_service import get_config

            config = await get_config()
            if not config.discord_webhook_url:
                return

            if state not in TERMINAL_JOB_STATES:
                logger.warning(
                    f"Job {job_id}: Discord notification requested for non-terminal state {state}"
                )
                return

            async with async_session() as session:
                job = await session.get(DiscJob, job_id)

            if state == JobState.COMPLETED:
                template = config.discord_template_completed or DEFAULT_TEMPLATE_COMPLETED
            else:
                template = config.discord_template_failed or DEFAULT_TEMPLATE_FAILED

            context = build_template_context(job, job_id)
            description = render_discord_template(template, context)
            await notify_discord(config.discord_webhook_url, job_id, description, state.value)
        except Exception as e:
            logger.warning(f"Job {job_id}: Discord notification failed: {e}", exc_info=True)

    @staticmethod
    def _has_complete_output(output_dir: Path, title: DiscTitle) -> bool:
        """Whether a non-empty ``*_tNN.mkv`` for this title exists in staging.

        Used by the one-pass rip fallback to tell which selected titles a single
        'all' invocation actually produced (vs. ones it never reached after a
        stall), so only the truly-missing titles are re-ripped individually.
        """
        for mkv in output_dir.glob(f"*_t{expected_native_index(title):02d}.mkv"):
            try:
                if mkv.stat().st_size > 0:
                    return True
            except OSError:
                # File vanished or is unreadable between glob and stat (e.g.
                # MakeMKV still finalizing). Treat as "not yet complete" and
                # keep scanning the remaining matches.
                continue
        return False

    @staticmethod
    def _find_title_file(title: DiscTitle, staging: Path | None) -> Path | None:
        """Locate a title's ripped .mkv: its recorded output, else a staging glob."""
        if title.output_filename:
            p = Path(title.output_filename)
            if p.exists():
                return p
        if staging:
            matches = list(staging.glob(f"*_t{expected_native_index(title):02d}.mkv"))
            if matches:
                return matches[0]
        return None

    async def reconcile_stuck_titles(self, job_id: int) -> None:
        """Resolve selected titles orphaned in PENDING/RIPPING after a rip finishes.

        A title whose staging file exists is routed into the normal completion path
        (TV → matching; movie → MATCHED); one with no file is
        marked FAILED. Guarantees no selected title is stranded in RIPPING once the
        MakeMKV subprocess has exited (the orphaned-last-title bug). Recovers work
        rather than discarding it.

        Identity gate (walk-away Phase B): with an unanswered identity prompt,
        recovered titles park in QUEUED with no dispatch — same gate as
        ``_on_title_ripped`` — so an orphaned title can't slip into matching
        (or MATCHED, for non-TV) without a confirmed identity.
        """
        recovered: list[tuple[int, Path]] = []
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            stuck = [
                t
                for t in result.scalars().all()
                if t.is_selected and t.state in (TitleState.PENDING, TitleState.RIPPING)
            ]
            if not stuck:
                return

            staging = Path(job.staging_path) if job.staging_path else None
            is_tv = job.content_type == ContentType.TV
            identity_pending = self._identity_pending(job)

            for title in stuck:
                file_path = self._find_title_file(title, staging)
                if file_path is None:
                    title.state = TitleState.FAILED
                    if not title.match_details:
                        title.match_details = json.dumps(
                            {"reason": "Ripping ended with no output file"}
                        )
                    session.add(title)
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: title "
                        f"{sanitize_log_value(title.title_index)} stuck with no file → FAILED"
                    )
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title.id,
                        TitleState.FAILED.value,
                        error="Ripping ended with no output file",
                    )
                    continue

                title.output_filename = str(file_path)
                # Enqueued for matching → QUEUED (the post-semaphore flip to MATCHING
                # happens in match_single_file once a slot is acquired). Movies skip
                # matching entirely → MATCHED — unless the identity prompt is
                # pending, in which case every type parks in QUEUED.
                title.state = (
                    TitleState.QUEUED if (is_tv or identity_pending) else TitleState.MATCHED
                )
                session.add(title)
                logger.info(
                    f"Job {sanitize_log_value(job_id)}: recovered orphaned title "
                    f"{sanitize_log_value(title.title_index)} "
                    f"({sanitize_log_value(file_path.name)}) → {title.state.value}"
                )
                await ws_manager.broadcast_title_update(
                    job_id,
                    title.id,
                    title.state.value,
                    output_filename=str(file_path),
                )
                if is_tv and not identity_pending:
                    recovered.append((title.id, file_path))
            await session.commit()

        # Queue matching for recovered TV titles (same dispatch as
        # _on_title_ripped), each outside the session above so match tasks own
        # their own sessions.
        for title_id, file_path in recovered:
            await self._dispatch_title_match(job_id, title_id, file_path)

    async def reconcile_and_advance(self, job_id: int, *, reason: str = "forced") -> bool:
        """Force a stuck job to its next resting state (watchdog + manual advance).

        Cancels any in-flight rip, resolves every still-active title (PENDING/RIPPING/
        MATCHING) — to REVIEW if its ripped file exists (so the user can assign it),
        else FAILED — then runs the normal completion check, which organizes whatever
        matched and lands the job in COMPLETED or REVIEW_NEEDED. Returns True if the
        job was non-terminal and processed.

        QUEUED is deliberately NOT in the active set below: a queued track is draining
        the global match semaphore, not stuck, so the watchdog must never force it to
        review (a genuinely hung *active* match is caught by the per-track timeout).
        """
        # Stop any in-flight rip/processing task so it can't race the reconcile.
        if job_id in self._active_jobs:
            self._active_jobs[job_id].cancel()
            del self._active_jobs[job_id]
        self._extractor.cancel(job_id)

        # NOTE: QUEUED excluded on purpose — see docstring (queued ≠ stuck).
        active = (TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING)
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job or job.state in (JobState.COMPLETED, JobState.FAILED):
                return False

            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            staging = Path(job.staging_path) if job.staging_path else None

            for title in result.scalars().all():
                if not title.is_selected or title.state not in active:
                    continue
                file_path = self._find_title_file(title, staging)
                if file_path is not None:
                    title.output_filename = str(file_path)
                    title.state = TitleState.REVIEW
                    title.match_details = self._forced_review_details(title.match_details, reason)
                    err = None
                else:
                    title.state = TitleState.FAILED
                    err = "Force-advanced with no output file"
                    if not title.match_details:
                        title.match_details = json.dumps({"reason": err})
                session.add(title)
                logger.info(
                    f"Job {sanitize_log_value(job_id)}: force-advance ({reason}) — title "
                    f"{sanitize_log_value(title.title_index)} → {title.state.value}"
                )
                await ws_manager.broadcast_title_update(
                    job_id,
                    title.id,
                    title.state.value,
                    error=err,
                    output_filename=title.output_filename,
                )
            await session.commit()

            await self._finalization.check_job_completion(session, job_id)
        return True

    async def skip_title(
        self, job_id: int, title_id: int, *, target: TitleState = TitleState.REVIEW
    ) -> bool:
        """Skip one stuck title: mark it REVIEW (default) or FAILED, then re-check completion.

        Only acts on titles still in an active state (PENDING/RIPPING/MATCHING). Lets the
        user unblock a single track without forcing the whole job forward.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                return False
            if title.state not in (
                TitleState.PENDING,
                TitleState.RIPPING,
                TitleState.QUEUED,
                TitleState.MATCHING,
            ):
                return False

            title.state = target
            err = "Skipped by user" if target == TitleState.FAILED else None
            if target == TitleState.FAILED and not title.match_details:
                title.match_details = json.dumps({"reason": "Skipped by user"})
            elif target == TitleState.REVIEW:
                title.match_details = self._forced_review_details(
                    title.match_details, "Skipped by user"
                )
            session.add(title)
            await session.commit()
            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title.title_index)} skipped → {target.value}"
            )
            await ws_manager.broadcast_title_update(job_id, title.id, target.value, error=err)

            await self._finalization.check_job_completion(session, job_id)
        return True

    async def skip_rip_title(self, job_id: int, title_id: int) -> bool:
        """Skip a queued/not-yet-ripped title so MakeMKV does not rip it.

        Acts only on PENDING or QUEUED titles (never one actively RIPPING or
        already terminal). Marks the title SKIPPED + deselected, registers its
        index in the extractor's live skip-set (honored by the per-title rip
        loop), then re-checks job completion. Returns False if the title is not
        in a skippable state.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                return False
            if title.state not in (TitleState.PENDING, TitleState.QUEUED):
                return False

            title.state = TitleState.SKIPPED
            title.is_selected = False
            title.match_details = json.dumps({"reason": "Skipped by user", "skipped": True})
            session.add(title)
            await session.commit()
            title_index = title.title_index

            # Live skip for an in-progress per-title rip loop.
            self._extractor.skip_title_index(job_id, title_index)

            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title_index)} skipped by user -> SKIPPED"
            )
            await ws_manager.broadcast_title_update(job_id, title_id, TitleState.SKIPPED.value)
            await self._finalization.check_job_completion(session, job_id)
        return True

    async def unskip_rip_title(self, job_id: int, title_id: int) -> bool:
        """Reverse a skip while the title's file has not been written yet.

        Allowed only while the title is still SKIPPED and no output file exists.
        Restores PENDING + selected and clears the extractor skip-set entry.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                return False
            if title.state != TitleState.SKIPPED:
                return False
            if title.output_filename:
                return False

            title.state = TitleState.PENDING
            title.is_selected = True
            title.match_details = None
            session.add(title)
            await session.commit()
            title_index = title.title_index

            self._extractor.unskip_title_index(job_id, title_index)

            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title_index)} un-skipped -> PENDING"
            )
            await ws_manager.broadcast_title_update(job_id, title_id, TitleState.PENDING.value)
        return True

    @staticmethod
    def _forced_review_details(existing: str | None, reason: str) -> str:
        """Tag a title's match_details as force-advanced/skipped to REVIEW.

        The ``forced_review`` flag tells the auto review-escalation to leave this
        title alone — it was deliberately handed to a human, not flagged by the
        matcher for a deeper retry.
        """
        data: dict = {}
        if existing:
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, dict):
                    data = parsed
            except (json.JSONDecodeError, TypeError):
                data = {}
        data["forced_review"] = True
        data.setdefault("reason", reason)
        return json.dumps(data)

    @staticmethod
    def _phase_timeout(config, state: JobState) -> int | None:
        """Per-phase no-activity ceiling (seconds), or None for resting/untimed states."""
        return {
            JobState.IDENTIFYING: config.timeout_identifying_seconds,
            JobState.RIPPING: config.timeout_ripping_seconds,
            JobState.MATCHING: config.timeout_matching_seconds,
            JobState.ORGANIZING: config.timeout_organizing_seconds,
        }.get(state)

    def _rip_task_alive(self, job_id: int) -> bool:
        """Whether the job's registered background task is still running.

        ``_active_jobs`` keeps finished tasks around (nothing prunes on
        success), so membership alone doesn't mean "live" — check done().
        """
        task = self._active_jobs.get(job_id)
        return task is not None and not task.done()

    async def _has_pending_match_work(self, job_id: int) -> bool:
        """True if the job still has tracks QUEUED for or actively MATCHING.

        Such a job is draining the global match queue (``max_concurrent_matches``),
        not stuck — the watchdog must not force its waiting tracks to review. A
        genuinely hung *active* match is caught by the per-track timeout in the
        matcher, which frees the slot so the queue keeps draining.
        """
        async with async_session() as session:
            result = await session.execute(
                select(DiscTitle.id)
                .where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.state.in_((TitleState.QUEUED, TitleState.MATCHING)),
                )
                .limit(1)
            )
            return result.first() is not None

    async def _watchdog_check_job(self, job: DiscJob, config, now: float) -> None:
        """Apply the stale-job timeout to one job (extracted from the loop for testing)."""
        timeout = self._phase_timeout(config, job.state)
        if not timeout or timeout <= 0:
            return
        # Queue-aware: a MATCHING job that still has tracks QUEUED or actively
        # MATCHING is healthy — it's draining the global match queue at
        # max_concurrent_matches, not stuck. Refresh its clock and leave its
        # waiting tracks alone (the original import-storm bug force-advanced them
        # all to REVIEW here). A genuinely hung *active* match is recovered by the
        # per-track timeout in the matcher, which frees the slot to drain the queue.
        #
        # Walk-away B5: the same applies to a RIPPING job whose rip task is gone
        # (e.g. a stale-rip reconcile cancelled it) once its identity question is
        # answered — a mid-rip answer dispatches matching with NO state change, so
        # the drain runs while the job is still RIPPING; re-firing reconcile would
        # dump those in-flight matches to review. Both extra conditions matter:
        # a LIVE rip task keeps the fs-monitor heartbeat as the sole authority (a
        # stalled rip must still trip the timeout even if matches progress), and
        # an UNANSWERED identity prompt means the QUEUED titles are parked, not
        # progressing — the clock stays stale so reconcile keeps re-firing (the
        # accepted B4 residual) until the user answers.
        queue_draining = job.state == JobState.MATCHING or (
            job.state == JobState.RIPPING
            and not self._rip_task_alive(job.id)
            and not self._identity_pending(job)
        )
        if queue_draining and await self._has_pending_match_work(job.id):
            self._last_activity[job.id] = now
            return
        last = self._last_activity.get(job.id)
        if last is None:
            # First sighting — seed the clock so we time from now, not from an
            # unknown past (avoids an instant false trip).
            self._last_activity[job.id] = now
            return
        idle = now - last
        if idle >= timeout:
            logger.warning(
                f"Watchdog: job {job.id} idle {idle:.0f}s in "
                f"{job.state.value} (timeout {timeout}s) → auto-advancing"
            )
            await self.reconcile_and_advance(job.id, reason=f"stale timeout in {job.state.value}")

    async def _watchdog_loop(self) -> None:
        """Periodically auto-advance jobs that have stopped making progress."""
        from app.services.config_service import get_config

        watched = (
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        )
        try:
            while True:
                config = None
                try:
                    config = await get_config()
                except Exception as e:
                    logger.warning(f"Watchdog: could not load config: {e}", exc_info=True)
                poll = (config.watchdog_poll_seconds if config else 60) or 60
                await asyncio.sleep(poll)

                if config is None or not config.watchdog_enabled:
                    continue

                try:
                    async with async_session() as session:
                        result = await session.execute(
                            select(DiscJob).where(DiscJob.state.in_(watched))
                        )
                        jobs = result.scalars().all()

                    now = time.monotonic()
                    for job in jobs:
                        try:
                            await self._watchdog_check_job(job, config, now)
                        except Exception as e:
                            logger.error(
                                f"Watchdog: auto-advance of job {job.id} failed: {e}",
                                exc_info=True,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Watchdog loop iteration failed: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Watchdog loop cancelled")
            raise

    async def apply_review(
        self,
        job_id: int,
        title_id: int,
        episode_code: str | None = None,
        edition: str | None = None,
    ) -> None:
        """Apply a user's review decision for a title."""
        # Organization (shutil.move) starts now — stop background prewarming so
        # ffmpeg/ffprobe don't hold the file open when the rename runs. The
        # in-flight thread finishes its current chunk (~60s) before yielding.
        self._prewarmer.cancel_for_job(job_id)
        await self._finalization.apply_review(job_id, title_id, episode_code, edition)

    async def apply_review_batch(self, job_id: int, decisions: list[dict]) -> None:
        """Apply several review decisions for a job in one atomic pass."""
        # Organization (shutil.move) starts now — stop background prewarming so
        # ffmpeg/ffprobe don't hold the file open when the rename runs. The
        # in-flight thread finishes its current chunk (~60s) before yielding.
        self._prewarmer.cancel_for_job(job_id)
        await self._finalization.apply_review_batch(job_id, decisions)

    async def reassign_episode(
        self,
        job_id: int,
        title_id: int,
        episode_code: str,
        edition: str | None = None,
        source: str = "user",
    ) -> None:
        """Manually reassign an episode for a title.

        ``source`` defaults to "user" (manual reassignment). When the user
        accepts an LLM suggestion via the review UI, pass source="ai_llm" so
        downstream consumers can distinguish that path.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError(f"Title {title_id} not found for job {job_id}")

            title.matched_episode = episode_code
            title.match_confidence = 1.0
            title.match_source = source
            if edition is not None:
                title.edition = edition
            if title.state != TitleState.MATCHED:
                title.state = TitleState.MATCHED
            # Clear stale review-reason flags describing the PRIOR match attempt:
            # a fresh user assignment supersedes them. Leaving error/message would
            # keep the Inspector's "File exists" badge lit on the new episode, and
            # leaving forced_review would wrongly mark this user pick non-rematchable.
            title.match_details = _strip_review_flags(title.match_details)
            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                TitleState.MATCHED.value,
                matched_episode=episode_code,
                match_confidence=1.0,
                match_source=source,
            )

        safe_job = sanitize_log_value(job_id)
        safe_title = sanitize_log_value(title_id)
        safe_episode = sanitize_log_value(episode_code)
        safe_source = sanitize_log_value(source)
        logger.info(
            f"Job {safe_job}: title {safe_title} reassigned to {safe_episode} (source={safe_source})"
        )

    async def amend_title_assignment(self, job_id: int, title_id: int, target: "NewTarget") -> None:
        """Correct a track on a COMPLETED job in place (reassign / extra / discard).

        Moves the organized library file to its new home, updates the DiscTitle, and
        reconciles the fingerprint network (retract old, re-contribute new). The job
        stays COMPLETED — we never re-enter the state machine.

        ``target`` is a contribution_correction.NewTarget.
        """
        import re as _re

        from app.core.organizer import organize_tv_episode, organize_tv_extras
        from app.services.config_service import get_config
        from app.services.contribution_correction import ContributionCorrectionService

        cfg = await get_config()
        library_tv_path = Path(cfg.library_tv_path) if cfg.library_tv_path else None

        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError(f"Title {title_id} not found for job {job_id}")
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            if not title.organized_to:
                raise ValueError("Title has no organized file to amend")

            current = Path(title.organized_to)
            if not current.exists():
                raise ValueError(f"Organized file is missing: {current}")

            show = job.tmdb_name or job.detected_title or job.volume_label
            tmdb_id = str(job.tmdb_id) if job.tmdb_id else None

            if target.kind == "episode":
                if not target.episode_code:
                    raise ValueError("episode_code required for episode reassignment")
                result = organize_tv_episode(
                    current,
                    show,
                    target.episode_code,
                    library_path=library_tv_path,
                    conflict_resolution="ask",
                    year=job.tmdb_year,
                    tmdb_id=tmdb_id,
                    ordering=title.episode_ordering or "aired",
                    episode_group_id=title.episode_group_id,
                )
                if not result.get("success"):
                    raise ValueError(result.get("error") or "Organize failed")
                title.matched_episode = target.episode_code
                title.is_extra = False
                title.organized_to = str(result["final_path"])
            else:  # extra or discard — both land in Extras
                season = job.detected_season
                if season is None and title.matched_episode:
                    m = _re.match(r"S(\d{1,2})E", title.matched_episode)
                    season = int(m.group(1)) if m else 1
                result = organize_tv_extras(
                    current,
                    show,
                    season or 1,
                    library_path=library_tv_path,
                    disc_number=job.disc_number or 1,
                    title_index=title.title_index,
                    year=job.tmdb_year,
                    tmdb_id=tmdb_id,
                )
                if not result.get("success"):
                    raise ValueError(result.get("error") or "Organize failed")
                title.matched_episode = None
                title.is_extra = True
                title.organized_to = str(result["final_path"])

            title.match_source = "user"
            title.match_confidence = 1.0
            title.match_details = _strip_review_flags(title.match_details)
            title.organized_from = current.name

            await ContributionCorrectionService().correct_title_contribution(
                session,
                title,
                target,
                job=job,
                enable_contributions=cfg.enable_fingerprint_contributions,
                pseudonym=cfg.contribution_pseudonym,
            )

            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                matched_episode=title.matched_episode,
                match_confidence=1.0,
                match_source="user",
            )
            await ws_manager.broadcast_job_update(job_id, job.state.value)

        logger.info(
            f"Job {sanitize_log_value(job_id)}: amended title "
            f"{sanitize_log_value(title_id)} -> {sanitize_log_value(target.kind)}"
        )

    async def rerun_matching(self, job_id: int, source_preference: str | None = None) -> None:
        """Re-run episode matching for all titles in a job."""
        # A full re-match starts conflict escalation over from the first tier.
        self._finalization.reset_conflict_passes(job_id)
        # If a previous subtitle download failed (e.g. unresolvable label that
        # has since been corrected via re-identification), give the user a
        # recovery path: retry subtitles when they hit "Re-match all".
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            needs_subtitle_retry = (
                job is not None
                and job.content_type == ContentType.TV
                and job.subtitle_status == "failed"
                and job.detected_title
                and job.detected_season is not None
            )
            retry_args = (
                (job_id, job.detected_title, job.detected_season) if needs_subtitle_retry else None
            )

            # Leave REVIEW_NEEDED for MATCHING so the dashboard's live (WebSocket)
            # view follows the re-matching that now runs in the background. Without
            # this the static review page stays mounted showing only cleared matches.
            if job is not None and job.state == JobState.REVIEW_NEEDED:
                job.state = JobState.MATCHING
                job.conflict_status = None  # drop any stale escalation note
                job.updated_at = datetime.now(UTC)
                await session.commit()
                await ws_manager.broadcast_job_update(
                    job_id,
                    JobState.MATCHING.value,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                    conflict_status="",  # "" overwrites the merged-in stale value
                )
            elif job is not None and job.conflict_status is not None:
                # Re-matching from a non-review state (e.g. an escalation pass is
                # in flight): the in-memory counter was already reset above, so
                # clear the persisted note too — "rerun starts over" must apply
                # to the DB column, not just the counter.
                job.conflict_status = None
                job.updated_at = datetime.now(UTC)
                await session.commit()
                await ws_manager.broadcast_job_update(job_id, job.state.value, conflict_status="")

        if retry_args is not None:
            await self._matching.restart_subtitle_download(*retry_args)

        await self._rerun_matching(job_id, source_preference)

    async def rematch_single_title(
        self,
        job_id: int,
        title_id: int,
        source_preference: str | None = None,
        deep: bool = False,
    ) -> None:
        """Re-match a single title. Delegates to matching coordinator.

        ``deep`` re-runs the engram matcher at strict scan density + vote gate
        (the same params as conflict escalation), giving a low-confidence title a
        real chance to resolve instead of re-failing at the original depth.

        This is the manual, user-initiated entry point (the only caller is the
        ``/rematch`` route), so it runs ``advisory=True``: the result is surfaced
        in review for confirmation and never auto-organized. Pipeline/escalation/
        conflict callers invoke the coordinator method directly (advisory False).
        """
        # A real (user-initiated) match starts now — stop background prewarming.
        # cancel_for_job prevents future chunks; the in-flight thread still
        # finishes its current chunk (~60 s).
        self._prewarmer.cancel_for_job(job_id)
        await self._matching.rematch_single_title(
            job_id,
            title_id,
            source_preference,
            num_points=STRICT_SCAN_POINTS if deep else None,
            min_vote_count=STRICT_MIN_VOTES if deep else None,
            advisory=True,
        )

    async def rematch_conflict(self, job_id: int, episode_code: str) -> dict:
        """Deep re-match every title claiming ``episode_code`` (strict params).

        Returns ``{"dispatched": [...], "skipped": [{"title_id", "reason"}]}``.
        """
        return await self._matching.rematch_conflict(
            job_id,
            episode_code,
            num_points=STRICT_SCAN_POINTS,
            min_vote_count=STRICT_MIN_VOTES,
        )

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
            if next_state in (JobState.COMPLETED, JobState.FAILED):
                # lightweight sim-only path skips the state machine; uphold its
                # terminal prompt-clear invariant by hand
                job.identity_prompt_json = None
            await session.commit()

            await ws_manager.broadcast_job_update(job_id, next_state.value)
            return next_state.value

    async def advance_job_via_state_machine(self, job_id: int) -> str:
        """Advance a job one step through the state machine, firing all registered callbacks.

        Unlike advance_job(), this goes through state_machine.transition() so terminal
        callbacks (e.g. Discord notifications, contribution enqueue) fire on COMPLETED/FAILED.
        """
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

            ok = await state_machine.transition(job, next_state, session)
            if not ok:
                raise ValueError(f"State machine rejected: {job.state} -> {next_state}")

        return next_state.value

    async def fail_job_via_state_machine(self, job_id: int, reason: str) -> None:
        """Transition a job to FAILED through the state machine, firing all registered callbacks."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            if job.state in (JobState.COMPLETED, JobState.FAILED):
                raise ValueError("Job already in terminal state")
            await state_machine.transition_to_failed(job, session, error_message=reason)

    # --- Simulation (delegated) ---

    async def simulate_disc_insert(self, params: dict) -> int:
        """Simulate a disc insertion for testing purposes."""
        return await self._simulation.simulate_disc_insert(params)

    async def simulate_disc_insert_realistic(self, params: dict) -> int:
        """Simulate disc insertion using real MKV files from staging."""
        return await self._simulation.simulate_disc_insert_realistic(params)

    # --- Ripping ---

    async def _transition_title_out_of_ripping(
        self,
        job_id: int,
        title_id: int,
        content_type: ContentType,
        expected_size: int,
        actual_size: int,
        *,
        on_error_level: int = logging.WARNING,
    ) -> None:
        """Transition a no-longer-active title out of RIPPING.

        TV titles move to QUEUED (enqueued for matching; the QUEUED→MATCHING flip
        happens once a match slot is acquired), movie titles to MATCHED. Only acts
        on titles currently in RIPPING. Failures are logged at ``on_error_level``
        and swallowed so progress tracking is never interrupted.

        Identity gate (walk-away Phase B): mirrors ``_on_title_ripped`` — when
        the job carries an unanswered identity prompt, every content type parks
        in QUEUED regardless of ``content_type``, so a pending-prompt + fs-monitor
        poll-timing race cannot leak a title to MATCHED and make it invisible to
        ``dispatch_pending_matches`` (which only releases QUEUED titles).
        """
        is_tv = content_type == ContentType.TV
        try:
            async with async_session() as sess:
                title_db = await sess.get(DiscTitle, title_id)
                if title_db and title_db.state == TitleState.RIPPING:
                    job = await sess.get(DiscJob, job_id)
                    identity_pending = self._identity_pending(job)
                    new_state = (
                        TitleState.QUEUED if (is_tv or identity_pending) else TitleState.MATCHED
                    )
                    title_db.state = new_state
                    sess.add(title_db)
                    await sess.commit()
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        new_state.value,
                        expected_size_bytes=expected_size,
                        actual_size_bytes=actual_size,
                    )
        except Exception:
            logger.log(
                on_error_level,
                f"Failed to transition title {title_id} out of RIPPING (Job {job_id})",
                exc_info=True,
            )

    async def rerip_titles(self, job_id: int, title_ids: list[int]) -> None:
        """Re-rip specific rip-failed titles using the disc currently in the drive.

        Reuses the normal rip→match→complete machinery for a focused subset:
        transitions the job back to RIPPING (also blocking spurious reinserts),
        deletes each title's stale staging file so MakeMKV overwrites cleanly,
        re-rips only ``title_ids``, and lets the existing title-complete/-error
        callbacks drive re-matching and completion (Feature C).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return
            titles = []
            for tid in title_ids:
                t = await session.get(DiscTitle, tid)
                if t and t.job_id == job_id and t.state == TitleState.REVIEW:
                    titles.append(t)
            if not titles:
                logger.info(
                    f"Job {sanitize_log_value(job_id)}: no eligible titles to re-rip "
                    f"({sanitize_log_value(title_ids)})"
                )
                return

            # A re-rip writes its output into the job's staging dir; without one
            # there is nowhere to put it (e.g. a seed/debug job leaves
            # staging_path unset). Bail BEFORE the state transition so the job is
            # never stranded in RIPPING.
            if not job.staging_path:
                logger.error(
                    f"Job {sanitize_log_value(job_id)}: cannot re-rip — staging_path is not set"
                )
                return

            # Un-hide a cleared job that is being actively re-processed.
            if job.cleared_at is not None:
                job.cleared_at = None
                session.add(job)

            # REVIEW_NEEDED -> RIPPING (valid; also makes a spurious reinsert an
            # unconditional drive-busy block during the re-rip). Bail if the job
            # can't transition (e.g. a double sentinel fire while already RIPPING):
            # transition() returns False and makes no state change.
            if not await state_machine.transition(job, JobState.RIPPING, session):
                logger.warning(
                    f"Job {sanitize_log_value(job_id)}: cannot re-rip — invalid transition "
                    f"from {sanitize_log_value(job.state.value)} to RIPPING; skipping."
                )
                return

            staging_dir = Path(job.staging_path)
            staging_dir.mkdir(parents=True, exist_ok=True)
            drive_id = job.drive_id

            for t in titles:
                t.rerip_attempts = (t.rerip_attempts or 0) + 1
                t.state = TitleState.RIPPING
                if t.output_filename:
                    old = Path(t.output_filename)
                    try:
                        if old.exists():
                            old.unlink()
                    except OSError as e:
                        logger.warning(
                            f"Job {sanitize_log_value(job_id)}: could not remove stale file "
                            f"{sanitize_log_value(old)}: {e}"
                        )
                t.output_filename = None
                session.add(t)
                await ws_manager.broadcast_title_update(job_id, t.id, TitleState.RIPPING.value)
            await session.commit()

            subset_sorted = sorted(titles, key=lambda x: x.title_index)
            rip_indices = [t.title_index for t in subset_sorted]
            for t in subset_sorted:
                session.expunge(t)

        self._note_activity(job_id)

        def on_title_complete(idx: int, path: Path):
            future = asyncio.run_coroutine_threadsafe(
                self._on_title_ripped(job_id, idx, path, subset_sorted), self._loop
            )

            def _check(fut):
                try:
                    fut.result(timeout=30)
                except Exception as e:  # noqa: BLE001 — surface, never swallow
                    logger.exception(f"[RERIP] _on_title_ripped failed (Job {job_id}): {e}")

            future.add_done_callback(_check)

        def on_title_error(cmd_idx: int, reason: str):
            list_idx = cmd_idx - 1
            if not (0 <= list_idx < len(subset_sorted)):
                logger.error(
                    f"Job {sanitize_log_value(job_id)}: re-rip title error "
                    f"cmd_idx={sanitize_log_value(cmd_idx)} out of range"
                )
                return
            title_id_err = subset_sorted[list_idx].id
            future = asyncio.run_coroutine_threadsafe(
                self._matching.route_rip_failure_to_review(
                    job_id, title_id_err, "rip_stalled", reason
                ),
                self._loop,
            )

            def _check_err(fut):
                # Mirror on_title_complete: a failure inside the threadsafe
                # coroutine would otherwise be swallowed, leaving the title in
                # RIPPING with no recovery path and nothing in the logs.
                try:
                    fut.result(timeout=30)
                except Exception as e:  # noqa: BLE001 — surface, never swallow
                    logger.exception(
                        f"[RERIP] route_rip_failure_to_review failed (Job {job_id}): {e}"
                    )

            future.add_done_callback(_check_err)

        from app.core.discdb_exporter import get_makemkv_log_dir
        from app.services.config_service import get_config

        cfg = await get_config()
        stall_timeout = cfg.ripping_stall_timeout if cfg else 120.0

        result = await self._extractor.rip_titles(
            drive_id,
            staging_dir,
            title_indices=rip_indices,
            title_complete_callback=on_title_complete,
            stall_timeout=stall_timeout,
            title_error_callback=on_title_error,
            log_dir=get_makemkv_log_dir(job_id),
            job_id=job_id,
        )

        # A clean MakeMKV failure (disc unreadable) returns success=False without
        # a per-title stall callback — route any still-RIPPING title back to review.
        if not result.success:
            async with async_session() as session:
                for t in subset_sorted:
                    db_t = await session.get(DiscTitle, t.id)
                    if db_t and db_t.state == TitleState.RIPPING:
                        await self._matching.route_rip_failure_to_review(
                            job_id, t.id, "incomplete_rip", INCOMPLETE_RIP_MESSAGE
                        )

        # Free the drive for the next disc.
        if cfg and cfg.auto_eject_enabled:
            try:
                from app.core.sentinel import eject_disc

                await asyncio.to_thread(eject_disc, drive_id)
                self._drive_monitor.notify_ejected(drive_id)
            except (OSError, RuntimeError) as e:
                logger.warning(f"Job {sanitize_log_value(job_id)}: eject after re-rip failed: {e}")

    async def rerip_title_manual(self, job_id: int, title_id: int) -> None:
        """Manually re-rip one title using the disc currently in the drive.

        Verifies the inserted disc matches the job by ContentHash and bypasses
        the automatic retry cap (the user explicitly asked). Spawns the re-rip in
        the background so the request returns promptly (Feature C).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            title = await session.get(DiscTitle, title_id)
            if not job or not title or title.job_id != job_id:
                raise ValueError("Job or title not found")
            if title.state != TitleState.REVIEW:
                raise ValueError("Title is not awaiting re-rip")
            if job.state != JobState.REVIEW_NEEDED:
                # rerip_titles can only transition REVIEW_NEEDED -> RIPPING; a
                # busy job (RIPPING/MATCHING/ORGANIZING) would silently no-op.
                raise ValueError("Job is not awaiting re-rip review")
            drive_id = job.drive_id
            job_hash = job.content_hash

        current_hash = await self._compute_disc_hash(drive_id)
        if not current_hash:
            raise ValueError("No readable disc in the drive — insert the matching disc first")
        if job_hash:
            if current_hash != job_hash:
                raise ValueError("A different disc is in the drive — insert the original disc")
        else:
            # Pre-#369 jobs (and seed/debug jobs) may have no stored ContentHash;
            # we can't verify disc identity. Allow the user-initiated re-rip but
            # log the bypass so it's visible rather than silent.
            logger.warning(
                f"Job {sanitize_log_value(job_id)}: content_hash not set — skipping "
                f"disc-identity check for manual re-rip"
            )

        task = asyncio.create_task(
            with_job_log_context(job_id, self.rerip_titles(job_id, [title_id]))
        )
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def _run_ripping(self, job_id: int) -> None:
        """Execute the ripping process.

        The setup session is scoped to setup only and released *before* the
        (potentially multi-hour) rip is awaited, so a single aiosqlite connection
        is never held across the rip — that previously risked SQLITE_BUSY under
        concurrent multi-drive jobs. After setup the ``job`` ORM object is
        detached, so every post-rip transition re-fetches the row on its own
        short-lived session, and failures route through :meth:`_fail_job`.
        """
        # job_id originates from the review path param (apply_review -> _run_ripping),
        # so it is user-provided — sanitize before logging to prevent CR/LF log
        # forging (py/log-injection).
        safe_job = sanitize_log_value(job_id)
        try:
            # --- Setup: short-lived session, released before the long rip ---
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job:
                    return

                # Capture scalars the closures / rip / post-rip need; `job` is
                # detached once this block exits, so nothing downstream touches it.
                content_type = job.content_type
                drive_id = job.drive_id
                staging_path = job.staging_path
                volume_label = job.volume_label
                detected_title = job.detected_title
                title_count = job.total_titles or 0
                output_dir = Path(staging_path)

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

                if not has_selection and content_type == ContentType.MOVIE and disc_titles:
                    longest = max(disc_titles, key=lambda t: t.duration_seconds or 0)
                    longest.is_selected = True
                    session.add(longest)
                    await session.commit()
                    has_selection = True

                titles_to_rip = disc_titles
                if has_selection:
                    titles_to_rip = [dt for dt in disc_titles if dt.is_selected]

                # Safety net: transition deselected PENDING titles to terminal state
                # SKIPPED titles are already terminal, leave them alone. Only a
                # deselected-but-still-PENDING title needs the safety-net nudge.
                deselected_ids = [
                    dt.id
                    for dt in disc_titles
                    if not dt.is_selected
                    and dt.state == TitleState.PENDING
                    and dt.state != TitleState.SKIPPED
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

            # Setup session released — the long rip runs with no DB connection held.

            await ws_manager.broadcast_job_update(
                job_id, JobState.RIPPING.value, current_title=1, total_titles=title_count
            )

            speed_calc = SpeedCalculator(total_job_bytes)

            _titles_marked_ripping: set[int] = set()
            _progress_lock = asyncio.Lock()
            _background_tasks: set[asyncio.Task] = set()

            # Title complete callback — called from extractor thread
            def on_title_complete(idx: int, path: Path):
                logger.info(f"[CALLBACK] Title complete: idx={idx} path={path.name} (Job {job_id})")
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

            # Rip the whole disc in a single MakeMKV invocation when every title
            # is selected — one disc open instead of one per title. MakeMKV
            # re-opens and re-scans the disc on every invocation, so per-title
            # looping thrashes a disc with many titles/extras. If the single pass
            # stalls or errors and leaves titles missing, they are recovered
            # individually after the rip (one-pass + per-title fallback below).
            rip_all = len(rip_indices) == len(disc_titles)
            if rip_all:
                rip_indices = None

            # Filesystem-based progress monitor — the single source of truth for
            # per-title and overall rip progress. (MakeMKV's PRGC/PRGV robot
            # codes carry no usable per-title index, so the output-file sizes are
            # the only signal that reliably maps to a specific title.)
            async def _filesystem_progress_monitor():
                _prev_file_sizes: dict[int, int] = {}

                while True:
                    # Poll faster than a title can rip so sub-2s extras still get
                    # a visible RIPPING frame instead of flashing past unnoticed.
                    await asyncio.sleep(1.0)
                    try:
                        async with _progress_lock:
                            total_done = 0
                            current_title_num = 0
                            file_sizes: dict[int, int] = {}
                            for t in sorted_titles:
                                pattern = f"*_t{expected_native_index(t):02d}.mkv"
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

                            # Only active file growth is evidence of rip progress
                            # (the heartbeat the removed PRGV progress callback used
                            # to provide). Disc-scan at the start of an 'all' pass
                            # produces no file growth, but the watchdog seeds its
                            # clock on first encounter of a RIPPING job, so no
                            # heartbeat is needed until the first write.
                            if active_title_id is not None:
                                self._note_activity(job_id)

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
                                                f"Job {safe_job}: failed to set title {t.id} "
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
                                    await self._transition_title_out_of_ripping(
                                        job_id,
                                        t.id,
                                        content_type,
                                        expected_size=t.file_size_bytes,
                                        actual_size=fsize,
                                        on_error_level=logging.DEBUG,
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
                                    f"Job {safe_job}: failed to update progress in DB",
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
                            f"Job {safe_job}: filesystem progress monitor error",
                            exc_info=True,
                        )

            monitor_task = asyncio.create_task(_filesystem_progress_monitor())
            _background_tasks.add(monitor_task)
            monitor_task.add_done_callback(_background_tasks.discard)

            # Run extraction
            try:
                from app.core.discdb_exporter import get_makemkv_log_dir

                result = await self._extractor.rip_titles(
                    drive_id,
                    output_dir,
                    title_indices=rip_indices,
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
                    # Expected: the monitor task was just cancelled above.
                    pass

            # One-pass + per-title fallback: a single 'all' pass that stalls or
            # errors leaves later titles unripped. Re-rip just the still-missing
            # selected titles individually so one bad title can't lose the rest of
            # the disc. (Live progress isn't refreshed during this rare recovery
            # pass — titles still finalize via the title-complete callback, and
            # reconcile_stuck_titles is the final safety net.)
            stall_title_list = sorted_titles
            if rip_all:
                async with async_session() as chk_session:
                    missing = []
                    for t in sorted_titles:
                        db_t = await chk_session.get(DiscTitle, t.id)
                        if (
                            db_t
                            and db_t.state in (TitleState.PENDING, TitleState.RIPPING)
                            and not self._has_complete_output(output_dir, t)
                        ):
                            missing.append(t)
                if missing:
                    logger.warning(
                        f"Job {safe_job}: single-pass rip left {len(missing)} title(s) "
                        f"missing; re-ripping individually: "
                        f"{[t.title_index for t in missing]}"
                    )
                    # The fs monitor (the sole stale-job heartbeat during ripping)
                    # was cancelled above, so reset the watchdog clock to give the
                    # fallback the full timeout_ripping_seconds window — otherwise a
                    # slow per-title recovery could trip reconcile_and_advance.
                    self._note_activity(job_id)
                    result = await self._extractor.rip_titles(
                        drive_id,
                        output_dir,
                        title_indices=[t.title_index for t in missing],
                        title_complete_callback=on_title_complete,
                        stall_timeout=stall_timeout,
                        title_error_callback=on_title_error,
                        log_dir=None,  # keep the informative all-pass rip.log
                        job_id=job_id,
                    )
                    # Stall bookkeeping below now refers to the fallback's per-title
                    # command order, not the original full title list.
                    stall_title_list = missing
                else:
                    # The single pass produced every title. 'all'-mode stall
                    # bookkeeping uses command indices that don't map to titles,
                    # so discard it — nothing actually failed.
                    result.stalled_titles = None
                    result.success = True

            if not result.success and not result.stalled_titles:
                await self._fail_job(job_id, result.error_message)
                return

            # Fallback: a stalled title is a rip-level failure → REVIEW
            # (re-rippable), not FAILED, so the job holds in REVIEW_NEEDED.
            if result.stalled_titles:
                for cmd_idx in result.stalled_titles:
                    list_idx = cmd_idx - 1
                    if 0 <= list_idx < len(stall_title_list):
                        stalled_title = stall_title_list[list_idx]
                        logger.warning(
                            f"Job {safe_job}: title {stalled_title.title_index} "
                            f"stalled (fallback) → REVIEW (re-rippable)"
                        )
                        await self._matching.route_rip_failure_to_review(
                            job_id, stalled_title.id, "rip_stalled", STALL_FAILURE_REASON
                        )

            # Eject disc and reset sentinel state so a new disc insert is detected
            if rip_config and rip_config.auto_eject_enabled:
                try:
                    from app.core.sentinel import eject_disc

                    await asyncio.to_thread(eject_disc, drive_id)
                    self._drive_monitor.notify_ejected(drive_id)
                except (OSError, RuntimeError) as e:
                    logger.warning(f"Could not eject disc from {drive_id}: {e}")

            # Post-rip convergence (walk-away Phase B4): re-read the job row —
            # the locals captured at setup go stale once mid-rip identity
            # answers (B5) mutate the job, so the fresh DB values drive the
            # TV-vs-movie fork below. volume_label is deliberately NOT
            # re-read: answers set detected_title, never the label; the movie
            # organize fallback may keep the setup-time label.
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job:
                    return
                content_type = job.content_type
                detected_title = job.detected_title
                blocking_prompt = self._blocking_identity_prompt(job)

            answered_mid_convergence = False
            if blocking_prompt is not None:
                # An unanswered BLOCKING identity question (kind=name/reidentify)
                # survived to rip end → the job parks in pooled review. Recover
                # stranded titles FIRST, while the prompt is still set, so the
                # identity gate inside parks them in QUEUED (clearing the prompt
                # before reconciling would let non-TV strays leak to MATCHED).
                await self._backfill_unmatched_titles(job_id, output_dir, sorted_titles)
                await self.reconcile_stuck_titles(job_id)
                if await self._converge_identity_pending_job(job_id, blocking_prompt):
                    return
                # B5 race tolerance: the prompt was answered between the
                # post-rip read above and the convergence re-read (the
                # backfill/reconcile awaits yield the loop). Re-read the
                # corrected identity and continue down the normal post-rip
                # flow — the repeated backfill/reconcile below are idempotent.
                answered_mid_convergence = True
                async with async_session() as session:
                    job = await session.get(DiscJob, job_id)
                    if not job:
                        return
                    content_type = job.content_type
                    detected_title = job.detected_title

            if content_type == ContentType.TV:
                await self._backfill_unmatched_titles(job_id, output_dir, sorted_titles)

                # Recover any title the rip left stranded in PENDING/RIPPING (e.g. the
                # final title whose completion callback never fired) before advancing.
                await self.reconcile_stuck_titles(job_id)

                async with async_session() as session:
                    job = await session.get(DiscJob, job_id)
                    if not job:
                        return
                    logger.info(
                        f"[RIP-DONE] Job {job_id}: rip_titles returned, "
                        f"{len(result.output_files)} files produced. "
                        f"Job state={job.state.value}. Backfill complete."
                    )

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

                if answered_mid_convergence:
                    # The mid-window answer's dispatch may have raced the
                    # backfill/reconcile above (titles parked QUEUED after the
                    # answer's sweep) — sweep again. Idempotent: QUEUED-only
                    # plus the in-flight dispatch guard.
                    await self.dispatch_pending_matches(job_id)

            else:
                # Movie post-ripping flow

                # Recover any title stranded in PENDING/RIPPING (movie titles move to
                # MATCHED) so the main-feature selection below sees every ripped file.
                await self.reconcile_stuck_titles(job_id)

                await self._finalize_ripped_movie(job_id, output_dir, volume_label, detected_title)

        except asyncio.CancelledError:
            logger.info(f"Job {safe_job} was cancelled")
            try:
                await self._fail_job(job_id, "Cancelled by user")
            except Exception:
                logger.warning(
                    f"Job {safe_job}: _fail_job raised during cancellation recovery",
                    exc_info=True,
                )
            # Re-raise so the task is actually marked cancelled (asyncio convention).
            raise
        except Exception as e:
            logger.exception(f"Error ripping job {safe_job}")
            try:
                await self._fail_job(job_id, str(e))
            except Exception:
                # Don't let a recovery failure shadow the original error above.
                logger.warning(
                    f"Job {safe_job}: _fail_job raised during error recovery",
                    exc_info=True,
                )

    async def _fail_job(self, job_id: int, error_message: str | None) -> None:
        """Fail a job on its own short-lived session.

        Lets the ripping error paths fail a job without depending on a session
        that may already be closed or a detached ``job`` ORM object.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job:
                await state_machine.transition_to_failed(
                    job, session, error_message=error_message or "Ripping failed"
                )

    async def _finalize_ripped_movie(
        self,
        job_id: int,
        output_dir: Path,
        volume_label: str,
        detected_title: str | None,
    ) -> None:
        """Run the movie tail of a finished rip: feature resolution → organize.

        Shared by the rip-end movie branch of ``_run_ripping`` and the post-rip
        identity-answer path (``_resume_movie_post_rip``, walk-away B5) so the
        two can't diverge. Multi-title discs go through TheDiscDB-MainMovie /
        feature-vs-extras resolution (possibly parking in review); otherwise
        the job organizes and completes. Title state is not a gate here — the
        completion loop finishes every non-terminal title, including ones an
        identity answer released to MATCHED or left QUEUED.
        """
        safe_job = sanitize_log_value(job_id)
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            titles_result = await session.execute(
                select(DiscTitle).where(DiscTitle.job_id == job_id)
            )
            ripped_titles = [t for t in titles_result.scalars().all() if t.is_selected]

            if len(ripped_titles) > 1:
                sent_to_review = await self._resolve_multi_title_movie(
                    job, job_id, ripped_titles, session
                )
                if sent_to_review:
                    return

            # Single title flow (Standard Movie). Commit ORGANIZING and
            # release the session before the blocking organize so no DB
            # connection is held across it (mirrors the rip itself).
            job.state = JobState.ORGANIZING
            await session.commit()

        await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

        organize_result = await asyncio.to_thread(
            movie_organizer.organize,
            output_dir,
            volume_label,
            detected_title,
        )

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            if organize_result["success"]:
                job.final_path = str(organize_result["main_file"])
                job.progress_percent = 100.0

                extras_mapping = organize_result.get("extras_mapping", {})

                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                for t in titles_result.scalars().all():
                    if t.state not in (
                        TitleState.COMPLETED,
                        TitleState.FAILED,
                    ):
                        t.state = TitleState.COMPLETED
                        t.organized_from = t.output_filename
                        output_basename = (
                            Path(t.output_filename).name if t.output_filename else None
                        )
                        if output_basename and output_basename in extras_mapping:
                            t.organized_to = str(extras_mapping[output_basename])
                            t.is_extra = True
                        else:
                            t.organized_to = str(organize_result.get("main_file") or "")
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
                logger.info(f"Job {safe_job} completed: {organize_result['main_file']}")
            else:
                await state_machine.transition_to_failed(
                    job,
                    session,
                    error_message=organize_result["error"],
                )

    async def _converge_identity_pending_job(self, job_id: int, prompt: dict) -> bool:
        """Park a rip-finished job whose identity question is still open in review.

        Walk-away Phase B4: the non-blocking identity CTA converts into the
        blocking pooled-review payload at rip end — ``review_reason`` carries
        the prompt's reason verbatim (preserving the frontend literal
        contracts: "label unreadable", "merged without separators",
        candidates_json-driven modals) and ``identity_prompt_json`` is cleared
        in the same commit. Parked QUEUED titles stay QUEUED (active in
        ``check_job_completion``); the review flow and the answer endpoints
        (B5) own them. The Phase A transcript prewarmer fires automatically via
        the state machine's ``on_transition`` observer — no explicit kickoff.

        Returns True when the rip-end flow should stop here (job parked in
        review, already out of RIPPING, or gone). Returns False when the
        prompt was ANSWERED between the caller's post-rip prompt read and this
        re-read (B5 race tolerance — the backfill/reconcile awaits in between
        yield the event loop): the caller then falls through to the normal
        post-rip flow with the corrected identity instead of parking an
        answered job behind a stale question.
        """
        safe_job = sanitize_log_value(job_id)
        reason = prompt.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = _FALLBACK_IDENTITY_REVIEW_REASON
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return True
            if job.state != JobState.RIPPING:
                # e.g. every title stalled → route_rip_failure_to_review already
                # parked the job in REVIEW_NEEDED with its own reason. Leave the
                # prompt in place — the review flow / answer endpoints own it.
                logger.info(
                    f"Job {safe_job}: skipping identity convergence — job already in "
                    f"{job.state.value}"
                )
                return True
            if self._blocking_identity_prompt(job) is None:
                # Answered (or otherwise no longer blocking) on the fresh row.
                logger.info(
                    f"Job {safe_job}: identity prompt answered mid-convergence — "
                    f"skipping review park"
                )
                return False
            job.identity_prompt_json = None
            succeeded = await state_machine.transition_to_review(
                job, session, reason=reason, broadcast=False
            )
        if succeeded:
            # "" deliberately: the enumerated-WS clear pattern ("" clears the
            # field on the frontend merge; None means "unchanged").
            await ws_manager.broadcast_job_update(
                job_id,
                JobState.REVIEW_NEEDED.value,
                review_reason=reason,
                identity_prompt_json="",
            )
            logger.info(
                f"Job {safe_job}: rip finished with unanswered identity prompt "
                f"(kind={sanitize_log_value(prompt.get('kind'))}) → REVIEW_NEEDED"
            )
        return True

    async def _resolve_multi_title_movie(
        self,
        job: DiscJob,
        job_id: int,
        ripped_titles: list[DiscTitle],
        session,
    ) -> bool:
        """Decide what to do when a movie rip produced more than one title.

        Prefers a TheDiscDB ``MainMovie`` tag; otherwise distinguishes the real
        feature from long bonus tracks via the movie's TMDB runtime (with a
        duration/bitrate fallback). Tags non-feature titles as extras. Only
        genuinely competing features (alternate cuts / obfuscation playlists)
        require a review.

        Returns True when the job was sent to REVIEW_NEEDED (caller should stop).
        """
        # job_id reaches this method from the /jobs/{job_id}/... review path param
        # (apply_review -> _run_ripping), so it is user-provided — sanitize before
        # logging to prevent CR/LF log forging (py/log-injection).
        safe_job = sanitize_log_value(job_id)
        discdb_maps = self._matching.get_discdb_mappings(job_id)
        main_movie_idx = next((m.index for m in discdb_maps if m.title_type == "MainMovie"), None)
        if main_movie_idx is not None:
            for dt in ripped_titles:
                dt.is_selected = dt.title_index == main_movie_idx
                dt.is_extra = dt.title_index != main_movie_idx
                session.add(dt)
            await session.commit()
            logger.info(
                f"Job {safe_job}: TheDiscDB auto-selected MainMovie "
                f"title index {main_movie_idx}, skipping review"
            )
            return False

        decision = await self._resolve_movie_feature(job, ripped_titles)

        for dt in ripped_titles:
            if dt.title_index in decision.extra_indices:
                # Extras are never the selected feature, in either path: deselected so
                # the version picker (review) and downstream state stay consistent.
                dt.is_extra = True
                dt.is_selected = False
                session.add(dt)
        await session.commit()

        if decision.needs_review:
            reason = (
                decision.review_reason
                or "Multiple feature-length titles found. Please select the correct version."
            )
            await state_machine.transition_to_review(job, session, reason=reason, broadcast=False)
            await ws_manager.broadcast_job_update(
                job_id, JobState.REVIEW_NEEDED.value, error=reason
            )
            logger.info(
                f"Job {safe_job}: {len(decision.candidate_indices)} feature candidates "
                f"{decision.candidate_indices} ripped — waiting for user selection."
            )
            return True

        logger.info(
            f"Job {safe_job}: auto-selected feature title {decision.feature_index}; "
            f"tagged {decision.extra_indices} as extras (no review)."
        )
        return False

    async def _resolve_movie_feature(self, job: DiscJob, ripped_titles: list[DiscTitle]):
        """Decide which ripped title is the movie's main feature.

        Uses the movie's TMDB runtime (when available) plus a duration/bitrate
        fallback so long bonus tracks aren't mistaken for the feature. Returns a
        ``MainFeatureDecision`` (see ``select_movie_main_feature``).
        """
        from app.core.analyst import TitleInfo, select_movie_main_feature
        from app.matcher.tmdb_client import fetch_movie_runtime
        from app.services.config_service import get_config

        config = await get_config()

        runtime = None
        if job.tmdb_id and config.tmdb_api_key:
            try:
                runtime = await asyncio.to_thread(
                    fetch_movie_runtime, str(job.tmdb_id), config.tmdb_api_key
                )
            except Exception as e:
                logger.warning(f"Job {job.id}: movie runtime lookup failed: {e}", exc_info=True)

        infos = [
            TitleInfo(
                index=t.title_index,
                duration_seconds=t.duration_seconds or 0,
                size_bytes=t.file_size_bytes or 0,
                chapter_count=t.chapter_count or 0,
            )
            for t in ripped_titles
        ]
        return select_movie_main_feature(
            infos, runtime, min_feature_duration=config.analyst_movie_min_duration
        )

    @staticmethod
    def _identity_pending(job: DiscJob | None) -> bool:
        """True when the job carries an unanswered BLOCKING identity question.

        Walk-away Phase B: ``identity_prompt_json`` is set when a disc rips
        first with an open identity question, but only kinds that mean "no
        confirmed show identity" park titles (``name``/``reidentify``, plus
        malformed prompts — fail closed). ``kind="season"`` is a shortcut CTA:
        the show identity IS confirmed and cross-season matching handles the
        unknown season, so its titles dispatch normally. Parking them would
        hang the job permanently — QUEUED titles refresh the MATCHING
        watchdog clock forever with no timeout and nothing ever dispatching.
        Matching without a confirmed show identity, by contrast, would burn
        ASR against the wrong (or no) reference corpus.
        """
        return JobManager._blocking_identity_prompt(job) is not None

    @staticmethod
    def _blocking_identity_prompt(job: DiscJob | None) -> dict | None:
        """Parse ``identity_prompt_json``; return the prompt only when it BLOCKS.

        Prompt-kind semantics (walk-away Phase B): ``kind="season"`` is
        non-blocking — the show identity is known and cross-season matching
        proceeds, so the CTA is just a shortcut — and returns None here, same
        as no prompt at all. ``kind="name"``/``"reidentify"`` mean there is no
        confirmed show identity, so they block. Malformed JSON or an
        unrecognized kind also blocks (fail closed, with a warning): a prompt
        we can't interpret must not let the job advance as if identity were
        confirmed.

        ``_identity_pending`` (the mid-rip QUEUED-parking gate) delegates
        here, so the parking gates and the rip-end convergence can never
        disagree about which prompts block.
        """
        if job is None or job.identity_prompt_json is None:
            return None
        try:
            prompt = json.loads(job.identity_prompt_json)
        except (json.JSONDecodeError, TypeError):
            prompt = None
        if not isinstance(prompt, dict):
            logger.warning(
                f"Job {job.id}: malformed identity_prompt_json "
                f"({sanitize_log_value(job.identity_prompt_json)!r}) — treating as blocking"
            )
            return {"kind": "unknown", "reason": _FALLBACK_IDENTITY_REVIEW_REASON}
        if prompt.get("kind") == "season":
            return None
        if prompt.get("kind") not in BLOCKING_KINDS:
            # Unrecognized kind (newer writer?) — fail closed, but say so.
            logger.warning(
                f"Job {job.id}: unrecognized identity prompt kind "
                f"{prompt.get('kind')!r} — treating as blocking"
            )
        return prompt

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

            if title.state == TitleState.SKIPPED:
                # Mid-"all"-pass skip: MakeMKV wrote this title before we could
                # drop it. Best-effort: delete the file and do not match it.
                # title_index and strerror derive from the disc scan, so sanitize
                # them inline before logging (py/log-injection), matching this
                # file's established sanitize_log_value pattern.
                try:
                    path.unlink(missing_ok=True)
                    logger.info(
                        f"Job {sanitize_log_value(job_id)}: title "
                        f"{sanitize_log_value(title.title_index)} skipped by user, "
                        f"deleted its ripped file"
                    )
                except OSError as e:
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: could not delete skipped "
                        f"file for title {sanitize_log_value(title.title_index)}: "
                        f"{sanitize_log_value(e.strerror)}"
                    )
                await self._finalization.check_job_completion(session, job_id)
                return

            title.output_filename = str(path)

            job = await session.get(DiscJob, job_id)
            is_tv = job is not None and job.content_type == ContentType.TV
            # Identity gate (walk-away Phase B): with an unanswered identity
            # prompt, EVERY content type parks in QUEUED — an unknown-type job
            # must not fall into the non-TV → MATCHED branch below (that would
            # mark titles matched with no identity). dispatch_pending_matches
            # releases the parked titles once the prompt is answered.
            identity_pending = self._identity_pending(job)
            if title.state in (TitleState.PENDING, TitleState.RIPPING):
                if is_tv or identity_pending:
                    # Enqueued for matching → QUEUED; the QUEUED→MATCHING flip
                    # happens in match_single_file once a slot is acquired.
                    title.state = TitleState.QUEUED
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
            title_id = title.id

        if identity_pending:
            logger.info(
                f"Job {job_id}: identity prompt pending — title {title_id} held in QUEUED "
                f"(matching deferred until the identity question is answered)"
            )
            return

        if is_tv:
            await self._dispatch_title_match(job_id, title_id, path)

    async def _dispatch_title_match(self, job_id: int, title_id: int, file_path: Path) -> bool:
        """Run the standard per-title match dispatch for a ripped file.

        Shared by ``_on_title_ripped``, ``reconcile_stuck_titles``, and
        ``dispatch_pending_matches`` so the sites can't diverge: dispatches a
        ``match_single_file`` task with the standard done callback. (DiscDB
        episode assignment is no longer a pre-dispatch step; under ASR-preferred
        precedence it runs only as a low-confidence fallback inside
        ``_match_single_file_inner``.) Owns its session — match tasks must own
        their own sessions.

        Skips a title that already has a live match task (see
        ``_inflight_match_dispatch``), making repeat dispatch safe. Returns
        True when a match task was dispatched, False when skipped (in-flight,
        or title vanished).

        TOCTOU note: the membership check and ``add`` are separated by NO
        awaits — both run in a single event-loop turn, so they are effectively
        atomic on asyncio's single-threaded event loop.  Any early-return path
        that does NOT ultimately spawn a ``create_task`` must ``discard`` the
        sentinel so a subsequent legitimate dispatch is not permanently blocked.
        """
        if title_id in self._inflight_match_dispatch:
            logger.debug(
                f"Job {job_id}: title {title_id} already has a live match task — skipping dispatch"
            )
            return False
        # Claim the slot immediately — no awaits between here and the check
        # above so the claim is atomic on the single event loop.  All paths
        # that don't reach create_task must discard the sentinel.
        self._inflight_match_dispatch.add(title_id)

        try:
            async with async_session() as session:
                title = await session.get(DiscTitle, title_id)
                if title is None:
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: title "
                        f"{sanitize_log_value(title_id)} vanished before match dispatch"
                    )
                    self._inflight_match_dispatch.discard(title_id)
                    return False
        except Exception:
            self._inflight_match_dispatch.discard(title_id)
            raise

        # ASR-preferred precedence: always run audio matching. A DiscDB episode
        # mapping (disc order, not aired order) is applied only as a low-confidence
        # fallback inside _match_single_file_inner.
        # match_single_file self-tags the job log context.
        task = asyncio.create_task(self._matching.match_single_file(job_id, title_id, file_path))
        task.add_done_callback(
            lambda t, jid=job_id, tid=title_id: self._on_match_dispatch_done(t, jid, tid)
        )
        return True

    def _on_match_dispatch_done(self, task: asyncio.Task, job_id: int, title_id: int) -> None:
        """Release the in-flight dispatch guard, then run the matching done callback."""
        self._inflight_match_dispatch.discard(title_id)
        self._matching.on_match_task_done(task, job_id, title_id)

    async def dispatch_pending_matches(self, job_id: int) -> int:
        """Dispatch matching for the job's QUEUED titles whose ripped file exists.

        Retroactive release for titles parked by the identity gate in
        ``_on_title_ripped`` (walk-away Phase B): the identity-answer paths call
        this (via ``_apply_identity_resume_action``, B5) once the user resolves
        the question. Callers MUST clear ``identity_prompt_json`` first — this
        does not self-check the prompt. Caller contract: this runs *episode*
        matching, so only call it for jobs resolved to TV — an answer that
        resolves the job to movie must route to the movie feature-resolution
        path instead. A defensive content-type guard returns 0 (and logs) for a
        non-TV job so a misrouted caller can't episode-match movie titles.

        Idempotent at the dispatch level: only QUEUED titles qualify, and
        ``_dispatch_title_match`` skips titles with a live match task (the
        QUEUED→MATCHING flip happens only post-semaphore, so the in-flight set
        — not title state — is what prevents a double spawn). Returns the
        number of titles dispatched.
        """
        pending: list[tuple[int, Path]] = []
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job and job.content_type != ContentType.TV:
                # Defensive: this runs EPISODE matching, so a caller that passed
                # the wrong resume action (e.g. routed a movie-resolved answer
                # here instead of the feature-resolution path) must not silently
                # episode-match movie titles. Skip rather than corrupt.
                logger.error(
                    f"Job {sanitize_log_value(job_id)}: dispatch_pending_matches called "
                    f"on non-TV job (content_type={job.content_type}) — skipping"
                )
                return 0
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            for t in result.scalars().all():
                if t.state != TitleState.QUEUED or not t.output_filename:
                    continue
                file_path = Path(t.output_filename)
                if not file_path.exists():
                    logger.warning(
                        f"Job {sanitize_log_value(job_id)}: queued title "
                        f"{sanitize_log_value(t.id)} skipped — ripped file missing "
                        f"({sanitize_log_value(file_path.name)})"
                    )
                    continue
                pending.append((t.id, file_path))

        dispatched = 0
        for title_id, file_path in pending:
            if await self._dispatch_title_match(job_id, title_id, file_path):
                dispatched += 1
        if dispatched:
            logger.info(
                f"Job {sanitize_log_value(job_id)}: dispatched matching for "
                f"{dispatched} queued title(s)"
            )
        return dispatched

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
        # A ripping stall is a rip-level failure: route to REVIEW (re-rippable),
        # not FAILED, so the job holds in REVIEW_NEEDED (Feature C).
        await self._matching.route_rip_failure_to_review(
            job_id, stalled_title.id, "rip_stalled", reason
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
            # Pass an explicit (type, value, tb) tuple — the portable form
            # accepted by logging on every supported Python version — so the
            # traceback is always captured.
            logger.error(
                f"Job {job_id} task failed with exception: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

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

    async def _download_subtitles(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Download subtitles — exposed for test routes."""
        await self._matching.download_subtitles(job_id, show_name, season, tmdb_id)


# Singleton instance
job_manager = JobManager()
