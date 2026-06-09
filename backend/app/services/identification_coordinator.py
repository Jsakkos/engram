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
from app.core.tmdb_classifier import TmdbSignal, classify_from_tmdb, should_flag_no_year
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import build_title_list

logger = logging.getLogger(__name__)


# Digit-boundary (not \b) so underscores/parens count as separators:
# FRASIER_2023 and FRASIER (2023) match; a longer number like 20231 does not.
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _label_has_year(*texts: str | None) -> bool:
    """True when any of the disc label/name strings contains a 19xx/20xx year.

    A year lets popularity+year disambiguate same-name twins, so the no-year
    proactive review flag is suppressed. Season-disc labels like ``FRASIER_S1D1``
    have no 4-digit year and return False.
    """
    return any(t and _YEAR_RE.search(t) for t in texts)


def _no_year_collision_reason(name, candidates) -> str:
    """Review reason for a no-year disc with same-name twins — lists every twin.

    Uses ``.get()`` so a candidate list deserialized from a DB value with a
    missing key degrades gracefully instead of raising (matches the access style
    in the finalization-side wrong-show helpers).
    """
    listed = "; ".join(
        f"{c.get('name', '?')} ({c.get('year') or '?'}, #{c.get('tmdb_id', '?')})"
        for c in (candidates or [])
    )
    return (
        f'"{name}" has multiple same-name shows on TMDB and the disc label has no '
        f"year to tell them apart: {listed}. Pick the correct one."
    )


def _candidates_json_from_signal(signal) -> str | None:
    """Serialize a TMDB signal's same-name twins for persistence on the job, else None.

    Records EVERY same-name twin (>=2) — e.g. Frasier 1993 #3452 + 2023 revival
    #195241 — regardless of whether the materiality gate flagged the job, so the
    downstream wrong-show detector can suggest the right one without re-querying TMDB.
    """
    cands = getattr(signal, "all_candidates", None) if signal else None
    return json.dumps(cands) if cands else None


