"""Sentinel - Drive Monitor (Hardware Abstraction Layer).

Detects disc insertion/removal using polling on Windows.
This is a more reliable approach than WM_DEVICECHANGE for cross-platform compatibility.
"""

import asyncio
import ctypes
import logging
import string
import sys
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Windows constants
DRIVE_CDROM = 5  # Optical drive type

# Callback type
DriveCallback = Callable[[str, str, str], None]  # (drive_letter, event, volume_label)


def get_optical_drives() -> list[str]:
    """Get list of optical drive letters on the system."""
    if sys.platform != "win32":
        return []

    drives = []
    kernel32 = ctypes.windll.kernel32
    get_drive_type = kernel32.GetDriveTypeW

    for letter in string.ascii_uppercase:
        drive_path = f"{letter}:\\"
        drive_type = get_drive_type(drive_path)
        if drive_type == DRIVE_CDROM:
            drives.append(f"{letter}:")

    return drives


def get_volume_label(drive_letter: str) -> str:
    """Get the volume label for a drive."""
    if sys.platform != "win32":
        return ""

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


def is_disc_present(drive_letter: str) -> bool:
    """Check if a disc is present in the drive."""
    if sys.platform != "win32":
        return False

    kernel32 = ctypes.windll.kernel32

    # Try to get volume information - fails if no disc
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


def eject_disc(drive_letter: str) -> bool:
    """Eject a disc from the specified drive on Windows.

    Uses the Win32 mciSendString API for reliable disc ejection.
    Returns True if eject was successful.
    """
    if sys.platform != "win32":
        logger.warning("Disc eject only supported on Windows")
        return False

    try:
        winmm = ctypes.windll.winmm
        mci_send = winmm.mciSendStringW

        # Normalize drive letter (e.g., "F:" -> "F:")
        drive = drive_letter.rstrip("\\")

        # Open the CD drive, send eject, then close
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


class DriveMonitor:
    """Monitors optical drives for disc insertion/removal.

    Uses a polling approach for maximum reliability across Windows versions.
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
        """Check all optical drives for state changes."""
        for drive in get_optical_drives():
            current_state = is_disc_present(drive)
            previous_state = self._drive_states.get(drive, False)

            if current_state != previous_state:
                self._drive_states[drive] = current_state

                if current_state:
                    # Disc inserted
                    label = get_volume_label(drive)
                    await self._notify("inserted", drive, label)
                else:
                    # Disc removed
                    await self._notify("removed", drive, "")

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
