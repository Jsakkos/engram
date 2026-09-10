"""Where a disc backup goes, and whether there is room for it.

Naming mirrors the library layout so a preservation shelf browses the same way
the library does. Sanitization reuses the Organizer's helper rather than
reimplementing it, so the two cannot drift.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.core.organizer import (
    format_movie_folder,
    format_season_folder,
    format_tv_show_folder,
    sanitize_filename,
)
from app.models import AppConfig, ContentType, DiscJob

logger = logging.getLogger(__name__)

# Free space must exceed the estimate by this factor. A backup carries the
# container and filesystem overhead the per-title sizes do not.
_SPACE_MARGIN = 1.15

# Assumed disc size when the scan produced no usable sizes. A dual-layer
# Blu-ray, so an unknown disc is treated as the largest realistic case rather
# than waved through.
_ASSUMED_DISC_BYTES = 50 * 1024**3


def _safe_name(raw: str) -> str:
    """Sanitize one path component, reusing the Organizer's rules.

    Returns "" when nothing survives sanitization, so each call site chooses
    its own fallback instead of everyone sharing one sentinel string (a disc
    genuinely labelled "Unknown" must not be treated the same as a title that
    sanitized away to nothing).
    """
    cleaned = sanitize_filename(raw or "")
    # sanitize_filename already strips '/' and '\\' along with the rest of the
    # Windows-illegal character set, which is what keeps a crafted title from
    # introducing a new path component and climbing out of the backup root.
    return cleaned


def _job_fallback(job: DiscJob) -> str:
    """The "nothing nameable survived" fallback, shared by every branch."""
    return f"job-{job.id}" if job.id else "job-unknown"


def backup_destination(job: DiscJob, config: AppConfig) -> Path | None:
    """Compute where this job's backup should be written.

    Builds the same folder shape the Organizer would for the library, using
    the user's configured naming formats, so the backup shelf mirrors the
    library layout and the two cannot drift.

    Returns None when no config or no root is configured, which the caller
    reports as a "not_configured" skip.
    """
    if not config or not config.backup_path:
        return None

    root = Path(config.backup_path).expanduser()
    name = job.tmdb_name or job.detected_title

    if job.content_type == ContentType.MOVIE and name:
        folder = format_movie_folder(config.naming_movie_format, name, job.tmdb_year, job.tmdb_id)
        folder = folder or _job_fallback(job)
        return root / "Movies" / folder

    if job.content_type == ContentType.TV and name:
        show = format_tv_show_folder(config.naming_tv_show_format, name, job.tmdb_year, job.tmdb_id)
        show = show or _job_fallback(job)
        disc = _safe_name(job.discdb_disc_slug or f"Disc {job.disc_number or 1}") or _job_fallback(
            job
        )
        base = root / "TV" / show
        if job.detected_season is not None:
            season = format_season_folder(config.naming_season_format, job.detected_season)
            return base / season / disc
        return base / disc

    label = _safe_name(job.volume_label) if job.volume_label else ""
    if not label:
        label = _job_fallback(job)
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


def reconcile_backup_location(job: DiscJob, config: AppConfig) -> Path | None:
    """Move a completed backup to the folder the CURRENT identity implies.

    The backup folder is named at BACKING_UP time, before the user has seen
    the identification. Correcting a misidentified disc updates the library
    output but used to leave the preservation shelf under the wrong show and
    TMDB id forever, which defeats the point of mirroring the library layout
    (#643).

    Returns the new path when the backup was moved, or None when there was
    nothing to do. Never raises: this is cosmetic housekeeping on the job
    completion path.
    """
    if not job.backup_path:
        return None
    current = Path(job.backup_path)
    if not current.is_dir():
        return None

    desired = backup_destination(job, config)
    if desired is None or desired == current:
        return None
    if desired.exists():
        # Legitimately another disc of the same set. Merging blindly would
        # be destructive, so leave both alone and say so.
        #
        # This check is NOT atomic with the rename below: two jobs reconciling
        # to the same destination could both pass it. What actually guarantees
        # "never merge, never overwrite" is the rename itself, which refuses a
        # non-empty directory (ENOTEMPTY on POSIX, FileExistsError on Windows)
        # and lands in the except branch as a logged no-op. This check exists to
        # make the common case explain itself in the log rather than to enforce
        # the invariant, so do not "optimize" it away or rely on it alone.
        logger.warning(f"Not moving backup {current} to {desired}: the destination already exists")
        return None

    try:
        desired.parent.mkdir(parents=True, exist_ok=True)
        # Same backup root means same volume, so this is a rename and not a
        # multi-gigabyte copy.
        current.rename(desired)
    except OSError as e:
        logger.warning(f"Could not move backup {current} to {desired}: {e}")
        return None

    logger.info(f"Moved backup to match the corrected identity: {desired}")
    _prune_empty_parents(current.parent, Path(config.backup_path).expanduser())
    return desired


def _prune_empty_parents(start: Path, root: Path) -> None:
    """Remove directories left empty by a rename, stopping at ``root``.

    ``root`` itself is never removed: an empty backup root is a configured
    location, not litter.
    """
    try:
        root = root.resolve()
        current = start.resolve()
    except OSError:
        return
    while current != root and root in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent
