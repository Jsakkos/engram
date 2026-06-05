"""Auto-update checker for Engram.

Checks GitHub Releases on startup, downloads the new version in the background,
verifies the SHA256 checksum, and stages it for a user-triggered restart.

Platform restart strategies:
  Linux/macOS: shutil.copy2 + os.execv (replaces process image in-place)
  Windows:     .bat helper that, after this process exits, robocopies the staged
               build to a sibling dir, verifies it, swaps it in via two atomic
               same-volume renames, relaunches, and rolls back on any failure —
               logging every step to ~/.engram/update_helper.log
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from enum import StrEnum
from pathlib import Path

import httpx
from loguru import logger

from app import __version__
from app.config import is_frozen
from app.core.errors import ConfigurationError, EngramError
from app.database import async_session

GITHUB_API_URL = "https://api.github.com/repos/Jsakkos/engram/releases/latest"
STAGING_BASE = Path.home() / ".engram" / "update"


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
        self.download_progress: float = 0.0
        self.staging_path: Path | None = None
        self.error: str | None = None
        self._is_frozen: bool = is_frozen()
        self._current_version: str = __version__
        self._broadcaster = None  # injected by set_broadcaster()

    def set_broadcaster(self, broadcaster) -> None:
        """Inject the EventBroadcaster (called from main.py after import)."""
        self._broadcaster = broadcaster

    async def start(self) -> None:
        """Entry point — call once from the FastAPI lifespan as asyncio.create_task()."""
        self._prune_staging()
        skipped_version = await self._load_skipped_version()
        await self._check(skipped_version)

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
        """
        if not STAGING_BASE.exists():
            return
        pid = os.getpid()
        for child in list(STAGING_BASE.iterdir()):
            if not child.is_dir():
                continue
            if ".pruning-" in child.name:
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
            self._restart_windows()
        else:
            self._restart_linux_macos()

    async def _check_no_active_jobs(self) -> None:
        """Raise UpdateError if any job is currently ripping/matching/organizing."""
        from sqlmodel import select

        from app.models import DiscJob, JobState

        active_states = [
            JobState.IDENTIFYING,
            JobState.REVIEW_NEEDED,
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
        os.chmod(sys.executable, 0o700)
        # os.execv replaces the process image — same PID, same port, zero downtime
        os.execv(sys.executable, sys.argv)

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
        old_dir = install_dir.parent / f"{install_dir.name}.old-{version}"
        log_path = Path.home() / ".engram" / "update_helper.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

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
    ) -> str:
        """Render the Windows update helper batch script (pure — testable off-Windows).

        Atomic + verified + recoverable swap, run after the parent PID ``pid`` exits:

          1. ``robocopy`` the staged build (``src``) to a sibling ``new_dir`` — never
             touches the live install. ``/MIR`` *replaces* rather than merges (so files
             dropped in the new version don't linger); ``/R:3 /W:2`` retries files still
             transiently locked just after the old process exits. robocopy "success" is
             any exit code ``< 8``.
          2. Verify the sentinel files every healthy onedir build must contain
             (launcher, ``base_library.zip``, the certifi CA bundle, the bundled
             frontend ``index.html``).
          3. Swap with two same-volume renames: ``install`` -> ``old_dir``, then
             ``new_dir`` -> ``install`` — each atomic, so the install is never a hybrid.
          4. Relaunch ``exe``. On any failure (copy / verify / move) roll back to the
             previous install and relaunch it.

        Logs every step to ``log_path`` and only deletes itself on success, so a failed
        run leaves the bat + log behind as evidence.

        Built as a line list so only the path/pid lines interpolate; every line that
        references a cmd variable (``%LOG%``, ``%ERRORLEVEL%``, ``%~f0`` …) is a plain
        string and never meets Python's ``%`` handling — no ``%%`` doubling needed.
        """
        lines = [
            "@echo off",
            "setlocal enableextensions",
            f'set "SRC={src}"',
            f'set "INSTALL={install}"',
            f'set "NEWDIR={new_dir}"',
            f'set "OLDDIR={old_dir}"',
            f'set "LOG={log_path}"',
            f'set "EXE=%INSTALL%\\{exe}"',
            f'echo [engram-update] start (pid {pid}) > "%LOG%"',
            # Diagnostic context: %CD% here is the cwd we INHERITED from engram. If it
            # equals %INSTALL%, the `cd /d` below is what saves the swap (see there).
            'echo [engram-update] cwd=%CD% >> "%LOG%"',
            'echo [engram-update] INSTALL=%INSTALL% NEWDIR=%NEWDIR% OLDDIR=%OLDDIR% >> "%LOG%"',
            'echo [engram-update] SRC=%SRC% EXE=%EXE% >> "%LOG%"',
            # --- wait for the parent process to exit, then let file handles release ---
            ":wait",
            f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL',
            "if not errorlevel 1 (",
            "    ping -n 2 127.0.0.1 >nul",
            "    goto wait",
            ")",
            'echo [engram-update] process exited; waiting for handles >> "%LOG%"',
            "ping -n 3 127.0.0.1 >nul",
            "ping -n 3 127.0.0.1 >nul",
            # Move our OWN working directory off the install dir before the swap. A
            # detached helper inherits the parent engram process's cwd, which for a
            # double-clicked onedir exe is the install dir. A directory that is any
            # process's current directory cannot be renamed, so without this the
            # `move "%INSTALL%"` below fails with "being used by another process" and
            # every restart silently rolls back (confirmed in an isolated swap harness).
            # %TEMP% is guaranteed valid — this bat lives there.
            'cd /d "%TEMP%"',
            'echo [engram-update] cwd(after cd)=%CD% >> "%LOG%"',
            # --- copy staged build to a sibling of the install (never in place) ---
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            'echo [engram-update] robocopy "%SRC%" to "%NEWDIR%" >> "%LOG%"',
            'robocopy "%SRC%" "%NEWDIR%" /MIR /R:3 /W:2 /NP /NFL /NDL >> "%LOG%" 2>&1',
            # Capture robocopy's exit BEFORE any other command (echo resets ERRORLEVEL),
            # then both log and gate on the captured value. Success is < 8.
            "set RC=%ERRORLEVEL%",
            'echo [engram-update] robocopy exit=%RC% >> "%LOG%"',
            "if %RC% GEQ 8 goto fail",
            # --- verify the copied tree before touching the live install ---
            'if not exist "%NEWDIR%\\engram.exe" goto fail',
            'if not exist "%NEWDIR%\\_internal\\base_library.zip" goto fail',
            'if not exist "%NEWDIR%\\_internal\\certifi\\cacert.pem" goto fail',
            'if not exist "%NEWDIR%\\_internal\\app\\static\\index.html" goto fail',
            # --- atomic swap: install -> .old, .new -> install ---
            # Clear any stale .old from a prior failed run, else `move` below fails.
            'rmdir /S /Q "%OLDDIR%" >nul 2>&1',
            'echo [engram-update] swapping install >> "%LOG%"',
            'move "%INSTALL%" "%OLDDIR%" >> "%LOG%" 2>&1',
            "if errorlevel 1 goto fail_no_swap",
            'move "%NEWDIR%" "%INSTALL%" >> "%LOG%" 2>&1',
            "if errorlevel 1 goto restore_old",
            # --- success --- relaunch with cwd pinned to the (new) install dir, exactly
            # as a double-click would, so nothing depends on the helper's %TEMP% cwd.
            'echo [engram-update] success; relaunching >> "%LOG%"',
            'start "" /D "%INSTALL%" "%EXE%"',
            'echo [engram-update] done (success) >> "%LOG%"',
            'rmdir /S /Q "%OLDDIR%" >nul 2>&1',
            '(goto) 2>nul & del "%~f0"',  # exit batch context, then delete self (idiom)
            # --- rollback paths (bat is NOT deleted — left with the log for diagnosis) ---
            ":restore_old",
            'echo [engram-update] swap failed; restoring previous install >> "%LOG%"',
            'rmdir /S /Q "%INSTALL%" >nul 2>&1',
            'move "%OLDDIR%" "%INSTALL%" >> "%LOG%" 2>&1',
            "goto relaunch_old",
            ":fail_no_swap",
            'echo [engram-update] could not move install aside; left untouched >> "%LOG%"',
            "goto relaunch_old",
            ":fail",
            'echo [engram-update] copy/verify failed; install untouched >> "%LOG%"',
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            "goto relaunch_old",
            ":relaunch_old",
            'echo [engram-update] rolled back to previous install >> "%LOG%"',
            'start "" /D "%INSTALL%" "%EXE%"',
            'echo [engram-update] done (rolled back) >> "%LOG%"',
            "endlocal",
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
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        base = detached | new_group
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
                error=self.error,
            )
        except Exception as exc:
            logger.debug(f"Failed to broadcast update status: {exc}")


# Module-level singleton — mirrors the job_manager, curator pattern
update_checker = UpdateChecker()
