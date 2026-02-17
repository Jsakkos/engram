"""Validation endpoints for pre-flight checks."""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class ValidationRequest(BaseModel):
    """Request model for validation endpoints."""

    path: str


class ValidationResponse(BaseModel):
    """Response model for validation endpoints."""

    valid: bool
    error: str | None = None
    version: str | None = None
    path: str | None = None


class ToolDetectionResult(BaseModel):
    """Detection result for a single tool."""

    found: bool
    path: str | None = None
    version: str | None = None
    error: str | None = None


class DetectToolsResponse(BaseModel):
    """Response for the detect-tools endpoint."""

    makemkv: ToolDetectionResult
    ffmpeg: ToolDetectionResult
    platform: str


def _get_makemkv_search_paths() -> list[str]:
    """Return platform-specific common MakeMKV installation paths."""
    if sys.platform == "win32":
        return [
            r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
            r"C:\Program Files\MakeMKV\makemkvcon64.exe",
            r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",
            r"C:\Program Files\MakeMKV\makemkvcon.exe",
        ]
    return [
        "/usr/bin/makemkvcon",
        "/usr/local/bin/makemkvcon",
        "/snap/bin/makemkvcon",
        "/var/lib/flatpak/exports/bin/com.makemkv.MakeMKV",
    ]


