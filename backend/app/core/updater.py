"""Auto-update checker for Engram.

Checks GitHub Releases on startup, downloads the new version in the background,
verifies the SHA256 checksum, and stages it for a user-triggered restart.

Platform restart strategies:
  Linux/macOS: shutil.copy2 + os.execv (replaces process image in-place)
  Windows:     .bat helper that, after this process exits, robocopies the staged
               build to a sibling dir, verifies it, swaps it in via two atomic
               same-volume renames, relaunches, and rolls back on any failure —
               logging every step to ~/.engram/update_helper.log. It deletes only the
               previous build's own files: an install dir that holds anything else is
               refused up front, and anything that slips through is kept (#825).
"""

import asyncio
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import httpx
from loguru import logger

from app import __version__
from app.config import is_frozen, settings
from app.core.errors import ConfigurationError, EngramError
from app.database import async_session

GITHUB_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/latest"
GITHUB_TAG_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/tags/v{version}"
STAGING_BASE = Path.home() / ".engram" / "update"

# The Windows build's release asset (#825). Every updater from v0.9.0 to v0.43.0 picks
# the Windows asset by ``.zip`` + "windows" in the name, and the helper those versions
# run deletes everything inside the install dir, user folders included. This name must
# never contain "windows": that keeps those versions from installing a release made
# after the fix, so they fail with "no release asset" and the user updates by hand once.
WINDOWS_ASSET_NAME = "engram-win-x64.zip"

# Top-level entries of a PyInstaller onedir build besides the launcher (engram.spec).
_BUILD_INTERNAL_DIR = "_internal"
# Names the Windows helper may delete inside the previous install. Anything else is left.
_SAFE_ENTRY_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
# Staged-update dir names: a version (what _download creates) or its "unknown" fallback.
_STAGED_VERSION_DIR = re.compile(r"\d+(\.\d+)*|unknown")


