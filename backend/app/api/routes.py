"""REST API routes for Engram."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import settings
from app.database import get_session
from app.models import DiscJob, JobState
from app.models.disc_job import DiscTitle

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
    # Onboarding
    setup_complete: bool | None = None


class ReviewRequest(BaseModel):
    """Request model for submitting a review decision."""

    title_id: int
    episode_code: str | None = None  # e.g., "S01E01"
    edition: str | None = None  # e.g., "Extended", "Theatrical"


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


# Routes
@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[DiscJob]:
    """List disc jobs (limited to 10 most recent)."""
    result = await session.execute(select(DiscJob).order_by(DiscJob.created_at.desc()).limit(10))
    return list(result.scalars().all())


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
        # Onboarding
        setup_complete=config.setup_complete,
    )


@router.put("/config")
async def update_config(config: ConfigUpdate) -> dict:
    """Update configuration and persist to database."""
    from app.services.config_service import update_config as update_db_config

    # Build kwargs from non-None fields
    update_data = {k: v for k, v in config.model_dump().items() if v is not None}

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
    """Clear all completed and failed jobs and their titles from the database."""
    from sqlalchemy import delete

    # 1. Find job IDs to delete
    terminal_jobs = await session.execute(
        select(DiscJob.id).where(DiscJob.state.in_([JobState.COMPLETED, JobState.FAILED]))
    )
    job_ids = [row[0] for row in terminal_jobs.all()]

    if not job_ids:
        return {"status": "cleared", "deleted_count": 0}

    # 2. Delete child DiscTitle rows first (prevents orphans)
    await session.execute(delete(DiscTitle).where(DiscTitle.job_id.in_(job_ids)))

    # 3. Delete the jobs themselves
    result = await session.execute(delete(DiscJob).where(DiscJob.id.in_(job_ids)))
    await session.commit()

    return {"status": "cleared", "deleted_count": result.rowcount}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Delete a single completed or failed job and its titles."""
    from sqlalchemy import delete

    job = await session.get(DiscJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.state not in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Can only delete completed or failed jobs (current state: {job.state})",
        )

    # Delete child titles first
    await session.execute(delete(DiscTitle).where(DiscTitle.job_id == job_id))
    await session.delete(job)
    await session.commit()

    return {"status": "deleted", "job_id": job_id}


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


@router.post("/simulate/insert-disc")
async def simulate_insert_disc(req: SimulateDiscRequest) -> dict:
    """Simulate a disc insertion. Only available in debug mode."""
    if not settings.debug:
        raise HTTPException(status_code=403, detail="Simulation only available in debug mode")

    from app.services.job_manager import job_manager

    params = req.model_dump()
    if params.get("detected_title") is None:
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
