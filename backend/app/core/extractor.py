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

# MakeMKV emits MSG:3032 when the drive's region setting does not match the
# inserted disc. It retries internally ("trying to work around...") and can hang
# there indefinitely, so the rip reads as a generic stall. Detecting the code
# lets us name the real cause instead of blaming the disc.
REGION_MISMATCH_FAILURE_REASON = (
    "Ripping stalled: the drive's region setting does not match this disc's "
    "region, so MakeMKV could not open the disc. Set the drive's region to match "
    "the disc, or use a region-free drive."
)

# Consecutive stable-size polls required before declaring in-flight title completion.
# Each poll is ~3 s apart, so STABLE_CHECKS_REQUIRED=3 means ~9 s of write-silence.
# Post-process force checks bypass this requirement (process exit guarantees write done).
STABLE_CHECKS_REQUIRED = 3

# Consecutive stalled commands, with nothing written by any of them, after which
# a rip gives up instead of re-opening the disc once per remaining title. A disc
# that has failed this many times in a row at disc-open is not going to succeed
# on the next title, and each retry costs a full ripping_stall_timeout.
# Abandoning is safe because every skipped title still routes to REVIEW as
# re-rippable (see route_rip_failure_to_review).
ZERO_OUTPUT_STALL_LIMIT = 2

# Seconds between stall-watchdog polls. A module constant so tests can shorten it;
# production behaviour is unchanged at 5 s.
STALL_POLL_INTERVAL = 5.0


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


def _should_abandon_zero_output_rip(stall_count: int, completed_outputs: int) -> bool:
    """Whether to stop issuing rip commands because the disc is unreadable.

    True only when this invocation has stalled ``ZERO_OUTPUT_STALL_LIMIT`` times
    **and** produced no completed output at all. Requiring zero output is what
    keeps the "one bad title, rest of the disc fine" case working: as soon as a
    single file lands, the rip is partially succeeding and every remaining title
    is still worth attempting.
    """
    if completed_outputs > 0:
        return False
    return stall_count >= ZERO_OUTPUT_STALL_LIMIT


def _extract_created_mkv(line: str, output_dir: Path) -> Path | None:
    """Extract the created .mkv path from a MakeMKV output line, if present."""
    if ".mkv" not in line or "created" not in line:
        return None
    match = _CREATED_MKV_PATTERN.search(line)
    if not match:
        return None
    return output_dir / Path(match.group(1)).name


def _is_region_mismatch(line: str) -> bool:
    """Whether *line* is MakeMKV's MSG:3032 region-mismatch warning.

    Robot mode emits ``MSG:3032,0,2,"Region setting of drive ..."``. The trailing
    comma in the prefix keeps this from matching unrelated codes that merely
    contain the digits (e.g. ``MSG:13032``).
    """
    return line.startswith("MSG:3032,")


