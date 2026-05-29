"""Validation endpoints for pre-flight checks."""

import asyncio
import logging
import os
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
_FPCALC_EXE_NAMES = ("fpcalc", "fpcalc.exe")


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
    fpcalc: ToolDetectionResult
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
_VERSION_PROBE_TIMEOUT = "MakeMKV (version probe timed out)"

# Robot mode (-r) prints a startup banner naming the version, e.g.
#   MSG:1005,0,1,"MakeMKV v1.18.3 win(x64-release) started",...
# Capture "MakeMKV v1.18.3 win(x64-release)" — product, semantic version, and the
# optional platform tag — while dropping the trailing " started".
_MAKEMKV_VERSION_RE = re.compile(
    r"MakeMKV\s+v\d+(?:\.\d+)*(?:\s+\w+\([^)]*\))?",
    re.IGNORECASE,
)


def _extract_makemkv_version(output: str) -> str:
    """Extract a MakeMKV version string from robot-mode command output.

    Matches only the MSG:1005 banner pattern. A looser line-scan would
    false-match the verbose drive-enumeration output (e.g. a ``DRV:`` line or a
    ``"v1.0 codec loaded"`` message) and return garbage as the version string.
    """
    match = _MAKEMKV_VERSION_RE.search(output)
    if match:
        return match.group(0).strip()
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
    except subprocess.TimeoutExpired:
        # Distinct from "not detectable" so operators can tell a slow/busy drive
        # apart from a binary that simply never emitted a parseable version.
        logger.warning("MakeMKV version probe timed out (20s)")
        return _VERSION_PROBE_TIMEOUT
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
    except subprocess.TimeoutExpired:
        return ToolDetectionResult(found=False, path=path_str, error="Command timeout (10s)")
    except Exception as e:
        return ToolDetectionResult(found=False, error=f"Execution failed: {e}")

    output = result.stdout + result.stderr
    if "makemkvcon" not in output.lower() and "makemkv" not in output.lower():
        return ToolDetectionResult(found=False, error="Not a valid MakeMKV executable")

    # Probe is decoupled from the validity check above: it self-catches all its
    # own errors, so its subprocess lifetime never interacts with this try block.
    return ToolDetectionResult(found=True, path=path_str, version=_probe_makemkv_version(path_str))


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


