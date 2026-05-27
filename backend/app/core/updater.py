"""Auto-update checker for Engram.

Checks GitHub Releases on startup, downloads the new version in the background,
verifies the SHA256 checksum, and stages it for a user-triggered restart.

Platform restart strategies:
  Linux/macOS: shutil.copy2 + os.execv (replaces process image in-place)
  Windows:     .bat helper that xcopy-swaps after the main process exits
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx
from loguru import logger

from app import __version__
from app.core.errors import ConfigurationError, EngramError
from app.database import async_session

GITHUB_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/latest"
STAGING_BASE = Path.home() / ".engram" / "update"


class UpdateStatus(str):
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
        self.download_progress: float = 0.0
        self.staging_path: Path | None = None
        self.error: str | None = None
        self._is_frozen: bool = getattr(sys, "frozen", False)
        self._current_version: str = __version__
        self._broadcaster = None  # injected by set_broadcaster()

    def set_broadcaster(self, broadcaster) -> None:
        """Inject the EventBroadcaster (called from main.py after import)."""
        self._broadcaster = broadcaster

    async def start(self) -> None:
        """Entry point — call once from the FastAPI lifespan as asyncio.create_task()."""
        skipped_version = await self._load_skipped_version()
        await self._check(skipped_version)

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

        version = self.latest_version or "unknown"
        staging_dir = STAGING_BASE / version
        staging_dir.mkdir(parents=True, exist_ok=True)
        archive_path = staging_dir / asset["name"]

        try:
            checksums_text: str | None = None
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                # Download checksum file first (small — ~200 bytes)
                if checksum_asset:
                    resp = await client.get(checksum_asset["browser_download_url"])
                    resp.raise_for_status()
                    checksums_text = resp.text

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

            # Extract archive
            self._extract(archive_path, staging_dir)
            archive_path.unlink(missing_ok=True)  # Remove the archive; keep extracted dir

            self.staging_path = staging_dir
            self.state = UpdateStatus.READY
            self.download_progress = 1.0
            logger.info(f"Update {version} staged at {staging_dir}")
            await self._broadcast()

        except UpdateError:
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.state = UpdateStatus.ERROR
            await self._broadcast()
        except Exception as exc:
            logger.error(f"Update download failed: {exc}", exc_info=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.state = UpdateStatus.ERROR
            self.error = str(exc)
            await self._broadcast()

    def _select_asset(self, assets: list[dict]) -> dict | None:
        """Return the platform-appropriate release asset, or None if not found."""
        platform = sys.platform
        for asset in assets:
            name = asset.get("name", "").lower()
            if platform == "win32" and name.endswith(".zip") and "windows" in name:
                return asset
            if platform == "linux" and name.endswith(".tar.gz") and "linux" in name:
                return asset
            if (
                platform == "darwin"
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
                zf.extractall(dest_dir)
        else:
            raise UpdateError(f"Unknown archive format: {name}")

    def get_status(self) -> dict:
        """Return a JSON-serialisable status dict for GET /api/updates/status."""
        return {
            "state": self.state,
            "current_version": self._current_version,
            "latest_version": self.latest_version,
            "release_notes": self.release_notes,
            "release_url": self.release_url,
            "download_progress": (
                self.download_progress if self.state == UpdateStatus.DOWNLOADING else None
            ),
            "error": self.error,
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

        if sys.platform == "win32":
            self._restart_windows()
        else:
            self._restart_linux_macos()

    async def _check_no_active_jobs(self) -> None:
        """Raise UpdateError if any job is currently ripping/matching/organizing."""
        from sqlmodel import select

        from app.models import DiscJob, JobState

        active_states = [
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        ]
        async with async_session() as session:
            result = await session.execute(
                select(DiscJob).where(DiscJob.state.in_(active_states)).limit(1)
            )
            if result.scalar_one_or_none():
                raise UpdateError(
                    "A disc operation is in progress. Please wait until it finishes before restarting."
                )

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
        os.chmod(sys.executable, 0o755)
        # os.execv replaces the process image — same PID, same port, zero downtime
        os.execv(sys.executable, sys.argv)

    def _restart_windows(self) -> None:
        """Write a .bat helper that xcopy-swaps the install dir after we exit (Windows).

        The .bat polls the current PID until it disappears, then copies the staged
        directory over the install directory and relaunches engram.exe.
        """
        assert self.staging_path is not None
        new_engram_dir = self.staging_path / "engram"
        if not new_engram_dir.exists():
            raise UpdateError(f"Staged update directory not found: {new_engram_dir}")

        install_dir = Path(sys.executable).parent
        temp_dir = Path(os.environ.get("TEMP", "C:\\Temp"))
        bat_path = temp_dir / "engram_update.bat"
        pid = os.getpid()

        bat_content = (
            "@echo off\n"
            ":wait\n"
            f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL\n'
            "if not errorlevel 1 (\n"
            "    timeout /t 1 /nobreak > nul\n"
            "    goto wait\n"
            ")\n"
            f'xcopy /Y /E /I "{new_engram_dir}\\*" "{install_dir}\\"\n'
            f'start "" "{install_dir}\\engram.exe"\n'
            'del "%~f0"\n'
        )

        with open(bat_path, "w") as f:
            f.write(bat_content)

        logger.info(f"Launching update helper: {bat_path}")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            shell=False,
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        os._exit(0)  # Hard exit: avoids asyncio catching SystemExit

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
                error=self.error,
            )
        except Exception as exc:
            logger.debug(f"Failed to broadcast update status: {exc}")


# Module-level singleton — mirrors the job_manager, curator pattern
update_checker = UpdateChecker()
