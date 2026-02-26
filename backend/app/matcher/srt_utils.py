import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import chardet
from loguru import logger


def _is_watermark_block(block_text: str, block_lines: list[str], subtitle_start: float) -> bool:
    """Detect subtitle blocks that are watermarks, ads, or non-dialogue annotations.

    Generically identifies watermark content regardless of source by checking for:
    - URLs or domain-like patterns (e.g., www.tvsubtitles.net, opensubtitles.org)
    - Blocks near timestamp 0:00 with non-dialogue content (ad overlays)
    - Font color/size tags wrapping the entire content (styled ads)
    """
    text_lower = block_text.lower().strip()

    # Check for URLs or domain patterns
    if re.search(r"(?:www\.|https?://|\w+\.(?:com|net|org|io|tv|cc|me))", text_lower):
        return True

    # Check for blocks that are only font/styling tags wrapping a URL or brand name
    stripped = re.sub(r"<[^>]+>", "", text_lower).strip()
    if stripped and re.search(r"(?:www\.|https?://|\w+\.(?:com|net|org|io|tv|cc|me))", stripped):
        return True

    # Very short non-dialogue at start (e.g., "sync by", "subtitles by", "corrected by")
    if subtitle_start < 5.0 and len(stripped.split()) <= 8:
        credit_patterns = [
            "sync",
            "subtitles by",
            "corrected by",
            "ripped by",
            "encoded by",
            "transcript by",
            "timing by",
        ]
        if any(p in stripped for p in credit_patterns):
            return True

    return False


def detect_file_encoding(file_path) -> str:
    """Detect the encoding of a file using chardet.

    Args:
        file_path: Path to the file

    Returns:
        Detected encoding, defaults to 'utf-8' if detection fails
    """
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(min(1024 * 1024, Path(file_path).stat().st_size))
        result = chardet.detect(raw_data)
        encoding = result["encoding"]
        confidence = result["confidence"]

        logger.debug(
            f"Detected encoding {encoding} with {confidence:.2%} confidence for {file_path}"
        )
        return encoding if encoding else "utf-8"
    except Exception as e:
        logger.warning(f"Error detecting encoding for {file_path}: {e}")
        return "utf-8"


@lru_cache(maxsize=100)
def read_file_with_fallback(file_path, encodings=None) -> str:
    """Read a file trying multiple encodings in order of preference.

    Args:
        file_path: Path to the file
        encodings: List of encodings to try, defaults to common subtitle encodings

    Returns:
        File contents

    Raises:
        ValueError: If file cannot be read with any encoding
    """
    if encodings is None:
        detected = detect_file_encoding(file_path)
        encodings = [detected, "utf-8", "latin-1", "cp1252", "iso-8859-1"]

    file_path = Path(file_path)
    errors = []

    for encoding in encodings:
        try:
            with open(file_path, encoding=encoding) as f:
                content = f.read()
            logger.debug(f"Successfully read {file_path} using {encoding} encoding")
            return content
        except UnicodeDecodeError as e:
            errors.append(f"{encoding}: {str(e)}")
            continue

    error_msg = f"Failed to read {file_path} with any encoding. Errors:\n" + "\n".join(errors)
    logger.error(error_msg)
    raise ValueError(error_msg)


class SubtitleReader:
    """Helper class for reading and parsing subtitle files."""

    @staticmethod
    def parse_timestamp(timestamp: str) -> float:
        """Parse SRT timestamp into seconds."""
        hours, minutes, seconds = timestamp.replace(",", ".").split(":")
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    @staticmethod
    def read_srt_file(file_path) -> str:
        """Read an SRT file with robust encoding handling."""
        return read_file_with_fallback(file_path)

    @staticmethod
    def extract_subtitle_chunk(content: str, start_time: float, end_time: float) -> list[str]:
        """Extract subtitle text for a specific time window, filtering watermarks."""
        text_lines = []

        for block in content.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines) < 3 or "-->" not in lines[1]:
                continue

            try:
                timestamp = lines[1]
                time_parts = timestamp.split(" --> ")
                start_stamp = time_parts[0].strip()
                end_stamp = time_parts[1].strip()

                subtitle_start = SubtitleReader.parse_timestamp(start_stamp)
                subtitle_end = SubtitleReader.parse_timestamp(end_stamp)

                if subtitle_end >= start_time and subtitle_start <= end_time:
                    text = " ".join(lines[2:])

                    # Skip watermark/ad blocks (URLs, credit lines, etc.)
                    if _is_watermark_block(text, lines, subtitle_start):
                        logger.debug(
                            f"Filtered watermark/ad block at {subtitle_start:.1f}s: {text[:80]}"
                        )
                        continue

                    text_lines.append(text)

            except (IndexError, ValueError) as e:
                logger.warning(f"Error parsing subtitle block: {e}")
                continue

        return text_lines

    @staticmethod
    def get_duration(content: str) -> float:
        """Get the duration of the subtitle file (max end timestamp across all blocks).

        Uses max() instead of last-block because some subtitle files have
        watermark/ad blocks appended at the end with timestamps near 0:00,
        which would incorrectly report the duration as ~2 seconds.

        Args:
            content: Full SRT file content

        Returns:
            Duration in seconds, or 0 if parsing fails
        """
        try:
            blocks = content.strip().split("\n\n")
            if not blocks:
                return 0.0

            max_end = 0.0
            for block in blocks:
                lines = block.split("\n")
                if len(lines) >= 2 and "-->" in lines[1]:
                    try:
                        time_parts = lines[1].split(" --> ")
                        end_stamp = time_parts[1].strip()
                        end_time = SubtitleReader.parse_timestamp(end_stamp)
                        if end_time > max_end:
                            max_end = end_time
                    except (IndexError, ValueError):
                        continue

            return max_end
        except Exception as e:
            logger.warning(f"Error getting duration from subtitle content: {e}")
            return 0.0


def clean_text(text: str) -> str:
    """Clean and normalize text for matching."""
    text = text.lower().strip()
    text = re.sub(r"\[.*?\]|\<.*?\>", "", text)
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


def extract_season_episode(filename):
    """Extract season and episode numbers from filename with support for multiple formats.

    Args:
        filename: Filename to parse

    Returns:
        tuple: (season_number, episode_number)
    """
    patterns = [
        r"S(\d+)E(\d+)",  # S01E01
        r"(\d+)x(\d+)",  # 1x01 or 01x01
        r"Season\s*(\d+).*?(\d+)",  # Season 1 - 01
    ]

    for pattern in patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))

    return None, None
