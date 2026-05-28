"""REST API routes for Engram."""

import asyncio
import io
import json
import logging
import platform
import re
import sys
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import settings
from app.core.discdb_exporter import get_makemkv_log_dir
from app.core.security import is_allowed_image_url, sanitize_log_value
from app.core.updater import UpdateError, UpdateStatus, update_checker
from app.database import get_session
from app.matcher.coverage_tracker import get_cache_status
from app.matcher.tmdb_client import fetch_season_episodes
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle

logger = logging.getLogger(__name__)

_SIM_DEFAULT_DRIVE = "/dev/sr0" if sys.platform != "win32" else "E:"

router = APIRouter(prefix="/api", tags=["jobs"])

_HOME_PATH = str(Path.home())


def _redact_home(p: object) -> str:
    """Replace the user's home directory in a path string with '~'."""
    return str(p).replace(_HOME_PATH, "~")


async def get_job_or_404(job_id: int, session: AsyncSession = Depends(get_session)) -> DiscJob:
    """FastAPI dependency that loads a job by ID or raises 404."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def require_debug() -> None:
    """FastAPI dependency that blocks an endpoint unless debug mode is enabled."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")


# Request/Response Models
class JobResponse(BaseModel):
    """Response model for a disc job."""

    id: int
    drive_id: str
    volume_label: str
    content_type: str
    state: str
    current_speed: str
    eta_seconds: int
    progress_percent: float
    current_title: int
    total_titles: int
    error_message: str | None
    detected_title: str | None = None
    detected_season: int | None = None
    subtitle_status: str | None = None
    subtitles_downloaded: int | None = None
    subtitles_total: int | None = None
    subtitles_failed: int | None = None
    review_reason: str | None = None
    # Transient auto-resolution note set during conflict / review escalation
    # (e.g. "Resolving episode conflicts — pass 2 of 3"). Cleared on resolution.
    conflict_status: str | None = None
    destination_mode: str = "library"
    created_at: datetime | str | None = None

    model_config = {"from_attributes": True}


class TitleResponse(BaseModel):
    """Response model for a disc title with match results."""

    id: int
    job_id: int
    title_index: int
    duration_seconds: int
    file_size_bytes: int
    chapter_count: int
    is_selected: bool
    output_filename: str | None
    matched_episode: str | None
    match_confidence: float
    match_details: str | None = None
    state: str = "pending"
    video_resolution: str | None = None
    edition: str | None = None
    conflict_resolution: str | None = None
    existing_file_path: str | None = None
    organized_from: str | None = None
    organized_to: str | None = None
    is_extra: bool = False
    match_source: str | None = None
    discdb_match_details: str | None = None
    discdb_flagged: bool = False
    discdb_flag_reason: str | None = None

    model_config = {"from_attributes": True}


class HistoryJobResponse(BaseModel):
    """Response model for a job in history view."""

    id: int
    volume_label: str
    content_type: str
    state: str
    detected_title: str | None = None
    detected_season: int | None = None
    error_message: str | None = None
    classification_source: str = "heuristic"
    classification_confidence: float = 0.0
    total_titles: int = 0
    content_hash: str | None = None
    discdb_slug: str | None = None
    disc_number: int = 1
    tmdb_id: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
    cleared_at: str | None = None


class JobDetailResponse(BaseModel):
    """Full job detail for history drill-down."""

    id: int
    volume_label: str
    drive_id: str
    content_type: str
    state: str
    detected_title: str | None = None
    detected_season: int | None = None
    disc_number: int = 1
    error_message: str | None = None
    review_reason: str | None = None
    # Transient auto-resolution note (e.g. "Resolving episode conflicts — pass 2 of 3"
    # / "Deep re-matching low-confidence titles — pass 1 of 3"). Set while the
    # finalization coordinator is auto-escalating; cleared on resolution.
    conflict_status: str | None = None
    # Classification
    classification_source: str = "heuristic"
    classification_confidence: float = 0.0
    tmdb_id: int | None = None
    tmdb_name: str | None = None
    is_ambiguous_movie: bool = False
    # TheDiscDB
    content_hash: str | None = None
    discdb_slug: str | None = None
    discdb_disc_slug: str | None = None
    discdb_mappings: list[dict] | None = None
    # Timestamps
    created_at: str | None = None
    completed_at: str | None = None
    cleared_at: str | None = None
    # Subtitles
    subtitle_status: str | None = None
    subtitles_downloaded: int = 0
    subtitles_total: int = 0
    subtitles_failed: int = 0
    # Paths
    staging_path: str | None = None
    final_path: str | None = None
    # Tracks
    titles: list[TitleResponse] = []


class StatsResponse(BaseModel):
    """Response model for job analytics."""

    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    tv_count: int = 0
    movie_count: int = 0
    total_titles_ripped: int = 0
    avg_processing_seconds: float | None = None
    common_errors: list[dict] = []
    recent_jobs: list[HistoryJobResponse] = []
    # Per-day completed-job counts over the last 14 days, oldest first.
    # Used by the History page throughput sparkline.
    daily_throughput: list[int] = []


class ConfigResponse(BaseModel):
    """Response model for configuration."""

    makemkv_path: str
    makemkv_key: str
    staging_path: str
    library_movies_path: str
    library_tv_path: str
    tmdb_api_key: str
    max_concurrent_matches: int
    ffmpeg_path: str
    conflict_resolution_default: str
    # Analyst thresholds
    analyst_movie_min_duration: int
    analyst_tv_duration_variance: int
    analyst_tv_min_cluster_size: int
    analyst_tv_min_duration: int
    analyst_tv_max_duration: int
    analyst_movie_dominance_threshold: float
    # Ripping coordination
    ripping_file_poll_interval: float
    ripping_stability_checks: int
    ripping_file_ready_timeout: float
    # Sentinel monitoring
    sentinel_poll_interval: float
    # Stale-job watchdog
    watchdog_enabled: bool
    watchdog_poll_seconds: int
    timeout_identifying_seconds: int
    timeout_ripping_seconds: int
    timeout_matching_seconds: int
    timeout_organizing_seconds: int
    # Staging cleanup
    staging_cleanup_policy: str
    staging_cleanup_days: int
    # Extras & naming
    extras_policy: str
    naming_season_format: str
    naming_episode_format: str
    naming_movie_format: str
    # AI identification
    ai_identification_enabled: bool
    ai_provider: str
    ai_api_key: str
    # Staging watcher
    staging_watch_enabled: bool
    # TheDiscDB
    discdb_enabled: bool
    # TheDiscDB Contributions
    discdb_contributions_enabled: bool
    discdb_contribution_tier: int
    discdb_export_path: str
    discdb_api_key_set: bool  # True if API key is configured (never expose the key)
    discdb_api_url: str
    # OpenSubtitles.com
    opensubtitles_api_key: str  # "***" if set
    opensubtitles_username: str
    opensubtitles_password: str  # "***" if set
    # Import watch folder
    import_watch_path: str | None = None
    import_destination_mode: str = "library"
    # Network access
    allow_lan_access: bool
    # Onboarding
    setup_complete: bool


class ConfigUpdate(BaseModel):
    """Request model for updating configuration."""

    makemkv_path: str | None = None
    makemkv_key: str | None = None
    staging_path: str | None = None
    library_movies_path: str | None = None
    library_tv_path: str | None = None
    tmdb_api_key: str | None = None
    max_concurrent_matches: int | None = None
    ffmpeg_path: str | None = None
    conflict_resolution_default: str | None = None
    # Analyst thresholds
    analyst_movie_min_duration: int | None = None
    analyst_tv_duration_variance: int | None = None
    analyst_tv_min_cluster_size: int | None = None
    analyst_tv_min_duration: int | None = None
    analyst_tv_max_duration: int | None = None
    analyst_movie_dominance_threshold: float | None = None
    # Ripping coordination
    ripping_file_poll_interval: float | None = None
    ripping_stability_checks: int | None = None
    ripping_file_ready_timeout: float | None = None
    # Sentinel monitoring
    sentinel_poll_interval: float | None = None
    # Stale-job watchdog
    watchdog_enabled: bool | None = None
    watchdog_poll_seconds: int | None = None
    timeout_identifying_seconds: int | None = None
    timeout_ripping_seconds: int | None = None
    timeout_matching_seconds: int | None = None
    timeout_organizing_seconds: int | None = None
    # Staging cleanup
    staging_cleanup_policy: str | None = None
    staging_cleanup_days: int | None = None
    # Extras & naming
    extras_policy: str | None = None
    naming_season_format: str | None = None
    naming_episode_format: str | None = None
    naming_movie_format: str | None = None
    # AI identification
    ai_identification_enabled: bool | None = None
    ai_provider: str | None = None
    ai_api_key: str | None = None
    # Staging watcher
    staging_watch_enabled: bool | None = None
    # TheDiscDB
    discdb_enabled: bool | None = None
    # TheDiscDB Contributions
    discdb_contributions_enabled: bool | None = None
    discdb_contribution_tier: int | None = None
    discdb_export_path: str | None = None
    discdb_api_key: str | None = None
    discdb_api_url: str | None = None
    # OpenSubtitles.com
    opensubtitles_api_key: str | None = None
    opensubtitles_username: str | None = None
    opensubtitles_password: str | None = None
    # Import watch folder
    import_watch_path: str | None = None
    import_destination_mode: str | None = None
    # Network access
    allow_lan_access: bool | None = None
    # Onboarding
    setup_complete: bool | None = None


class ReviewRequest(BaseModel):
    """Request model for submitting a review decision."""

    title_id: int
    episode_code: str | None = None  # e.g., "S01E01"
    edition: str | None = None  # e.g., "Extended", "Theatrical"


