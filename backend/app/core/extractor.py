"""Extractor - MakeMKV CLI Wrapper.

Handles disc scanning and extraction using makemkvcon.
"""

import asyncio
import concurrent.futures
import hashlib
import logging
import re
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.analyst import TitleInfo

logger = logging.getLogger(__name__)

# Matches a robot-mode MSG line announcing a created .mkv file, e.g. ... "Show_t00.mkv" ...
_CREATED_MKV_PATTERN = re.compile(r'["\']([^"\']+\.mkv)["\']')

# Single source of truth for the user-facing stall reason, shared by the watchdog
# callback and the job_manager fallback so the live update and History agree.
STALL_FAILURE_REASON = "Ripping stalled — no progress; the disc may be dirty or damaged."


def _to_drive_spec(drive: str) -> str:
    """Normalize a drive identifier into a MakeMKV drive spec.

    Drive letters/device paths become ``dev:<drive>``; ``disc:N`` specs pass through.
    """
    if not drive.startswith("disc:"):
        return f"dev:{drive}"
    return drive


def _is_stalled(now: float, last_progress: float, timeout: float) -> bool:
    """Whether a rip is stalled: no progress for `timeout` seconds.

    "Progress" is any liveness signal — output-file growth OR MakeMKV stdout
    activity — recorded in ``last_progress``. A tiny track that has finished
    writing but is still emitting progress lines is therefore NOT stalled.
    """
    return (now - last_progress) >= timeout


def _extract_created_mkv(line: str, output_dir: Path) -> Path | None:
    """Extract the created .mkv path from a MakeMKV output line, if present."""
    if ".mkv" not in line or "created" not in line:
        return None
    match = _CREATED_MKV_PATTERN.search(line)
    if not match:
        return None
    return output_dir / Path(match.group(1)).name


def _safe_callback(cb: Callable, *args, label: str) -> None:
    """Invoke a user-supplied callback, logging (but not raising) any exception.

    CancelledError is not caught here — it is not an ``Exception`` subclass.
    """
    try:
        cb(*args)
    except Exception as e:
        logger.exception(f"Error in {label}: {e}")


def _terminate_proc(proc: subprocess.Popen, timeout: float = 5.0, *, label: str = "") -> None:
    """Terminate a subprocess, escalating to SIGKILL if it ignores SIGTERM.

    Blocking (calls ``proc.wait``) — run via ``asyncio.to_thread`` from async
    callers so the event loop is never blocked. Guarantees a hung makemkvcon
    cannot be left orphaned.
    """
    name = label or f"pid {proc.pid}"
    try:
        proc.terminate()
    except (ProcessLookupError, PermissionError) as e:
        logger.debug(f"terminate() failed for {name}: {e}")
        return
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        logger.warning(f"Process {name} did not exit within {timeout}s; sending SIGKILL")
    try:
        proc.kill()
        proc.wait(timeout=timeout)
    except (ProcessLookupError, PermissionError) as e:
        logger.debug(f"kill() failed for {name}: {e}")
    except subprocess.TimeoutExpired:
        logger.error(f"Process {name} survived SIGKILL after {timeout}s")


# Callback types
TitleCompleteCallback = Callable[[int, Path], None]
TitleErrorCallback = Callable[[int, str], None]  # (command_idx, error_reason)


@dataclass
class RipResult:
    """Result of a ripping operation."""

    success: bool
    output_files: list[Path]
    error_message: str | None = None
    stalled_titles: list[int] | None = None  # Command indices that were skipped due to stall


class ScanTimeoutError(Exception):
    """MakeMKV disc scan exceeded time limit."""


def _save_makemkv_log(log_path: Path, content: str) -> None:
    """Save MakeMKV output to a log file for TheDiscDB contributions."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(content, encoding="utf-8")
        logger.debug(f"Saved MakeMKV log to {log_path}")
    except OSError as e:
        logger.warning(f"Failed to save MakeMKV log to {log_path}: {e}")


def _find_linux_mount_point(device: str) -> Path | None:
    """Find the mount point for a block device on Linux by parsing /proc/mounts."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device:
                    return Path(parts[1])
    except (OSError, PermissionError) as e:
        logger.debug(f"Could not read /proc/mounts: {e}")
    return None


