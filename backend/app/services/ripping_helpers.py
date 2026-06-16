"""Shared helpers for ripping coordination.

Extracted from job_manager.py to eliminate duplicate implementations of
SpeedCalculator, title resolution, and title list building.
"""

import logging
import time
from collections import deque
from pathlib import Path

from app.core.extractor import title_index_from_filename
from app.core.security import sanitize_log_value
from app.models.disc_job import DiscJob, DiscTitle

logger = logging.getLogger(__name__)


class SpeedCalculator:
    """Calculates transfer speed and ETA using windowed averaging."""

    def __init__(self, total_bytes: int) -> None:
        self._total_bytes = total_bytes
        self._start_time = time.time()
        self._last_update = self._start_time
        self._bytes_history: deque[int] = deque(maxlen=10)
        self._time_history: deque[float] = deque(maxlen=10)
        self._current_speed: float = 0.0

    def update(self, current_bytes: int) -> None:
        now = time.time()
        if self._bytes_history and (now - self._last_update < 0.5):
            return

        self._bytes_history.append(current_bytes)
        self._time_history.append(now)

        if len(self._bytes_history) > 1:
            bytes_diff = self._bytes_history[-1] - self._bytes_history[0]
            time_diff = self._time_history[-1] - self._time_history[0]
            if time_diff > 0:
                self._current_speed = bytes_diff / time_diff

        self._last_update = now

    @property
    def speed_str(self) -> str:
        if self._current_speed == 0:
            return "0.0x (0.0 M/s)"
        mb_s = self._current_speed / (1024 * 1024)
        x_speed = mb_s / 4.5
        return f"{x_speed:.1f}x ({mb_s:.1f} M/s)"

    @property
    def eta_seconds(self) -> int:
        if self._current_speed == 0:
            return 0
        if self._bytes_history:
            current = self._bytes_history[-1]
            remaining = max(0, self._total_bytes - current)
            return int(remaining / self._current_speed)
        return 0


async def resolve_title_from_filename(
    path: Path,
    sorted_titles: list[DiscTitle],
    rip_index: int,
    job_id: int,
    session,
) -> DiscTitle | None:
    """Resolve a ripped .mkv file to a DiscTitle record.

    Matches using:
    1. Title index extracted from filename (e.g. B1_t03.mkv → index 3)
    2. Fallback: sequential rip_index mapped to sorted titles
    """
    title = None
    # path.name derives from the disc volume label (user-controlled), so sanitize
    # it before it reaches any log sink (py/log-injection).
    safe_name = sanitize_log_value(path.name)

    # Try to extract the MakeMKV title index from the filename (e.g.
    # B1_t00.mkv -> 0). This is the authoritative mapping — MakeMKV's _tNN is
    # the disc title index, which is also DiscTitle.title_index.
    title_index = title_index_from_filename(path.name)

    if title_index is not None:
        for st in sorted_titles:
            if st.title_index == title_index:
                title = await session.get(DiscTitle, st.id)
                break
        if title:
            logger.debug(
                f"Mapped {safe_name} to title_index={title_index} "
                f"(Title DB id={title.id}, Job {job_id})"
            )
        else:
            # The filename names a real title index that isn't among the titles
            # this rip produced — it's a foreign file (e.g. another title's
            # already-finished output sitting in the staging dir during a
            # single-title re-rip). Do NOT positionally fall back: that would
            # mis-attribute it onto the wrong (subset) title and stamp it with
            # the wrong filename. Treat it as unresolved.
            logger.debug(
                f"Ripped file {safe_name} has title_index={title_index} not in this "
                f"rip's title set — ignoring as foreign (Job {job_id})"
            )
            return None

    # Fallback: map by sequential rip order — only when the filename carried no
    # parseable title index at all (an odd disc naming scheme).
    if not title and 0 <= (rip_index - 1) < len(sorted_titles):
        st = sorted_titles[rip_index - 1]
        title = await session.get(DiscTitle, st.id)
        logger.debug(
            f"Fallback mapping: rip_index={rip_index} → "
            f"title_index={st.title_index} (Title DB id={st.id}, Job {job_id})"
        )

    if not title:
        logger.warning(f"Could not map ripped file {safe_name} to any title (Job {job_id})")

    return title


def find_staging_file(job: DiscJob, title: DiscTitle) -> Path | None:
    """Locate the staging .mkv file for a title.

    Tries, in order:
    1. The recorded ``output_filename`` path directly.
    2. ``staging_path / output_filename.name`` (file moved/renamed staging dir).
    3. A ``*_t{index:02d}.mkv`` glob within ``staging_path``.
    4. ``organized_to`` — the library path, for re-matching an already-organized
       title (e.g. from a completed job).
    """
    if title.output_filename:
        p = Path(title.output_filename)
        if p.exists():
            return p
        if job.staging_path:
            p2 = Path(job.staging_path) / p.name
            if p2.exists():
                return p2

    if job.staging_path:
        matches = list(Path(job.staging_path).glob(f"*_t{title.title_index:02d}.mkv"))
        if matches:
            return matches[0]

    organized_to = getattr(title, "organized_to", None)
    if organized_to:
        p = Path(organized_to)
        if p.exists():
            return p

    return None


def build_title_list(titles, *, include_video_resolution: bool = False) -> list[dict]:
    """Build a title list dict for WebSocket broadcast.

    Used by titles_discovered broadcasts to send title metadata to the frontend.
    """
    result = []
    for t in titles:
        entry = {
            "id": t.id,
            "title_index": t.title_index,
            "duration_seconds": t.duration_seconds,
            "file_size_bytes": t.file_size_bytes,
            "chapter_count": t.chapter_count,
            "state": "pending",
        }
        if include_video_resolution and hasattr(t, "video_resolution") and t.video_resolution:
            entry["video_resolution"] = t.video_resolution
        result.append(entry)
    return result
