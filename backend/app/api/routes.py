"""REST API routes for Engram."""

import asyncio
import io
import ipaddress
import json
import logging
import os
import platform
import re
import string
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import settings
from app.core.discdb_exporter import get_makemkv_log_dir
from app.core.errors import AIProviderError
from app.core.security import is_allowed_image_url, sanitize_log_value
from app.core.updater import UpdateError, UpdateStatus, update_checker
from app.database import get_session
from app.matcher.coverage_tracker import get_cache_status
from app.matcher.episode_identification import reference_coverage
from app.matcher.manual_subtitle_import import (
    MAX_FILES_PER_BATCH,
    CommitInputFile,
    PreviewInputFile,
    classify_files,
    commit_files,
)
from app.matcher.tmdb_client import fetch_season_episodes, get_number_of_seasons
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle
from app.services.identity_prompts import BLOCKING_KINDS

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


def _is_loopback(host: str | None) -> bool:
    """True if ``host`` names the local machine.

    Covers every loopback form a peer address can take — IPv4 (`127.0.0.0/8`),
    IPv6 (`::1`), and the IPv4-mapped IPv6 loopback (`::ffff:127.0.0.1`) that
    arrives on dual-stack binds (`HOST=::`). `localhost` is kept as an explicit
    fallback: Starlette normally reports a numeric peer address, but a literal
    hostname is accepted rather than silently rejected.
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    if addr.is_loopback:
        return True
    # Python < 3.13's is_loopback does not unwrap IPv4-mapped addresses, so
    # check the mapped IPv4 explicitly for version-independent correctness.
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def require_localhost(request: Request) -> None:
    """FastAPI dependency: 403 unless the request came from the host machine.

    Used by endpoints that surface or mutate privacy-sensitive local data
    (e.g. ripping history, DiscDB/fingerprint contributions) so they stay
    reachable from the dashboard but not from LAN peers when
    `allow_lan_access=True` opens the bind to all interfaces. Tests should
    override the dependency via
    `app.dependency_overrides[require_localhost] = lambda: None` rather than
    spoofing peer addresses.
    """
    if not _is_loopback(request.client.host if request.client else None):
        raise HTTPException(
            status_code=403, detail="This endpoint is only reachable from the host machine"
        )


async def require_localhost_or_lan(request: Request) -> None:
    """FastAPI dependency: allow loopback always, LAN peers only when opted in.

    Like `require_localhost`, but a non-loopback (LAN) client is also permitted
    when the user has explicitly enabled `allow_lan_access`. Used by the manual
    *import* endpoints so headless/Docker deployments — where the dashboard is
    reached from another machine — can browse and import, while the more
    sensitive fingerprint/contribution endpoints stay strictly host-only via
    `require_localhost`.

    The filesystem-browse surface this exposes is why the gate is the explicit
    opt-in rather than always-on: enabling `allow_lan_access` is the user's
    statement that they trust their network.
    """
    if _is_loopback(request.client.host if request.client else None):
        return
    try:
        from app.services.config_service import get_config

        config = await get_config()
        allow_lan = bool(config.allow_lan_access)
    except Exception:  # noqa: BLE001 — a config read failure must fail closed
        allow_lan = False
    if not allow_lan:
        raise HTTPException(
            status_code=403,
            detail=(
                "This endpoint is only reachable from the host machine. To use it "
                "from another device (e.g. a Docker deployment), enable LAN access "
                "in Settings."
            ),
        )


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
    # JSON list of same-name TMDB twins (e.g. Frasier 1993 + 2023) recorded at
    # identify time; lets the UI offer "did you mean ...?" on a wrong-show review.
    candidates_json: str | None = None
    # Transient auto-resolution note set during conflict / review escalation
    # (e.g. "Resolving episode conflicts — pass 2 of 3"). Cleared on resolution.
    conflict_status: str | None = None
    # Why classification ran without TMDB (key absent/rejected); None when TMDB
    # participated. Rendered verbatim by the DiscCard degraded-mode alert (#243).
    tmdb_degraded_reason: str | None = None
    # Resolved TMDB identity. tmdb_id is null while identity is unconfirmed (e.g. a
    # same-name collision the analyst withholds an id for) — the dashboard reads that
    # to suppress the dead-end episode-review button, and the re-identify modal shows
    # tmdb_name/tmdb_year/tmdb_id so the user can confirm which show is selected.
    tmdb_id: int | None = None
    tmdb_name: str | None = None
    tmdb_year: int | None = None
    destination_mode: str = "library"
    created_at: datetime | str | None = None
    # Identity CTA for rip-first jobs (walk-away Phase B). Raw JSON string:
    # {"kind": "name"|"season"|"reidentify", "reason": "<human text>"}.
    # Null when no prompt is pending; set by identify_disc's B2 gates and
    # cleared by the answer endpoints / B4 rip-end convergence.
    identity_prompt_json: str | None = None

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
    # JSON list of same-name TMDB twins (e.g. Frasier 1993 + 2023) recorded at
    # identify time; lets the UI offer "did you mean ...?" on a wrong-show review.
    candidates_json: str | None = None
    # Identity CTA for rip-first jobs (walk-away Phase B) — see JobResponse.
    identity_prompt_json: str | None = None
    # Transient auto-resolution note (e.g. "Resolving episode conflicts — pass 2 of 3"
    # / "Deep re-matching low-confidence titles — pass 1 of 3"). Set while the
    # finalization coordinator is auto-escalating; cleared on resolution.
    conflict_status: str | None = None
    # Why classification ran without TMDB (key absent/rejected); None when TMDB
    # participated normally (#243).
    tmdb_degraded_reason: str | None = None
    # Classification
    classification_source: str = "heuristic"
    classification_confidence: float = 0.0
    tmdb_id: int | None = None
    tmdb_name: str | None = None
    tmdb_year: int | None = None
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
    # Whether a TMDB key is stored. The key itself is redacted ("***"/"") above,
    # so the dashboard health banner reads this boolean instead of sniffing the
    # masked value (#243).
    tmdb_configured: bool
    max_concurrent_matches: int
    enable_gpu_acceleration: bool
    # Background pre-transcription (transcript cache prewarmer)
    enable_background_pretranscription: bool
    pretranscribe_full_file: bool
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
    # Drive behavior
    auto_eject_enabled: bool
    # Staging cleanup
    staging_cleanup_policy: str
    staging_cleanup_days: int
    # Extras & naming
    extras_policy: str
    naming_season_format: str
    naming_episode_format: str
    naming_movie_format: str
    naming_tv_show_format: str
    # Episode ordering (#200) — global default output ordering
    episode_ordering_preference: str
    # AI identification
    ai_identification_enabled: bool
    ai_provider: str
    ai_api_key: str
    ai_episode_matching_enabled: bool
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
    # Chromaprint fingerprinting (Phase 1)
    fpcalc_path: str
    enable_fingerprint_contributions: bool
    # Chromaprint Phase 2
    fingerprint_server_url: str | None = None
    fingerprint_disclosure_accepted: bool = False
    fingerprint_disclosure_accepted_at: datetime | None = None
    contribution_pseudonym: str | None = None
    # Notifications
    discord_webhook_url: str = ""
    discord_template_completed: str = ""
    discord_template_failed: str = ""


class ConfigUpdate(BaseModel):
    """Request model for updating configuration."""

    makemkv_path: str | None = None
    makemkv_key: str | None = None
    staging_path: str | None = None
    library_movies_path: str | None = None
    library_tv_path: str | None = None
    tmdb_api_key: str | None = None
    max_concurrent_matches: int | None = None
    enable_gpu_acceleration: bool | None = None
    # Background pre-transcription (transcript cache prewarmer)
    enable_background_pretranscription: bool | None = None
    pretranscribe_full_file: bool | None = None
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
    # Drive behavior
    auto_eject_enabled: bool | None = None
    # Staging cleanup
    staging_cleanup_policy: str | None = None
    staging_cleanup_days: int | None = None
    # Extras & naming
    extras_policy: str | None = None
    naming_season_format: str | None = None
    naming_episode_format: str | None = None
    naming_movie_format: str | None = None
    naming_tv_show_format: str | None = None
    # Episode ordering (#200) — global default output ordering
    episode_ordering_preference: str | None = None
    # AI identification
    ai_identification_enabled: bool | None = None
    ai_provider: str | None = None
    ai_api_key: str | None = None
    ai_episode_matching_enabled: bool | None = None
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
    # Chromaprint fingerprinting (Phase 1)
    fpcalc_path: str | None = None
    enable_fingerprint_contributions: bool | None = None
    # Chromaprint Phase 2
    fingerprint_server_url: str | None = None
    fingerprint_disclosure_accepted: bool | None = None
    # Notifications
    discord_webhook_url: str | None = None
    discord_template_completed: str | None = None
    discord_template_failed: str | None = None


class ReviewRequest(BaseModel):
    """Request model for submitting a review decision."""

    title_id: int
    episode_code: str | None = None  # e.g., "S01E01"
    edition: str | None = None  # e.g., "Extended", "Theatrical"


class ReviewDecision(BaseModel):
    """A single title's review decision within a batch."""

    title_id: int
    episode_code: str | None = None  # e.g., "S01E01", "extra", "skip"
    edition: str | None = None  # e.g., "Extended", "Theatrical"


class ReviewBatchRequest(BaseModel):
    """Request model for submitting multiple review decisions at once."""

    decisions: list[ReviewDecision] = Field(..., min_length=1)


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
    # Subtitle-reference availability (the source of truth for auto-matching).
    # ``missing`` means no precomputed vector and no downloaded SRT exist for the
    # episode, so the matcher had nothing to identify it against — the silent gap
    # the review UI must flag. None when the coverage scan was unavailable.
    reference_source: Literal["precomputed", "downloaded", "missing"] | None = None
    has_reference: bool = True


class OrderingOption(BaseModel):
    """One selectable output ordering for a show (#200)."""

    ordering: str  # "aired" | "dvd" (v1 scope)
    label: str  # human label (TMDB group name, e.g. "DVD Order")
    tmdb_type: int  # TMDB episode-group type enum
    diverges: bool  # does this ordering renumber any matched episode on the disc
    projection: dict[str, str] = {}  # canonical "SxxExx" -> projected "SxxExx"


class SeasonRosterResponse(BaseModel):
    """Season episode list (code + name) plus per-episode coverage.

    ``status`` reflects the persisted state: ``assigned`` (one title),
    ``duplicate`` (two+ titles share the episode), ``missing`` (no title but
    inside the disc's covered range — a gap to fill) and ``off`` (outside the
    range, i.e. on another disc). The frontend recomputes status live as the
    user edits unsaved selections.

    The ``ordering_*`` fields (#200) drive the episode-ordering selector: it is
    only surfaced when ``ordering_diverges`` is true (a real renumbering exists
    for this disc), keeping the common single-ordering case silent.
    """

    available: bool
    season_number: int | None = None
    show_id: int | None = None
    episodes: list[RosterEpisode] = []
    reason: str | None = None
    # Season picker (#370): total seasons for the show, populated only while the
    # job's season is unknown (no extra TMDB call on the normal detected path).
    season_count: int | None = None
    # Episode ordering (#200)
    ordering_available: bool = False
    ordering_diverges: bool = False
    current_ordering: str = "aired"
    ordering_options: list[OrderingOption] = []