def compute_content_hash(drive: str) -> str | None:
    """Compute TheDiscDB-compatible ContentHash for a disc.

    The hash is MD5 of concatenated Int64 file sizes from BDMV/STREAM/*.m2ts
    (Blu-ray) or VIDEO_TS/* (DVD), sorted by filename. This matches the
    algorithm used by TheDiscDB's ImportBuddy tool.

    Args:
        drive: Drive letter (e.g., "E:" or "E") on Windows, or device path
               (e.g., "/dev/sr0") on Linux. On Linux the disc must be mounted
               for the hash to be computed; returns None if not mounted.

    Returns:
        Uppercase hex MD5 hash string, or None if disc structure not found
    """
    if sys.platform != "win32":
        mount_point = _find_linux_mount_point(drive)
        if mount_point is None:
            logger.debug(f"Device {drive} is not mounted; cannot compute ContentHash.")
            return None
        bdmv_path = mount_point / "BDMV" / "STREAM"
        dvd_path = mount_point / "VIDEO_TS"
    else:
        clean_drive = drive.rstrip(":\\")
        bdmv_path = Path(f"{clean_drive}:\\BDMV\\STREAM")
        dvd_path = Path(f"{clean_drive}:\\VIDEO_TS")

    target_path = None
    pattern = "*"

    if bdmv_path.is_dir():
        target_path = bdmv_path
        pattern = "*.m2ts"
    elif dvd_path.is_dir():
        target_path = dvd_path
    else:
        logger.debug(f"No BDMV/STREAM or VIDEO_TS found on drive {drive}")
        return None

    try:
        files = sorted(target_path.glob(pattern), key=lambda f: f.name)
        if not files:
            return None

        md5 = hashlib.md5()
        for f in files:
            size = f.stat().st_size
            # Pack as Int64 little-endian (matches C# BitConverter.GetBytes(long))
            md5.update(struct.pack("<q", size))

        content_hash = md5.hexdigest().upper()
        logger.info(f"Computed ContentHash for drive {drive}: {content_hash}")
        return content_hash
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not compute ContentHash for drive {drive}: {e}")
        return None


