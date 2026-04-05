"""Identification Coordinator - Disc scanning, classification, and DiscDB/TMDB lookup.

Extracted from JobManager to isolate the disc identification pipeline.
"""

import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.analyst import DiscAnalyst
from app.core.extractor import MakeMKVExtractor, ScanTimeoutError
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import build_title_list

logger = logging.getLogger(__name__)


class IdentificationCoordinator:
    """Coordinates disc identification: scanning, classification, and metadata lookup."""

    def __init__(
        self,
        analyst: DiscAnalyst,
        extractor: MakeMKVExtractor,
        event_broadcaster: EventBroadcaster,
        state_machine: JobStateMachine,
    ) -> None:
        self._analyst = analyst
        self._extractor = extractor
        self._broadcaster = event_broadcaster
        self._state_machine = state_machine

        # These will be set by JobManager after MatchingCoordinator is created
        self._get_discdb_mappings: callable = None
        self._set_discdb_mappings: callable = None
        self._start_subtitle_download: callable = None
        self._try_discdb_assignment: callable = None
        self._match_single_file: callable = None
        self._on_match_task_done: callable = None
        self._check_job_completion: callable = None
        self._run_ripping: callable = None
        self._finalize_disc_job: callable = None

    def set_callbacks(
        self,
        *,
        get_discdb_mappings,
        set_discdb_mappings,
        start_subtitle_download,
        try_discdb_assignment,
        match_single_file,
        on_match_task_done,
        check_job_completion,
        run_ripping,
        finalize_disc_job,
    ) -> None:
        """Set cross-coordinator callbacks after all coordinators are constructed."""
        self._get_discdb_mappings = get_discdb_mappings
        self._set_discdb_mappings = set_discdb_mappings
        self._start_subtitle_download = start_subtitle_download
        self._try_discdb_assignment = try_discdb_assignment
        self._match_single_file = match_single_file
        self._on_match_task_done = on_match_task_done
        self._check_job_completion = check_job_completion
        self._run_ripping = run_ripping
        self._finalize_disc_job = finalize_disc_job

    async def identify_disc(self, job_id: int) -> None:
        """Identify the disc contents using MakeMKV and the Analyst."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            try:
                # Scan disc with MakeMKV
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.IDENTIFYING)

                try:
                    from app.core.discdb_exporter import get_makemkv_log_dir

                    titles = await self._extractor.scan_disc(
                        job.drive_id, log_dir=get_makemkv_log_dir(job_id), job_id=job_id
                    )
                except ScanTimeoutError:
                    await self._state_machine.transition_to_failed(
                        job,
                        session,
                        "Disc scan timed out after 10 minutes — disc may be encrypted or damaged",
                    )
                    return

                if not titles:
                    await self._state_machine.transition_to_failed(
                        job, session, "No titles found on disc"
                    )
                    return

                # Run classification pipeline
                analysis = await self._run_classification(job, job_id, titles, session)

                logger.info(f"Job {job_id} Analysis Result: {analysis}")

                # Save snapshot for debugging and test fixture generation
                from app.core.snapshot import save_snapshot

                save_snapshot(job.volume_label, analysis)

                # Update job with analysis results
                job.content_type = analysis.content_type
                job.detected_title = analysis.detected_name
                job.detected_season = analysis.detected_season
                job.total_titles = len(titles)
                job.updated_at = datetime.now(UTC)

                # If TMDB and DiscDB both failed and label looks like a catalog
                # number, clear detected_title so the NamePromptModal triggers.
                discdb_signal = getattr(analysis, "_discdb_signal", None)
                tmdb_signal = getattr(analysis, "_tmdb_signal", None)
                if (
                    not tmdb_signal
                    and not discdb_signal
                    and job.detected_title
                    and DiscAnalyst._looks_like_catalog_number(job.volume_label)
                ):
                    logger.info(
                        f"Job {job_id}: Label '{job.volume_label}' looks like a catalog "
                        f"number and no external match found — clearing detected_title "
                        f"to trigger name prompt"
                    )
                    job.detected_title = None

                # Persist classification metadata
                job.classification_confidence = analysis.confidence
                job.classification_source = analysis.classification_source
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                job.is_ambiguous_movie = analysis.is_ambiguous_movie
                if analysis.play_all_title_indices:
                    job.play_all_indices_json = json.dumps(analysis.play_all_title_indices)

                # Extract disc number from volume label
                disc_match = re.search(r"d(?:isc)?[_\s]*(\d+)", job.volume_label, re.IGNORECASE)
                if disc_match:
                    job.disc_number = int(disc_match.group(1))
                    logger.info(
                        f"Detected disc number: {job.disc_number} from volume label: {job.volume_label}"
                    )
                else:
                    job.disc_number = 1
                    logger.info("No disc number detected in volume label, defaulting to 1")

                # Clear any existing titles for this job
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
                        source_filename=title.source_filename or None,
                        segment_count=title.segment_count,
                        segment_map=title.segment_map or None,
                    )
                    session.add(disc_title)

                # For TV discs, deselect "Play All" concatenation titles
                if analysis.content_type == ContentType.TV and analysis.play_all_title_indices:
                    await session.flush()
                    play_all_set = set(analysis.play_all_title_indices)
                    stmt = select(DiscTitle).where(DiscTitle.job_id == job_id)
                    db_titles_for_filter = (await session.execute(stmt)).scalars().all()

                    deselected = 0
                    for dt in db_titles_for_filter:
                        if dt.title_index in play_all_set:
                            dt.is_selected = False
                            dt.state = TitleState.COMPLETED
                            dt.is_extra = True
                            dt.match_details = json.dumps(
                                {"reason": "Play All concatenation title"}
                            )
                            deselected += 1
                            logger.info(
                                f"Job {job_id}: Deselected 'Play All' title {dt.title_index} "
                                f"({dt.duration_seconds // 60}min) → COMPLETED/extra"
                            )

                    if deselected:
                        logger.info(f"Job {job_id}: Deselected {deselected} 'Play All' title(s)")

                # For movies with DiscDB mappings, tag extras
                discdb_maps = self._get_discdb_mappings(job_id)
                if analysis.content_type == ContentType.MOVIE and discdb_maps:
                    main_indices = {m.index for m in discdb_maps if m.title_type == "MainMovie"}
                    extra_indices = {
                        m.index
                        for m in discdb_maps
                        if m.title_type in ("Extra", "") and m.index not in main_indices
                    }
                    if main_indices:
                        await session.flush()
                        stmt = select(DiscTitle).where(DiscTitle.job_id == job_id)
                        db_titles_for_select = (await session.execute(stmt)).scalars().all()
                        for dt in db_titles_for_select:
                            if dt.title_index in extra_indices:
                                dt.is_extra = True
                            elif dt.title_index not in main_indices:
                                dt.is_extra = True
                            session.add(dt)
                        logger.info(
                            f"Job {job_id}: TheDiscDB tagged MainMovie={main_indices}, "
                            f"extras={extra_indices}"
                        )

                # Broadcast titles discovered with full metadata
                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                title_list = build_title_list(
                    titles_result.scalars().all(), include_video_resolution=True
                )
                await ws_manager.broadcast_titles_discovered(
                    job_id,
                    title_list,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                )

                # If no title could be determined, ask user
                if not job.detected_title:
                    await self._state_machine.transition_to_review(
                        job,
                        session,
                        reason="Disc label unreadable. Please enter the title to continue.",
                        broadcast=False,
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=(job.content_type.value if job.content_type else None),
                        total_titles=job.total_titles,
                        review_reason="Disc label unreadable. Please enter the title to continue.",
                    )
                    logger.info(
                        f"Job {job_id}: no title detected (volume label: '{job.volume_label}'), "
                        f"waiting for user to supply name"
                    )
                    return

                # Start subtitle download for ALL TV content
                if (
                    job.content_type == ContentType.TV
                    and job.detected_title
                    and job.detected_season
                ):
                    self._start_subtitle_download(job_id, job.detected_title, job.detected_season)
                    logger.info(
                        f"Job {job_id}: starting subtitle download for "
                        f"{job.detected_title} S{job.detected_season}"
                    )

                if analysis.needs_review:
                    # Special handling for Ambiguous Movies
                    is_ambiguous_movie = (
                        job.content_type == ContentType.MOVIE and analysis.is_ambiguous_movie
                    )

                    if is_ambiguous_movie:
                        logger.info(
                            f"Job {job_id}: Ambiguous movie detected. "
                            f"Auto-ripping candidates for later review."
                        )
                        await session.commit()

                        statement = select(DiscTitle).where(DiscTitle.job_id == job_id)
                        db_titles = (await session.execute(statement)).scalars().all()

                        candidate_count = 0
                        for dt in db_titles:
                            if dt.duration_seconds and dt.duration_seconds >= 80 * 60:
                                dt.is_selected = True
                                candidate_count += 1
                                session.add(dt)

                        await session.commit()

                        job.state = JobState.RIPPING
                        await session.commit()
                        await ws_manager.broadcast_job_update(
                            job_id,
                            job.state.value,
                            content_type=job.content_type.value,
                            detected_title=job.detected_title,
                        )

                        await self._run_ripping(job_id)
                        return

                    await self._state_machine.transition_to_review(
                        job,
                        session,
                        reason=analysis.review_reason,
                        broadcast=False,
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

                    await self._run_ripping(job_id)
                    return

            except Exception as e:
                logger.exception(f"Error identifying disc for job {job_id}")
                await self._state_machine.transition_to_failed(job, session, str(e))

    async def identify_from_staging(self, job_id: int) -> None:
        """Identify and process pre-ripped MKV files from staging."""
        from app.core.analyst import TitleInfo

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return

            try:
                await self._broadcaster.broadcast_job_state_changed(job_id, JobState.IDENTIFYING)

                staging_dir = Path(job.staging_path)
                mkv_files = sorted(staging_dir.glob("*.mkv"))

                if not mkv_files:
                    await self._state_machine.transition_to_failed(
                        job, session, "No MKV files found in staging directory"
                    )
                    return

                # Build TitleInfo objects from MKV files using ffprobe
                titles: list[TitleInfo] = []
                for idx, mkv_file in enumerate(mkv_files):
                    duration = await self._probe_duration(mkv_file)
                    file_size = mkv_file.stat().st_size

                    titles.append(
                        TitleInfo(
                            index=idx,
                            duration_seconds=int(duration),
                            size_bytes=file_size,
                            chapter_count=0,
                            name=mkv_file.stem,
                        )
                    )

                # Run classification pipeline
                analysis = await self._run_classification(
                    job, job_id, titles, session, is_staging=True
                )

                # Apply user-provided hints (override classification if given)
                if job.content_type and job.content_type != ContentType.UNKNOWN:
                    analysis.content_type = job.content_type
                if job.detected_title:
                    analysis.detected_name = job.detected_title

                # Update job with analysis results
                job.content_type = analysis.content_type
                job.detected_title = analysis.detected_name or job.detected_title
                job.detected_season = analysis.detected_season or job.detected_season
                job.total_titles = len(titles)
                job.updated_at = datetime.utcnow()
                job.classification_confidence = analysis.confidence
                job.classification_source = analysis.classification_source or "staging_import"
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                job.is_ambiguous_movie = analysis.is_ambiguous_movie
                if analysis.play_all_title_indices:
                    job.play_all_indices_json = json.dumps(analysis.play_all_title_indices)

                # Extract disc number from volume label
                disc_match = re.search(r"d(?:isc)?[_\s]*(\d+)", job.volume_label, re.IGNORECASE)
                job.disc_number = int(disc_match.group(1)) if disc_match else 1

                # Create DiscTitle records with output_filename already set
                await session.execute(delete(DiscTitle).where(DiscTitle.job_id == job_id))

                for title, mkv_file in zip(titles, mkv_files, strict=True):
                    disc_title = DiscTitle(
                        job_id=job_id,
                        title_index=title.index,
                        duration_seconds=title.duration_seconds,
                        file_size_bytes=title.size_bytes,
                        chapter_count=title.chapter_count,
                        output_filename=str(mkv_file),
                    )
                    session.add(disc_title)

                await session.commit()

                # Broadcast titles discovered
                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                title_list = build_title_list(titles_result.scalars().all())
                await ws_manager.broadcast_titles_discovered(
                    job_id,
                    title_list,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                )

                # If no title detected, ask user
                if not job.detected_title:
                    await self._state_machine.transition_to_review(
                        job,
                        session,
                        reason="Could not determine title. Please enter the title to continue.",
                        broadcast=False,
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=(job.content_type.value if job.content_type else None),
                        total_titles=job.total_titles,
                        review_reason="Could not determine title. Please enter the title to continue.",
                    )
                    return

                # Skip ripping — files already exist. Proceed to matching/organization.
                if job.content_type == ContentType.TV:
                    # Start subtitle download
                    if job.detected_title and job.detected_season:
                        self._start_subtitle_download(
                            job_id, job.detected_title, job.detected_season
                        )

                    # Transition to MATCHING and kick off per-title matching
                    succeeded = await self._state_machine.transition(
                        job, JobState.MATCHING, session, broadcast=False
                    )
                    if succeeded:
                        await ws_manager.broadcast_job_update(job_id, JobState.MATCHING.value)

                    # Queue matching for each title
                    db_titles = (
                        (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                        .scalars()
                        .all()
                    )

                    for dt in db_titles:
                        dt.state = TitleState.MATCHING
                        session.add(dt)
                    await session.commit()

                    for dt in db_titles:
                        if dt.output_filename:
                            file_path = Path(dt.output_filename)
                            discdb_applied = await self._try_discdb_assignment(job_id, dt, session)
                            if not discdb_applied:
                                task = asyncio.create_task(
                                    self._match_single_file(job_id, dt.id, file_path)
                                )
                                task.add_done_callback(
                                    lambda t, jid=job_id, tid=dt.id: self._on_match_task_done(
                                        t, jid, tid
                                    )
                                )
                else:
                    # Movie: skip matching, go straight to organization
                    job.state = JobState.ORGANIZING
                    await session.commit()
                    await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

                    # Mark titles as MATCHED
                    db_titles = (
                        (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                        .scalars()
                        .all()
                    )

                    for dt in db_titles:
                        dt.state = TitleState.MATCHED
                        session.add(dt)
                    await session.commit()

                    # Run organization
                    await self._finalize_disc_job(job_id)

            except Exception as e:
                logger.exception(f"Error processing staging import for job {job_id}")
                async with async_session() as err_session:
                    err_job = await err_session.get(DiscJob, job_id)
                    if err_job:
                        await self._state_machine.transition_to_failed(err_job, err_session, str(e))

    async def _probe_duration(self, mkv_file: Path) -> float:
        """Get duration of an MKV file using ffprobe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mkv_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return float(stdout.decode().strip()) if stdout.decode().strip() else 1800
        except (TimeoutError, OSError, ValueError) as e:
            logger.debug(f"Could not determine MKV duration via ffprobe: {e}")
            return 1800  # Default 30 minutes

    async def set_name_and_resume(
        self,
        job_id: int,
        name: str,
        content_type_str: str,
        season: int | None = None,
    ) -> None:
        """Set a user-provided name for an unlabeled disc and resume ripping."""
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
            job.updated_at = datetime.now(UTC)
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

        return job_id  # Signal to JobManager to create the ripping task

    async def _run_classification(self, job, job_id, titles, session, is_staging=False):
        """Run the full classification pipeline (DiscDB, TMDB, AI, Analyst)."""
        from app.services.config_service import get_config_sync

        config = get_config_sync()

        # Attempt TheDiscDB lookup
        discdb_signal = None
        if config.discdb_enabled:
            try:
                from app.core.discdb_classifier import classify_from_discdb

                if is_staging:
                    content_hash = None
                else:
                    from app.core.extractor import compute_content_hash

                    content_hash = await asyncio.to_thread(compute_content_hash, job.drive_id)
                    if content_hash:
                        job.content_hash = content_hash

                discdb_signal = classify_from_discdb(
                    job.volume_label, titles, content_hash=content_hash
                )
                if discdb_signal:
                    logger.info(
                        f"Job {job_id}: TheDiscDB signal: "
                        f"{discdb_signal.content_type.value} "
                        f"({discdb_signal.confidence:.0%}) - "
                        f"{discdb_signal.matched_title}"
                        + (f" [{discdb_signal.source}]" if not is_staging else "")
                    )
                    if not is_staging:
                        job.discdb_slug = discdb_signal.matched_title.lower().replace(" ", "-")
                        job.discdb_disc_slug = discdb_signal.disc_slug
            except Exception as e:
                logger.warning(f"Job {job_id}: TheDiscDB lookup failed: {e}", exc_info=True)

        # Attempt TMDB lookup
        tmdb_signal = None
        detected_name, _, _ = DiscAnalyst._parse_volume_label(job.volume_label)
        if detected_name:
            try:
                from app.core.tmdb_classifier import classify_from_tmdb

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
                    f"Job {job_id}: TMDB lookup failed: {e}"
                    if is_staging
                    else f"Job {job_id}: TMDB lookup failed, using heuristics only: {e}"
                )

        # AI-powered identification fallback (not for staging)
        ai_identified_name = None
        if (
            not is_staging
            and not tmdb_signal
            and not (discdb_signal and discdb_signal.confidence >= 0.90)
            and config.ai_identification_enabled
            and config.ai_api_key
        ):
            try:
                from app.core.ai_identifier import identify_from_label

                logger.info(
                    f"Job {job_id}: TMDB lookup failed, trying AI identification "
                    f"for '{job.volume_label}'"
                )
                ai_result = await identify_from_label(
                    job.volume_label,
                    config.ai_provider,
                    config.ai_api_key,
                )
                if ai_result and ai_result.get("title"):
                    ai_identified_name = ai_result["title"]
                    logger.info(f"Job {job_id}: AI identified as '{ai_identified_name}'")
                    # Re-query TMDB with the AI-corrected name
                    if config.tmdb_api_key:
                        try:
                            from app.core.tmdb_classifier import classify_from_tmdb

                            tmdb_signal = classify_from_tmdb(
                                ai_identified_name, config.tmdb_api_key
                            )
                            if tmdb_signal:
                                logger.info(
                                    f"Job {job_id}: TMDB re-query with AI name: "
                                    f"{tmdb_signal.content_type.value} "
                                    f"({tmdb_signal.confidence:.0%}) - "
                                    f"{tmdb_signal.tmdb_name}"
                                )
                        except Exception as e:
                            logger.warning(f"Job {job_id}: TMDB re-query after AI failed: {e}")
            except Exception as e:
                logger.warning(f"Job {job_id}: AI identification failed: {e}", exc_info=True)

        # Analyze disc content
        analysis = self._analyst.analyze(titles, job.volume_label, tmdb_signal=tmdb_signal)

        # If AI identified a name but TMDB re-query also failed
        if ai_identified_name and not analysis.detected_name:
            analysis.detected_name = ai_identified_name
            analysis.classification_source = "ai"

        # If TheDiscDB returned a high-confidence match, override the analysis
        if discdb_signal and discdb_signal.confidence >= 0.90:
            if not is_staging:
                logger.info(
                    f"Job {job_id}: TheDiscDB override — "
                    f"{discdb_signal.content_type.value} "
                    f"({discdb_signal.confidence:.0%})"
                )
            analysis.content_type = discdb_signal.content_type
            analysis.confidence = discdb_signal.confidence
            analysis.classification_source = f"discdb_{discdb_signal.source}"
            analysis.detected_name = discdb_signal.matched_title
            analysis.needs_review = False
            if discdb_signal.tmdb_id:
                analysis.tmdb_id = discdb_signal.tmdb_id
            # Store title mappings for use during matching phase
            if discdb_signal.title_mappings:
                self._set_discdb_mappings(job_id, discdb_signal.title_mappings)
                # Persist to DB so mappings survive server restarts
                job.discdb_mappings_json = json.dumps(
                    [asdict(m) for m in discdb_signal.title_mappings]
                )
        elif discdb_signal:
            if not is_staging:
                logger.info(
                    f"Job {job_id}: TheDiscDB low-confidence signal "
                    f"({discdb_signal.confidence:.0%}), using as supplementary"
                )
            if not analysis.detected_name:
                analysis.detected_name = discdb_signal.matched_title

        # Stash signals on the analysis object for the caller to check
        # (used by identify_disc for catalog number detection)
        analysis._discdb_signal = discdb_signal
        analysis._tmdb_signal = tmdb_signal

        return analysis
