"""Sentinel - Drive Monitor (Hardware Abstraction Layer).

Detects disc insertion/removal using platform-specific APIs:
- Windows: kernel32 ctypes (GetDriveTypeW, GetVolumeInformationW)
- Linux: /sys/block/sr* enumeration, blkid, eject
"""

import asyncio
import glob
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

if sys.platform == "win32":
    import ctypes
    import string

logger = logging.getLogger(__name__)

# Windows constants
DRIVE_CDROM = 5  # Optical drive type

# Callback type
DriveCallback = Callable[[str, str, str], None]  # (drive_letter, event, volume_label)


# ---------------------------------------------------------------------------
# Windows implementations
# ---------------------------------------------------------------------------


def _get_optical_drives_windows() -> list[str]:
    """Get optical drive letters on Windows via kernel32."""
    drives = []
    kernel32 = ctypes.windll.kernel32
    get_drive_type = kernel32.GetDriveTypeW

    for letter in string.ascii_uppercase:
        drive_path = f"{letter}:\\"
        drive_type = get_drive_type(drive_path)
        if drive_type == DRIVE_CDROM:
            drives.append(f"{letter}:")

    return drives


def _get_volume_label_windows(drive_letter: str) -> str:
    """Get the volume label for a drive on Windows."""
    kernel32 = ctypes.windll.kernel32
    volume_name = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)

    result = kernel32.GetVolumeInformationW(
        f"{drive_letter}\\",
        volume_name,
        261,
        None,
        None,
        None,
        fs_name,
        261,
    )

    if result:
        return volume_name.value
    return ""


def _is_disc_present_windows(drive_letter: str) -> bool:
    """Check if a disc is present on Windows."""
    kernel32 = ctypes.windll.kernel32
    volume_name = ctypes.create_unicode_buffer(261)
    result = kernel32.GetVolumeInformationW(
        f"{drive_letter}\\",
        volume_name,
        261,
        None,
        None,
        None,
        None,
        0,
    )
    return bool(result)


def _eject_disc_windows(drive_letter: str) -> bool:
    """Eject a disc on Windows using mciSendString."""
    try:
        winmm = ctypes.windll.winmm
        mci_send = winmm.mciSendStringW
        drive = drive_letter.rstrip("\\")
        buf = ctypes.create_unicode_buffer(256)

        err = mci_send(f"open {drive} type cdaudio alias disc_eject", buf, 256, 0)
        if err != 0:
            logger.warning(f"mciSendString open failed for {drive} (error {err})")
            return False

        err = mci_send("set disc_eject door open", buf, 256, 0)
        mci_send("close disc_eject", buf, 256, 0)

        if err != 0:
            logger.warning(f"mciSendString eject failed for {drive} (error {err})")
            return False

        logger.info(f"Disc ejected from {drive}")
        return True
    except Exception:
        logger.exception(f"Failed to eject disc from {drive_letter}")
        return False


# ---------------------------------------------------------------------------
# Linux implementations
# ---------------------------------------------------------------------------


def _get_optical_drives_linux() -> list[str]:
    """Get optical drive device paths on Linux via /sys/block/sr*."""
    drives = []
    for block_dev in sorted(glob.glob("/sys/block/sr*")):
        dev_name = os.path.basename(block_dev)
        drives.append(f"/dev/{dev_name}")
    return drives


