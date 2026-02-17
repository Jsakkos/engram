"""Ripping coordination for MakeMKV operations.

Coordinates the ripping process including:
- Title selection and progress tracking
- File stability monitoring
- Title completion callbacks
- Backfilling unmatched files
"""

import asyncio
import logging
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websocket import ConnectionManager
from app.core.extractor import MakeMKVExtractor, RipProgress
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine

logger = logging.getLogger(__name__)


class SpeedCalculator:
    """Calculates ripping speed and ETA."""

    def __init__(self, total_bytes: int):
        self.total_bytes = total_bytes
        self.speed_str = "—"
        self.eta_seconds: int | None = None
        self._start_time = time.monotonic()
        self._last_bytes = 0
        self._last_update = self._start_time

    def update(self, bytes_done: int):
        """Update speed calculation with current progress."""
        now = time.monotonic()
        elapsed = now - self._start_time

        if elapsed > 0:
            bytes_per_sec = bytes_done / elapsed
            if bytes_per_sec > 0:
                self.speed_str = f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"
                remaining = self.total_bytes - bytes_done
                self.eta_seconds = int(remaining / bytes_per_sec) if bytes_per_sec > 0 else None

        self._last_bytes = bytes_done
        self._last_update = now


class RippingCoordinator:
    """Coordinates MakeMKV ripping operations with progress tracking."""

    def __init__(
        self,
        extractor: MakeMKVExtractor,
        ws_manager: ConnectionManager,
        event_broadcaster: EventBroadcaster,
        state_machine: JobStateMachine,
        loop: asyncio.AbstractEventLoop,
    ):
        self._extractor = extractor
        self._ws = ws_manager
        self._broadcaster = event_broadcaster
        self._state_machine = state_machine
        self._loop = loop

    async def run_ripping(
        self, job: DiscJob, session: AsyncSession, movie_organizer=None
    ) -> None:
        """Execute the ripping process for a job.

        Args:
            job: Job to rip
            session: Database session
            movie_organizer: Optional movie organizer for single-movie flow
        """
        job_id = job.id

        # Calculate title count for initial update
        title_count = job.total_titles or 0

        await self._ws.broadcast_job_update(
            job_id,
            JobState.RIPPING.value,
            current_title=1,
            total_titles=title_count,
        )

        try:
            output_dir = Path(job.staging_path)

            # Fetch titles and calculate sizes
            titles_result = await session.execute(
                select(DiscTitle).where(DiscTitle.job_id == job_id)
            )
            disc_titles = titles_result.scalars().all()

            # Filter for selected titles if any selection exists
            has_selection = any(dt.is_selected for dt in disc_titles if dt.is_selected)
            titles_to_rip = (
                [dt for dt in disc_titles if dt.is_selected]
                if has_selection
                else disc_titles
            )

            # Calculate total size
            total_job_bytes = sum(t.file_size_bytes for t in titles_to_rip)

            # Sort titles by index for consistent mapping
            sorted_titles = sorted(titles_to_rip, key=lambda t: t.title_index)

            # Initialize speed calculator
            speed_calc = SpeedCalculator(total_job_bytes)

            # Create progress callback
            async def progress_callback(progress: RipProgress) -> None:
                current_idx = progress.current_title
                active_title_size = 0
                cumulative_previous = 0

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

                await self._ws.broadcast_job_update(
                    job_id,
                    JobState.RIPPING.value,
                    progress=global_percent,
                    speed=speed_calc.speed_str,
                    eta=speed_calc.eta_seconds,
                    current_title=progress.current_title,
                    total_titles=len(sorted_titles),
                )

            # Create title completion callback
            def on_title_complete(idx: int, path: Path):
                logger.info(
                    f"[CALLBACK] Title complete: idx={idx} path={path.name} "
                    f"(Job {job_id})"
                )
                future = asyncio.run_coroutine_threadsafe(
                    self._on_title_ripped(job_id, idx, path, sorted_titles, session),
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
                            f"[CALLBACK] _on_title_ripped failed for {path.name} "
                            f"(Job {job_id}): {e}"
                        )

                future.add_done_callback(_check_result)

            # Determine indices to pass to extractor
            rip_indices = [t.title_index for t in sorted_titles]
            if len(rip_indices) == len(disc_titles):
                rip_indices = None  # Rip all (faster)

            # Run extraction
            result = await self._extractor.rip_titles(
                job.drive_id,
                output_dir,
                title_indices=rip_indices,
                progress_callback=lambda p: asyncio.create_task(progress_callback(p)),
                title_complete_callback=on_title_complete,
            )

            if not result.success:
                await self._state_machine.transition_to_failed(
                    job, session, error_message=result.error_message
                )
                return

            # Eject disc now that ripping is complete
            await self._eject_disc(job.drive_id)

            # Handle TV vs Movie workflows
            if job.content_type == ContentType.TV:
                await self._handle_tv_completion(job_id, job, session, sorted_titles)
            else:
                await self._handle_movie_completion(
                    job_id, job, session, disc_titles, sorted_titles, movie_organizer
                )

        except asyncio.CancelledError:
            logger.info(f"Job {job_id} was cancelled")
            await self._state_machine.transition_to_failed(
                job, session, error_message="Cancelled by user"
            )
        except Exception as e:
            logger.exception(f"Error ripping job {job_id}")
            await self._state_machine.transition_to_failed(
                job, session, error_message=str(e)
            )

    async def _eject_disc(self, drive_id: str) -> None:
        """Eject disc from drive."""
        try:
            from app.core.sentinel import eject_disc

            await asyncio.to_thread(eject_disc, drive_id)
        except (OSError, RuntimeError) as e:
            logger.warning(f"Could not eject disc from {drive_id}: {e}")

    async def _handle_tv_completion(
        self,
        job_id: int,
        job: DiscJob,
        session: AsyncSession,
        sorted_titles: list[DiscTitle],
    ) -> None:
        """Handle completion of TV disc ripping."""
        # Backfill any unmatched titles
        await self._backfill_unmatched_titles(
            job_id, Path(job.staging_path), sorted_titles, session
        )

        # Transition to matching state
        job.state = JobState.MATCHING
        await session.commit()
        await self._ws.broadcast_job_update(job_id, JobState.MATCHING.value)

    async def _handle_movie_completion(
        self,
        job_id: int,
        job: DiscJob,
        session: AsyncSession,
        disc_titles: list[DiscTitle],
        sorted_titles: list[DiscTitle],
        movie_organizer=None,
    ) -> None:
        """Handle completion of movie disc ripping."""
        # Check for multiple ripped versions (ambiguous movie workflow)
        ripped_titles = [t for t in disc_titles if t.is_selected]

        if len(ripped_titles) > 1:
            await self._state_machine.transition_to_review(
                job,
                session,
                reason="Multiple versions ripped. Please select the correct one.",
                broadcast=False,
            )
            await self._ws.broadcast_job_update(
                job_id,
                JobState.REVIEW_NEEDED.value,
                error="Multiple versions ripped. Please select the correct one.",
            )
            logger.info(
                f"Job {job_id}: Multiple movie versions ripped. Waiting for user selection."
            )
            return

        # Single movie flow - organize immediately
        job.state = JobState.ORGANIZING
        await session.commit()
        await self._ws.broadcast_job_update(job_id, JobState.ORGANIZING.value)

        if movie_organizer:
            # Run organizer
            organize_result = await asyncio.to_thread(
                movie_organizer.organize,
                Path(job.staging_path),
                job.volume_label,
                job.detected_title,
            )

            if organize_result["success"]:
                job.final_path = str(organize_result["main_file"])
                job.progress_percent = 100.0
                await self._state_machine.transition_to_completed(job, session)
                logger.info(f"Job {job_id} completed: {organize_result['main_file']}")
            else:
                await self._state_machine.transition_to_failed(
                    job, session, error_message=organize_result["error"]
                )

    async def _on_title_ripped(
        self,
        job_id: int,
        rip_index: int,
        path: Path,
        sorted_titles: list[DiscTitle],
        parent_session: AsyncSession,
    ) -> None:
        """Handle completion of a single title rip.

        Matches the ripped file to a DiscTitle using:
        1. Title index extracted from filename (e.g. B1_t03.mkv → index 3)
        2. Fallback: sequential rip_index mapped to sorted titles
        """
        # Use a new session since this is called from a thread
        from app.database import async_session

        async with async_session() as session:
            title = None

            # Try to extract title index from MakeMKV filename pattern
            idx_match = re.search(r"t(\d+)\.mkv$", path.name, re.IGNORECASE)
            if not idx_match:
                idx_match = re.search(r"title[_]?(\d+)\.mkv$", path.name, re.IGNORECASE)

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
                logger.warning(
                    f"Could not map ripped file {path.name} to any title (Job {job_id})"
                )
                return

            title.output_filename = str(path)
            title.state = TitleState.RIPPING  # File detected but may still be written
            session.add(title)
            await session.commit()
            await self._ws.broadcast_title_update(
                job_id,
                title.id,
                title.state.value,
                duration_seconds=title.duration_seconds,
                file_size_bytes=title.file_size_bytes,
            )

            logger.info(
                f"Title detected: {path.name} → title_index={title.title_index} "
                f"(Title {title.id}, Job {job_id}) — queuing for matching"
            )

    async def _backfill_unmatched_titles(
        self,
        job_id: int,
        staging_dir: Path,
        sorted_titles: list[DiscTitle],
        session: AsyncSession,
    ) -> None:
        """Scan staging dir for .mkv files not yet assigned to a title.

        This catches any files missed by the real-time filesystem polling.
        """
        # Get current title states
        result = await session.execute(
            select(DiscTitle).where(DiscTitle.job_id == job_id)
        )
        titles = result.scalars().all()
        assigned_indices = {
            t.title_index for t in titles if t.output_filename is not None
        }

        # Scan staging dir for .mkv files
        mkv_files = list(staging_dir.glob("*.mkv")) if staging_dir.exists() else []

        for mkv in mkv_files:
            # Extract title index from filename
            idx_match = re.search(r"t(\d+)\.mkv$", mkv.name, re.IGNORECASE)
            if not idx_match:
                idx_match = re.search(r"title[_]?(\d+)\.mkv$", mkv.name, re.IGNORECASE)
            if not idx_match:
                continue

            title_index = int(idx_match.group(1))
            if title_index in assigned_indices:
                continue  # Already handled by real-time callback

            logger.info(
                f"Backfill: found unmatched file {mkv.name} "
                f"(title_index={title_index}, Job {job_id})"
            )
            await self._on_title_ripped(job_id, 0, mkv, sorted_titles, session)

    async def wait_for_file_ready(
        self,
        file_path: Path,
        title_id: int,
        job_id: int,
        expected_size: int = 0,
        timeout: float | None = None,
    ) -> bool:
        """Wait until a ripped file is fully written and ready for processing.

        MakeMKV creates output files immediately but writes to them over minutes.
        We poll the file size and require it to be stable for several checks.

        Returns True if file is ready, False on timeout.
        """
        from app.services.config_service import get_config

        config = await get_config()
        check_interval = config.ripping_file_poll_interval
        required_stable = config.ripping_stability_checks
        if timeout is None:
            timeout = config.ripping_file_ready_timeout

        last_size = -1
        stable_count = 0
        start = time.monotonic()

        logger.info(
            f"[MATCH] Title {title_id} (Job {job_id}): waiting for file to finish "
            f"writing: {file_path.name} (expected ~{expected_size / 1024 / 1024:.0f} MB)"
        )

        while time.monotonic() - start < timeout:
            if not file_path.exists():
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file not yet on disk, "
                    f"waiting... ({file_path.name})"
                )
                await asyncio.sleep(check_interval)
                await self._ws.broadcast_title_update(
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
                    f"[MATCH] Title {title_id} (Job {job_id}): cannot stat file "
                    f"({e}), retrying..."
                )
                await asyncio.sleep(check_interval)
                continue

            if current_size > 0 and current_size == last_size:
                stable_count += 1
                logger.debug(
                    f"[MATCH] Title {title_id} (Job {job_id}): file size stable "
                    f"({current_size / 1024 / 1024:.0f} MB) — check "
                    f"{stable_count}/{required_stable}"
                )
                if stable_count >= required_stable:
                    logger.info(
                        f"[MATCH] Title {title_id} (Job {job_id}): file ready "
                        f"({current_size / 1024 / 1024:.0f} MB, stable for "
                        f"{stable_count * check_interval:.0f}s): {file_path.name}"
                    )
                    return True
            else:
                if stable_count > 0:
                    logger.debug(
                        f"[MATCH] Title {title_id} (Job {job_id}): file size changed "
                        f"({last_size} -> {current_size}), resetting stability counter"
                    )
                stable_count = 0

            last_size = current_size

            # Broadcast wait progress
            await self._ws.broadcast_title_update(
                job_id,
                title_id,
                TitleState.RIPPING.value,
                match_stage="waiting_for_file",
                match_progress=(stable_count / required_stable) * 100.0,
                expected_size_bytes=expected_size,
                actual_size_bytes=current_size,
            )

            await asyncio.sleep(check_interval)

        logger.error(
            f"[MATCH] Title {title_id} (Job {job_id}): timeout waiting for file to "
            f"finish writing: {file_path.name}"
        )
        return False
