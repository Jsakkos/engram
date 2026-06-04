"""Finalization Coordinator - Conflict resolution, organization, and job completion.

Extracted from JobManager to isolate finalization concerns.
"""

import asyncio
import json
import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine

logger = logging.getLogger(__name__)


def _detect_wrong_show(job, titles) -> dict | None:
    """Detect a same-name wrong-show pick from a wholesale match failure.

    Frasier-class bug: a disc identified as the dominant same-name twin (e.g. the
    1993 original #3452) when it's actually the other twin (the 2023 revival
    #195241). Every episode then matches the wrong reference corpus at noise floor,
    so the WHOLE disc returns ``matched_episode is None``. Combined with a persisted
    same-name twin (``candidates_json``), that is a confident wrong-show signal.

    Returns ``{"current", "twin", "unmatched"}`` (candidate dicts) when detected,
    else None. Pure — no DB/IO. The signal is AGGREGATE (all episode candidates
    matched nothing), never an absolute per-chunk score, so it's robust to the
    structurally-low chunk-cosine scale. A merely low-confidence disc still carries
    an episode code, so it is excluded here and handled by the normal review path.
    """
    if job.content_type != ContentType.TV:
        return None

    try:
        candidates = json.loads(job.candidates_json) if job.candidates_json else []
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(candidates, list) or len(candidates) < 2:
        return None

    # Episode-candidate titles = the selected, non-extra tracks we tried to match.
    episode_candidates = [t for t in titles if t.is_selected and not t.is_extra]
    if len(episode_candidates) < 2:
        return None
    if not all(t.matched_episode is None for t in episode_candidates):
        return None

    twin = next((c for c in candidates if str(c.get("tmdb_id")) != str(job.tmdb_id)), None)
    if not twin:
        return None
    current = next((c for c in candidates if str(c.get("tmdb_id")) == str(job.tmdb_id)), None)
    return {"current": current, "twin": twin, "unmatched": len(episode_candidates)}


def _wrong_show_review_reason(detection: dict, job) -> str:
    """Human-readable, actionable review reason naming the suspected correct twin."""

    def _label(cand, fallback_name):
        if not cand:
            return fallback_name
        year = cand.get("year")
        name = cand.get("name") or fallback_name
        return f"{name} ({year})" if year else name

    current = _label(detection.get("current"), job.tmdb_name or job.detected_title or "this show")
    twin = _label(detection.get("twin"), "another same-named show")
    return (
        f"Content doesn't resemble {current} — none of the "
        f"{detection['unmatched']} episodes matched its subtitles. This is likely a "
        f"different same-named show; did you mean {twin}? Re-identify to fix."
    )


def _library_path_for_job(job, content_type: str) -> "Path | None":
    """Return a library_path override for in_place jobs, or None for library mode."""
    if job.destination_mode != "in_place":
        return None
    from app.services.config_service import get_config_sync

    cfg = get_config_sync()
    if not cfg.import_watch_path:
        return None
    return Path(cfg.import_watch_path) / ("Movies" if content_type == "movie" else "TV")


# --- Automatic conflict escalation ---------------------------------------
# When two titles match the same episode, we re-run the audio matcher on the
# contested titles at progressively denser sampling before falling back to
# manual review. The ladder is depth-only: scan more points (more evidence),
# but keep the matcher's default vote gate so a genuinely-correct match on a
# hard episode isn't demoted straight to review.
_CHUNK_DURATION_S = 30  # mirrors EpisodeMatcher.chunk_duration
_MAX_SCAN_POINTS = 200  # bound runtime even for very long tracks
_CONFLICT_FIXED_DEPTHS = (25, 50)  # first two tiers; final tier is full coverage
_EP_CODE_RE = re.compile(r"[Ss](\d+)[Ee](\d+)")


def _normalize_episode_code(code: str | None) -> str:
    """Canonicalize ``SxxExx`` so padded/unpadded variants collide.

    The matcher's fallback path can emit unpadded codes ("S1E14") while its
    main path emits "S01E14"; without normalizing, a real collision would be
    grouped under two different keys and missed.
    """
    match = _EP_CODE_RE.search(code or "")
    if not match:
        return (code or "").upper()
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"


