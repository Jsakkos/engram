"""REST API routes for Engram."""

import json
import logging
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import settings
from app.database import get_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["jobs"])


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


class ConfigResponse(BaseModel):
    """Response model for configuration."""

    makemkv_path: str
    makemkv_key: str
    staging_path: str
    library_movies_path: str
    library_tv_path: str
    transcoding_enabled: bool
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
    # Onboarding
    setup_complete: bool


class ConfigUpdate(BaseModel):
    """Request model for updating configuration."""

    makemkv_path: str | None = None
    makemkv_key: str | None = None
    staging_path: str | None = None
    library_movies_path: str | None = None
    library_tv_path: str | None = None
    transcoding_enabled: bool | None = None
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
    # Onboarding
    setup_complete: bool | None = None


class ReviewRequest(BaseModel):
    """Request model for submitting a review decision."""

    title_id: int
    episode_code: str | None = None  # e.g., "S01E01"
    edition: str | None = None  # e.g., "Extended", "Theatrical"


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

    return [
        {
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
        for j in jobs
    ]


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

    return {
        "total_jobs": len(jobs),
        "completed_jobs": len(completed),
        "failed_jobs": len(failed),
        "tv_count": len(tv_jobs),
        "movie_count": len(movie_jobs),
        "total_titles_ripped": total_titles,
        "avg_processing_seconds": avg_processing,
        "common_errors": common_errors,
        "recent_jobs": [
            {
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
            for j in recent
        ],
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)) -> DiscJob:
    """Get a specific job by ID."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/titles", response_model=list[TitleResponse])
async def get_job_titles(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> list[DiscTitle]:
    """Get all titles with match results for a job."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await session.execute(
        select(DiscTitle).where(DiscTitle.job_id == job_id).order_by(DiscTitle.title_index)
    )
    return list(result.scalars().all())


