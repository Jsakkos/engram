"""TheDiscDB Contribution Exporter.

Generates JSON export files containing disc metadata for submission to TheDiscDB.
Exports are written to a configurable directory (default: ~/.engram/discdb-exports/)
organized by content hash.
"""

import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState

logger = logging.getLogger(__name__)

EXPORT_SCHEMA_VERSION = "1.1"

_EPISODE_RE = re.compile(r"S(\d+)E(\d+)", re.IGNORECASE)


def _parse_episode_code(code: str | None) -> tuple[int | None, int | None]:
    """Parse an episode code like 'S01E01' into (season, episode) integers.

    Returns (None, None) for None or malformed input.
    For multi-episode codes like 'S01E01E02', returns the first episode.
    """
    if not code:
        return None, None
    m = _EPISODE_RE.search(code)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def get_export_directory(config: AppConfig) -> Path:
    """Return the export directory, creating it if needed."""
    if config.discdb_export_path:
        export_dir = Path(config.discdb_export_path)
    else:
        export_dir = Path.home() / ".engram" / "discdb-exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def get_makemkv_log_dir(job_id: int) -> Path:
    """Return the MakeMKV log directory for a job."""
    return Path.home() / ".engram" / "logs" / "makemkv" / str(job_id)


def _derive_title_type(
    title: DiscTitle,
    content_type: ContentType,
    discdb_mappings: list[dict] | None,
) -> str | None:
    """Derive the title type for export.

    Uses DiscDB mappings if available, otherwise infers from Engram data.
    """
    # Check DiscDB mappings first
    if discdb_mappings:
        for mapping in discdb_mappings:
            if mapping.get("index") == title.title_index:
                return mapping.get("title_type") or None

    # Infer from Engram data
    if title.is_extra:
        return "Extra"
    if content_type == ContentType.TV and title.matched_episode:
        return "Episode"
    if content_type == ContentType.MOVIE and title.is_selected:
        return "MainMovie"
    return None


def generate_export(
    job: DiscJob,
    titles: list[DiscTitle],
    config: AppConfig,
    app_version: str = "0.4.4",
) -> Path | None:
    """Generate a JSON export file for a completed job.

    Returns the path to the export directory, or None if export cannot be generated.
    """
    if not job.content_hash:
        logger.warning(f"Job {job.id}: Cannot export — no content hash available")
        return None

    export_base = get_export_directory(config)
    export_dir = export_base / job.content_hash
    export_dir.mkdir(parents=True, exist_ok=True)

    # Parse DiscDB mappings if available
    discdb_mappings = None
    if job.discdb_mappings_json:
        try:
            discdb_mappings = json.loads(job.discdb_mappings_json)
        except json.JSONDecodeError:
            pass

    # Build title entries and track match sources for skip logic
    title_entries = []
    match_sources = []
    for title in titles:
        # Determine match source
        match_source = None
        if title.match_details:
            try:
                details = json.loads(title.match_details)
                match_source = details.get("source")
            except json.JSONDecodeError:
                pass
        if match_source:
            match_sources.append(match_source)

        season, episode = _parse_episode_code(title.matched_episode)

        title_entries.append(
            {
                "index": title.title_index,
                "source_filename": title.source_filename,
                "duration_seconds": title.duration_seconds,
                "size_bytes": title.file_size_bytes,
                "chapter_count": title.chapter_count,
                "segment_count": title.segment_count,
                "segment_map": title.segment_map,
                "title_type": _derive_title_type(title, job.content_type, discdb_mappings),
                "season": season,
                "episode": episode,
                "match_confidence": title.match_confidence,
                "match_source": match_source,
                "edition": title.edition,
            }
        )

    # Skip export if all matched titles came from TheDiscDB (avoids resubmission)
    if match_sources and all(s == "discdb" for s in match_sources):
        logger.info(f"Job {job.id}: Skipping export — all matches sourced from TheDiscDB")
        return None

    # Build the export payload
    payload = {
        "engram_version": app_version,
        "export_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "contribution_tier": config.discdb_contribution_tier,
        "disc": {
            "content_hash": job.content_hash,
            "volume_label": job.volume_label,
            "content_type": job.content_type,
            "disc_number": job.disc_number,
            "release_id": getattr(job, "release_group_id", None),
        },
        "identification": {
            "tmdb_id": job.tmdb_id,
            "detected_title": job.detected_title,
            "detected_season": job.detected_season,
            "classification_source": job.classification_source,
            "classification_confidence": job.classification_confidence,
        },
        "titles": title_entries,
        "upc": job.upc_code,
        "images": _list_images(export_dir),
        "scan_log": _collect_scan_log(job.id, export_dir),
    }

    # Write JSON
    json_path = export_dir / "disc_data.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info(f"Job {job.id}: Exported disc data to {export_dir}")

    return export_dir


def _collect_scan_log(job_id: int | None, export_dir: Path) -> str | None:
    """Copy MakeMKV scan log to export directory and return the filename."""
    if not job_id:
        return None

    log_dir = get_makemkv_log_dir(job_id)
    if not log_dir.exists():
        return None

    scan_log = log_dir / "scan.log"
    if scan_log.exists():
        dest = export_dir / "makemkv_scan.log"
        shutil.copy2(scan_log, dest)
        return "makemkv_scan.log"

    return None


def _list_images(export_dir: Path) -> list[str]:
    """List image files in the export directory (tier 3 contributions)."""
    images = []
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        for img in export_dir.glob(ext):
            images.append(img.name)
    return sorted(images)


async def get_pending_exports(session: AsyncSession) -> list[DiscJob]:
    """Get completed jobs that haven't been exported yet."""
    result = await session.execute(
        select(DiscJob).where(
            DiscJob.state == JobState.COMPLETED,
            DiscJob.exported_at.is_(None),
            DiscJob.content_hash.is_not(None),
        )
    )
    return list(result.scalars().all())


async def mark_exported(job_id: int, session: AsyncSession) -> None:
    """Mark a job as exported."""
    result = await session.execute(select(DiscJob).where(DiscJob.id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.exported_at = datetime.now(UTC)
        session.add(job)
        await session.commit()


async def mark_skipped(job_id: int, session: AsyncSession) -> None:
    """Mark a job as skipped for contribution (sets exported_at to epoch)."""
    result = await session.execute(select(DiscJob).where(DiscJob.id == job_id))
    job = result.scalar_one_or_none()
    if job:
        # Use epoch as sentinel for "explicitly skipped"
        job.exported_at = datetime(1970, 1, 1, tzinfo=UTC)
        session.add(job)
        await session.commit()