def _validate_fpcalc_binary(path_str: str) -> ToolDetectionResult:
    """Validate a chromaprint fpcalc binary and extract version info."""
    try:
        result = subprocess.run(
            [path_str, "-version"],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode != 0:
            return ToolDetectionResult(
                found=False,
                path=path_str,
                error=f"Non-zero exit code {result.returncode}",
            )
        version_line = (result.stdout or "").split("\n")[0] or "unknown"
        return ToolDetectionResult(found=True, path=path_str, version=version_line)
    except subprocess.TimeoutExpired:
        return ToolDetectionResult(found=False, path=path_str, error="Timed out")
    except Exception as e:
        return ToolDetectionResult(found=False, path=path_str, error=str(e))


FPCALC_COMMON_PATHS = [
    # Windows
    r"C:\Program Files\Chromaprint\fpcalc.exe",
    r"C:\Program Files (x86)\Chromaprint\fpcalc.exe",
    # macOS (homebrew)
    "/opt/homebrew/bin/fpcalc",
    "/usr/local/bin/fpcalc",
    # Linux
    "/usr/bin/fpcalc",
]

# Developers can point auto-detect at a local-tree spike binary (or any other
# off-PATH install) by setting ENGRAM_FPCALC_PATH. Shipping the spike binary
# directly in `FPCALC_COMMON_PATHS` would leak an internal repo layout to all
# users' subprocess audit trails and add a useless probe in production.
_DEV_FPCALC_ENV = "ENGRAM_FPCALC_PATH"


def _bundled_fpcalc_path() -> str | None:
    """Return the path to the fpcalc binary shipped with Engram, if present.

    Frozen PyInstaller builds carry it at ``<sys._MEIPASS>/bin/fpcalc[.exe]``;
    source checkouts get it at ``app/bin/fpcalc[.exe]`` once
    ``scripts/fetch_fpcalc.py`` has populated that directory. Returns the first
    existing candidate, or None when no bundled copy is available — so the
    detector cleanly falls through to PATH / common locations.
    """
    name = "fpcalc.exe" if os.name == "nt" else "fpcalc"
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # Frozen build: the spec bundles fpcalc to <_MEIPASS>/bin. (In a frozen
        # build __file__ also lives under _MEIPASS, so the app/bin probe below
        # would resolve to a path that never exists — skip it.)
        roots.append(Path(meipass) / "bin")
    else:
        # Source checkout: app/bin/ (app/ is the parent of this module's package
        # dir, app/api/ -> app/), populated by scripts/fetch_fpcalc.py.
        roots.append(Path(__file__).resolve().parent.parent / "bin")
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return str(candidate)
    return None


def detect_fpcalc() -> ToolDetectionResult:
    """Auto-detect a usable fpcalc binary.

    Order: explicit ``ENGRAM_FPCALC_PATH`` env var, then Engram's bundled copy
    (``<_MEIPASS>/bin`` when frozen, ``app/bin`` in a checkout), then PATH, then
    common platform locations. Returns the first result that validates
    successfully.
    """
    candidates: list[str] = []
    env_override = os.environ.get(_DEV_FPCALC_ENV)
    if env_override:
        candidates.append(env_override)
    # Engram's own bundled copy beats whatever is on PATH so users get the
    # known-good shipped version by default — but an explicit env override still
    # wins, and a bundled binary that fails validation (e.g. wrong arch) falls
    # through to PATH / common locations below.
    bundled = _bundled_fpcalc_path()
    if bundled:
        candidates.append(bundled)
    via_path = shutil.which("fpcalc")
    if via_path:
        candidates.append(via_path)
    candidates.extend(FPCALC_COMMON_PATHS)

    for candidate in candidates:
        result = _validate_fpcalc_binary(candidate)
        if result.found:
            return result

    return ToolDetectionResult(
        found=False,
        path=None,
        error="fpcalc not found in PATH or common locations",
    )


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
    """Auto-detect MakeMKV, FFmpeg, and fpcalc installations."""
    # Detection shells out to the tools (blocking, multi-second on slow/busy
    # drives), so run it off the event loop to avoid stalling other requests.
    makemkv, ffmpeg, fpcalc = await asyncio.gather(
        asyncio.to_thread(detect_makemkv),
        asyncio.to_thread(detect_ffmpeg),
        asyncio.to_thread(detect_fpcalc),
    )
    return DetectToolsResponse(makemkv=makemkv, ffmpeg=ffmpeg, fpcalc=fpcalc, platform=sys.platform)


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
    # Runs blocking subprocesses, so offload to a thread to keep the event loop free.
    result = await asyncio.to_thread(_validate_makemkv_binary, str(makemkv_path))
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

    result = await asyncio.to_thread(_validate_ffmpeg_binary, ffmpeg_path_str)
    if not result.found:
        error = result.error
        if error == "Non-zero exit code":
            error = "FFmpeg returned non-zero exit code"
        elif error == "Command timeout (10s)":
            error = "FFmpeg command timeout (10s)"
        return ValidationResponse(valid=False, error=error)
    return ValidationResponse(valid=True, version=result.version, path=result.path)


@router.post("/validate/fpcalc", response_model=ValidationResponse)
async def validate_fpcalc(request: ValidationRequest) -> ValidationResponse:
    """Validate a user-supplied fpcalc binary path."""
    fpcalc_cmd = Path(request.path)
    # Constrain to known fpcalc executables before filesystem/subprocess use.
    if not executable_basename_allowed(str(fpcalc_cmd), _FPCALC_EXE_NAMES):
        return ValidationResponse(valid=False, error="Path does not point to an fpcalc executable")
    if not fpcalc_cmd.exists():
        return ValidationResponse(valid=False, error="File not found at specified path")

    result = await asyncio.to_thread(_validate_fpcalc_binary, request.path)
    if not result.found:
        return ValidationResponse(valid=False, error=result.error, path=result.path)
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
