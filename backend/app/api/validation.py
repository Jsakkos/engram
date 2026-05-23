"""Validation endpoints for pre-flight checks."""

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.security import executable_basename_allowed

logger = logging.getLogger(__name__)

router = APIRouter()

# Known executable filenames for the tool validators. Validation runs the
# binary, so it must be a real tool executable — never an arbitrary script
# supplied as a config path.
_MAKEMKV_EXE_NAMES = (
    "makemkvcon",
    "makemkvcon.exe",
    "makemkvcon64",
    "makemkvcon64.exe",
    "com.makemkv.MakeMKV",
)
_FFMPEG_EXE_NAMES = ("ffmpeg", "ffmpeg.exe")


class ValidationRequest(BaseModel):
    """Request model for validation endpoints."""

    path: str


class TmdbValidationRequest(BaseModel):
    """Request model for TMDB API key validation."""

    api_key: str


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


_VERSION_NOT_DETECTABLE = "MakeMKV (version not detectable)"

# Robot mode (-r) prints a startup banner naming the version, e.g.
#   MSG:1005,0,1,"MakeMKV v1.18.3 win(x64-release) started",...
# Capture "MakeMKV v1.18.3 win(x64-release)" — product, semantic version, and the
# optional platform tag — while dropping the trailing " started".
_MAKEMKV_VERSION_RE = re.compile(
    r"MakeMKV\s+v\d+(?:\.\d+)*(?:\s+\w+\([^)]*\))?",
    re.IGNORECASE,
)


def _extract_makemkv_version(output: str) -> str:
    """Extract a MakeMKV version string from command output."""
    match = _MAKEMKV_VERSION_RE.search(output)
    if match:
        return match.group(0).strip()
    # Fallback for output that names a version on its own line without the banner.
    for line in output.split("\n"):
        if "version" in line.lower() or "v1." in line or "v2." in line:
            return line.strip()
    return _VERSION_NOT_DETECTABLE


def _probe_makemkv_version(path_str: str) -> str:
    """Best-effort version read via robot mode.

    Running with no arguments only prints usage text (no version), so the version
    comes from the robot-mode (-r) startup banner. Robot mode enumerates optical
    drives, so this is kept separate from the binary validity check and never
    blocks detection on a slow or busy drive. The out-of-range ``disc:99999``
    index can't open a real disc — it just triggers the banner.
    """
    # Refuse to launch anything that isn't a MakeMKV executable, so a
    # user-supplied config path can't coerce this into running an arbitrary
    # binary (py/command-line-injection). Mirrors the endpoint-level guard.
    if not executable_basename_allowed(path_str, _MAKEMKV_EXE_NAMES):
        return _VERSION_NOT_DETECTABLE
    try:
        result = subprocess.run(
            [path_str, "-r", "info", "disc:99999"],
            capture_output=True,
            timeout=20,
            text=True,
        )
    except Exception as e:
        logger.debug(f"MakeMKV version probe failed: {e}")
        return _VERSION_NOT_DETECTABLE
    return _extract_makemkv_version(result.stdout + result.stderr)


def _validate_makemkv_binary(path_str: str) -> ToolDetectionResult:
    """Validate a MakeMKV binary and extract version info."""
    # Self-guard the subprocess sink: never execute a path whose basename isn't
    # a known MakeMKV executable, independent of the caller (py/command-line-injection).
    if not executable_basename_allowed(path_str, _MAKEMKV_EXE_NAMES):
        return ToolDetectionResult(found=False, error="Not a valid MakeMKV executable")
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

        return ToolDetectionResult(
            found=True, path=path_str, version=_probe_makemkv_version(path_str)
        )
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

    # Constrain to known MakeMKV executables before any filesystem or
    # subprocess access — the endpoint must not run an arbitrary binary.
    if not executable_basename_allowed(str(makemkv_path), _MAKEMKV_EXE_NAMES):
        return ValidationResponse(valid=False, error="Path does not point to a MakeMKV executable")

    # Check existence
    if not makemkv_path.exists():
        return ValidationResponse(valid=False, error="File not found at specified path")

    if not makemkv_path.is_file():
        return ValidationResponse(valid=False, error="Path is not a file")

    # MakeMKV returns exit code 1 for help, so the helper checks output content instead.
    result = _validate_makemkv_binary(str(makemkv_path))
    if not result.found:
        error = result.error
        if error == "Command timeout (10s)":
            error = "MakeMKV command timeout (10s)"
        return ValidationResponse(valid=False, error=error)
    # Note: path is intentionally omitted from this response.
    return ValidationResponse(valid=True, version=result.version)


@router.post("/validate/ffmpeg", response_model=ValidationResponse)
async def validate_ffmpeg(request: ValidationRequest) -> ValidationResponse:
    """Validate FFmpeg installation. Empty path = check PATH."""
    if request.path:
        ffmpeg_cmd = Path(request.path)
        # Constrain to known FFmpeg executables before filesystem/subprocess use.
        if not executable_basename_allowed(str(ffmpeg_cmd), _FFMPEG_EXE_NAMES):
            return ValidationResponse(
                valid=False, error="Path does not point to an FFmpeg executable"
            )
        if not ffmpeg_cmd.exists():
            return ValidationResponse(valid=False, error="File not found at specified path")
        ffmpeg_path_str = str(ffmpeg_cmd)
    else:
        # Check system PATH
        ffmpeg_cmd_found = shutil.which("ffmpeg")
        if not ffmpeg_cmd_found:
            return ValidationResponse(valid=False, error="FFmpeg not found in system PATH")
        ffmpeg_path_str = ffmpeg_cmd_found

    result = _validate_ffmpeg_binary(ffmpeg_path_str)
    if not result.found:
        error = result.error
        if error == "Non-zero exit code":
            error = "FFmpeg returned non-zero exit code"
        elif error == "Command timeout (10s)":
            error = "FFmpeg command timeout (10s)"
        return ValidationResponse(valid=False, error=error)
    return ValidationResponse(valid=True, version=result.version, path=result.path)


@router.post("/validate/tmdb", response_model=ValidationResponse)
async def validate_tmdb(request: TmdbValidationRequest) -> ValidationResponse:
    """Validate a TMDB API key by making a lightweight configuration request."""
    api_key = request.api_key.strip()
    if not api_key:
        return ValidationResponse(valid=False, error="API key is empty")

    from app.core.tmdb_classifier import _build_auth

    headers, params = _build_auth(api_key)

    try:
        response = requests.get(
            "https://api.themoviedb.org/3/configuration",
            headers=headers,
            params=params,
            timeout=5,
        )
        if response.status_code == 200:
            return ValidationResponse(valid=True, version="TMDB API v3")
        elif response.status_code in (401, 403):
            return ValidationResponse(valid=False, error="Invalid API key or token")
        else:
            return ValidationResponse(
                valid=False, error=f"TMDB returned status {response.status_code}"
            )
    except requests.exceptions.Timeout:
        return ValidationResponse(valid=False, error="TMDB API timeout (5s)")
    except requests.exceptions.ConnectionError:
        return ValidationResponse(
            valid=False, error="Cannot reach TMDB API — check internet connection"
        )
    except Exception as e:
        return ValidationResponse(valid=False, error=f"Validation failed: {str(e)}")
