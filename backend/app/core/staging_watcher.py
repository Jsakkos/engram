"""Staging directory watcher for auto-importing pre-ripped MKV files.

Polls the staging directory for new subdirectories containing MKV files.
When a stable directory is detected (file sizes unchanged across consecutive
polls), fires a callback to create a job for processing.

Follows the same polling/callback pattern as DriveMonitor in sentinel.py.
"""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Number of consecutive polls with stable file sizes before triggering import
STABILITY_THRESHOLD = 2

# Matches "Season 1", "season 01", "Season 12", etc.
_SEASON_RE = re.compile(r"^[Ss]eason\s*0*(\d+)$")


class StagingWatcher:
    """Watches a staging directory for new subdirectories with MKV files.

    Uses polling (not filesystem events) for cross-platform reliability,
    matching the DriveMonitor approach.
    """

    def __init__(
        self,
        staging_path: str,
        import_watch_path: str | None = None,
        import_destination_mode: str = "library",
        config=None,
    ) -> None:
        self._staging_path = Path(staging_path).expanduser() if staging_path else None
        self._import_watch_path = (
            Path(import_watch_path).expanduser() if import_watch_path else None
        )
        self._import_destination_mode = import_destination_mode
        self._running = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_callback: Callable[[str, str, str, dict | None], Any] | None = None
        self._config = config
        self._poll_interval: float = 2.0

        # Tracking state for each discovered subdirectory
        # path_str -> {"mkv_count": int, "total_size": int, "stable_polls": int}
        self._known_dirs: dict[str, dict] = {}
        # Directories that have already been processed (won't trigger again)
        self._processed_dirs: set[str] = set()

    def set_async_callback(
        self,
        callback: Callable[[str, str, str], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Set an async callback for staging events.

        Callback signature: (event, staging_dir_path, volume_label)
        """
        self._async_callback = callback
        self._loop = loop

    def start(self) -> None:
        """Start watching the staging directory."""
        if self._running:
            return

        self._running = True

        # Load poll interval from config
        if self._config:
            self._poll_interval = getattr(self._config, "sentinel_poll_interval", 2.0)

        if self._loop:
            self._task = self._loop.create_task(self._poll_loop())

        logger.info(
            f"Staging watcher started (path={self._staging_path}, interval={self._poll_interval}s)"
        )

    def stop(self) -> None:
        """Stop watching."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

        logger.info("Staging watcher stopped")

    async def _poll_loop(self) -> None:
        """Poll for new staging directories."""
        while self._running:
            try:
                await self._check_staging()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in staging watcher poll: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _update_stability(
        self,
        dir_str: str,
        dir_path: Path,
        mkv_count: int,
        total_size: int,
        metadata: dict | None,
    ) -> None:
        """Shared stability tracking for staging and import entries."""
        prev = self._known_dirs.get(dir_str)
        if prev is None:
            self._known_dirs[dir_str] = {
                "mkv_count": mkv_count,
                "total_size": total_size,
                "stable_polls": 0,
                "metadata": metadata,
            }
            logger.debug(
                f"Watcher: new directory {dir_path.name} "
                f"({mkv_count} MKV files, {total_size} bytes)"
            )
        elif prev["mkv_count"] != mkv_count or prev["total_size"] != total_size:
            self._known_dirs[dir_str] = {
                "mkv_count": mkv_count,
                "total_size": total_size,
                "stable_polls": 0,
                "metadata": metadata,
            }
            logger.debug(f"Watcher: {dir_path.name} changed — stability reset")
        else:
            prev["stable_polls"] += 1
            if prev["stable_polls"] >= STABILITY_THRESHOLD:
                label = dir_path.name.upper().replace(" ", "_")
                source = metadata["source"] if metadata else "staging"
                logger.info(
                    f"Watcher: {dir_path.name} is stable ({mkv_count} MKV files) — "
                    f"triggering import (source={source})"
                )
                self._processed_dirs.add(dir_str)
                del self._known_dirs[dir_str]
                await self._notify("staging_ready", dir_str, label, metadata)

    async def _check_staging(self) -> None:
        """Scan both staging and import paths for new MKV directories."""
        # --- Existing staging scan ---
        if self._staging_path and self._staging_path.exists():
            try:
                entries = await asyncio.to_thread(self._scan_staging_dir)
            except OSError as e:
                logger.debug(f"Could not scan staging directory: {e}")
                entries = []

            seen_staging: set[str] = set()
            for dir_path, mkv_count, total_size in entries:
                dir_str = str(dir_path)
                seen_staging.add(dir_str)
                if dir_str in self._processed_dirs:
                    continue
                if mkv_count == 0:
                    continue
                await self._update_stability(
                    dir_str, dir_path, mkv_count, total_size, metadata=None
                )

            # Clean up tracking for staging dirs that disappeared
            stale = [
                k
                for k in self._known_dirs
                if k not in seen_staging
                and not (
                    self._import_watch_path and str(k).startswith(str(self._import_watch_path))
                )
            ]
            for key in stale:
                del self._known_dirs[key]

        # --- Import path scan ---
        if self._import_watch_path and self._import_watch_path.exists():
            try:
                import_entries = await asyncio.to_thread(
                    self._scan_import_dir, self._import_watch_path
                )
            except OSError as e:
                logger.debug(f"Could not scan import directory: {e}")
                import_entries = []

            seen_import: set[str] = set()
            for dir_path, mkv_count, total_size, meta in import_entries:
                dir_str = str(dir_path)
                seen_import.add(dir_str)
                if dir_str in self._processed_dirs:
                    continue
                if mkv_count == 0:
                    continue
                await self._update_stability(
                    dir_str, dir_path, mkv_count, total_size, metadata=meta
                )

            # Clean up tracking for import dirs that disappeared
            if self._import_watch_path:
                stale_import = [
                    k
                    for k in self._known_dirs
                    if k not in seen_import and str(k).startswith(str(self._import_watch_path))
                ]
                for key in stale_import:
                    del self._known_dirs[key]

    def _scan_import_dir(self, root: Path) -> list[tuple[Path, int, int, dict]]:
        """Detect ARM output structure under root and return import units.

        Returns list of (dir_path, mkv_count, total_size, metadata) tuples.
        """
        units = []
        # Tally loose top-level MKVs during the single scan pass so the flat
        # (Pattern C) decision and its count/size don't need a second iterdir()
        # of root afterwards (avoids a redundant traversal and a TOCTOU window
        # where a file removed between passes would skew the count).
        root_loose_count = 0
        root_loose_size = 0
        try:
            for entry in os.scandir(root):
                if entry.is_file() and entry.name.lower().endswith(".mkv"):
                    # Defer the flat (Pattern C) decision: a root that also
                    # contains structured subfolders (Season/disc dirs) is a
                    # container, not a flat dump. Returning here on the first
                    # loose file (os.scandir order is arbitrary) would shadow
                    # those subfolders and skip importing them — the data-loss
                    # path that left un-imported Season folders to be deleted.
                    root_loose_count += 1
                    try:
                        root_loose_size += entry.stat().st_size
                    except OSError:
                        pass  # File vanished between scandir and stat; skip its size
                    continue

                if not entry.is_dir():
                    continue

                subdir = Path(entry.path)
                # Check for Pattern B: subdir contains Season subdirs with MKVs
                season_units = self._try_pattern_b(subdir)
                if season_units:
                    units.extend(season_units)
                    continue

                # Pattern B': the watch root IS the show — subdir itself is a Season
                # folder (root/Season NN/*.mkv). Derive the show name from the root.
                season_match = _SEASON_RE.match(entry.name)
                if season_match:
                    mkv_count, total_size = self._count_mkvs(subdir)
                    if mkv_count > 0:
                        units.append(
                            (
                                subdir,
                                mkv_count,
                                total_size,
                                {
                                    "structure": "show_organised",
                                    "show_name": root.name,
                                    "season": int(season_match.group(1)),
                                    "destination_mode": self._import_destination_mode,
                                    "source": "import",
                                },
                            )
                        )
                    continue

                # Pattern A: subdir directly contains MKVs
                mkv_count, total_size = self._count_mkvs(subdir)
                if mkv_count > 0:
                    units.append(
                        (
                            subdir,
                            mkv_count,
                            total_size,
                            {
                                "structure": "disc_folder",
                                "show_name": None,
                                "season": None,
                                "destination_mode": self._import_destination_mode,
                                "source": "import",
                            },
                        )
                    )
        except OSError as e:
            logger.debug(f"Could not scan import directory {root}: {e}")

        # Pattern C (flat): treat the whole root as one unit ONLY when it has
        # loose MKVs and no structured subfolder units were found. When both
        # exist, the subfolders win and the loose top-level files are left
        # untouched (they're ambiguous specials/strays — move them into a
        # Season folder to import them).
        if root_loose_count and not units:
            units.append(
                (
                    root,
                    root_loose_count,
                    root_loose_size,
                    {
                        "structure": "flat",
                        "show_name": None,
                        "season": None,
                        "destination_mode": self._import_destination_mode,
                        "source": "import",
                    },
                )
            )
        elif root_loose_count and units:
            logger.info(
                "Import scan: %s has structured subfolders, so %d loose top-level "
                "MKV file(s) were left un-imported (move them into a Season folder "
                "to import them)",
                root,
                root_loose_count,
            )
        return units

    def _try_pattern_b(self, show_dir: Path) -> list[tuple[Path, int, int, dict]]:
        """Return season units if show_dir looks like a show-organised ARM folder."""
        units = []
        try:
            for entry in os.scandir(show_dir):
                if not entry.is_dir():
                    continue
                m = _SEASON_RE.match(entry.name)
                if not m:
                    continue
                season_num = int(m.group(1))
                season_dir = Path(entry.path)
                mkv_count, total_size = self._count_mkvs(season_dir)
                if mkv_count > 0:
                    units.append(
                        (
                            season_dir,
                            mkv_count,
                            total_size,
                            {
                                "structure": "show_organised",
                                "show_name": show_dir.name,
                                "season": season_num,
                                "destination_mode": self._import_destination_mode,
                                "source": "import",
                            },
                        )
                    )
        except OSError as e:
            logger.debug("Could not scan show directory %s: %s", show_dir, e)
        return units

    def _count_mkvs(self, directory: Path) -> tuple[int, int]:
        """Return (mkv_count, total_size_bytes) for MKVs directly inside directory."""
        count, size = 0, 0
        try:
            for f in directory.iterdir():
                if f.is_file() and f.suffix.lower() == ".mkv":
                    count += 1
                    try:
                        size += f.stat().st_size
                    except OSError:
                        pass  # File removed between iterdir and stat; skip size contribution
        except OSError as e:
            logger.debug("Could not scan directory %s: %s", directory, e)
        return count, size

    def _scan_staging_dir(self) -> list[tuple[Path, int, int]]:
        """Scan staging directory for subdirectories with MKV files.

        Returns list of (dir_path, mkv_count, total_size) tuples.
        Runs in a thread via asyncio.to_thread().
        """
        results = []
        try:
            for entry in os.scandir(self._staging_path):
                if not entry.is_dir():
                    continue
                # Skip job_* directories (managed by ripping pipeline)
                if entry.name.startswith("job_"):
                    continue
                dir_path = Path(entry.path)
                mkv_count = 0
                total_size = 0
                for f in dir_path.iterdir():
                    if f.suffix.lower() == ".mkv" and f.is_file():
                        mkv_count += 1
                        try:
                            total_size += f.stat().st_size
                        except OSError:
                            pass
                results.append((dir_path, mkv_count, total_size))
        except OSError:
            pass
        return results

    async def _notify(
        self, event: str, staging_dir: str, label: str, metadata: dict | None = None
    ) -> None:
        """Fire the async callback."""
        if self._async_callback:
            try:
                await self._async_callback(event, staging_dir, label, metadata)
            except Exception as e:
                logger.error(f"Watcher callback error: {e}", exc_info=True)
