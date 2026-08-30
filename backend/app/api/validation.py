"""Validation endpoints for pre-flight checks."""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.guards import require_localhost_or_lan
from app.core.security import executable_basename_allowed, sanitize_log_value

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


class DiscordTemplateValidationRequest(BaseModel):
    """Request model for Discord notification template validation."""

    template: str


class AiValidationRequest(BaseModel):
    """Request model for AI provider connection testing.

    ``api_key`` is optional: when omitted or blank the stored key for the
    provider is used, so a user can verify a key they saved earlier without
    having it to hand. The stored key is never returned.

    ``model`` is likewise optional and blank means the provider default. It is
    sent from the wizard as typed, not as saved, so the button answers "will
    this combination work" rather than "did the last saved one work" — which is
    the whole point when the user is testing a model their key might not have.

    ``base_url`` applies only to the local providers and, like the others, is
    sent as typed rather than as saved, so the button answers "will this
    endpoint work" before the user commits to it.
    """

    provider: str
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


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
    """Return platform-specific common FFmpeg installation paths.

    On Windows this covers manual extracts (``C:\\ffmpeg\\bin``) plus the
    install layouts of the common package managers (Chocolatey, scoop) and a
    user-home extract. These often aren't on the *running* process's PATH —
    e.g. PATH was updated after Engram launched, so a restart-less install is
    invisible to ``shutil.which`` — yet the binary is sitting in a predictable
    spot. winget's version-stamped layout needs globbing and is handled
    separately by ``_iter_winget_ffmpeg_paths``.
    """
    if sys.platform != "win32":
        return [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
        ]

    paths = [
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        # Chocolatey drops a shim here; its bin dir is usually (but not always) on PATH.
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    ]
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        home = Path(userprofile)
        paths.extend(
            [
                # scoop (per-user): shim + the real binary under the app dir
                str(home / "scoop" / "shims" / "ffmpeg.exe"),
                str(home / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"),
                # common manual extract under the home directory
                str(home / "ffmpeg" / "bin" / "ffmpeg.exe"),
            ]
        )
    return paths


def _iter_winget_ffmpeg_paths() -> list[str]:
    """Resolve FFmpeg installed via ``winget install Gyan.FFmpeg``.

    winget is the install hint Engram shows in the Config Wizard, but it lays
    the build down under a version-stamped path —
    ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_*\\ffmpeg-*\\bin\\ffmpeg.exe``
    — that can't be hardcoded, so glob for it. Returns an empty list off
    Windows, when ``LOCALAPPDATA`` is unset, or when nothing matches.
    """
    if sys.platform != "win32":
        return []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return []
    packages = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
    try:
        return [str(p) for p in packages.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe")]
    except OSError:
        return []


_VERSION_NOT_DETECTABLE = "MakeMKV (version not detectable)"
_VERSION_PROBE_TIMEOUT = "MakeMKV (version probe timed out)"

# The MakeMKV version probe runs `makemkvcon -r info disc:99999`, which enumerates
# optical drives and can block for many seconds on a slow or busy drive.
#   * The interactive /validate/makemkv endpoint can afford the full wait.
#   * detect-tools fires on dashboard/Config-Wizard load, so it uses a short
#     budget — found+path never depends on the version string, so a stuck drive
#     shouldn't stall the page. A timed-out probe degrades to _VERSION_PROBE_TIMEOUT.
_VERSION_PROBE_TIMEOUT_S = 20.0
_DETECT_VERSION_PROBE_TIMEOUT_S = 3.0

# Hard wall-clock cap on the whole MakeMKV detector inside detect-tools. The short
# probe budget above handles the common slow-drive case; this is the backstop so
# anything else hanging in detect_makemkv (e.g. the no-arg validity call) still
# can't gate the ffmpeg/fpcalc results. Comfortably above the probe budget so a
# healthy-but-unhurried drive isn't cut off.
_MAKEMKV_DETECT_DEADLINE_S = 8.0

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


def _probe_makemkv_version(path_str: str, *, timeout: float = _VERSION_PROBE_TIMEOUT_S) -> str:
    """Best-effort version read via robot mode.

    Running with no arguments only prints usage text (no version), so the version
    comes from the robot-mode (-r) startup banner. Robot mode enumerates optical
    drives, so this is kept separate from the binary validity check and never
    blocks detection on a slow or busy drive. The out-of-range ``disc:99999``
    index can't open a real disc — it just triggers the banner. ``timeout`` bounds
    that drive enumeration: callers on a latency-sensitive path (detect-tools)
    pass a short budget; the interactive validate endpoint takes the default.
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
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired:
        # Distinct from "not detectable" so operators can tell a slow/busy drive
        # apart from a binary that simply never emitted a parseable version.
        logger.warning("MakeMKV version probe timed out (%.0fs)", timeout)
        return _VERSION_PROBE_TIMEOUT
    except Exception as e:
        logger.debug(f"MakeMKV version probe failed: {e}")
        return _VERSION_NOT_DETECTABLE
    return _extract_makemkv_version(result.stdout + result.stderr)


def _validate_makemkv_binary(
    path_str: str, *, version_probe_timeout: float = _VERSION_PROBE_TIMEOUT_S
) -> ToolDetectionResult:
    """Validate a MakeMKV binary and extract version info.

    ``version_probe_timeout`` bounds only the drive-enumerating version probe,
    not the fast no-arg validity check — so found+path is returned regardless of
    how slow the optical drive is. detect-tools passes a short budget here.
    """
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
    return ToolDetectionResult(
        found=True,
        path=path_str,
        version=_probe_makemkv_version(path_str, timeout=version_probe_timeout),
    )


def _validate_ffmpeg_binary(path_str: str) -> ToolDetectionResult:
    """Validate an FFmpeg binary and extract version info."""
    # Self-guard the subprocess sink: never execute a path whose basename isn't a
    # known FFmpeg executable, independent of the caller (py/command-line-injection).
    # Mirrors _validate_makemkv_binary and the endpoint-level guard.
    if not executable_basename_allowed(path_str, _FFMPEG_EXE_NAMES):
        return ToolDetectionResult(found=False, error="Not a valid FFmpeg executable")
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


def detect_makemkv(
    *, version_probe_timeout: float = _DETECT_VERSION_PROBE_TIMEOUT_S
) -> ToolDetectionResult:
    """Auto-detect MakeMKV by searching PATH then common install locations.

    The version probe defaults to the short budget because every caller is
    latency-sensitive and uses the version only cosmetically: detect-tools (on
    page load), lifespan startup (blocks the server coming up), and the
    diagnostics report. found+path is the load-bearing result and never depends
    on the probe, so a busy drive degrades the version to _VERSION_PROBE_TIMEOUT
    rather than stalling the caller.
    """
    # 1. Check system PATH
    for name in ("makemkvcon64", "makemkvcon"):
        found = shutil.which(name)
        if found:
            logger.info(f"Found MakeMKV on PATH: {found}")
            result = _validate_makemkv_binary(found, version_probe_timeout=version_probe_timeout)
            if result.found:
                return result

    # 2. Check platform-specific common locations
    for path_str in _get_makemkv_search_paths():
        if Path(path_str).is_file():
            logger.info(f"Found MakeMKV at: {path_str}")
            result = _validate_makemkv_binary(path_str, version_probe_timeout=version_probe_timeout)
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

    # 2. Check platform-specific common locations (package managers, manual
    #    extracts, and winget's version-stamped layout). Each candidate is still
    #    verified by _validate_ffmpeg_binary before it's accepted.
    for path_str in (*_get_ffmpeg_search_paths(), *_iter_winget_ffmpeg_paths()):
        if Path(path_str).is_file():
            logger.info(f"Found FFmpeg at: {path_str}")
            result = _validate_ffmpeg_binary(path_str)
            if result.found:
                return result

    return ToolDetectionResult(found=False, error="FFmpeg not found")


async def _detect_within_deadline(
    detector,
    *,
    deadline: float,
    on_timeout: ToolDetectionResult,
    label: str,
) -> ToolDetectionResult:
    """Run a blocking detector off the event loop under a wall-clock deadline.

    Bounds a single detector so a slow tool can't gate /detect-tools. A blocking
    subprocess can't be interrupted, so ``asyncio.wait`` is used to *return at*
    the deadline without cancelling — the over-deadline worker thread is left to
    finish and its result discarded, and ``on_timeout`` is returned in its place.
    """
    task = asyncio.create_task(asyncio.to_thread(detector))
    done, _pending = await asyncio.wait({task}, timeout=deadline)
    if task in done:
        return task.result()
    logger.warning(
        "%s detection exceeded its %.1fs deadline; returning degraded result", label, deadline
    )

    # When the orphaned probe eventually finishes, retrieve its result/exception so a
    # late failure isn't logged as "exception never retrieved". The result is dropped
    # (the response already moved on), but an unexpected exception — detectors are
    # meant to catch their own errors — is traced at debug rather than vanishing.
    def _discard_orphan(t):
        if not t.cancelled() and (exc := t.exception()):
            logger.debug("%s orphaned detector finished with error: %s", label, exc)

    task.add_done_callback(_discard_orphan)
    return on_timeout


@router.get("/detect-tools", response_model=DetectToolsResponse)
async def detect_tools() -> DetectToolsResponse:
    """Auto-detect MakeMKV, FFmpeg, and fpcalc installations.

    Detection shells out to the tools (blocking), so each detector runs off the
    event loop. MakeMKV is additionally bounded by a deadline: its version probe
    enumerates optical drives and can block for seconds on a busy drive, so it
    must never gate the quick ffmpeg/fpcalc ``-version`` results.
    """
    makemkv, ffmpeg, fpcalc = await asyncio.gather(
        _detect_within_deadline(
            detect_makemkv,
            deadline=_MAKEMKV_DETECT_DEADLINE_S,
            on_timeout=ToolDetectionResult(
                found=False, error="MakeMKV detection timed out (drive busy?)"
            ),
            label="MakeMKV",
        ),
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


class PathCheckResponse(BaseModel):
    """Whether a configured folder will actually work, and why not if it won't."""

    ok: bool
    # The value as Engram would store it: quotes stripped, ~ expanded, separators
    # settled. Shown back so the user can see what their input became.
    normalized: str
    exists: bool
    writable: bool
    is_network: bool
    # True when the location is fine to save but not reachable right now (an
    # offline share) — a warning, not a rejection.
    unreachable: bool = False
    free_bytes: int | None = None
    reason: str | None = None


@router.post("/validate/path", response_model=PathCheckResponse)
async def validate_path(
    request: ValidationRequest,
    _: None = Depends(require_localhost_or_lan),
) -> PathCheckResponse:
    """Check that a library/staging folder exists and Engram can write to it.

    Settings paths are the ones most likely to be quietly wrong — a typo, an
    offline NAS, a Docker volume owned by another uid — and the failure otherwise
    surfaces as an organize error hours later, after a full rip. The write test is
    a real touch/unlink, because on network shares and container mounts the
    permission bits regularly disagree with what the server can actually do.

    Gated like the other side-effecting validators in this module: the caller
    chooses the path, and the answer reports existence, writability and free
    space after really touching and unlinking a probe file there.
    """
    from app.core.paths import describe_path

    status = await asyncio.to_thread(describe_path, request.path)
    logger.debug(
        "Path check: %s -> ok=%s", sanitize_log_value(status.path), sanitize_log_value(status.ok)
    )
    return PathCheckResponse(
        ok=status.ok,
        normalized=status.path,
        exists=status.exists,
        writable=status.writable,
        is_network=status.is_network,
        unreachable=status.unreachable,
        free_bytes=status.free_bytes,
        reason=status.reason,
    )


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


@router.post("/validate/discord-template", response_model=ValidationResponse)
async def validate_discord_template_endpoint(
    request: DiscordTemplateValidationRequest,
) -> ValidationResponse:
    """Validate a Discord notification template string (chevron/mustache syntax).

    Live per-keystroke check for ConfigWizard; PUT /api/config re-validates via
    the same underlying function before persisting, so this is UX-only — it
    can't be bypassed to skip the real enforcement.
    """
    from app.core.discord_notifier import validate_discord_template

    error = validate_discord_template(request.template)
    if error:
        return ValidationResponse(valid=False, error=error)
    return ValidationResponse(valid=True)


@router.post("/validate/ai", response_model=ValidationResponse)
async def validate_ai(
    request: AiValidationRequest,
    _: None = Depends(require_localhost_or_lan),
) -> ValidationResponse:
    """Verify an AI provider credential with one real, tiny completion.

    Deliberately routed through the same ``complete_json`` the episode matcher
    uses, rather than a bespoke auth ping: a ping would exercise a different
    endpoint, without the structured-output convention, and could therefore pass
    while real matching fails. Gated to the host (or an opted-in LAN) because,
    unlike the other validators, this one makes a real outbound request — to a
    paid hosted API for a remote provider, or to whatever local server the user
    pointed it at.
    """
    from app.core.ai_client import (
        DEFAULT_MODELS,
        KNOWN_PROVIDERS,
        LOCAL_PROVIDERS,
        LOCAL_VALIDATE_TIMEOUT_SECONDS,
        ai_is_configured,
        complete_json,
    )
    from app.core.errors import AIProviderError

    provider = (request.provider or "").strip()
    if provider not in KNOWN_PROVIDERS:
        return ValidationResponse(
            valid=False, error=f"Unknown AI provider: {provider or '(empty)'}"
        )

    api_key = (request.api_key or "").strip()
    model = (request.model or "").strip()
    base_url = (request.base_url or "").strip()
    if not api_key or not model or (provider in LOCAL_PROVIDERS and not base_url):
        from app.services.config_service import get_config

        # Guarded: this endpoint promises never to 500, and a config read can
        # fail (locked or migrating DB). Reporting "could not read the saved
        # key" is more useful than a stack trace behind a dead button.
        config = None
        try:
            config = await get_config()
        except Exception:  # noqa: BLE001 — a validator must never 500
            logger.warning("AI validation could not read the stored config", exc_info=True)
        if config is None and not api_key and provider not in LOCAL_PROVIDERS:
            # Only fatal when the key itself was what we came for. A failed read
            # with a supplied key just means "no stored model override", which
            # falls back to the provider default like any blank model. Local
            # providers never needed a key in the first place.
            return ValidationResponse(
                valid=False, error="Could not read the saved API key from the database"
            )
        if config is not None:
            if not api_key:
                api_key = (getattr(config, "ai_api_key", "") or "").strip()
            if not model:
                model = (getattr(config, "ai_model", "") or "").strip()
            if not base_url:
                base_url = (getattr(config, "ai_local_base_url", "") or "").strip()

    if not ai_is_configured(provider, api_key):
        return ValidationResponse(
            valid=False, error="No API key provided and none is saved for this provider"
        )
    if provider in LOCAL_PROVIDERS and not model:
        return ValidationResponse(
            valid=False,
            error=(
                "Select a model first. Local providers have no default, because the "
                "answer depends on which models you have installed."
            ),
        )

    if provider in LOCAL_PROVIDERS:
        # LM Studio answers a request for an unknown model by silently serving
        # whichever model is currently loaded, with HTTP 200 and no warning, so a
        # typo would otherwise pass this check and then quietly matter at match
        # time. Confirming against the server's own list is the only way to catch
        # it. A server we cannot reach is left to the completion below, whose
        # error message is better than anything we could say here.
        available, _list_error = await _fetch_local_model_ids(provider, base_url)
        if available and model not in available:
            shown = ", ".join(available[:10])
            if len(available) > 10:
                shown += ", ..."
            return ValidationResponse(
                valid=False,
                error=f"'{model}' is not installed on this server. Available: {shown}",
            )

    try:
        result = await complete_json(
            # "JSON" must appear in the prompt: OpenAI rejects a json_object
            # response_format whose messages never mention it.
            prompt='Reply with {"ok": true} and nothing else. Respond as JSON.',
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            provider=provider,
            api_key=api_key,
            model=model or None,
            base_url=base_url,
            max_tokens=16,
            raise_on_error=True,
            # A dead key must fail in one request rather than paying the backoff
            # ladder, and a user waiting on a button should not sit for 30s. Local
            # providers get a much longer budget: LM Studio JIT-loads model
            # weights on the first request, so a cold model can take a minute-plus.
            retries=0,
            timeout=(LOCAL_VALIDATE_TIMEOUT_SECONDS if provider in LOCAL_PROVIDERS else 10.0),
        )
    except AIProviderError as e:
        logger.warning("AI validation failed for %s: %s", sanitize_log_value(provider), e.code)
        return ValidationResponse(valid=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — a validator must never 500
        logger.warning(
            "AI validation raised unexpectedly for %s",
            sanitize_log_value(provider),
            exc_info=True,
        )
        return ValidationResponse(valid=False, error=f"Validation failed: {e}")

    if not result:
        return ValidationResponse(valid=False, error="Provider returned an empty response")
    # Report the model that was requested (or the provider default), not the one
    # that actually answered — complete_json doesn't surface that. For remote
    # providers those can differ if the provider silently substitutes a model;
    # for local providers the pre-flight check above is what keeps them in
    # agreement, by rejecting a request whose model the server doesn't offer.
    return ValidationResponse(valid=True, version=model or DEFAULT_MODELS.get(provider, ""))


@router.post("/validate/discord-webhook", response_model=ValidationResponse)
async def test_discord_webhook(
    _: None = Depends(require_localhost_or_lan),
) -> ValidationResponse:
    """Post a sample notification so the user can confirm the saved webhook works.

    Takes NO request body on purpose. An earlier revision accepted a URL so the
    user could test before saving, but that made an HTTP request body the target
    of a server-side request, which is server-side request forgery however
    carefully the value is validated first. Testing only the persisted value
    also answers the more useful question: the saved webhook is the one real
    notifications will use, so a test of an unsaved string can pass and still
    leave the user with a broken configuration.

    Builds the sample through the real build_embed path, so a passing test
    exercises the production builder rather than a stub, and posts with
    ``raise_on_error=True`` so a rejection by Discord (revoked webhook, deleted
    channel) reports as invalid. Without that flag notify_discord swallows the
    failure and the button would cheerfully report success for a dead URL, which
    is the one thing it exists to rule out.

    Gated to the host (or an opted-in LAN) for the same reason as the AI
    validator: it is the one validator that triggers an outbound request the
    caller can provoke without owning the destination.
    """
    from app.core.discord_notifier import EVENTS, build_embed, notify_discord
    from app.core.security import is_safe_remote_url
    from app.models.disc_job import ContentType, DiscJob, JobState
    from app.services.config_service import get_config

    config = await get_config()
    webhook_url = config.discord_webhook_url
    if not webhook_url:
        return ValidationResponse(
            valid=False, error="No webhook URL configured. Save one first, then test it."
        )

    # Same guard PUT /api/config applies on write. Re-checked here because a
    # stored value can predate validation or be edited into the DB directly.
    if not is_safe_remote_url(webhook_url):
        return ValidationResponse(
            valid=False,
            error="Saved webhook URL must be an http/https URL pointing to a non-internal host",
        )

    sample = DiscJob(
        drive_id="E:",
        content_type=ContentType.TV,
        detected_title="Engram Test",
        volume_label="ENGRAM_TEST_S1D1",
        detected_season=1,
        disc_number=1,
        total_titles=6,
        state=JobState.COMPLETED,
    )
    embed = build_embed(sample, [], EVENTS[JobState.COMPLETED], "**Engram Test**")

    # Positional, matching every other call site: the tests assert on
    # call_args[0][2] being the embed. job_id 0 is a sentinel: no real job backs
    # this smoke test, and real ids autoincrement from 1, so a "job 0" log line
    # is unambiguously from here.
    try:
        await notify_discord(webhook_url, 0, embed, raise_on_error=True)
    except Exception as e:
        logger.warning(f"Discord webhook test failed: {e}", exc_info=True)
        return ValidationResponse(valid=False, error=f"Discord rejected the test message: {e}")

    return ValidationResponse(valid=True)


class LocalModelsResponse(BaseModel):
    """Model ids available on a local AI server.

    Never signals failure with a status code: an unreachable server is the
    NORMAL case while a user is still setting things up, and a 5xx would make
    the wizard render an error banner instead of quietly falling back to a
    free-text model field.
    """

    models: list[str] = []
    error: str | None = None


async def _fetch_local_model_ids(provider: str, base_url: str) -> tuple[list[str], str | None]:
    """Model ids a local server offers, plus a human error if it could not be read.

    Shared by ``GET /ai/local-models`` and ``validate_ai``'s pre-flight check, so
    the two cannot disagree about what the server offers. Never raises: a bad
    URL, an unreachable server, or a malformed response all come back as an
    empty list with a message, never an exception — both callers treat an empty
    list as "could not confirm", not as "the server has zero models".
    """
    from app.core.ai_client import classify_provider_error, resolve_local_base_url
    from app.core.security import is_safe_local_ai_url

    url_base = resolve_local_base_url(provider, base_url)
    if not is_safe_local_ai_url(url_base):
        return [], (
            "That is not a valid server address. Use a plain http(s) URL such "
            "as http://localhost:11434/v1."
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url_base}/models")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        _, message = classify_provider_error(provider, e, base_url=url_base)
        logger.info(
            "Local model list unavailable for %s: %s",
            sanitize_log_value(provider),
            sanitize_log_value(str(e)),
        )
        return [], message
    except Exception:  # noqa: BLE001 — callers must never see this raise
        logger.warning("Local model list raised unexpectedly", exc_info=True)
        return [], "Could not read the model list"

    models: list[str] = []
    for entry in (data or {}).get("data") or []:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(str(entry["id"]))
    return models, None


@router.get("/ai/local-models", response_model=LocalModelsResponse)
async def list_local_models(
    provider: str,
    base_url: str = "",
    _: None = Depends(require_localhost_or_lan),
) -> LocalModelsResponse:
    """List the models a local AI server currently offers.

    Both Ollama and LM Studio expose the OpenAI-compatible ``GET /v1/models``,
    so one implementation serves both, shared with ``validate_ai``'s pre-flight
    check via ``_fetch_local_model_ids``. Gated to the host (or an opted-in LAN)
    for the same reason as validate_ai: it triggers an outbound request the
    caller can provoke without owning the destination.

    Doubles as a reachability check, which is why the wizard does not need a
    separate Test button for the local case.
    """
    from app.core.ai_client import LOCAL_PROVIDERS

    if provider not in LOCAL_PROVIDERS:
        return LocalModelsResponse(
            error=f"'{sanitize_log_value(provider)}' is not a local AI provider"
        )

    models, error = await _fetch_local_model_ids(provider, base_url)
    return LocalModelsResponse(models=models, error=error)
