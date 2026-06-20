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
from app.core.fingerprint_disc_classifier import (
    identify_disc_via_network,
    network_titles_to_mappings,
)
from app.core.tmdb_classifier import (
    TMDB_DEGRADED_AUTH_FAILED,
    TMDB_DEGRADED_NOT_CONFIGURED,
    TmdbAuthError,
    TmdbSignal,
    classify_from_tmdb,
    should_flag_no_year,
)
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.identity_prompts import (
    IdentityResumeResult,
    ReIdentifyResumeResult,
    ResumeAction,
    mid_rip_resume_action,
)
from app.services.job_state_machine import JobStateMachine
from app.services.ripping_helpers import build_title_list

logger = logging.getLogger(__name__)


# Review reason for a job with no usable title. Shared between
# identify_from_staging's no-title park here and job_manager's fallback for a
# malformed blocking identity prompt (walk-away Phase B) — ONE literal, so the
# frontend routing (review page, no auto-modal: it matches no classifyPromptJob
# substring) can't drift between the two sites. job_manager imports it; the
# import goes this direction because job_manager already imports this module.
NO_TITLE_REVIEW_REASON = "Could not determine title. Please enter the title to continue."

# Permissive title-selection floor for identity-unknown discs (walk-away B2):
# anything >= 15 minutes could plausibly be an episode or a feature.
PERMISSIVE_MIN_DURATION_SECONDS = 900

# Digit-boundary (not \b) so underscores/parens count as separators:
# FRASIER_2023 and FRASIER (2023) match; a longer number like 20231 does not.
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def apply_permissive_title_selection(titles: list[DiscTitle]) -> None:
    """Select rip-worthy titles for a disc whose identity/type is unknown (B2).

    With no confirmed content type, neither the TV nor the movie selection
    heuristics apply at rip time. Rip permissively: every title that could
    plausibly be an episode or a feature (>= 15 min); when nothing clears the
    bar, the single longest title, so the disc always produces something to
    identify post-rip. No-op on an empty list (identification already fails a
    disc with no titles before selection).

    Only PENDING, non-extra titles are eligible. Titles finalized before rip
    (e.g. Play-All concat rows set to COMPLETED+is_extra by the TV branch) must
    not be re-selected — doing so wastes rip time on a multi-hour concat and
    adds an output_filename to a row that was intentionally finalized. When no
    eligible titles remain (all finalized), the helper is a no-op.
    """
    if not titles:
        return
    eligible = [t for t in titles if t.state == TitleState.PENDING and not t.is_extra]
    if not eligible:
        return
    keep = [t for t in eligible if (t.duration_seconds or 0) >= PERMISSIVE_MIN_DURATION_SECONDS]
    if not keep:
        keep = [max(eligible, key=lambda t: t.duration_seconds or 0)]
    keep_ids = {id(t) for t in keep}
    for t in eligible:
        t.is_selected = id(t) in keep_ids


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