@router.get("/jobs/{job_id}/season-roster", response_model=SeasonRosterResponse)
async def get_season_roster(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
    season: int | None = Query(
        default=None, ge=0, description="Override season (unknown-season picker, #370)"
    ),
) -> SeasonRosterResponse:
    """Season episode list with per-episode coverage for the review UI.

    ``?season=N`` overrides the job's detected season so the review page can
    browse rosters for discs whose label carried no season (#370).
    """
    if job.content_type != ContentType.TV:
        return SeasonRosterResponse(available=False, reason="Not a TV disc")

    effective_season = season if season is not None else job.detected_season

    # Season-picker support (#370): while the job's season is unknown, report
    # how many seasons exist so the prompt/picker can render options. The
    # lookup is best-effort decoration — a TMDB failure must not break review.
    season_count: int | None = None
    if job.tmdb_id and job.detected_season is None:
        try:
            season_count = await asyncio.to_thread(get_number_of_seasons, str(job.tmdb_id))
        except Exception as e:  # noqa: BLE001 — picker is best-effort decoration
            logger.debug(
                "Season-count lookup failed for show %s: %s",
                sanitize_log_value(job.tmdb_id),
                sanitize_log_value(str(e)),
            )
            season_count = None

    if not job.tmdb_id or effective_season is None:
        return SeasonRosterResponse(
            available=False,
            season_number=effective_season,
            show_id=job.tmdb_id,
            season_count=season_count,
            reason="Show or season not identified yet",
        )

    season_num = effective_season
    from app.services.config_service import get_config

    config = await get_config()
    # fetch_season_episodes does a synchronous requests.get; run it off the
    # event loop so a slow TMDB call doesn't stall other requests / WS pushes.
    episodes_raw = await asyncio.to_thread(
        fetch_season_episodes, str(job.tmdb_id), season_num, config.tmdb_api_key
    )
    if not episodes_raw:
        return SeasonRosterResponse(
            available=False,
            season_number=season_num,
            show_id=job.tmdb_id,
            season_count=season_count,
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
        if not match or int(match.group(1)) != season_num:
            continue
        assigned.setdefault(int(match.group(2)), []).append(title.id)

    present = sorted(assigned)
    lo, hi = (present[0], present[-1]) if present else (0, -1)

    # Per-episode subtitle-reference availability: which episodes the matcher
    # actually had a reference for (precomputed vector or downloaded SRT) vs the
    # silent gap (none). Best-effort decoration off the event loop — a cache-scan
    # failure must never break review, so we default every slot to "has a
    # reference" rather than crying wolf.
    cache_dir = Path(config.subtitles_cache_path).expanduser()
    episode_numbers = [ep["episode_number"] for ep in episodes_raw]
    try:
        coverage = await asyncio.to_thread(
            reference_coverage,
            cache_dir,
            job.tmdb_id,
            job.detected_title or "",
            season_num,
            episode_numbers,
        )
    except Exception as e:  # noqa: BLE001 — coverage is decoration, not load-bearing
        logger.debug(
            "Reference-coverage scan failed for show %s S%s: %s",
            sanitize_log_value(job.tmdb_id),
            sanitize_log_value(f"{season_num:02d}"),
            sanitize_log_value(str(e)),
        )
        coverage = {}

    episodes = [
        RosterEpisode(
            episode_code=(code := f"S{season_num:02d}E{ep['episode_number']:02d}"),
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
            reference_source=coverage.get(code),
            has_reference=coverage.get(code, "precomputed") != "missing",
        )
        for ep in episodes_raw
    ]

    # Episode-ordering options (#200): which orderings exist for this show and
    # whether any renumbers an episode actually matched on this disc. Cached at
    # the TMDB layer; off-thread so a cold fetch doesn't stall the event loop.
    from app.core import episode_ordering
    from app.services.episode_ordering_service import resolve_show_ordering

    current_ordering, _ = await resolve_show_ordering(job.tmdb_id, session)
    roster_pairs = [(season_num, ep["episode_number"]) for ep in episodes_raw]
    matched_pairs = [(season_num, ep_num) for ep_num in present]
    ordering_data = await asyncio.to_thread(
        episode_ordering.build_ordering_options,
        str(job.tmdb_id),
        season_num,
        roster_pairs,
        matched_pairs,
        config.tmdb_api_key,
        current_ordering,
    )

    return SeasonRosterResponse(
        available=True,
        season_number=season_num,
        show_id=job.tmdb_id,
        season_count=season_count,
        episodes=episodes,
        ordering_available=ordering_data["available"],
        ordering_diverges=ordering_data["diverges"],
        current_ordering=ordering_data["current"],
        ordering_options=[OrderingOption(**o) for o in ordering_data["options"]],
    )


class ManualSubtitleFileIn(BaseModel):
    """One file in a manual-subtitle preview request, as read client-side."""

    filename: str
    content: str


class ManualSubtitlePreviewRequest(BaseModel):
    files: list[ManualSubtitleFileIn]


class ManualSubtitlePreviewResult(BaseModel):
    filename: str
    season: int | None = None
    episode: int | None = None
    status: Literal["ready", "already_covered", "unparseable", "invalid_content", "duplicate"]
    warning: str | None = None


class ManualSubtitlePreviewResponse(BaseModel):
    results: list[ManualSubtitlePreviewResult]


class ManualSubtitleCommitFileIn(BaseModel):
    filename: str
    season: int
    episode: int
    content: str


class ManualSubtitleCommitRequest(BaseModel):
    files: list[ManualSubtitleCommitFileIn]


class ManualSubtitleCommitOutcome(BaseModel):
    filename: str
    season: int
    episode: int
    status: Literal["imported", "skipped", "error"]
    reason: str | None = None


class ManualSubtitleCommitResponse(BaseModel):
    outcomes: list[ManualSubtitleCommitOutcome]


def _require_identified_tv_job(job: DiscJob) -> None:
    if job.content_type != ContentType.TV or not job.tmdb_id or not job.detected_title:
        raise HTTPException(
            status_code=400, detail="Job must be an identified TV show to import manual subtitles"
        )
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot import manual subtitles in state: {job.state.value}",
        )


@router.post("/jobs/{job_id}/subtitles/preview", response_model=ManualSubtitlePreviewResponse)
async def preview_manual_subtitles(
    request: ManualSubtitlePreviewRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> ManualSubtitlePreviewResponse:
    """Classify a batch of user-supplied .srt files before import.

    Read-only — does not write anything. See ``classify_files`` for the
    per-file status logic (ready / already_covered / unparseable /
    invalid_content / duplicate).
    """
    _require_identified_tv_job(job)
    if len(request.files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_BATCH})")

    from app.services.config_service import get_config

    config = await get_config()
    cache_dir = Path(config.subtitles_cache_path).expanduser()

    results = await asyncio.to_thread(
        classify_files,
        cache_dir,
        job.tmdb_id,
        job.detected_title,
        [PreviewInputFile(filename=f.filename, content=f.content) for f in request.files],
    )
    return ManualSubtitlePreviewResponse(
        results=[
            ManualSubtitlePreviewResult(
                filename=r.filename,
                season=r.season,
                episode=r.episode,
                status=r.status,
                warning=r.warning,
            )
            for r in results
        ]
    )


@router.post("/jobs/{job_id}/subtitles/commit", response_model=ManualSubtitleCommitResponse)
async def commit_manual_subtitles(
    request: ManualSubtitleCommitRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> ManualSubtitleCommitResponse:
    """Write the user-confirmed subset of previewed files into the subtitle
    cache. Re-validates independently of the preview step (see ``commit_files``).
    """
    _require_identified_tv_job(job)
    if len(request.files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_BATCH})")

    from app.services.config_service import get_config

    config = await get_config()
    cache_dir = Path(config.subtitles_cache_path).expanduser()

    outcomes = await asyncio.to_thread(
        commit_files,
        cache_dir,
        job.tmdb_id,
        job.detected_title,
        [
            CommitInputFile(
                filename=f.filename, season=f.season, episode=f.episode, content=f.content
            )
            for f in request.files
        ],
    )
    return ManualSubtitleCommitResponse(
        outcomes=[
            ManualSubtitleCommitOutcome(
                filename=o.filename,
                season=o.season,
                episode=o.episode,
                status=o.status,
                reason=o.reason,
            )
            for o in outcomes
        ]
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
        "candidates_json": job.candidates_json,
        "identity_prompt_json": job.identity_prompt_json,
        "conflict_status": job.conflict_status,
        "tmdb_degraded_reason": job.tmdb_degraded_reason,
        "classification_source": job.classification_source,
        "classification_confidence": job.classification_confidence,
        "tmdb_id": job.tmdb_id,
        "tmdb_name": job.tmdb_name,
        "tmdb_year": job.tmdb_year,
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


@router.post("/jobs/{job_id}/titles/{title_id}/rerip")
async def rerip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Manually re-rip a single rip-failed title using the disc in the drive."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    try:
        await job_manager.rerip_title_manual(job.id, title_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "reripping", "job_id": job.id, "title_id": title_id}


@router.post("/jobs/{job_id}/titles/{title_id}/skip-rip")
async def skip_rip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Skip a queued/not-yet-ripped title so MakeMKV does not rip it."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    ok = await job_manager.skip_rip_title(job.id, title_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Title not found, not part of this job, or not skippable "
            "(only queued/not-yet-ripped titles can be skipped)",
        )
    return {"status": "skipped", "job_id": job.id, "title_id": title_id}


@router.post("/jobs/{job_id}/titles/{title_id}/unskip-rip")
async def unskip_rip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Reverse a skip while the title's file has not been written yet."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    ok = await job_manager.unskip_rip_title(job.id, title_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Title not found or no longer un-skippable (already ripped)",
        )
    return {"status": "unskipped", "job_id": job.id, "title_id": title_id}


