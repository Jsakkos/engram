"""Where a disc backup goes, and whether there is room for it.

Naming mirrors the library layout so a preservation shelf browses the same way
the library does. Sanitization reuses the Organizer's helper rather than
reimplementing it, so the two cannot drift.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.core.organizer import sanitize_filename
from app.models import ContentType, DiscJob

logger = logging.getLogger(__name__)

# Free space must exceed the estimate by this factor. A backup carries the
# container and filesystem overhead the per-title sizes do not.
_SPACE_MARGIN = 1.15

# Assumed disc size when the scan produced no usable sizes. A dual-layer
# Blu-ray, so an unknown disc is treated as the largest realistic case rather
# than waved through.
_ASSUMED_DISC_BYTES = 50 * 1024**3


def _safe_name(raw: str) -> str:
    """Sanitize one path component, reusing the Organizer's rules."""
    cleaned = sanitize_filename(raw or "")
    # sanitize_filename already strips '/' and '\\' along with the rest of the
    # Windows-illegal character set, which is what keeps a crafted title from
    # introducing a new path component and climbing out of the backup root.
    return cleaned or "Unknown"


def backup_destination(job: DiscJob, backup_root: str) -> Path | None:
    """Compute where this job's backup should be written.

    Returns None when no root is configured, which the caller reports as a
    "not_configured" skip.
    """
    if not backup_root:
        return None

    root = Path(backup_root).expanduser()
    name = job.tmdb_name or job.detected_title

    if job.content_type == ContentType.MOVIE and name:
        folder = f"{_safe_name(name)} ({job.tmdb_year})" if job.tmdb_year else _safe_name(name)
        return root / "Movies" / folder

    if job.content_type == ContentType.TV and name:
        show = f"{_safe_name(name)} ({job.tmdb_year})" if job.tmdb_year else _safe_name(name)
        disc = _safe_name(job.discdb_disc_slug or f"Disc {job.disc_number or 1}")
        base = root / "TV" / show
        if job.detected_season is not None:
            return base / f"Season {job.detected_season:02d}" / disc
        return base / disc

    label = _safe_name(job.volume_label) if job.volume_label else ""
    if not label or label == "Unknown":
        label = f"job-{job.id}" if job.id else "job-unknown"
    return root / "Unidentified" / label


def has_room_for_backup(dest: Path, needed_bytes: int) -> bool:
    """Whether ``dest``'s filesystem has room for the backup, with margin.

    Fails closed: an unreadable destination returns False and the caller falls
    back to a direct rip. A false negative costs one direct rip; a false
    positive costs a half-written 40 GB folder and a failed job.
    """
    estimate = needed_bytes if needed_bytes > 0 else _ASSUMED_DISC_BYTES
    required = int(estimate * _SPACE_MARGIN)
    # The destination itself may not exist yet, so measure the nearest existing
    # ancestor: that is the filesystem the write will land on.
    probe = dest
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read free space at {probe}: {e}")
        return False
    if free < required:
        logger.warning(
            f"Not enough room for backup at {probe}: "
            f"{free / 1024**3:.1f} GB free, {required / 1024**3:.1f} GB required"
        )
        return False
    return True