def _resolve_tmdb_display_name(tmdb_id: int, content_type: ContentType) -> str | None:
    """Best-effort display name for a tmdb_id (the disc network returns only an id).

    TV -> the cached ``fetch_show_details`` ``name``; movie -> the cached
    ``fetch_movie_details`` ``title``. Both are public, cached, key-aware
    wrappers. Sync + network-bound (call via ``asyncio.to_thread``). Swallows
    every error and returns ``None`` — the caller still applies the override
    keyed on tmdb_id.
    """
    if not tmdb_id:
        return None
    try:
        if content_type == ContentType.TV:
            from app.matcher.tmdb_client import fetch_show_details

            details = fetch_show_details(tmdb_id)
            name = (details or {}).get("name")
            return str(name) if name else None

        # Movie: resolve title via the public, cached /movie/{id} wrapper.
        from app.matcher.tmdb_client import fetch_movie_details

        data = fetch_movie_details(tmdb_id)
        title = (data or {}).get("title")
        return str(title) if title else None
    except Exception as e:
        logger.warning(f"TMDB display-name lookup failed for id {tmdb_id}: {e}", exc_info=True)
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
        """Identify the disc contents using MakeMKV and the Analyst.

        Walk-away B2 path map: the four former pre-rip parks (A unreadable
        label, B TV-without-TMDB, C same-name collision, D unknown season) now
        ship to RIPPING with an ``identity_prompt_json`` CTA — see
        tests/unit/test_identify_rip_first_gates.py's module docstring for the
        gate-by-gate table and where each seam is pinned.
        """
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
                job.tmdb_degraded_reason = getattr(analysis, "tmdb_degraded_reason", None)
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

                # Broadcast titles discovered with full metadata (db_titles is
                # also reused by gate A's permissive selection just below).
                titles_result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                db_titles = list(titles_result.scalars().all())
                title_list = build_title_list(db_titles, include_video_resolution=True)
                await ws_manager.broadcast_titles_discovered(
                    job_id,
                    title_list,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                )

                # Gate A (walk-away B2): no title could be determined — rip
                # first with a non-blocking name prompt instead of parking.
                # Content type is unknown, so neither selection heuristic
                # applies: pick titles permissively before ripping. Ripped
                # titles park QUEUED under the blocking prompt (B3) and the
                # job converges to pooled review at rip end if unanswered (B4).
                if not job.detected_title:
                    apply_permissive_title_selection(db_titles)
                    logger.info(
                        f"Job {job_id}: no title detected (volume label: '{job.volume_label}'), "
                        f"ripping first and prompting for a name"
                    )
                    await self._rip_first_with_prompt(
                        job,
                        session,
                        job_id,
                        kind="name",
                        reason="Disc label unreadable. Please enter the title to continue.",
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

                # Gate B (walk-away B2): TV show detected but TMDB lookup failed —
                # the name cannot be trusted for episode matching, so rip first with
                # a non-blocking name prompt instead of parking. Subtitle prefetch is
                # skipped (returning before the prefetch block below): with no
                # tmdb_id, a name-keyed download would fetch the wrong show.
                # (Disc-name fallback already ran in _run_classification; if we reach
                # here, neither the volume label nor the DINFO name resolved on TMDB.)
                # Exclude collision jobs: they carry a candidate-naming reason that
                # the needs_review branch below converts (gate C).
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
                    # When the real cause is a missing/rejected key, say so instead
                    # of blaming the label (#243). Appended — the NamePromptModal
                    # keys on the "merged without separators" substring above.
                    if job.tmdb_degraded_reason:
                        reason = f"{reason} Note: {job.tmdb_degraded_reason}"
                    logger.info(
                        f"Job {job_id}: TMDB lookup failed for '{job.detected_title}' "
                        f"(volume label: '{job.volume_label}') — ripping first and "
                        f"prompting for the correct show name"
                    )
                    await self._rip_first_with_prompt(
                        job, session, job_id, kind="name", reason=reason
                    )
                    return

                # Start subtitle download for ALL TV content — except when identity is
                # ambiguous (same-name collision) or a no-year twin needs disambiguation.
                # Downloading by the tentative name would fetch the wrong show's subtitles
                # before the user disambiguates.
                if job.content_type == ContentType.TV and job.detected_title and not _collision:
                    # Gate D (walk-away B2): an unknown season auto-pins
                    # single-season shows, otherwise sets a non-blocking
                    # kind="season" prompt and continues — detected_season
                    # stays None, so the prefetch below takes the all-seasons
                    # path and matching searches across every season.
                    if job.detected_season is None:
                        await self._gate_unknown_season_disc(job, session, job_id)
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

                    # Gate C (walk-away B2): identity is uncertain but we have a
                    # best guess — rip first with a reidentify prompt instead of
                    # parking. Two cases: a same-name collision (twins persisted
                    # in candidates_json for the ReIdentifyModal quick-pick;
                    # prefetch already skipped) OR an uncorroborated single TMDB
                    # identity (analysis.identity_unconfirmed; tmdb_id is real, so
                    # the prefetch above ran). The user confirms via the
                    # "Confirm title" CTA any time, or it converges to the pooled
                    # review at rip end (B4).
                    if _collision or analysis.identity_unconfirmed:
                        await self._rip_first_with_prompt(
                            job,
                            session,
                            job_id,
                            kind="reidentify",
                            reason=analysis.review_reason,
                        )
                        return

                    # Every other review reason still parks BEFORE ripping
                    # (e.g. TMDB/heuristic content-type conflicts). A blocking
                    # review supersedes the season shortcut CTA — clear it so
                    # an identify-time REVIEW_NEEDED job never carries a
                    # prompt (the broadcast below clears it with "").
                    job.identity_prompt_json = None
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
                # broadcaster) and carries the review reason for parked jobs.
                # identity_prompt_json rides along so a gate-D season prompt reaches
                # the dashboard with the RIPPING update ("" clears on the frontend
                # merge when no prompt is set).
                await ws_manager.broadcast_job_update(
                    job_id,
                    job.state.value,
                    content_type=job.content_type.value,
                    detected_title=job.detected_title,
                    detected_season=job.detected_season,
                    total_titles=job.total_titles,
                    review_reason=analysis.review_reason,
                    identity_prompt_json=job.identity_prompt_json or "",
                    tmdb_degraded_reason=job.tmdb_degraded_reason or "",
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

    async def _gate_unknown_season_disc(self, job, session, job_id: int) -> None:
        """Resolve the unknown-season fate of a TV disc (#370, reworked for B2).

        Single-season show → auto-pin S1 and continue. Multi-season (or
        unresolvable) → set a non-blocking ``kind="season"`` prompt as an
        optional shortcut and continue: the show identity IS known, so the
        caller's all-seasons prefetch covers matching across every season
        while the user may pin a season at any time. Pre-B2 this gate parked
        the job in REVIEW_NEEDED before ripping; it no longer parks.
        """
        # Disc label carried no season (box-set labels like "Eureka D3"). A
        # single-season show needs no prompt; downstream, an unknown season
        # used to skip subtitle download entirely and dead-end every title in
        # review (#370) — the all-seasons prefetch now covers it.
        seasons = await self._resolve_all_season_numbers(job.detected_title, tmdb_id=job.tmdb_id)
        if len(seasons) == 1:
            job.detected_season = seasons[0]
            await session.commit()
            return
        # "select a season" is a frontend contract: the dashboard keys the
        # SeasonPromptModal on that exact substring — keep it in ONE literal.
        reason = (
            f"Identified as '{job.detected_title}' but the season could not "
            f"be detected from the disc label — select a season to continue."
        )
        job.identity_prompt_json = json.dumps({"kind": "season", "reason": reason})
        await session.commit()
        logger.info(
            f"Job {job_id}: season unknown for '{job.detected_title}' — non-blocking "
            f"season prompt set, matching will search across all seasons"
        )

    async def _rip_first_with_prompt(
        self, job, session, job_id: int, *, kind: str, reason: str
    ) -> None:
        """Ship a job to RIPPING carrying an identity prompt instead of parking (B2).

        Replaces a pre-rip REVIEW_NEEDED park: ``reason`` is recorded VERBATIM
        — the texts are frontend contracts (modal routing keys on substrings
        like "label unreadable" / "merged without separators") and B4's
        rip-end convergence replays them as ``review_reason``, reproducing
        today's review UX for an unanswered prompt. The transition mirrors the
        high-confidence auto-rip path (direct RIPPING + commit + broadcast +
        ``_run_ripping``). Blocking kinds (``name``/``reidentify``) park
        ripped titles in QUEUED via the B3 matching gate; ``season`` is a
        shortcut CTA and titles dispatch normally.
        """
        job.identity_prompt_json = json.dumps({"kind": kind, "reason": reason})
        job.state = JobState.RIPPING
        await session.commit()
        await ws_manager.broadcast_job_update(
            job_id,
            job.state.value,
            content_type=(job.content_type.value if job.content_type else None),
            detected_title=job.detected_title,
            detected_season=job.detected_season,
            total_titles=job.total_titles,
            identity_prompt_json=job.identity_prompt_json,
            tmdb_degraded_reason=job.tmdb_degraded_reason or "",
        )
        logger.info(
            f"Job {job_id}: rip-first with an open identity question (kind={kind}) — "
            f"auto-starting rip"
        )
        await self._run_ripping(job_id)

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
            job.tmdb_degraded_reason = TMDB_DEGRADED_NOT_CONFIGURED
            return None

        # A TV job re-resolving its (possibly over-specified) title pins the lookup
        # to the TV namespace, so a box-set title doesn't resolve to a fuzzy movie
        # whose id is then dropped as cross-namespace noise (Avatar box-set).
        prefer = ContentType.TV if job.content_type == ContentType.TV else None
        try:
            signal = await asyncio.to_thread(
                classify_from_tmdb,
                job.detected_title,
                config.tmdb_api_key,
                prefer_content_type=prefer,
            )
        except TmdbAuthError as e:
            # Surfaced on the job so the matcher's degraded results name the
            # real cause instead of looking like a missing show (#243).
            job.tmdb_degraded_reason = TMDB_DEGRADED_AUTH_FAILED
            logger.warning(
                f"Job {job.id}: TMDB rejected the API key while resolving "
                f"'{job.detected_title}', proceeding with null tmdb_id: {e}",
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.warning(
                f"Job {job.id}: TMDB resolution failed for '{job.detected_title}', "
                f"proceeding with null tmdb_id: {e}",
                exc_info=True,
            )
            return None

        if not signal:
            return signal
        # The lookup succeeded, so the key works — clear any stale degraded
        # marker recorded at identify time (#243).
        job.tmdb_degraded_reason = None
        if not signal.tmdb_id:
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
                job.tmdb_degraded_reason = getattr(analysis, "tmdb_degraded_reason", None)
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
                        reason=NO_TITLE_REVIEW_REASON,
                        broadcast=False,
                    )
                    await ws_manager.broadcast_job_update(
                        job_id,
                        JobState.REVIEW_NEEDED.value,
                        content_type=(job.content_type.value if job.content_type else None),
                        total_titles=job.total_titles,
                        review_reason=NO_TITLE_REVIEW_REASON,
                        tmdb_degraded_reason=job.tmdb_degraded_reason or "",
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
                        tmdb_degraded_reason=job.tmdb_degraded_reason or "",
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
                        await ws_manager.broadcast_job_update(
                            job_id,
                            JobState.MATCHING.value,
                            tmdb_degraded_reason=job.tmdb_degraded_reason or "",
                        )

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
    ) -> IdentityResumeResult:
        """Set a user-provided name for a disc and resume the pipeline.

        Accepts a job in REVIEW_NEEDED (a pre-rip park, the B4 post-rip
        convergence, or a stall review) or RIPPING (a mid-rip answer to the
        non-blocking identity CTA — walk-away B5).

        Returns the resume contract consumed by ``JobManager``:
        ``{"job_id": ..., "resume_action": ...}`` where ``resume_action`` is

        - ``"start_rip"`` — pre-rip review resume → spawn ``_run_ripping``.
          The ONLY action that may start a rip; every other action must not
          (the double-rip hazard: mid-rip the rip is already running, post-rip
          the disc is already ripped and ejected).
        - ``"dispatch_matches"`` — TV answer with files ripping/ripped:
          release identity-parked QUEUED titles into episode matching.
        - ``"release_movie_titles"`` — non-TV mid-rip answer: flip parked
          QUEUED titles to MATCHED; the running rip's movie tail finishes.
        - ``"resolve_movie"`` — non-TV post-rip answer: route to the movie
          feature-resolution path (never episode matching).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING):
                raise ValueError(f"Cannot set name on job in state: {job.state}")

            mid_rip = job.state == JobState.RIPPING
            # Post-rip detection (mirrors re_identify): staged .mkv files mean
            # the disc already ripped (and was ejected at rip end) — resuming
            # must route to matching/movie resolution, never a second rip.
            has_ripped = False
            if not mid_rip and job.staging_path:
                staging = Path(job.staging_path)
                has_ripped = staging.exists() and any(staging.glob("*.mkv"))

            job.detected_title = name
            job.content_type = ContentType(content_type_str)
            if season is not None:
                job.detected_season = season
            # Resolve the TMDB id for the user-provided name now (same tmdb-keyed-cache
            # requirement as imports — #288). Confident single match sets the id;
            # ambiguous same-name twins are left null. Committed atomically with the
            # state handling below (the resolver does not commit).
            await self._resolve_missing_tmdb_id(job)
            # The season prompt and the unreadable-label prompt both resume
            # through here — kick the reference-subtitle prefetch now that the
            # identity is final. (This path previously never started a download
            # at all; #370.) A season the user left unset ("match across all
            # seasons") falls through to the all-seasons prefetch.
            if job.content_type == ContentType.TV and job.detected_title:
                await self._start_tv_subtitle_prefetch(job)
            # The question is answered — retire the walk-away CTA (B5); the ""
            # in the broadcast below clears it on the frontend merge.
            job.identity_prompt_json = None
            is_tv = job.content_type == ContentType.TV

            if mid_rip:
                # Mid-rip answer: metadata only — NO state change and NO new
                # rip task (the rip is already running). review_reason is left
                # untouched: if the B4 convergence committed REVIEW_NEEDED
                # between our state read and this commit (possible — the
                # awaits above yield the event loop), the job parks in review
                # with its reason intact and the user answers again there
                # (review-resume); matching dispatched below still proceeds.
                target_state = JobState.RIPPING
                resume_action: ResumeAction = mid_rip_resume_action(is_tv)
            elif has_ripped:
                job.review_reason = None
                if is_tv:
                    # Post-rip answer (B4 convergence / stall review): files
                    # exist and the disc is gone — go to MATCHING, never re-rip.
                    job.state = JobState.MATCHING
                    target_state = JobState.MATCHING
                    resume_action = "dispatch_matches"
                else:
                    # Movie post-rip: the feature-resolution task owns the
                    # state from here (ORGANIZING, or back to review for
                    # competing cuts) — leave it for one coherent broadcast.
                    target_state = job.state
                    resume_action = "resolve_movie"
            else:
                job.review_reason = None
                job.state = JobState.RIPPING
                target_state = JobState.RIPPING
                resume_action = "start_rip"

            job.updated_at = datetime.now(UTC)
            await session.commit()

            await ws_manager.broadcast_job_update(
                job_id,
                target_state.value,
                content_type=job.content_type.value,
                detected_title=job.detected_title,
                detected_season=job.detected_season,
                identity_prompt_json="",
            )

            logger.info(
                f"Job {job_id}: user set name to '{name}' ({content_type_str}), "
                f"resume action: {resume_action}"
            )

        return {"job_id": job_id, "resume_action": resume_action}

    async def re_identify(
        self,
        job_id: int,
        title: str,
        content_type_str: str,
        season: int | None = None,
        tmdb_id: int | None = None,
    ) -> ReIdentifyResumeResult:
        """Re-identify a job with user-corrected metadata.

        Accepts a job in REVIEW_NEEDED (today's review answer) or RIPPING (a
        mid-rip answer to the walk-away identity CTA — B5).

        Returns:
            dict with 'job_id', 'has_ripped' (bool), and 'resume_action' — the
            same contract as ``set_name_and_resume`` (see its docstring), plus
            ``"rerun_matching"`` for the TV post-rip review answer (full
            re-match with corrected metadata, today's behavior).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING):
                raise ValueError(f"Cannot re-identify job in state: {job.state.value}")

            mid_rip = job.state == JobState.RIPPING

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
            if not mid_rip:
                # Mid-rip there is no review_reason to clear, and leaving it
                # untouched keeps a convergence-race park readable (see the
                # mid_rip comment in set_name_and_resume).
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
                        _signal = await asyncio.to_thread(
                            classify_from_tmdb, title, config.tmdb_api_key
                        )
                        # The key was tried and accepted (no TmdbAuthError) — any
                        # stale "rejected" marker from identify time is now wrong
                        # regardless of whether results were found (#243).
                        job.tmdb_degraded_reason = None
                        if _signal and _signal.tmdb_id:
                            job.tmdb_id = _signal.tmdb_id
                            if _signal.tmdb_name:
                                job.detected_title = _signal.tmdb_name
                    else:
                        job.tmdb_degraded_reason = TMDB_DEGRADED_NOT_CONFIGURED
                except TmdbAuthError as e:
                    job.tmdb_degraded_reason = TMDB_DEGRADED_AUTH_FAILED
                    logger.warning(
                        f"Job {job_id}: TMDB rejected the API key during re-identify of "
                        f"'{title}', continuing with user-provided title: {e}",
                        exc_info=True,
                    )
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

            # The question is answered — retire the walk-away CTA (B5); the ""
            # in the broadcast below clears it on the frontend merge.
            job.identity_prompt_json = None
            is_tv = job.content_type == ContentType.TV

            if mid_rip:
                # Mid-rip answer: metadata only — NO state change and NO new
                # tasks; the running rip continues and the rip-end re-read
                # (B4) picks up the corrected identity.
                target_state = JobState.RIPPING
                resume_action: ResumeAction = mid_rip_resume_action(is_tv)
                # The identify-time prefetch was skipped while the identity
                # question was open (B2 gates B/C) — kick it now. Known season
                # → that season; unknown → all seasons (cross-season matching).
                if is_tv and job.detected_title:
                    await self._start_tv_subtitle_prefetch(job)
            elif has_ripped:
                if is_tv:
                    # Post-rip: go to MATCHING to re-run episode matching
                    job.state = JobState.MATCHING
                    target_state = JobState.MATCHING
                    resume_action = "rerun_matching"
                else:
                    # A movie answer with ripped files routes to the movie
                    # feature-resolution path, never episode MATCHING. The
                    # resolution task owns the state from here.
                    target_state = job.state
                    resume_action = "resolve_movie"
            else:
                # Pre-rip: go to RIPPING
                job.state = JobState.RIPPING
                target_state = JobState.RIPPING
                resume_action = "start_rip"

            job.updated_at = datetime.now(UTC)
            await session.commit()

            # Restart subtitle download with the corrected title. The original
            # subtitle attempt likely failed against the unresolvable label,
            # leaving subtitle_status="failed" and a stale `_subtitle_ready`
            # event that would gate matching back into REVIEW. (Review-resume
            # only: the mid-rip branch above starts a fresh prefetch instead —
            # nothing stale exists while the identity question is open.)
            should_restart_subtitles = (
                not mid_rip
                and job.content_type == ContentType.TV
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
                tmdb_degraded_reason=job.tmdb_degraded_reason or "",
                identity_prompt_json="",
            )

            logger.info(
                f"Job {job_id}: re-identified as '{job.detected_title}' "
                f"({content_type_str}), resume action: {resume_action}"
            )

        # Restart outside the session block: restart_subtitle_download opens its
        # own session for cleanup and would deadlock on the same connection.
        if restart_args is not None:
            await self._restart_subtitle_download(*restart_args)

        return {"job_id": job_id, "has_ripped": has_ripped, "resume_action": resume_action}

    async def _run_classification(
        self, job, job_id, titles, session, is_staging=False, disc_name: str = ""
    ):
        """Run the full classification pipeline (DiscDB, TMDB, AI, Analyst)."""
        from app.core.tmdb_classifier import classify_from_tmdb
        from app.services.config_service import get_config

        config = await get_config()
        self._analyst.set_config(config)

        # Why the TMDB stage was unavailable for this job, if it was (#243 P3).
        # Pre-set when no key is stored; flipped to the auth message the moment a
        # lookup is rejected. Cleared at the end if a TMDB signal was obtained.
        tmdb_degraded_reason: str | None = (
            None if config.tmdb_api_key else TMDB_DEGRADED_NOT_CONFIGURED
        )

        async def _try_tmdb(
            name: str, context: str, prefer_content_type: ContentType | None = None
        ):
            """Run a TMDB lookup, swallowing and logging failures.

            ``context`` distinguishes the warning message between call sites.
            ``prefer_content_type`` is forwarded to ``classify_from_tmdb`` so a
            namespace-known caller (e.g. the TV re-resolve below) can pin the
            result to that namespace. Returns the TMDB signal, or None on failure
            / no API key.

            ``classify_from_tmdb`` makes blocking ``requests.get`` calls, so it is
            offloaded to a thread to avoid stalling the event loop (mirrors
            ``_resolve_missing_tmdb_id``). ``asyncio.to_thread`` re-raises the
            worker thread's exception at the await point, so the handlers below
            still catch TMDB failures.
            """
            nonlocal tmdb_degraded_reason
            if not config.tmdb_api_key:
                return None
            try:
                return await asyncio.to_thread(
                    classify_from_tmdb,
                    name,
                    config.tmdb_api_key,
                    prefer_content_type=prefer_content_type,
                )
            except TmdbAuthError as e:
                # Bad/expired key — not a transient lookup failure. Remember the
                # cause so it can be surfaced on the job instead of letting the
                # fall-through look like "show not found".
                logger.warning(f"Job {job_id}: {context}: {e}", exc_info=True)
                tmdb_degraded_reason = TMDB_DEGRADED_AUTH_FAILED
                return None
            except Exception as e:
                logger.warning(f"Job {job_id}: {context}: {e}", exc_info=True)
                return None

        # Disc-hash network identification (walk-away Phase C). Best-effort: a
        # confident crowd-promoted hit gives an identity (and, for the top tier,
        # a verified episode mapping) with ZERO audio matching. Gated on the
        # opt-in flag AND a known content hash; everything is swallowed by
        # identify_disc_via_network so this can never break classification. The
        # override is APPLIED below (after the analyst runs) so the analyst's
        # base name/season are present as a fallback; ``network_signal`` being a
        # confident-tier result also suppresses the AI fallback (no clobber).
        network_signal = None
        network_confident = False
        network_content_type: ContentType | None = None
        if getattr(config, "enable_fingerprint_identification", False) and job.content_hash:
            network_signal = await identify_disc_via_network(
                job.content_hash, getattr(config, "fingerprint_server_url", None)
            )
            if network_signal and network_signal.tier in ("canonical", "confirmed"):
                # Map the content type up front. An unrecognized value (not
                # "tv"/"movie") means a confident tier is carrying a garbage
                # type — don't trust it: skip the override entirely and fall
                # through to the normal TMDB/AI/heuristic flow.
                network_content_type = {
                    "tv": ContentType.TV,
                    "movie": ContentType.MOVIE,
                }.get(network_signal.content_type)
                if network_content_type is None:
                    logger.warning(
                        f"Job {job_id}: disc network hit with unrecognized "
                        f"content_type={network_signal.content_type!r} "
                        f"(tier={network_signal.tier}) — ignoring (not a confident override)"
                    )
                else:
                    network_confident = True
                    logger.info(
                        f"Job {job_id}: disc network hit — tier={network_signal.tier} "
                        f"tmdb_id={network_signal.tmdb_id} "
                        f"({network_signal.unique_contributors} contributors)"
                    )

        # Attempt TheDiscDB lookup
        from app.core.features import DISCDB_LOOKUP_ENABLED

        discdb_signal = None
        if DISCDB_LOOKUP_ENABLED and config.discdb_enabled:
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
        detected_name, label_season, _ = DiscAnalyst._parse_volume_label(job.volume_label)
        if detected_name:
            tmdb_context = (
                "TMDB lookup failed" if is_staging else "TMDB lookup failed, using heuristics only"
            )
            tmdb_signal = await _try_tmdb(detected_name, tmdb_context)
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

        # A season parsed from the label OR the disc name means the disc is TV.
        # Computed here so the disc-name fallback below can pin its lookup to the
        # TV namespace (a box-set title resolves to a fuzzy movie otherwise).
        disc_says_tv = label_season is not None or disc_name_season is not None

        # DINFO disc-name TMDB fallback — when the volume label gave no TMDB signal,
        # resolve identity from the disc name instead. For a known-TV disc, pin the
        # lookup to the TV namespace so a box-set title ("Avatar: The Last Airbender
        # Book One: Water") resolves to the series rather than a fuzzy movie that
        # the downstream cross-namespace guard would then discard (Avatar box-set).
        disc_name_queried = False
        if not tmdb_signal and disc_name_title and config.tmdb_api_key:
            disc_name_queried = True
            disc_tmdb_signal = await _try_tmdb(
                disc_name_title,
                "TMDB disc-name fallback failed",
                prefer_content_type=ContentType.TV if disc_says_tv else None,
            )
            if disc_tmdb_signal:
                tmdb_signal = disc_tmdb_signal
                logger.info(
                    f"Job {job_id}: TMDB fallback via disc name '{disc_name_title}' succeeded "
                    f"(label '{job.volume_label}' gave garbled name)"
                )

        # Cross-namespace re-resolve (Mad Men S3 regression): a volume-label match
        # that came back as a MOVIE is suspect when on-disc evidence says TV — a
        # movie tmdb_id is dereferenced as a TV id downstream (subtitle/roster) and
        # resolves to an unrelated show. When the label or the DINFO disc name
        # carries a season, re-resolve identity from the cleaner disc name and
        # prefer a TV hit (e.g. "Madmen"->movie "Two Madmen" -> disc name
        # "Mad Men Season 3" -> the real TV show). Skip if the disc-name fallback
        # already queried this exact title — it would return the same movie result.
        if (
            tmdb_signal
            and tmdb_signal.content_type == ContentType.MOVIE
            and disc_says_tv
            and disc_name_title
            and not disc_name_queried
            and config.tmdb_api_key
        ):
            # The disc is known-TV, so pin the re-resolve to the TV namespace: a
            # box-set title ("Avatar: The Last Airbender Book One: Water") whose
            # stripped variation also matches a fuzzy, sometimes more-popular movie
            # ("Avatar Aang: The Last Airbender") would otherwise come back a movie
            # again and the recovery would fail. (Avatar box-set regression.)
            tv_retry = await _try_tmdb(
                disc_name_title,
                "TMDB TV re-resolve from disc name failed",
                prefer_content_type=ContentType.TV,
            )
            if tv_retry and tv_retry.content_type == ContentType.TV:
                logger.info(
                    f"Job {job_id}: volume-label match '{tmdb_signal.tmdb_name}' was a movie "
                    f"but the disc looks like TV; re-resolved to TV '{tv_retry.tmdb_name}' "
                    f"via disc name '{disc_name_title}'"
                )
                tmdb_signal = tv_retry

        # AI-powered identification fallback (not for staging). Skipped when the
        # disc network already resolved a confident identity — that result takes
        # precedence and must not be clobbered by a lower-trust AI guess.
        ai_identified_name = None
        if (
            not is_staging
            and not network_confident
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
                    ai_tmdb_signal = await _try_tmdb(
                        ai_identified_name, "TMDB re-query after AI failed"
                    )
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

        # Expected episode runtimes let the analyst keep a double-length pilot
        # (e.g. DS9 "Emissary") instead of mistaking it for a Play-All concatenation.
        expected_runtimes: list[int] | None = None
        if tmdb_signal and tmdb_signal.tmdb_id and tmdb_signal.content_type == ContentType.TV:
            season_for_runtimes = label_season or disc_name_season
            if season_for_runtimes:
                from app.matcher.tmdb_client import fetch_season_episode_runtimes

                try:
                    expected_runtimes = await asyncio.to_thread(
                        fetch_season_episode_runtimes,
                        str(tmdb_signal.tmdb_id),
                        season_for_runtimes,
                    )
                except Exception as e:  # network/runtime data is best-effort
                    logger.warning(
                        f"Job {job_id}: expected-runtime fetch failed: {e}", exc_info=True
                    )

        # Analyze disc content — pass disc_name_title so the analyst uses the clean
        # DINFO title as the base name and as a corroboration signal for the
        # authoritative TMDB name (instead of the garbled volume-label parse).
        analysis = self._analyst.analyze(
            titles,
            job.volume_label,
            tmdb_signal=tmdb_signal,
            disc_title=disc_name_title,
            expected_episode_runtimes=expected_runtimes,
        )

        # If the disc-name fallback found a season the volume label didn't have, propagate it
        if disc_name_title and disc_name_season and not analysis.detected_season:
            analysis.detected_season = disc_name_season

        # If AI identified a name but TMDB re-query also failed
        if ai_identified_name and not analysis.detected_name:
            analysis.detected_name = ai_identified_name
            analysis.classification_source = "ai"

        # Disc-network override (walk-away Phase C). A confident crowd-promoted
        # hit is authoritative: it sets identity directly from tmdb_id (the
        # network returns no name, so the display name is resolved best-effort —
        # a failed lookup still applies the override). For the top tier
        # (canonical) we additionally pre-assign episodes via the EXISTING DiscDB
        # mapping machinery (verified ±2s/±1% against scanned titles), letting
        # try_discdb_assignment skip ASR at rip time. "confirmed" is identity
        # only — episodes are still verified by chromaprint/ASR. This runs BEFORE
        # the TheDiscDB block and wins over it (network_confident guards that
        # block) so the network result is never clobbered downstream.
        if network_confident:
            # Recognized type guaranteed by the gate above (unrecognized types
            # never set network_confident, so they fall through to TMDB/AI).
            net_type = network_content_type
            analysis.content_type = net_type
            analysis.tmdb_id = network_signal.tmdb_id
            analysis.confidence = 0.99 if network_signal.tier == "canonical" else 0.95
            analysis.classification_source = "fingerprint_network"
            analysis.needs_review = False

            resolved_name = await asyncio.to_thread(
                _resolve_tmdb_display_name, network_signal.tmdb_id, net_type
            )
            if resolved_name:
                analysis.detected_name = resolved_name
            if (
                net_type == ContentType.TV
                and network_signal.season is not None
                and analysis.detected_season is None
            ):
                analysis.detected_season = network_signal.season

            if network_signal.tier == "canonical":
                mappings = network_titles_to_mappings(network_signal.titles, titles)
                if mappings:
                    self._set_discdb_mappings(job_id, mappings)
                    job.discdb_mappings_json = json.dumps([asdict(m) for m in mappings])
                logger.info(
                    f"Job {job_id}: disc network canonical override — tmdb_id="
                    f"{network_signal.tmdb_id}, {len(mappings)} verified mapping(s) applied"
                )
            else:
                logger.info(
                    f"Job {job_id}: disc network confirmed override — tmdb_id="
                    f"{network_signal.tmdb_id} (identity only, episodes verified by ASR)"
                )

        # If TheDiscDB returned a high-confidence match, override the analysis
        if not network_confident and discdb_signal and discdb_signal.confidence >= 0.90:
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
        elif discdb_signal and not network_confident:
            # Symmetry with the AI fallback and the high-confidence DiscDB block:
            # a confident network override owns identity (its tmdb_id is
            # authoritative), so a DiscDB-derived name — possibly a different
            # match — must not back-fill even when the network's best-effort
            # name came back blank. The pure DiscDB path (network disabled / no
            # hit) is unaffected.
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
        # A signal can only exist when the key worked, so it trumps any earlier
        # degraded marker (and the no-key preset can never coexist with one).
        analysis.tmdb_degraded_reason = None if tmdb_signal else tmdb_degraded_reason

        return analysis