class MakeMKVExtractor:
    """Wrapper for MakeMKV command-line interface."""

    def __init__(self, makemkv_path: Path | None = None) -> None:
        self._makemkv_path_override = makemkv_path
        # Per-job process tracking for multi-drive cancel isolation.
        # Each running job registers its subprocess here so cancel() only
        # terminates the correct process.
        self._processes: dict[int, subprocess.Popen] = {}  # job_id -> process
        self._cancelled_jobs: set[int] = set()
        # Per-drive locks prevent concurrent MakeMKV operations on the same drive.
        # Two makemkvcon processes fighting over one drive causes both to stall/fail.
        self._drive_locks: dict[str, asyncio.Lock] = {}

    @property
    def makemkv_path(self) -> Path:
        """Get MakeMKV path, lazy-loading from DB config if not explicitly set."""
        if self._makemkv_path_override is not None:
            return self._makemkv_path_override
        from app.services.config_service import get_config_sync

        return Path(get_config_sync().makemkv_path)

    def _get_drive_lock(self, drive: str) -> asyncio.Lock:
        """Get or create a per-drive lock to serialize MakeMKV operations."""
        # Normalize drive key (e.g., "F:" and "dev:F:" should use same lock)
        key = drive.replace("dev:", "").replace("disc:", "").rstrip("\\")
        if key not in self._drive_locks:
            self._drive_locks[key] = asyncio.Lock()
        return self._drive_locks[key]

    async def scan_disc(
        self, drive: str, log_dir: Path | None = None, *, job_id: int = 0
    ) -> tuple[list[TitleInfo], str]:
        """Scan a disc and return title information and the disc display name.

        Args:
            drive: Drive letter (e.g., "E:") or disc index (e.g., "disc:0")
            log_dir: Optional directory for saving MakeMKV scan logs

        Returns:
            (titles, disc_name) — list of titles and the CINFO:2 disc display name
            (disc_name is empty string when not present in MakeMKV output)
        """
        lock = self._get_drive_lock(drive)
        if lock.locked():
            logger.warning(
                f"Drive {drive} is already in use by another MakeMKV operation, "
                f"waiting for it to finish"
            )

        async with lock:
            return await self._scan_disc_unlocked(drive, log_dir=log_dir, job_id=job_id)

    async def _scan_disc_unlocked(
        self, drive: str, log_dir: Path | None = None, *, job_id: int = 0
    ) -> tuple[list[TitleInfo], str]:
        """Internal scan implementation (caller must hold drive lock)."""
        drive_spec = _to_drive_spec(drive)

        cmd = [
            str(self.makemkv_path),
            "-r",  # Robot mode (machine-readable output)
            "info",
            drive_spec,
        ]

        start = time.monotonic()
        logger.info(f"Scanning disc: {' '.join(cmd)}")

        self._cancelled_jobs.discard(job_id)

        def run_makemkv() -> subprocess.CompletedProcess:
            """Run MakeMKV in a thread (Windows asyncio subprocess workaround)."""
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self._processes[job_id] = proc
            try:
                stdout, stderr = proc.communicate(timeout=600)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()  # Clean up zombie process
                raise
            finally:
                self._processes.pop(job_id, None)

        try:
            # Run in thread to avoid blocking event loop
            result = await asyncio.to_thread(run_makemkv)

            elapsed = time.monotonic() - start
            logger.debug(f"MakeMKV stdout: {result.stdout[:500] if result.stdout else 'empty'}")
            if result.stderr:
                logger.debug(f"MakeMKV stderr: {result.stderr[:500]}")

            if result.returncode != 0:
                logger.error(
                    f"MakeMKV scan failed after {elapsed:.1f}s "
                    f"(exit code {result.returncode}): {result.stderr}"
                )
                return [], ""

            titles, disc_name = self._parse_disc_info(result.stdout or "")
            logger.info(
                f"Scan completed in {elapsed:.1f}s, found {len(titles)} titles"
                + (f", disc name: '{disc_name}'" if disc_name else "")
            )

            # Save scan log for TheDiscDB contributions
            if log_dir and result.stdout:
                _save_makemkv_log(log_dir / "scan.log", result.stdout)

            return titles, disc_name

        except FileNotFoundError:
            logger.error(f"MakeMKV not found at: {self.makemkv_path}")
            return [], ""
        except subprocess.TimeoutExpired as e:
            elapsed = time.monotonic() - start
            logger.error(f"MakeMKV scan timed out after {elapsed:.1f}s for drive {drive}")
            raise ScanTimeoutError(f"Disc scan timed out after 10 minutes on drive {drive}") from e
        except Exception as e:
            logger.exception(f"Error scanning disc: {e}")
            return [], ""

    async def rip_titles(
        self,
        drive: str,
        output_dir: Path,
        title_indices: list[int] | None = None,
        title_complete_callback: TitleCompleteCallback | None = None,
        stall_timeout: float | None = None,
        title_error_callback: TitleErrorCallback | None = None,
        log_dir: Path | None = None,
        *,
        job_id: int = 0,
    ) -> RipResult:
        """Rip selected titles from a disc.

        Args:
            drive: Drive letter or disc specification
            output_dir: Directory to save MKV files
            title_indices: List of title indices to rip, or None for all
            title_complete_callback: Optional callback when a title finishes ripping
            stall_timeout: Seconds of no file growth before killing the process
                and skipping to the next title. None or 0 disables detection.
            title_error_callback: Optional callback when a title fails (e.g., stall
                detected). Called with (command_idx, error_reason).
            log_dir: Optional directory for saving MakeMKV rip logs

        Returns:
            RipResult with success status and output files
        """
        lock = self._get_drive_lock(drive)
        if lock.locked():
            logger.warning(
                f"Drive {drive} is already in use by another MakeMKV operation, "
                f"waiting for it to finish"
            )

        async with lock:
            return await self._rip_titles_unlocked(
                drive,
                output_dir,
                title_indices,
                title_complete_callback,
                stall_timeout=stall_timeout,
                title_error_callback=title_error_callback,
                log_dir=log_dir,
                job_id=job_id,
            )

    async def _rip_titles_unlocked(
        self,
        drive: str,
        output_dir: Path,
        title_indices: list[int] | None = None,
        title_complete_callback: TitleCompleteCallback | None = None,
        stall_timeout: float | None = None,
        title_error_callback: TitleErrorCallback | None = None,
        log_dir: Path | None = None,
        *,
        job_id: int = 0,
    ) -> RipResult:
        """Internal rip implementation (caller must hold drive lock)."""
        self._cancelled_jobs.discard(job_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        drive_spec = _to_drive_spec(drive)

        # Prepare commands to run
        commands = []

        if not title_indices:
            # Rip ALL titles (single command, most efficient)
            logger.info("Ripping ALL titles")
            commands.append(
                [
                    str(self.makemkv_path),
                    "-r",
                    "--progress=-same",
                    "mkv",
                    drive_spec,
                    "all",
                    str(output_dir),
                ]
            )
        elif len(title_indices) == 1:
            # Rip single specific title
            idx = title_indices[0]
            logger.info(f"Ripping single title {idx}")
            commands.append(
                [
                    str(self.makemkv_path),
                    "-r",
                    "--progress=-same",
                    "mkv",
                    drive_spec,
                    str(idx),
                    str(output_dir),
                ]
            )
        else:
            # Rip multiple specific titles (must loop commands)
            logger.info(f"Ripping {len(title_indices)} specific titles: {title_indices}")
            for idx in title_indices:
                commands.append(
                    [
                        str(self.makemkv_path),
                        "-r",
                        "--progress=-same",
                        "mkv",
                        drive_spec,
                        str(idx),
                        str(output_dir),
                    ]
                )

        # State tracking for progress
        current_title_idx = 0  # 0-based absolute index for progress reporting

        output_lines: list[str] = []
        output_files: list[Path] = []

        # Shared state for filesystem-based title completion detection.
        known_files: dict[str, int] = {}  # filename -> last known size
        completed_files: set[str] = set()
        # Tracks consecutive polls where the file size has been stable.  We
        # require STABLE_CHECKS_REQUIRED consecutive stable readings before
        # declaring in-flight completion, which avoids false positives caused
        # by brief MakeMKV write pauses (e.g. buffering or disc seeking) that
        # may hold the file size constant for one polling interval (~3 s).
        stable_counts: dict[str, int] = {}
        # How many consecutive stable size polls are needed to declare a file
        # complete *while the MakeMKV process is still running*.  Post-process
        # force checks bypass this requirement entirely.
        STABLE_CHECKS_REQUIRED = 3  # 3 × 3 s ≈ 9 s of write-silence needed
        _fs_lock = threading.Lock()

        def _fire_title_complete(fname: str, size: int) -> None:
            """Mark *fname* complete and invoke the title callback (lock held)."""
            completed_files.add(fname)
            stable_counts.pop(fname, None)
            filepath = output_dir / fname
            output_files.append(filepath)
            logger.info(f"Title file completed: {fname} ({size / 1024 / 1024:.0f} MB)")
            if title_complete_callback:
                _safe_callback(
                    title_complete_callback,
                    len(completed_files),
                    filepath,
                    label="title complete callback",
                )

        def _check_for_completed_files(force: bool = False) -> None:
            """Scan output dir for newly completed .mkv files.

            In-flight (``force=False``): requires *STABLE_CHECKS_REQUIRED*
            consecutive polls with an identical, non-zero size before firing.
            This prevents false positives from MakeMKV briefly pausing writes
            between disc segments or while buffering.

            Post-process (``force=True``): fires immediately for any file with
            a non-zero size not yet marked complete.  Called after
            ``process.wait()`` returns so the write is guaranteed finished.
            """
            with _fs_lock:
                try:
                    for mkv in output_dir.glob("*.mkv"):
                        fname = mkv.name
                        if fname in completed_files:
                            continue
                        current_size = mkv.stat().st_size
                        if fname in known_files:
                            if current_size > 0:
                                if force:
                                    # Process exited: write is guaranteed done.
                                    _fire_title_complete(fname, current_size)
                                elif current_size == known_files[fname]:
                                    # Size unchanged since last check — increment counter.
                                    stable_counts[fname] = stable_counts.get(fname, 0) + 1
                                    if stable_counts[fname] >= STABLE_CHECKS_REQUIRED:
                                        _fire_title_complete(fname, current_size)
                                else:
                                    # File is still growing — reset stability counter.
                                    stable_counts[fname] = 0
                        known_files[fname] = current_size
                except (OSError, PermissionError) as e:
                    logger.exception(f"Error checking for completed files: {e}")

        def run_rip_with_streaming() -> tuple[int, str, set[int]]:
            """Run ripping commands in sequence."""
            nonlocal current_title_idx

            last_fs_check = time.monotonic()
            combined_stderr = ""
            final_returncode = 0
            # Tracks which commands were terminated due to stall detection
            stalled_commands: set[int] = set()
            # Liveness timestamp shared with the stdout reader. It is bumped both on
            # MakeMKV progress lines (see PRGC/PRGV parsing below) and on output-file
            # growth, so a small/fast track that has finished writing but is still
            # being finalized — while MakeMKV keeps emitting progress — is not flagged
            # as stalled. Single-element list write/read is atomic under the GIL.
            last_progress = [time.monotonic()]

            def _stall_watchdog(proc, watch_dir, timeout, poll_interval=5.0):
                """Terminate MakeMKV if it shows no liveness for `timeout` seconds.

                Runs in a daemon thread alongside each MakeMKV subprocess. Liveness
                is output-file growth OR MakeMKV stdout activity (recorded in
                ``last_progress`` by the reader loop). Using stdout — not file growth
                alone — avoids false positives on tiny tracks that finish writing in
                one burst while MakeMKV is still emitting progress.
                """
                prev_sizes: dict[str, int] = {}

                while proc.poll() is None:
                    time.sleep(poll_interval)
                    if proc.poll() is not None:
                        break

                    current_sizes: dict[str, int] = {}
                    try:
                        for mkv in watch_dir.glob("*.mkv"):
                            try:
                                current_sizes[mkv.name] = mkv.stat().st_size
                            except OSError:
                                pass
                    except OSError:
                        continue

                    # File growth counts as liveness.
                    for name, size in current_sizes.items():
                        if size > prev_sizes.get(name, 0):
                            last_progress[0] = time.monotonic()
                            break

                    if _is_stalled(time.monotonic(), last_progress[0], timeout):
                        logger.warning(
                            f"Ripping stall detected: no progress for "
                            f"{timeout:.0f}s. Terminating MakeMKV process "
                            f"(command {current_title_idx}/{len(commands)})"
                        )
                        stalled_commands.add(current_title_idx)
                        # Fire error callback immediately so the UI
                        # shows the title as FAILED right away
                        if title_error_callback:
                            _safe_callback(
                                title_error_callback,
                                current_title_idx,
                                STALL_FAILURE_REASON,
                                label="title_error_callback",
                            )
                        try:
                            proc.terminate()
                        except (ProcessLookupError, PermissionError):
                            # Process already gone (raced its own exit) — nothing to kill.
                            pass
                        return

                    prev_sizes = current_sizes

            try:
                self._cancelled_jobs.discard(job_id)

                for cmd in commands:
                    if job_id in self._cancelled_jobs:
                        break

                    current_title_idx += 1
                    logger.info(
                        f"Executing rip command {current_title_idx}/{len(commands)}: "
                        f"{' '.join(cmd)}"
                    )

                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,  # Line buffered
                    )
                    self._processes[job_id] = process

                    # Start stall watchdog thread if timeout is configured
                    watchdog_thread = None
                    if stall_timeout and stall_timeout > 0:
                        # Fresh liveness baseline for this title's watchdog.
                        last_progress[0] = time.monotonic()
                        watchdog_thread = threading.Thread(
                            target=_stall_watchdog,
                            args=(process, output_dir, stall_timeout),
                            daemon=True,
                        )
                        watchdog_thread.start()

                    # Read stdout line by line
                    for line in iter(process.stdout.readline, ""):
                        if job_id in self._cancelled_jobs:
                            process.terminate()
                            break

                        line = line.strip()
                        if not line:
                            continue

                        output_lines.append(line)

                        # MakeMKV progress codes are liveness — feed the stall
                        # watchdog so a tiny track being finalized isn't killed.
                        #
                        # We deliberately do NOT derive per-title progress from
                        # PRGC/PRGV. Their robot-mode format is:
                        #   PRGC:code,id,"name"   PRGT:code,id,"name"
                        #   PRGV:current,total,max
                        # `code` is a *message code* (e.g. 5017 "Saving to MKV
                        # file"), NOT a title index, and `max` is a fixed 65536
                        # scale (per-bar % is value/max, never current/total).
                        # Per-title progress and completion are owned by the
                        # filesystem (stable output-file size) via
                        # _check_for_completed_files + the job's fs monitor, the
                        # only signal that reliably maps to a specific title.
                        if line.startswith(("PRGV:", "PRGC:", "PRGT:")):
                            last_progress[0] = time.monotonic()

                        # Catch robot-mode MSG lines about file creation
                        filepath = _extract_created_mkv(line, output_dir)
                        if filepath is not None:
                            # Track the file for stable-size detection but do NOT
                            # fire title_complete_callback — the file was just created,
                            # not finished writing. Let _check_for_completed_files
                            # detect true completion via stable file size.
                            with _fs_lock:
                                if filepath.name not in known_files:
                                    known_files[filepath.name] = 0
                                    logger.info(f"MakeMKV created output file: {filepath.name}")

                        # Also check filesystem periodically from the thread
                        now = time.monotonic()
                        if now - last_fs_check >= 3.0:
                            _check_for_completed_files()
                            last_fs_check = now

                    # End of process loop
                    process.wait()

                    # Join watchdog thread if it was started
                    if watchdog_thread is not None:
                        watchdog_thread.join(timeout=2.0)

                    if process.returncode != 0:
                        was_stall = current_title_idx in stalled_commands
                        stderr = process.stderr.read() if process.stderr else ""

                        if was_stall:
                            # Stall detected — delete incomplete file, continue
                            logger.warning(
                                f"Command {current_title_idx}/{len(commands)} "
                                f"terminated due to stall. Skipping to next title."
                            )
                            # Delete incomplete .mkv files created by this command
                            for mkv in output_dir.glob("*.mkv"):
                                if mkv.name not in completed_files:
                                    try:
                                        size_mb = mkv.stat().st_size / 1024 / 1024
                                        mkv.unlink()
                                        logger.info(
                                            f"Deleted incomplete file: {mkv.name} "
                                            f"({size_mb:.0f} MB)"
                                        )
                                    except OSError as e:
                                        logger.warning(
                                            f"Failed to delete incomplete file {mkv.name}: {e}"
                                        )
                            # Don't break — continue to next command
                            continue

                        combined_stderr += f"\nCommand failed ({cmd}): {stderr}"
                        final_returncode = process.returncode
                        # If one fails in a loop, should we stop? Yes, probably.
                        if len(commands) > 1:
                            break

                    # Final fs check for this command — process has exited so
                    # the write is guaranteed done; bypass stability counter.
                    _check_for_completed_files(force=True)

                # End of all commands
                return (final_returncode, combined_stderr, stalled_commands)

            except Exception as e:
                logger.exception("Error in rip subprocess")
                proc = self._processes.get(job_id)
                if proc:
                    try:
                        proc.terminate()
                    except (ProcessLookupError, PermissionError) as term_err:
                        logger.debug(f"Could not terminate MakeMKV process: {term_err}")
                return (-1, str(e), set())
            finally:
                self._processes.pop(job_id, None)

        try:
            # Start ripping in thread
            last_async_fs_check = time.monotonic()

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_rip_with_streaming)

                # Poll the filesystem for title completion while ripping. This
                # runs even if MakeMKV stdout is block-buffered, and is the sole
                # signal that maps completion to a specific title.
                while not future.done():
                    now = time.monotonic()
                    if now - last_async_fs_check >= 3.0:
                        await asyncio.to_thread(_check_for_completed_files)
                        last_async_fs_check = now

                    await asyncio.sleep(0.1)  # Yield to other tasks

                # Final filesystem check after process exits — force mode so
                # any title not yet fired by the stability counter is caught.
                await asyncio.to_thread(lambda: _check_for_completed_files(force=True))

                returncode, stderr, stalled = future.result()

            logger.debug(f"Rip completed with return code {returncode}")

            # Save rip log for TheDiscDB contributions
            if log_dir and output_lines:
                _save_makemkv_log(log_dir / "rip.log", "\n".join(output_lines))

            if job_id in self._cancelled_jobs:
                return RipResult(
                    success=False,
                    output_files=[],
                    error_message="Ripping cancelled by user",
                )

            # Fallback: parse output_lines if thread didn't track any files
            if not output_files:
                for line in output_lines:
                    filepath = _extract_created_mkv(line, output_dir)
                    if filepath is not None:
                        output_files.append(filepath)

            stalled_list = sorted(stalled) if stalled else None

            if returncode != 0 and not stalled:
                return RipResult(
                    success=False,
                    output_files=output_files,
                    error_message=stderr or "Unknown error during ripping",
                    stalled_titles=stalled_list,
                )

            # Find all MKV files in output directory if none tracked
            if not output_files:
                output_files = list(output_dir.glob("*.mkv"))

            if stalled:
                logger.warning(f"Ripping complete with {len(stalled)} stalled title(s) skipped")

            logger.info(f"Ripping complete: {len(output_files)} files created")
            return RipResult(
                success=True,
                output_files=output_files,
                stalled_titles=stalled_list,
            )

        except Exception as e:
            logger.exception("Error during ripping")
            return RipResult(
                success=False,
                output_files=[],
                error_message=str(e),
            )

    def cancel(self, job_id: int) -> None:
        """Cancel ripping for a specific job."""
        self._cancelled_jobs.add(job_id)
        proc = self._processes.get(job_id)
        if proc:
            try:
                proc.terminate()
            except (ProcessLookupError, PermissionError) as e:
                logger.debug(f"Could not terminate MakeMKV process for job {job_id}: {e}")

    async def shutdown(self, grace: float = 5.0) -> None:
        """Drain all tracked MakeMKV subprocesses on server shutdown.

        Marks every active job cancelled, then terminates (escalating to SIGKILL)
        each subprocess off the event loop so no makemkvcon survives shutdown.
        """
        # Snapshot first: the rip thread's finally-block pops from _processes
        # concurrently, and its .pop(..., None) already tolerates a missing key.
        procs = list(self._processes.items())
        if not procs:
            return
        logger.info(f"Draining {len(procs)} MakeMKV subprocess(es) on shutdown")
        for job_id, _ in procs:
            self._cancelled_jobs.add(job_id)
        await asyncio.gather(
            *(
                asyncio.to_thread(_terminate_proc, proc, grace, label=f"job {job_id}")
                for job_id, proc in procs
            )
        )
        self._processes.clear()
        self._cancelled_jobs.clear()

    def _parse_disc_info(self, output: str) -> tuple[list[TitleInfo], str]:
        """Parse MakeMKV robot-mode output to extract title information and disc name.

        MakeMKV output format (robot mode):
            CINFO:2,0,"Disc display name"   (disc-level title, attr 2)
            TINFO:0,2,0,"Title name"
            TINFO:0,9,0,"1:30:45"  (duration)
            TINFO:0,10,0,"12.5 GB"  (size)
            TINFO:0,8,0,"24"  (chapter count)
            TINFO:0,27,0,"Show - Season 3_t00.mkv"  (suggested output filename)

        Returns:
            (titles, disc_name) where disc_name is the CINFO:2 value (empty string if absent)
        """
        titles: dict[int, TitleInfo] = {}
        disc_name = ""

        for line in output.split("\n"):
            line = line.strip()

            # Capture disc display name from CINFO attr 2
            if line.startswith("CINFO:"):
                match = re.match(r"CINFO:(\d+),\d+,\"(.*)\"", line)
                if match and int(match.group(1)) == 2:
                    disc_name = match.group(2)
                continue

            # Parse TINFO lines
            if line.startswith("TINFO:"):
                match = re.match(r"TINFO:(\d+),(\d+),\d+,\"(.*)\"", line)
                if match:
                    title_idx = int(match.group(1))
                    attr_id = int(match.group(2))
                    value = match.group(3)

                    if title_idx not in titles:
                        titles[title_idx] = TitleInfo(
                            index=title_idx,
                            duration_seconds=0,
                            size_bytes=0,
                            chapter_count=0,
                        )

                    title = titles[title_idx]

                    if attr_id == 2:  # Name
                        title.name = value
                    elif attr_id == 9:  # Duration (H:MM:SS)
                        title.duration_seconds = self._parse_duration(value)
                    elif attr_id == 10:  # Size
                        title.size_bytes = self._parse_size(value)
                    elif attr_id == 8:  # Chapter count
                        try:
                            title.chapter_count = int(value)
                        except ValueError:
                            pass
                    elif attr_id == 16:  # Source filename (e.g., "00001.m2ts")
                        title.source_filename = value
                    elif attr_id == 19:  # Video resolution name (e.g., "1920x1080")
                        title.video_resolution = self._parse_resolution(value)
                    elif attr_id == 25:  # Segment count
                        try:
                            title.segment_count = int(value)
                        except ValueError:
                            pass
                    elif attr_id == 26:  # Segment map (e.g., "1,2,3,4,5")
                        title.segment_map = value
                    elif attr_id == 27:  # Suggested output filename (e.g., "Show - S3_t00.mkv")
                        title.disc_title = value
                    elif attr_id == 28:  # Language code - good for filtering later
                        pass

        return list(titles.values()), disc_name

    def _parse_resolution(self, res_str: str) -> str:
        """Parse resolution string to standard label."""
        if not res_str:
            return ""

        # MakeMKV often returns "1920x1080 (16:9)" or just "1920x1080"
        match = re.search(r"(\d+)x(\d+)", res_str)
        if match:
            width = int(match.group(1))
            height = int(match.group(2))

            if width >= 3800 or height >= 2100:
                return "4K"
            if width >= 1900 or height >= 1000:
                return "1080p"
            if width >= 1200 or height >= 700:
                return "720p"
            if height >= 570 or height == 480:
                return "480p"  # DVD
            if height == 576:
                return "576p"  # PAL DVD

        return "Unknown"

    def _parse_duration(self, duration_str: str) -> int:
        """Parse duration string (H:MM:SS) to seconds."""
        parts = duration_str.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0])
        except ValueError:
            return 0

    def _parse_size(self, size_str: str) -> int:
        """Parse size string (e.g., '12.5 GB') to bytes."""
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|B)", size_str, re.IGNORECASE)
        if not match:
            return 0

        value = float(match.group(1))
        unit = match.group(2).upper()

        # MakeMKV reports sizes in decimal SI units (1 GB = 10^9 bytes, not 2^30)
        multipliers = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3}
        return int(value * multipliers.get(unit, 1))