def _detect_conflicts(titles) -> dict[str, list]:
    """Group titles by canonical episode code, keeping only ties.

    Considers MATCHED titles AND REVIEW titles that still carry the matcher's
    borderline best-guess episode code. A confidently-matched title colliding
    with a low-confidence REVIEW title on the same episode IS a conflict the
    auto-resolution should break — handling only MATCHED-vs-MATCHED leaves the
    pair stuck (the borderline one re-matches alone via review-escalation while
    the confident one rides alongside, never resolving the collision).
    Excludes extras, force-advanced titles, and REVIEW titles parked for
    non-matching reasons (file_exists / subtitle_failed).
    """
    by_ep: dict[str, list] = {}
    for t in titles:
        if not t.matched_episode:
            continue
        if t.state == TitleState.MATCHED:
            by_ep.setdefault(_normalize_episode_code(t.matched_episode), []).append(t)
        elif t.state == TitleState.REVIEW and _is_rematchable_review(t):
            by_ep.setdefault(_normalize_episode_code(t.matched_episode), []).append(t)
    return {ep: tl for ep, tl in by_ep.items() if len(tl) > 1}


# REVIEW reasons a deeper matcher pass cannot fix — never auto re-match these.
_NON_REMATCHABLE_REVIEW_ERRORS = {"file_exists", "subtitle_download_failed"}


def _is_rematchable_review(t) -> bool:
    """A REVIEW title whose low confidence a denser matcher pass could plausibly fix.

    Excludes extras (not episodes) and titles parked in REVIEW for non-matching
    reasons (organization conflicts, missing reference subtitles) — re-running the
    audio matcher on those just wastes a pass.
    """
    if t.state != TitleState.REVIEW or t.is_extra:
        return False
    if t.match_details:
        try:
            details = json.loads(t.match_details)
        except (json.JSONDecodeError, TypeError):
            details = None
        if isinstance(details, dict):
            if details.get("error") in _NON_REMATCHABLE_REVIEW_ERRORS:
                return False
            if details.get("auto_sorted") == "extras":
                return False
            # Force-advanced (watchdog) or user-skipped → deliberate hand-to-human;
            # re-matching would undo that and risk re-entering a stuck state.
            if details.get("forced_review"):
                return False
    return True


def _full_coverage_points(titles) -> int:
    """Scan points needed to transcribe ~100% of the longest contested track."""
    longest = max((t.duration_seconds or 0 for t in titles), default=0)
    if longest <= 0:
        return _MAX_SCAN_POINTS  # duration unknown: go as deep as allowed
    return min(math.ceil(longest / _CHUNK_DURATION_S) + 1, _MAX_SCAN_POINTS)


def _conflict_scan_ladder(titles) -> list[int]:
    """Strictly-increasing escalation ladder, capped at full coverage.

    For short episodes the fixed tiers already exceed full coverage, so the
    ladder collapses (e.g. ``[25, 45]``); for long tracks it grows
    (``[25, 50, 180]``). The last entry is always full coverage — there is no
    "deeper" to go, which is the natural termination point.
    """
    full = _full_coverage_points(titles)
    ladder: list[int] = []
    for depth in (*_CONFLICT_FIXED_DEPTHS, full):
        depth = min(depth, full)
        if depth > 1 and (not ladder or depth > ladder[-1]):
            ladder.append(depth)
    return ladder