def _history_job_dict(j: DiscJob) -> dict:
    """Serialize a job into the HistoryJobResponse dict shape."""
    return {
        "id": j.id,
        "volume_label": j.volume_label,
        "content_type": j.content_type,
        "state": j.state,
        "detected_title": j.detected_title,
        "detected_season": j.detected_season,
        "error_message": j.error_message,
        "classification_source": j.classification_source,
        "classification_confidence": j.classification_confidence,
        "total_titles": j.total_titles,
        "content_hash": j.content_hash,
        "discdb_slug": j.discdb_slug,
        "disc_number": j.disc_number,
        "tmdb_id": j.tmdb_id,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "cleared_at": j.cleared_at.isoformat() if j.cleared_at else None,
    }


def _export_status(job: DiscJob) -> str:
    """Classify a job's TheDiscDB export status."""
    if job.submitted_at:
        return "submitted"
    if job.exported_at is None:
        return "pending"
    if job.exported_at.year == 1970:
        return "skipped"
    return "exported"


# Routes
@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[DiscJob]:
    """List active disc jobs (excludes cleared/archived jobs)."""
    result = await session.execute(
        select(DiscJob)
        .where(DiscJob.cleared_at.is_(None))
        .order_by(DiscJob.created_at.desc())
        .limit(10)
    )
    return list(result.scalars().all())


@router.get("/jobs/history", response_model=list[HistoryJobResponse])
async def get_job_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    content_type: str | None = None,
    state: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Get all completed/failed job history with pagination and filtering."""
    query = select(DiscJob).where(DiscJob.state.in_([JobState.COMPLETED, JobState.FAILED]))

    if content_type:
        query = query.where(DiscJob.content_type == content_type)
    if state:
        query = query.where(DiscJob.state == state)

    query = (
        query.order_by(DiscJob.completed_at.desc().nulls_last(), DiscJob.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await session.execute(query)
    jobs = result.scalars().all()

    return [_history_job_dict(j) for j in jobs]


@router.get("/jobs/stats", response_model=StatsResponse)
async def get_job_stats(session: AsyncSession = Depends(get_session)) -> dict:
    """Get job analytics and statistics."""
    all_jobs = await session.execute(select(DiscJob))
    jobs = list(all_jobs.scalars().all())

    completed = [j for j in jobs if j.state == JobState.COMPLETED]
    failed = [j for j in jobs if j.state == JobState.FAILED]
    tv_jobs = [j for j in jobs if j.content_type == ContentType.TV]
    movie_jobs = [j for j in jobs if j.content_type == ContentType.MOVIE]

    # Total titles ripped
    title_count_result = await session.execute(select(func.count(DiscTitle.id)))
    total_titles = title_count_result.scalar() or 0

    # Avg processing time (for completed jobs with both timestamps)
    processing_times = []
    for j in completed:
        if j.completed_at and j.created_at:
            delta = (j.completed_at - j.created_at).total_seconds()
            if delta > 0:
                processing_times.append(delta)

    avg_processing = sum(processing_times) / len(processing_times) if processing_times else None

    # Common errors
    error_counts: dict[str, int] = {}
    for j in failed:
        msg = j.error_message or "Unknown error"
        key = msg[:100]
        error_counts[key] = error_counts.get(key, 0) + 1

    common_errors = sorted(
        [{"message": k, "count": v} for k, v in error_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    # Recent 10 jobs
    recent_result = await session.execute(
        select(DiscJob).order_by(DiscJob.created_at.desc()).limit(10)
    )
    recent = recent_result.scalars().all()

    # 14-day throughput: count of completions per day in server-local time, oldest first.
    # Engram is self-hosted, so server local time matches the user's calendar day —
    # bucketing by UTC would mis-attribute evening completions in negative-UTC timezones
    # to the next day.
    now_local = datetime.now().astimezone()
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_throughput: list[int] = [0] * 14
    for j in completed:
        if not j.completed_at:
            continue
        completed_at = j.completed_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        local_completed = completed_at.astimezone()
        day_start = local_completed.replace(hour=0, minute=0, second=0, microsecond=0)
        days_ago = (today - day_start).days
        if 0 <= days_ago < 14:
            # Index 0 is 13 days ago, index 13 is today.
            daily_throughput[13 - days_ago] += 1

    return {
        "total_jobs": len(jobs),
        "completed_jobs": len(completed),
        "failed_jobs": len(failed),
        "tv_count": len(tv_jobs),
        "movie_count": len(movie_jobs),
        "total_titles_ripped": total_titles,
        "avg_processing_seconds": avg_processing,
        "common_errors": common_errors,
        "recent_jobs": [_history_job_dict(j) for j in recent],
        "daily_throughput": daily_throughput,
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job: DiscJob = Depends(get_job_or_404)) -> DiscJob:
    """Get a specific job by ID."""
    return job


@router.get("/jobs/{job_id}/titles", response_model=list[TitleResponse])
async def get_job_titles(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[DiscTitle]:
    """Get all titles with match results for a job."""
    result = await session.execute(
        select(DiscTitle).where(DiscTitle.job_id == job.id).order_by(DiscTitle.title_index)
    )
    return list(result.scalars().all())


_EPISODE_CODE_RE = re.compile(r"S(\d+)E(\d+)", re.IGNORECASE)


class RosterEpisode(BaseModel):
    """One episode slot in the season roster with cross-disc coverage."""

    episode_code: str
    episode_number: int
    name: str
    status: Literal["assigned", "duplicate", "missing", "off"]
    assigned_title_ids: list[int]


class SeasonRosterResponse(BaseModel):
    """Season episode list (code + name) plus per-episode coverage.

    ``status`` reflects the persisted state: ``assigned`` (one title),
    ``duplicate`` (two+ titles share the episode), ``missing`` (no title but
    inside the disc's covered range — a gap to fill) and ``off`` (outside the
    range, i.e. on another disc). The frontend recomputes status live as the
    user edits unsaved selections.
    """

    available: bool
    season_number: int | None = None
    show_id: int | None = None
    episodes: list[RosterEpisode] = []
    reason: str | None = None


@router.get("/jobs/{job_id}/season-roster", response_model=SeasonRosterResponse)
async def get_season_roster(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> SeasonRosterResponse:
    """Season episode list with per-episode coverage for the review UI."""
    if job.content_type != ContentType.TV:
        return SeasonRosterResponse(available=False, reason="Not a TV disc")
    if not job.tmdb_id or job.detected_season is None:
        return SeasonRosterResponse(
            available=False,
            season_number=job.detected_season,
            show_id=job.tmdb_id,
            reason="Show or season not identified yet",
        )

    season = job.detected_season
    from app.services.config_service import get_config

    config = await get_config()
    # fetch_season_episodes does a synchronous requests.get; run it off the
    # event loop so a slow TMDB call doesn't stall other requests / WS pushes.
    episodes_raw = await asyncio.to_thread(
        fetch_season_episodes, str(job.tmdb_id), season, config.tmdb_api_key
    )
    if not episodes_raw:
        return SeasonRosterResponse(
            available=False,
            season_number=season,
            show_id=job.tmdb_id,
            reason="Could not load season episodes from TMDB",
        )

    # Map this season's matched episodes → the title ids claiming them.
    result = await session.execute(
        select(DiscTitle).where(DiscTitle.job_id == job.id).order_by(DiscTitle.title_index)
    )
    assigned: dict[int, list[int]] = {}
    for title in result.scalars().all():
        if not title.matched_episode:
            continue
        match = _EPISODE_CODE_RE.search(title.matched_episode)
        if not match or int(match.group(1)) != season:
            continue
        assigned.setdefault(int(match.group(2)), []).append(title.id)

    present = sorted(assigned)
    lo, hi = (present[0], present[-1]) if present else (0, -1)

    episodes = [
        RosterEpisode(
            episode_code=f"S{season:02d}E{ep['episode_number']:02d}",
            episode_number=ep["episode_number"],
            name=ep.get("name") or "",
            status=(
                "duplicate"
                if len(assigned.get(ep["episode_number"], [])) > 1
                else "assigned"
                if len(assigned.get(ep["episode_number"], [])) == 1
                else "missing"
                if lo <= ep["episode_number"] <= hi
                else "off"
            ),
            assigned_title_ids=assigned.get(ep["episode_number"], []),
        )
        for ep in episodes_raw
    ]

    return SeasonRosterResponse(
        available=True,
        season_number=season,
        show_id=job.tmdb_id,
        episodes=episodes,
    )


async def build_job_detail(job: DiscJob, session: AsyncSession) -> dict:
    """Assemble the full job-detail dict (job fields + ordered titles).

    Shared by the history drill-down endpoint and the diagnostics bundle so
    the two never drift. ``titles`` are ORM objects; callers that need a
    JSON-safe form validate the result through ``JobDetailResponse``.
    """
    titles_result = await session.execute(
        select(DiscTitle).where(DiscTitle.job_id == job.id).order_by(DiscTitle.title_index)
    )
    titles = list(titles_result.scalars().all())

    # Parse persisted DiscDB mappings if available
    discdb_mappings = None
    if job.discdb_mappings_json:
        try:
            discdb_mappings = json.loads(job.discdb_mappings_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": job.id,
        "volume_label": job.volume_label,
        "drive_id": job.drive_id,
        "content_type": job.content_type,
        "state": job.state,
        "detected_title": job.detected_title,
        "detected_season": job.detected_season,
        "disc_number": job.disc_number,
        "error_message": job.error_message,
        "review_reason": job.review_reason,
        "conflict_status": job.conflict_status,
        "classification_source": job.classification_source,
        "classification_confidence": job.classification_confidence,
        "tmdb_id": job.tmdb_id,
        "tmdb_name": job.tmdb_name,
        "is_ambiguous_movie": job.is_ambiguous_movie,
        "content_hash": job.content_hash,
        "discdb_slug": job.discdb_slug,
        "discdb_disc_slug": job.discdb_disc_slug,
        "discdb_mappings": discdb_mappings,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "cleared_at": job.cleared_at.isoformat() if job.cleared_at else None,
        "subtitle_status": job.subtitle_status,
        "subtitles_downloaded": job.subtitles_downloaded,
        "subtitles_total": job.subtitles_total,
        "subtitles_failed": job.subtitles_failed,
        "staging_path": job.staging_path,
        "final_path": job.final_path,
        "titles": titles,
    }


@router.get("/jobs/{job_id}/detail", response_model=JobDetailResponse)
async def get_job_detail(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get full job detail with titles for history drill-down."""
    return await build_job_detail(job, session)