def title_index_from_filename(name: str) -> int | None:
    """Parse MakeMKV's disc-native title number out of an output filename, or None.

    MakeMKV names each output ``{label}_t{NN}.mkv`` (or ``title_NN.mkv``) where
    ``NN`` is MakeMKV's own disc-native title number. This is USUALLY the same
    as the scan-order ``DiscTitle.title_index``, but not guaranteed — some
    discs number titles starting at 1 (no "t00") or with gaps (issue #517).
    Callers that need to map a ripped filename back to a specific
    ``DiscTitle`` row should prefer matching against ``DiscTitle.output_index``
    (the native number recorded at scan time), falling back to ``title_index``
    only for legacy rows without it — see
    ``app.services.ripping_helpers.expected_native_index``, which most
    resolution sites call directly. ``finalization_coordinator._resolve_source_file``
    applies the same precedence inline (it works over a DB-free dict snapshot,
    not a title object, so it can't call the helper directly).

    ``_files_to_ignore`` is the one real gap: it has no DB access to consult
    ``output_index``, so on an offset-numbered disc it can disagree with
    ``resolve_title_from_filename`` whenever ``rip_titles`` is called with a
    real subset of titles — the manual single-track re-rip path
    (``job_manager.rerip_titles``), and also the automatic one-pass-stalled
    fallback that re-rips individually-missing titles after a failed 'all'
    pass. In practice this is bounded: a stale sibling file typically earns
    ``TitleCompletionDetector``'s deletion-protection within
    ``STABLE_CHECKS_REQUIRED`` polls (~9s), well inside the default stall
    timeout, so the risk is redundant reprocessing rather than data loss. This
    is a documented, narrow gap (see issue #517's fix plan, Follow-up
    section), not an oversight.
    """
    m = re.search(r"t(\d+)\.mkv$", name, re.IGNORECASE)
    if not m:
        m = re.search(r"title[_]?(\d+)\.mkv$", name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _files_to_ignore(output_dir: Path, title_indices: list[int] | None) -> set[str]:
    """``*.mkv`` files already in *output_dir* that this rip must not touch.

    A single-title re-rip writes into a staging dir that still holds the disc's
    other, already-finished titles. A fresh ``TitleCompletionDetector`` has no
    memory of those files, so without this it would (a) re-report them as freshly
    "completed" — mis-attributing them to the title being re-ripped — and (b) on
    a stall, delete them as "incomplete", wiping good episodes. Return the
    pre-existing files that belong to a title we are *not* (re-)ripping.

    A leftover partial of a title we ARE ripping is deliberately excluded (it
    will be overwritten and re-detected normally). When ``title_indices`` is
    falsy (None for a full ``rip all``, or an empty list) nothing is ignored —
    the dir is fresh.
    """
    if not title_indices:  # None → full "rip all"; [] → same (no titles to re-rip)
        return set()
    ripping = set(title_indices)
    ignore: set[str] = set()
    for p in output_dir.glob("*.mkv"):
        idx = title_index_from_filename(p.name)
        if idx is None or idx not in ripping:
            ignore.add(p.name)
    return ignore


def _build_rip_commands(
    makemkv_path: str,
    drive_spec: str,
    output_dir: str,
    title_indices: list[int] | None,
) -> list[tuple[int | None, list[str]]]:
    """Build ``(title_index, argv)`` rip commands.

    ``title_index`` is None for the single full-disc "all" pass (which rips
    every title in one MakeMKV invocation and cannot drop an individual title);
    otherwise each command carries the specific title index so the rip loop can
    consult the live skip-set before starting it.
    """
    base = [makemkv_path, "-r", "--progress=-same", "mkv", drive_spec]
    if not title_indices:
        return [(None, [*base, "all", output_dir])]
    return [(idx, [*base, str(idx), output_dir]) for idx in title_indices]


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
    # Specific reason for a stall, when one is known (e.g. a region mismatch).
    # None means the generic STALL_FAILURE_REASON applies. Callers routing
    # stalled titles to review read this so the live update and History agree.
    failure_reason: str | None = None
    # True when a full-disc "all" pass was terminated early because the user
    # skipped a title mid-rip (a single MakeMKV invocation cannot drop one
    # title). Not a failure: titles ripped before the abort are kept, and the
    # caller re-rips the remaining not-skipped titles individually — where the
    # live skip-set IS honored (issue #538).
    aborted_for_skip: bool = False


class ScanTimeoutError(Exception):
    """MakeMKV disc scan exceeded time limit."""


class TitleCompletionDetector:
    """Decides, from output-file sizes alone, when MakeMKV has finished a title.

    MakeMKV writes one title at a time and emits no reliable per-title "done"
    signal on stdout (its PRGC/PRGV codes carry no title index), so completion
    is inferred from the ``*.mkv`` files in the output directory. The detector
    is fed a ``{filename: size}`` snapshot on each poll and reports which files
    have *newly* finished.

    A file is reported complete only when MakeMKV has **demonstrably moved on**:

    * ``force`` — the process exited, so every non-empty file is final; or
    * its size has been stable for ``stable_required`` polls **and a different
      file is now the one growing** (MakeMKV started the next title).

    Size-stability *alone* is deliberately not trusted. MakeMKV pauses writes
    mid-title on slow or dirty discs, holding a file's size constant for many
    seconds. Treating such a pause as completion hands a still-ripping file to
    the matcher; the filesystem progress monitor then keeps re-broadcasting the
    same title as RIPPING when growth resumes, so the job UI flickers between
    the red RIPPING state and the green matched/idle state for the rest of the
    rip (issue #381). Because a paused title is always the *most-recently-grown*
    file, the "another file is growing" gate makes that false positive
    impossible: the last/only title of a rip is finalized by the post-process
    ``force`` check instead.

    Not internally synchronized — callers serialize access (the rip loop holds
    its filesystem lock around every ``poll``/``seed``).
    """

    def __init__(
        self,
        stable_required: int = STABLE_CHECKS_REQUIRED,
        *,
        ignore: set[str] | None = None,
    ) -> None:
        self._stable_required = stable_required
        self._known: dict[str, int] = {}  # filename -> last polled size
        self._stable_counts: dict[str, int] = {}  # consecutive unchanged polls
        self._completed: set[str] = set()
        self._active: str | None = None  # most-recently-growing file
        # Files that predate this rip (a subset re-rip into a populated staging
        # dir). Never reported complete, never counted, never deleted on stall.
        self._ignored: set[str] = set(ignore or ())

    def seed(self, fname: str) -> None:
        """Record a file MakeMKV announced as 'created' (no bytes written yet)."""
        self._known.setdefault(fname, 0)

    def is_known(self, fname: str) -> bool:
        """Whether *fname* has been seen (seeded or polled) before."""
        return fname in self._known

    def is_completed(self, fname: str) -> bool:
        """Whether *fname* has already been reported complete."""
        return fname in self._completed

    def should_preserve(self, fname: str) -> bool:
        """Whether *fname* must NOT be deleted as 'incomplete' on a stall.

        True for a title this rip completed *and* for a pre-existing (ignored)
        file from a prior rip — the latter is another title's good episode that
        a re-rip must never delete.
        """
        return fname in self._completed or fname in self._ignored

    @property
    def completed_count(self) -> int:
        """Total number of titles reported complete so far."""
        return len(self._completed)

    def files_incomplete_at_abort(self, sizes: dict[str, int]) -> list[str]:
        """Files to delete when the rip process was killed mid-pass.

        Used only on an abort (e.g. a skip that terminates the full-disc "all"
        pass): the ``force`` promotion path is unsafe here because the process
        did not exit naturally, so the file MakeMKV was actively writing is
        genuinely truncated. MakeMKV writes titles sequentially, so at a kill at
        most one file is partial: the one whose size **grew since the last poll**
        (or a zero-byte stub, or one never polled at all). A title that already
        finished has a size equal to its last-polled value — even if its
        "a later title started growing" completion gate never fired because it
        was the most recent title when the kill landed — and MUST be preserved
        rather than needlessly re-ripped. ``_completed`` and pre-existing
        (``_ignored``) files are always preserved.

        Reading ``_active`` instead would be wrong: it is the most-recently-grown
        file and is never cleared, so between one title finishing and the next
        starting it points at the already-finished title.
        """
        doomed: list[str] = []
        for fname, size in sizes.items():
            if fname in self._completed or fname in self._ignored:
                continue
            prev = self._known.get(fname)
            # size<=0: never-valid stub. prev is None: never polled — in a
            # sequential rip that is the just-opened current title. size>prev:
            # still growing when killed. Otherwise (size == prev > 0) the title
            # had stopped writing, i.e. it is finished.
            if size <= 0 or prev is None or size > prev:
                doomed.append(fname)
        return doomed

    def poll(self, sizes: dict[str, int], *, force: bool = False) -> list[tuple[str, int]]:
        """Feed the current ``{filename: size}`` snapshot.

        Returns ``(filename, ordinal)`` for each file that finished writing on
        this poll, in the order the snapshot iterates them. ``ordinal`` is the
        1-based count of titles completed so far *at the moment that file was
        marked done* — so when a single ``force`` poll finalizes several files
        at once, each gets a distinct sequential ordinal rather than all sharing
        the final total (the value the rip loop passes through as the callback's
        sequential index). Updates the detector's internal tracking state in
        place.
        """
        # Pass 1: advance the "active" pointer to whatever grew this poll, so a
        # completion decision in pass 2 sees an up-to-date view of which file
        # MakeMKV is currently writing. A file that just appeared with bytes is
        # also "active" — MakeMKV opened the next title and started writing it
        # (this is what supersedes the previous title). A growing/new file
        # resets its own stability counter.
        for fname, size in sizes.items():
            if size <= 0 or fname in self._completed or fname in self._ignored:
                continue
            prev = self._known.get(fname)
            if prev is None or size > prev:
                self._active = fname
                self._stable_counts[fname] = 0

        # Pass 2: decide completion, then record the new sizes.
        newly: list[tuple[str, int]] = []
        for fname, size in sizes.items():
            if fname in self._ignored:
                continue
            prev = self._known.get(fname)
            self._known[fname] = size
            if fname in self._completed or size <= 0 or prev is None:
                continue
            if force:
                # Process exited: the write is guaranteed finished.
                self._mark_complete(fname)
                newly.append((fname, len(self._completed)))
            elif size == prev:
                self._stable_counts[fname] = self._stable_counts.get(fname, 0) + 1
                # Complete only once MakeMKV has provably moved on to another
                # title — a stable size while THIS file is still the active one
                # is just a mid-rip write pause, not completion.
                if (
                    self._stable_counts[fname] >= self._stable_required
                    and self._active is not None
                    and self._active != fname
                ):
                    self._mark_complete(fname)
                    newly.append((fname, len(self._completed)))
            # A shrinking size (rare) is ignored beyond updating _known above.
        return newly

    def _mark_complete(self, fname: str) -> None:
        self._completed.add(fname)
        self._stable_counts.pop(fname, None)


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
        # Per-job set of title_index values to skip. Checked before each
        # per-title rip command so a queued-but-not-yet-ripped title can be
        # dropped mid-rip. A full-disc "all" pass cannot honor this (one process
        # rips everything); those skips are handled downstream by deleting the
        # finished file. Single-writer per job under the rip thread + async
        # skip calls; set mutation is atomic under the GIL.
        self._skipped_indices: dict[int, set[int]] = {}
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
        commands = _build_rip_commands(
            str(self.makemkv_path),
            drive_spec,
            str(output_dir),
            title_indices,
        )
        if not title_indices:
            logger.info("Ripping ALL titles")
        else:
            logger.info(f"Ripping {len(title_indices)} specific title(s): {title_indices}")

        # State tracking for progress
        current_title_idx = 0  # 0-based absolute index for progress reporting

        output_lines: list[str] = []
        output_files: list[Path] = []

        # Filesystem-based title completion detection. The detector reports a
        # title complete only when MakeMKV has provably moved on (a later file
        # is growing, or the process exited via force) — a stable size alone is
        # a mid-rip write pause, not completion (issue #381). See
        # ``TitleCompletionDetector``.
        #
        # A single-title re-rip writes into a staging dir that still holds the
        # disc's other, already-finished titles. Ignore those pre-existing files
        # so the detector neither re-reports them as fresh completions (which
        # mis-attributes them to the title being re-ripped) nor deletes them on
        # a stall.
        completion = TitleCompletionDetector(
            STABLE_CHECKS_REQUIRED, ignore=_files_to_ignore(output_dir, title_indices)
        )
        _fs_lock = threading.Lock()

        # Set when MakeMKV reports a region mismatch (MSG:3032). Single-element
        # list so the watchdog thread sees writes from the reader loop, matching
        # the last_progress pattern below. Declared out here (not inside
        # run_rip_with_streaming) because the RipResult returns below also read it.
        region_mismatch = [False]

        # Set by the reader loop when a skip lands during a full-disc "all" pass
        # (which cannot drop one title from its single MakeMKV invocation). The
        # RipResult returns below read it, so — like region_mismatch — it lives
        # out here rather than inside run_rip_with_streaming.
        aborted_for_skip = [False]

        def _stall_reason() -> str:
            """The most specific reason we can give for a stall."""
            return REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else STALL_FAILURE_REASON

        def _fire_title_complete(fname: str, size: int, ordinal: int) -> None:
            """Record output file and invoke the title callback (lock held).

            ``ordinal`` is this file's 1-based completion index, used by the
            callback's sequential title-resolution fallback. It comes per-file
            from the detector so a multi-file ``force`` batch yields 1, 2, …, N
            rather than every callback seeing the final total.
            """
            filepath = output_dir / fname
            output_files.append(filepath)
            logger.info(f"Title file completed: {fname} ({size / 1024 / 1024:.0f} MB)")
            if title_complete_callback:
                _safe_callback(
                    title_complete_callback,
                    ordinal,
                    filepath,
                    label="title complete callback",
                )

        def _check_for_completed_files(force: bool = False) -> None:
            """Scan output dir and fire the callback for newly completed files.

            In-flight (``force=False``): a file must be stable for
            *STABLE_CHECKS_REQUIRED* consecutive polls **and** superseded by a
            later growing title before it is declared complete — so a brief
            MakeMKV write pause mid-title can't be mistaken for completion.

            Post-process (``force=True``): fires immediately for any non-zero
            file not yet complete. Called after ``process.wait()`` returns so
            the write is guaranteed finished (this finalizes the last/only
            title, which has no successor to supersede it in-flight).
            """
            with _fs_lock:
                try:
                    current_sizes: dict[str, int] = {}
                    for mkv in output_dir.glob("*.mkv"):
                        try:
                            current_sizes[mkv.name] = mkv.stat().st_size
                        except OSError as e:
                            logger.debug(f"Could not stat {mkv.name}: {e}")
                    for fname, ordinal in completion.poll(current_sizes, force=force):
                        _fire_title_complete(fname, current_sizes[fname], ordinal)
                except OSError as e:
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

                def _scan_sizes() -> dict[str, int]:
                    sizes: dict[str, int] = {}
                    try:
                        for mkv in watch_dir.glob("*.mkv"):
                            try:
                                sizes[mkv.name] = mkv.stat().st_size
                            except OSError:
                                pass  # transient stat race — skip this file
                    except OSError:
                        pass  # dir unreadable this tick — return what we have
                    return sizes

                # Seed with the files already present so a re-rip's pre-existing
                # titles (a subset re-rip writes into a populated staging dir)
                # don't register as first-poll "growth" from 0 → real size, which
                # would briefly reset the stall clock and delay detection.
                prev_sizes: dict[str, int] = _scan_sizes()

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
                                _stall_reason(),
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

                for title_index, cmd in commands:
                    if job_id in self._cancelled_jobs:
                        break

                    current_title_idx += 1

                    # Live skip: a queued title the user skipped before MakeMKV
                    # reached it is dropped here. (The "all" pass has
                    # title_index None and cannot be skipped this way; those are
                    # handled by deleting the finished file downstream.)
                    if title_index is not None and title_index in self._skipped_indices.get(
                        job_id, set()
                    ):
                        logger.info(
                            f"Skipping title {title_index} (command "
                            f"{current_title_idx}/{len(commands)}) - user-skipped"
                        )
                        continue

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
                            args=(process, output_dir, stall_timeout, STALL_POLL_INTERVAL),
                            daemon=True,
                        )
                        watchdog_thread.start()

                    # Read stdout line by line
                    for line in iter(process.stdout.readline, ""):
                        if job_id in self._cancelled_jobs:
                            process.terminate()
                            break

                        # All-pass skip (issue #538): a single "mkv … all"
                        # invocation cannot be told to drop one title, so a skip
                        # requested mid-pass is honored by aborting here. The
                        # titles finished before the abort are kept; the caller
                        # re-rips the remaining not-skipped titles per-title,
                        # where the live skip-set IS consulted. (Per-title
                        # commands carry a real title_index and skip themselves
                        # before starting, so this only fires for the all-pass.)
                        if title_index is None and self._skipped_indices.get(job_id):
                            logger.info(
                                f"Skip requested during full-disc 'all' pass "
                                f"(job {job_id}); aborting to re-rip the remaining "
                                f"titles individually"
                            )
                            aborted_for_skip[0] = True
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

                        # A region mismatch makes MakeMKV retry disc-open forever;
                        # remember it so the stall is reported with its real cause.
                        if _is_region_mismatch(line):
                            region_mismatch[0] = True

                        # Catch robot-mode MSG lines about file creation
                        filepath = _extract_created_mkv(line, output_dir)
                        if filepath is not None:
                            # Track the file for stable-size detection but do NOT
                            # fire title_complete_callback — the file was just created,
                            # not finished writing. Let _check_for_completed_files
                            # detect true completion via stable file size.
                            with _fs_lock:
                                if not completion.is_known(filepath.name):
                                    completion.seed(filepath.name)
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

                    if aborted_for_skip[0]:
                        # The 'all' pass was terminated to honor a mid-rip skip.
                        # Delete ONLY the title MakeMKV was actively writing when
                        # killed (identified by growth since the last poll) so it
                        # is never handed to matching; titles that already finished
                        # are kept so the caller doesn't needlessly re-rip them.
                        # The post-process force poll below then finalizes those
                        # kept titles. Snapshot under _fs_lock to stay consistent
                        # with the concurrent completion polls.
                        with _fs_lock:
                            abort_sizes: dict[str, int] = {}
                            for mkv in output_dir.glob("*.mkv"):
                                try:
                                    abort_sizes[mkv.name] = mkv.stat().st_size
                                except OSError as e:
                                    logger.debug(f"Could not stat {mkv.name} on abort: {e}")
                            doomed = completion.files_incomplete_at_abort(abort_sizes)
                        for fname in doomed:
                            try:
                                (output_dir / fname).unlink()
                                logger.info(
                                    f"Deleted partial file from skip-aborted 'all' pass: {fname}"
                                )
                            except OSError as e:
                                logger.warning(f"Failed to delete partial file {fname}: {e}")
                        break

                    if process.returncode != 0:
                        was_stall = current_title_idx in stalled_commands
                        stderr = process.stderr.read() if process.stderr else ""

                        if was_stall:
                            # Stall detected — delete incomplete file, continue
                            logger.warning(
                                f"Command {current_title_idx}/{len(commands)} "
                                f"terminated due to stall. Skipping to next title."
                            )
                            # Delete incomplete .mkv files created by this command.
                            # should_preserve also shields pre-existing files from
                            # a prior rip (another title's good episode in the
                            # staging dir during a single-title re-rip).
                            for mkv in output_dir.glob("*.mkv"):
                                if not completion.should_preserve(mkv.name):
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
                            # Give up rather than re-opening a disc that has
                            # already failed at disc-open this many times with
                            # nothing written. Each retry costs a full
                            # stall_timeout, which is what made a region-locked
                            # disc take ~26 minutes to resolve (issue #506).
                            if _should_abandon_zero_output_rip(
                                len(stalled_commands), len(output_files)
                            ):
                                remaining = list(range(current_title_idx + 1, len(commands) + 1))
                                if remaining:
                                    logger.warning(
                                        f"Abandoning rip after {len(stalled_commands)} "
                                        f"stalled command(s) with no output. Skipping "
                                        f"{len(remaining)} remaining command(s)."
                                    )
                                # Report the untried commands as stalled too, so every
                                # title still reaches review instead of being stranded.
                                for skipped_idx in remaining:
                                    stalled_commands.add(skipped_idx)
                                    if title_error_callback:
                                        _safe_callback(
                                            title_error_callback,
                                            skipped_idx,
                                            _stall_reason(),
                                            label="title_error_callback",
                                        )
                                break

                            # Don't break — continue to next command
                            continue

                        combined_stderr += f"\nCommand failed ({cmd}): {stderr}"
                        final_returncode = process.returncode
                        # If one fails in a loop, should we stop? Yes, probably.
                        if len(commands) > 1:
                            break

                    # Final fs check for this command.  process.wait() has
                    # returned (success *or* error exit) so MakeMKV is no longer
                    # writing; bypass the stability counter.
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
                self._skipped_indices.pop(job_id, None)

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

            if aborted_for_skip[0]:
                # Not a failure: the titles finished before the abort are kept,
                # and the caller's one-pass + per-title fallback re-rips the
                # still-missing (not-skipped) titles individually.
                if not output_files:
                    output_files = list(output_dir.glob("*.mkv"))
                logger.info(
                    f"'all' pass aborted for a user skip: {len(output_files)} "
                    f"title(s) already ripped; remaining titles re-ripped per-title"
                )
                return RipResult(
                    success=True,
                    output_files=output_files,
                    aborted_for_skip=True,
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
                    failure_reason=REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else None,
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
                failure_reason=REGION_MISMATCH_FAILURE_REASON if region_mismatch[0] else None,
            )

        except Exception as e:
            logger.exception("Error during ripping")
            return RipResult(
                success=False,
                output_files=[],
                error_message=str(e),
            )

    def skip_title_index(self, job_id: int, title_index: int) -> None:
        """Register a title_index to skip in the per-title rip loop for a job."""
        self._skipped_indices.setdefault(job_id, set()).add(title_index)

    def unskip_title_index(self, job_id: int, title_index: int) -> None:
        """Remove a previously-registered skip (no-op if absent)."""
        s = self._skipped_indices.get(job_id)
        if s:
            s.discard(title_index)

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
        self._skipped_indices.clear()

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
