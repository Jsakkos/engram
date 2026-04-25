"""Simulation Service - Test/debug simulation endpoints.

Extracted from JobManager to isolate simulation concerns.
Only active when DEBUG=true.
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websocket import manager as ws_manager
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import build_title_list

logger = logging.getLogger(__name__)

_SIM_DEFAULT_DRIVE = "/dev/sr0" if sys.platform != "win32" else "E:"


class SimulationService:
    """Handles all simulation methods for E2E testing."""

    def __init__(
        self,
        event_broadcaster: EventBroadcaster,
        state_machine: JobStateMachine,
    ) -> None:
        self._broadcaster = event_broadcaster
        self._state_machine = state_machine

        # Cross-coordinator references (set by JobManager)
        self._subtitle_ready: dict = None
        self._subtitle_tasks: dict = None
        self._active_jobs: dict = None
        self._on_task_done: callable = None

    def set_callbacks(
        self,
        *,
        subtitle_ready,
        subtitle_tasks,
        active_jobs,
        on_task_done,
    ) -> None:
        """Set cross-coordinator references."""
        self._subtitle_ready = subtitle_ready
        self._subtitle_tasks = subtitle_tasks
        self._active_jobs = active_jobs
        self._on_task_done = on_task_done

    async def simulate_disc_insert(self, params: dict) -> int:
        """Simulate a disc insertion for testing purposes."""
        from app.services.config_service import get_config as get_sim_config

        drive_id = params.get("drive_id", _SIM_DEFAULT_DRIVE)
        volume_label = params.get("volume_label", "SIMULATED_DISC")
        content_type_str = params.get("content_type", "tv")

        # Parse detected_title
        default_title = volume_label.replace("_", " ").title()
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
                    {
                        "duration_seconds": 1320 + i * 60,
                        "file_size_bytes": 1024 * 1024 * 1024,
                    }
                    for i in range(8)
                ]
            else:
                title_params = [
                    {
                        "duration_seconds": 7200,
                        "file_size_bytes": 4 * 1024 * 1024 * 1024,
                    }
                ]

        async with async_session() as session:
            job = DiscJob(
                drive_id=drive_id,
                volume_label=volume_label,
                content_type=content_type,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
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
            await self._broadcaster.broadcast_drive_inserted(drive_id, volume_label)

            # Broadcast identifying
            await ws_manager.broadcast_job_update(
                job.id,
                JobState.IDENTIFYING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
                total_titles=len(title_params),
            )

            await asyncio.sleep(0.5)

            # Broadcast titles discovered
            title_list = build_title_list(titles)
            await ws_manager.broadcast_titles_discovered(
                job.id,
                title_list,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
            )

            # Start subtitle download for TV content
            if content_type == ContentType.TV and detected_title and detected_season:
                self._subtitle_ready[job.id] = asyncio.Event()
                self._subtitle_tasks[job.id] = asyncio.create_task(
                    self._simulate_subtitle_download(job.id, len(title_params), detected_title)
                )
                logger.info(
                    f"Job {job.id}: starting simulated subtitle download for "
                    f"{detected_title} S{detected_season}"
                )

            force_review = params.get("force_review_needed", False)
            if force_review:
                job.state = JobState.REVIEW_NEEDED
                job.detected_title = None
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
                task = asyncio.create_task(
                    self._simulate_ripping(job.id, titles, rip_speed_multiplier, content_type)
                )
                task.add_done_callback(lambda t, jid=job.id: self._on_task_done(t, jid))
                self._active_jobs[job.id] = task
            else:
                job.state = JobState.RIPPING
                await session.commit()
                await ws_manager.broadcast_job_update(
                    job.id,
                    JobState.RIPPING.value,
                    content_type=content_type.value,
                    detected_title=detected_title,
                    detected_season=(detected_season if content_type == ContentType.TV else None),
                    total_titles=len(title_params),
                )

            return job.id

    async def simulate_disc_insert_realistic(self, params: dict) -> int:
        """Simulate disc insertion using real MKV files from staging."""
        drive_id = params.get("drive_id", _SIM_DEFAULT_DRIVE)
        volume_label = params.get("volume_label", "REAL_DATA_DISC")
        content_type = ContentType(params.get("content_type", "tv"))
        detected_title = params.get("detected_title")
        detected_season = params.get("detected_season", 1)
        title_params = params.get("titles", [])
        staging_path = params.get("staging_path")
        rip_speed_multiplier = params.get("rip_speed_multiplier", 1)

        async with async_session() as session:
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

            await self._broadcaster.broadcast_drive_inserted(drive_id, volume_label)

            await ws_manager.broadcast_job_update(
                job.id,
                JobState.IDENTIFYING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
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

            job.state = JobState.RIPPING
            job.total_titles = len(titles)
            await session.commit()
            await ws_manager.broadcast_job_update(
                job.id,
                JobState.RIPPING.value,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
                total_titles=len(titles),
            )

            for t in titles:
                await session.refresh(t)

            title_list = build_title_list(titles)
            await ws_manager.broadcast_titles_discovered(
                job.id,
                title_list,
                content_type=content_type.value,
                detected_title=detected_title,
                detected_season=(detected_season if content_type == ContentType.TV else None),
            )

            # Start subtitle download for TV content
            if content_type == ContentType.TV and detected_title and detected_season:
                self._subtitle_ready[job.id] = asyncio.Event()
                self._subtitle_tasks[job.id] = asyncio.create_task(
                    self._simulate_subtitle_download(job.id, len(title_params), detected_title)
                )
                logger.info(
                    f"Job {job.id}: starting simulated subtitle download for "
                    f"{detected_title} S{detected_season}"
                )

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
        """Simulate ripping with configurable speed per track."""
        async with async_session() as session:
            for i, title in enumerate(titles):
                logger.info(f"[SIMULATE] Job {job_id}: starting realistic rip of title {i}")

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

                base_steps = 20
                steps = max(4, base_steps // max(1, speed_multiplier))
                sleep_time = 0.5 / max(1, speed_multiplier)
                step_size = title_bytes / steps if steps > 0 else 0
                for step in range(steps + 1):
                    await asyncio.sleep(sleep_time)

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

                    title_actual = int(step_size * step)
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title_db.id,
                        TitleState.RIPPING.value,
                        expected_size_bytes=title_bytes,
                        actual_size_bytes=min(title_actual, title_bytes),
                    )

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
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.MATCHING)
                await self._simulate_matching(job_id, titles, session)
            else:
                job.state = JobState.ORGANIZING
                await session.commit()
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.ORGANIZING)
                await asyncio.sleep(0.5)
                job.progress_percent = 100.0
                for title in titles:
                    title_db = await session.get(DiscTitle, title.id)
                    if title_db and title_db.state not in (
                        TitleState.COMPLETED,
                        TitleState.FAILED,
                    ):
                        title_db.state = TitleState.COMPLETED
                        session.add(title_db)
                await session.commit()
                await self._state_machine.transition_to_completed(job, session)

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

                    title_actual = int(step_size * (step + 1))
                    await ws_manager.broadcast_title_update(
                        job_id,
                        title.id,
                        TitleState.RIPPING.value,
                        expected_size_bytes=title_bytes,
                        actual_size_bytes=min(title_actual, title_bytes),
                    )

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
                if job_id in self._subtitle_ready:
                    logger.info(f"[SIMULATE] Job {job_id}: waiting for subtitle download...")
                    try:
                        await asyncio.wait_for(self._subtitle_ready[job_id].wait(), timeout=10)
                        logger.info(f"[SIMULATE] Job {job_id}: subtitle download complete")
                        await session.refresh(job)
                    except TimeoutError:
                        logger.warning(f"[SIMULATE] Job {job_id}: subtitle download timed out")

                job.state = JobState.MATCHING
                await session.commit()
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.MATCHING)
                await self._simulate_matching(job_id, titles, session)
            else:
                job.state = JobState.ORGANIZING
                await session.commit()
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.ORGANIZING)
                await asyncio.sleep(0.5)
                job.progress_percent = 100.0
                for title in titles:
                    title_db = await session.get(DiscTitle, title.id)
                    if title_db and title_db.state not in (
                        TitleState.COMPLETED,
                        TitleState.FAILED,
                    ):
                        title_db.state = TitleState.COMPLETED
                        session.add(title_db)
                await session.commit()
                await self._state_machine.transition_to_completed(job, session)

    async def _simulate_subtitle_download(self, job_id: int, total: int, show_name: str) -> None:
        """Simulate subtitle download events."""
        import random

        from sqlalchemy import update

        async with async_session() as session:
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(subtitle_status="downloading")
            )
            await session.commit()

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

        async with async_session() as session:
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(subtitle_status=status)
            )
            await session.commit()

        await ws_manager.broadcast_subtitle_event(
            job_id,
            status,
            downloaded=downloaded,
            total=total,
            failed_count=failed,
        )

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

        job = await session.get(DiscJob, job_id)
        subtitle_status = job.subtitle_status if job else None

        if subtitle_status == "failed":
            logger.error(
                f"[SIMULATE] Job {job_id}: BLOCKING matching - subtitle download failed. "
                f"Marking all titles as FAILED."
            )
            for title in titles:
                title_db = await session.get(DiscTitle, title.id)
                if title_db:
                    title_db.state = TitleState.FAILED
                    title_db.match_confidence = 0.0
                    title_db.match_details = json.dumps(
                        {
                            "error": "subtitle_download_failed",
                            "message": (
                                job.error_message
                                or "Subtitle download failed, cannot match without reference files"
                            ),
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
            await self._state_machine.transition_to_failed(
                job,
                session,
                error_message="Subtitle download failed - cannot proceed with matching",
            )
            return

        logger.info(
            f"[SIMULATE] Job {job_id}: subtitle status '{subtitle_status}', "
            f"proceeding with matching simulation"
        )

        needs_review = False
        for i, title in enumerate(titles):
            title_db = await session.get(DiscTitle, title.id)
            if not title_db:
                continue

            confidence = random.uniform(0.7, 1.0)
            season = 1
            job = await session.get(DiscJob, job_id)
            if job and job.detected_season:
                season = job.detected_season

            episode_code = f"S{season:02d}E{(i + 1):02d}"

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

            target_votes = 4
            for vote_round in range(target_votes):
                progress = ((vote_round + 1) / target_votes) * 100.0
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
            await self._state_machine.transition_to_review(
                job,
                session,
                reason="Some episodes have low confidence matches",
                broadcast=False,
            )
            await ws_manager.broadcast_job_update(job_id, job.state.value, progress=0)
        else:
            job.progress_percent = 100.0
            result = await self._state_machine.transition_to_completed(job, session)
            logger.info(
                f"[SIMULATE] Job {job_id}: transition_to_completed returned {result}, "
                f"job.state={job.state.value}"
            )