@router.post("/jobs/{job_id}/review")
async def submit_review(
    review: ReviewRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Submit a review decision for a title."""
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    try:
        await job_manager.apply_review(
            job.id, review.title_id, episode_code=review.episode_code, edition=review.edition
        )
    except ValueError as e:
        # e.g. an unknown title_id — a client error, not a server fault.
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"status": "reviewed", "job_id": job.id}


@router.post("/jobs/{job_id}/review/batch")
async def submit_review_batch(
    review: ReviewBatchRequest,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Submit several review decisions for a job in one atomic request.

    Backs the review-tab multiselect bulk actions (mark many titles as extra,
    discard, etc.). Decisions are applied together and the job is finalized once,
    avoiding the FILE_EXISTS collisions that repeated single-title saves can hit.
    """
    if job.state != JobState.REVIEW_NEEDED:
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    from app.services.job_manager import job_manager

    decisions = [d.model_dump() for d in review.decisions]
    try:
        await job_manager.apply_review_batch(job.id, decisions)
    except ValueError as e:
        # e.g. an unknown title_id in one of the decisions — a client error.
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"status": "reviewed", "job_id": job.id, "count": len(decisions)}


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
    """Set a user-provided name for a disc with unreadable volume label and resume the pipeline.

    Accepted while RIPPING too (walk-away B5): the non-blocking identity CTA can
    be answered mid-rip — metadata updates and parked titles dispatch without
    interrupting the rip.
    """
    if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING):
        raise HTTPException(status_code=400, detail="Job is not awaiting name input")

    from app.services.job_manager import job_manager

    try:
        await job_manager.set_name_and_resume(job.id, req.name, req.content_type, req.season)
    except ValueError as e:
        # TOCTOU: the state check above read a snapshot; the coordinator
        # re-validates on a fresh row and raises if the job moved on (e.g.
        # the rip finished and organized between the check and the call).
        raise HTTPException(status_code=409, detail=str(e)) from e
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
    """Re-identify a disc with user-corrected title, content type, and optional TMDB ID.

    Accepted while RIPPING too (walk-away B5): a mid-rip answer updates the
    metadata and dispatches parked titles without interrupting the rip.
    """
    # REVIEW_NEEDED / RIPPING only — NOT IDENTIFYING: re-identifying a job whose
    # identify_disc task is still in flight races a second rip against the same
    # drive (see IdentificationCoordinator.re_identify's guard for the full rationale, #520).
    if job.state not in (JobState.REVIEW_NEEDED, JobState.RIPPING):
        raise HTTPException(
            status_code=400,
            detail=f"Job must be in review_needed or ripping state, currently: {job.state.value}",
        )

    from app.services.job_manager import job_manager

    try:
        await job_manager.re_identify_job(
            job.id, req.title, req.content_type, req.season, req.tmdb_id
        )
    except ValueError as e:
        # TOCTOU: the state check above read a snapshot; the coordinator
        # re-validates on a fresh row and raises if the job moved on.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"status": "re-identifying", "job_id": job.id}


class ArmManualRequest(BaseModel):
    """Arm a drive so the next disc adopts this identity verbatim."""

    drive_id: str
    title: str
    content_type: Literal["tv", "movie"]
    season: int | None = None
    tmdb_id: int | None = None
    disc_number: int | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v.strip()


class DisarmManualRequest(BaseModel):
    drive_id: str


