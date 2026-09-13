import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chardet
from loguru import logger


def detect_file_encoding(file_path: Path) -> str:
    """Detect the encoding of a file using chardet."""
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(min(1024 * 1024, file_path.stat().st_size))
        result = chardet.detect(raw_data)
        encoding = result["encoding"]
        return encoding if encoding else "utf-8"
    except Exception as e:
        logger.warning(f"Error detecting encoding for {file_path}: {e}")
        return "utf-8"


def read_file_with_fallback(file_path: Path, encodings: list[str] | None = None) -> str:
    """Read a file trying multiple encodings."""
    if encodings is None:
        detected = detect_file_encoding(file_path)
        encodings = [detected, "utf-8", "latin-1", "cp1252", "iso-8859-1"]

    errors = []
    for encoding in encodings:
        try:
            with open(file_path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError as e:
            errors.append(f"{encoding}: {str(e)}")
            continue

    raise ValueError(f"Failed to read {file_path} with any encoding. Errors: {errors}")


_STAMP = r"\d{1,3}:\d{1,3}:\d{1,3}(?:[,.]\d{1,3})?"
_TIMING_LINE_RE = re.compile(rf"^({_STAMP})\s*-->\s*({_STAMP})")
_TIMING_LIKE_RE = re.compile(r"^\d{1,3}:\d{1,3}")


def parse_srt_timestamp(timestamp: str) -> float:
    """Parse ``HH:MM:SS,mmm`` (comma or dot before the milliseconds) into seconds."""
    hours, minutes, seconds = timestamp.strip().replace(",", ".").split(":")
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


@dataclass(frozen=True)
class SrtCue:
    """One subtitle cue: its timing and its non-blank text lines."""

    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def _intra_cue_gap(lines: list[str]) -> int:
    """The number of blank lines separating a timing line from its own text in this file.

    0 in a normal SRT, 1 when every line ending was doubled (CRLF text written
    through a Windows text-mode write), 2 when tripled. Taken as the most common
    gap across cues, ties going to the wider gap: a gap that is too narrow empties
    every cue in the file, while one that is too wide only lets a timing-less stray
    line join the cue before it. Measured only from timing lines to the text that
    follows them, so a header or trailer cannot change it. Cues with no text, and
    cues whose first text line is only a number, are not measured.
    """
    counts: Counter[int] = Counter()
    for i, line in enumerate(lines):
        if not _TIMING_LINE_RE.match(line):
            continue
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j == len(lines):
            continue
        following = lines[j]
        if following.isdigit() or ("-->" in following and _TIMING_LIKE_RE.match(following)):
            continue
        counts[j - i - 1] += 1
    if not counts:
        return 0
    return max(counts, key=lambda gap: (counts[gap], gap))


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Group stripped lines into cue blocks.

    A run of blank lines longer than the file's intra-cue gap (see
    ``_intra_cue_gap``) separates cues, so doubled or tripled line endings read
    like a normal file. In a doubled file, a trailer written with single line
    endings directly after the last cue joins that cue's text.
    """
    max_gap = _intra_cue_gap(lines)
    blocks: list[list[str]] = []
    current: list[str] = []
    gap = 0
    for line in lines:
        if not line:
            gap += 1
            continue
        if current and gap > max_gap:
            blocks.append(current)
            current = []
        gap = 0
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def iter_srt_cues(content: str | None) -> Iterator[SrtCue]:
    """Yield every cue in SRT text, tolerating the layouts real downloads arrive in.

    Handles LF, CRLF, lone CR, doubled or tripled line endings, a leading BOM,
    missing cue index lines, and whitespace-only separator lines. Within a block,
    lines before the first timing line are ignored (the index, or stray text) and
    lines after it are the cue's text. A block with no timing line is ignored, so in
    a normally spaced file a blank line inside a cue's text ends that cue. A blank
    line between a timing line and its text, in a file whose other cues have none,
    empties that cue. A further timing line inside the same block (a missing blank
    separator) starts another cue, and a purely numeric line directly before it is
    dropped as that cue's index. A line that starts like a timestamp and contains
    ``-->`` but is not a valid timing line drops that cue only. Milliseconds
    shorter than three digits are read as a decimal fraction (``01,5`` is 1.5
    seconds).
    """
    if not content:
        return
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]
    for block in _split_blocks(lines):
        timing: tuple[float, float] | None = None
        body: list[str] = []
        for line in block:
            match = _TIMING_LINE_RE.match(line)
            if match is None and not ("-->" in line and _TIMING_LIKE_RE.match(line)):
                if timing is not None:
                    body.append(line)
                continue
            if timing is not None:
                if body and body[-1].isdigit():
                    body.pop()  # the index line of the cue that starts here
                yield SrtCue(timing[0], timing[1], tuple(body))
            timing, body = None, []
            if match is not None:
                try:
                    timing = (
                        parse_srt_timestamp(match.group(1)),
                        parse_srt_timestamp(match.group(2)),
                    )
                except ValueError:
                    timing = None
        if timing is not None:
            yield SrtCue(timing[0], timing[1], tuple(body))


def has_srt_cues(content: str | None) -> bool:
    """True when at least one cue in ``content`` carries text."""
    return any(cue.lines for cue in iter_srt_cues(content))


class SubtitleReader:
    """Helper class for reading and parsing subtitle files."""

    @staticmethod
    def parse_timestamp(timestamp: str) -> float:
        """Parse SRT timestamp into seconds."""
        hours, minutes, seconds = timestamp.replace(",", ".").split(":")
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    @staticmethod
    def read_srt_file(file_path: Path) -> str:
        return read_file_with_fallback(file_path)

    @staticmethod
    def extract_subtitle_chunk(content: str, start_time: float, end_time: float) -> list[str]:
        """Extract subtitle text for a specific time window."""
        return [
            cue.text
            for cue in iter_srt_cues(content)
            if cue.lines and cue.end >= start_time and cue.start <= end_time
        ]


def clean_text(text: str) -> str:
    """Clean and normalize text for matching."""
    text = text.lower().strip()
    text = re.sub(r"[\[{][^\]}]*[\]}]|<.*?>", "", text)  # tolerate mismatched [/{ ]/} delimiters
    text = re.sub(r"([A-Za-z])-\1+", r"\1", text)
    return " ".join(text.split())


@lru_cache(maxsize=2)
def _find_executable(name: str) -> str:
    """Find executable in PATH, with caching."""
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"{name} not found in PATH. Please ensure FFmpeg is installed and accessible.\n"
            f"Windows: Add FFmpeg to your system PATH environment variable\n"
            f"Linux/macOS: Install via package manager (apt, brew, etc.)"
        )
    return path


def get_ffprobe_path() -> str:
    """Get path to ffprobe executable."""
    return _find_executable("ffprobe")


def get_ffmpeg_path() -> str:
    """Get path to ffmpeg executable."""
    return _find_executable("ffmpeg")


def get_video_duration(video_file: Path) -> float:
    """Get video duration using ffprobe."""
    try:
        ffprobe = get_ffprobe_path()
        video_path = os.fspath(video_file)
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]

        logger.debug(f"Running ffprobe command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            logger.error(f"ffprobe failed for {video_file}: {result.stderr}")
            raise RuntimeError(f"ffprobe error: {result.stderr}")

        return float(result.stdout.strip())
    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timeout for {video_file}")
        return 0.0
    except FileNotFoundError as e:
        logger.error(str(e))
        return 0.0
    except Exception as e:
        logger.error(f"Failed to get duration for {video_file}: {e}")
        return 0.0


def extract_audio_chunk(
    video_file: Path, start_time: float, duration: float, output_path: Path
) -> Path:
    """Extract audio chunk using ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    video_path = os.fspath(video_file)
    output_file_path = os.fspath(output_path)

    cmd = [
        ffmpeg,
        "-ss",
        str(start_time),
        "-t",
        str(duration),
        "-i",
        video_path,
        "-vn",
        "-sn",
        "-dn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-y",
        output_file_path,
    ]

    logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError("Output file too small or missing")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed for {video_file}: {e.stderr}")
        raise
    except FileNotFoundError as e:
        logger.error(str(e))
        raise
    except Exception as e:
        logger.error(f"Extraction failed for {video_file}: {e}")
        raise
