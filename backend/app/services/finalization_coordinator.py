"""Finalization Coordinator - Conflict resolution, organization, and job completion.

Extracted from JobManager to isolate finalization concerns.
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine

logger = logging.getLogger(__name__)


class FinalizationCoordinator:
    """Coordinates conflict resolution, file organization, and job completion."""

    def __init__(
        self,
        event_broadcaster: EventBroadcaster,
        state_machine: JobStateMachine,
    ) -> None:
        self._broadcaster = event_broadcaster
        self._state_machine = state_machine

        # Cross-coordinator callbacks (set by JobManager)
        self._run_ripping: callable = None
        self._on_task_done: callable = None
        self._active_jobs: dict = None
        self._match_single_file: callable = None

    def set_callbacks(
        self, *, run_ripping, on_task_done, active_jobs, match_single_file=None
    ) -> None:
        """Set cross-coordinator callbacks."""
        self._run_ripping = run_ripping
        self._on_task_done = on_task_done
        self._active_jobs = active_jobs
        self._match_single_file = match_single_file

    async def check_job_completion(self, session, job_id: int):
        """Check if all titles in a job are processed, and if so, finalize."""
        session.expire_all()

        job = await session.get(DiscJob, job_id)
        if not job:
            return

        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
        result = await session.execute(statement)
        titles = result.scalars().all()

        active_states = [TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHING]
        active_titles = [t for t in titles if t.state in active_states]

        # Diagnostic: log ALL title states every time this is called
        state_summary = ", ".join(
            f"t{t.title_index}={t.state.value}" for t in sorted(titles, key=lambda x: x.title_index)
        )
        logger.info(
            f"[COMPLETION-CHECK] Job {job_id}: {len(active_titles)} active / "
            f"{len(titles)} total — [{state_summary}]"
        )

        if active_titles:
            logger.debug(
                f"Job {job_id}: {len(active_titles)} still active "
                f"({', '.join(f'{t.id}:{t.state.value}' for t in active_titles[:5])})"
            )
            return

        # All titles are terminal
        logger.info(f"All titles for job {job_id} effectively processed. Finalizing...")

        has_matched = any(t.state == TitleState.MATCHED for t in titles)
        has_review = any(t.state == TitleState.REVIEW for t in titles)
        has_completed = any(t.state == TitleState.COMPLETED for t in titles)
        all_failed = all(t.state == TitleState.FAILED for t in titles)

        if has_matched:
            try:
                await self.finalize_disc_job(job_id)
            except Exception as e:
                logger.exception(f"Job {job_id}: _finalize_disc_job failed: {e}")
                await self._state_machine.transition_to_failed(
                    job, session, error_message=f"Finalization failed: {e}"
                )
        elif has_review:
            await self._state_machine.transition_to_review(
                job,
                session,
                reason=f"{sum(1 for t in titles if t.state == TitleState.REVIEW)} title(s) need manual episode assignment",
            )
        elif all_failed and not has_completed:
            await self._state_machine.transition_to_failed(
                job, session, error_message="All titles failed to process"
            )
        else:
            job.progress_percent = 100.0
            await self._state_machine.transition_to_completed(job, session)

    async def finalize_disc_job(self, job_id: int):
        """Run conflict resolution with cascading reassignment and organize matches."""
        from app.core.organizer import tv_organizer

        logger.info(f"Running conflict resolution for Job {job_id}")

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
            titles = (await session.execute(statement)).scalars().all()

            def _find_source_file(title):
                if title.output_filename:
                    p = Path(title.output_filename)
                    if p.exists():
                        return p
                staging_path = Path(job.staging_path)
                matches = list(staging_path.glob(f"*_t{title.title_index:02d}.mkv"))
                return matches[0] if matches else None

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
                if score == 0.0:
                    score = t.match_confidence
                return score, vote_count, file_cov, runner_ups

            # Iterative conflict resolution (max 3 rounds)
            for round_num in range(3):
                candidates = {}
                for t in titles:
                    if t.state == TitleState.MATCHED and t.matched_episode:
                        candidates.setdefault(t.matched_episode, []).append(t)

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
                        key=lambda x: (
                            x["vote_count"],
                            x["score"],
                            x["file_coverage"],
                        ),
                        reverse=True,
                    )

                    winner = ranked[0]
                    logger.info(
                        f"  Winner: Title {winner['title'].id} "
                        f"(votes={winner['vote_count']}, score={winner['score']:.3f})"
                    )

                    for cand in ranked[1:]:
                        loser = cand["title"]
                        reassigned = False

                        for ru in cand["runner_ups"]:
                            alt_ep = ru["episode"]
                            current_claimants = candidates.get(alt_ep, [])
                            if not current_claimants:
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
                                        f"(beats claimant {claimant.id}: "
                                        f"{ru['score']:.3f} > {claimant_score:.3f})"
                                    )
                                    break

                        if not reassigned:
                            loser.state = TitleState.REVIEW
                            if loser.match_details:
                                try:
                                    details = json.loads(loser.match_details)
                                    details["conflict_reason"] = (
                                        f"Lost conflict for {ep_code}, no viable runner-up"
                                    )
                                    loser.match_details = json.dumps(details)
                                except (
                                    json.JSONDecodeError,
                                    KeyError,
                                    TypeError,
                                ) as e:
                                    logger.debug(f"Could not update conflict details: {e}")
                            session.add(loser)
                            logger.info(
                                f"  Title {loser.id}: no viable alternative, marked for REVIEW"
                            )

                if not reassigned_any:
                    break

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
                    t.organized_from = source_file.name
                    t.organized_to = (
                        str(org_result.get("final_path")) if org_result.get("final_path") else None
                    )
                    t.is_extra = False
                else:
                    t.state = TitleState.REVIEW
                    logger.error(f"Organize failed for Title {t.id}: {org_result['error']}")

                session.add(t)

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

            await session.commit()

            # Determine final job state
            has_review = any(t.state == TitleState.REVIEW for t in titles)
            has_completed = any(t.state == TitleState.COMPLETED for t in titles)

            if has_review:
                review_count = sum(1 for t in titles if t.state == TitleState.REVIEW)
                await self._state_machine.transition_to_review(
                    job,
                    session,
                    reason=f"{review_count} title(s) need manual episode assignment",
                )
            elif has_completed:
                job.progress_percent = 100.0
                from app.services.config_service import get_config as get_db_config

                db_config = await get_db_config()
                job.final_path = str(
                    Path(db_config.library_tv_path) / (job.detected_title or job.volume_label)
                )
                await self._state_machine.transition_to_completed(job, session)
            else:
                job.progress_percent = 100.0
                await self._state_machine.transition_to_completed(job, session)

    async def apply_review(
        self,
        job_id: int,
        title_id: int,
        episode_code: str | None = None,
        edition: str | None = None,
    ) -> None:
        """Apply a user's review decision for a title."""
        from datetime import UTC, datetime

        from app.core.organizer import movie_organizer, organize_tv_extras, tv_organizer

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")

            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError("Title not found for this job")

            if episode_code:
                title.matched_episode = episode_code
                if episode_code == "extra":
                    title.is_extra = True

            if edition:
                title.edition = edition

            title.match_confidence = 1.0  # User-confirmed

            # Discard: mark title as failed for both movie and TV
            if episode_code == "skip":
                title.state = TitleState.FAILED
                session.add(title)
                await session.commit()
                # For movies, we're done. For TV, fall through to check
                # if all titles are now resolved and trigger organizing.
                if job.content_type == ContentType.MOVIE:
                    return
            else:
                session.add(title)
                await session.commit()

            # Handle Movie Workflow
            if job.content_type == ContentType.MOVIE:
                if title.output_filename:
                    source_file = Path(title.output_filename)
                    if not source_file.exists():
                        logger.info(
                            f"Source file {source_file} not found. "
                            f"Triggering ripping for selected title."
                        )

                        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
                        all_titles = (await session.execute(statement)).scalars().all()
                        for t in all_titles:
                            t.is_selected = t.id == title.id
                            session.add(t)

                        await session.commit()

                        job.state = JobState.RIPPING
                        job.updated_at = datetime.now(UTC)
                        session.add(job)
                        await session.commit()
                        await self._broadcaster.broadcast_job_state_changed(job_id, job.state)

                        task = asyncio.create_task(self._run_ripping(job_id))
                        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
                        self._active_jobs[job_id] = task
                        return

                    else:
                        # File exists — Post-Rip workflow
                        job.state = JobState.ORGANIZING
                        await session.commit()
                        await self._broadcaster.broadcast_job_state_changed(job_id, job.state)

                        # Clean up unselected ripped files
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
                                    f"Failed to delete unselected file "
                                    f"{unselected.output_filename}: {e}"
                                )

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
                            await self._state_machine.transition_to_completed(job, session)
                            logger.info(f"Organized movie: {org_result['main_file']}")
                        elif org_result.get("error_code") == "FILE_EXISTS":
                            title.state = TitleState.REVIEW
                            try:
                                existing_details = (
                                    json.loads(title.match_details) if title.match_details else {}
                                )
                                existing_details.update(
                                    {
                                        "error": "file_exists",
                                        "message": str(org_result["error"]),
                                    }
                                )
                                title.match_details = json.dumps(existing_details)
                            except (json.JSONDecodeError, TypeError):
                                title.match_details = json.dumps(
                                    {
                                        "error": "file_exists",
                                        "message": str(org_result["error"]),
                                    }
                                )

                            await self._state_machine.transition_to_review(
                                job,
                                session,
                                reason="File already exists in library",
                            )
                            logger.warning(
                                f"Organization conflict for movie: {org_result['error']}"
                            )
                        else:
                            title.state = TitleState.FAILED
                            logger.error(f"Failed to organize movie: {org_result['error']}")
                            await self._state_machine.transition_to_failed(
                                job,
                                session,
                                error_message=org_result["error"],
                            )

                return

            # Handle TV Workflow — check for unresolved titles
            # Exclude already-completed and failed titles (they don't need review)
            result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.matched_episode.is_(None),
                    DiscTitle.state.notin_([TitleState.COMPLETED, TitleState.FAILED]),
                )
            )
            unresolved = result.scalars().all()

            if not unresolved:
                job.state = JobState.ORGANIZING
                session.add(job)
                await session.commit()
                await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

                all_titles_result = await session.execute(
                    select(DiscTitle).where(
                        DiscTitle.job_id == job_id,
                        DiscTitle.matched_episode.isnot(None),
                    )
                )
                resolved_titles = all_titles_result.scalars().all()

                success_count = 0
                conflict_count = 0

                extra_index = 1
                for disc_title in resolved_titles:
                    if disc_title.output_filename and disc_title.matched_episode != "skip":
                        # Skip already-organized titles
                        if disc_title.state == TitleState.COMPLETED and disc_title.organized_to:
                            success_count += 1
                            continue

                        source_file = Path(disc_title.output_filename)
                        if source_file.exists():
                            if disc_title.matched_episode == "extra":
                                org_result = await asyncio.to_thread(
                                    organize_tv_extras,
                                    source_file,
                                    job.detected_title or job.volume_label,
                                    job.detected_season or 1,
                                    disc_number=job.disc_number or 1,
                                    extra_index=extra_index,
                                    title_index=disc_title.title_index,
                                )
                                extra_index += 1
                            else:
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
                                    str(org_result.get("final_path"))
                                    if org_result.get("final_path")
                                    else None
                                )
                                disc_title.is_extra = disc_title.matched_episode == "extra"
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
                    await self._state_machine.transition_to_review(
                        job,
                        session,
                        reason=f"{conflict_count} files already exist in library",
                    )
                elif success_count > 0:
                    job.progress_percent = 100.0
                    job.error_message = None
                    from app.services.config_service import (
                        get_config as get_db_config,
                    )

                    db_config = await get_db_config()
                    job.final_path = str(
                        Path(db_config.library_tv_path) / (job.detected_title or job.volume_label)
                    )
                    await self._state_machine.transition_to_completed(job, session)
                else:
                    await self._state_machine.transition_to_failed(
                        job,
                        session,
                        error_message="Failed to organize files",
                    )
            else:
                await session.commit()

    async def process_matched_titles(self, job_id: int) -> dict:
        """Process all matched titles for a job without waiting for unresolved ones."""
        from app.core.organizer import organize_tv_extras, tv_organizer
        from app.services.config_service import get_config as get_db_config

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")

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
            extra_index = 1

            for disc_title in matched_titles:
                if not disc_title.output_filename:
                    continue

                source_file = Path(disc_title.output_filename)
                if not source_file.exists():
                    logger.warning(f"Source file not found: {source_file}")
                    continue

                if disc_title.matched_episode == "extra":
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=disc_title.title_index,
                    )
                    extra_index += 1
                else:
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
                await self._state_machine.transition_to_completed(job, session)
            else:
                await session.commit()

        return {
            "organized": success_count,
            "conflicts": conflict_count,
            "unresolved": len(unresolved),
        }