def _merge_match_details(existing: str | None, updates: dict) -> str:
    """Merge ``updates`` into an existing match_details JSON string.

    If ``existing`` is missing or unparseable, the result is just ``updates``.
    """
    merged: dict = {}
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict):
                merged = parsed
        except (json.JSONDecodeError, TypeError):
            merged = {}
    merged.update(updates)
    return json.dumps(merged)


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
        self._rematch_conflict: callable = None
        self._rematch_title: callable = None

        # Last scan depth attempted per job while auto-resolving. Transient (in
        # memory); cleared on resolution, exhaustion, or restart. Conflicts and
        # plain reviews escalate on separate counters so one can't stall the other.
        self._conflict_passes: dict[int, int] = {}
        self._review_passes: dict[int, int] = {}

    def set_callbacks(
        self,
        *,
        run_ripping,
        on_task_done,
        active_jobs,
        match_single_file=None,
        rematch_conflict=None,
        rematch_title=None,
    ) -> None:
        """Set cross-coordinator callbacks."""
        self._run_ripping = run_ripping
        self._on_task_done = on_task_done
        self._active_jobs = active_jobs
        self._match_single_file = match_single_file
        self._rematch_conflict = rematch_conflict
        self._rematch_title = rematch_title

    def reset_conflict_passes(self, job_id: int) -> None:
        """Forget any in-progress auto-escalation for ``job_id`` (conflict + review).

        Called from terminal-state hooks and from rerun-matching, where the whole
        escalation history is meant to be discarded. Intra-pass bail-outs in the
        finalization loop use the per-kind ``_clear_conflict_state`` /
        ``_clear_review_state`` helpers instead so the two escalations don't
        clobber each other's progress counter.
        """
        self._conflict_passes.pop(job_id, None)
        self._review_passes.pop(job_id, None)

    async def on_terminal_clear_conflicts(self, job_id: int, _state) -> None:
        """Terminal-state hook: drop conflict-escalation tracking for the job.

        Also clears the persisted ``conflict_status`` so a job forced to a
        terminal state mid-escalation (e.g. finalization throws on a later
        pass) doesn't leave a stale "Resolving…" note in the DB.
        """
        self.reset_conflict_passes(job_id)
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is not None and job.conflict_status is not None:
                job.conflict_status = None
                await session.commit()

    # Note prefixes used to identify whose escalation set ``conflict_status`` so
    # each clear path only wipes its own note (avoids the conflict path flickering
    # the review note off mid-pass when both are running in the same job).
    _CONFLICT_NOTE_PREFIX = "Resolving episode conflicts"
    _REVIEW_NOTE_PREFIX = "Deep re-matching low-confidence"

    async def _clear_conflict_state(self, session, job) -> None:
        """Drop only the conflict-escalation tracking + note for ``job``.

        Review-escalation state is intentionally left alone — wiping it from
        here was a bug that pinned review-escalation at pass 1 forever, because
        every check_job_completion re-entry first runs conflict-escalate (which
        clears, when no MATCHED collisions are present) and then review-escalate
        (which reads the now-zeroed counter and re-dispatches at the lowest depth).
        """
        self._conflict_passes.pop(job.id, None)
        if job.conflict_status and job.conflict_status.startswith(self._CONFLICT_NOTE_PREFIX):
            job.conflict_status = None
            await session.commit()

    async def _clear_review_state(self, session, job) -> None:
        """Drop only the review-escalation tracking + note for ``job``."""
        self._review_passes.pop(job.id, None)
        if job.conflict_status and job.conflict_status.startswith(self._REVIEW_NOTE_PREFIX):
            job.conflict_status = None
            await session.commit()

    async def _maybe_escalate_conflicts(self, session, job, titles) -> bool:
        """Deep re-match titles colliding on the same episode, escalating depth.

        Returns ``True`` if a re-match was dispatched (the job is held in
        MATCHING and the caller should return — completion re-entry will pick
        it back up). Returns ``False`` when there is nothing to escalate
        (no collision, ladder exhausted, or contested files missing), after
        clearing any transient state so the normal review/organize path runs.
        """
        job_id = job.id
        # Episode collisions only apply to TV; movies resolve file conflicts elsewhere.
        if job.content_type != ContentType.TV or self._rematch_conflict is None:
            await self._clear_conflict_state(session, job)
            return False

        conflicts = _detect_conflicts(titles)
        if not conflicts:
            await self._clear_conflict_state(session, job)
            return False

        contested = [t for group in conflicts.values() for t in group]
        ladder = _conflict_scan_ladder(contested)
        last_depth = self._conflict_passes.get(job_id, 0)
        next_depth = next((d for d in ladder if d > last_depth), None)

        if next_depth is None:
            logger.info(
                f"Job {job_id}: conflict re-match exhausted at {last_depth} scan points; "
                f"{list(conflicts)} still contested — handing to review"
            )
            # See _maybe_escalate_reviews: leave the counter at last_depth so
            # re-entries see "exhausted" and bail. Popping it caused an
            # infinite pass-1 loop when titles can't be untangled (e.g. all
            # contested titles match the same episode regardless of depth).
            if job.conflict_status and job.conflict_status.startswith(self._CONFLICT_NOTE_PREFIX):
                job.conflict_status = None
                await session.commit()
            return False

        # Re-match every distinct raw code in each tie (padded + unpadded), so
        # all contested titles are covered regardless of stored formatting.
        dispatched: list[int] = []
        for group in conflicts.values():
            for raw_code in {t.matched_episode for t in group if t.matched_episode}:
                result = await self._rematch_conflict(
                    job_id, raw_code, num_points=next_depth, min_vote_count=None
                )
                dispatched.extend(result.get("dispatched", []))

        if not dispatched:
            logger.warning(
                f"Job {job_id}: conflict re-match dispatched no titles (files missing); "
                f"handing {list(conflicts)} to review"
            )
            await self._clear_conflict_state(session, job)
            return False

        self._conflict_passes[job_id] = next_depth
        pass_no = ladder.index(next_depth) + 1
        job.conflict_status = f"Resolving episode conflicts — pass {pass_no} of {len(ladder)}"
        job.updated_at = datetime.now(UTC)
        # Hold the disc in MATCHING so the dashboard shows live re-match progress.
        if job.state != JobState.MATCHING:
            job.state = JobState.MATCHING
        await session.commit()
        await ws_manager.broadcast_job_update(
            job_id,
            JobState.MATCHING.value,
            content_type=job.content_type.value if job.content_type else None,
            detected_title=job.detected_title,
            detected_season=job.detected_season,
            conflict_status=job.conflict_status,
        )
        logger.info(
            f"Job {job_id}: deep re-match for conflicts {list(conflicts)} at "
            f"{next_depth} scan points (pass {pass_no}/{len(ladder)}, titles {dispatched})"
        )
        return True

    async def _maybe_escalate_reviews(
        self, session, job, titles, *, wrong_show_suspected: bool = False
    ) -> bool:
        """Deep re-match low-confidence REVIEW titles, escalating scan depth.

        Complements :meth:`_maybe_escalate_conflicts`: where that breaks same-
        episode ties, this gives a single low-confidence title (no collision)
        progressively deeper matcher passes before handing it to manual review.
        Depth-only ladder, capped at full coverage; a per-job pass counter
        (separate from the conflict counter) guarantees termination.

        ``wrong_show_suspected`` collapses the ladder to a SINGLE full-coverage
        confirming pass (see below) — the caller passes the pre-escalation
        :func:`_detect_wrong_show` verdict so a probable wrong-show disc doesn't
        burn the whole 25→50→full ladder against the wrong reference corpus.

        Returns ``True`` if a re-match was dispatched (job held in MATCHING; the
        caller should return and let completion re-entry pick it back up).
        """
        job_id = job.id
        # Episode re-matching only applies to TV.
        if job.content_type != ContentType.TV or self._rematch_title is None:
            await self._clear_review_state(session, job)
            return False

        review_titles = [t for t in titles if _is_rematchable_review(t)]
        if not review_titles:
            await self._clear_review_state(session, job)
            return False

        if wrong_show_suspected:
            # Suspected same-name wrong-show pick: every episode candidate matched
            # nothing AND a same-name twin is persisted. Don't climb the gradual
            # 25→50→full ladder against a corpus that's probably the wrong show —
            # that's up to 3 wasted ASR passes. Do ONE full-coverage confirming
            # pass instead: it's the strongest possible evidence, so a still-empty
            # result lets the wrong-show branch fire with confidence, while a
            # legitimately hard twin-having disc gets its densest matching shot in
            # a single pass rather than being flagged prematurely. The confirming
            # pass MUST be full coverage — a sparse tier matching nothing cannot
            # distinguish a wrong corpus from a disc that just needed denser
            # sampling, which is exactly the false positive we must avoid.
            full = _full_coverage_points(review_titles)
            ladder = [full] if full > 1 else []
        else:
            ladder = _conflict_scan_ladder(review_titles)
        last_depth = self._review_passes.get(job_id, 0)
        next_depth = next((d for d in ladder if d > last_depth), None)

        if next_depth is None:
            logger.info(
                f"Job {job_id}: review re-match exhausted at {last_depth} scan points; "
                f"{len(review_titles)} title(s) still unresolved — handing to review"
            )
            # Leave ``_review_passes[job_id]`` at last_depth so the next
            # ``check_job_completion`` re-entry sees the ladder as still
            # exhausted and bails. Popping it here lets pass 1 re-fire on the
            # very next recheck — an infinite loop when titles can never
            # match (e.g. precomputed cache gap). The counter is reset for
            # real via ``reset_conflict_passes`` on terminal-state transitions
            # and by the "no review titles" branch above when titles resolve.
            if job.conflict_status and job.conflict_status.startswith(self._REVIEW_NOTE_PREFIX):
                job.conflict_status = None
                await session.commit()
            return False

        dispatched: list[int] = []
        for t in review_titles:
            try:
                await self._rematch_title(
                    job_id,
                    t.id,
                    source_preference="engram",
                    num_points=next_depth,
                    min_vote_count=None,
                )
                dispatched.append(t.id)
            except Exception as e:
                # e.g. staging file missing (ValueError) — skip this title rather
                # than aborting the whole pass. Catch broadly (not BaseException,
                # so CancelledError still propagates).
                logger.warning(f"Review re-match: skipping title {t.id} (job {job_id}): {e}")

        if not dispatched:
            await self._clear_review_state(session, job)
            return False

        self._review_passes[job_id] = next_depth
        pass_no = ladder.index(next_depth) + 1
        job.conflict_status = (
            f"Deep re-matching low-confidence titles — pass {pass_no} of {len(ladder)}"
        )
        job.updated_at = datetime.now(UTC)
        if job.state != JobState.MATCHING:
            job.state = JobState.MATCHING
        await session.commit()
        await ws_manager.broadcast_job_update(
            job_id,
            JobState.MATCHING.value,
            content_type=job.content_type.value if job.content_type else None,
            detected_title=job.detected_title,
            detected_season=job.detected_season,
            conflict_status=job.conflict_status,
        )
        logger.info(
            f"Job {job_id}: deep review re-match at {next_depth} scan points "
            f"(pass {pass_no}/{len(ladder)}, titles {dispatched})"
        )
        return True

    async def _complete_tv_job(self, session, job) -> None:
        """Finalize a TV job: set progress, compute final_path, transition to COMPLETED."""
        from app.services.config_service import get_config as get_db_config

        job.progress_percent = 100.0
        job.error_message = None
        db_config = await get_db_config()
        _lib_path = _library_path_for_job(job, "tv")
        job.final_path = str(
            (_lib_path if _lib_path else Path(db_config.library_tv_path))
            / (job.detected_title or job.volume_label)
        )
        await self._state_machine.transition_to_completed(job, session)

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

        # Detect a same-name wrong-show pick up front (pure, no IO): EVERY episode
        # candidate matched nothing AND a same-name twin is persisted. Computed
        # before escalation so it can both (a) collapse review-escalation to a
        # single confirming pass and (b) route to the re-identify review once that
        # pass also comes back empty. Titles aren't mutated within this call, so
        # the verdict stays valid down to the branch below.
        wrong_show = _detect_wrong_show(job, titles)

        # Auto-resolve same-episode collisions before any manual review: deep
        # re-match contested titles at escalating scan density until the tie
        # breaks or the whole track has been transcribed. Runs BEFORE the
        # has_review short-circuit so a pass that leaves one title unmatched
        # doesn't abort escalation of the titles still colliding.
        if await self._maybe_escalate_conflicts(session, job, titles):
            return

        # Then give plain low-confidence reviews (no collision) escalating deep
        # re-match passes before surfacing them for manual assignment. When a
        # wrong-show pick is suspected, escalation collapses to ONE full-coverage
        # confirming pass instead of the full ladder — so a disc identified as the
        # wrong same-named show doesn't burn 3 ASR passes against the wrong corpus.
        if await self._maybe_escalate_reviews(
            session, job, titles, wrong_show_suspected=bool(wrong_show)
        ):
            return

        # Wrong-show detector (Frasier 1993 vs 2023): if the full-coverage
        # confirming pass STILL left every episode candidate unmatched, the disc
        # was identified as the wrong same-named show. Surface an actionable
        # re-identify review instead of the generic "assign episodes" message.
        if wrong_show:
            reason = _wrong_show_review_reason(wrong_show, job)
            logger.warning(f"Job {job_id}: wrong-show suspected — {reason}")
            # We only get here after _maybe_escalate_reviews exhausted its (now
            # single-pass) ladder, so _review_passes is pinned at full coverage.
            # REVIEW_NEEDED isn't terminal, so reset_conflict_passes never fires —
            # clear it now or a re-identify to the correct show would skip deep
            # re-match for its low-confidence titles.
            await self._clear_review_state(session, job)
            await self._state_machine.transition_to_review(job, session, reason=reason)
            return

        # Review takes priority: while ANY title still needs manual review, do
        # not organize anything — hold the whole disc in staging until it is
        # fully resolved. (finalize_disc_job also guards against conflicts it
        # creates mid-run; this avoids even starting finalization when the
        # matcher already flagged a title for review.)
        if has_review:
            await self._state_machine.transition_to_review(
                job,
                session,
                reason=f"{sum(1 for t in titles if t.state == TitleState.REVIEW)} title(s) need manual episode assignment",
            )
        elif has_matched:
            try:
                await self.finalize_disc_job(job_id)
            except Exception as e:
                logger.exception(f"Job {job_id}: _finalize_disc_job failed: {e}")
                await self._state_machine.transition_to_failed(
                    job, session, error_message=f"Finalization failed: {e}"
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
        from app.core.organizer import organize_tv_episode, organize_tv_extras, tv_organizer
        from app.services.episode_ordering_service import resolve_show_ordering

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
                    # Fallback only fires for titles WITHOUT a raw ranked_voting
                    # score in match_details — i.e. DiscDB-assigned (match_confidence
                    # is a hardcoded 0.99 sentinel, intentionally outranking engram)
                    # or filename-parsed titles. Engram matches always carry a raw
                    # details["score"] (> match_threshold), so a calibrated
                    # match_confidence never reaches this comparison.
                    score = t.match_confidence
                return score, vote_count, file_cov, runner_ups

            # Iterative conflict resolution (max 3 rounds)
            for round_num in range(3):
                candidates = {}
                for t in titles:
                    if t.state == TitleState.MATCHED and t.matched_episode:
                        # Extras carry the synthetic "extra" code, not a real
                        # episode slot — multiple extras coexist on one disc and
                        # never collide. Skip them here so two extras aren't
                        # mistaken for an episode conflict (which would bounce one
                        # to review); they organize into the season's Extras/
                        # folder in the loop below.
                        if t.matched_episode == "extra":
                            continue
                        # Normalize so padded/unpadded variants of the same episode
                        # ("S1E3" vs "S01E03") group together — otherwise a real
                        # collision is missed and both files organize to one path.
                        candidates.setdefault(
                            _normalize_episode_code(t.matched_episode), []
                        ).append(t)

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
                            # Canonicalize to match the normalized candidates keys
                            # (and to store a consistent code on reassignment).
                            alt_ep = _normalize_episode_code(ru["episode"])
                            current_claimants = candidates.get(alt_ep, [])
                            if not current_claimants:
                                loser.matched_episode = alt_ep
                                # Ranking uses raw score; the stored confidence is
                                # the calibrated value (falls back to raw for old
                                # match_details that predate calibration).
                                loser.match_confidence = ru.get("confidence", ru["score"])
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
                                    loser.match_confidence = ru.get("confidence", ru["score"])
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

            # Defer organization: if conflict resolution left any title needing
            # review, organize NOTHING — hold the entire disc in staging until
            # the user resolves the remaining title(s). Organizing only the
            # winners here would move files out from under an unresolved disc.
            if any(t.state == TitleState.REVIEW for t in titles):
                await session.commit()
                review_count = sum(1 for t in titles if t.state == TitleState.REVIEW)
                logger.info(
                    f"Job {job_id}: {review_count} title(s) need review — "
                    f"deferring organization until the disc is fully resolved"
                )
                await self._state_machine.transition_to_review(
                    job,
                    session,
                    reason=f"{review_count} title(s) need manual episode assignment",
                )
                return

            # Resolve the show's output ordering once for this sweep (#200).
            # Canonical (aired) for the common case; projected to the filename only.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
            _tmdb_year = job.tmdb_year

            # Organize all MATCHED winners
            extra_index = 1
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

                _lib_path = _library_path_for_job(job, "tv")
                if t.matched_episode == "extra":
                    # Mirror the review path (apply_review / process_matched_titles):
                    # extras go to the season's Extras/ folder with "Extra tNN"
                    # naming, NOT through organize_tv_episode (which rejects the
                    # synthetic "extra" code as an invalid episode format).
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=t.title_index,
                        tmdb_id=_tmdb_id_str,
                        year=_tmdb_year,
                    )
                elif _lib_path:
                    org_result = await asyncio.to_thread(
                        organize_tv_episode,
                        source_file,
                        job.detected_title or job.volume_label,
                        t.matched_episode,
                        _lib_path,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                        year=_tmdb_year,
                    )
                else:
                    org_result = await asyncio.to_thread(
                        tv_organizer.organize,
                        source_file,
                        job.detected_title,
                        t.matched_episode,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                        year=_tmdb_year,
                    )

                # Classification is independent of the file move: a failed extra
                # still IS an extra and must keep is_extra=True so the episode
                # re-match loop (_is_rematchable_review) skips it on the way to
                # REVIEW. Set it before the success branch, not inside it.
                t.is_extra = t.matched_episode == "extra"

                if org_result["success"]:
                    t.state = TitleState.COMPLETED
                    t.organized_from = source_file.name
                    t.organized_to = (
                        str(org_result.get("final_path")) if org_result.get("final_path") else None
                    )
                    # Audit which output ordering was applied (#200) — episodes only;
                    # matched_episode itself stays canonical. Extras bypass projection.
                    if t.matched_episode != "extra" and ordering != "aired":
                        t.episode_ordering = ordering
                        t.episode_group_id = ordering_group_id
                    # Advance the extras slot only on a confirmed write.
                    if t.matched_episode == "extra":
                        extra_index += 1
                elif org_result.get("error_code") == "FILE_EXISTS":
                    # The target already exists in the library — almost always a
                    # duplicate disc track or a mis-matched extra. Record a
                    # structured review reason (the Inspector renders a
                    # "File exists" badge + message from match_details.error) so
                    # the conflict surfaces in the UI instead of failing silently.
                    # Mirror the other organize paths (movie / process_matched /
                    # _finalize_tv) which already do this.
                    t.state = TitleState.REVIEW
                    t.match_details = _merge_match_details(
                        t.match_details,
                        {
                            "error": "file_exists",
                            "message": (
                                f"{org_result['error']} — likely a duplicate or extra; "
                                "reassign the episode or mark it as an Extra."
                            ),
                        },
                    )
                    logger.warning(f"Organization conflict for Title {t.id}: {org_result['error']}")
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
                _lib_path = _library_path_for_job(job, "tv")
                job.final_path = str(
                    (_lib_path if _lib_path else Path(db_config.library_tv_path))
                    / (job.detected_title or job.volume_label)
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

        from app.core.organizer import movie_organizer, organize_movie

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")

            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError("Title not found for this job")

            self._apply_decision_fields(title, episode_code, edition)
            session.add(title)
            await session.commit()

            # Discard on a movie is terminal; for TV fall through to the
            # all-resolved check that triggers organizing.
            if episode_code == "skip" and job.content_type == ContentType.MOVIE:
                return

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

                        _lib_path = _library_path_for_job(job, "movie")
                        if _lib_path:
                            org_result = await asyncio.to_thread(
                                organize_movie,
                                source_file,
                                final_title,
                                None,  # year
                                _lib_path,
                            )
                        else:
                            org_result = await asyncio.to_thread(
                                movie_organizer.organize,
                                source_file,
                                job.volume_label,
                                final_title,
                            )

                        if org_result["success"]:
                            title.state = TitleState.COMPLETED
                            title.organized_from = source_file.name
                            title.organized_to = str(org_result["main_file"])
                            session.add(title)
                            _org_from = title.organized_from
                            _org_to = title.organized_to
                            _out = title.output_filename
                            _state = title.state.value
                            await ws_manager.broadcast_title_update(
                                job_id,
                                title.id,
                                _state,
                                organized_from=_org_from,
                                organized_to=_org_to,
                                output_filename=_out,
                            )
                            job.progress_percent = 100.0
                            job.error_message = None
                            job.final_path = str(org_result["main_file"])
                            await self._state_machine.transition_to_completed(job, session)
                            logger.info(f"Organized movie: {org_result['main_file']}")
                        elif org_result.get("error_code") == "FILE_EXISTS":
                            title.state = TitleState.REVIEW
                            title.match_details = _merge_match_details(
                                title.match_details,
                                {
                                    "error": "file_exists",
                                    "message": str(org_result["error"]),
                                },
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

            # TV: organize everything once all titles are resolved.
            await self._finalize_tv_if_resolved(session, job)

    @staticmethod
    def _apply_decision_fields(
        title: DiscTitle, episode_code: str | None, edition: str | None
    ) -> None:
        """Apply a single review decision to a title's fields (no commit).

        Shared by the single-title ``apply_review`` and the batch path so both
        record decisions identically. Does not organize or change job state.
        """
        if episode_code:
            title.matched_episode = episode_code
            if episode_code == "extra":
                title.is_extra = True

        if edition:
            title.edition = edition

        title.match_confidence = 1.0  # User-confirmed

        # Discard: mark title as failed for both movie and TV.
        if episode_code == "skip":
            title.state = TitleState.FAILED

    async def _finalize_tv_if_resolved(self, session, job) -> None:
        """Organize a TV job's resolved titles in one pass once none are unresolved.

        Runs a single organization sweep with one monotonic ``extra_index``, so
        marking many extras at once names them uniquely (``Extra tNN``) instead
        of colliding on FILE_EXISTS across repeated single-title finalizes.
        """
        from app.core.organizer import (
            organize_tv_episode,
            organize_tv_extras,
            tv_organizer,
        )
        from app.services.episode_ordering_service import resolve_show_ordering

        job_id = job.id

        # Check for unresolved titles, excluding already-completed and failed
        # titles (they don't need review).
        result = await session.execute(
            select(DiscTitle).where(
                DiscTitle.job_id == job_id,
                DiscTitle.matched_episode.is_(None),
                DiscTitle.state.notin_([TitleState.COMPLETED, TitleState.FAILED]),
            )
        )
        unresolved = result.scalars().all()

        if unresolved:
            await session.commit()
            return

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

        # Resolve output ordering once for this sweep (#200); filename-only projection.
        ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
        _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
        _tmdb_year = job.tmdb_year

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
                    _lib_path = _library_path_for_job(job, "tv")
                    if disc_title.matched_episode == "extra":
                        org_result = await asyncio.to_thread(
                            organize_tv_extras,
                            source_file,
                            job.detected_title or job.volume_label,
                            job.detected_season or 1,
                            library_path=_lib_path,
                            disc_number=job.disc_number or 1,
                            extra_index=extra_index,
                            title_index=disc_title.title_index,
                            tmdb_id=_tmdb_id_str,
                            year=_tmdb_year,
                        )
                        extra_index += 1
                    else:
                        if _lib_path:
                            org_result = await asyncio.to_thread(
                                organize_tv_episode,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                _lib_path,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                                year=_tmdb_year,
                            )
                        else:
                            org_result = await asyncio.to_thread(
                                tv_organizer.organize,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                                year=_tmdb_year,
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
                        if disc_title.matched_episode != "extra" and ordering != "aired":
                            disc_title.episode_ordering = ordering
                            disc_title.episode_group_id = ordering_group_id
                        disc_title.state = TitleState.COMPLETED
                        logger.info(f"Organized: {org_result['final_path']}")
                    elif org_result.get("error_code") == "FILE_EXISTS":
                        conflict_count += 1
                        disc_title.state = TitleState.REVIEW
                        disc_title.match_details = _merge_match_details(
                            disc_title.match_details,
                            {
                                "error": "file_exists",
                                "message": str(org_result["error"]),
                            },
                        )
                        logger.warning(f"Organization conflict for TV: {org_result['error']}")
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
            await self._complete_tv_job(session, job)
        else:
            await self._state_machine.transition_to_failed(
                job,
                session,
                error_message="Failed to organize files",
            )

    async def apply_review_batch(self, job_id: int, decisions: list[dict]) -> None:
        """Apply several review decisions for a job in one atomic pass.

        ``decisions`` is a list of ``{"title_id", "episode_code", "edition"}``
        dicts. For TV, every decision is recorded first, then the job is
        finalized once — so bulk "mark as extra" organizes without FILE_EXISTS
        collisions. Movies stay single-title: each decision runs the proven
        ``apply_review`` path.
        """
        # An empty batch is a no-op — never let it sweep an already-resolved
        # disc into finalization.
        if not decisions:
            return

        # Re-verify state inside our own session: the HTTP route's check is a
        # stale snapshot by the time we run, and a concurrent save could have
        # moved the job out of review.
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError("Job not found")
            if job.state != JobState.REVIEW_NEEDED:
                raise ValueError("Job is not awaiting review")

            if job.content_type != ContentType.MOVIE:
                # TV: record every decision and finalize once, all in this
                # single session.
                for decision in decisions:
                    title = await session.get(DiscTitle, decision["title_id"])
                    if not title or title.job_id != job_id:
                        raise ValueError(f"Title {decision['title_id']} not found for this job")
                    self._apply_decision_fields(
                        title, decision.get("episode_code"), decision.get("edition")
                    )
                    session.add(title)
                await session.commit()
                await self._finalize_tv_if_resolved(session, job)
                return

        # Movies are single-title selection: apply via the proven single-title
        # path, stopping once the job leaves review — the first accepted version
        # finalizes the job, so later decisions would run on a non-review job.
        for decision in decisions:
            async with async_session() as session:
                current = await session.get(DiscJob, job_id)
                if not current or current.state != JobState.REVIEW_NEEDED:
                    break
            await self.apply_review(
                job_id,
                decision["title_id"],
                episode_code=decision.get("episode_code"),
                edition=decision.get("edition"),
            )

    async def process_matched_titles(self, job_id: int) -> dict:
        """Process all matched titles for a job without waiting for unresolved ones."""
        from app.core.organizer import organize_tv_episode, organize_tv_extras, tv_organizer
        from app.services.episode_ordering_service import resolve_show_ordering

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

            # Resolve output ordering once for this sweep (#200); filename-only projection.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
            _tmdb_year = job.tmdb_year

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

                _lib_path = _library_path_for_job(job, "tv")
                if disc_title.matched_episode == "extra":
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=disc_title.title_index,
                        tmdb_id=_tmdb_id_str,
                        year=_tmdb_year,
                    )
                    extra_index += 1
                else:
                    if _lib_path:
                        org_result = await asyncio.to_thread(
                            organize_tv_episode,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            _lib_path,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                            year=_tmdb_year,
                        )
                    else:
                        org_result = await asyncio.to_thread(
                            tv_organizer.organize,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                            year=_tmdb_year,
                        )

                if org_result["success"]:
                    success_count += 1
                    disc_title.organized_from = source_file.name
                    disc_title.organized_to = (
                        str(org_result.get("final_path")) if org_result.get("final_path") else None
                    )
                    disc_title.is_extra = disc_title.matched_episode == "extra"
                    if disc_title.matched_episode != "extra" and ordering != "aired":
                        disc_title.episode_ordering = ordering
                        disc_title.episode_group_id = ordering_group_id
                    disc_title.state = TitleState.COMPLETED
                    logger.info(f"Organized: {org_result['final_path']}")
                elif org_result.get("error_code") == "FILE_EXISTS":
                    conflict_count += 1
                    disc_title.state = TitleState.REVIEW
                    disc_title.match_details = _merge_match_details(
                        disc_title.match_details,
                        {
                            "error": "file_exists",
                            "message": str(org_result["error"]),
                        },
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
                await self._complete_tv_job(session, job)
            else:
                await session.commit()

        return {
            "organized": success_count,
            "conflicts": conflict_count,
            "unresolved": len(unresolved),
        }
