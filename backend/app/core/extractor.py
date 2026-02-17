"""Extractor - MakeMKV CLI Wrapper.

Handles disc scanning and extraction using makemkvcon.
"""

import asyncio
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.analyst import TitleInfo
from app.core.errors import MakeMKVError

logger = logging.getLogger(__name__)


@dataclass
class RipProgress:
    """Progress information during ripping."""

    percent: float
    current_title: int
    total_titles: int


# Progress callback type
ProgressCallback = Callable[[RipProgress], None]
TitleCompleteCallback = Callable[[int, Path], None]


@dataclass
class RipResult:
    """Result of a ripping operation."""

    success: bool
    output_files: list[Path]
    error_message: str | None = None





class MakeMKVExtractor:
    """Wrapper for MakeMKV command-line interface."""

    def __init__(self, makemkv_path: Path | None = None) -> None:
        self._makemkv_path_override = makemkv_path
        self._current_process: asyncio.subprocess.Process | None = None
        self._cancelled = False

    @property
    def makemkv_path(self) -> Path:
        """Get MakeMKV path, lazy-loading from DB config if not explicitly set."""
        if self._makemkv_path_override is not None:
            return self._makemkv_path_override
        from app.services.config_service import get_config_sync

        return Path(get_config_sync().makemkv_path)

    async def scan_disc(self, drive: str) -> list[TitleInfo]:
        """Scan a disc and return title information.

        Args:
            drive: Drive letter (e.g., "E:") or disc index (e.g., "disc:0")

        Returns:
            List of titles found on the disc
        """
        # Normalize drive specification
        if not drive.startswith("disc:"):
            # Convert drive letter to disc index
            drive_spec = f"dev:{drive}"
        else:
            drive_spec = drive

        cmd = [
            str(self.makemkv_path),
            "-r",  # Robot mode (machine-readable output)
            "info",
            drive_spec,
        ]

        logger.info(f"Scanning disc: {' '.join(cmd)}")

        def run_makemkv() -> subprocess.CompletedProcess:
            """Run MakeMKV in a thread (Windows asyncio subprocess workaround)."""
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout for scanning
            )

        try:
            # Run in thread to avoid blocking event loop
            result = await asyncio.to_thread(run_makemkv)
            
            logger.debug(f"MakeMKV stdout: {result.stdout[:500] if result.stdout else 'empty'}")
            if result.stderr:
                logger.debug(f"MakeMKV stderr: {result.stderr[:500]}")

            if result.returncode != 0:
                logger.error(f"MakeMKV scan failed (exit code {result.returncode}): {result.stderr}")
                return []

            titles = self._parse_disc_info(result.stdout or "")
            logger.info(f"Found {len(titles)} titles on disc")
            return titles

        except FileNotFoundError:
            logger.error(f"MakeMKV not found at: {self.makemkv_path}")
            return []
        except subprocess.TimeoutExpired:
            logger.error("MakeMKV scan timed out")
            return []
        except Exception as e:
            logger.exception(f"Error scanning disc: {e}")
            return []

    async def rip_titles(
        self,
        drive: str,
        output_dir: Path,
        title_indices: list[int] | None = None,
        progress_callback: ProgressCallback | None = None,
        title_complete_callback: TitleCompleteCallback | None = None,
    ) -> RipResult:
        """Rip selected titles from a disc.

        Args:
            drive: Drive letter or disc specification
            output_dir: Directory to save MKV files
            title_indices: List of title indices to rip, or None for all
            progress_callback: Optional callback for progress updates
            title_complete_callback: Optional callback when a title finishes ripping

        Returns:
            RipResult with success status and output files
        """
        self._cancelled = False
        output_dir.mkdir(parents=True, exist_ok=True)

        # Normalize drive specification
        if not drive.startswith("disc:"):
            drive_spec = f"dev:{drive}"
        else:
            drive_spec = drive

        # Prepare commands to run
        commands = []
        
        if not title_indices:
            # Rip ALL titles (single command, most efficient)
            logger.info("Ripping ALL titles")
            commands.append([
                str(self.makemkv_path),
                "-r",
                "--progress=-same",
                "mkv",
                drive_spec,
                "all",
                str(output_dir),
            ])
            total_titles_count = 0  # Will be updated from PRGC
        elif len(title_indices) == 1:
            # Rip single specific title
            idx = title_indices[0]
            logger.info(f"Ripping single title {idx}")
            commands.append([
                str(self.makemkv_path),
                "-r",
                "--progress=-same",
                "mkv",
                drive_spec,
                str(idx),
                str(output_dir),
            ])
            total_titles_count = 1
        else:
            # Rip multiple specific titles (must loop commands)
            logger.info(f"Ripping {len(title_indices)} specific titles: {title_indices}")
            total_titles_count = len(title_indices)
            for idx in title_indices:
                commands.append([
                    str(self.makemkv_path),
                    "-r",
                    "--progress=-same",
                    "mkv",
                    drive_spec,
                    str(idx),
                    str(output_dir),
                ])

        # State tracking for progress
        current_title_idx = 0 # 0-based absolute index for progress reporting
        
        # Queue for progress updates from thread to async context
        import queue
        progress_queue: queue.Queue[RipProgress] = queue.Queue()
        output_lines: list[str] = []
        output_files: list[Path] = []

        # Shared state for filesystem-based title completion detection.
        import threading

        known_files: dict[str, int] = {}  # filename -> last known size
        completed_files: set[str] = set()
        _fs_lock = threading.Lock()

        def _check_for_completed_files():
            """Scan output dir for newly completed .mkv files."""
            with _fs_lock:
                try:
                    for mkv in output_dir.glob("*.mkv"):
                        fname = mkv.name
                        if fname in completed_files:
                            continue
                        current_size = mkv.stat().st_size
                        if fname in known_files:
                            if current_size == known_files[fname] and current_size > 0:
                                # Size stable between checks = file is complete
                                completed_files.add(fname)
                                filepath = output_dir / fname
                                output_files.append(filepath)
                                logger.info(
                                    f"Title file completed: {fname} "
                                    f"({current_size / 1024 / 1024:.0f} MB)"
                                )
                                if title_complete_callback:
                                    try:
                                        title_complete_callback(
                                            len(completed_files), filepath
                                        )
                                    except Exception as e:
                                        logger.exception(
                                            f"Error in title complete callback: {e}"
                                        )
                        known_files[fname] = current_size
                except (OSError, PermissionError) as e:
                    logger.exception(f"Error checking for completed files: {e}")

        def run_rip_with_streaming() -> tuple[int, str]:
            """Run ripping commands in sequence."""
            nonlocal current_title_idx, total_titles_count

            import time as _time
            
            last_fs_check = _time.monotonic()
            combined_stderr = ""
            final_returncode = 0

            try:
                self._cancelled = False
                
                for cmd in commands:
                    if self._cancelled:
                        break
                        
                    current_title_idx += 1
                    logger.info(f"Executing rip command {current_title_idx}/{len(commands)}: {' '.join(cmd)}")
                    
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,  # Line buffered
                    )
                    self._current_process = process

                    # Read stdout line by line
                    for line in iter(process.stdout.readline, ""):
                        if self._cancelled:
                            process.terminate()
                            break

                        line = line.strip()
                        if not line:
                            continue

                        output_lines.append(line)

                        # Parse progress messages
                        if line.startswith("PRGC:"):
                            match = re.match(r"PRGC:\d+,(\d+),", line)
                            if match:
                                total = int(match.group(1))
                                if total > 0 and len(commands) == 1:
                                    # Update total only for single-command mode (e.g. "all")
                                    # For multi-command loop, we know total from len(title_indices)
                                    total_titles_count = total

                        elif line.startswith("PRGV:"):
                            match = re.match(r"PRGV:\s*(\d+),\s*(\d+),\s*(\d+)", line)
                            if match:
                                current = int(match.group(1))
                                total = int(match.group(2)) # Sub-task total
                                max_val = int(match.group(3))

                                if max_val > 0:
                                    percent = (current / max_val) * 100

                                    report_title_idx = current_title_idx
                                    if len(commands) == 1 and not title_indices:
                                         # "All" mode: use dynamic file count as proxy for title index
                                         report_title_idx = len(completed_files) + 1
                                    
                                    progress = RipProgress(
                                        percent=percent,
                                        current_title=report_title_idx,
                                        total_titles=total_titles_count,
                                    )
                                    progress_queue.put(progress)

                        # Also catch robot-mode MSG lines about file creation
                        if ".mkv" in line and "created" in line:
                            match = re.search(r'["\']([^"\']+\.mkv)["\']', line)
                            if match:
                                filepath = output_dir / Path(match.group(1)).name
                                if filepath.name not in completed_files:
                                    completed_files.add(filepath.name)
                                    output_files.append(filepath)
                                    if title_complete_callback:
                                        try:
                                            title_complete_callback(
                                                len(completed_files), filepath
                                            )
                                        except Exception:
                                            logger.exception(
                                                "Error in title complete callback"
                                            )

                        # Also check filesystem periodically from the thread
                        now = _time.monotonic()
                        if now - last_fs_check >= 3.0:
                            _check_for_completed_files()
                            last_fs_check = now

                    # End of process loop
                    process.wait()
                    if process.returncode != 0:
                         stderr = process.stderr.read() if process.stderr else ""
                         combined_stderr += f"\nCommand failed ({cmd}): {stderr}"
                         final_returncode = process.returncode
                         # If one fails in a loop, should we stop? Yes, probably.
                         if len(commands) > 1:
                              break
                    
                    # Final fs check for this command
                    _check_for_completed_files()

                # End of all commands
                return (final_returncode, combined_stderr)

            except Exception as e:
                logger.exception("Error in rip subprocess")
                try:
                    if self._current_process:
                        self._current_process.terminate()
                except (ProcessLookupError, PermissionError) as e:
                    logger.debug(f"Could not terminate MakeMKV process: {e}")
                    pass
                return (-1, str(e))
            finally:
                self._current_process = None

        try:
            # Start ripping in thread
            import concurrent.futures
            import time as _async_time

            last_async_fs_check = _async_time.monotonic()

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_rip_with_streaming)

                # Poll for progress updates while ripping
                while not future.done():
                    try:
                        # Get progress updates with timeout
                        progress = progress_queue.get(timeout=0.5)
                        if progress_callback:
                            progress_callback(progress)
                    except queue.Empty:
                        pass

                    # Filesystem polling from async context — runs even if
                    # MakeMKV stdout is block-buffered and PRGV lines don't
                    # arrive in the thread.
                    now = _async_time.monotonic()
                    if now - last_async_fs_check >= 3.0:
                        await asyncio.to_thread(_check_for_completed_files)
                        last_async_fs_check = now

                    await asyncio.sleep(0.1)  # Yield to other tasks

                # Final filesystem check after process exits
                await asyncio.to_thread(_check_for_completed_files)

                # Drain remaining progress updates
                while not progress_queue.empty():
                    try:
                        progress = progress_queue.get_nowait()
                        if progress_callback:
                            progress_callback(progress)
                    except queue.Empty:
                        break

                returncode, stderr = future.result()

            logger.debug(f"Rip completed with return code {returncode}")

            if self._cancelled:
                return RipResult(
                    success=False,
                    output_files=[],
                    error_message="Ripping cancelled by user",
                )

            # Fallback: parse output_lines if thread didn't track any files
            if not output_files:
                for line in output_lines:
                    if ".mkv" in line and "created" in line:
                        match = re.search(r'["\']([^"\']+\.mkv)["\']', line)
                        if match:
                            output_files.append(output_dir / Path(match.group(1)).name)

            if returncode != 0:
                return RipResult(
                    success=False,
                    output_files=output_files,
                    error_message=stderr or "Unknown error during ripping",
                )

            # Find all MKV files in output directory if none tracked
            if not output_files:
                output_files = list(output_dir.glob("*.mkv"))

            logger.info(f"Ripping complete: {len(output_files)} files created")
            return RipResult(success=True, output_files=output_files)

        except Exception as e:
            logger.exception("Error during ripping")
            return RipResult(
                success=False,
                output_files=[],
                error_message=str(e),
            )

    def cancel(self) -> None:
        """Cancel the current ripping operation."""
        self._cancelled = True
        if self._current_process:
            try:
                self._current_process.terminate()
            except (ProcessLookupError, PermissionError) as e:
                logger.debug(f"Could not terminate MakeMKV process during cancel: {e}")
                pass

    def _parse_disc_info(self, output: str) -> list[TitleInfo]:
        """Parse MakeMKV robot-mode output to extract title information.

        MakeMKV output format (robot mode):
            TINFO:0,2,0,"Title name"
            TINFO:0,9,0,"1:30:45"  (duration)
            TINFO:0,10,0,"12.5 GB"  (size)
            TINFO:0,8,0,"24"  (chapter count)
        """
        titles: dict[int, TitleInfo] = {}

        for line in output.split("\n"):
            line = line.strip()

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
                    elif attr_id == 19:  # Video resolution name (e.g., "1920x1080")
                        title.video_resolution = self._parse_resolution(value)
                    elif attr_id == 28:  # Language code - good for filtering later
                        pass

        return list(titles.values())

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
                return "480p" # DVD
            if height == 576:
                return "576p" # PAL DVD
                
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

        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        return int(value * multipliers.get(unit, 1))