class UpdateStatus(StrEnum):
    """Update lifecycle state values."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    DOWNLOADING = "downloading"
    READY = "ready"
    SKIPPED = "skipped"
    ERROR = "error"


class UpdateError(EngramError):
    """Update check or application failed."""

    pass


class UpdateChecker:
    """Manages the full update lifecycle: check, download, verify, stage, restart.

    Instantiate once as a module-level singleton and call start() from the
    FastAPI lifespan.  All state is in-memory; the only DB interaction is reading
    and writing AppConfig.skipped_update_version.
    """

    def __init__(self) -> None:
        self.state: str = UpdateStatus.IDLE
        self.latest_version: str | None = None
        self.release_notes: str | None = None
        self.release_url: str | None = None
        # Notes for the version ALREADY RUNNING, for the what's-new modal. Kept
        # separate from latest_version/release_notes so nothing in the update flow
        # can mistake the running build for an available update.
        self.current_release_notes: str | None = None
        self.current_release_url: str | None = None
        self._current_release_fetched: bool = False
        self.download_progress: float = 0.0
        self.staging_path: Path | None = None
        self.error: str | None = None
        self.last_update_error: str | None = None
        self.last_update_success_version: str | None = None
        self._is_frozen: bool = is_frozen()
        self._current_version: str = __version__
        self._broadcaster = None  # injected by set_broadcaster()

    def set_broadcaster(self, broadcaster) -> None:
        """Inject the EventBroadcaster (called from main.py after import)."""
        self._broadcaster = broadcaster

    async def start(self) -> None:
        """Entry point — call once from the FastAPI lifespan as asyncio.create_task()."""
        self._consume_update_result_marker()
        self._prune_staging()
        if self._is_frozen and sys.platform == "win32":
            # Each leftover is a full build copy; delete off the event loop.
            await asyncio.to_thread(self._sweep_stale_swap_dirs, Path(sys.executable).parent)
        skipped_version = await self._load_skipped_version()
        await self._check(skipped_version)
        await self._fetch_current_release_notes()
        if self.last_update_error or self.last_update_success_version or self.current_release_notes:
            await self._broadcast()

    def _prune_staging(self) -> None:
        """Delete staged update dirs for versions already installed (<= current).

        Staging holds only not-yet-applied updates. Without this, every release ever
        downloaded accumulates under ~/.engram/update/ forever (e.g. 0.9.1 … 0.13.0).

        Each doomed dir is renamed to a ``.pruning-<pid>`` sibling *before* deletion,
        so an interrupted rmtree can only ever leave an obviously-temporary dir — never
        a half-deleted real-version dir that looks like a staged build (we observed a
        181-of-825-file ``0.15.2`` tree caught mid-rmtree). Leftover ``.pruning-*`` dirs
        from an earlier interrupted run are swept on entry. Best-effort: never let
        cleanup failures interfere with the update check.

        Only dirs named like the ones _download creates are touched (#825): anything
        else under the staging base is not Engram's to delete.
        """
        if not STAGING_BASE.exists():
            return
        pid = os.getpid()
        for child in list(STAGING_BASE.iterdir()):
            if not child.is_dir():
                continue
            name, sep, _ = child.name.partition(".pruning-")
            if not _STAGED_VERSION_DIR.fullmatch(name):
                continue
            if sep:
                shutil.rmtree(child, ignore_errors=True)  # leftover from an interrupted prune
                continue
            if self._is_older_or_equal(child.name, self._current_version):
                logger.info(f"Pruning stale staged update: {child}")
                doomed = child.with_name(f"{child.name}.pruning-{pid}")
                try:
                    os.replace(child, doomed)  # atomic rename aside; never leaves a partial
                except OSError:
                    doomed = child  # rename failed (locked?) — delete in place, best effort
                shutil.rmtree(doomed, ignore_errors=True)

    @staticmethod
    def _sweep_stale_swap_dirs(install_dir: Path) -> None:
        """Delete ``<install>.new-<version>`` dirs left beside the install by failed swaps.

        Helpers before #791 left one behind every time ``move_install`` failed, a full
        build copy per attempted version. Only ``.new-*`` dirs are removed: an
        ``.old-*`` dir can be the only runnable copy of the previous install after a
        failed restore, so it is left for the user. A ``.new-*`` dir holding anything
        but a build (#825) is left too. Best-effort, like _prune_staging.
        """
        parent = install_dir.parent
        if not parent.is_dir():
            return
        prefix = f"{install_dir.name}.new-"
        try:
            children = list(parent.iterdir())
        except OSError as exc:
            logger.debug(f"Could not list {parent} for stale update dirs: {exc}")
            return
        build = {Path(sys.executable).name.lower(), "engram.exe", _BUILD_INTERNAL_DIR}
        for child in children:
            if not (child.name.startswith(prefix) and child.is_dir() and not child.is_symlink()):
                continue
            try:
                foreign = [p.name for p in child.iterdir() if p.name.lower() not in build]
            except OSError as exc:
                logger.debug(f"Could not list {child}: {exc}")
                continue
            if foreign:
                logger.warning(f"Leaving {child}: it holds files Engram did not put there")
                continue
            logger.info(f"Removing leftover update copy: {child}")
            shutil.rmtree(child, ignore_errors=True)

    async def _load_skipped_version(self) -> str | None:
        """Load the skipped version preference from AppConfig."""
        try:
            from sqlmodel import select

            from app.models.app_config import AppConfig

            async with async_session() as session:
                result = await session.execute(select(AppConfig).limit(1))
                config = result.scalar_one_or_none()
                return config.skipped_update_version if config else None
        except Exception as exc:
            logger.debug(f"Could not load skipped_update_version: {exc}")
            return None

    def _consume_update_result_marker(self, path: Path | None = None) -> None:
        """Read + delete the Windows helper's result marker (~/.engram/update_result.txt)
        and translate it into last_update_error / last_update_success_version. Best-effort."""
        marker = path or (Path.home() / ".engram" / "update_result.txt")
        if not marker.exists():
            return
        data: dict[str, str] = {}
        try:
            # cmd writes the OS's error text in the console code page, not UTF-8; a
            # non-ASCII byte there must not raise out of start() and stop the checker.
            for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    data[key.strip()] = value.strip()
        except OSError as exc:
            logger.debug(f"Could not read update result marker: {exc}")
            return
        finally:
            # Best-effort: missing_ok only swallows FileNotFoundError; a PermissionError
            # (hardened/locked file) must not escape finally and crash the checker task.
            try:
                marker.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug(f"Could not delete update result marker: {exc}")

        version = data.get("version") or "?"
        if data.get("result") == "success":
            self.last_update_success_version = version
            logger.info(f"Update applied successfully to {version}")
            kept = data.get("kept")
            if kept:
                # The helper left the previous install in place because it held files
                # Engram did not install (#825). Say where, so the user can move them back.
                self.last_update_error = (
                    f"Updated to {version}. The previous install was kept at {kept} "
                    "because it holds files Engram didn't install. Move anything you "
                    "need out of it (and update the paths in Settings), then delete it."
                )
                logger.warning(f"Previous install kept at {kept}: it holds foreign files")
        elif data.get("result") == "failed":
            step = data.get("step") or "unknown"
            self.last_update_error = self._describe_update_failure(
                version, step, data.get("path") or "", data.get("reason") or ""
            )
            logger.warning(
                f"Update to {version} failed at step {step} "
                f"(path={data.get('path') or '?'}, reason={data.get('reason') or '?'}); "
                f"helper log: {marker.with_name('update_helper.log')}"
            )

    @staticmethod
    def _describe_update_failure(version: str, step: str, path: str, reason: str) -> str:
        """Turn the helper's failure marker into the dashboard's notice.

        Markers from helpers older than #791 carry no path or reason; those keep the
        original wording. Some steps (``wait_timeout``) have a reason but no path.
        """
        head = f"Update to {version} couldn't be applied automatically (step: {step})"
        detail = head
        if path:
            detail += f": {path}"
        if reason:
            detail += f": {reason.rstrip('.')}"
        if step == "restore_failed":
            # The previous install could not be moved back, so nothing will retry on
            # its own: the user has to rename the .old dir (the path) back by hand.
            return (
                f"{detail}. The previous version was left in that folder. If Engram "
                f"does not start, rename it back to its original name (drop the "
                f"'.old-{version}' ending), or download manually."
            )
        if step == "move_install" and path:
            return (
                f"{detail}. Another program is using that folder, often a browser or "
                "terminal that was started from it. Close it (restarting the "
                "browser is enough if Engram opened it) and the next update will go "
                "through, or download manually."
            )
        return f"{detail}. It'll retry on the next check, or download manually."

    async def _check(self, skipped_version: str | None) -> None:
        """Query GitHub for the latest release and decide whether to download."""
        self.state = UpdateStatus.CHECKING
        await self._broadcast()

        try:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"engram/{self._current_version}",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(GITHUB_API_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug(f"Update check failed (will retry next startup): {exc}")
            self.state = UpdateStatus.IDLE
            # Don't broadcast — stay silent on network errors
            return

        tag = data.get("tag_name", "").lstrip("v")
        if not tag:
            self.state = UpdateStatus.IDLE
            return

        if self._is_older_or_equal(tag, self._current_version):
            if tag == self._current_version:
                # This IS the running version's release, so the what's-new modal
                # already has what it needs; skip the tag-specific refetch.
                self._store_current_release(data)
            self.state = UpdateStatus.UP_TO_DATE
            return

        self.latest_version = tag
        self.release_notes = data.get("body")
        self.release_url = data.get("html_url")

        if tag == skipped_version:
            self.state = UpdateStatus.SKIPPED
            await self._broadcast()
            return

        if not self._is_frozen:
            # Dev mode: show banner but don't download; Restart button will be hidden
            self.state = UpdateStatus.READY
            await self._broadcast()
            logger.info(
                f"New version {tag} available (dev mode — download skipped). "
                f"Update at {self.release_url}"
            )
            return

        await self._download(data)

    async def _fetch_current_release_notes(self) -> None:
        """Populate release notes for the version already running.

        ``GITHUB_API_URL`` points at /releases/latest, which is the wrong release for
        a user sitting on 0.27.0 while 0.28.0 is out. The what's-new modal shows what
        changed in the build you are running, so it needs the tag-specific release.

        Failure is expected and silent: dev builds have no published release (404),
        and offline installs cannot reach GitHub at all. Both leave the fields None,
        which the frontend treats as "do not show the modal".
        """
        if self._current_release_fetched:
            return

        url = GITHUB_TAG_API_URL.format(version=self._current_version)
        try:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"engram/{self._current_version}",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug(
                f"Release notes for v{self._current_version} unavailable "
                f"({type(exc).__name__}): {exc}"
            )
            return

        self._store_current_release(data)

    def _store_current_release(self, data: dict) -> None:
        """Record the running version's release notes from a GitHub release payload."""
        self.current_release_notes = data.get("body")
        self.current_release_url = data.get("html_url")
        self._current_release_fetched = True

    @staticmethod
    def _is_older_or_equal(tag: str, current: str) -> bool:
        """Return True if tag is the same version or older than current."""
        try:
            t = tuple(int(x) for x in tag.split("."))
            c = tuple(int(x) for x in current.split("."))
            return t <= c
        except ValueError:
            return True  # Unparseable version: treat as not an upgrade

    async def _download(self, release_data: dict) -> None:
        """Stream-download the platform asset, verify checksum, extract."""
        self.state = UpdateStatus.DOWNLOADING
        await self._broadcast()

        asset = self._select_asset(release_data.get("assets", []))
        checksum_asset = next(
            (a for a in release_data.get("assets", []) if a["name"] == "sha256sums.txt"),
            None,
        )

        if not asset:
            logger.warning("No matching release asset found for this platform")
            self.state = UpdateStatus.ERROR
            self.error = "No release asset found for this platform"
            await self._broadcast()
            return

        manifest_asset = next(
            (
                a
                for a in release_data.get("assets", [])
                if a["name"] == self._manifest_name(asset["name"])
            ),
            None,
        )

        version = self.latest_version or "unknown"
        staging_dir = STAGING_BASE / version
        staging_dir.mkdir(parents=True, exist_ok=True)
        archive_path = staging_dir / asset["name"]

        try:
            checksums_text: str | None = None
            manifest_text: str | None = None
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                # Download checksum file first (small — ~200 bytes)
                if checksum_asset:
                    resp = await client.get(checksum_asset["browser_download_url"])
                    resp.raise_for_status()
                    checksums_text = resp.text

                # Download the per-build file manifest if the release ships one (small).
                if manifest_asset:
                    resp = await client.get(manifest_asset["browser_download_url"])
                    resp.raise_for_status()
                    manifest_text = resp.text

                # Stream-download the platform asset
                async with client.stream("GET", asset["browser_download_url"]) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0

                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                self.download_progress = downloaded / total

            # Verify checksum if file was available
            if checksums_text:
                self._verify_checksum(archive_path, asset["name"], checksums_text)

            # Extract into a temp dir, verify completeness, then atomically promote.
            # A partially-extracted tree must never become the staged engram/ dir that
            # apply_update() swaps over the install — that is how a truncated build
            # (missing certifi/cacert.pem and ~640 other files) reached the user.
            incoming = staging_dir / ".incoming"
            shutil.rmtree(incoming, ignore_errors=True)
            incoming.mkdir(parents=True, exist_ok=True)
            self._extract(archive_path, incoming)
            archive_path.unlink(missing_ok=True)  # Remove the archive; keep extracted dir

            extracted = incoming / "engram"
            if not extracted.is_dir():
                raise UpdateError(f"Archive did not contain an 'engram' directory: {asset['name']}")
            self._verify_extracted(extracted, manifest_text)

            # Promote the verified build. On POSIX os.replace() is an atomic rename(2);
            # on Windows it uses MoveFileEx, which is not POSIX-atomic. Either way there
            # is a brief window after rmtree(final) and before os.replace() where neither
            # dir exists — a crash there leaves staging without an engram/ dir (apply
            # refuses, the next run re-downloads, since state never reached READY). The
            # live install is never touched, so the consequence is tolerable.
            final = staging_dir / "engram"
            shutil.rmtree(final, ignore_errors=True)
            os.replace(extracted, final)
            shutil.rmtree(incoming, ignore_errors=True)

            # Persist the manifest beside the staged build so apply_update() can
            # re-verify completeness right before the swap (catches post-stage loss,
            # e.g. AV quarantine).
            manifest_path = staging_dir / "engram.manifest.sha256"
            if manifest_text is not None:
                manifest_path.write_text(manifest_text, encoding="utf-8")
            else:
                manifest_path.unlink(missing_ok=True)

            self.staging_path = staging_dir
            self.state = UpdateStatus.READY
            self.download_progress = 1.0
            logger.info(f"Update {version} staged at {staging_dir}")
            await self._broadcast()

        except UpdateError as exc:
            # A rejection here (checksum/integrity failure) is the main guard against
            # staging a bad build — leave a trace so a support case has something to grep.
            logger.warning(f"Staged update rejected: {exc}", exc_info=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.state = UpdateStatus.ERROR
            self.error = str(exc)
            await self._broadcast()
        except Exception as exc:
            logger.error(f"Update download failed: {exc}", exc_info=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.state = UpdateStatus.ERROR
            self.error = str(exc)
            await self._broadcast()

    def _select_asset(self, assets: list[dict]) -> dict | None:
        """Return the platform-appropriate release asset, or None if not found.

        On Windows the renamed ``WINDOWS_ASSET_NAME`` wins over the legacy
        ``engram-windows-x64.zip`` (see the constant for why it was renamed).

        On Linux the architecture matters: we ship both an x86_64 bundle
        (``engram-linux-x64.tar.gz``) and an aarch64 bundle
        (``engram-linux-arm64.tar.gz``), so an arm64 host (e.g. a Jetson) must
        not pick up the x86_64 tarball and vice versa.
        """
        system = sys.platform
        is_arm = platform.machine().lower() in ("aarch64", "arm64")
        if system == "win32":
            renamed = next((a for a in assets if a.get("name") == WINDOWS_ASSET_NAME), None)
            if renamed:
                return renamed
        for asset in assets:
            name = asset.get("name", "").lower()
            if system == "win32" and name.endswith(".zip") and "windows" in name:
                return asset
            if system == "linux" and name.endswith(".tar.gz") and "linux" in name:
                name_is_arm = "arm64" in name or "aarch64" in name
                if name_is_arm == is_arm:
                    return asset
                continue
            if (
                system == "darwin"
                and name.endswith(".tar.gz")
                and ("macos" in name or "darwin" in name)
            ):
                return asset
        return None

    def _verify_checksum(self, file_path: Path, filename: str, checksums_text: str) -> None:
        """Verify SHA256 checksum from sha256sums.txt. Raises UpdateError on mismatch.

        sha256sums.txt format (standard sha256sum output): ``<hash>  <filename>``
        """
        expected: str | None = None
        for line in checksums_text.strip().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == filename:
                expected = parts[0]
                break

        if not expected:
            logger.debug(f"No checksum entry for {filename} — skipping verification")
            return

        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)

        actual = h.hexdigest()
        if actual != expected:
            logger.error(
                f"Checksum mismatch for {filename}: expected {expected[:12]}…, got {actual[:12]}…"
            )
            raise UpdateError(f"Checksum mismatch for {filename}")

        logger.debug(f"Checksum verified for {filename}")

    def _extract(self, archive_path: Path, dest_dir: Path) -> None:
        """Extract .tar.gz or .zip to dest_dir."""
        name = archive_path.name
        if name.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(dest_dir, filter="data")
        elif name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.infolist():
                    member_path = (dest_dir / member.filename).resolve()
                    if not str(member_path).startswith(str(dest_dir.resolve())):
                        raise UpdateError(f"Unsafe zip entry rejected: {member.filename}")
                zf.extractall(dest_dir)
        else:
            raise UpdateError(f"Unknown archive format: {name}")

    @staticmethod
    def _manifest_name(asset_name: str) -> str:
        """Map a platform asset name to its file-manifest asset name.

        ``engram-windows-x64.zip`` -> ``engram-windows-x64.manifest.sha256``
        ``engram-linux-x64.tar.gz`` -> ``engram-linux-x64.manifest.sha256``
        """
        base = asset_name
        for suffix in (".tar.gz", ".zip"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base}.manifest.sha256"

    @staticmethod
    def _manifest_paths(manifest_text: str) -> list[str]:
        """Parse relative file paths from a ``sha256sum``-style manifest.

        Each line is ``<64-hex-hash>  <relative/path>`` (two spaces; binary-mode
        entries prefix the path with ``*``). Paths are relative to the build's
        ``engram/`` directory. Blank/garbage lines are skipped.
        """
        paths: list[str] = []
        for line in manifest_text.strip().splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            rel = parts[1].lstrip("*").strip()
            if rel.startswith("./"):
                rel = rel[2:]  # tolerate sha256sum's leading ./ (BSD find output)
            if rel:
                paths.append(rel.replace("/", os.sep))
        return paths

    def _verify_extracted(self, engram_dir: Path, manifest_text: str | None) -> None:
        """Verify the extracted build is complete. Raises UpdateError if not.

        Two layers:
          * Always: sentinel files every healthy onedir build must contain. This
            catches a grossly truncated extraction even for releases that ship no
            manifest (the failure that delivered a build with no certifi/cacert.pem).
          * If a manifest is present: every file it lists must exist, which catches
            arbitrary missing files (e.g. 181 of 825 extracted).
        """
        binary = "engram.exe" if sys.platform == "win32" else "engram"
        sentinels = [
            engram_dir / binary,
            engram_dir / "_internal" / "base_library.zip",
            engram_dir / "_internal" / "certifi" / "cacert.pem",
        ]
        missing_sentinels = [str(p.relative_to(engram_dir)) for p in sentinels if not p.exists()]
        if missing_sentinels:
            raise UpdateError(
                f"Staged build is incomplete (missing {', '.join(missing_sentinels)}); "
                "refusing to apply."
            )

        if not manifest_text:
            logger.debug("No manifest for staged build — sentinel check only")
            return

        missing = [
            rel for rel in self._manifest_paths(manifest_text) if not (engram_dir / rel).exists()
        ]
        if missing:
            sample = ", ".join(missing[:5])
            raise UpdateError(
                f"Staged build is missing {len(missing)} manifest file(s) (e.g. {sample}); "
                "refusing to apply."
            )
        logger.debug("Staged build verified complete against manifest")

    def get_status(self) -> dict:
        """Return a JSON-serialisable status dict for GET /api/updates/status."""
        return {
            "state": self.state,
            "current_version": self._current_version,
            "latest_version": self.latest_version,
            "release_notes": self.release_notes,
            "release_url": self.release_url,
            "current_release_notes": self.current_release_notes,
            "current_release_url": self.current_release_url,
            "download_progress": (
                self.download_progress if self.state == UpdateStatus.DOWNLOADING else None
            ),
            "error": self.error,
            "last_update_error": self.last_update_error,
            "last_update_success_version": self.last_update_success_version,
            "is_frozen": self._is_frozen,
        }

    async def skip_version(self, version: str) -> None:
        """Persist the user's skip preference and update in-memory state."""
        try:
            from sqlmodel import select

            from app.models.app_config import AppConfig

            async with async_session() as session:
                result = await session.execute(select(AppConfig).limit(1))
                config = result.scalar_one_or_none()
                if config:
                    config.skipped_update_version = version
                    await session.commit()
        except Exception as exc:
            logger.error(f"Failed to persist skipped version: {exc}", exc_info=True)
            raise

        if self.latest_version == version:
            self.state = UpdateStatus.SKIPPED
            await self._broadcast()

    async def apply_update(self) -> None:
        """Apply the staged update. Returns normally then exits the process.

        Called via BackgroundTasks so the HTTP 200 response is sent first.
        Raises ConfigurationError in dev mode; raises UpdateError if no update is ready
        or if active jobs prevent restart.
        """
        if not self._is_frozen:
            raise ConfigurationError(
                "Updates can only be applied in frozen builds. "
                f"Download manually from {self.release_url or 'GitHub'}."
            )

        if self.state != UpdateStatus.READY:
            raise UpdateError("No staged update is ready to apply.")

        await self._check_no_active_jobs()

        # Re-verify the staged build is still complete right before the swap. It
        # passed verification at download time, but files can disappear afterwards
        # (AV quarantine, partial deletion). Never swap an incomplete build over a
        # working install. staging_path is always set once state == READY, so a None
        # here is a bug, not a "skip verification" case — fail loudly.
        if self.staging_path is None:
            raise UpdateError("staging_path is unset despite READY state — refusing to apply.")
        manifest_path = self.staging_path / "engram.manifest.sha256"
        manifest_text = (
            manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None
        )
        try:
            self._verify_extracted(self.staging_path / "engram", manifest_text)
        except UpdateError as exc:
            # The build was complete at download time but isn't now (e.g. AV quarantine).
            # Drop out of READY and clear the staged path so the UI stops offering a
            # broken update and a fresh download is required, then re-raise for the API.
            logger.warning(f"Pre-apply verification failed: {exc}", exc_info=True)
            self.state = UpdateStatus.ERROR
            self.error = "Staged build is incomplete (possibly quarantined); re-download required."
            self.staging_path = None
            await self._broadcast()
            raise

        if sys.platform == "win32":
            await self._check_install_dir()
            self._restart_windows()
        else:
            self._restart_linux_macos()

    async def _check_no_active_jobs(self) -> None:
        """Raise UpdateError if any uncleared job is actively doing disc I/O."""
        from sqlmodel import select

        from app.models import DiscJob, JobState

        # REVIEW_NEEDED is excluded: disc is already out, job is parked awaiting user input.
        # The .where(cleared_at IS NULL) below limits the check to jobs still visible on the
        # dashboard — dismissed jobs (cleared_at IS NOT NULL) are implicitly excluded and must
        # not ghost-block an update restart.
        active_states = [
            JobState.IDENTIFYING,
            JobState.BACKING_UP,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        ]
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob)
                .where(DiscJob.state.in_(active_states))
                .where(DiscJob.cleared_at.is_(None))
                .limit(1)
            )
            if result.scalar_one_or_none():
                raise UpdateError(
                    "A disc operation is in progress. Please wait until it finishes before restarting."
                )

    async def _check_install_dir(self) -> None:
        """Refuse to update when the install dir holds anything Engram didn't install (#825).

        The Windows swap moves the whole install dir aside, so a library, staging dir or
        any other folder kept inside it goes with it. Helpers before #825 then deleted
        the moved-aside dir with ``rmdir /S /Q``, which is how a 300 GB TV library
        vanished. The helper no longer deletes anything it can't name, but the files
        would still end up in the ``.old`` dir, away from the paths Settings points at,
        so the update is refused up front with the list of what is in the way. The
        notice also goes to the dashboard banner, since apply runs after the HTTP reply.
        Only Windows: the POSIX path replaces the binary and leaves its folder alone.
        """
        if sys.platform != "win32":
            return
        install_dir = Path(sys.executable).parent
        new_build = self.staging_path / "engram" if self.staging_path else None
        data_paths = await self._configured_data_paths()
        conflicts = await asyncio.to_thread(
            self._install_dir_conflicts,
            install_dir,
            new_build,
            Path(sys.executable).name,
            data_paths,
        )
        if not conflicts:
            return
        shown = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            shown += f" and {len(conflicts) - 5} more"
        message = (
            f"Engram can't update itself: its install folder {install_dir} also holds "
            f"things it didn't install ({shown}). Updating replaces that whole folder, so "
            "move them to a folder outside it first (and update the paths in Settings), "
            "then try again."
        )
        logger.warning(f"Update refused: {message}")
        self.last_update_error = message
        await self._broadcast()
        raise UpdateError(message)

    async def _configured_data_paths(self) -> list[str]:
        """Every folder the user's data lives in: the configured paths, the DB, ~/.engram."""
        paths = [str(Path.home() / ".engram")]
        db_url = settings.database_url
        if db_url.startswith("sqlite") and ":///" in db_url:
            paths.append(db_url.split(":///", 1)[1])
        try:
            from sqlmodel import select

            from app.models.app_config import AppConfig

            async with async_session() as session:
                result = await session.execute(select(AppConfig).limit(1))
                config = result.scalar_one_or_none()
        except Exception as exc:
            # The install dir's own contents are still checked; this only adds paths.
            logger.warning(f"Could not read configured paths for the update check: {exc}")
            return paths
        if config is not None:
            for field in (
                "staging_path",
                "library_movies_path",
                "library_tv_path",
                "import_watch_path",
                "backup_path",
                "subtitles_cache_path",
                "discdb_export_path",
            ):
                value = getattr(config, field, None)
                if value:
                    paths.append(value)
        return paths

    @staticmethod
    def _install_dir_conflicts(
        install_dir: Path,
        new_build: Path | None,
        exe_name: str,
        data_paths: list[str | None],
    ) -> list[str]:
        """List what in or under ``install_dir`` is not part of an Engram build.

        Two kinds: top-level entries that neither the launcher, ``_internal`` nor the
        new build accounts for, and configured data paths that sit inside the install
        dir (even ones not created yet). Each is reported once, as a path.
        """

        def norm(path: str | Path) -> str:
            return os.path.normcase(os.path.realpath(path))

        ours = {exe_name.lower(), _BUILD_INTERNAL_DIR}
        if new_build is not None:
            try:
                ours |= {p.name.lower() for p in new_build.iterdir()}
            except OSError:
                pass

        conflicts: list[str] = []
        covered: list[str] = []
        for entry in sorted(install_dir.iterdir(), key=lambda p: p.name):
            if entry.name.lower() not in ours:
                conflicts.append(str(entry))
                covered.append(norm(entry))

        root = norm(install_dir)
        for raw in data_paths:
            if not raw:
                continue
            shown = Path(os.path.expanduser(raw))
            path = norm(shown)
            if not (path == root or path.startswith(root.rstrip(os.sep) + os.sep)):
                continue
            if any(path == c or path.startswith(c + os.sep) for c in covered):
                continue
            conflicts.append(str(shown))
            covered.append(path)
        return conflicts

    def _restart_linux_macos(self) -> None:
        """Replace binary in-place and exec the new version (Linux/macOS).

        The archive extracts to <staging_dir>/engram/engram (same structure as
        the release tarball: ``tar -C backend/dist engram``).
        """
        assert self.staging_path is not None
        new_binary = self.staging_path / "engram" / "engram"
        if not new_binary.exists():
            raise UpdateError(f"New binary not found at {new_binary}")

        logger.info(f"Applying update: {sys.executable} -> {new_binary}")
        shutil.copy2(str(new_binary), sys.executable)
        os.chmod(sys.executable, 0o700)
        # Re-exec re-runs run.py, which would open a second browser tab; --updated suppresses
        # it so the already-open tab just reconnects.
        argv = sys.argv if "--updated" in sys.argv else [*sys.argv, "--updated"]
        os.execv(sys.executable, argv)

    def _restart_windows(self) -> None:
        """Swap the staged build in for the live install via a detached .bat helper.

        The helper waits for this process to exit, then performs an atomic, verified,
        recoverable swap (see ``_render_update_bat``): robocopy the staged tree to a
        sibling ``.new`` dir, verify sentinels, swap with two same-volume renames,
        relaunch, and roll back to the previous install on any failure — logging every
        step to ``~/.engram/update_helper.log``.

        This replaces the old in-place ``xcopy /E`` over the live install, which had no
        exit-code check, no verification, and no rollback: a single destination file
        still locked just after the old process exited left a half-old/half-new install
        that wouldn't launch, with no way back (the recurring "restart bricks my
        install, I have to re-download" failure).

        Spawned with CREATE_BREAKAWAY_FROM_JOB (via ``_spawn_detached_helper``) so a
        kill-on-close Job Object can't terminate the helper before it swaps. The bat
        uses ``ping`` (not ``timeout``) for delays — ``timeout`` aborts immediately
        ("input redirection is not supported") in the console-less DETACHED_PROCESS
        context.
        """
        assert self.staging_path is not None
        new_engram_dir = self.staging_path / "engram"
        if not new_engram_dir.exists():
            raise UpdateError(f"Staged update directory not found: {new_engram_dir}")

        install_dir = Path(sys.executable).parent
        version = self.latest_version or "new"
        # The ``.new``/``.old`` siblings live next to the INSTALL (not staging), so the
        # swap is two fast atomic same-volume renames even when staging is on a
        # different drive than the install.
        new_dir = install_dir.parent / f"{install_dir.name}.new-{version}"
        # Never reuse an existing .old dir: one kept by an earlier update can hold the
        # user's files (#825), and the helper refuses to swap rather than delete it.
        old_dir = install_dir.parent / f"{install_dir.name}.old-{version}"
        suffix = 2
        while os.path.lexists(old_dir):
            old_dir = install_dir.parent / f"{install_dir.name}.old-{version}-{suffix}"
            suffix += 1
        # What the helper may delete from the old dir once the new build is in: the
        # running build's own top-level entries, nothing else.
        ours = {Path(sys.executable).name.lower(), _BUILD_INTERNAL_DIR}
        ours |= {p.name.lower() for p in new_engram_dir.iterdir()}
        old_build_entries = [p.name for p in install_dir.iterdir() if p.name.lower() in ours]
        log_path = Path.home() / ".engram" / "update_helper.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result_path = Path.home() / ".engram" / "update_result.txt"

        temp_dir = Path(os.environ.get("TEMP", "C:\\Temp"))
        bat_path = temp_dir / "engram_update.bat"
        pid = os.getpid()

        bat_content = self._render_update_bat(
            src=str(new_engram_dir),
            install=str(install_dir),
            new_dir=str(new_dir),
            old_dir=str(old_dir),
            log_path=str(log_path),
            exe=Path(sys.executable).name,
            pid=pid,
            version=version,
            result_path=str(result_path),
            old_build_entries=old_build_entries,
        )
        # cmd.exe requires CRLF line endings (LF-only batch files mishandle labels/goto);
        # write them explicitly so the helper is valid regardless of host platform.
        with open(bat_path, "w", newline="\r\n") as f:
            f.write(bat_content)

        # Log where the helper will write its own log, plus the resolved home, so a
        # support case can reconcile "no update_helper.log" against where it actually
        # looked (HOME/USERPROFILE drift is a known way the file appears "missing").
        logger.info(f"Launching update helper: {bat_path}")
        logger.info(f"Update helper log -> {log_path} (home={Path.home()})")
        self._spawn_detached_helper(["cmd", "/c", str(bat_path)])
        os._exit(0)  # Hard exit: avoids asyncio catching SystemExit

    @staticmethod
    def _render_update_bat(
        *,
        src: str,
        install: str,
        new_dir: str,
        old_dir: str,
        log_path: str,
        exe: str,
        pid: int,
        version: str,
        result_path: str,
        move_attempts: int = 6,
        old_build_entries: Sequence[str] | None = None,
    ) -> str:
        """Render the Windows update helper batch script (pure — testable off-Windows).

        Spawned with CREATE_NO_WINDOW (hidden console) so every command — including the
        `tasklist | find` wait pipe — works; DETACHED_PROCESS (no console) deadlocked it.

        After the parent PID exits (bounded ~10s wait, filtered on PID *and* image name so a
        reused PID can't wedge it): robocopy the staged build to a sibling `.new`, verify
        sentinels, swap via two same-volume renames, relaunch with `--updated` (suppresses a
        duplicate browser tab; the existing tab reconnects), and roll back on any failure. Each
        rename goes through `:retry_move`, which retries a busy directory with backoff up to
        ``move_attempts`` times. Every terminal path writes a `result=...` marker to %RESULT%
        that the relaunched app reads to toast success / show a failure notice; a failure also
        records which path it was working on and the OS's reason. Logs every step to %LOG%;
        only deletes itself on success.

        Nothing the helper did not create is ever deleted recursively (#825). After the
        swap it removes only ``old_build_entries`` (the previous build's own top-level
        entries; default: the launcher and ``_internal``) from the old dir, then the dir
        itself only if that left it empty. Anything else keeps the dir, and its path goes
        into the marker as ``kept=``. An existing ``.old`` dir is never cleared: the swap
        fails instead (``_restart_windows`` picks an unused name).
        """
        entries = [exe, _BUILD_INTERNAL_DIR] if old_build_entries is None else old_build_entries
        # Only plain names: a rendered `..`, `&` or separator could reach outside the dir.
        entries = [e for e in entries if _SAFE_ENTRY_NAME.fullmatch(e) and ".." not in e]
        cleanup = []
        for entry in entries:
            # rmdir takes a directory, del a file; each is a no-op on the other kind.
            cleanup.append(f'rmdir /S /Q "%OLDDIR%\\{entry}" >nul 2>&1')
            cleanup.append(f'del /F /Q "%OLDDIR%\\{entry}" >nul 2>&1')
        lines = [
            "@echo off",
            "setlocal enableextensions",
            f'set "SRC={src}"',
            f'set "INSTALL={install}"',
            f'set "NEWDIR={new_dir}"',
            f'set "OLDDIR={old_dir}"',
            f'set "LOG={log_path}"',
            f'set "RESULT={result_path}"',
            f'set "VER={version}"',
            f'set "EXE=%INSTALL%\\{exe}"',
            'set "MOVEOUT=%TEMP%\\engram_update_move.txt"',
            f"set /a MOVE_ATTEMPTS={move_attempts}",
            'set "STEP=start"',
            'set "FAILPATH="',
            'set "REASON="',
            f'echo [engram-update] start (pid {pid}) > "%LOG%"',
            'echo [engram-update] cwd=%CD% >> "%LOG%"',
            'echo [engram-update] INSTALL=%INSTALL% NEWDIR=%NEWDIR% OLDDIR=%OLDDIR% >> "%LOG%"',
            'echo [engram-update] SRC=%SRC% EXE=%EXE% >> "%LOG%"',
            # --- wait for parent to exit; bounded (~10s), filtered on PID + image name ---
            "set /a WAITED=0",
            ":wait",
            f'tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {exe}" 2>NUL | find /I "{pid}" >NUL',
            "if errorlevel 1 goto exited",
            "set /a WAITED+=1",
            "if %WAITED% GEQ 10 (",
            '    echo [engram-update] timed out waiting for exit >> "%LOG%"',
            '    set "STEP=wait_timeout"',
            '    set "REASON=Engram did not exit within 10s"',
            "    goto fail",
            ")",
            "ping -n 2 127.0.0.1 >nul",
            "goto wait",
            ":exited",
            'echo [engram-update] process exited; waiting for handles >> "%LOG%"',
            "ping -n 3 127.0.0.1 >nul",
            "ping -n 3 127.0.0.1 >nul",
            # Move our cwd off the install dir before the swap (a process's cwd can't be renamed).
            'cd /d "%TEMP%"',
            'echo [engram-update] cwd(after cd)=%CD% >> "%LOG%"',
            # --- copy staged build to a sibling of the install (never in place) ---
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            'echo [engram-update] robocopy "%SRC%" to "%NEWDIR%" >> "%LOG%"',
            'robocopy "%SRC%" "%NEWDIR%" /MIR /R:3 /W:2 /NP /NFL /NDL >> "%LOG%" 2>&1',
            "set RC=%ERRORLEVEL%",
            'echo [engram-update] robocopy exit=%RC% >> "%LOG%"',
            'set "STEP=robocopy"',
            'set "FAILPATH=%NEWDIR%"',
            'set "REASON=robocopy exit code %RC%"',
            "if %RC% GEQ 8 goto fail",
            # --- verify the copied tree before touching the live install ---
            'set "STEP=verify"',
            'set "REASON=the copied build is incomplete"',
            f'if not exist "%NEWDIR%\\{exe}" goto fail',
            'if not exist "%NEWDIR%\\_internal\\base_library.zip" goto fail',
            'if not exist "%NEWDIR%\\_internal\\certifi\\cacert.pem" goto fail',
            'if not exist "%NEWDIR%\\_internal\\app\\static\\index.html" goto fail',
            'set "REASON="',
            # --- atomic swap: install -> .old, .new -> install ---
            # An existing .old dir is never cleared: it can hold the user's files (#825).
            'if exist "%OLDDIR%" goto old_dir_taken',
            'echo [engram-update] swapping install >> "%LOG%"',
            'set "STEP=move_install"',
            'set "FAILPATH=%INSTALL%"',
            'call :retry_move "%INSTALL%" "%OLDDIR%"',
            "if errorlevel 1 goto fail_no_swap",
            'set "STEP=move_new"',
            'set "FAILPATH=%NEWDIR%"',
            'call :retry_move "%NEWDIR%" "%INSTALL%"',
            "if errorlevel 1 goto restore_old",
            # --- success: remove the previous build's own entries, nothing else ---
            'echo [engram-update] swapped; removing the previous build >> "%LOG%"',
            'set "STEP=cleanup"',
            *cleanup,
            # Non-recursive: removes the old dir only if nothing but the build was in it.
            'rmdir "%OLDDIR%" >nul 2>&1',
            'echo result=success>"%RESULT%"',
            'echo version=%VER%>>"%RESULT%"',
            'if not exist "%OLDDIR%" goto old_dir_removed',
            'echo [engram-update] kept "%OLDDIR%": it holds files Engram did not install >> "%LOG%"',
            # Delayed expansion, like the failure marker below: the path is user-chosen.
            "setlocal enabledelayedexpansion",
            '>>"%RESULT%" echo(kept=!OLDDIR!',
            "endlocal",
            ":old_dir_removed",
            'echo [engram-update] success; relaunching >> "%LOG%"',
            'start "" /D "%INSTALL%" "%EXE%" --updated',
            'echo [engram-update] done (success) >> "%LOG%"',
            '(goto) 2>nul & del "%~f0"',
            # --- rollback paths (bat NOT deleted — left with the log for diagnosis) ---
            ":restore_old",
            'echo [engram-update] swap failed; restoring previous install >> "%LOG%"',
            'set "STEP=swap_restore"',
            # The install path should be gone after the first move. Something there now is
            # not ours to delete: only an empty dir is removed, else the restore fails.
            'rmdir "%INSTALL%" >nul 2>&1',
            'call :retry_move "%OLDDIR%" "%INSTALL%"',
            "if errorlevel 1 goto restore_failed",
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            "goto relaunch_old",
            # Both builds are kept: the .old dir is the previous install and the .new dir
            # the update, so the user can rename either one back by hand.
            ":restore_failed",
            'set "STEP=restore_failed"',
            'set "FAILPATH=%OLDDIR%"',
            'echo [engram-update] restore failed; kept "%OLDDIR%" and "%NEWDIR%" >> "%LOG%"',
            "goto relaunch_old",
            ":old_dir_taken",
            'echo [engram-update] "%OLDDIR%" already exists; not touching it >> "%LOG%"',
            'set "STEP=old_dir_taken"',
            'set "FAILPATH=%OLDDIR%"',
            'set "REASON=a folder with that name already exists"',
            "goto fail_no_swap",
            ":fail_no_swap",
            'echo [engram-update] could not move install aside; left untouched >> "%LOG%"',
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            "goto relaunch_old",
            ":fail",
            'echo [engram-update] failed at step %STEP%; install untouched >> "%LOG%"',
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            "goto relaunch_old",
            ":relaunch_old",
            'echo [engram-update] rolled back to previous install >> "%LOG%"',
            'echo result=failed>"%RESULT%"',
            'echo version=%VER%>>"%RESULT%"',
            'echo step=%STEP%>>"%RESULT%"',
            # These two values come from outside the script (an install dir like
            # `D:\R&D\engram`, a localized OS message), so they are written with delayed
            # expansion: `!VAR!` expands after cmd has parsed `&`, `|`, `<`, `>` and `)`,
            # so those characters land in the file instead of splitting the command.
            # Redirect first: a value ending in a digit would otherwise be read as a
            # handle number (`...1>>file`). `echo(` writes an empty value as `key=`.
            "setlocal enabledelayedexpansion",
            '>>"%RESULT%" echo(path=!FAILPATH!',
            '>>"%RESULT%" echo(reason=!REASON!',
            "endlocal",
            'start "" /D "%INSTALL%" "%EXE%" --updated',
            'echo [engram-update] done (rolled back) >> "%LOG%"',
            "endlocal",
            "goto :eof",
            # --- :retry_move <src> <dst>: rename with backoff, ERRORLEVEL 1 on give-up ---
            # Windows refuses to rename a directory while any process has it as its
            # working directory or holds a file open inside it (#791). Short-lived holders
            # (antivirus scanning a fresh copy, a process that is still exiting) let go
            # within seconds, so retry with a doubling wait capped at 8s (2+4+8+8+8 = 30s
            # over the default six attempts). On give-up, the first line of move's output
            # (the OS message, e.g. "...being used by another process.") lands in REASON.
            ":retry_move",
            "set /a TRIES=0",
            ":retry_move_again",
            'move "%~1" "%~2" > "%MOVEOUT%" 2>&1',
            "if errorlevel 1 goto retry_move_busy",
            'echo [engram-update] moved "%~1" to "%~2" >> "%LOG%"',
            "exit /b 0",
            ":retry_move_busy",
            'type "%MOVEOUT%" >> "%LOG%"',
            "set /a TRIES+=1",
            "if %TRIES% GEQ %MOVE_ATTEMPTS% goto retry_move_give_up",
            'set /a "BACKOFF=1<<TRIES"',
            "if %BACKOFF% GTR 8 set /a BACKOFF=8",
            "set /a PINGS=BACKOFF+1",
            'echo [engram-update] "%~1" is in use; retry %TRIES% in %BACKOFF%s >> "%LOG%"',
            "ping -n %PINGS% 127.0.0.1 >nul",
            "goto retry_move_again",
            ":retry_move_give_up",
            'echo [engram-update] giving up on "%~1" after %TRIES% attempts >> "%LOG%"',
            'set "REASON="',
            'set /p REASON=<"%MOVEOUT%"',
            "exit /b 1",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _spawn_detached_helper(args: list[str]) -> None:
        """Spawn a helper that outlives this process and any owning Job Object.

        Tries CREATE_BREAKAWAY_FROM_JOB first so the helper escapes a kill-on-close
        job. The flag is a no-op when we're not in a job; if we're in a job that
        forbids breakaway, CreateProcess raises and we fall back to a plain detached
        spawn (best effort — that's the pre-existing behavior).
        """
        # Resolve the Windows creationflags via getattr so this module stays
        # importable and unit-testable on non-Windows CI, where they're absent.
        # CREATE_NO_WINDOW gives the helper a HIDDEN console. DETACHED_PROCESS (no console
        # at all) makes `tasklist` emit nothing and a cmd pipe (`tasklist | find`) deadlock,
        # which hung the wait loop forever and was the real "restart bricks my install" bug.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        base = no_window | new_group
        # Pin a neutral working directory. Without an explicit cwd the detached helper
        # inherits engram's cwd — the install dir for a double-clicked onedir exe — and a
        # process whose cwd is a directory holds an open handle that blocks renaming it,
        # so the helper's own `move install -> .old` fails and the swap silently rolls
        # back. The bat also `cd /d "%TEMP%"`s for defense in depth.
        safe_cwd = tempfile.gettempdir()
        try:
            subprocess.Popen(
                args, shell=False, close_fds=True, cwd=safe_cwd, creationflags=base | breakaway
            )
        except OSError as exc:
            logger.warning(f"Update helper breakaway spawn failed ({exc}); retrying detached")
            subprocess.Popen(args, shell=False, close_fds=True, cwd=safe_cwd, creationflags=base)

    async def _broadcast(self) -> None:
        """Push current update state to all WebSocket clients."""
        if self._broadcaster is None:
            return
        try:
            await self._broadcaster.broadcast_update_status(
                state=self.state,
                latest_version=self.latest_version,
                release_notes=self.release_notes,
                release_url=self.release_url,
                current_release_notes=self.current_release_notes,
                current_release_url=self.current_release_url,
                error=self.error,
                last_update_error=self.last_update_error,
                last_update_success_version=self.last_update_success_version,
            )
        except Exception as exc:
            logger.debug(f"Failed to broadcast update status: {exc}")


# Module-level singleton — mirrors the job_manager, curator pattern
update_checker = UpdateChecker()