def _get_ffmpeg_search_paths() -> list[str]:
    """Return platform-specific common FFmpeg installation paths."""
    if sys.platform == "win32":
        return [
            r"C:\tools\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        ]
    return [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]


def _validate_makemkv_binary(path_str: str) -> ToolDetectionResult:
    """Validate a MakeMKV binary and extract version info."""
    try:
        result = subprocess.run(
            [path_str],
            capture_output=True,
            timeout=10,
            text=True,
        )
        output = result.stdout + result.stderr
        if "makemkvcon" not in output.lower() and "makemkv" not in output.lower():
            return ToolDetectionResult(found=False, error="Not a valid MakeMKV executable")

        version = "MakeMKV (version not detectable)"
        for line in output.split("\n"):
            if "version" in line.lower() or "v1." in line or "v2." in line:
                version = line.strip()
                break

        return ToolDetectionResult(found=True, path=path_str, version=version)
    except subprocess.TimeoutExpired:
        return ToolDetectionResult(found=False, path=path_str, error="Command timeout (10s)")
    except Exception as e:
        return ToolDetectionResult(found=False, error=f"Execution failed: {e}")


def _validate_ffmpeg_binary(path_str: str) -> ToolDetectionResult:
    """Validate an FFmpeg binary and extract version info."""
    try:
        result = subprocess.run(
            [path_str, "-version"],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode != 0:
            return ToolDetectionResult(found=False, path=path_str, error="Non-zero exit code")

        version_line = result.stdout.split("\n")[0] if result.stdout else "Unknown"
        return ToolDetectionResult(found=True, path=path_str, version=version_line)
    except subprocess.TimeoutExpired:
        return ToolDetectionResult(found=False, path=path_str, error="Command timeout (10s)")
    except Exception as e:
        return ToolDetectionResult(found=False, error=f"Execution failed: {e}")


def detect_makemkv() -> ToolDetectionResult:
    """Auto-detect MakeMKV by searching PATH then common install locations."""
    # 1. Check system PATH
    for name in ("makemkvcon64", "makemkvcon"):
        found = shutil.which(name)
        if found:
            logger.info(f"Found MakeMKV on PATH: {found}")
            result = _validate_makemkv_binary(found)
            if result.found:
                return result

    # 2. Check platform-specific common locations
    for path_str in _get_makemkv_search_paths():
        if Path(path_str).is_file():
            logger.info(f"Found MakeMKV at: {path_str}")
            result = _validate_makemkv_binary(path_str)
            if result.found:
                return result

    return ToolDetectionResult(found=False, error="MakeMKV not found")


def detect_ffmpeg() -> ToolDetectionResult:
    """Auto-detect FFmpeg by searching PATH then common install locations."""
    # 1. Check system PATH
    found = shutil.which("ffmpeg")
    if found:
        logger.info(f"Found FFmpeg on PATH: {found}")
        result = _validate_ffmpeg_binary(found)
        if result.found:
            return result

    # 2. Check platform-specific common locations
    for path_str in _get_ffmpeg_search_paths():
        if Path(path_str).is_file():
            logger.info(f"Found FFmpeg at: {path_str}")
            result = _validate_ffmpeg_binary(path_str)
            if result.found:
                return result

    return ToolDetectionResult(found=False, error="FFmpeg not found")


@router.get("/detect-tools", response_model=DetectToolsResponse)
async def detect_tools() -> DetectToolsResponse:
    """Auto-detect MakeMKV and FFmpeg installations."""
    return DetectToolsResponse(
        makemkv=detect_makemkv(),
        ffmpeg=detect_ffmpeg(),
        platform=sys.platform,
    )


@router.post("/validate/makemkv", response_model=ValidationResponse)
async def validate_makemkv(request: ValidationRequest) -> ValidationResponse:
    """Validate MakeMKV installation by checking path and running without arguments."""
    makemkv_path = Path(request.path)

    # Check existence
    if not makemkv_path.exists():
        return ValidationResponse(valid=False, error="File not found at specified path")

    if not makemkv_path.is_file():
        return ValidationResponse(valid=False, error="Path is not a file")

    # Try running without arguments to get help text (with timeout to avoid hanging)
    # Note: MakeMKV returns exit code 1 for help, so we check output content instead
    try:
        result = subprocess.run([str(makemkv_path)], capture_output=True, timeout=10, text=True)

        # Check if output contains expected MakeMKV text
        output = result.stdout + result.stderr
        if "makemkvcon" not in output.lower() and "makemkv" not in output.lower():
            return ValidationResponse(valid=False, error="Not a valid MakeMKV executable")

        # Extract version if available in output, otherwise just confirm it's MakeMKV
        version = "MakeMKV (version not detectable)"
        for line in output.split("\n"):
            if "version" in line.lower() or "v1." in line or "v2." in line:
                version = line.strip()
                break

        return ValidationResponse(valid=True, version=version)

    except subprocess.TimeoutExpired:
        return ValidationResponse(valid=False, error="MakeMKV command timeout (10s)")
    except Exception as e:
        return ValidationResponse(valid=False, error=f"Execution failed: {str(e)}")


@router.post("/validate/ffmpeg", response_model=ValidationResponse)
async def validate_ffmpeg(request: ValidationRequest) -> ValidationResponse:
    """Validate FFmpeg installation. Empty path = check PATH."""
    if request.path:
        ffmpeg_cmd = Path(request.path)
        if not ffmpeg_cmd.exists():
            return ValidationResponse(valid=False, error="File not found at specified path")
        ffmpeg_path_str = str(ffmpeg_cmd)
    else:
        # Check system PATH
        ffmpeg_cmd_found = shutil.which("ffmpeg")
        if not ffmpeg_cmd_found:
            return ValidationResponse(valid=False, error="FFmpeg not found in system PATH")
        ffmpeg_path_str = ffmpeg_cmd_found

    try:
        result = subprocess.run(
            [ffmpeg_path_str, "-version"], capture_output=True, timeout=10, text=True
        )
        if result.returncode != 0:
            return ValidationResponse(valid=False, error="FFmpeg returned non-zero exit code")

        # Parse version
        version_line = result.stdout.split("\n")[0] if result.stdout else "Unknown"
        return ValidationResponse(valid=True, version=version_line, path=ffmpeg_path_str)

    except subprocess.TimeoutExpired:
        return ValidationResponse(valid=False, error="FFmpeg command timeout (10s)")
    except Exception as e:
        return ValidationResponse(valid=False, error=f"Execution failed: {str(e)}")