def _resolve_show_year(tmdb_id: int | None, signal=None) -> int | None:
    """First-air year for a show, for library-folder disambiguation.

    No-network fast path: same-name candidates already carry a 'year' string
    (Frasier 1993 vs 2023). Universal fallback: cached TMDB details. Returns
    None when unknown — the organizer then degrades to an id-only/bare folder.
    Sync (blocking on the fallback) — call via ``asyncio.to_thread``.
    """
    # Falsy guard (not ``is None``) so a 0/empty id short-circuits instead of
    # making a pointless fetch_show_details(0) call for a non-existent id.
    if not tmdb_id:
        return None
    cands = getattr(signal, "all_candidates", None) if signal else None
    for c in cands or []:
        if c.get("tmdb_id") == tmdb_id:
            y = (c.get("year") or "").strip()
            if y.isdigit():
                return int(y)
    from app.matcher.tmdb_client import fetch_show_details

    details = fetch_show_details(tmdb_id)
    if details:
        fa = (details.get("first_air_date") or "")[:4]
        if fa.isdigit():
            return int(fa)
    return None


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
        self._start_subtitle_download_all_seasons: callable = None
        self._restart_subtitle_download: callable = None
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
        restart_subtitle_download,
        try_discdb_assignment,
        start_subtitle_download_all_seasons=None,
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
        self._start_subtitle_download_all_seasons = start_subtitle_download_all_seasons
        self._restart_subtitle_download = restart_subtitle_download
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

                    titles, disc_name = await self._extractor.scan_disc(
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
                analysis = await self._run_classification(
                    job, job_id, titles, session, disc_name=disc_name
                )

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
                job.candidates_json = _candidates_json_from_signal(tmdb_signal)
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, tmdb_signal
                )
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
                            # extra_indices is disjoint from main_indices, so any
                            # title not in main_indices is an extra.
                            dt.is_extra = dt.title_index not in main_indices
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

                # Compute ambiguous-identity flag before the TMDB-lookup-failed guard so
                # same-name collisions (which withhold tmdb_id by design) are not
                # intercepted with the generic "words merged" message.
                _signal = getattr(analysis, "_tmdb_signal", None)
                _amb = bool(_signal and _signal.ambiguous_identity)

                # No-year backstop (Frasier 1993 vs 2023): a label with no year can't
                # pick between same-name twins, so flag for review BEFORE ripping rather
                # than silently matching the popularity-best show's subtitles. The
                # popularity-best tmdb_id stays as the pre-selected guess; the user
                # confirms or switches via the existing re-identify UI.
                _noyear = bool(
                    _signal
                    and not _amb
                    and job.content_type == ContentType.TV
                    and should_flag_no_year(
                        _signal.all_candidates, _label_has_year(job.volume_label, disc_name)
                    )
                )
                if _noyear:
                    analysis.needs_review = True
                    analysis.review_reason = _no_year_collision_reason(
                        job.detected_title, _signal.all_candidates
                    )
                # Either form of same-name collision skips auto subtitle download and the
                # words-merged guard below.
                _collision = _amb or _noyear

                # TV show detected but TMDB lookup failed — name cannot be trusted for episode
                # matching. Block ripping until the user confirms the correct show name.
                # (Disc-name fallback already ran in _run_classification; if we reach here,
                # neither the volume label nor the DINFO name resolved on TMDB.)
                # Exclude collision jobs: they carry a candidate-naming review_reason that
                # the needs_review branch below will surface.
                if (
                    job.content_type == ContentType.TV
                    and not job.tmdb_id
                    and job.detected_title
                    and not _collision
                ):
                    reason = (
                        f'Could not find "{job.detected_title}" on TMDB — the disc label '
                        f"may have words merged without separators. "
                        f"Please enter the correct show title."
                    )
                    await self._state_machine.transition_to_review(
                        job,
                        session,
                        reason=reason,
                        broadcast=False,
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=job.content_type.value,
                        detected_title=job.detected_title,
                        detected_season=job.detected_season,
                        total_titles=job.total_titles,
                        review_reason=reason,
                    )
                    logger.info(
                        f"Job {job_id}: TMDB lookup failed for '{job.detected_title}' "
                        f"(volume label: '{job.volume_label}') — prompting user to supply correct show name"
                    )
                    return

                # Start subtitle download for ALL TV content — except when identity is
                # ambiguous (same-name collision) or a no-year twin needs disambiguation.
                # Downloading by the tentative name would fetch the wrong show's subtitles
                # before the user disambiguates.
                if job.content_type == ContentType.TV and job.detected_title and not _collision:
                    if job.detected_season is None:
                        if await self._gate_unknown_season_disc(job, session, job_id):
                            return
                    await self._start_tv_subtitle_prefetch(job)

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
                else:
                    # High-confidence detection - auto-start ripping
                    job.state = JobState.RIPPING
                    await session.commit()

                # Both review and high-confidence paths broadcast the same job update.
                # review_reason is None for the high-confidence path (omitted by the
                # broadcaster) and carries the candidate-naming reason for review jobs —
                # the ReIdentifyModal needs it to show why the disc needs disambiguation.
                await ws_manager.broadcast_job_update(
                    job_id,
                    job.state.value,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                    total_titles=job.total_titles,
                    review_reason=analysis.review_reason,
                )

                if analysis.needs_review:
                    logger.info(f"Job {job_id} needs review: {analysis.review_reason}")
                else:
                    logger.info(
                        f"Job {job_id} identified as {analysis.content_type.value} "
                        f"(confidence: {analysis.confidence:.1%}) - auto-starting rip"
                    )
                    await self._run_ripping(job_id)
                    return

            except Exception as e:
                logger.exception(f"Error identifying disc for job {job_id}")
                await self._state_machine.transition_to_failed(job, session, str(e))

    async def _gate_unknown_season_disc(self, job, session, job_id: int) -> bool:
        """Resolve the unknown-season fate of a TV disc (#370).

        Single-season show → auto-pin S1 and continue. Multi-season (or
        unresolvable) → park in REVIEW_NEEDED with the season prompt and
        return True so the caller stops before ripping.
        """
        # Disc label carried no season (box-set labels like
        # "Eureka D3"). A single-season show needs no prompt;
        # otherwise park the job for a season pick BEFORE
        # ripping — downstream, an unknown season used to skip
        # subtitle download entirely and dead-end every title
        # in review (#370). Resumes via set_name_and_resume.
        seasons = await self._resolve_all_season_numbers(job.detected_title, tmdb_id=job.tmdb_id)
        if len(seasons) == 1:
            job.detected_season = 1
            await session.commit()
            return False
        reason = (
            f"Identified as '{job.detected_title}' but the season "
            f"could not be detected from the disc label — select a "
            f"season to continue."
        )
        await self._state_machine.transition_to_review(job, session, reason=reason, broadcast=False)
        await ws_manager.broadcast_job_update(
            job_id,
            JobState.REVIEW_NEEDED.value,
            content_type=job.content_type.value,
            detected_title=job.detected_title,
            detected_season=None,
            total_titles=job.total_titles,
            review_reason=reason,
        )
        logger.info(
            f"Job {job_id}: season unknown for '{job.detected_title}', prompting user for season"
        )
        return True

    async def _resolve_all_season_numbers(
        self, title: str, tmdb_id: int | None = None
    ) -> list[int]:
        """Resolve 1..N season numbers for a show via TMDB (unknown season).

        Uses ``tmdb_id`` directly when the job already resolved it — re-resolving
        by name can pick the dominant same-name twin (#370). Returns an empty list
        when the show can't be resolved; callers then rely on the precomputed
        cache / already-downloaded references during matching.
        """
        try:
            from app.matcher.tmdb_client import fetch_show_id, get_number_of_seasons

            show_id = str(tmdb_id) if tmdb_id else await asyncio.to_thread(fetch_show_id, title)
            if not show_id:
                return []
            count = await asyncio.to_thread(get_number_of_seasons, show_id)
            if count and count > 0:
                return list(range(1, count + 1))
        except Exception as e:  # noqa: BLE001 — best-effort; fall back to cache at match time
            logger.debug(f"Could not resolve season count for '{title}': {e}")
        return []

    async def _start_tv_subtitle_prefetch(self, job) -> None:
        """Kick off the background reference-subtitle download for a TV job.

        Known season → that season only. Unknown season (the user chose "match
        across all seasons" in the season prompt, or a flat import folder) →
        prefetch EVERY season so matching can search across all of them instead
        of silently skipping the download and dead-ending in review (#370).
        """
        if job.detected_season:
            self._start_subtitle_download(
                job.id, job.detected_title, job.detected_season, job.tmdb_id
            )
            logger.info(
                f"Job {job.id}: starting subtitle download for "
                f"{job.detected_title} S{job.detected_season}"
            )
        elif self._start_subtitle_download_all_seasons:
            all_seasons = await self._resolve_all_season_numbers(
                job.detected_title, tmdb_id=job.tmdb_id
            )
            if all_seasons:
                logger.info(
                    f"Job {job.id}: season unknown for '{job.detected_title}'; "
                    f"prefetching subtitles for seasons {all_seasons}"
                )
                self._start_subtitle_download_all_seasons(
                    job.id, job.detected_title, all_seasons, tmdb_id=job.tmdb_id
                )

    async def _resolve_missing_tmdb_id(self, job: DiscJob) -> TmdbSignal | None:
        """Resolve a missing ``tmdb_id`` from a job's known title (caller commits).

        Generic-label imports (volume label "SEASON_3") and user-named discs set
        ``detected_title`` but never run the identify-time TMDB lookup (it is gated on
        the nameless volume label), leaving ``tmdb_id`` null. The subtitle/reference
        cache is keyed by tmdb_id (#288), so a null id makes the matcher read a
        non-existent name-keyed dir (``cache/data/<name>``) and find no references.
        Resolving the id keys matching, the season roster, and the extras pre-filter
        on the real show.

        Mutates ``job`` in place for a CONFIDENT, unambiguous match and returns the
        ``TmdbSignal``; same-name twins (Frasier 1993 vs the 2023 revival) are left
        null so the caller's collision block routes them to review (#287). Does NOT
        commit — the caller persists ``job`` atomically with its state transition, so
        no half-written {detected_title, tmdb_id} row is ever observable. Returns
        ``None`` (no mutation) when there is nothing to resolve, or when the TMDB
        lookup fails — a transient TMDB error is recoverable, not fatal to the job.
        """
        if job.tmdb_id is not None or not job.detected_title:
            return None

        from app.services.config_service import get_config

        config = await get_config()
        if not config.tmdb_api_key:
            return None

        try:
            signal = await asyncio.to_thread(
                classify_from_tmdb, job.detected_title, config.tmdb_api_key
            )
        except Exception as e:
            logger.warning(
                f"Job {job.id}: TMDB resolution failed for '{job.detected_title}', "
                f"proceeding with null tmdb_id: {e}",
                exc_info=True,
            )
            return None

        if not signal or not signal.tmdb_id:
            return signal

        # Mirror the caller's collision gate: don't auto-pick an ambiguous twin, nor a
        # no-year dominant twin (folder/label carries no year to disambiguate).
        no_year_twin = (
            not signal.ambiguous_identity
            and job.content_type == ContentType.TV
            and should_flag_no_year(signal.all_candidates, _label_has_year(job.volume_label))
        )
        if not signal.ambiguous_identity and not no_year_twin:
            job.tmdb_id = signal.tmdb_id
            job.tmdb_name = signal.tmdb_name
            job.candidates_json = _candidates_json_from_signal(signal)
            try:
                job.tmdb_year = await asyncio.to_thread(_resolve_show_year, signal.tmdb_id, signal)
            except Exception as e:
                logger.warning(
                    f"Job {job.id}: could not resolve show year for tmdb_id={signal.tmdb_id}: {e}",
                    exc_info=True,
                )
                job.tmdb_year = None
            logger.info(
                f"Job {job.id}: resolved missing tmdb_id={job.tmdb_id} "
                f"('{job.tmdb_name}') from title '{job.detected_title}'"
            )
        return signal

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
                job.updated_at = datetime.now(UTC)
                job.classification_confidence = analysis.confidence
                job.classification_source = analysis.classification_source or "staging_import"
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                _signal = getattr(analysis, "_tmdb_signal", None)
                job.candidates_json = _candidates_json_from_signal(_signal)
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, _signal
                )
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

                # Imports / generic-label discs derive the show name from the folder/label
                # but the identify-time TMDB lookup was gated on the (nameless) volume label,
                # leaving tmdb_id null. Resolve it now from the known title so reference
                # subtitles (tmdb-keyed since #288), the season roster, and the extras
                # pre-filter all key on the real id. Feeds the same-name signal to the block
                # below so ambiguous twins still route to review.
                if job.tmdb_id is None and not getattr(analysis, "_tmdb_signal", None):
                    _resolved_signal = await self._resolve_missing_tmdb_id(job)
                    if _resolved_signal is not None:
                        analysis._tmdb_signal = _resolved_signal

                # Same-name collision: route to review with the candidate list instead of
                # matching against the wrong same-named show's corpus by name. Covers both
                # the materiality-gate case (item 1) and the no-year backstop (Frasier
                # 1993 vs 2023) where the folder name has no year to disambiguate twins.
                _amb_signal = getattr(analysis, "_tmdb_signal", None)
                _amb = bool(_amb_signal and _amb_signal.ambiguous_identity)
                _noyear = bool(
                    _amb_signal
                    and not _amb
                    and job.content_type == ContentType.TV
                    and should_flag_no_year(
                        _amb_signal.all_candidates, _label_has_year(job.volume_label)
                    )
                )
                if _amb or _noyear:
                    reason = (
                        analysis.review_reason
                        if _amb
                        else _no_year_collision_reason(
                            job.detected_title, _amb_signal.all_candidates
                        )
                    )
                    await self._state_machine.transition_to_review(
                        job, session, reason=reason, broadcast=False
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=job.content_type.value,
                        detected_title=job.detected_title,
                        detected_season=job.detected_season,
                        total_titles=job.total_titles,
                        review_reason=reason,
                    )
                    logger.info(
                        f"Job {job_id}: same-name collision for '{job.detected_title}' "
                        f"(ambiguous={_amb}, no_year={_noyear}), routing to REVIEW_NEEDED"
                    )
                    return

                # Skip ripping — files already exist. Proceed to matching/organization.
                # Imports keep automatic all-seasons prefetch (flat folders genuinely
                # span seasons); only physical discs get the season prompt.
                if job.content_type == ContentType.TV:
                    if job.detected_title:
                        await self._start_tv_subtitle_prefetch(job)

                    # Transition to MATCHING and kick off per-title matching. Gate
                    # all downstream work on the transition succeeding: if it was
                    # rejected (e.g. a concurrent cancel/fail left the job in a
                    # terminal state), broadcasting tracks as MATCHING and spawning
                    # match tasks on a job that never left IDENTIFYING would desync
                    # the dashboard from the backend.
                    succeeded = await self._state_machine.transition(
                        job, JobState.MATCHING, session, broadcast=False
                    )
                    if succeeded:
                        await ws_manager.broadcast_job_update(job_id, JobState.MATCHING.value)

                        # Queue matching for each title
                        db_titles = (
                            (
                                await session.execute(
                                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                                )
                            )
                            .scalars()
                            .all()
                        )

                        for dt in db_titles:
                            dt.state = TitleState.QUEUED
                            session.add(dt)
                        await session.commit()

                        # titles_discovered already populated the UI with the tracks.
                        # They're enqueued for matching but only ``max_concurrent_matches``
                        # run at once, so render them as QUEUED ("waiting for a slot")
                        # — the QUEUED→MATCHING flip happens in match_single_file once a
                        # slot is actually acquired.
                        for dt in db_titles:
                            await self._broadcaster.broadcast_title_queued(dt)

                        for dt in db_titles:
                            if dt.output_filename:
                                file_path = Path(dt.output_filename)
                                discdb_applied = await self._try_discdb_assignment(
                                    job_id, dt, session
                                )
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
                    # Movie: skip matching, go straight to organization. Route
                    # through the state machine (now a legal IDENTIFYING ->
                    # ORGANIZING edge) so it persists, broadcasts, and fires
                    # transition observers like every other transition. Bail if the
                    # transition was rejected — finalizing a job that never entered
                    # ORGANIZING would organize on an inconsistent state.
                    succeeded = await self._state_machine.transition(
                        job, JobState.ORGANIZING, session
                    )
                    if not succeeded:
                        return

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
            # Resolve the TMDB id for the user-provided name now (same tmdb-keyed-cache
            # requirement as imports — #288). Confident single match sets the id;
            # ambiguous same-name twins are left null. Committed atomically with the
            # RIPPING transition below (the resolver does not commit).
            await self._resolve_missing_tmdb_id(job)
            # The season prompt and the unreadable-label prompt both resume
            # through here — kick the reference-subtitle prefetch now that the
            # identity is final. (This path previously never started a download
            # at all; #370.) A season the user left unset ("match across all
            # seasons") falls through to the all-seasons prefetch.
            if job.content_type == ContentType.TV and job.detected_title:
                await self._start_tv_subtitle_prefetch(job)
            job.review_reason = None
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

    async def re_identify(
        self,
        job_id: int,
        title: str,
        content_type_str: str,
        season: int | None = None,
        tmdb_id: int | None = None,
    ) -> dict:
        """Re-identify a job with user-corrected metadata.

        Returns:
            dict with 'job_id' and 'has_ripped' (bool) to signal
            whether JobManager should start ripping or matching.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state != JobState.REVIEW_NEEDED:
                raise ValueError(f"Cannot re-identify job in state: {job.state.value}")

            # Check if files already exist in staging (post-rip)
            has_ripped = False
            if job.staging_path:
                staging = Path(job.staging_path)
                has_ripped = staging.exists() and any(staging.glob("*.mkv"))

            # Update job metadata with user-corrected values
            job.detected_title = title
            job.content_type = ContentType(content_type_str)
            if season is not None:
                job.detected_season = season
            job.review_reason = None
            # Clear same-name candidates recorded for the PREVIOUS identification
            # attempt: the user has now made an explicit choice, so they're stale.
            # Leaving them would let the ReIdentifyModal quick-pick and the
            # wrong-show backstop (_detect_wrong_show) resurface the rejected twin.
            job.candidates_json = None

            # Optionally re-run TMDB lookup with corrected title
            _old_tmdb_id = job.tmdb_id
            _old_year = job.tmdb_year
            _signal = None
            if tmdb_id is not None:
                job.tmdb_id = tmdb_id
            else:
                # Try TMDB search with the corrected title
                try:
                    from app.core.tmdb_classifier import classify_from_tmdb
                    from app.services.config_service import get_config

                    config = await get_config()
                    if config.tmdb_api_key:
                        _signal = classify_from_tmdb(title, config.tmdb_api_key)
                        if _signal and _signal.tmdb_id:
                            job.tmdb_id = _signal.tmdb_id
                            if _signal.tmdb_name:
                                job.detected_title = _signal.tmdb_name
                except Exception:
                    logger.warning(
                        f"Job {job_id}: TMDB re-lookup failed for '{title}', "
                        f"continuing with user-provided title",
                        exc_info=True,
                    )

            # Re-derive the year for the (possibly changed) show so the library
            # folder stays correct after re-identification. Preserve a previously
            # resolved year ONLY when the show identity is unchanged and the
            # (cache-miss + offline) lookup fails — so a transient TMDB outage
            # can't blank a good year, but a stale year isn't carried across an
            # identity change to a different show.
            _year = await asyncio.to_thread(_resolve_show_year, job.tmdb_id, _signal)
            if _year is None and job.tmdb_id == _old_tmdb_id:
                _year = _old_year
            job.tmdb_year = _year

            if has_ripped:
                # Post-rip: go to MATCHING to re-run episode matching
                job.state = JobState.MATCHING
                target_state = JobState.MATCHING
            else:
                # Pre-rip: go to RIPPING
                job.state = JobState.RIPPING
                target_state = JobState.RIPPING

            job.updated_at = datetime.now(UTC)
            await session.commit()

            # Restart subtitle download with the corrected title. The original
            # subtitle attempt likely failed against the unresolvable label,
            # leaving subtitle_status="failed" and a stale `_subtitle_ready`
            # event that would gate matching back into REVIEW.
            should_restart_subtitles = (
                job.content_type == ContentType.TV
                and job.detected_title
                and job.detected_season is not None
                and self._restart_subtitle_download is not None
            )
            restart_args = (
                (job_id, job.detected_title, job.detected_season, job.tmdb_id)
                if should_restart_subtitles
                else None
            )

            await ws_manager.broadcast_job_update(
                job_id,
                target_state.value,
                content_type=job.content_type.value,
                detected_title=job.detected_title,
                detected_season=job.detected_season,
            )

            logger.info(
                f"Job {job_id}: re-identified as '{job.detected_title}' "
                f"({content_type_str}), transitioning to {target_state.value}"
            )

        # Restart outside the session block: restart_subtitle_download opens its
        # own session for cleanup and would deadlock on the same connection.
        if restart_args is not None:
            await self._restart_subtitle_download(*restart_args)

        return {"job_id": job_id, "has_ripped": has_ripped}

    async def _run_classification(
        self, job, job_id, titles, session, is_staging=False, disc_name: str = ""
    ):
        """Run the full classification pipeline (DiscDB, TMDB, AI, Analyst)."""
        from app.core.tmdb_classifier import classify_from_tmdb
        from app.services.config_service import get_config

        config = await get_config()
        self._analyst.set_config(config)

        def _try_tmdb(name: str, context: str):
            """Run a TMDB lookup, swallowing and logging failures.

            ``context`` distinguishes the warning message between call sites.
            Returns the TMDB signal, or None on failure / no API key.
            """
            if not config.tmdb_api_key:
                return None
            try:
                return classify_from_tmdb(name, config.tmdb_api_key)
            except Exception as e:
                logger.warning(f"Job {job_id}: {context}: {e}")
                return None

        # Attempt TheDiscDB lookup
        from app.core.features import DISCDB_ENABLED

        discdb_signal = None
        if DISCDB_ENABLED and config.discdb_enabled:
            try:
                from app.core.discdb_classifier import classify_from_discdb

                if is_staging:
                    content_hash = None
                elif job.content_hash:
                    # Set at insert (_create_job_for_disc); reuse it.
                    content_hash = job.content_hash
                else:
                    # Backfill: a job created before this change, or a cold-disc
                    # insert that missed the hash. Cheap (glob + stat).
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
            tmdb_context = (
                "TMDB lookup failed" if is_staging else "TMDB lookup failed, using heuristics only"
            )
            tmdb_signal = _try_tmdb(detected_name, tmdb_context)
            if tmdb_signal:
                logger.info(
                    f"Job {job_id}: TMDB signal: "
                    f"{tmdb_signal.content_type.value} "
                    f"({tmdb_signal.confidence:.0%}) - "
                    f"{tmdb_signal.tmdb_name}"
                )

        # Parse the MakeMKV DINFO disc name unconditionally (when present). Its
        # clean, human-readable title is used to corroborate the TMDB name and as a
        # better base name than the volume label — even when the volume label
        # already resolved on TMDB (the BREAKINGBADS2 -> "Breaking Bad" case).
        disc_name_title: str | None = None
        disc_name_season: int | None = None
        if disc_name:
            parsed_title, parsed_season = DiscAnalyst._parse_disc_name(disc_name)
            if parsed_title:
                disc_name_title = parsed_title
                disc_name_season = parsed_season

        # DINFO disc-name TMDB fallback — when the volume label gave no TMDB signal,
        # resolve identity from the disc name instead.
        if not tmdb_signal and disc_name_title and config.tmdb_api_key:
            disc_tmdb_signal = _try_tmdb(disc_name_title, "TMDB disc-name fallback failed")
            if disc_tmdb_signal:
                tmdb_signal = disc_tmdb_signal
                logger.info(
                    f"Job {job_id}: TMDB fallback via disc name '{disc_name_title}' succeeded "
                    f"(label '{job.volume_label}' gave garbled name)"
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
                    ai_tmdb_signal = _try_tmdb(ai_identified_name, "TMDB re-query after AI failed")
                    if ai_tmdb_signal:
                        tmdb_signal = ai_tmdb_signal
                        logger.info(
                            f"Job {job_id}: TMDB re-query with AI name: "
                            f"{tmdb_signal.content_type.value} "
                            f"({tmdb_signal.confidence:.0%}) - "
                            f"{tmdb_signal.tmdb_name}"
                        )
            except Exception as e:
                logger.warning(f"Job {job_id}: AI identification failed: {e}", exc_info=True)

        # Analyze disc content — pass disc_name_title so the analyst uses the clean
        # DINFO title as the base name and as a corroboration signal for the
        # authoritative TMDB name (instead of the garbled volume-label parse).
        analysis = self._analyst.analyze(
            titles,
            job.volume_label,
            tmdb_signal=tmdb_signal,
            disc_title=disc_name_title,
        )

        # If the disc-name fallback found a season the volume label didn't have, propagate it
        if disc_name_title and disc_name_season and not analysis.detected_season:
            analysis.detected_season = disc_name_season

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