def _get_volume_label_linux(drive: str) -> str:
    """Get the volume label for a drive on Linux using blkid."""
    try:
        result = subprocess.run(
            ["blkid", "-s", "LABEL", "-o", "value", drive],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug(f"blkid failed for {drive}: {e}")
        return ""


def _is_disc_present_linux(drive: str) -> bool:
    """Check if a disc is present on Linux by reading /sys/block size."""
    try:
        dev_name = os.path.basename(drive)  # "sr0" from "/dev/sr0"
        size_path = f"/sys/block/{dev_name}/size"
        with open(size_path) as f:
            size = int(f.read().strip())
        return size > 0
    except (FileNotFoundError, ValueError, PermissionError, OSError):
        return False


def _eject_disc_linux(drive: str) -> bool:
    """Eject a disc on Linux using the eject command."""
    try:
        result = subprocess.run(
            ["eject", drive],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Disc ejected from {drive}")
            return True
        logger.warning(f"eject failed for {drive}: {result.stderr.strip()}")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"eject command failed for {drive}: {e}")
        return False


# ---------------------------------------------------------------------------
# Public API (dispatches by platform)
# ---------------------------------------------------------------------------


def get_optical_drives() -> list[str]:
    """Get list of optical drives on the system."""
    if sys.platform == "win32":
        return _get_optical_drives_windows()
    elif sys.platform == "linux":
        return _get_optical_drives_linux()
    return []


def get_volume_label(drive: str) -> str:
    """Get the volume label for a drive."""
    if sys.platform == "win32":
        return _get_volume_label_windows(drive)
    elif sys.platform == "linux":
        return _get_volume_label_linux(drive)
    return ""


def is_disc_present(drive: str) -> bool:
    """Check if a disc is present in the drive."""
    if sys.platform == "win32":
        return _is_disc_present_windows(drive)
    elif sys.platform == "linux":
        return _is_disc_present_linux(drive)
    return False


def eject_disc(drive: str) -> bool:
    """Eject a disc from the specified drive."""
    if sys.platform == "win32":
        return _eject_disc_windows(drive)
    elif sys.platform == "linux":
        return _eject_disc_linux(drive)
    logger.warning(f"Disc eject not supported on {sys.platform}")
    return False


# ---------------------------------------------------------------------------
# DriveMonitor class (platform-independent)
# ---------------------------------------------------------------------------


class DriveMonitor:
    """Monitors optical drives for disc insertion/removal.

    Uses a polling approach for maximum reliability across platforms.
    Poll interval is configurable via AppConfig.
    """

    def __init__(self, callback: DriveCallback | None = None, config=None) -> None:
        self._callback = callback
        self._running = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_callback: Callable[[str, str, str], Any] | None = None
        self._drive_states: dict[str, bool] = {}  # drive -> has_disc
        self._config = config
        self._poll_interval: float | None = None
        # Debounce: require 2 consecutive polls with the new state before firing.
        # Prevents spurious events from disc spinup flickering.
        self._pending_changes: dict[str, int] = {}  # drive -> consecutive polls with new state

    def set_async_callback(
        self,
        callback: Callable[[str, str, str], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Set an async callback to be called on drive events."""
        self._async_callback = callback
        self._loop = loop

    def start(self) -> None:
        """Start monitoring for drive events."""
        if self._running:
            return

        self._running = True

        # Initialize drive states and collect already-inserted discs
        discs_already_present: list[tuple[str, str]] = []
        for drive in get_optical_drives():
            has_disc = is_disc_present(drive)
            self._drive_states[drive] = has_disc
            if has_disc:
                label = get_volume_label(drive)
                discs_already_present.append((drive, label))
                logger.info(f"Initial state for {drive}: disc present (label: {label})")
            else:
                logger.debug(f"Initial state for {drive}: empty")

        # Start polling task
        if self._loop:
            self._task = self._loop.create_task(self._poll_loop())

            # Fire "inserted" events for discs already in drives at startup
            for drive, label in discs_already_present:
                self._loop.create_task(self._notify("inserted", drive, label))

        logger.info(
            f"Drive monitor started (polling mode, {len(self._drive_states)} optical drives found)"
        )

    def stop(self) -> None:
        """Stop the drive monitor."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

        logger.info("Drive monitor stopped")

    async def _poll_loop(self) -> None:
        """Poll for drive changes."""
        # Load poll interval from config
        if self._poll_interval is None:
            if self._config is None:
                from app.services.config_service import get_config_sync

                self._config = get_config_sync()
            self._poll_interval = self._config.sentinel_poll_interval

        while self._running:
            try:
                await self._check_drives()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in drive poll: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _check_drives(self) -> None:
        """Check all optical drives for state changes.

        Uses debounce: a state change must be seen on 2 consecutive polls
        before firing an event. This prevents spurious events from disc
        spinup flickering (GetVolumeInformationW can fail temporarily
        while the drive spins up, causing false "removed" events).
        """
        for drive in get_optical_drives():
            current_state = is_disc_present(drive)
            previous_state = self._drive_states.get(drive, False)

            if current_state != previous_state:
                # State differs — increment debounce counter
                self._pending_changes[drive] = self._pending_changes.get(drive, 0) + 1

                if self._pending_changes[drive] >= 2:
                    # Confirmed state change after 2 consecutive polls
                    self._drive_states[drive] = current_state
                    self._pending_changes.pop(drive, None)

                    if current_state:
                        label = get_volume_label(drive)
                        await self._notify("inserted", drive, label)
                    else:
                        await self._notify("removed", drive, "")
            else:
                # State matches — reset debounce counter
                self._pending_changes.pop(drive, None)

    async def _notify(self, event: str, drive: str, label: str) -> None:
        """Notify callbacks of a drive event."""
        logger.info(f"Drive event: {drive} {event} (label: {label})")

        if self._callback:
            self._callback(drive, event, label)

        if self._async_callback:
            try:
                await self._async_callback(drive, event, label)
            except Exception as e:
                logger.error(f"Error in async callback: {e}")
