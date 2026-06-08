"""On-demand NVIDIA CUDA math-library runtime for faster-whisper GPU acceleration.

faster-whisper → CTranslate2 needs **cuDNN 9 + cuBLAS** (CUDA ≥ 12.3) to run ASR on the
GPU. CTranslate2 ``dlopen``s those libraries lazily by name at runtime, so PyInstaller's
static analysis never sees them and they aren't in the frozen build. They are also large
(~1.2 GB), so bundling them into every download would triple the size for the CPU-only
majority. Instead this module downloads them **on demand, opt-in** into
``~/.engram/cuda/<version>/`` and makes them loadable before the first ``WhisperModel``
load. The cache lives outside the install dir, so — like the database and the subtitle
cache — it survives app updates and needs no updater changes.

The implementation mirrors two patterns already in the repo:

* ``scripts/fetch_fpcalc.py`` — pinned URL + SHA256 + extract the needed members from an
  archive (the nvidia pip wheels are just zips).
* the updater's integrity approach — stage into a sibling temp dir, then atomically
  ``os.replace`` it into place, so a killed/partial download never leaves a half-registered
  tree (a manifest ``complete`` sentinel is the source of truth).

CTranslate2 only supports NVIDIA CUDA (no Metal/CoreML, and AMD ROCm only via non-PyPI
wheels), so this is Windows + Linux on x86_64/aarch64 only. macOS and other platforms have
no asset and report unsupported — they stay on CPU ``int8``.

The pinned versions mirror the ``gpu`` optional-extra in ``pyproject.toml`` so the cuDNN
major always matches the locked ``ctranslate2``; bump them together.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import platform
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Pinned to match the `gpu` extra (nvidia-cudnn-cu12 9.19.0.56 + its nvidia-cublas-cu12
# 12.9.1.4) which in turn matches the locked ctranslate2 4.6.x (cuDNN 9 / CUDA 12). The
# cache dir is versioned by this string so a future ctranslate2 bump that needs a different
# cuDNN re-fetches into a fresh dir instead of loading stale libs.
RUNTIME_VERSION = "cudnn9.19.0.56-cublas12.9.1.4"


class CudaRuntimeError(RuntimeError):
    """Raised when the CUDA runtime cannot be downloaded or installed."""


@dataclass(frozen=True)
class _Wheel:
    """A pinned nvidia wheel (a plain zip) holding the CUDA shared libraries."""

    url: str
    sha256: str
    size: int  # bytes, for progress total + UI sizing


# Per-platform wheels. cuBLAS is listed first so it is present on disk before cuDNN (cuDNN
# depends on cuBLAS). URLs + hashes + sizes are copied verbatim from backend/uv.lock — keep
# them in sync with the `gpu` extra. cuBLAS 12.9.1.4 + cuDNN 9.19.0.56.
_WHEELS: dict[str, list[_Wheel]] = {
    "win_amd64": [
        _Wheel(
            "https://files.pythonhosted.org/packages/45/a1/a17fade6567c57452cfc8f967a40d1035bb9301db52f27808167fbb2be2f/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl",
            "1e5fee10662e6e52bd71dec533fbbd4971bb70a5f24f3bc3793e5c2e9dc640bf",
            553153899,
        ),
        _Wheel(
            "https://files.pythonhosted.org/packages/a7/a5/48f07449fc9c6cc146dcafe6149fa5d69630137d2ec5b7d9e09f255fadd7/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl",
            "cec70596b9ce878fab83810c3f5a2e606d35f510e5fee579759e4cbc68a23750",
            644003014,
        ),
    ],
    "linux_x86_64": [
        _Wheel(
            "https://files.pythonhosted.org/packages/77/3c/aa88abe01f3be3d1f8f787d1d33dc83e76fec05945f9a28fbb41cfb99cd5/nvidia_cublas_cu12-12.9.1.4-py3-none-manylinux_2_27_x86_64.whl",
            "453611eb21a7c1f2c2156ed9f3a45b691deda0440ec550860290dc901af5b4c2",
            581242350,
        ),
        _Wheel(
            "https://files.pythonhosted.org/packages/c5/41/65225d42fba06fb3dd3972485ea258e7dd07a40d6e01c95da6766ad87354/nvidia_cudnn_cu12-9.19.0.56-py3-none-manylinux_2_27_x86_64.whl",
            "ac6ad90a075bb33a94f2b4cf4622eac13dd4dc65cf6dd9c7572a318516a36625",
            657906812,
        ),
    ],
    "linux_aarch64": [
        _Wheel(
            "https://files.pythonhosted.org/packages/82/6c/90d3f532f608a03a13c1d6c16c266ffa3828e8011b1549d3b61db2ad59f5/nvidia_cublas_cu12-12.9.1.4-py3-none-manylinux_2_27_aarch64.whl",
            "7a950dae01add3b415a5a5cdc4ec818fb5858263e9cca59004bb99fdbbd3a5d6",
            575006342,
        ),
        _Wheel(
            "https://files.pythonhosted.org/packages/09/b8/277c51962ee46fa3e5b203ac5f76107c650f781d6891e681e28e6f3e9fe6/nvidia_cudnn_cu12-9.19.0.56-py3-none-manylinux_2_27_aarch64.whl",
            "08caaf27fe556aca82a3ee3b5aa49a77e7de0cfcb7ff4e5c29da426387a8267e",
            656910700,
        ),
    ],
}

_MANIFEST_NAME = "manifest.json"

# Process-wide download state, readable by GET /api/asr-status and updated during a download
# so the UI can show progress. Guarded so the worker thread and the event loop don't race.
_state_lock = threading.Lock()
_download_state: dict = {"state": "idle", "downloaded": 0, "total": 0, "error": None}
_download_thread: threading.Thread | None = None


# --------------------------------------------------------------------------- platform ----


def platform_key() -> str | None:
    """Return the ``_WHEELS`` key for this OS/arch, or ``None`` if unsupported.

    CTranslate2 ships CUDA support for NVIDIA on Windows + Linux only; macOS (no NVIDIA) and
    other arches get ``None`` and stay on CPU.
    """
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win_amd64" if machine in ("amd64", "x86_64") else None
    if sys.platform.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "linux_x86_64"
        if machine in ("aarch64", "arm64"):
            return "linux_aarch64"
    return None


def is_supported_platform() -> bool:
    """True if this OS/arch has a pinned CUDA runtime asset (Windows/Linux + NVIDIA-capable)."""
    return platform_key() is not None


def download_size_bytes() -> int:
    """Total bytes of the CUDA wheels for this platform (0 if unsupported)."""
    key = platform_key()
    return sum(w.size for w in _WHEELS[key]) if key else 0


# ------------------------------------------------------------------------------ cache ----


def engram_home() -> Path:
    """``~/.engram`` — the per-user state dir (DB, logs, caches) that survives updates."""
    return Path.home() / ".engram"


def cuda_cache_dir() -> Path:
    """``~/.engram/cuda/<RUNTIME_VERSION>/`` — where the extracted DLLs/.so land."""
    return engram_home() / "cuda" / RUNTIME_VERSION


def is_cuda_runtime_present(cache_dir: Path | None = None) -> bool:
    """True only when a *complete* downloaded runtime exists in ``cache_dir``.

    The manifest's ``complete`` flag plus an existence check on every listed file is the
    source of truth, so a partial extraction (killed mid-write) reads as absent and is
    re-fetched rather than half-loaded.
    """
    import json

    cache_dir = cache_dir or cuda_cache_dir()
    manifest = cache_dir / _MANIFEST_NAME
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not data.get("complete"):
        return False
    files = data.get("files", [])
    return bool(files) and all((cache_dir / name).is_file() for name in files)


def _pip_nvidia_available() -> bool:
    """True when the pip ``nvidia.*`` packages are importable (dev ``uv sync -E gpu``)."""
    try:
        return (
            importlib.util.find_spec("nvidia.cudnn") is not None
            and importlib.util.find_spec("nvidia.cublas") is not None
        )
    except (ImportError, ValueError):
        return False


def is_cuda_runtime_installed() -> bool:
    """True when CUDA libs are available by *either* path: the download cache or pip (dev)."""
    return is_cuda_runtime_present() or _pip_nvidia_available()


# --------------------------------------------------------------------------- download ----


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_verified(wheel: _Wheel, progress_cb=None, base: int = 0, total: int = 0) -> bytes:
    """Stream a pinned wheel, verifying its SHA256, reporting cumulative bytes via callback."""
    chunks: list[bytes] = []
    received = 0
    try:
        with urllib.request.urlopen(wheel.url, timeout=120) as resp:  # noqa: S310 - pinned https
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if progress_cb is not None and total:
                    progress_cb(base + received, total)
    except urllib.error.HTTPError as exc:
        raise CudaRuntimeError(f"HTTP {exc.code} fetching {wheel.url}") from exc
    except urllib.error.URLError as exc:
        raise CudaRuntimeError(f"network failure fetching {wheel.url}: {exc.reason}") from exc

    data = b"".join(chunks)
    actual = _sha256(data)
    if actual != wheel.sha256:
        raise CudaRuntimeError(
            f"SHA256 mismatch for {wheel.url}\n  expected {wheel.sha256}\n  got      {actual}"
        )
    return data


def _is_lib_member(name: str) -> bool:
    """True for the shared-library members inside an nvidia wheel (skip ``__init__`` etc.).

    Members look like ``nvidia/cublas/bin/cublas64_12.dll`` (Windows) or
    ``nvidia/cudnn/lib/libcudnn.so.9`` (Linux). We take regular files under a ``bin/`` or
    ``lib/`` segment whose basename is a DLL or an ``.so``.
    """
    parts = name.replace("\\", "/").split("/")
    if "nvidia" not in parts or not (("bin" in parts) or ("lib" in parts)):
        return False
    base = parts[-1]
    if not base or name.endswith("/"):
        return False
    lower = base.lower()
    return lower.endswith(".dll") or ".so" in lower


def _extract_libs(wheel_bytes: bytes, dest: Path) -> list[str]:
    """Extract the shared libs from a wheel zip into ``dest`` (flat), returning basenames."""
    extracted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        for member in zf.namelist():
            if not _is_lib_member(member):
                continue
            base = Path(member).name
            with zf.open(member) as src, open(dest / base, "wb") as out:
                shutil.copyfileobj(src, out)
            if os.name != "nt":
                (dest / base).chmod(0o755)
            extracted.append(base)
    return extracted


def _atomic_install(staging: Path, final: Path) -> None:
    """Replace ``final`` with ``staging`` atomically (both on the same filesystem)."""
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)


def download_cuda_runtime(progress_cb=None, *, force: bool = False) -> Path:
    """Download + verify + install the CUDA runtime for this platform. Returns the cache dir.

    ``progress_cb(downloaded_bytes, total_bytes)`` is called during the network transfer.
    Idempotent: returns immediately if a complete runtime is already present (unless
    ``force``). Staging happens in a hidden sibling dir on the same filesystem so a crash
    never leaves a partially-populated cache dir.
    """
    import json

    key = platform_key()
    if key is None:
        raise CudaRuntimeError(
            "GPU acceleration is not available on this platform "
            "(CTranslate2 supports NVIDIA CUDA on Windows/Linux only)"
        )

    final = cuda_cache_dir()
    if is_cuda_runtime_present(final) and not force:
        return final

    wheels = _WHEELS[key]
    total = sum(w.size for w in wheels)
    parent = final.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{RUNTIME_VERSION}-", dir=parent))
    try:
        files: list[str] = []
        base = 0
        for wheel in wheels:
            data = _download_verified(wheel, progress_cb, base=base, total=total)
            base += wheel.size
            files.extend(_extract_libs(data, staging))
        if not files:
            raise CudaRuntimeError("no CUDA libraries found inside the downloaded wheels")
        (staging / _MANIFEST_NAME).write_text(
            json.dumps({"version": RUNTIME_VERSION, "files": sorted(files), "complete": True}),
            encoding="utf-8",
        )
        _atomic_install(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logger.info(f"Installed CUDA runtime ({len(files)} libs) -> {final}")
    return final


# --------------------------------------------------------------------------- register ----


def _register_dir(lib_dir: Path) -> bool:
    """Make every shared lib in ``lib_dir`` loadable by a later CTranslate2 ``dlopen``."""
    if os.name == "nt":
        # add_dll_directory is the robust mechanism (Python 3.8+); PATH is belt-and-suspenders
        # and also helps any child process.
        try:
            os.add_dll_directory(str(lib_dir))
        except (OSError, AttributeError):
            pass
        existing = os.environ.get("PATH", "")
        if str(lib_dir) not in existing:
            os.environ["PATH"] = str(lib_dir) + os.pathsep + existing
        logger.debug(f"Registered CUDA DLL directory: {lib_dir}")
        return True
    return _preload_linux(lib_dir)


def _preload_linux(lib_dir: Path) -> bool:
    """Preload the .so files with RTLD_GLOBAL so CTranslate2's later dlopen finds them.

    On Linux, ``LD_LIBRARY_PATH`` is read once by the dynamic linker at process start, so
    setting it now would only help child processes. Instead we ``dlopen`` each library into
    the global symbol namespace; a subsequent ``dlopen`` of the same soname by CTranslate2
    returns the already-mapped handle. Inter-library deps (cuDNN→cuBLAS) are resolved by the
    wheels' ``$ORIGIN`` rpath plus a few retry passes, so explicit ordering isn't required.
    """
    import ctypes

    libs = sorted(lib_dir.glob("*.so*"))
    if not libs:
        return False
    remaining = list(libs)
    for _ in range(4):
        if not remaining:
            break
        still: list[Path] = []
        for lib in remaining:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                still.append(lib)
        if len(still) == len(remaining):
            break  # no progress this pass — give up on the stragglers
        remaining = still
    # Belt-and-suspenders for child processes / late soname resolution.
    os.environ["LD_LIBRARY_PATH"] = (
        str(lib_dir) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    )
    loaded = len(libs) - len(remaining)
    logger.debug(f"Preloaded {loaded}/{len(libs)} CUDA libs from {lib_dir}")
    return loaded > 0


def _register_pip() -> bool:
    """Dev fallback: register the pip ``nvidia.*`` package lib dirs (``uv sync -E gpu``)."""
    registered = False
    for mod_name in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(mod_name)
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.submodule_search_locations:
            continue
        pkg_dir = Path(next(iter(spec.submodule_search_locations)))
        lib_dir = pkg_dir / ("bin" if os.name == "nt" else "lib")
        if lib_dir.is_dir():
            registered = _register_dir(lib_dir) or registered
    return registered


def register_cuda_runtime() -> bool:
    """Make CUDA math libs loadable in this process before the first WhisperModel load.

    Prefers the downloaded cache (``~/.engram/cuda/``); falls back to the pip ``nvidia.*``
    packages for developers running ``uv sync -E gpu``. Returns True if libs were registered.
    Safe to call when nothing is installed (returns False).
    """
    cache_dir = cuda_cache_dir()
    if is_cuda_runtime_present(cache_dir):
        return _register_dir(cache_dir)
    if _pip_nvidia_available():
        return _register_pip()
    return False


# ----------------------------------------------------------------------- download task ----


def get_download_state() -> dict:
    """A snapshot of the current download progress for GET /api/asr-status."""
    with _state_lock:
        return dict(_download_state)


def _set_download_state(state: str, downloaded: int = 0, total: int = 0, error: str | None = None):
    with _state_lock:
        _download_state.update(
            {"state": state, "downloaded": downloaded, "total": total, "error": error}
        )


def is_downloading() -> bool:
    with _state_lock:
        return _download_state["state"] in ("downloading", "installing")


def start_background_download(on_done=None) -> bool:
    """Start a background thread that downloads + installs the CUDA runtime.

    ``on_done(success: bool, error: str | None)`` runs after the download finishes (e.g. to
    flip the config flag + broadcast). Returns False if a download is already running.
    """
    global _download_thread
    with _state_lock:
        if _download_state["state"] in ("downloading", "installing"):
            return False
        _download_state.update(
            {"state": "downloading", "downloaded": 0, "total": download_size_bytes(), "error": None}
        )

    def _run():
        try:
            download_cuda_runtime(
                progress_cb=lambda done, total: _set_download_state("downloading", done, total)
            )
            _set_download_state("idle", 0, 0, None)
            if on_done:
                on_done(True, None)
        except BaseException as exc:  # noqa: BLE001 - surface any failure to the UI
            logger.error(f"CUDA runtime download failed: {exc}")
            _set_download_state("error", 0, 0, str(exc))
            if on_done:
                on_done(False, str(exc))

    _download_thread = threading.Thread(target=_run, name="cuda-runtime-download", daemon=True)
    _download_thread.start()
    return True
