"""Staging directory watcher for auto-importing pre-ripped MKV files.

Polls the staging directory for new subdirectories containing MKV files.
When a stable directory is detected (file sizes unchanged across consecutive
polls), fires a callback to create a job for processing.

Follows the same polling/callback pattern as DriveMonitor in sentinel.py.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Number of consecutive polls with stable file sizes before triggering import
STABILITY_THRESHOLD = 2


class StagingWatcher:
    """Watches a staging directory for new subdirectories with MKV files.

    Uses polling (not filesystem events) for cross-platform reliability,
    matching the DriveMonitor approach.
    """

    def __init__(self, staging_path: str, config=None) -> None:
        self._staging_path = Path(staging_path).expanduser()
        self._running = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_callback: Callable[[str, str, str], Any] | None = None
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

    async def _check_staging(self) -> None:
        """Scan staging directory for new subdirectories with MKV files."""
        if not self._staging_path.exists():
            return

        # List subdirectories (run in thread to avoid blocking)
        try:
            entries = await asyncio.to_thread(self._scan_staging_dir)
        except OSError as e:
            logger.debug(f"Could not scan staging directory: {e}")
            return

        # Track which dirs we saw this poll (for cleanup of stale entries)
        seen_dirs: set[str] = set()

        for dir_path, mkv_count, total_size in entries:
            dir_str = str(dir_path)
            seen_dirs.add(dir_str)

            # Skip already-processed directories
            if dir_str in self._processed_dirs:
                continue

            # Skip directories with no MKV files
            if mkv_count == 0:
                continue

            prev = self._known_dirs.get(dir_str)

            if prev is None:
                # New directory — start tracking
                self._known_dirs[dir_str] = {
                    "mkv_count": mkv_count,
                    "total_size": total_size,
                    "stable_polls": 0,
                }
                logger.debug(
                    f"Staging watcher: new directory {dir_path.name} "
                    f"({mkv_count} MKV files, {total_size} bytes)"
                )
            elif prev["mkv_count"] != mkv_count or prev["total_size"] != total_size:
                # Files changed — reset stability counter
                self._known_dirs[dir_str] = {
                    "mkv_count": mkv_count,
                    "total_size": total_size,
                    "stable_polls": 0,
                }
                logger.debug(
                    f"Staging watcher: {dir_path.name} changed "
                    f"(files: {prev['mkv_count']}→{mkv_count}, "
                    f"size: {prev['total_size']}→{total_size})"
                )
            else:
                # Same state — increment stability
                prev["stable_polls"] += 1

                if prev["stable_polls"] >= STABILITY_THRESHOLD:
                    # Directory is stable — fire callback
                    label = dir_path.name.upper().replace(" ", "_")
                    logger.info(
                        f"Staging watcher: {dir_path.name} is stable "
                        f"({mkv_count} MKV files, {total_size} bytes) — "
                        f"triggering import"
                    )
                    self._processed_dirs.add(dir_str)
                    del self._known_dirs[dir_str]
                    await self._notify("staging_ready", dir_str, label)

        # Clean up tracking for directories that disappeared
        stale_keys = [k for k in self._known_dirs if k not in seen_dirs]
        for key in stale_keys:
            del self._known_dirs[key]

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

    async def _notify(self, event: str, staging_dir: str, label: str) -> None:
        """Fire the async callback."""
        if self._async_callback:
            try:
                await self._async_callback(event, staging_dir, label)
            except Exception as e:
                logger.error(f"Staging watcher callback error: {e}", exc_info=True)
