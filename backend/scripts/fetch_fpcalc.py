"""Fetch the bundled ``fpcalc`` binary for the current platform.

Downloads the pinned Chromaprint release asset, verifies its SHA256 against the
hardcoded table below, extracts the ``fpcalc`` executable, and installs it to
``backend/app/bin/fpcalc[.exe]`` — the location :func:`app.api.validation.detect_fpcalc`
checks before PATH, and the one ``engram.spec`` bundles into frozen builds.

Run by CI before ``pyinstaller engram.spec`` so end users get a working fpcalc
out of the box, and usable by developers who want a locally-detected fpcalc
without a system-wide install::

    uv run python scripts/fetch_fpcalc.py            # current platform
    uv run python scripts/fetch_fpcalc.py --force    # re-download even if present

``app/bin/`` is gitignored: the binary is fetched, never committed (the same
way the frontend is built into ``app/static`` rather than checked in).

Chromaprint is licensed under the LGPL-2.1+; the binary is redistributed
unmodified. See ``THIRD_PARTY_LICENSES`` at the repo root.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CHROMAPRINT_VERSION = "1.5.1"
_BASE = f"https://github.com/acoustid/chromaprint/releases/download/v{CHROMAPRINT_VERSION}"

# Per-platform release asset + its SHA256. Hashes were computed from the
# official v1.5.1 GitHub release assets. Bump all three together on a version
# change. macOS uses the native arm64 build, matching the arm64-only release
# matrix in .github/workflows/release.yml.
_ASSETS: dict[str, tuple[str, str]] = {
    "windows": (
        f"chromaprint-fpcalc-{CHROMAPRINT_VERSION}-windows-x86_64.zip",
        "36b478e16aa69f757f376645db0d436073a42c0097b6bb2677109e7835b59bbc",
    ),
    "linux": (
        f"chromaprint-fpcalc-{CHROMAPRINT_VERSION}-linux-x86_64.tar.gz",
        "4d7433a7f778e5946d7225230681cbcd634e153316ecac87c538c33ac32387a5",
    ),
    "darwin": (
        f"chromaprint-fpcalc-{CHROMAPRINT_VERSION}-macos-arm64.tar.gz",
        "9c5d9565d2396dbcf0e1d797e1ffdf1e19242f3bed88ac3200e144286b57ede6",
    ),
}


def _target_path() -> Path:
    """``backend/app/bin/fpcalc[.exe]`` — resolved relative to this script."""
    exe = "fpcalc.exe" if os.name == "nt" else "fpcalc"
    # scripts/ -> backend/, so backend/app/bin/
    return Path(__file__).resolve().parent.parent / "app" / "bin" / exe


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_fpcalc(archive: Path, dest: Path) -> None:
    """Pull the single ``fpcalc[.exe]`` member out of the archive into ``dest``.

    The release archives wrap the binary in a versioned top-level folder, so we
    locate it by basename rather than assuming a fixed path.
    """
    want = dest.name
    dest.parent.mkdir(parents=True, exist_ok=True)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            member = next((m for m in zf.namelist() if Path(m).name == want), None)
            if member is None:
                raise SystemExit(f"error: {want} not found inside {archive.name}")
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
    else:  # .tar.gz
        with tarfile.open(archive, "r:gz") as tf:
            member = next((m for m in tf.getmembers() if Path(m.name).name == want), None)
            if member is None:
                raise SystemExit(f"error: {want} not found inside {archive.name}")
            extracted = tf.extractfile(member)
            if extracted is None:
                raise SystemExit(f"error: {want} is not a regular file in {archive.name}")
            with extracted as src, open(dest, "wb") as out:
                out.write(src.read())

    if os.name != "nt":
        dest.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the bundled fpcalc binary.")
    parser.add_argument(
        "--force", action="store_true", help="re-download even if the binary already exists"
    )
    args = parser.parse_args()

    system = platform.system().lower()
    if system not in _ASSETS:
        raise SystemExit(f"error: no pinned fpcalc asset for platform {system!r}")

    dest = _target_path()
    if dest.is_file() and not args.force:
        print(f"fpcalc already present at {dest} (use --force to re-download)")
        return 0

    asset, expected_sha = _ASSETS[system]
    url = f"{_BASE}/{asset}"
    print(f"downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - pinned https URL
            data = resp.read()
    except urllib.error.HTTPError as exc:  # HTTPError is a URLError subclass — catch first
        raise SystemExit(f"error: HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: network failure fetching {url}: {exc.reason}") from exc

    actual_sha = _sha256(data)
    if actual_sha != expected_sha:
        raise SystemExit(
            f"error: SHA256 mismatch for {asset}\n  expected {expected_sha}\n  got      {actual_sha}"
        )
    print(f"sha256 ok ({actual_sha})")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        archive.write_bytes(data)
        _extract_fpcalc(archive, dest)

    print(f"installed fpcalc -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