@router.post("/manual/arm")
async def arm_manual_identity(
    req: ArmManualRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Arm a drive with a user-asserted identity for the next disc inserted.

    Rejects with 409 when the drive already holds a non-terminal job: that disc
    is already being worked on, and the caller should edit that job's identity
    instead of arming for a disc that will not be inserted.
    """
    from app.models.disc_job import TERMINAL_JOB_STATES
    from app.services.manual_identity import ManualIdentity, arm_store

    result = await session.execute(
        select(DiscJob).where(
            DiscJob.drive_id == req.drive_id,
            DiscJob.state.notin_(list(TERMINAL_JOB_STATES)),
        )
    )
    if result.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Drive {req.drive_id} already has an active job. "
                f"Edit that job's identity instead of arming the drive."
            ),
        )

    identity = ManualIdentity(
        title=req.title,
        content_type=req.content_type,
        season=req.season,
        tmdb_id=req.tmdb_id,
        disc_number=req.disc_number,
    )
    arm_store.arm(req.drive_id, identity)

    from app.api.websocket import manager as ws_manager

    await ws_manager.broadcast_drive_armed(req.drive_id, identity.to_dict())
    logger.info(
        f"Armed drive {sanitize_log_value(req.drive_id)} with manual identity "
        f"'{sanitize_log_value(req.title)}' ({sanitize_log_value(req.content_type)})"
    )
    return {"status": "armed", "drive_id": req.drive_id}


@router.post("/manual/disarm")
async def disarm_manual_identity(req: DisarmManualRequest) -> dict:
    """Clear a drive's armed identity. Idempotent."""
    from app.services.manual_identity import arm_store

    was_armed = arm_store.disarm(req.drive_id)
    if was_armed:
        from app.api.websocket import manager as ws_manager

        await ws_manager.broadcast_drive_armed(req.drive_id, None)
    return {
        "status": "disarmed" if was_armed else "not_armed",
        "drive_id": req.drive_id,
    }


@router.get("/manual/armed")
async def list_armed_drives() -> dict:
    """Snapshot every currently-armed drive.

    Lets the dashboard restore its ``ArmedDriveCard``s on page load and after a
    WebSocket reconnect (the ``drive_armed`` push only carries live deltas), the
    same way ``GET /api/parked-discs`` reseeds parked discs.
    """
    from app.services.manual_identity import arm_store

    return {
        "armed": {
            drive_id: identity.to_dict() for drive_id, identity in arm_store.all_armed().items()
        }
    }


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
        job_manager._download_subtitles(
            job.id, job.detected_title, job.detected_season, job.tmdb_id
        )
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
        tmdb_configured=bool(config.tmdb_api_key),
        max_concurrent_matches=config.max_concurrent_matches,
        enable_gpu_acceleration=config.enable_gpu_acceleration,
        # Background pre-transcription (transcript cache prewarmer)
        enable_background_pretranscription=config.enable_background_pretranscription,
        pretranscribe_full_file=config.pretranscribe_full_file,
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
        # Drive behavior
        auto_eject_enabled=config.auto_eject_enabled,
        # Staging cleanup
        staging_cleanup_policy=config.staging_cleanup_policy,
        staging_cleanup_days=config.staging_cleanup_days,
        # Extras & naming
        extras_policy=config.extras_policy,
        naming_season_format=config.naming_season_format,
        naming_episode_format=config.naming_episode_format,
        naming_movie_format=config.naming_movie_format,
        naming_tv_show_format=config.naming_tv_show_format,
        # Episode ordering (#200)
        episode_ordering_preference=config.episode_ordering_preference,
        # AI identification
        ai_identification_enabled=config.ai_identification_enabled,
        ai_provider=config.ai_provider,
        ai_api_key="***" if config.ai_api_key else "",  # Redacted
        ai_episode_matching_enabled=config.ai_episode_matching_enabled,
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
        # Chromaprint fingerprinting (Phase 1)
        fpcalc_path=config.fpcalc_path or "",
        enable_fingerprint_contributions=config.enable_fingerprint_contributions,
        # Chromaprint Phase 2
        fingerprint_server_url=config.fingerprint_server_url,
        fingerprint_disclosure_accepted=config.fingerprint_disclosure_accepted,
        fingerprint_disclosure_accepted_at=config.fingerprint_disclosure_accepted_at,
        contribution_pseudonym=config.contribution_pseudonym,
        # Notifications
        discord_webhook_url="***" if config.discord_webhook_url else "",
        # Coalesce None->"" defensively: a DB upgraded by an early 0.26.0 build may
        # already hold NULL here (see database._add_missing_columns), and
        # ConfigResponse requires str — a bare None would 500 GET /api/config.
        discord_template_completed=config.discord_template_completed or "",
        discord_template_failed=config.discord_template_failed or "",
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
    # Allow None through for fields that can be cleared to null
    # NOTE: fingerprint_disclosure_accepted_at is intentionally absent — it is
    # not a ConfigUpdate field, so it never arrives via model_dump(). It's
    # managed server-side in the disclosure block below and cleared through
    # config_service.update_config's own _nullable_fields.
    _nullable_fields = {"import_watch_path", "fingerprint_server_url"}
    update_data = {
        k: v for k, v in config.model_dump().items() if v is not None or k in _nullable_fields
    }

    # Validate fingerprint_server_url against SSRF before persisting
    if update_data.get("fingerprint_server_url"):
        from app.core.security import is_safe_remote_url

        if not is_safe_remote_url(update_data["fingerprint_server_url"]):
            raise HTTPException(
                status_code=422,
                detail="fingerprint_server_url must be an http/https URL pointing to a non-internal host",
            )

    # Validate discord_webhook_url against SSRF before persisting
    if update_data.get("discord_webhook_url"):
        from app.core.security import is_safe_remote_url

        if not is_safe_remote_url(update_data["discord_webhook_url"]):
            raise HTTPException(
                status_code=422,
                detail="discord_webhook_url must be an http/https URL pointing to a non-internal host",
            )

    # Validate Discord notification templates before persisting
    from app.core.discord_notifier import validate_discord_template

    for field in ("discord_template_completed", "discord_template_failed"):
        if update_data.get(field):
            error = validate_discord_template(update_data[field])
            if error:
                raise HTTPException(status_code=422, detail=f"{field}: {error}")

    # Validate naming format strings before persisting
    from app.core.organizer import (
        ALLOWED_EPISODE_PLACEHOLDERS,
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        ALLOWED_TV_SHOW_PLACEHOLDERS,
        validate_naming_format,
    )

    format_checks = [
        ("naming_season_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_episode_format", ALLOWED_EPISODE_PLACEHOLDERS),
        ("naming_movie_format", ALLOWED_MOVIE_PLACEHOLDERS),
        ("naming_tv_show_format", ALLOWED_TV_SHOW_PLACEHOLDERS),
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

    # Validate episode_ordering_preference (#200) — reject absolute (deferred) and unknowns
    if "episode_ordering_preference" in update_data:
        from app.core.episode_ordering import ALLOWED_ORDERINGS

        if update_data["episode_ordering_preference"] not in ALLOWED_ORDERINGS:
            raise HTTPException(
                status_code=422,
                detail=f"episode_ordering_preference must be one of {sorted(ALLOWED_ORDERINGS)}",
            )

    # Keep the disclosure-acceptance timestamp consistent with the flag,
    # server-side: stamp it on accept, clear it on explicit revoke.
    if "fingerprint_disclosure_accepted" in update_data:
        if update_data["fingerprint_disclosure_accepted"] is True:
            update_data["fingerprint_disclosure_accepted_at"] = datetime.now(UTC)
        else:
            update_data["fingerprint_disclosure_accepted_at"] = None

    if update_data:
        await update_db_config(**update_data)

    # Completing first-run setup releases any disc parked by the setup gate
    # (inserted while setup_complete was false): replay its insert event so
    # ripping starts without an eject/reinsert. The wizard sends
    # setup_complete=true on every save, but resume is a no-op unless
    # something is actually parked.
    if update_data.get("setup_complete"):
        from app.services.job_manager import job_manager

        await job_manager.resume_parked_discs()

    return {"status": "updated", "persisted": True}


@router.get("/parked-discs")
async def get_parked_discs() -> dict:
    """Discs detected while first-run setup was incomplete (pipeline parked).

    Seeds the dashboard banner on page load; live changes ride the
    ``parked_discs`` WebSocket broadcast.
    """
    from app.services.job_manager import job_manager

    return {"discs": job_manager.parked_discs}


@router.get("/jobs/{job_id}/poster")
async def get_job_poster(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Get the TMDB poster URL for a job.

    Prefer the authoritative ``job.tmdb_id`` (exact, immune to a garbled
    detected_title); fall back to a name search only when no id is set.
    """
    import requests

    from app.core.tmdb_classifier import _build_auth
    from app.matcher.tmdb_client import BASE_IMAGE_URL
    from app.services.config_service import get_config as get_db_config

    config = await get_db_config()
    api_key = config.tmdb_api_key
    if not api_key:
        return {"poster_url": None}

    media = "movie" if job.content_type == "movie" else "tv"
    headers, params = _build_auth(api_key)

    try:
        if job.tmdb_id:
            # int() sanitizes the id into the URL (no path/host injection, and clears
            # CodeQL's partial-SSRF taint); the guard above ensures it is set.
            detail_url = f"https://api.themoviedb.org/3/{media}/{int(job.tmdb_id)}"
            response = await asyncio.to_thread(
                requests.get, detail_url, headers=headers, params=params, timeout=10
            )
            if response.status_code == 200:
                poster_path = response.json().get("poster_path")
                if poster_path:
                    return {"poster_url": f"{BASE_IMAGE_URL}{poster_path}"}
            return {"poster_url": None}

        if not job.detected_title:
            return {"poster_url": None}
        search_url = f"https://api.themoviedb.org/3/search/{media}"
        params["query"] = job.detected_title
        response = await asyncio.to_thread(
            requests.get, search_url, headers=headers, params=params, timeout=10
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                return {"poster_url": f"{BASE_IMAGE_URL}{results[0]['poster_path']}"}
    except Exception as e:
        logger.warning(f"Error fetching poster: {e}", exc_info=True)

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


@router.get("/fingerprint/contributions", dependencies=[Depends(require_localhost)])
async def list_fingerprint_contributions(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(200, ge=1, le=1000),
    include_log: bool = Query(False),
) -> dict:
    """Return locally-queued fingerprint contributions (Phase 1 audit log).

    Excludes the chromaprint blob body — only summarizes byte size — so the response
    stays manageable. Phase 2 adds filtering by upload status.

    When ``include_log=true``, the response also carries an ``audit_log`` key: the
    tail (last 200 entries) of the append-only JSONL upload log the uploader writes
    on each successful contribution — what makes "exactly what left my machine"
    auditable from the dashboard.

    Localhost-only: the response includes recent ripping activity (TMDB IDs,
    season/episode, timestamps), which is the user's viewing history. The
    `require_localhost` guard rejects LAN peers even when `allow_lan_access`
    has opened the bind address.
    """
    from sqlalchemy import func

    from app.models.fingerprint import FingerprintContribution

    # Select metadata columns plus the blob *length* (not the blob itself) so we
    # don't pull tens of megabytes of fingerprint data through SQLite just to
    # report a size summary.
    fc = FingerprintContribution
    result = await session.execute(
        select(
            fc.id,
            fc.queued_at,
            fc.title_id,
            fc.tmdb_id,
            fc.season,
            fc.episode,
            fc.match_confidence,
            fc.match_source,
            fc.uploaded_at,
            fc.upload_attempts,
            fc.upload_status,
            fc.upload_error_msg,
            func.length(fc.chromaprint_blob).label("blob_size_bytes"),
        )
        .order_by(fc.queued_at.desc())
        .limit(limit)
    )
    rows = result.all()
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
            "upload_status": r.upload_status,
            "upload_error_msg": r.upload_error_msg,
            "blob_size_bytes": r.blob_size_bytes or 0,
        }
        for r in rows
    ]
    payload: dict = {"count": len(items), "items": items}

    if include_log:
        # Tail the append-only JSONL upload log written by ContributionUploader.
        # Missing file or a malformed line must never 500 this read-only endpoint.
        from app.services.contribution_uploader import CONTRIBUTION_LOG_PATH

        audit: list[dict] = []
        try:
            if CONTRIBUTION_LOG_PATH.exists():
                lines = CONTRIBUTION_LOG_PATH.read_text(encoding="utf-8").splitlines()
                for line in lines[-200:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        audit.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            logger.warning("Failed to read contribution audit log", exc_info=True)
        payload["audit_log"] = audit

    return payload


@router.delete("/fingerprint/contributions/{contrib_id}", dependencies=[Depends(require_localhost)])
async def forget_fingerprint_contribution(
    contrib_id: int,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a locally-queued fingerprint contribution (forget).

    Returns 400 if the contribution was already uploaded — the data already
    exists on the server and cannot be recalled from here (use /fingerprint/forget
    to recall server-side data). Returns 409 if the row has been attempted and is
    still retrying, unless `force=true` is passed.

    `force=true` is the escape hatch for the resilience behavior: transient upload
    failures keep a row pending (`upload_status=None`, `upload_attempts>0`) and
    retry across drains indefinitely, so without force a row stuck against a dead
    server could never be deleted individually. force overrides ONLY that retry
    guard — it never bypasses the already-uploaded (400) guard.
    """
    from app.models.fingerprint import FingerprintContribution

    contrib = await session.get(FingerprintContribution, contrib_id)
    if contrib is None:
        raise HTTPException(status_code=404, detail="Contribution not found")
    if contrib.upload_status == "success":
        # Never bypassed, even with force — the data is already on the server.
        raise HTTPException(
            status_code=400,
            detail="Cannot delete an already-uploaded contribution; the data is already on the server.",
        )
    if not force and contrib.upload_status is None and contrib.upload_attempts > 0:
        # upload_attempts > 0 means the background uploader has already tried this
        # row at least once and may be holding it in an active HTTP call; deleting
        # now could be a silent no-op UPDATE on a ghost row. Transient failures keep
        # the row pending (status=None) and retry across drains rather than reaching
        # a terminal "failed", so this guard holds until the row eventually uploads.
        raise HTTPException(
            status_code=409,
            detail=(
                "Contribution upload already attempted; it will keep retrying on "
                "later drains until it succeeds. To retract it now, retry with "
                "force=true, or opt out / use the forget action."
            ),
        )
    await session.delete(contrib)
    await session.commit()
    return {"status": "deleted", "contrib_id": contrib_id, "forced": force}


@router.post(
    "/fingerprint/contributions/rotate-pseudonym", dependencies=[Depends(require_localhost)]
)
async def rotate_contribution_pseudonym(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a fresh pseudonym and re-tag all pending contributions.

    Already-uploaded rows retain their old pseudonym (server already has them
    under that identity). Pending rows get the new pseudonym so future uploads
    are unlinkable to past ones.
    """
    from app.models.fingerprint import FingerprintContribution
    from app.services.config_service import update_config as update_db_config
    from app.services.contribution_pseudonym import generate_pseudonym

    new_pseudonym = generate_pseudonym()

    # update_db_config auto-creates the app_config row when absent, so the
    # pseudonym is always persisted even on a fresh database.
    await update_db_config(contribution_pseudonym=new_pseudonym)

    pending = (
        (
            await session.execute(
                select(FingerprintContribution).where(
                    FingerprintContribution.upload_status.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for row in pending:
        row.pseudonym = new_pseudonym

    await session.commit()
    return {"pseudonym": new_pseudonym, "pending_retagged": len(pending)}


@router.post("/fingerprint/forget", dependencies=[Depends(require_localhost)])
async def forget_fingerprint_on_server(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Server-side 'forget me': delete this install's raw contributions on the
    fingerprint network, wipe the local un-uploaded queue, and rotate to a fresh
    pseudonym + reset disclosure consent so future contributions are unlinkable
    to the old identity.

    Already-promoted canonical fingerprints cannot be recalled (the server
    reports canonical_unaffected) — only this pseudonym's raw rows are deleted.
    """
    import httpx
    from sqlalchemy import delete as sa_delete

    from app.models.fingerprint import FingerprintContribution
    from app.services.config_service import get_config
    from app.services.config_service import update_config as update_db_config
    from app.services.contribution_pseudonym import generate_pseudonym

    cfg = await get_config()
    old_pseudonym = cfg.contribution_pseudonym or ""
    if not old_pseudonym:
        raise HTTPException(status_code=400, detail="No pseudonym to forget")

    # NULL/blank stored URL resolves to the default network base origin, so a
    # forget always reaches the same server the uploader contributes to.
    from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL

    server_url = cfg.fingerprint_server_url or DEFAULT_FINGERPRINT_SERVER_URL
    server_rows_deleted = 0
    if server_url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{server_url.rstrip('/')}/v1/forget",
                    json={"pseudonym": old_pseudonym},
                )
                r.raise_for_status()
                server_rows_deleted = int(r.json().get("rows_deleted", 0))
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=503, detail=f"Could not reach fingerprint server: {e}"
            ) from e

    # Rotate identity + revoke consent FIRST, then wipe the local queue. If the
    # local delete failed after a config update, the worst case is leftover
    # un-uploaded rows under the old pseudonym — harmless, since consent is now
    # revoked so nothing uploads. The reverse order would risk the old pseudonym
    # surviving with consent intact and re-contributing the identity we just
    # asked the server to forget.
    new_pseudonym = generate_pseudonym()
    await update_db_config(
        contribution_pseudonym=new_pseudonym,
        fingerprint_disclosure_accepted=False,
        fingerprint_disclosure_accepted_at=None,
    )

    result = await session.execute(
        sa_delete(FingerprintContribution).where(FingerprintContribution.uploaded_at.is_(None))
    )
    local_rows_deleted = result.rowcount or 0
    await session.commit()

    return {
        "old_pseudonym": old_pseudonym,
        "new_pseudonym": new_pseudonym,
        "local_rows_deleted": local_rows_deleted,
        "server_rows_deleted": server_rows_deleted,
    }


# --- Bootstrap Library Endpoints ---
# NOTE: The accept endpoint processes items synchronously. Very large libraries
# should be chunked by the caller (e.g. 50–100 items per request). A future
# enhancement could background the extraction work for very large batches.


class BootstrapScanRequest(BaseModel):
    """Request body for /fingerprint/bootstrap/scan."""

    path: str


class BootstrapEpisodeItem(BaseModel):
    """A single parseable episode found during a library scan."""

    file: str
    season: int
    episode: int


class BootstrapShowResult(BaseModel):
    """Per-show grouping returned by /fingerprint/bootstrap/scan."""

    folder_name: str
    tmdb_id: int | None
    tmdb_name: str | None
    tmdb_year: int | None
    resolved: bool
    episode_count: int
    episodes: list[BootstrapEpisodeItem]


class BootstrapUnparseableItem(BaseModel):
    """A media file whose name could not be parsed as Show - SnnEnn."""

    file: str


class BootstrapScanSummary(BaseModel):
    total_files: int
    parsed: int
    shows: int
    unparseable: int


class BootstrapScanResponse(BaseModel):
    shows: list[BootstrapShowResult]
    unparseable: list[BootstrapUnparseableItem]
    summary: BootstrapScanSummary


class BootstrapAcceptItem(BaseModel):
    """One file + resolved metadata the user confirmed for fingerprinting."""

    file: str
    tmdb_id: int
    season: int
    episode: int
    # Human-readable show name (from the scan's tmdb_name/folder_name) for local
    # display/diagnostics. Optional — uploads key off tmdb_id+season+episode.
    show_title: str | None = None


class BootstrapAcceptRequest(BaseModel):
    """Request body for /fingerprint/bootstrap/accept."""

    items: list[BootstrapAcceptItem]


class BootstrapAcceptResponse(BaseModel):
    """Response body for /fingerprint/bootstrap/accept.

    ``queued`` counts episodes confirmed in the local contribution queue —
    including items already queued by a prior call (idempotent re-runs).
    """

    queued: int
    failed: int


@router.post(
    "/fingerprint/bootstrap/scan",
    dependencies=[Depends(require_localhost)],
    response_model=BootstrapScanResponse,
)
async def bootstrap_scan(req: BootstrapScanRequest) -> BootstrapScanResponse:
    """Walk an existing TV library and group files by show for TMDB resolution.

    The caller supplies the root directory of a TV library whose files are
    already correctly labeled with the canonical ``Show - SnnEnn.ext`` naming
    convention.  The endpoint:

    1. Validates that the path exists and is a directory (400 otherwise).
    2. Globs all ``.mkv``/``.mp4``/``.m2ts`` files under the path (excluding
       ``Extras`` directories).
    3. Uses ``walk_library`` + ``parse_episode_filename`` to distinguish
       parseable episodes from unparseable files.
    4. Groups parseable episodes by show name and tries ``fetch_show_id`` for
       each unique show (TMDB lookups are cached within the request).
    5. Returns a per-show breakdown so the UI can confirm auto-resolved shows
       and offer a manual TMDB picker for the rest.

    Localhost-only: the response includes the user's local library paths.
    """
    from app.matcher.tmdb_client import fetch_show_details, fetch_show_id
    from app.scripts.bootstrap_library import parse_episode_filename, walk_library

    # Canonicalize the user-supplied root so the symlink-containment check
    # below has a stable base. The endpoint is localhost-only and the path is
    # the user's own library, but rglob/exists follow symlinks, so confine
    # every hit to the resolved tree.
    root = Path(req.path).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(
            status_code=400, detail=f"Path does not exist or is not a directory: {req.path}"
        )

    # --- Collect all media files, skipping Extras ---
    _MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m2ts"}

    all_media: set[Path] = set()
    for ext in _MEDIA_EXTENSIONS:
        for p in root.rglob(f"*{ext}"):
            # rglob follows symlinks; skip any hit whose real path escapes the
            # scanned tree so a crafted symlink can't surface outside files.
            try:
                if not p.resolve().is_relative_to(root):
                    continue
            except (OSError, ValueError):
                continue
            if "Extras" not in p.parts:
                all_media.add(p)

    # --- Separate parseable from unparseable ---
    # walk_library yields (path, (show, season, ep)) for parseable MKV files;
    # any media file not returned by walk_library is unparseable.
    parseable_paths: set[Path] = set()
    # show_name -> list of (path, season, episode)
    show_episodes: dict[str, list[tuple[Path, int, int]]] = {}

    for file_path, (show, season, episode) in walk_library(root):
        parseable_paths.add(file_path)
        show_episodes.setdefault(show, []).append((file_path, season, episode))

    # Non-MKV parseable files: check mp4/m2ts against parse_episode_filename
    for p in all_media:
        if p in parseable_paths:
            continue
        if p.suffix.lower() in (".mp4", ".m2ts"):
            label = parse_episode_filename(p.name)
            if label is not None:
                show, season, episode = label
                parseable_paths.add(p)
                show_episodes.setdefault(show, []).append((p, season, episode))

    unparseable_files = [p for p in all_media if p not in parseable_paths]

    # --- TMDB resolution with per-request cache ---
    tmdb_id_cache: dict[str, str | None] = {}

    async def _resolve_show(show_name: str) -> str | None:
        if show_name not in tmdb_id_cache:
            # fetch_show_id hits TMDB; a network timeout / 429 must not bubble
            # up as an unhandled 500 — treat it as a miss (symmetric with
            # _get_details below and the CLI's _default_search).
            try:
                raw = await asyncio.to_thread(fetch_show_id, show_name)
            except Exception:
                logger.warning(f"TMDB show lookup failed for {show_name!r}", exc_info=True)
                raw = None
            tmdb_id_cache[show_name] = raw
        return tmdb_id_cache[show_name]

    # Fetch details (name + year) for resolved shows; cache by id string.
    tmdb_details_cache: dict[str, dict | None] = {}

    async def _get_details(tmdb_id_str: str) -> dict | None:
        if tmdb_id_str not in tmdb_details_cache:
            try:
                details = await asyncio.to_thread(fetch_show_details, int(tmdb_id_str))
            except Exception:
                logger.warning(f"TMDB details fetch failed for id {tmdb_id_str}", exc_info=True)
                details = None
            tmdb_details_cache[tmdb_id_str] = details
        return tmdb_details_cache[tmdb_id_str]

    # --- Build per-show results ---
    shows: list[BootstrapShowResult] = []
    for show_name, episodes in sorted(show_episodes.items()):
        raw_id = await _resolve_show(show_name)

        tmdb_id: int | None = None
        tmdb_name: str | None = None
        tmdb_year: int | None = None
        resolved = False

        if raw_id is not None:
            try:
                tmdb_id = int(raw_id)
                resolved = True
            except (TypeError, ValueError):
                # Non-numeric id from TMDB (shouldn't happen); leave unresolved.
                pass

        if tmdb_id is not None:
            details = await _get_details(str(tmdb_id))
            if details:
                tmdb_name = details.get("name") or details.get("original_name")
                first_air = details.get("first_air_date") or ""
                if first_air and len(first_air) >= 4:
                    try:
                        tmdb_year = int(first_air[:4])
                    except ValueError:
                        # Malformed first_air_date prefix; year stays None.
                        pass

        episode_items = [
            BootstrapEpisodeItem(file=str(p), season=s, episode=e)
            for p, s, e in sorted(episodes, key=lambda x: (x[1], x[2]))
        ]

        shows.append(
            BootstrapShowResult(
                folder_name=show_name,
                tmdb_id=tmdb_id,
                tmdb_name=tmdb_name,
                tmdb_year=tmdb_year,
                resolved=resolved,
                episode_count=len(episode_items),
                episodes=episode_items,
            )
        )

    parsed_count = sum(len(eps) for eps in show_episodes.values())
    total_files = parsed_count + len(unparseable_files)

    return BootstrapScanResponse(
        shows=shows,
        unparseable=[BootstrapUnparseableItem(file=str(p)) for p in sorted(unparseable_files)],
        summary=BootstrapScanSummary(
            total_files=total_files,
            parsed=parsed_count,
            shows=len(shows),
            unparseable=len(unparseable_files),
        ),
    )


@router.post(
    "/fingerprint/bootstrap/accept",
    dependencies=[Depends(require_localhost)],
    response_model=BootstrapAcceptResponse,
)
async def bootstrap_accept(
    req: BootstrapAcceptRequest,
    session: AsyncSession = Depends(get_session),
) -> BootstrapAcceptResponse:
    """Fingerprint a confirmed set of library files and enqueue contributions.

    The UI calls this after the user reviews the scan results and confirms
    (or manually corrects) each show's TMDB mapping.  For each item the
    endpoint:

    1. Extracts a chromaprint fingerprint via fpcalc (from ``cfg.fpcalc_path``
       or ``detect_fpcalc()``).
    2. Enqueues a ``FingerprintContribution`` row tagged ``match_source="bootstrap"``
       and ``match_confidence=1.0`` (filename was ground truth).
    3. Per-file extraction failures are counted and do NOT abort the batch.

    Returns ``{"queued": N, "failed": M}``.

    Localhost-only: processes local library files on behalf of the user.
    """
    from app.api.validation import detect_ffmpeg, detect_fpcalc
    from app.matcher.chromaprint_extractor import ChromaprintExtractor
    from app.models.fingerprint import FingerprintContribution
    from app.services.config_service import get_config
    from app.services.contribution_queue import ContributionQueue

    cfg = await get_config()

    # Resolve fpcalc path: prefer explicit config, fall back to auto-detection.
    fpcalc_path = cfg.fpcalc_path
    if not fpcalc_path:
        detected = await asyncio.to_thread(detect_fpcalc)
        fpcalc_path = detected.path if detected.found else None

    if not fpcalc_path:
        raise HTTPException(
            status_code=400,
            detail=(
                "fpcalc is not available. Install it (libchromaprint-tools / chromaprint) "
                "and set its path in Engram settings, or ensure it is on PATH."
            ),
        )

    # Resolve ffmpeg too — it backs the pre-decode fallback for codecs fpcalc's
    # bundled FFmpeg can't decode (DTS/TrueHD/FLAC/E-AC-3). Optional: if absent,
    # the fallback is simply disabled and such files are reported as failures.
    ffmpeg_path = cfg.ffmpeg_path
    if not ffmpeg_path:
        detected_ffmpeg = await asyncio.to_thread(detect_ffmpeg)
        ffmpeg_path = detected_ffmpeg.path if detected_ffmpeg.found else None

    extractor = ChromaprintExtractor(fpcalc_path=fpcalc_path, ffmpeg_path=ffmpeg_path)
    queue = ContributionQueue()
    pseudonym = cfg.contribution_pseudonym or ""

    # Idempotency guard: bootstrap is re-runnable and the UI submits in batches,
    # so a double-click — or a batch retried after it actually succeeded
    # server-side — must not insert the same episode twice (the uploader would
    # then push duplicate rows to the network). Skip any (tmdb_id, season,
    # episode) already present as a bootstrap row, plus duplicates within this
    # request. Keyed off episode identity since the filename was ground truth.
    request_tmdb_ids = {item.tmdb_id for item in req.items}
    existing_keys: set[tuple[int, int | None, int | None]] = set()
    if request_tmdb_ids:
        existing_rows = await session.execute(
            select(
                FingerprintContribution.tmdb_id,
                FingerprintContribution.season,
                FingerprintContribution.episode,
            ).where(
                FingerprintContribution.match_source == "bootstrap",
                FingerprintContribution.tmdb_id.in_(request_tmdb_ids),
            )
        )
        existing_keys = {(r[0], r[1], r[2]) for r in existing_rows.all()}

    queued = 0
    failed = 0
    seen: set[tuple[int, int | None, int | None]] = set()

    for item in req.items:
        key = (item.tmdb_id, item.season, item.episode)
        if key in existing_keys or key in seen:
            # Already queued by a prior call or earlier in this batch — count it
            # as queued (the episode is in the queue) but don't duplicate it.
            queued += 1
            continue
        try:
            result = await extractor.extract(item.file)
            await queue.enqueue(
                session=session,
                title_id=None,
                chromaprint_blob=result.to_blob(),
                tmdb_id=item.tmdb_id,
                season=item.season,
                episode=item.episode,
                match_confidence=1.0,
                match_source="bootstrap",
                disc_content_hash=None,
                pseudonym=pseudonym,
                show_title=item.show_title,
                contributions_enabled=cfg.enable_fingerprint_contributions,
            )
            seen.add(key)
            queued += 1
        except Exception as exc:
            logger.warning(
                f"bootstrap_accept: fingerprint extraction failed for {item.file!r}: {exc}",
                exc_info=True,
            )
            failed += 1

    await session.commit()
    return BootstrapAcceptResponse(queued=queued, failed=failed)


# --- Simulation Endpoints (debug mode only) ---


# Blocking kinds park titles; "season" is the one non-blocking shortcut CTA.
_VALID_IDENTITY_PENDING = BLOCKING_KINDS | {"season"}


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
    identity_pending: str | None = None
    """Inject a walk-away identity prompt on the RIPPING job (DEBUG only).

    Allowed values: ``"name"`` | ``"season"`` | ``"reidentify"``.  Sets
    ``identity_prompt_json`` with the real reason literal the frontend keys on
    so E2E tests exercise the actual modal routing without a physical disc.
    Blocking kinds (``name`` / ``reidentify``) park titles in QUEUED;
    ``season`` lets titles dispatch normally.  When ``simulate_ripping=True``
    and a blocking prompt is pending, the completed rip converges to
    REVIEW_NEEDED (same as the real B4 path).
    """


@router.post("/simulate/insert-disc", dependencies=[Depends(require_debug)])
async def simulate_insert_disc(req: SimulateDiscRequest) -> dict:
    """Simulate a disc insertion. Only available in debug mode."""
    from app.services.job_manager import job_manager

    if req.identity_pending is not None and req.identity_pending not in _VALID_IDENTITY_PENDING:
        raise HTTPException(
            status_code=400,
            detail=(
                f"identity_pending must be one of {sorted(_VALID_IDENTITY_PENDING)!r}, "
                f"got {req.identity_pending!r}"
            ),
        )

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


@router.post("/simulate/step-job/{job_id}", dependencies=[Depends(require_debug)])
async def simulate_step_job(job_id: int) -> dict:
    """Advance a job via the state machine, firing terminal callbacks (Discord, etc). DEBUG only."""
    from app.services.job_manager import job_manager

    try:
        new_state = await job_manager.advance_job_via_state_machine(job_id)
        return {"status": "advanced", "job_id": job_id, "new_state": new_state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/simulate/fail-job/{job_id}", dependencies=[Depends(require_debug)])
async def simulate_fail_job(job_id: int, reason: str = "simulated failure") -> dict:
    """Transition a job to FAILED via the state machine, firing terminal callbacks. DEBUG only."""
    from app.services.job_manager import job_manager

    try:
        await job_manager.fail_job_via_state_machine(job_id, reason)
        return {"status": "failed", "job_id": job_id, "new_state": "failed"}
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


@router.post("/simulate/seed-incomplete-rip", dependencies=[Depends(require_debug)])
async def simulate_seed_incomplete_rip(
    volume_label: str = "DAMAGED_DISC_S1D1",
) -> dict:
    """Seed a REVIEW_NEEDED job with one incomplete_rip title. Debug mode only."""
    from app.services.job_manager import job_manager

    return await job_manager._simulation.seed_incomplete_rip(volume_label)


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


# --- Debug test-support endpoints (require DEBUG + localhost) ---


@router.post(
    "/debug/uploader/drain",
    dependencies=[Depends(require_localhost), Depends(require_debug)],
)
async def debug_drain_uploader(request: Request) -> dict:
    """Force a full uploader drain (DEBUG only).

    The uploader normally drains on a long poll interval; tests call this to
    drain the pending queue immediately. Returns {"ok": true}.
    """
    uploader = getattr(request.app.state, "contribution_uploader", None)
    if uploader is None:
        raise HTTPException(status_code=503, detail="uploader not running")
    await uploader._drain()
    return {"ok": True}


@router.post(
    "/debug/fingerprint/seed",
    dependencies=[Depends(require_localhost), Depends(require_debug)],
)
async def debug_seed_fingerprint(session: AsyncSession = Depends(get_session)) -> dict:
    """Seed one fake pending FingerprintContribution (DEBUG only).

    Tags it with the current pseudonym so the upload/forget flow treats it as a
    real local contribution. Lets E2E tests deterministically trigger the JIT
    disclosure modal without depending on the rip+match+chromaprint pipeline.
    """
    from app.matcher.chromaprint_extractor import ChromaprintResult
    from app.models.fingerprint import FingerprintContribution
    from app.services.config_service import get_config

    cfg = await get_config()
    blob = ChromaprintResult(
        hashes=[1, 2, 3, 4, 5], duration_seconds=42.0, fpcalc_version="debug-seed"
    ).to_blob()
    row = FingerprintContribution(
        chromaprint_blob=blob,
        tmdb_id=1399,
        season=1,
        episode=1,
        match_confidence=0.95,
        match_source="engram_asr",
        pseudonym=cfg.contribution_pseudonym or "00000000-0000-4000-8000-000000000000",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"ok": True, "contribution_id": row.id}


class ImportPathRequest(BaseModel):
    path: str


class ImportStartRequest(BaseModel):
    path: str
    destination_mode: str = "library"


@router.get("/import/browse", dependencies=[Depends(require_localhost_or_lan)])
async def import_browse(path: str = "") -> dict:
    """Read-only directory listing for the manual-import picker.

    Guarded by require_localhost_or_lan: reachable from the host machine always,
    and from LAN peers only when the user has enabled allow_lan_access (so
    headless/Docker deployments accessed from another device can import). Returns
    directory names with a shallow direct-child
    MKV count, plus selectable .mkv files. Never returns file contents. Empty
    path returns the drive roots (Windows) or / and home (POSIX).
    """
    if not path:
        if os.name == "nt":
            # Probing 26 drive letters can each block for seconds on a stale
            # network/UNC mapping, so run it off the event loop thread.
            roots = await asyncio.to_thread(
                lambda: [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            )
        else:
            roots = ["/", str(Path.home())]
        return {"cwd": None, "parent": None, "roots": roots, "entries": []}

    try:
        p = Path(path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries: list[dict] = []
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_dir(follow_symlinks=False):
                    count = 0
                    try:
                        for f in os.scandir(entry.path):
                            if f.is_file(follow_symlinks=False) and f.name.lower().endswith(".mkv"):
                                count += 1
                    except OSError:
                        count = 0
                    entries.append(
                        {"name": entry.name, "path": entry.path, "type": "dir", "mkv_count": count}
                    )
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".mkv"):
                    entries.append({"name": entry.name, "path": entry.path, "type": "mkv"})
            except OSError:
                continue
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Cannot read directory") from exc

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    parent = str(p.parent) if p.parent != p else None
    logger.info("Import browse: %s (%d entries)", sanitize_log_value(str(p)), len(entries))
    return {"cwd": str(p), "parent": parent, "roots": [], "entries": entries}


@router.post("/import/preview", dependencies=[Depends(require_localhost_or_lan)])
async def import_preview(req: ImportPathRequest) -> dict:
    """Scan a path and return the import units, loose files, and totals.

    Filesystem-only (no network); safe to call on each folder selection.
    """
    from app.core import import_scanner

    # resolve() collapses .. and symlinks before the path reaches the scanner
    # (mirrors import_browse), so a caller can't pick a traversal/symlink root.
    p = Path(req.path).expanduser().resolve()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {req.path}")

    scan = await asyncio.to_thread(import_scanner.scan, p)
    units = [
        {
            "show_name": u.show_name,
            "season": u.season,
            "file_count": len(u.files),
            "total_bytes": u.total_bytes,
        }
        for u in scan.units
    ]
    return {
        "root": str(scan.root),
        "units": units,
        "loose_files": [str(f) for f in scan.loose_files],
        "total_jobs": len(scan.units),
        "total_files": scan.total_files,
        "total_bytes": scan.total_bytes,
        "truncated": scan.truncated,
    }


@router.post("/import/start", dependencies=[Depends(require_localhost_or_lan)])
async def import_start(req: ImportStartRequest) -> dict:
    """Create one import job per (show, season) unit from a chosen path.

    Each job gets an explicit file manifest, so nested Disc/ files import
    correctly. Remembers the path and destination as the next defaults.
    """
    from app.core import import_scanner
    from app.services import config_service
    from app.services.job_manager import job_manager

    if req.destination_mode not in ("library", "in_place"):
        raise HTTPException(status_code=400, detail="Invalid destination_mode")

    # resolve() collapses .. and symlinks before the path reaches the scanner
    # (mirrors import_browse), so a caller can't pick a traversal/symlink root.
    p = Path(req.path).expanduser().resolve()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {req.path}")

    scan = await asyncio.to_thread(import_scanner.scan, p)
    if not scan.units:
        raise HTTPException(status_code=400, detail="No MKV files found to import")

    root_str = str(scan.root)
    job_ids: list[int] = []
    for unit in scan.units:
        files = [str(f) for f in unit.files]
        staging = str(Path(files[0]).parent) if len(files) == 1 else os.path.commonpath(files)
        manifest = {
            "root": root_str,
            "files": files,
            "picked_is_show": scan.picked_is_show,
            "picked_is_season": scan.picked_is_season,
        }
        jid = await job_manager.create_job_from_staging(
            staging_path=staging,
            content_type="tv" if unit.season is not None else "unknown",
            detected_title=unit.show_name,
            detected_season=unit.season,
            destination_mode=req.destination_mode,
            drive_id="import",
            import_manifest=manifest,
        )
        if jid != -1:
            job_ids.append(jid)

    skipped = len(scan.units) - len(job_ids)
    # Persist the path/destination as next-time defaults even when everything was
    # a dedup no-op, so the modal still reopens where the user last browsed.
    await config_service.update_config(
        import_watch_path=req.path, import_destination_mode=req.destination_mode
    )
    logger.info(
        "Import start: %s -> %d job(s) created, %d skipped",
        sanitize_log_value(req.path),
        len(job_ids),
        skipped,
    )
    # Every unit already had an active or completed job for its folder. Tell the
    # caller (409) instead of returning a silent empty list with 200 — the
    # DB-backed dedup persists across restarts, so this is a real "nothing to do".
    if not job_ids and skipped:
        raise HTTPException(
            status_code=409,
            detail=(
                f"All {skipped} import job(s) for this folder already exist "
                "(active or completed); nothing new was started."
            ),
        )
    return {"job_ids": job_ids, "skipped": skipped}


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
    from app.config import is_frozen
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
        "runtime": {
            "is_frozen": is_frozen(),
            "sys_frozen": bool(getattr(sys, "frozen", False)),
            "meipass": hasattr(sys, "_MEIPASS"),
        },
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


def _build_markdown_summary(
    env: dict,
    job_summary: dict | None,
    recent_errors: list[str],
    recent_errors_is_fallback: bool = False,
) -> str:
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
        f"**Build**: {'frozen' if env.get('runtime', {}).get('is_frozen') else 'dev'} "
        f"(sys.frozen={env.get('runtime', {}).get('sys_frozen')}, "
        f"_MEIPASS={env.get('runtime', {}).get('meipass')})",
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
        parts += ["### Recent Errors"]
        if recent_errors_is_fallback:
            fallback_note = (
                "_Note: this job has no tagged log lines (predates job-tagged "
                "logging or none were emitted) — showing the most recent global "
                "error tail below, which is not specific to this job._"
            )
            parts += [fallback_note, ""]
        parts += ["```"]
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


def _read_job_error_lines(
    job_id: int, limit: int = 20, log_path: Path | None = None
) -> tuple[list[str], bool]:
    """Return ``(lines, is_fallback)`` — this job's ERROR/CRITICAL log lines.

    Scopes to the job via ``_read_job_tagged_logs`` so a bug report for one
    job never surfaces another job's errors (#506: job 47's report showed
    job 39's errors because the old code read the unscoped global tail
    regardless of ``job_id``). When the job has no tagged lines at all
    (predates job-tagged logging), the fallback is already the global
    ERROR/CRITICAL tail — ``is_fallback`` lets callers flag it as such.
    """
    lines, is_fallback = _read_job_tagged_logs(job_id, log_path=log_path)
    if not is_fallback:
        lines = [ln for ln in lines if "ERROR" in ln or "CRITICAL" in ln]
    return (lines[-limit:], is_fallback)


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

    # Scope "recent errors" to the requested job so a bug report never leaks
    # an unrelated job's errors (#506). Only fall back to the unscoped global
    # tail when the report was opened without job context at all.
    if job_id is not None:
        recent_errors, recent_errors_is_fallback = _read_job_error_lines(job_id)
    else:
        recent_errors = _read_recent_error_lines()
        recent_errors_is_fallback = False

    report = {
        "app_version": env["app_version"],
        "python_version": env["python_version"],
        "os": env["os"],
        "makemkv_version": env["makemkv_version"],
        "ffmpeg_version": env["ffmpeg_version"],
        "job": job_summary,
        "recent_errors": recent_errors,
        "recent_errors_is_fallback": recent_errors_is_fallback,
        "config": env["config"],
    }

    # --- GitHub issue body (kept small; full detail lives in the bundle) ---
    body_parts = [
        _build_markdown_summary(env, job_summary, recent_errors, recent_errors_is_fallback),
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


@router.get(
    "/contributions",
    response_model=list[ContributionJobResponse],
    dependencies=[Depends(require_localhost)],
)
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


@router.get(
    "/contributions/stats",
    response_model=ContributionStatsResponse,
    dependencies=[Depends(require_localhost)],
)
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


def _require_discdb_contributions() -> None:
    """Reject contribution endpoints unless the contribution feature is enabled.

    Lookup may be on while contribution stays gated; without this guard the
    submit/export endpoints would remain reachable directly.
    """
    from app.core.features import DISCDB_CONTRIBUTIONS_ENABLED

    if not DISCDB_CONTRIBUTIONS_ENABLED:
        raise HTTPException(status_code=404, detail="TheDiscDB contributions are disabled")


def _require_contributions_opt_in(config) -> None:
    """Reject external submission unless the user has explicitly opted in.

    The master feature flag (`_require_discdb_contributions`) exposes the
    pipeline; this per-user consent (Settings -> TheDiscDB Contributions,
    `discdb_contributions_enabled`, default False) is the gate that actually
    permits sending disc metadata off-machine to thediscdb.com. Enforced at
    the API layer so a direct localhost request can't bypass the UI's opt-in
    check — the frontend hiding the Submit button is not a security boundary.
    """
    if not config.discdb_contributions_enabled:
        raise HTTPException(
            status_code=403,
            detail="Enable TheDiscDB contributions in Settings before submitting",
        )


@router.post("/contributions/{job_id}/export", dependencies=[Depends(require_localhost)])
async def export_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger export for a specific job."""
    _require_discdb_contributions()
    from app.core.discdb_exporter import generate_export, mark_exported
    from app.core.discdb_submitter import ensure_release_group_id
    from app.services.config_service import get_config as get_db_config

    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")

    if not job.release_group_id:
        ensure_release_group_id(job)
        session.add(job)
        await session.commit()
        await session.refresh(job)

    config = await get_db_config()
    titles_result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job.id))
    titles = list(titles_result.scalars().all())

    from app import __version__

    export_dir = generate_export(job, titles, config, app_version=__version__)
    if not export_dir:
        raise HTTPException(status_code=400, detail="Cannot export — no content hash")

    await mark_exported(job.id, session)
    return {"status": "exported", "export_path": str(export_dir)}


@router.post("/contributions/{job_id}/skip", dependencies=[Depends(require_localhost)])
async def skip_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Mark a job as skipped for contribution."""
    _require_discdb_contributions()
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


@router.post("/contributions/{job_id}/upc-lookup", dependencies=[Depends(require_localhost)])
async def upc_lookup(
    request: UPCLookupRequest,
    job: DiscJob = Depends(get_job_or_404),
):
    """Look up product info by UPC barcode."""
    _require_discdb_contributions()
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


@router.post("/contributions/{job_id}/fetch-cover", dependencies=[Depends(require_localhost)])
async def fetch_cover(
    job_id: int,
    request: FetchCoverRequest,
    session: AsyncSession = Depends(get_session),
):
    """Download a cover image and save it to the export directory."""
    _require_discdb_contributions()
    # Lazy import (matches get_db_config below). Keep it inline: the fetch-cover
    # test patches app.core.discdb_exporter.get_export_directory — the binding
    # this resolves at call time — so hoisting it to module scope would bypass
    # the patch and silently break that test.
    from app.core.discdb_exporter import get_export_directory
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

    # Use the canonical export-dir helper (falls back to ~/.engram/discdb-exports/)
    # rather than 400-ing on an unset discdb_export_path — matches every other
    # call site, and an empty path is the default.
    config = await get_db_config()
    export_dir = get_export_directory(config) / job.content_hash
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


@router.post("/contributions/{job_id}/enhance", dependencies=[Depends(require_localhost)])
async def enhance_contribution(
    request: EnhanceRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Add tier-3 data (UPC) and re-export."""
    _require_discdb_contributions()
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


@router.post("/jobs/{job_id}/flag-discdb", dependencies=[Depends(require_localhost)])
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


class AmendTarget(BaseModel):
    """Where a completed-job track is being reassigned."""

    kind: Literal["episode", "extra", "discard"]
    episode_code: str | None = None


class AmendRequest(BaseModel):
    target: AmendTarget


@router.post("/jobs/{job_id}/titles/{title_id}/amend")
async def amend_title(
    title_id: int,
    request: AmendRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Reassign a track on a COMPLETED job (episode / extra / discard).

    Moves the organized library file, updates the title, and reconciles the
    fingerprint network. Only valid for completed jobs — jobs still in review use
    the existing review/reassign flow.
    """
    if job.state != JobState.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Amend is only available for completed jobs (state: {job.state.value})",
        )

    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    if request.target.kind == "episode" and not request.target.episode_code:
        raise HTTPException(
            status_code=400, detail="episode_code required for episode reassignment"
        )

    from app.services.contribution_correction import NewTarget
    from app.services.job_manager import job_manager

    try:
        await job_manager.amend_title_assignment(
            job.id,
            title_id,
            NewTarget(kind=request.target.kind, episode_code=request.target.episode_code),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {"status": "amended", "title_id": title_id, "kind": request.target.kind}


class ShowOrderingRequest(BaseModel):
    """Set a show's output ordering preference (#200)."""

    ordering: str  # one of episode_ordering.ALLOWED_ORDERINGS


@router.get("/shows/{tmdb_id}/ordering")
async def get_show_ordering(
    tmdb_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a show's effective output ordering and whether it's an explicit override."""
    from app.models.show_ordering import ShowOrderingPreference
    from app.services.episode_ordering_service import resolve_show_ordering

    pref = await session.get(ShowOrderingPreference, tmdb_id)
    effective, group_id = await resolve_show_ordering(tmdb_id, session)
    return {
        "tmdb_id": tmdb_id,
        "ordering": effective,
        "episode_group_id": group_id,
        "source": "show" if pref else "default",
    }


@router.put("/shows/{tmdb_id}/ordering")
async def set_show_ordering(
    tmdb_id: int,
    request: ShowOrderingRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set (upsert) a show's output ordering preference.

    Divergence is a property of the show, so ordering is stored per show (by
    tmdb_id) and applied to future organizes — not threaded through individual
    review decisions. Resolves and caches the episode-group id eagerly.
    """
    from datetime import UTC, datetime

    from app.core import episode_ordering
    from app.models.show_ordering import ShowOrderingPreference
    from app.services.config_service import get_config

    if request.ordering not in episode_ordering.ALLOWED_ORDERINGS:
        raise HTTPException(
            status_code=422,
            detail=f"ordering must be one of {sorted(episode_ordering.ALLOWED_ORDERINGS)}",
        )

    group_id = None
    if request.ordering != episode_ordering.ORDERING_AIRED:
        config = await get_config()
        group_id = await asyncio.to_thread(
            episode_ordering.resolve_episode_group_id,
            str(tmdb_id),
            request.ordering,
            config.tmdb_api_key,
        )

    pref = await session.get(ShowOrderingPreference, tmdb_id)
    if pref is None:
        pref = ShowOrderingPreference(tmdb_id=tmdb_id)
        session.add(pref)
    pref.ordering = request.ordering
    pref.episode_group_id = group_id
    pref.updated_at = datetime.now(UTC)
    await session.commit()

    return {"tmdb_id": tmdb_id, "ordering": request.ordering, "episode_group_id": group_id}


@dataclass(frozen=True)
class LLMMatchOutcome:
    """Result of an LLM-match attempt. ``reason is None`` means success."""

    suggestion: dict | None
    reason: str | None

    @classmethod
    def ok(cls, suggestion: dict) -> "LLMMatchOutcome":
        return cls(suggestion=suggestion, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "LLMMatchOutcome":
        return cls(suggestion=None, reason=reason)


# Operational failures the caller may retry (HTTP 503). Every other reason is a
# deterministic config/data outcome returned as 200 with a differentiated reason.
_LLM_MATCH_RETRYABLE_REASONS = frozenset(
    {"matcher_unavailable", "transcription_failed", "llm_error"}
)


async def _run_llm_match_for_title(*, title: "DiscTitle", job: "DiscJob") -> LLMMatchOutcome:
    """Invoke the LLM episode matcher for a single title.

    Returns an :class:`LLMMatchOutcome` whose ``reason`` distinguishes each
    failure mode (``ai_disabled``, ``not_configured``, ``no_show``, ``no_season``,
    ``matcher_unavailable``, ``show_not_found``, ``transcription_failed``,
    ``llm_error``, ``no_match``) or is ``None`` on success.
    """
    from app.services.config_service import get_config

    config = await get_config()
    if not config:
        # Defensive: get_config() creates defaults rather than returning None, but
        # if it ever can't, "not_configured" is truer than "feature turned off".
        return LLMMatchOutcome.failed("not_configured")
    if not getattr(config, "ai_episode_matching_enabled", False):
        return LLMMatchOutcome.failed("ai_disabled")
    if not config.ai_api_key:
        return LLMMatchOutcome.failed("not_configured")
    if not job.detected_title:
        return LLMMatchOutcome.failed("no_show")
    if not job.detected_season:
        return LLMMatchOutcome.failed("no_season")

    from app.core.curator import curator as episode_curator
    from app.matcher.llm_episode_matcher import match_episode_via_llm
    from app.matcher.tmdb_client import fetch_show_id
    from app.services.ripping_helpers import find_staging_file

    # Make sure the matcher is initialized for the show (so transcribe_full works).
    # 503/retryable fits the "still initializing" case; a permanent init failure
    # (missing model weights / corrupt index) keeps returning this on retry.
    episode_curator._ensure_initialized(job.detected_title)
    if not episode_curator._matcher:
        return LLMMatchOutcome.failed("matcher_unavailable")

    tmdb_show_id = await asyncio.to_thread(fetch_show_id, job.detected_title)
    if not tmdb_show_id:
        return LLMMatchOutcome.failed("show_not_found")

    # Resolve the ripped file the way the matching pipeline does — handles moved/
    # renamed staging files and re-matching an already-organized title. A missing
    # file is folded into transcription_failed (no transcript can be produced).
    file_path = find_staging_file(job, title)
    if not file_path:
        return LLMMatchOutcome.failed("transcription_failed")

    transcript = await asyncio.to_thread(episode_curator._matcher.transcribe_full, file_path)
    if not transcript:
        return LLMMatchOutcome.failed("transcription_failed")

    try:
        suggestion = await match_episode_via_llm(
            transcript=transcript,
            show_name=job.detected_title,
            season=job.detected_season,
            tmdb_show_id=str(tmdb_show_id),
            ai_provider=config.ai_provider,
            ai_api_key=config.ai_api_key,
            tmdb_api_key=config.tmdb_api_key,
            raise_on_error=True,
        )
    except AIProviderError:
        logger.warning(
            "LLM match: provider error for title %s -> llm_error",
            sanitize_log_value(title.id),
            exc_info=True,
        )
        return LLMMatchOutcome.failed("llm_error")

    if not suggestion:
        return LLMMatchOutcome.failed("no_match")
    return LLMMatchOutcome.ok(
        {
            "episode": suggestion.episode,
            "confidence": suggestion.confidence,
            "reasoning": suggestion.reasoning,
            "runner_up": (
                {
                    "episode": suggestion.runner_up.episode,
                    "confidence": suggestion.runner_up.confidence,
                }
                if suggestion.runner_up is not None
                else None
            ),
            "model": suggestion.model,
        }
    )


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
        outcome = await _run_llm_match_for_title(title=title, job=job)
    except Exception:
        logger.exception("LLM match endpoint failed for title %s", sanitize_log_value(title_id))
        return JSONResponse(
            status_code=500, content={"suggestion": None, "reason": "internal_error"}
        )

    if outcome.reason in _LLM_MATCH_RETRYABLE_REASONS:
        return JSONResponse(status_code=503, content={"suggestion": None, "reason": outcome.reason})

    if outcome.suggestion is None:
        return {"suggestion": None, "reason": outcome.reason}

    # Persist into match_details for refresh durability
    existing["llm_suggestion"] = outcome.suggestion
    title.match_details = json.dumps(existing)
    session.add(title)
    await session.commit()

    return {"suggestion": outcome.suggestion, "reason": None}


@router.post("/contributions/{job_id}/submit", dependencies=[Depends(require_localhost)])
async def submit_contribution(
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Submit a job's disc data to TheDiscDB API."""
    _require_discdb_contributions()
    from app.core.discdb_submitter import ensure_release_group_id, submit_job
    from app.services.config_service import get_config as get_db_config

    if job.state != JobState.COMPLETED:
        raise HTTPException(status_code=400, detail="Job is not completed")
    if not job.exported_at or job.exported_at.year == 1970:
        raise HTTPException(status_code=400, detail="Job must be exported before submission")

    if not job.release_group_id:
        ensure_release_group_id(job)
        session.add(job)
        await session.commit()
        await session.refresh(job)

    config = await get_db_config()
    _require_contributions_opt_in(config)

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


@router.post("/contributions/release-group", dependencies=[Depends(require_localhost)])
async def create_release_group(
    request: ReleaseGroupRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a release group linking multiple disc jobs."""
    _require_discdb_contributions()
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


@router.put("/contributions/{job_id}/release-group", dependencies=[Depends(require_localhost)])
async def assign_release_group(
    request: ReleaseGroupAssignRequest,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Assign or remove a job from a release group."""
    _require_discdb_contributions()
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


@router.post(
    "/contributions/release-group/{release_group_id}/submit",
    dependencies=[Depends(require_localhost)],
)
async def submit_release_group_endpoint(
    release_group_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Batch-submit all completed jobs in a release group to TheDiscDB."""
    _require_discdb_contributions()
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
    _require_contributions_opt_in(config)

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


def _gpu_state(*, device: str, detected: bool, installed: bool, downloading: dict) -> str:
    """Collapse the GPU situation into one badge state for the dashboard/settings UI."""
    from app.matcher.cuda_runtime import is_supported_platform

    # In-flight or failed download takes precedence so the field matches gpu_download.state.
    if downloading.get("state") in ("downloading", "installing", "error"):
        return downloading["state"]
    if device == "cuda":
        return "active"
    if not is_supported_platform():
        return "unsupported_os"  # macOS / non-NVIDIA arch — CTranslate2 has no GPU path
    if detected:
        # NVIDIA GPU present but not running on it: either not enabled or libs not downloaded.
        return "available_not_installed" if not installed else "available_not_enabled"
    return "unavailable"  # supported OS but no NVIDIA GPU


@router.get("/asr-status")
async def get_asr_status():
    """Resolved ASR backend for the dashboard badge + GPU-acceleration settings (no secrets).

    ``device`` is the *effective* device (what the model actually loads on), so the badge
    can't claim CUDA while silently running on CPU. The ``gpu_*`` fields drive the opt-in
    download toggle in settings.
    """
    from app.matcher.asr_models import detect_asr_device, gpu_detected, resolve_asr_runtime
    from app.matcher.cuda_runtime import (
        download_size_bytes,
        get_download_state,
        is_cuda_runtime_installed,
    )
    from app.services.config_service import get_config as _get_app_config

    config = await _get_app_config()
    device = detect_asr_device()
    runtime = resolve_asr_runtime(device, config.max_concurrent_matches)
    detected = gpu_detected()
    installed = is_cuda_runtime_installed()
    download = get_download_state()
    return {
        "device": device,
        "compute_type": runtime.compute_type,
        "model": "small",  # current hardcoded matcher default (EpisodeMatcher.model_name)
        "workers": runtime.workers,
        "cpu_threads": runtime.cpu_threads,
        "max_concurrent_matches": config.max_concurrent_matches,
        # GPU acceleration (opt-in CUDA runtime download)
        "gpu_detected": detected,
        "gpu_enabled": config.enable_gpu_acceleration,
        "gpu_runtime_installed": installed,
        "gpu_download_size_bytes": download_size_bytes(),
        "gpu_download": download,
        "gpu_state": _gpu_state(
            device=device, detected=detected, installed=installed, downloading=download
        ),
    }


async def _finish_gpu_enable(success: bool, error: str | None) -> None:
    """Post-download hook: flip the config flag on success, broadcast the final state."""
    from app.matcher.cuda_runtime import get_download_state
    from app.services.config_service import update_config as update_db_config
    from app.services.job_manager import event_broadcaster

    if success:
        # Persist the flag, but never let a DB error swallow the completion broadcast —
        # the UI must still learn the download finished.
        try:
            await update_db_config(enable_gpu_acceleration=True)
        except Exception:
            logger.error("Failed to persist enable_gpu_acceleration flag", exc_info=True)
    await event_broadcaster.broadcast_gpu_status(get_download_state())


@router.post("/asr/gpu/enable")
async def enable_gpu_acceleration():
    """Enable GPU ASR: download the CUDA runtime if needed, then arm it for the next restart.

    Rejected when the platform/hardware can't use CUDA. The ~1.2 GB cuDNN/cuBLAS download
    runs in the background (progress via /api/asr-status + the ``gpu_status`` WS message);
    activation takes effect on the next backend restart (registration must precede the first
    model load, especially on Linux).
    """
    from app.matcher.asr_models import gpu_detected
    from app.matcher.cuda_runtime import (
        get_download_state,
        is_cuda_runtime_installed,
        is_supported_platform,
        start_background_download,
    )
    from app.services.config_service import update_config as update_db_config
    from app.services.job_manager import event_broadcaster

    if not is_supported_platform():
        raise HTTPException(
            status_code=400,
            detail="GPU acceleration requires an NVIDIA GPU on Windows or Linux "
            "(CTranslate2 has no GPU path on macOS).",
        )
    if not gpu_detected():
        raise HTTPException(status_code=400, detail="No NVIDIA GPU detected on this machine.")

    # Already have the libraries (downloaded earlier, or dev `uv sync -E gpu`): just arm it.
    if is_cuda_runtime_installed():
        await update_db_config(enable_gpu_acceleration=True)
        return {"status": "ready", "restart_required": True, "gpu_download": get_download_state()}

    def _on_done(success: bool, error: str | None) -> None:
        # Runs on the download thread; hop back to the event loop to touch DB + WS. Surface any
        # failure of the coroutine itself (otherwise the discarded Future swallows it silently).
        fut = asyncio.run_coroutine_threadsafe(_finish_gpu_enable(success, error), _loop)
        fut.add_done_callback(
            lambda f: (
                logger.error("_finish_gpu_enable failed", exc_info=f.exception())
                if f.exception()
                else None
            )
        )

    _loop = asyncio.get_running_loop()
    started = start_background_download(on_done=_on_done)
    await event_broadcaster.broadcast_gpu_status(get_download_state())
    return {
        "status": "downloading" if started else "already_downloading",
        "restart_required": True,
        "gpu_download": get_download_state(),
    }


@router.post("/asr/gpu/disable")
async def disable_gpu_acceleration():
    """Disable GPU ASR (revert to CPU on the next restart). The cached libraries are kept."""
    from app.matcher.cuda_runtime import get_download_state
    from app.services.config_service import update_config as update_db_config
    from app.services.job_manager import event_broadcaster

    await update_db_config(enable_gpu_acceleration=False)
    # Broadcast so other connected clients (e.g. a second settings tab) don't show stale state.
    await event_broadcaster.broadcast_gpu_status(get_download_state())
    return {"status": "disabled", "restart_required": True, "gpu_download": get_download_state()}


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
