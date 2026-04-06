"""Matching Coordinator - Episode matching, subtitle download, and DiscDB assignment.

Extracted from JobManager to isolate matching concerns.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.curator import curator as episode_curator
from app.core.errors import MatchingError
from app.database import async_session
from app.models import DiscJob
from app.models.disc_job import DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine

logger = logging.getLogger(__name__)


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

        # Cross-coordinator callback
        self._check_job_completion: callable = None

    def set_callbacks(self, *, check_job_completion) -> None:
        """Set cross-coordinator callbacks."""
        self._check_job_completion = check_job_completion

    def init_semaphore(self, concurrency: int) -> None:
        """Initialize the match semaphore with the given concurrency."""
        self._match_semaphore = asyncio.Semaphore(concurrency)

    async def clear_job_caches(self, job_id: int, state) -> None:
        """Clear per-job caches to prevent memory leaks. Called on terminal states."""
        self._episode_runtimes.pop(job_id, None)
        self._discdb_mappings.pop(job_id, None)

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
        title.match_details = f'{{"source": "discdb", "episode_title": "{mapping.episode_title}"}}'
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

    async def match_single_file(self, job_id: int, title_id: int, file_path: Path) -> None:
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

        # 2. Wait for the file to be fully written before matching
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

        # 1c. Duration pre-filter
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
                    logger.warning(
                        f"[MATCH] Title {title_id} (Job {job_id}): duration pre-filter failed: {e}. "
                        f"Proceeding with matching normally."
                    )

        # 3. Acquire semaphore to limit concurrent matching
        if self._match_semaphore is not None:
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): waiting for match semaphore...")
            await self._match_semaphore.acquire()
            logger.info(f"[MATCH] Title {title_id} (Job {job_id}): acquired match semaphore")

        # 4. Transition title to MATCHING
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
            raise
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
                loop = asyncio.get_running_loop()
                _json_dumps = json.dumps

                def on_progress(stage: str, percent: float, vote_data: list | None = None):
                    try:
                        details = None
                        if vote_data:
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

                # Run the episode matcher
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
                        title.state = TitleState.MATCHED
                    else:
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

            downloaded = sum(
                1 for ep in result["episodes"] if ep["status"] in ("downloaded", "cached")
            )
            failed = sum(1 for ep in result["episodes"] if ep["status"] in ("not_found", "failed"))
            total = len(result["episodes"])

            status = "completed" if failed == 0 else ("partial" if downloaded > 0 else "failed")

            error_msg = None
            if status == "failed":
                error_msg = "Subtitle download failed: No subtitles found"

            logger.info(
                f"Subtitle download complete for {show_name} S{season}: "
                f"{status} ({downloaded} downloaded/cached, {failed} failed)"
            )

            async with async_session() as session:
                update_values = {"subtitle_status": status}

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
                job_id,
                status,
                downloaded=downloaded,
                total=total,
                failed_count=failed,
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
                    .values(
                        subtitle_status="failed",
                        error_message=f"Download error: {str(e)}",
                    )
                )
                await session.commit()
            await ws_manager.broadcast_subtitle_event(job_id, "failed")

        finally:
            if job_id in self._subtitle_ready:
                self._subtitle_ready[job_id].set()