@router.get("/jobs/{job_id}/detail", response_model=JobDetailResponse)
async def get_job_detail(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Get full job detail with titles for history drill-down."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Fetch associated titles
    titles_result = await session.execute(
        select(DiscTitle).where(DiscTitle.job_id == job_id).order_by(DiscTitle.title_index)
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
        "titles": [
            {
                "id": t.id,
                "job_id": t.job_id,
                "title_index": t.title_index,
                "duration_seconds": t.duration_seconds,
                "file_size_bytes": t.file_size_bytes,
                "chapter_count": t.chapter_count,
                "is_selected": t.is_selected,
                "output_filename": t.output_filename,
                "matched_episode": t.matched_episode,
                "match_confidence": t.match_confidence,
                "match_details": t.match_details,
                "state": t.state,
                "video_resolution": t.video_resolution,
                "edition": t.edition,
                "conflict_resolution": t.conflict_resolution,
                "existing_file_path": t.existing_file_path,
                "organized_from": t.organized_from,
                "organized_to": t.organized_to,
                "is_extra": t.is_extra,
                "match_source": t.match_source,
                "discdb_match_details": t.discdb_match_details,
                "discdb_flagged": t.discdb_flagged,
                "discdb_flag_reason": t.discdb_flag_reason,
            }
            for t in titles
        ],
    }


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Start ripping a disc."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state not in (JobState.IDLE, JobState.REVIEW_NEEDED):
        raise HTTPException(status_code=400, detail=f"Cannot start job in state: {job.state}")

    # Import here to avoid circular imports
    from app.services.job_manager import job_manager

    await job_manager.start_ripping(job_id)
    return {"status": "started", "job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Cancel a running job."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.services.job_manager import job_manager

    await job_manager.cancel_job(job_id)
    return {"status": "cancelled", "job_id": job_id}


@router.post("/jobs/{job_id}/review")
async def submit_review(
    job_id: int,
    review: ReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Submit a review decision for a title."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    await job_manager.apply_review(
        job_id, review.title_id, episode_code=review.episode_code, edition=review.edition
    )
    return {"status": "reviewed", "job_id": job_id}


class SetNameRequest(BaseModel):
    """Request model for setting a user-provided name on an unlabeled disc."""

    name: str
    content_type: str  # "tv" | "movie" | "unknown"
    season: int | None = None


@router.post("/jobs/{job_id}/set-name")
async def set_job_name(
    job_id: int,
    req: SetNameRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set a user-provided name for a disc with unreadable volume label, then resume ripping."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting name input")

    from app.services.job_manager import job_manager

    await job_manager.set_name_and_resume(job_id, req.name, req.content_type, req.season)
    return {"status": "ok", "job_id": job_id}


class ReIdentifyRequest(BaseModel):
    """Request model for re-identifying a disc with corrected metadata."""

    title: str
    content_type: str  # "tv" | "movie"
    season: int | None = None
    tmdb_id: int | None = None


@router.post("/jobs/{job_id}/re-identify")
async def re_identify_job(
    job_id: int,
    req: ReIdentifyRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-identify a disc with user-corrected title, content type, and optional TMDB ID."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in review_needed state, currently: {job.state.value}",
        )

    from app.services.job_manager import job_manager

    await job_manager.re_identify_job(job_id, req.title, req.content_type, req.season, req.tmdb_id)
    return {"status": "re-identifying", "job_id": job_id}


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
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Retry subtitle download for a job that failed."""
    import asyncio

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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
        job_manager._download_subtitles(job_id, job.detected_title, job.detected_season)
    )

    return {"status": "retry_started", "job_id": job_id}


@router.post("/jobs/{job_id}/process-matched")
async def process_matched_titles(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Process all matched titles for a job without waiting for unresolved ones."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    result = await job_manager.process_matched_titles(job_id)
    return {"status": "processed", "job_id": job_id, **result}


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
        transcoding_enabled=config.transcoding_enabled,
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
        # Onboarding
        setup_complete=config.setup_complete,
    )


@router.put("/config")
async def update_config(config: ConfigUpdate) -> dict:
    """Update configuration and persist to database."""
    from app.services.config_service import update_config as update_db_config

    # Build kwargs from non-None fields
    update_data = {k: v for k, v in config.model_dump().items() if v is not None}

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

    return {"status": "updated", "persisted": True}


@router.get("/jobs/{job_id}/poster")
async def get_job_poster(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Get TMDB poster URL for a job."""
    result = await session.execute(select(DiscJob).where(DiscJob.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.detected_title:
        return {"poster_url": None}

    # Fetch poster from TMDB
    import requests

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

    headers = {}
    params = {"query": job.detected_title}

    if len(api_key) > 40:  # v4 token
        headers["Authorization"] = f"Bearer {api_key}"
    else:  # v3 key
        params["api_key"] = api_key

    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                poster_path = results[0]["poster_path"]
                return {"poster_url": f"{BASE_IMAGE_URL}{poster_path}"}
    except Exception as e:
        print(f"Error fetching poster: {e}")

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
async def delete_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Soft-delete a single completed or failed job (moves to history)."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state not in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Can only clear completed or failed jobs (current state: {job.state})",
        )

    job.cleared_at = datetime.now(UTC)
    await session.commit()

    return {"status": "cleared", "job_id": job_id}


# --- Simulation Endpoints (debug mode only) ---


class SimulateDiscRequest(BaseModel):
    """Request model for simulating a disc insertion."""

    drive_id: str = "E:"
    volume_label: str = "SIMULATED_DISC"
    content_type: str = "tv"
    detected_title: str | None = None
    detected_season: int | None = 1
    titles: list[dict] | None = None
    simulate_ripping: bool = True
    rip_speed_multiplier: int = 10
    force_review_needed: bool = False
    review_reason: str | None = None


@router.post("/simulate/insert-disc")
async def simulate_insert_disc(req: SimulateDiscRequest) -> dict:
    """Simulate a disc insertion. Only available in debug mode."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from app.services.job_manager import job_manager

    params = req.model_dump()
    if not params.get("force_review_needed") and params.get("detected_title") is None:
        params["detected_title"] = req.volume_label.replace("_", " ").title()
    if params.get("titles") is None:
        params.pop("titles", None)

    job_id = await job_manager.simulate_disc_insert(params)
    return {"status": "simulated", "job_id": job_id}


@router.post("/simulate/remove-disc")
async def simulate_remove_disc(drive_id: str = "E:") -> dict:
    """Simulate a disc removal. Only available in debug mode."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from app.api.websocket import manager as ws_manager
    from app.services.job_manager import job_manager

    await ws_manager.broadcast_drive_event(drive_id, "removed")
    await job_manager._cancel_jobs_for_drive(drive_id)
    return {"status": "removed", "drive_id": drive_id}


@router.post("/simulate/trigger-real-scan")
async def trigger_real_scan(drive_id: str = "F:") -> dict:
    """Trigger a real disc scan and rip pipeline. Only available in debug mode.

    This fires the same event as a physical disc insertion, using the real
    MakeMKV extractor to scan and rip the disc currently in the drive.
    """
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from app.core.sentinel import get_volume_label, is_disc_present
    from app.services.job_manager import job_manager

    if not is_disc_present(drive_id):
        raise HTTPException(status_code=400, detail=f"No disc found in drive {drive_id}")

    label = get_volume_label(drive_id)
    await job_manager._on_drive_event(drive_id, "inserted", label)
    return {"status": "triggered", "drive_id": drive_id, "volume_label": label}


@router.post("/simulate/advance-job/{job_id}")
async def simulate_advance_job(job_id: int) -> dict:
    """Manually advance a job to the next state. Only available in debug mode."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from app.services.job_manager import job_manager

    try:
        new_state = await job_manager.advance_job(job_id)
        return {"status": "advanced", "job_id": job_id, "new_state": new_state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.delete("/simulate/reset-all-jobs")
async def reset_all_jobs(session: AsyncSession = Depends(get_session)) -> dict:
    """Delete ALL jobs and titles regardless of state. Debug mode only."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from sqlalchemy import delete

    await session.execute(delete(DiscTitle))
    result = await session.execute(delete(DiscJob))
    await session.commit()
    return {"status": "reset", "deleted_count": result.rowcount}


@router.post("/simulate/insert-disc-from-staging")
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
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

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
        "drive_id": "E:",
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
async def get_staging_size(session: AsyncSession = Depends(get_session)) -> dict:
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

_HOME_PATH = str(Path.home())
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


@router.get("/diagnostics/logs")
async def get_recent_logs(
    lines: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Return the last N lines from the engram log file, sanitized."""
    log_path = Path.home() / ".engram" / "engram.log"
    if not log_path.exists():
        return {"lines": [], "log_path": str(log_path).replace(_HOME_PATH, "~")}

    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        tail = raw.splitlines()[-lines:]
        sanitized = [_sanitize_line(line) for line in tail]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log: {e}") from e

    return {
        "lines": sanitized,
        "log_path": str(log_path).replace(_HOME_PATH, "~"),
    }


@router.get("/diagnostics/report")
async def generate_bug_report(
    job_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a sanitized bug report with optional job context."""
    from app import __version__
    from app.services.config_service import get_config

    config = await get_config()

    # --- Job summary (optional) ---
    job_summary = None
    if job_id is not None:
        job = await session.get(DiscJob, job_id)
        if job:
            job_summary = {
                "id": job.id,
                "volume_label": job.volume_label,
                "content_type": job.content_type.value if job.content_type else "unknown",
                "state": job.state.value if job.state else "unknown",
                "error": job.error_message,
                "created_at": str(job.created_at) if job.created_at else None,
                "completed_at": str(job.completed_at) if job.completed_at else None,
            }

    # --- Recent error lines from log ---
    log_path = Path.home() / ".engram" / "engram.log"
    recent_errors: list[str] = []
    if log_path.exists():
        try:
            raw = log_path.read_text(encoding="utf-8", errors="replace")
            all_lines = raw.splitlines()
            error_lines = [ln for ln in all_lines if "ERROR" in ln or "CRITICAL" in ln]
            recent_errors = [_sanitize_line(line) for line in error_lines[-20:]]
        except Exception:
            recent_errors = ["(could not read log file)"]

    # --- Redacted config ---
    redacted_config = {
        "staging_path": str(config.staging_path).replace(_HOME_PATH, "~"),
        "library_movies_path": str(config.library_movies_path).replace(_HOME_PATH, "~"),
        "library_tv_path": str(config.library_tv_path).replace(_HOME_PATH, "~"),
        "transcoding_enabled": config.transcoding_enabled,
        "max_concurrent_matches": config.max_concurrent_matches,
        "conflict_resolution_default": config.conflict_resolution_default,
        "extras_policy": config.extras_policy,
        "discdb_enabled": config.discdb_enabled,
    }

    # --- Build report ---
    report = {
        "app_version": __version__,
        "python_version": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()}",
        "job": job_summary,
        "recent_errors": recent_errors,
        "config": redacted_config,
    }

    # --- Build GitHub issue body ---
    body_parts = [
        "## Bug Report",
        "",
        f"**Engram version**: {__version__}",
        f"**OS**: {report['os']}",
        f"**Python**: {report['python_version']}",
        "",
    ]
    if job_summary:
        body_parts += [
            "### Job Context",
            f"- **ID**: {job_summary['id']}",
            f"- **Label**: {job_summary['volume_label']}",
            f"- **Type**: {job_summary['content_type']}",
            f"- **State**: {job_summary['state']}",
        ]
        if job_summary["error"]:
            body_parts.append(f"- **Error**: {job_summary['error']}")
        body_parts.append("")

    if recent_errors:
        body_parts += ["### Recent Errors", "```"]
        body_parts += recent_errors[-10:]
        body_parts += ["```", ""]

    body_parts += [
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
    return report


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


class ContributionStatsResponse(BaseModel):
    """Stats for the contribution nav badge."""

    pending: int
    exported: int
    skipped: int
    submitted: int


class EnhanceRequest(BaseModel):
    """Request model for tier-3 contribution enhancement."""

    upc_code: str | None = None


class FlagDiscDBRequest(BaseModel):
    """Request model for flagging incorrect DiscDB data on a title."""

    title_id: int
    reason: str
    details: str | None = None


class RematchRequest(BaseModel):
    """Request model for re-matching titles."""

    source_preference: str | None = None  # "discdb", "engram", or None


class ReassignRequest(BaseModel):
    """Request model for manual episode reassignment."""

    episode_code: str
    edition: str | None = None


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
        if job.submitted_at:
            status = "submitted"
        elif job.exported_at is None:
            status = "pending"
        elif job.exported_at.year == 1970:
            status = "skipped"
        else:
            status = "exported"

        # Use stored contribute URL, or construct from submission ID as fallback
        contribute_url = getattr(job, "discdb_contribute_url", None)
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
            )
        )
    return responses


@router.get("/contributions/stats", response_model=ContributionStatsResponse)
async def contribution_stats(session: AsyncSession = Depends(get_session)):
    """Get contribution counts for nav badge."""
    result = await session.execute(select(DiscJob).where(DiscJob.state == JobState.COMPLETED))
    jobs = result.scalars().all()

    pending = 0
    exported = 0
    skipped = 0
    submitted = 0
    for job in jobs:
        if job.submitted_at:
            submitted += 1
        elif job.exported_at is None:
            pending += 1
        elif job.exported_at.year == 1970:
            skipped += 1
        else:
            exported += 1

    return ContributionStatsResponse(
        pending=pending, exported=exported, skipped=skipped, submitted=submitted
    )


@router.post("/contributions/{job_id}/export")
async def export_contribution(job_id: int, session: AsyncSession = Depends(get_session)):
    """Manually trigger export for a specific job."""
    from app.core.discdb_exporter import generate_export, mark_exported
    from app.services.config_service import get_config as get_db_config

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    config = await get_db_config()
    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
    titles = list(titles_result.scalars().all())

    from app import __version__

    export_dir = generate_export(job, titles, config, app_version=__version__)
    if not export_dir:
        raise HTTPException(status_code=400, detail="Cannot export — no content hash")

    await mark_exported(job_id, session)
    return {"status": "exported", "export_path": str(export_dir)}


@router.post("/contributions/{job_id}/skip")
async def skip_contribution(job_id: int, session: AsyncSession = Depends(get_session)):
    """Mark a job as skipped for contribution."""
    from app.core.discdb_exporter import mark_skipped

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    await mark_skipped(job_id, session)
    return {"status": "skipped"}


@router.post("/contributions/{job_id}/enhance")
async def enhance_contribution(
    job_id: int,
    request: EnhanceRequest,
    session: AsyncSession = Depends(get_session),
):
    """Add tier-3 data (UPC) and re-export."""
    from app.core.discdb_exporter import generate_export, mark_exported
    from app.services.config_service import get_config as get_db_config

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    # Update UPC
    if request.upc_code:
        job.upc_code = request.upc_code
        session.add(job)
        await session.commit()
        await session.refresh(job)

    config = await get_db_config()
    config.discdb_contribution_tier = 3  # Force tier 3 for this export

    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
    titles = list(titles_result.scalars().all())

    from app import __version__

    export_dir = generate_export(job, titles, config, app_version=__version__)
    if not export_dir:
        raise HTTPException(status_code=400, detail="Cannot export — no content hash")

    await mark_exported(job_id, session)
    return {"status": "enhanced", "export_path": str(export_dir)}


@router.post("/jobs/{job_id}/flag-discdb")
async def flag_discdb(
    job_id: int,
    request: FlagDiscDBRequest,
    session: AsyncSession = Depends(get_session),
):
    """Flag a DiscDB title match as incorrect."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    title = await session.get(DiscTitle, request.title_id)
    if not title or title.job_id != job_id:
        raise HTTPException(status_code=404, detail="Title not found")

    title.discdb_flagged = True
    title.discdb_flag_reason = request.reason
    session.add(title)
    await session.commit()

    return {"status": "flagged", "title_id": title.id}


@router.post("/jobs/{job_id}/titles/{title_id}/rematch")
async def rematch_title(
    job_id: int,
    title_id: int,
    request: RematchRequest,
    session: AsyncSession = Depends(get_session),
):
    """Re-match a single title with optional source preference."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job_id:
        raise HTTPException(status_code=404, detail="Title not found")

    from app.services.job_manager import job_manager

    try:
        await job_manager.rematch_single_title(job_id, title_id, request.source_preference)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "rematching", "title_id": title_id}


@router.post("/jobs/{job_id}/rematch")
async def rematch_job(
    job_id: int,
    request: RematchRequest,
    session: AsyncSession = Depends(get_session),
):
    """Re-match all titles for a job."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.services.job_manager import job_manager

    await job_manager._rerun_matching(job_id, request.source_preference)

    return {"status": "rematching", "job_id": job_id}


@router.post("/jobs/{job_id}/titles/{title_id}/reassign")
async def reassign_episode(
    job_id: int,
    title_id: int,
    request: ReassignRequest,
    session: AsyncSession = Depends(get_session),
):
    """Manually reassign an episode for a title."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state in (JobState.ORGANIZING, JobState.FAILED):
        raise HTTPException(status_code=400, detail=f"Cannot reassign in state: {job.state}")

    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job_id:
        raise HTTPException(status_code=404, detail="Title not found")

    from app.services.job_manager import job_manager

    try:
        await job_manager.reassign_episode(job_id, title_id, request.episode_code, request.edition)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "reassigned", "title_id": title_id}