@router.post("/jobs/{job_id}/start")
async def start_job(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Start ripping a disc."""
    if job.state not in (JobState.IDLE, JobState.REVIEW_NEEDED):
        raise HTTPException(status_code=400, detail=f"Cannot start job in state: {job.state}")

    # Import here to avoid circular imports
    from app.services.job_manager import job_manager

    await job_manager.start_ripping(job.id)
    return {"status": "started", "job_id": job.id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Cancel a running job."""
    from app.services.job_manager import job_manager

    await job_manager.cancel_job(job.id)
    return {"status": "cancelled", "job_id": job.id}


@router.post("/jobs/{job_id}/advance")
async def advance_job(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Force a stuck job forward to its next resting state.

    Reconciles tracks still ripping/matching (ripped-but-unmatched → review,
    no-file → failed), then organizes whatever matched and lands the job in
    completed or review_needed. The manual counterpart to the stale-job watchdog.
    """
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    advanced = await job_manager.reconcile_and_advance(job.id, reason="manual advance")
    if not advanced:
        raise HTTPException(status_code=400, detail="Job could not be advanced")
    return {"status": "advanced", "job_id": job.id}


class SkipTitleRequest(BaseModel):
    """Request model for skipping a single stuck title."""

    target: Literal["review", "fail"] = "review"


@router.post("/jobs/{job_id}/titles/{title_id}/skip")
async def skip_title(
    title_id: int,
    req: SkipTitleRequest | None = None,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Skip a single track stuck in ripping/matching, without forcing the whole job."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    target = req.target if req else "review"

    from app.models.disc_job import TitleState
    from app.services.job_manager import job_manager

    target_state = TitleState.FAILED if target == "fail" else TitleState.REVIEW
    skipped = await job_manager.skip_title(job.id, title_id, target=target_state)
    if not skipped:
        raise HTTPException(
            status_code=400,
            detail="Title not found, not part of this job, or already resolved",
        )
    return {"status": "skipped", "job_id": job.id, "title_id": title_id, "target": target}


@router.post("/jobs/{job_id}/review")
async def submit_review(
    review: ReviewRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Submit a review decision for a title."""
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    await job_manager.apply_review(
        job.id, review.title_id, episode_code=review.episode_code, edition=review.edition
    )
    return {"status": "reviewed", "job_id": job.id}


class SetNameRequest(BaseModel):
    """Request model for setting a user-provided name on an unlabeled disc."""

    name: str
    content_type: str  # "tv" | "movie" | "unknown"
    season: int | None = None


@router.post("/jobs/{job_id}/set-name")
async def set_job_name(
    req: SetNameRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Set a user-provided name for a disc with unreadable volume label, then resume ripping."""
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting name input")

    from app.services.job_manager import job_manager

    await job_manager.set_name_and_resume(job.id, req.name, req.content_type, req.season)
    return {"status": "ok", "job_id": job.id}


class ReIdentifyRequest(BaseModel):
    """Request model for re-identifying a disc with corrected metadata."""

    title: str
    content_type: str  # "tv" | "movie"
    season: int | None = None
    tmdb_id: int | None = None


@router.post("/jobs/{job_id}/re-identify")
async def re_identify_job(
    req: ReIdentifyRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Re-identify a disc with user-corrected title, content type, and optional TMDB ID."""
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in review_needed state, currently: {job.state.value}",
        )

    from app.services.job_manager import job_manager

    await job_manager.re_identify_job(job.id, req.title, req.content_type, req.season, req.tmdb_id)
    return {"status": "re-identifying", "job_id": job.id}


@router.get("/tmdb/search")
async def tmdb_search(query: str = Query(..., min_length=1)) -> dict:
    """Search TMDB for TV shows and movies. Returns merged results."""
    from app.core.tmdb_classifier import _build_auth, _name_similarity
    from app.services.config_service import get_config

    config = await get_config()
    if not config.tmdb_api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    import requests

    headers, base_params = _build_auth(config.tmdb_api_key)
    results = []

    for endpoint, result_type in [
        ("https://api.themoviedb.org/3/search/tv", "tv"),
        ("https://api.themoviedb.org/3/search/movie", "movie"),
    ]:
        try:
            params = {**base_params, "query": query}
            resp = requests.get(endpoint, headers=headers, params=params, timeout=5)
            if resp.status_code == 200:
                for item in resp.json().get("results", [])[:5]:
                    name = item.get("name", item.get("title", ""))
                    year = item.get("first_air_date", item.get("release_date", ""))[:4]
                    results.append(
                        {
                            "tmdb_id": item["id"],
                            "name": name,
                            "type": result_type,
                            "year": year,
                            "poster_path": item.get("poster_path"),
                            "popularity": item.get("popularity", 0),
                        }
                    )
        except (requests.RequestException, ConnectionError, TimeoutError):
            pass

    # Sort by name similarity to query, then popularity
    results.sort(key=lambda r: (-_name_similarity(query, r["name"]), -r["popularity"]))

    return {"results": results[:10]}


@router.post("/jobs/{job_id}/retry-subtitles")
async def retry_subtitle_download(
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Retry subtitle download for a job that failed."""
    import asyncio

    if job.subtitle_status not in ("failed", None):
        raise HTTPException(
            status_code=400,
            detail=f"Subtitle status is '{job.subtitle_status}', retry only allowed for failed downloads",
        )

    if not job.detected_title or job.detected_season is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot retry subtitles: missing detected_title or detected_season",
        )

    # Trigger subtitle download
    from app.services.job_manager import job_manager

    asyncio.create_task(
        job_manager._download_subtitles(job.id, job.detected_title, job.detected_season)
    )

    return {"status": "retry_started", "job_id": job.id}


@router.post("/jobs/{job_id}/process-matched")
async def process_matched_titles(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Process all matched titles for a job without waiting for unresolved ones.

    The review UI submits each pending selection via POST /review before calling
    this endpoint. Resolving the last unresolved title makes apply_review finalize
    the job inline, so by the time this runs the job may already be ORGANIZING or
    COMPLETED. Treat those as benign no-ops rather than an error, otherwise the UI
    surfaces a spurious "not awaiting review" failure and stays stuck on the
    review screen even though apply_review already handled the titles.

    The ``organized``/``conflicts``/``unresolved`` counts report work done by THIS
    call. They are 0 here because apply_review did the organizing (and emitted its
    own broadcasts); this is not the cumulative total for the job.
    """
    if job.state == JobState.COMPLETED:
        return {
            "status": "already_finalized",
            "job_id": job.id,
            "organized": 0,
            "conflicts": 0,
            "unresolved": 0,
        }
    if job.state == JobState.ORGANIZING:
        # Mid-flight: another path is actively moving files. Don't start a second
        # organize pass, and report progress honestly rather than claiming the job
        # is finished.
        return {
            "status": "organizing",
            "job_id": job.id,
            "organized": 0,
            "conflicts": 0,
            "unresolved": 0,
        }
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    result = await job_manager.process_matched_titles(job.id)
    return {"status": "processed", "job_id": job.id, **result}


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Get current configuration from database.

    Sensitive fields (API keys) are redacted for security.
    """
    from app.services.config_service import get_config as get_db_config

    config = await get_db_config()
    return ConfigResponse(
        makemkv_path=config.makemkv_path,
        makemkv_key="***" if config.makemkv_key else "",  # Redacted
        staging_path=config.staging_path,
        library_movies_path=config.library_movies_path,
        library_tv_path=config.library_tv_path,
        tmdb_api_key="***" if config.tmdb_api_key else "",  # Redacted
        max_concurrent_matches=config.max_concurrent_matches,
        ffmpeg_path=config.ffmpeg_path,
        conflict_resolution_default=config.conflict_resolution_default,
        # Analyst thresholds
        analyst_movie_min_duration=config.analyst_movie_min_duration,
        analyst_tv_duration_variance=config.analyst_tv_duration_variance,
        analyst_tv_min_cluster_size=config.analyst_tv_min_cluster_size,
        analyst_tv_min_duration=config.analyst_tv_min_duration,
        analyst_tv_max_duration=config.analyst_tv_max_duration,
        analyst_movie_dominance_threshold=config.analyst_movie_dominance_threshold,
        # Ripping coordination
        ripping_file_poll_interval=config.ripping_file_poll_interval,
        ripping_stability_checks=config.ripping_stability_checks,
        ripping_file_ready_timeout=config.ripping_file_ready_timeout,
        # Sentinel monitoring
        sentinel_poll_interval=config.sentinel_poll_interval,
        # Stale-job watchdog
        watchdog_enabled=config.watchdog_enabled,
        watchdog_poll_seconds=config.watchdog_poll_seconds,
        timeout_identifying_seconds=config.timeout_identifying_seconds,
        timeout_ripping_seconds=config.timeout_ripping_seconds,
        timeout_matching_seconds=config.timeout_matching_seconds,
        timeout_organizing_seconds=config.timeout_organizing_seconds,
        # Staging cleanup
        staging_cleanup_policy=config.staging_cleanup_policy,
        staging_cleanup_days=config.staging_cleanup_days,
        # Extras & naming
        extras_policy=config.extras_policy,
        naming_season_format=config.naming_season_format,
        naming_episode_format=config.naming_episode_format,
        naming_movie_format=config.naming_movie_format,
        # AI identification
        ai_identification_enabled=config.ai_identification_enabled,
        ai_provider=config.ai_provider,
        ai_api_key="***" if config.ai_api_key else "",  # Redacted
        # Staging watcher
        staging_watch_enabled=config.staging_watch_enabled,
        # TheDiscDB
        discdb_enabled=config.discdb_enabled,
        # TheDiscDB Contributions
        discdb_contributions_enabled=config.discdb_contributions_enabled,
        discdb_contribution_tier=config.discdb_contribution_tier,
        discdb_export_path=config.discdb_export_path,
        discdb_api_key_set=bool(config.discdb_api_key),
        discdb_api_url=config.discdb_api_url,
        # OpenSubtitles.com
        opensubtitles_api_key="***" if config.opensubtitles_api_key else "",  # Redacted
        opensubtitles_username=config.opensubtitles_username,
        opensubtitles_password="***" if config.opensubtitles_password else "",  # Redacted
        # Import watch folder
        import_watch_path=config.import_watch_path,
        import_destination_mode=config.import_destination_mode,
        # Network access
        allow_lan_access=config.allow_lan_access,
        # Onboarding
        setup_complete=config.setup_complete,
    )


class NetworkInfoResponse(BaseModel):
    """Network reachability info for the dashboard's LAN access panel."""

    lan_access_enabled: bool  # persisted toggle (may differ from the live bind)
    active_lan_bound: bool  # True if the server actually bound a LAN address this session
    lan_ip: str | None  # host's primary LAN IP, if detectable
    port: int
    lan_url: str | None  # http://<lan_ip>:<port>, if an IP was detected


@router.get("/network/info", response_model=NetworkInfoResponse)
async def get_network_info(request: Request) -> NetworkInfoResponse:
    """Report whether the dashboard is reachable on the LAN and at what URL.

    ``active_lan_bound`` reflects the address uvicorn actually bound this
    session; when it disagrees with ``lan_access_enabled`` the UI shows a
    "restart to apply" notice.
    """
    from app.core.network import ALL_INTERFACES, get_lan_ip
    from app.services.config_service import get_config as get_db_config

    config = await get_db_config()
    bound_host = getattr(request.app.state, "bound_host", settings.host)
    port = getattr(request.app.state, "bound_port", settings.port)

    active_lan_bound = bound_host == ALL_INTERFACES
    lan_ip = await asyncio.to_thread(get_lan_ip)
    lan_url = f"http://{lan_ip}:{port}" if lan_ip else None

    return NetworkInfoResponse(
        lan_access_enabled=config.allow_lan_access,
        active_lan_bound=active_lan_bound,
        lan_ip=lan_ip,
        port=port,
        lan_url=lan_url,
    )


@router.put("/config")
async def update_config(config: ConfigUpdate) -> dict:
    """Update configuration and persist to database."""
    from app.services.config_service import update_config as update_db_config

    # Build kwargs from non-None fields
    # Allow None through for import_watch_path so users can clear the field
    _nullable_fields = {"import_watch_path"}
    update_data = {
        k: v for k, v in config.model_dump().items() if v is not None or k in _nullable_fields
    }

    # Validate naming format strings before persisting
    from app.core.organizer import (
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        validate_naming_format,
    )

    format_checks = [
        ("naming_season_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_episode_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_movie_format", ALLOWED_MOVIE_PLACEHOLDERS),
    ]
    for field, allowed in format_checks:
        if field in update_data:
            error = validate_naming_format(update_data[field], allowed)
            if error:
                raise HTTPException(status_code=400, detail=f"{field}: {error}")

    # Validate extras_policy
    if "extras_policy" in update_data:
        if update_data["extras_policy"] not in ("keep", "skip", "ask"):
            raise HTTPException(
                status_code=400,
                detail="extras_policy must be 'keep', 'skip', or 'ask'",
            )

    if update_data:
        await update_db_config(**update_data)

    # Reload the staging watcher if watch-related settings changed
    _watch_fields = {
        "staging_watch_enabled",
        "staging_path",
        "import_watch_path",
        "import_destination_mode",
    }
    if update_data.keys() & _watch_fields:
        from app.services.job_manager import job_manager

        await job_manager.reload_staging_watcher()

    return {"status": "updated", "persisted": True}


@router.get("/jobs/{job_id}/poster")
async def get_job_poster(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Get TMDB poster URL for a job."""
    if not job.detected_title:
        return {"poster_url": None}

    # Fetch poster from TMDB
    import requests

    from app.core.tmdb_classifier import _build_auth
    from app.matcher.tmdb_client import BASE_IMAGE_URL
    from app.services.config_service import get_config as get_db_config

    config = await get_db_config()
    api_key = config.tmdb_api_key

    if not api_key:
        return {"poster_url": None}

    # Determine endpoint based on content type
    if job.content_type == "movie":
        search_url = "https://api.themoviedb.org/3/search/movie"
    else:  # tv
        search_url = "https://api.themoviedb.org/3/search/tv"

    headers, params = _build_auth(api_key)
    params["query"] = job.detected_title

    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                poster_path = results[0]["poster_path"]
                return {"poster_url": f"{BASE_IMAGE_URL}{poster_path}"}
    except Exception as e:
        logger.warning(f"Error fetching poster: {e}")

    return {"poster_url": None}


@router.get("/drives")
async def list_drives() -> list[dict]:
    """List available optical drives."""
    from app.core.sentinel import get_optical_drives

    drives = get_optical_drives()
    return [{"drive_id": d, "status": "ready"} for d in drives]


@router.delete("/jobs/completed")
async def clear_completed_jobs(session: AsyncSession = Depends(get_session)) -> dict:
    """Soft-delete all completed and failed jobs (moves to history)."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(DiscJob).where(
            DiscJob.state.in_([JobState.COMPLETED, JobState.FAILED]),
            DiscJob.cleared_at.is_(None),
        )
    )
    jobs = list(result.scalars().all())

    if not jobs:
        return {"status": "cleared", "cleared_count": 0}

    for job in jobs:
        job.cleared_at = now

    await session.commit()
    return {"status": "cleared", "cleared_count": len(jobs)}


@router.delete("/jobs/{job_id}")
async def delete_job(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Soft-delete a single completed or failed job (moves to history)."""
    if job.state not in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Can only clear completed or failed jobs (current state: {job.state})",
        )

    job.cleared_at = datetime.now(UTC)
    await session.commit()

    return {"status": "cleared", "job_id": job.id}


@router.get("/fingerprint/contributions")
async def list_fingerprint_contributions(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    """Return locally-queued fingerprint contributions (Phase 1 audit log).

    Excludes the chromaprint blob body — only summarizes byte size — so the response
    stays manageable. Phase 2 adds filtering by upload status.
    """
    from app.models.fingerprint import FingerprintContribution

    result = await session.execute(
        select(FingerprintContribution)
        .order_by(FingerprintContribution.queued_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    items = [
        {
            "id": r.id,
            "queued_at": r.queued_at.isoformat() if r.queued_at else None,
            "title_id": r.title_id,
            "tmdb_id": r.tmdb_id,
            "season": r.season,
            "episode": r.episode,
            "match_confidence": r.match_confidence,
            "match_source": r.match_source,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "upload_attempts": r.upload_attempts,
            "blob_size_bytes": len(r.chromaprint_blob) if r.chromaprint_blob else 0,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


# --- Simulation Endpoints (debug mode only) ---


class SimulateDiscRequest(BaseModel):
    """Request model for simulating a disc insertion."""

    drive_id: str = _SIM_DEFAULT_DRIVE
    volume_label: str = "SIMULATED_DISC"
    content_type: str = "tv"
    detected_title: str | None = None
    detected_season: int | None = 1
    titles: list[dict] | None = None
    simulate_ripping: bool = True
    rip_speed_multiplier: int = 10
    force_review_needed: bool = False
    review_reason: str | None = None


@router.post("/simulate/insert-disc", dependencies=[Depends(require_debug)])
async def simulate_insert_disc(req: SimulateDiscRequest) -> dict:
    """Simulate a disc insertion. Only available in debug mode."""
    from app.services.job_manager import job_manager

    params = req.model_dump()
    if not params.get("force_review_needed") and params.get("detected_title") is None:
        params["detected_title"] = req.volume_label.replace("_", " ").title()
    if params.get("titles") is None:
        del params["titles"]

    job_id = await job_manager.simulate_disc_insert(params)
    return {"status": "simulated", "job_id": job_id}


@router.post("/simulate/remove-disc", dependencies=[Depends(require_debug)])
async def simulate_remove_disc(drive_id: str = _SIM_DEFAULT_DRIVE) -> dict:
    """Simulate a disc removal. Only available in debug mode."""
    from app.api.websocket import manager as ws_manager
    from app.services.job_manager import job_manager

    await ws_manager.broadcast_drive_event(drive_id, "removed")
    await job_manager._cancel_jobs_for_drive(drive_id)
    return {"status": "removed", "drive_id": drive_id}


@router.post("/simulate/trigger-real-scan", dependencies=[Depends(require_debug)])
async def trigger_real_scan(drive_id: str = _SIM_DEFAULT_DRIVE) -> dict:
    """Trigger a real disc scan and rip pipeline. Only available in debug mode.

    This fires the same event as a physical disc insertion, using the real
    MakeMKV extractor to scan and rip the disc currently in the drive.
    """
    from app.core.sentinel import get_volume_label, is_disc_present
    from app.services.job_manager import job_manager

    if not is_disc_present(drive_id):
        raise HTTPException(status_code=400, detail=f"No disc found in drive {drive_id}")

    label = get_volume_label(drive_id)
    await job_manager._on_drive_event(drive_id, "inserted", label)
    return {"status": "triggered", "drive_id": drive_id, "volume_label": label}


@router.post("/simulate/advance-job/{job_id}", dependencies=[Depends(require_debug)])
async def simulate_advance_job(job_id: int) -> dict:
    """Manually advance a job to the next state. Only available in debug mode."""
    from app.services.job_manager import job_manager

    try:
        new_state = await job_manager.advance_job(job_id)
        return {"status": "advanced", "job_id": job_id, "new_state": new_state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.delete("/simulate/reset-all-jobs", dependencies=[Depends(require_debug)])
async def reset_all_jobs(session: AsyncSession = Depends(get_session)) -> dict:
    """Delete ALL jobs and titles regardless of state. Debug mode only."""
    from sqlalchemy import delete

    await session.execute(delete(DiscTitle))
    result = await session.execute(delete(DiscJob))
    await session.commit()
    return {"status": "reset", "deleted_count": result.rowcount}


@router.post("/simulate/insert-disc-from-staging", dependencies=[Depends(require_debug)])
async def simulate_insert_disc_from_staging(
    staging_path: str,
    volume_label: str = "REAL_DATA_DISC",
    content_type: str = "tv",
    detected_title: str | None = None,
    detected_season: int = 1,
    rip_speed_multiplier: int = 1,
) -> dict:
    """
    Simulate disc insertion using real MKV files from a staging directory.
    Simulates ripping per track with progress updates.
    Only available in debug mode.
    """
    import asyncio
    from pathlib import Path

    from app.services.job_manager import job_manager

    staging_dir = Path(staging_path)
    if not staging_dir.exists():
        raise HTTPException(status_code=404, detail=f"Staging directory not found: {staging_path}")

    # Find all MKV files
    mkv_files = sorted(staging_dir.glob("*.mkv"))
    if not mkv_files:
        raise HTTPException(status_code=404, detail=f"No MKV files found in {staging_path}")

    # Get metadata for each file using async ffprobe
    titles = []
    for idx, mkv_file in enumerate(mkv_files):
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
            duration = float(stdout.decode().strip()) if stdout.decode().strip() else 1800
        except (TimeoutError, OSError, ValueError) as e:
            logger.debug(f"Could not determine MKV duration via ffprobe: {e}")
            duration = 1800  # Default 30 minutes

        file_size = mkv_file.stat().st_size

        titles.append(
            {
                "title_index": idx,
                "duration_seconds": int(duration),
                "file_size_bytes": file_size,
                "chapter_count": 5,
                "output_filename": mkv_file.name,
            }
        )

    # Create the simulation
    params = {
        "drive_id": _SIM_DEFAULT_DRIVE,
        "volume_label": volume_label,
        "content_type": content_type,
        "detected_title": detected_title or volume_label.replace("_", " ").title(),
        "detected_season": detected_season,
        "titles": titles,
        "simulate_ripping": True,
        "rip_speed_multiplier": rip_speed_multiplier,
        "staging_path": str(staging_dir),
    }

    job_id = await job_manager.simulate_disc_insert_realistic(params)
    return {"status": "simulated", "job_id": job_id, "titles_count": len(titles)}


class StagingImportRequest(BaseModel):
    """Request model for importing pre-ripped MKV files from a staging directory."""

    staging_path: str
    volume_label: str = ""
    content_type: str = "unknown"
    detected_title: str | None = None
    detected_season: int | None = None


@router.post("/staging/import")
async def import_from_staging(request: StagingImportRequest) -> dict:
    """Import pre-ripped MKV files from a staging directory.

    Creates a real job that skips the ripping phase and proceeds
    directly to identification, matching, and organization.
    Available in all modes (no DEBUG required).
    """
    from app.services.job_manager import job_manager

    staging_dir = Path(request.staging_path)
    if not staging_dir.exists():
        raise HTTPException(
            status_code=404, detail=f"Staging directory not found: {request.staging_path}"
        )

    mkv_files = sorted(staging_dir.glob("*.mkv"))
    if not mkv_files:
        raise HTTPException(status_code=404, detail=f"No MKV files found in {request.staging_path}")

    job_id = await job_manager.create_job_from_staging(
        staging_path=str(staging_dir),
        volume_label=request.volume_label,
        content_type=request.content_type,
        detected_title=request.detected_title,
        detected_season=request.detected_season,
    )

    return {"status": "created", "job_id": job_id, "titles_count": len(mkv_files)}


@router.get("/staging/orphaned")
async def get_orphaned_staging(session: AsyncSession = Depends(get_session)) -> dict:
    """Find staging directories that don't belong to active jobs."""
    from pathlib import Path

    from app.services.config_service import get_config

    config = await get_config()
    staging_root = Path(config.staging_path)

    if not staging_root.exists():
        return {"directories": [], "total_size": 0}

    # Get all job_* subdirectories
    job_dirs = [d for d in staging_root.iterdir() if d.is_dir() and d.name.startswith("job_")]

    # Get active staging paths from database
    result = await session.execute(select(DiscJob.staging_path))
    active_staging = {Path(p) for p in result.scalars() if p}

    orphaned = []
    total_size = 0

    for d in job_dirs:
        if d not in active_staging:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            orphaned.append({"path": str(d), "size_bytes": size, "name": d.name})
            total_size += size

    return {"directories": orphaned, "total_size": total_size}


@router.delete("/staging/orphaned")
async def cleanup_orphaned_staging(session: AsyncSession = Depends(get_session)) -> dict:
    """Delete all orphaned staging directories."""
    import shutil

    orphaned_info = await get_orphaned_staging(session)

    deleted_count = 0
    for item in orphaned_info["directories"]:
        try:
            shutil.rmtree(item["path"])
            deleted_count += 1
            logger.info(f"Deleted orphaned staging: {item['path']}")
        except Exception as e:
            logger.error(f"Failed to delete {item['path']}: {e}")

    return {"deleted_count": deleted_count, "reclaimed_bytes": orphaned_info["total_size"]}


@router.get("/staging/size")
async def get_staging_size() -> dict:
    """Get total staging directory size and per-job breakdown."""
    from pathlib import Path

    from app.services.config_service import get_config

    config = await get_config()
    staging_root = Path(config.staging_path)

    if not staging_root.exists():
        return {"total_size": 0, "jobs": [], "policy": config.staging_cleanup_policy}

    jobs = []
    total_size = 0

    for d in staging_root.iterdir():
        if not d.is_dir():
            continue
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        jobs.append({"path": str(d), "name": d.name, "size_bytes": size})
        total_size += size

    return {
        "total_size": total_size,
        "jobs": jobs,
        "policy": config.staging_cleanup_policy,
        "cleanup_days": config.staging_cleanup_days,
    }


@router.delete("/staging/job/{job_id}")
async def cleanup_job_staging(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Delete staging files for a specific job."""
    import shutil

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Safety: only allow cleanup for terminal jobs
    if job.state not in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot clean staging for active job (state: {job.state.value})",
        )

    if not job.staging_path:
        return {"deleted": False, "reason": "No staging path set"}

    from pathlib import Path

    staging_path = Path(job.staging_path)
    if not staging_path.exists():
        return {"deleted": False, "reason": "Staging directory already removed"}

    size = sum(f.stat().st_size for f in staging_path.rglob("*") if f.is_file())

    try:
        shutil.rmtree(staging_path)
        logger.info(f"Manually cleaned staging for job {job_id}: {staging_path}")
        return {"deleted": True, "reclaimed_bytes": size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete staging: {e}") from e


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

_SENSITIVE_RE = re.compile(
    r"(eyJ[A-Za-z0-9_-]{20,})"  # JWT tokens
    r"|(?<=key=)[^\s,;'\"]{8,}"  # key=VALUE
    r"|(?<=token=)[^\s,;'\"]{8,}",  # token=VALUE
    re.IGNORECASE,
)


def _sanitize_line(line: str) -> str:
    """Redact sensitive data from a single log line."""
    line = line.replace(_HOME_PATH, "~")
    return _SENSITIVE_RE.sub("***REDACTED***", line)


async def _collect_environment() -> dict:
    """Gather app/OS/tool versions and a redacted config snapshot.

    Shared by the diagnostics report and the diagnostics bundle. Tool
    detection spawns subprocesses (up to a 10 s timeout each), so the two
    probes run concurrently off the event loop.
    """
    from app import __version__
    from app.api.validation import detect_ffmpeg, detect_makemkv
    from app.services.config_service import get_config

    config = await get_config()
    mk, ff = await asyncio.gather(
        asyncio.to_thread(detect_makemkv),
        asyncio.to_thread(detect_ffmpeg),
    )
    return {
        "app_version": __version__,
        "python_version": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()}",
        "makemkv_version": mk.version if mk.found else (mk.error or "not found"),
        "ffmpeg_version": ff.version if ff.found else (ff.error or "not found"),
        "config": {
            "staging_path": _redact_home(config.staging_path),
            "library_movies_path": _redact_home(config.library_movies_path),
            "library_tv_path": _redact_home(config.library_tv_path),
            "max_concurrent_matches": config.max_concurrent_matches,
            "conflict_resolution_default": config.conflict_resolution_default,
            "extras_policy": config.extras_policy,
            "discdb_enabled": config.discdb_enabled,
        },
    }


def _build_markdown_summary(env: dict, job_summary: dict | None, recent_errors: list[str]) -> str:
    """Render the factual core of a bug report (env + job context + errors).

    Used verbatim by the GitHub issue body and as the opening of the
    downloadable bundle's ``report.md`` so the two never diverge.
    """
    parts = [
        "## Bug Report",
        "",
        f"**Engram version**: {env['app_version']}",
        f"**OS**: {env['os']}",
        f"**Python**: {env['python_version']}",
        f"**MakeMKV**: {env['makemkv_version']}",
        f"**FFmpeg**: {env['ffmpeg_version']}",
        "",
    ]
    if job_summary:
        parts += [
            "### Job Context",
            f"- **ID**: {job_summary['id']}",
            f"- **Label**: {job_summary['volume_label']}",
            f"- **Type**: {job_summary['content_type']}",
            f"- **State**: {job_summary['state']}",
        ]
        if job_summary["error"]:
            parts.append(f"- **Error**: {job_summary['error']}")
        parts.append("")
    if recent_errors:
        parts += ["### Recent Errors", "```"]
        parts += recent_errors[-10:]
        parts += ["```", ""]
    return "\n".join(parts)


def _read_recent_error_lines(limit: int = 20, log_path: Path | None = None) -> list[str]:
    """Return the last ``limit`` sanitized ERROR/CRITICAL lines from the log.

    The global fallback when a job has no job-tagged lines (e.g. it ran
    before job-tagged logging existed).
    """
    log_path = log_path or (Path.home() / ".engram" / "engram.log")
    if not log_path.exists():
        return []
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        error_lines = [ln for ln in raw.splitlines() if "ERROR" in ln or "CRITICAL" in ln]
        return [_sanitize_line(line) for line in error_lines[-limit:]]
    except Exception:
        logger.warning(f"Failed to read log file for bug report: {log_path}", exc_info=True)
        return ["(could not read log file)"]


def _sanitize_obj(obj: object) -> object:
    """Recursively redact every string in a nested dict/list structure.

    Reuses ``_sanitize_line`` (home path + secret patterns) so paths, volume
    labels, detected titles, and the ``match_details``/``discdb_match_details``
    JSON blobs are all scrubbed before leaving the machine.
    """
    if isinstance(obj, str):
        return _sanitize_line(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_obj(v) for v in obj]
    return obj


def _read_job_tagged_logs(
    job_id: int, limit: int = 2000, log_path: Path | None = None
) -> tuple[list[str], bool]:
    """Return ``(lines, is_fallback)`` for a job's log lines.

    Filters the global log to lines carrying this job's ``| job=<id> |`` tag.
    Jobs that ran before job-tagged logging existed have no tagged lines — in
    that case fall back to the recent global ERROR/CRITICAL tail and flag it.
    """
    log_path = log_path or (Path.home() / ".engram" / "engram.log")
    # No file / unreadable → mark as fallback (not job-specific) so the bundle
    # never labels empty/placeholder content as "log lines for this job".
    if not log_path.exists():
        return ([], True)
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        logger.warning(f"Failed to read log file for bundle: {log_path}", exc_info=True)
        return (["(could not read log file)"], True)
    token = f"| job={job_id} |"
    matched = [ln for ln in raw.splitlines() if token in ln]
    if matched:
        return ([_sanitize_line(ln) for ln in matched[-limit:]], False)
    return (_read_recent_error_lines(log_path=log_path), True)


def _cap_text(text: str, max_chars: int = 200_000) -> str:
    """Keep the tail of an oversized log so the bundle stays small."""
    if len(text) <= max_chars:
        return text
    return f"... (truncated; showing last {max_chars} chars)\n" + text[-max_chars:]


def _build_track_table(detail: dict) -> str:
    titles = detail.get("titles") or []
    if not titles:
        return "### Tracks\n\n_No tracks recorded._"
    rows = [
        "### Tracks",
        "",
        "| # | Duration (s) | Chapters | Resolution | State | Episode | Conf | Source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in titles:
        rows.append(
            f"| {t.get('title_index')} | {t.get('duration_seconds')} "
            f"| {t.get('chapter_count')} | {t.get('video_resolution') or '-'} "
            f"| {t.get('state')} | {t.get('matched_episode') or '-'} "
            f"| {t.get('match_confidence')} | {t.get('match_source') or '-'} |"
        )
    return "\n".join(rows)


def _build_cache_table(cache_status: dict) -> str:
    rows = [
        "### Subtitle Cache / Coverage",
        "",
        f"- **TMDB show metadata cached**: {cache_status['tmdb_show_cached']}",
        f"- **TMDB season metadata cached**: {cache_status['tmdb_season_cached']}",
        "",
    ]
    coverage = cache_status.get("coverage") or []
    if not coverage:
        rows.append("_No subtitle-coverage records for this show._")
        return "\n".join(rows)
    rows += ["| Season | Covered | Total | Ratio |", "|---|---|---|---|"]
    for row in coverage:
        ratio = row.get("coverage_ratio")
        ratio_str = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else str(ratio)
        rows.append(
            f"| {row.get('season')} | {row.get('covered_episodes')} "
            f"| {row.get('total_episodes')} | {ratio_str} |"
        )
    return "\n".join(rows)


def _build_bundle_markdown(
    env: dict,
    job_summary: dict,
    detail: dict,
    cache_status: dict,
    log_is_fallback: bool,
) -> str:
    parts = [
        _build_markdown_summary(env, job_summary, []),
        "### Configuration",
        *[f"- **{k}**: {v}" for k, v in env["config"].items()],
        "",
        _build_track_table(detail),
        "",
        _build_cache_table(cache_status),
        "",
        "### Attached Files",
        "- `job-detail.json` — full job + per-track detail",
        (
            "- `job-logs.txt` — recent global errors (this job predates job-tagged logging)"
            if log_is_fallback
            else "- `job-logs.txt` — log lines for this job"
        ),
        "- `scan.log` / `rip.log` — raw MakeMKV output (when present)",
    ]
    return "\n".join(parts)


@router.get("/diagnostics/logs")
async def get_recent_logs(
    lines: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Return the last N lines from the engram log file, sanitized."""
    log_path = Path.home() / ".engram" / "engram.log"
    if not log_path.exists():
        return {"lines": [], "log_path": _redact_home(log_path)}

    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        tail = raw.splitlines()[-lines:]
        sanitized = [_sanitize_line(line) for line in tail]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log: {e}") from e

    return {
        "lines": sanitized,
        "log_path": _redact_home(log_path),
    }


@router.get("/diagnostics/report")
async def generate_bug_report(
    job_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a sanitized bug report with optional job context."""
    env = await _collect_environment()

    # --- Job summary (optional) ---
    job_summary = None
    if job_id is not None:
        job = await session.get(DiscJob, job_id)
        if job:
            job_summary = {
                "id": job.id,
                # Sanitized: these flow into the GitHub issue title/body.
                "volume_label": _sanitize_line(job.volume_label or ""),
                "content_type": job.content_type.value if job.content_type else "unknown",
                "state": job.state.value if job.state else "unknown",
                "error": _sanitize_line(job.error_message) if job.error_message else None,
                "created_at": str(job.created_at) if job.created_at else None,
                "completed_at": str(job.completed_at) if job.completed_at else None,
            }

    recent_errors = _read_recent_error_lines()

    report = {
        "app_version": env["app_version"],
        "python_version": env["python_version"],
        "os": env["os"],
        "makemkv_version": env["makemkv_version"],
        "ffmpeg_version": env["ffmpeg_version"],
        "job": job_summary,
        "recent_errors": recent_errors,
        "config": env["config"],
    }

    # --- GitHub issue body (kept small; full detail lives in the bundle) ---
    body_parts = [
        _build_markdown_summary(env, job_summary, recent_errors),
        "### Steps to Reproduce",
        "1. ",
        "",
        "### Expected Behavior",
        "",
        "",
        "### Actual Behavior",
        "",
    ]
    issue_body = "\n".join(body_parts)
    title = "[Bug] " + (
        f"Job {job_id} failed in {job_summary['state']}" if job_summary else "Describe the issue"
    )
    github_url = (
        f"https://github.com/Jsakkos/engram/issues/new"
        f"?title={quote(title)}&body={quote(issue_body)}"
    )

    report["github_url"] = github_url
    report["markdown"] = issue_body

    # Bundle-preview hints so the modal can describe the downloadable bundle
    # without fetching it. Only meaningful for an existing job.
    if job_summary is not None:
        cache_status = await asyncio.to_thread(get_cache_status, job.tmdb_id, job.detected_season)
        report["bundle_available"] = True
        report["has_scan_log"] = (get_makemkv_log_dir(job_id) / "scan.log").exists()
        report["coverage_seasons"] = len(cache_status["coverage"])
        report["tmdb_cached"] = cache_status["tmdb_show_cached"]
    else:
        report["bundle_available"] = False

    return report


@router.get("/diagnostics/report/{job_id}/bundle")
async def download_bug_report_bundle(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Download a sanitized diagnostic bundle (.zip) for a single job.

    Bundles the full job + per-track detail, the job's tagged log lines, the
    subtitle cache/coverage status for the series, and the raw MakeMKV scan
    logs — everything run through the same sanitization as the inline report.
    """
    job = await session.get(DiscJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    detail = await build_job_detail(job, session)
    detail_json = JobDetailResponse.model_validate(detail).model_dump(mode="json")
    safe_detail = _sanitize_obj(detail_json)

    env = await _collect_environment()
    cache_status = await asyncio.to_thread(get_cache_status, job.tmdb_id, job.detected_season)
    log_lines, log_is_fallback = await asyncio.to_thread(_read_job_tagged_logs, job_id)

    job_summary = {
        "id": job.id,
        "volume_label": _sanitize_line(job.volume_label or ""),
        "content_type": job.content_type.value if job.content_type else "unknown",
        "state": job.state.value if job.state else "unknown",
        "error": _sanitize_line(job.error_message) if job.error_message else None,
    }
    report_md = _build_bundle_markdown(env, job_summary, safe_detail, cache_status, log_is_fallback)

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report.md", report_md)
            zf.writestr("job-detail.json", json.dumps(safe_detail, indent=2))
            zf.writestr("job-logs.txt", "\n".join(log_lines) if log_lines else "(no logs)")
            log_dir = get_makemkv_log_dir(job_id)
            for name in ("scan.log", "rip.log"):
                p = log_dir / name
                if not p.exists():
                    continue
                try:
                    raw = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                sanitized = "\n".join(_sanitize_line(ln) for ln in raw.splitlines())
                zf.writestr(name, _cap_text(sanitized))
        return buf.getvalue()

    data = await asyncio.to_thread(_build_zip)
    filename = f"engram-bug-report-job-{job_id}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── TheDiscDB Contribution Endpoints ─────────────────────────────────────


class ContributionJobResponse(BaseModel):
    """Response model for a job in the contributions list."""

    id: int
    volume_label: str
    content_type: str
    detected_title: str | None
    detected_season: int | None
    content_hash: str | None
    completed_at: datetime | None
    export_status: str  # "pending", "exported", "skipped", "submitted"
    submitted_at: datetime | None = None
    contribute_url: str | None = None
    release_group_id: str | None = None
    upc_code: str | None = None
    asin: str | None = None
    release_date: str | None = None


class ContributionStatsResponse(BaseModel):
    """Stats for the contribution nav badge."""

    pending: int
    exported: int
    skipped: int
    submitted: int


class EnhanceRequest(BaseModel):
    """Request model for tier-3 contribution enhancement."""

    upc_code: str | None = None
    asin: str | None = None
    release_date: str | None = None
    extra_descriptions: dict[int, str] | None = None  # title_id -> description


class FlagDiscDBRequest(BaseModel):
    """Request model for flagging incorrect DiscDB data on a title."""

    title_id: int
    reason: str
    details: str | None = None


class RematchRequest(BaseModel):
    """Request model for re-matching titles."""

    source_preference: Literal["discdb", "engram"] | None = None
    deep: bool = False


class ReassignRequest(BaseModel):
    """Request model for manual episode reassignment."""

    episode_code: str
    edition: str | None = None
    source: str = "user"


class ReleaseGroupRequest(BaseModel):
    """Request model for creating a release group."""

    job_ids: list[int]


class ReleaseGroupAssignRequest(BaseModel):
    """Request model for assigning a job to a release group."""

    release_group_id: str | None = None


@router.get("/contributions", response_model=list[ContributionJobResponse])
async def list_contributions(session: AsyncSession = Depends(get_session)):
    """List completed jobs with their export status."""
    result = await session.execute(
        select(DiscJob)
        .where(DiscJob.state == JobState.COMPLETED)
        .order_by(DiscJob.completed_at.desc())
    )
    jobs = result.scalars().all()

    responses = []
    for job in jobs:
        status = _export_status(job)

        # Use stored contribute URL, or construct from submission ID as fallback
        contribute_url = job.discdb_contribute_url
        if not contribute_url and job.discdb_submission_id:
            contribute_url = f"https://thediscdb.com/contribute/engram/{job.discdb_submission_id}"

        responses.append(
            ContributionJobResponse(
                id=job.id,
                volume_label=job.volume_label,
                content_type=job.content_type,
                detected_title=job.detected_title,
                detected_season=job.detected_season,
                content_hash=job.content_hash,
                completed_at=job.completed_at,
                export_status=status,
                submitted_at=job.submitted_at,
                contribute_url=contribute_url,
                release_group_id=job.release_group_id,
                upc_code=job.upc_code,
                asin=job.asin,
                release_date=job.release_date,
            )
        )
    return responses


@router.get("/contributions/stats", response_model=ContributionStatsResponse)
async def contribution_stats(session: AsyncSession = Depends(get_session)):
    """Get contribution counts for nav badge."""
    result = await session.execute(select(DiscJob).where(DiscJob.state == JobState.COMPLETED))
    jobs = result.scalars().all()

    counts = Counter(_export_status(job) for job in jobs)

    return ContributionStatsResponse(
        pending=counts["pending"],
        exported=counts["exported"],
        skipped=counts["skipped"],
        submitted=counts["submitted"],
    )


@router.post("/contributions/{job_id}/export")
async def export_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger export for a specific job."""
    from app.core.discdb_exporter import generate_export, mark_exported
    from app.services.config_service import get_config as get_db_config

    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    config = await get_db_config()
    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
    titles = list(titles_result.scalars().all())

    from app import __version__

    export_dir = generate_export(job, titles, config, app_version=__version__)
    if not export_dir:
        raise HTTPException(status_code=400, detail="Cannot export — no content hash")

    await mark_exported(job.id, session)
    return {"status": "exported", "export_path": str(export_dir)}


@router.post("/contributions/{job_id}/skip")
async def skip_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Mark a job as skipped for contribution."""
    from app.core.discdb_exporter import mark_skipped

    await mark_skipped(job.id, session)
    return {"status": "skipped"}


class UPCLookupRequest(BaseModel):
    """Request model for UPC product lookup."""

    upc_code: str

    @field_validator("upc_code")
    @classmethod
    def validate_upc(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 14):
            raise ValueError("UPC must be 8-14 digits")
        return v


class FetchCoverRequest(BaseModel):
    """Request model for fetching cover art."""

    image_url: str


@router.post("/contributions/{job_id}/upc-lookup")
async def upc_lookup(
    request: UPCLookupRequest,
    job: DiscJob = Depends(get_job_or_404),
):
    """Look up product info by UPC barcode."""
    from app.core.upc_lookup import compute_match_confidence, lookup_upc

    result = await lookup_upc(request.upc_code)
    if not result.success:
        return {"success": False, "error": result.error}

    confidence = compute_match_confidence(result.product_title, job.detected_title)

    return {
        "success": True,
        "product_title": result.product_title,
        "brand": result.brand,
        "asins": result.asins,
        "images": result.images,
        "description": result.description,
        "match_confidence": confidence,
    }


@router.post("/contributions/{job_id}/fetch-cover")
async def fetch_cover(
    job_id: int,
    request: FetchCoverRequest,
    session: AsyncSession = Depends(get_session),
):
    """Download a cover image and save it to the export directory."""
    from app.services.config_service import get_config as get_db_config

    # SSRF guard runs BEFORE the DB lookup so a disallowed URL fails fast
    # with 400 — and so test_fetch_cover_security can prove the guard fires
    # before any other handler logic. Do NOT replace this with
    # Depends(get_job_or_404), which would 404 first on a missing job and
    # mask the security check.
    if not is_allowed_image_url(request.image_url):
        raise HTTPException(status_code=400, detail="Image URL host is not in the allowlist")

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.content_hash:
        raise HTTPException(status_code=400, detail="No content hash")

    config = await get_db_config()
    export_base = Path(config.discdb_export_path) if config.discdb_export_path else None
    if not export_base:
        raise HTTPException(status_code=400, detail="No export path configured")

    export_dir = export_base / job.content_hash
    export_dir.mkdir(parents=True, exist_ok=True)

    max_size = 10 * 1024 * 1024  # 10 MB
    try:
        # follow_redirects stays off: the SSRF guard validates only the
        # initial URL, so a redirect could otherwise reach an internal host.
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            resp = await client.get(request.image_url)
            # Check redirects before raise_for_status(): the latter fires only
            # for 4xx/5xx, and with follow_redirects disabled a 3xx would
            # otherwise save the redirect's HTML body as the cover.
            if resp.is_redirect:
                raise HTTPException(status_code=502, detail="Image server returned a redirect")
            resp.raise_for_status()

            # Parse Content-Length defensively — a malformed header must not
            # raise (int() rejects Unicode digits that str.isdigit() accepts).
            # The actual-size check below still catches oversized bodies.
            content_length = resp.headers.get("content-length")
            try:
                declared_size = int(content_length) if content_length else None
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_size:
                raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")
            if len(resp.content) > max_size:
                raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")

            # Determine extension from content type or URL
            content_type = resp.headers.get("content-type", "")
            if "png" in content_type:
                ext = ".png"
            else:
                ext = ".jpg"

            filename = f"cover{ext}"
            filepath = export_dir / filename
            filepath.write_bytes(resp.content)

        return {"status": "saved", "filename": filename}
    except httpx.HTTPError as e:
        # No user-derived value in the log args: the exception message embeds
        # the URL and even job_id is a tainted path parameter (log-injection).
        # exc_info=True still records the full exception and traceback.
        logger.warning("fetch_cover download failed (%s)", type(e).__name__, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to download image: {e}") from e


@router.post("/contributions/{job_id}/enhance")
async def enhance_contribution(
    request: EnhanceRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Add tier-3 data (UPC) and re-export."""
    from app.core.discdb_exporter import generate_export, mark_exported
    from app.services.config_service import get_config as get_db_config

    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    # Update job-level fields
    updated = False
    if request.upc_code is not None:
        job.upc_code = request.upc_code
        updated = True
    if request.asin is not None:
        job.asin = request.asin
        updated = True
    if request.release_date is not None:
        job.release_date = request.release_date
        updated = True
    if updated:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
    titles = list(titles_result.scalars().all())

    # Update per-title extra descriptions
    if request.extra_descriptions:
        for title in titles:
            if title.id in request.extra_descriptions:
                title.extra_description = request.extra_descriptions[title.id]
                session.add(title)
        await session.commit()

    config = await get_db_config()
    # In-memory override only — forces tier 3 for this single export call
    # without persisting the change to the database
    config.discdb_contribution_tier = 3

    from app import __version__

    export_dir = generate_export(job, titles, config, app_version=__version__)
    if not export_dir:
        raise HTTPException(status_code=400, detail="Cannot export — no content hash")

    await mark_exported(job.id, session)
    return {"status": "enhanced", "export_path": str(export_dir)}


@router.post("/jobs/{job_id}/flag-discdb")
async def flag_discdb(
    request: FlagDiscDBRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Flag a DiscDB title match as incorrect."""
    title = await session.get(DiscTitle, request.title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    title.discdb_flagged = True
    title.discdb_flag_reason = request.reason
    session.add(title)
    await session.commit()

    return {"status": "flagged", "title_id": title.id}


@router.post("/jobs/{job_id}/titles/{title_id}/rematch")
async def rematch_title(
    title_id: int,
    request: RematchRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Re-match a single title with optional source preference."""
    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    from app.services.job_manager import job_manager

    try:
        await job_manager.rematch_single_title(
            job.id, title_id, request.source_preference, deep=request.deep
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "rematching", "title_id": title_id}


@router.post("/jobs/{job_id}/rematch")
async def rematch_job(
    request: RematchRequest,
    job: DiscJob = Depends(get_job_or_404),
):
    """Re-match all titles for a job."""
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot re-match in state: {job.state.value}",
        )

    from app.services.job_manager import job_manager

    await job_manager.rerun_matching(job.id, request.source_preference)

    return {"status": "rematching", "job_id": job.id}


class RematchConflictRequest(BaseModel):
    """Request model for re-matching all titles claiming one episode."""

    episode_code: str


@router.post("/jobs/{job_id}/rematch-conflict")
async def rematch_conflict(
    request: RematchConflictRequest,
    job: DiscJob = Depends(get_job_or_404),
):
    """Deep re-match every title currently claiming ``episode_code``.

    Re-runs the audio matcher with stricter parameters (denser sampling + a
    higher vote requirement) for each contested title so a same-episode
    collision can resolve either way.
    """
    from app.services.job_manager import job_manager

    result = await job_manager.rematch_conflict(job.id, request.episode_code)
    if not result["dispatched"] and not result["skipped"]:
        raise HTTPException(
            status_code=404,
            detail=f"No titles are currently matched to {request.episode_code}",
        )

    return {
        "status": "rematching",
        "episode_code": request.episode_code,
        "title_ids": result["dispatched"],
        "skipped": result["skipped"],
    }


@router.post("/jobs/{job_id}/titles/{title_id}/reassign")
async def reassign_episode(
    title_id: int,
    request: ReassignRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Manually reassign an episode for a title."""
    if job.state in (JobState.ORGANIZING, JobState.FAILED, JobState.COMPLETED):
        raise HTTPException(status_code=400, detail=f"Cannot reassign in state: {job.state}")

    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    from app.services.job_manager import job_manager

    try:
        await job_manager.reassign_episode(
            job.id, title_id, request.episode_code, request.edition, source=request.source
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "reassigned", "title_id": title_id}


async def _run_llm_match_for_title(*, title: "DiscTitle", job: "DiscJob") -> dict | None:
    """Invoke the LLM episode matcher for a single title. Returns suggestion dict or None."""
    from app.core.curator import episode_curator
    from app.matcher.llm_episode_matcher import match_episode_via_llm
    from app.matcher.tmdb_client import fetch_show_id
    from app.services.config_service import get_config

    config = await get_config()
    if not config or not getattr(config, "ai_episode_matching_enabled", False):
        return None
    if not config.ai_api_key or not job.detected_title or not job.detected_season:
        return None

    # Make sure the matcher is initialized for the show (so transcribe_full works)
    episode_curator._ensure_initialized(job.detected_title)
    if not episode_curator._matcher:
        return None

    tmdb_show_id = await asyncio.to_thread(fetch_show_id, job.detected_title)
    if not tmdb_show_id:
        return None

    transcript = await asyncio.to_thread(
        episode_curator._matcher.transcribe_full, Path(title.file_path)
    )
    if not transcript:
        return None

    suggestion = await match_episode_via_llm(
        transcript=transcript,
        show_name=job.detected_title,
        season=job.detected_season,
        tmdb_show_id=str(tmdb_show_id),
        ai_provider=config.ai_provider,
        ai_api_key=config.ai_api_key,
        tmdb_api_key=config.tmdb_api_key,
    )
    if not suggestion:
        return None
    return {
        "episode": suggestion.episode,
        "confidence": suggestion.confidence,
        "reasoning": suggestion.reasoning,
        "runner_up": (
            {"episode": suggestion.runner_up.episode, "confidence": suggestion.runner_up.confidence}
            if suggestion.runner_up is not None
            else None
        ),
        "model": suggestion.model,
    }


@router.post("/jobs/{job_id}/titles/{title_id}/llm-match")
async def llm_match_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Run the LLM episode matcher on a single title and persist the suggestion.

    Idempotent under double-clicks: if `match_details.llm_suggestion` is
    already populated, returns it immediately (`reason: "cached"`) without
    kicking off another 1–3 minute Whisper transcription. Re-running
    intentionally is out of scope for v1.
    """
    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    # Cache-hit dedup: avoid duplicate expensive transcription on double-click.
    existing = json.loads(title.match_details or "{}") if title.match_details else {}
    cached = existing.get("llm_suggestion")
    if cached:
        return {"suggestion": cached, "reason": "cached"}

    try:
        suggestion = await _run_llm_match_for_title(title=title, job=job)
    except Exception:
        logger.exception("LLM match endpoint failed for title %s", sanitize_log_value(title_id))
        return {"suggestion": None, "reason": "internal_error"}

    if not suggestion:
        return {"suggestion": None, "reason": "no_suggestion"}

    # Persist into match_details for refresh durability
    existing["llm_suggestion"] = suggestion
    title.match_details = json.dumps(existing)
    session.add(title)
    await session.commit()

    return {"suggestion": suggestion, "reason": None}


@router.post("/contributions/{job_id}/submit")
async def submit_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Submit a job's disc data to TheDiscDB API."""
    from app.core.discdb_submitter import submit_job
    from app.services.config_service import get_config as get_db_config

    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")
    if not job.exported_at or job.exported_at.year == 1970:
        raise HTTPException(status_code=400, detail="Job must be exported before submission")

    config = await get_db_config()

    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
    titles = list(titles_result.scalars().all())

    from app import __version__

    result = await submit_job(job, titles, config, app_version=__version__)

    if result.success:
        job.submitted_at = datetime.now(UTC)
        job.discdb_submission_id = result.submission_id
        job.discdb_contribute_url = result.contribute_url
        session.add(job)
        await session.commit()

    return {
        "success": result.success,
        "submission_id": result.submission_id,
        "contribute_url": result.contribute_url,
        "error": result.error,
    }


@router.post("/contributions/release-group")
async def create_release_group(
    request: ReleaseGroupRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a release group linking multiple disc jobs."""
    import uuid

    unique_ids = list(dict.fromkeys(request.job_ids))  # deduplicate, preserve order
    if len(unique_ids) < 2:
        raise HTTPException(status_code=400, detail="A release group requires at least 2 jobs")
    request.job_ids = unique_ids

    # Verify all jobs exist
    jobs = []
    for job_id in request.job_ids:
        job = await session.get(DiscJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        jobs.append(job)

    release_group_id = str(uuid.uuid4())
    for job in jobs:
        job.release_group_id = release_group_id
        session.add(job)
    await session.commit()

    return {"release_group_id": release_group_id, "job_ids": request.job_ids}


@router.put("/contributions/{job_id}/release-group")
async def assign_release_group(
    request: ReleaseGroupAssignRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Assign or remove a job from a release group."""
    if request.release_group_id:
        # Verify the release group exists (at least one other job has it)
        result = await session.execute(
            select(DiscJob).where(
                DiscJob.release_group_id == request.release_group_id,
                DiscJob.id != job.id,
            )
        )
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail="Release group not found")

    job.release_group_id = request.release_group_id
    session.add(job)
    await session.commit()

    return {"job_id": job.id, "release_group_id": request.release_group_id}


@router.post("/contributions/release-group/{release_group_id}/submit")
async def submit_release_group_endpoint(
    release_group_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Batch-submit all completed jobs in a release group to TheDiscDB."""
    from app.core.discdb_submitter import submit_release_group
    from app.services.config_service import get_config as get_db_config

    # Verify release group exists (lightweight count check)
    count_result = await session.execute(
        select(func.count())
        .select_from(DiscJob)
        .where(DiscJob.release_group_id == release_group_id)
    )
    if count_result.scalar() == 0:
        raise HTTPException(status_code=404, detail="Release group not found")

    config = await get_db_config()

    from app import __version__

    batch_result = await submit_release_group(
        release_group_id, session, config, app_version=__version__
    )

    return {
        "submitted": batch_result.submitted,
        "failed": batch_result.failed,
        "results": batch_result.results,
        "contribute_url": batch_result.contribute_url,
    }


# ---------------------------------------------------------------------------
# Update endpoints
# ---------------------------------------------------------------------------


class SkipVersionRequest(BaseModel):
    version: str


@router.get("/updates/status")
async def get_update_status():
    """Get current update check state."""
    return update_checker.get_status()


@router.post("/updates/skip")
async def skip_update_version(body: SkipVersionRequest):
    """Persist user's choice to skip a specific version."""
    try:
        await update_checker.skip_version(body.version)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/updates/restart")
async def restart_for_update(background_tasks: BackgroundTasks):
    """Schedule update application after response is sent.

    Returns 200 immediately; actual restart happens in a BackgroundTask so the
    response has time to reach the client before the process exits/exec's.
    """

    # Guard checks run synchronously before returning 200
    if not update_checker._is_frozen:
        raise HTTPException(
            status_code=400,
            detail=(
                "Updates can only be applied in frozen builds. "
                f"Download manually from {update_checker.release_url or 'GitHub'}."
            ),
        )

    if update_checker.state != UpdateStatus.READY:
        raise HTTPException(status_code=400, detail="No staged update is ready to apply.")

    try:
        await update_checker._check_no_active_jobs()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def _do_restart() -> None:
        try:
            await update_checker.apply_update()
        except PermissionError as exc:
            logger.error(f"Update restart permission error: {exc}", exc_info=True)
        except Exception as exc:
            logger.error(f"Update restart failed: {exc}", exc_info=True)

    background_tasks.add_task(_do_restart)
    return {"ok": True}