@router.post("/contributions/{job_id}/submit")
async def submit_contribution(job_id: int, session: AsyncSession = Depends(get_session)):
    """Submit a job's disc data to TheDiscDB API."""
    from app.core.discdb_submitter import submit_job
    from app.services.config_service import get_config as get_db_config

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    config = await get_db_config()

    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
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

    if len(request.job_ids) < 2:
        raise HTTPException(status_code=400, detail="A release group requires at least 2 jobs")

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
    job_id: int,
    request: ReleaseGroupAssignRequest,
    session: AsyncSession = Depends(get_session),
):
    """Assign or remove a job from a release group."""
    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if request.release_group_id:
        # Verify the release group exists (at least one other job has it)
        result = await session.execute(
            select(DiscJob).where(
                DiscJob.release_group_id == request.release_group_id,
                DiscJob.id != job_id,
            )
        )
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail="Release group not found")

    job.release_group_id = request.release_group_id
    session.add(job)
    await session.commit()

    return {"job_id": job_id, "release_group_id": request.release_group_id}


@router.post("/contributions/release-group/{release_group_id}/submit")
async def submit_release_group_endpoint(
    release_group_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Batch-submit all completed jobs in a release group to TheDiscDB."""
    from app.core.discdb_submitter import submit_release_group
    from app.services.config_service import get_config as get_db_config

    # Verify release group exists
    result = await session.execute(
        select(DiscJob).where(DiscJob.release_group_id == release_group_id)
    )
    if not result.scalars().first():
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
