# Auto-Update Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add startup update checking, silent background download, WebSocket push notification, and one-click restart-to-apply flow to Engram.

**Architecture:** Backend `UpdateChecker` singleton (new `app/core/updater.py`) owns the full lifecycle: GitHub API check on startup, SHA256-verified download to `~/.engram/update/<version>/`, `EventBroadcaster.broadcast_update_status()` push, and platform-specific restart (`os.execv()` on Linux/macOS, `.bat` helper on Windows). Frontend receives `update_status` WebSocket messages and renders `UpdateBanner` + `UpdateModal`. Dev mode shows the banner but hides the Restart button.

**Tech Stack:** Python `httpx` (already in deps), `tarfile`/`zipfile` (stdlib), `os.execv` (stdlib), FastAPI `BackgroundTasks`, React + Framer Motion, `react-markdown` (new dep), Sonner toasts.

---

## File Map

**Create:**
- `backend/app/core/updater.py` — `UpdateStatus`, `UpdateError`, `UpdateChecker`, module-level singleton
- `frontend/src/app/components/UpdateBanner.tsx` — slim top-of-page banner
- `frontend/src/components/UpdateModal.tsx` — release-notes modal with restart/skip actions
- `backend/tests/unit/test_updater.py` — unit tests
- `backend/tests/integration/test_update_workflow.py` — integration tests

**Modify:**
- `backend/app/models/app_config.py` — add `skipped_update_version`, `last_update_check`
- `backend/app/services/event_broadcaster.py` — add `broadcast_update_status()`
- `backend/app/api/routes.py` — add `GET /api/updates/status`, `POST /api/updates/skip`, `POST /api/updates/restart`
- `backend/app/main.py` — wire `update_checker.start()` in lifespan
- `frontend/src/types/index.ts` — add `UpdateStatus`, `UpdateStatusMessage` types + union
- `frontend/src/app/hooks/useJobManagement.ts` — handle `update_status` WS case
- `frontend/src/app/App.tsx` — mount `UpdateBanner`, thread `updateStatus` state
- `.github/workflows/release.yml` — add SHA256 checksum generation + upload

---

## Task 1: Backend model additions

**Files:**
- Modify: `backend/app/models/app_config.py`

- [ ] **Step 1: Add the two new nullable fields**

Open `backend/app/models/app_config.py`. After the `setup_complete` field at the end of the class, add:

```python
    # Auto-update preferences
    skipped_update_version: str | None = None  # e.g. "0.8.2" — user dismissed this version
    last_update_check: datetime | None = None   # informational timestamp
```

Also add the import at the top of the file (after `from sqlalchemy import text`):

```python
from datetime import datetime
```

The `database.py` `_add_missing_columns()` reconciler automatically ALTERs existing databases on first boot — no migration script needed for these nullable columns.

- [ ] **Step 2: Verify the model loads**

```bash
cd backend
uv run python -c "from app.models.app_config import AppConfig; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/app_config.py
git commit -m "feat(update): add skipped_update_version + last_update_check to AppConfig"
```

---

## Task 2: Core UpdateChecker

**Files:**
- Create: `backend/app/core/updater.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/tests/unit/test_updater.py`:

```python
"""Unit tests for UpdateChecker.

Patches async_session so no test touches engram.db.
httpx is mocked via respx or unittest.mock so no real network calls are made.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.updater import UpdateChecker, UpdateError, UpdateStatus


FAKE_RELEASE = {
    "tag_name": "v99.0.0",
    "html_url": "https://github.com/Jsakkos/engram/releases/tag/v99.0.0",
    "body": "## What's new\n- Feature A\n- Bug fix B",
    "assets": [
        {
            "name": "engram-linux-x64.tar.gz",
            "browser_download_url": "https://example.com/engram-linux-x64.tar.gz",
        },
        {
            "name": "engram-windows-x64.zip",
            "browser_download_url": "https://example.com/engram-windows-x64.zip",
        },
        {
            "name": "sha256sums.txt",
            "browser_download_url": "https://example.com/sha256sums.txt",
        },
    ],
}


class TestUpdateCheckerStates:
    async def test_up_to_date_when_version_matches(self):
        """When GitHub returns the same version, state should be up_to_date."""
        checker = UpdateChecker()
        same_release = {**FAKE_RELEASE, "tag_name": f"v{checker._current_version}"}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = same_release

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                with patch.object(checker, "_load_skipped_version", AsyncMock(return_value=None)):
                    await checker._check(skipped_version=None)

        assert checker.state == UpdateStatus.UP_TO_DATE

    async def test_downloading_when_newer_version_frozen(self, monkeypatch, tmp_path):
        """When a newer version exists and we are frozen, state goes downloading -> ready."""
        checker = UpdateChecker()
        # Simulate frozen build
        monkeypatch.setattr(checker, "_is_frozen", True)
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path)

        # Mock the GitHub API response
        mock_api_response = MagicMock()
        mock_api_response.raise_for_status = MagicMock()
        mock_api_response.json.return_value = FAKE_RELEASE

        # Mock the checksum file response
        mock_sums_response = MagicMock()
        mock_sums_response.raise_for_status = MagicMock()
        mock_sums_response.text = ""  # No checksum entries — verification skipped

        # Simulate a tiny archive download
        import tarfile
        import io
        fake_archive = io.BytesIO()
        with tarfile.open(fileobj=fake_archive, mode="w:gz") as tar:
            content = b"fake binary"
            info = tarfile.TarInfo(name="engram/engram")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        fake_archive.seek(0)
        archive_bytes = fake_archive.read()

        # Build a mock streaming response
        class FakeStream:
            headers = {"content-length": str(len(archive_bytes))}
            async def aiter_bytes(self, chunk_size=65536):
                yield archive_bytes
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def raise_for_status(self):
                pass

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_sums_response)
        mock_client.stream = MagicMock(return_value=FakeStream())

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                await checker._download(FAKE_RELEASE)

        assert checker.state == UpdateStatus.READY
        assert checker.staging_path is not None
        assert checker.staging_path.exists()

    async def test_skipped_version_stays_skipped(self):
        """When GitHub returns a version the user previously skipped, state = SKIPPED."""
        checker = UpdateChecker()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = FAKE_RELEASE  # v99.0.0

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                await checker._check(skipped_version="99.0.0")  # matches tag without "v"

        assert checker.state == UpdateStatus.SKIPPED

    async def test_api_failure_stays_idle(self):
        """Network failure during version check should silently stay idle."""
        import httpx as _httpx
        checker = UpdateChecker()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_httpx.ConnectError("timeout"))

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                await checker._check(skipped_version=None)

        assert checker.state == UpdateStatus.IDLE

    async def test_checksum_mismatch_raises_update_error(self, tmp_path):
        """SHA256 mismatch should raise UpdateError."""
        checker = UpdateChecker()
        archive_path = tmp_path / "test.tar.gz"
        archive_path.write_bytes(b"fake content")

        checksums_text = "badhash  test.tar.gz\n"

        with pytest.raises(UpdateError, match="Checksum mismatch"):
            checker._verify_checksum(archive_path, "test.tar.gz", checksums_text)

    def test_checksum_match_passes(self, tmp_path):
        """Matching SHA256 should pass silently."""
        import hashlib
        checker = UpdateChecker()
        content = b"real content"
        archive_path = tmp_path / "test.tar.gz"
        archive_path.write_bytes(content)

        digest = hashlib.sha256(content).hexdigest()
        checksums_text = f"{digest}  test.tar.gz\n"

        # Should not raise
        checker._verify_checksum(archive_path, "test.tar.gz", checksums_text)

    async def test_apply_update_raises_in_non_frozen(self):
        """apply_update() must raise ConfigurationError in non-frozen (dev) builds."""
        from app.core.errors import ConfigurationError
        checker = UpdateChecker()
        checker._is_frozen = False
        checker.state = UpdateStatus.READY

        with pytest.raises(ConfigurationError):
            await checker.apply_update()

    async def test_apply_update_raises_with_active_jobs(self, monkeypatch):
        """apply_update() must refuse when a job is actively ripping/matching."""
        from app.models.app_config import AppConfig
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, select
        from app.models import DiscJob, JobState
        from app.database import async_session as real_session
        import app.core.updater as updater_mod

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        test_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Insert a ripping job
        async with test_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST",
                state=JobState.RIPPING,
                content_type="unknown",
            )
            session.add(job)
            await session.commit()

        monkeypatch.setattr(updater_mod, "async_session", test_session_factory)

        checker = UpdateChecker()
        checker._is_frozen = True
        checker.state = UpdateStatus.READY
        checker.staging_path = Path("/fake/path")

        with pytest.raises(UpdateError, match="in progress"):
            await checker.apply_update()

    def test_get_status_serializable(self):
        """get_status() must return a plain dict with no non-serializable types."""
        import json
        checker = UpdateChecker()
        status = checker.get_status()
        # Should not raise
        json.dumps(status)
        assert "state" in status
        assert "current_version" in status
        assert "is_frozen" in status

    def test_select_asset_linux(self, monkeypatch):
        """_select_asset picks the .tar.gz on linux."""
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "linux")
        checker = UpdateChecker()
        asset = checker._select_asset(FAKE_RELEASE["assets"])
        assert asset is not None
        assert asset["name"].endswith(".tar.gz")
        assert "linux" in asset["name"]

    def test_select_asset_windows(self, monkeypatch):
        """_select_asset picks the .zip on win32."""
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        checker = UpdateChecker()
        asset = checker._select_asset(FAKE_RELEASE["assets"])
        assert asset is not None
        assert asset["name"].endswith(".zip")
        assert "windows" in asset["name"]
```

- [ ] **Step 2: Run the tests to verify they fail (module not yet created)**

```bash
cd backend
uv run pytest tests/unit/test_updater.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'app.core.updater'`

- [ ] **Step 3: Create `backend/app/core/updater.py`**

```python
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
            from app.database import async_session
            from app.models.app_config import AppConfig
            from sqlmodel import select

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
            if platform == "darwin" and name.endswith(".tar.gz") and (
                "macos" in name or "darwin" in name
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
                tar.extractall(dest_dir)
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
            from app.database import async_session
            from app.models.app_config import AppConfig
            from sqlmodel import select

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
        from app.database import async_session
        from app.models import DiscJob, JobState
        from sqlmodel import select

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
```

- [ ] **Step 4: Run the tests**

```bash
cd backend
uv run pytest tests/unit/test_updater.py -v
```
Expected: All tests pass. If any fail, fix them before continuing.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "feat(update): add UpdateChecker core with SHA256 verification"
```

---

## Task 3: EventBroadcaster + API routes + lifespan wiring

**Files:**
- Modify: `backend/app/services/event_broadcaster.py`
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add `broadcast_update_status()` to EventBroadcaster**

Open `backend/app/services/event_broadcaster.py`. At the end of the class, after `broadcast_subtitle_download_failed`, add:

```python
    # --- Update Events ---

    async def broadcast_update_status(
        self,
        state: str,
        latest_version: str | None = None,
        release_notes: str | None = None,
        release_url: str | None = None,
        error: str | None = None,
    ) -> None:
        """Broadcast update availability status to all connected clients.

        current_version is always the running build's __version__ — injected here
        so UpdateChecker doesn't need to import it separately.
        """
        from app import __version__

        data: dict = {
            "type": "update_status",
            "state": state,
            "current_version": __version__,
        }
        if latest_version is not None:
            data["latest_version"] = latest_version
        if release_notes is not None:
            data["release_notes"] = release_notes
        if release_url is not None:
            data["release_url"] = release_url
        if error is not None:
            data["error"] = error
        await self._ws.broadcast(data)
```

- [ ] **Step 2: Add API routes**

Open `backend/app/api/routes.py`. At the top of the file, the imports already include `BaseModel`, `HTTPException`, `BackgroundTasks` needs to be added. Locate the existing imports line:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
```

Change it to:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
```

Then at the very end of `routes.py` (before any `if __name__` block if present), add:

```python
# ---------------------------------------------------------------------------
# Update endpoints
# ---------------------------------------------------------------------------

from app.core.updater import UpdateError, UpdateStatus, update_checker


class SkipVersionRequest(BaseModel):
    version: str


@router.get("/updates/status")
async def get_update_status():
    """Get current update check state."""
    return update_checker.get_status()


@router.post("/updates/skip")
async def skip_update_version(body: SkipVersionRequest):
    """Persist user's choice to skip a specific version."""
    try:
        await update_checker.skip_version(body.version)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.post("/updates/restart")
async def restart_for_update(background_tasks: BackgroundTasks):
    """Schedule update application after response is sent.

    Returns 200 immediately; actual restart happens in a BackgroundTask so the
    response has time to reach the client before the process exits/exec's.
    """
    from app.core.errors import ConfigurationError

    # Guard checks run synchronously before returning 200
    if not update_checker._is_frozen:
        raise HTTPException(
            status_code=400,
            detail=(
                "Updates can only be applied in frozen builds. "
                f"Download manually from {update_checker.release_url or 'GitHub'}."
            ),
        )

    if update_checker.state != UpdateStatus.READY:
        raise HTTPException(status_code=400, detail="No staged update is ready to apply.")

    try:
        await update_checker._check_no_active_jobs()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    async def _do_restart() -> None:
        try:
            await update_checker.apply_update()
        except PermissionError as exc:
            logger.error(f"Update restart permission error: {exc}", exc_info=True)
        except Exception as exc:
            logger.error(f"Update restart failed: {exc}", exc_info=True)

    background_tasks.add_task(_do_restart)
    return {"ok": True}
```

- [ ] **Step 3: Wire update_checker into main.py lifespan**

Open `backend/app/main.py`. After the line:

```python
    app.state.precomputed_cache_task = asyncio.create_task(ensure_precomputed_cache())
```

Add:

```python
    # Check for updates in the background — fire-and-forget, never blocks startup.
    from app.core.updater import update_checker
    from app.services.event_broadcaster import EventBroadcaster

    update_checker.set_broadcaster(EventBroadcaster(ws_manager))
    app.state.update_check_task = asyncio.create_task(update_checker.start())
```

Also, in the shutdown section, after the `cache_task` cancellation, add:

```python
    update_task = getattr(app.state, "update_check_task", None)
    if update_task and not update_task.done():
        update_task.cancel()
```

- [ ] **Step 4: Run the backend tests**

```bash
cd backend
uv run pytest tests/unit/test_updater.py -v
uv run pytest tests/unit/test_api_routes.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/event_broadcaster.py backend/app/api/routes.py backend/app/main.py
git commit -m "feat(update): add update API routes, EventBroadcaster method, lifespan wiring"
```

---

## Task 4: Integration tests

**Files:**
- Create: `backend/tests/integration/test_update_workflow.py`

- [ ] **Step 1: Write the integration tests**

Create `backend/tests/integration/test_update_workflow.py`:

```python
"""Integration tests for /api/updates/* endpoints.

Uses the standard integration test fixture pattern (in-memory DB, AsyncClient).
The update_checker singleton's state is reset between tests.
"""

import pytest
from httpx import AsyncClient

from app.core.updater import UpdateStatus, update_checker


@pytest.fixture(autouse=True)
def reset_update_checker():
    """Reset singleton state between tests."""
    original_state = update_checker.state
    original_latest = update_checker.latest_version
    original_frozen = update_checker._is_frozen
    yield
    update_checker.state = original_state
    update_checker.latest_version = original_latest
    update_checker._is_frozen = original_frozen


class TestGetUpdateStatus:
    async def test_returns_expected_shape(self, integration_client: AsyncClient):
        """GET /api/updates/status returns all required fields."""
        response = await integration_client.get("/api/updates/status")
        assert response.status_code == 200
        data = response.json()

        required_fields = {
            "state", "current_version", "latest_version",
            "release_notes", "release_url", "download_progress",
            "error", "is_frozen",
        }
        assert required_fields.issubset(data.keys()), (
            f"Missing fields: {required_fields - data.keys()}"
        )
        # current_version should be a non-empty string
        assert isinstance(data["current_version"], str)
        assert len(data["current_version"]) > 0

    async def test_state_is_valid_value(self, integration_client: AsyncClient):
        """State field should be one of the known UpdateStatus values."""
        response = await integration_client.get("/api/updates/status")
        data = response.json()
        valid_states = {"idle", "checking", "up_to_date", "downloading", "ready", "skipped", "error"}
        assert data["state"] in valid_states


class TestSkipVersion:
    async def test_skip_version_returns_200(
        self, integration_client: AsyncClient, integration_config
    ):
        """POST /api/updates/skip persists and returns ok."""
        response = await integration_client.post(
            "/api/updates/skip",
            json={"version": "99.9.9"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    async def test_skip_version_persists_to_db(
        self, integration_client: AsyncClient, integration_config, async_session
    ):
        """Skipped version should be persisted in AppConfig."""
        await integration_client.post(
            "/api/updates/skip",
            json={"version": "0.0.0"},
        )

        from app.models.app_config import AppConfig
        from sqlmodel import select

        result = await async_session.execute(select(AppConfig).limit(1))
        config = result.scalar_one_or_none()
        assert config is not None
        assert config.skipped_update_version == "0.0.0"


class TestRestartForUpdate:
    async def test_restart_returns_400_in_non_frozen(self, integration_client: AsyncClient):
        """POST /api/updates/restart returns 400 in non-frozen (test) environment."""
        # update_checker._is_frozen is False in test (no PyInstaller)
        update_checker._is_frozen = False
        update_checker.state = UpdateStatus.READY

        response = await integration_client.post("/api/updates/restart")
        assert response.status_code == 400
        assert "frozen" in response.json()["detail"].lower()

    async def test_restart_returns_400_when_not_ready(self, integration_client: AsyncClient):
        """POST /api/updates/restart returns 400 when state is not READY."""
        update_checker._is_frozen = True
        update_checker.state = UpdateStatus.IDLE

        response = await integration_client.post("/api/updates/restart")
        assert response.status_code == 400
```

- [ ] **Step 2: Run the integration tests**

```bash
cd backend
uv run pytest tests/integration/test_update_workflow.py -v
```
Expected: All 5 tests pass.

- [ ] **Step 3: Run the full test suite as a regression check**

```bash
cd backend
uv run pytest --tb=short -q
```
Expected: All previously passing tests still pass. Note any pre-existing failures (the `test_movie_ambiguous_rip_first_workflow` staging-cleanup race is a known pre-existing failure).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_update_workflow.py
git commit -m "test(update): add integration tests for /api/updates/* endpoints"
```

---

## Task 5: Frontend types + WebSocket handler

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/app/hooks/useJobManagement.ts`

- [ ] **Step 1: Add UpdateStatus type and WebSocket message type**

Open `frontend/src/types/index.ts`. After the `TitlesDiscovered` interface and before the `WebSocketMessage` union, add:

```typescript
export interface UpdateStatusMessage {
    type: 'update_status';
    state: 'idle' | 'checking' | 'up_to_date' | 'downloading' | 'ready' | 'skipped' | 'error';
    current_version: string;
    latest_version?: string | null;
    release_notes?: string | null;
    release_url?: string | null;
    download_progress?: number | null;
    error?: string | null;
    is_frozen?: boolean;
}

/** Snapshot of update state, stored in App.tsx state. */
export interface UpdateStatus {
    state: 'idle' | 'checking' | 'up_to_date' | 'downloading' | 'ready' | 'skipped' | 'error';
    current_version: string;
    latest_version: string | null;
    release_notes: string | null;
    release_url: string | null;
    download_progress: number | null;
    error: string | null;
    is_frozen: boolean;
}
```

Update the `WebSocketMessage` union to include `UpdateStatusMessage`:

```typescript
export type WebSocketMessage =
    | DriveEvent
    | JobUpdate
    | TitleUpdate
    | SubtitleEvent
    | TitlesDiscovered
    | UpdateStatusMessage;
```

- [ ] **Step 2: Add update_status handler to useJobManagement.ts**

Open `frontend/src/app/hooks/useJobManagement.ts`.

Add `updateStatus` to the function signature. Find the function declaration:

```typescript
export function useJobManagement(devMode: boolean = false) {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [titlesMap, setTitlesMap] = useState<Record<number, DiscTitle[]>>({});
```

Add the new state:

```typescript
export function useJobManagement(devMode: boolean = false) {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [titlesMap, setTitlesMap] = useState<Record<number, DiscTitle[]>>({});
    const [updateStatus, setUpdateStatus] = useState<import('../../types').UpdateStatus | null>(null);
```

In the WebSocket message switch (after the `subtitle_event` case, before `default`), add:

```typescript
                case 'update_status': {
                    const { type: _type, ...data } = message as import('../../types').UpdateStatusMessage;
                    setUpdateStatus({
                        state: data.state,
                        current_version: data.current_version,
                        latest_version: data.latest_version ?? null,
                        release_notes: data.release_notes ?? null,
                        release_url: data.release_url ?? null,
                        download_progress: data.download_progress ?? null,
                        error: data.error ?? null,
                        is_frozen: data.is_frozen ?? false,
                    });
                    break;
                }
```

At the bottom of the hook's return statement, add `updateStatus` to the returned object:

```typescript
    return {
        jobs,
        titlesMap,
        isConnected,
        updateStatus,
        cancelJob,
        advanceJob,
        clearCompleted,
        setJobName,
        reIdentifyJob,
    };
```

- [ ] **Step 3: TypeScript compile check**

```bash
cd frontend
npm run build 2>&1 | tail -20
```
Expected: `built in Xs` (no TypeScript errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/app/hooks/useJobManagement.ts
git commit -m "feat(update): add UpdateStatus types and WS message handler"
```

---

## Task 6: Frontend components (UpdateBanner + UpdateModal)

**Files:**
- Install `react-markdown`
- Create: `frontend/src/app/components/UpdateBanner.tsx`
- Create: `frontend/src/components/UpdateModal.tsx`
- Modify: `frontend/src/app/App.tsx`

- [ ] **Step 1: Install react-markdown**

```bash
cd frontend
npm install react-markdown
```

- [ ] **Step 2: Create UpdateBanner**

Create `frontend/src/app/components/UpdateBanner.tsx`:

```tsx
/**
 * UpdateBanner — slim top-of-page notification shown when a new version is staged.
 *
 * Visible only when updateStatus.state === 'ready'.
 * In dev mode (is_frozen = false) the "Restart now" button is hidden.
 */

import { useState } from "react";
import { ArrowUp, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { sv } from "./synapse";
import { ApiError, apiFetchVoid } from "../../api/client";
import type { UpdateStatus } from "../../types";

interface UpdateBannerProps {
    updateStatus: UpdateStatus | null;
    onShowNotes: () => void;
    onDismiss: () => void;
}

export function UpdateBanner({ updateStatus, onShowNotes, onDismiss }: UpdateBannerProps) {
    const [restarting, setRestarting] = useState(false);

    if (!updateStatus || updateStatus.state !== "ready") return null;

    const isFrozen = updateStatus.is_frozen;

    const handleRestart = async () => {
        setRestarting(true);
        try {
            await apiFetchVoid("/api/updates/restart", { method: "POST" });
            toast.info("Restarting to apply update…");
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.status === 409) {
                    toast.error("A disc operation is in progress. Please wait before restarting.");
                } else if (err.status === 400) {
                    toast.error("Updates cannot be applied in dev mode.");
                } else {
                    toast.error(
                        `Restart failed. Download manually from GitHub: ${updateStatus.release_url ?? ""}`,
                    );
                }
            } else {
                toast.error("Restart failed. Please try again.");
            }
            setRestarting(false);
        }
    };

    const handleSkip = async () => {
        if (!updateStatus.latest_version) return;
        try {
            await apiFetchVoid("/api/updates/skip", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: updateStatus.latest_version }),
            });
            onDismiss();
        } catch {
            toast.error("Failed to save skip preference.");
        }
    };

    return (
        <div
            data-testid="update-banner"
            style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 28px",
                background: `${sv.cyan}10`,
                borderBottom: `1px solid ${sv.cyan}55`,
                boxShadow: `0 0 12px ${sv.cyan}22`,
                fontFamily: sv.mono,
                fontSize: 12,
                letterSpacing: "0.06em",
                color: sv.cyanHi,
            }}
        >
            <ArrowUp size={14} color={sv.cyan} style={{ flexShrink: 0 }} />
            <span style={{ flex: 1 }}>
                engram {updateStatus.latest_version} is ready to install
                {!isFrozen && (
                    <span style={{ color: sv.inkDim }}> — dev mode, manual download required</span>
                )}
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                    onClick={onShowNotes}
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        letterSpacing: "0.14em",
                        textTransform: "uppercase",
                        color: sv.cyanHi,
                        background: "transparent",
                        border: `1px solid ${sv.cyan}55`,
                        padding: "4px 10px",
                        cursor: "pointer",
                    }}
                >
                    What's new
                </button>

                {isFrozen && (
                    <button
                        onClick={handleRestart}
                        disabled={restarting}
                        style={{
                            fontFamily: sv.mono,
                            fontSize: 10,
                            letterSpacing: "0.14em",
                            textTransform: "uppercase",
                            color: sv.bg0,
                            background: restarting ? `${sv.cyan}99` : sv.cyan,
                            border: "none",
                            padding: "4px 10px",
                            cursor: restarting ? "wait" : "pointer",
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 6,
                        }}
                    >
                        {restarting && <RefreshCw size={10} />}
                        {restarting ? "Restarting…" : "Restart now"}
                    </button>
                )}

                <button
                    onClick={handleSkip}
                    title="Skip this version"
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        color: sv.inkDim,
                        background: "transparent",
                        border: "none",
                        padding: "4px 6px",
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                    }}
                >
                    <X size={11} />
                    Skip
                </button>
            </div>
        </div>
    );
}
```

- [ ] **Step 3: Create UpdateModal**

Create `frontend/src/components/UpdateModal.tsx`:

```tsx
/**
 * UpdateModal — release notes modal opened from UpdateBanner's "What's new" button.
 *
 * Contains the same Restart / Skip actions as the banner for convenience.
 * Follows the BugReportModal overlay pattern.
 */

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ArrowUp, ExternalLink, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { toast } from "sonner";
import { SvPanel, sv } from "../app/components/synapse";
import { ApiError, apiFetchVoid } from "../api/client";
import type { UpdateStatus } from "../types";

interface UpdateModalProps {
    open: boolean;
    updateStatus: UpdateStatus | null;
    onClose: () => void;
    onDismiss: () => void;
}

export default function UpdateModal({
    open,
    updateStatus,
    onClose,
    onDismiss,
}: UpdateModalProps) {
    const [restarting, setRestarting] = useState(false);

    // Close on Escape key
    useEffect(() => {
        if (!open) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [open, onClose]);

    const handleRestart = useCallback(async () => {
        setRestarting(true);
        try {
            await apiFetchVoid("/api/updates/restart", { method: "POST" });
            onClose();
            toast.info("Restarting to apply update…");
        } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
                toast.error("A disc operation is in progress. Please wait before restarting.");
            } else {
                toast.error(
                    `Restart failed. Download manually from GitHub: ${updateStatus?.release_url ?? ""}`,
                );
            }
            setRestarting(false);
        }
    }, [onClose, updateStatus]);

    const handleSkip = useCallback(async () => {
        if (!updateStatus?.latest_version) return;
        try {
            await apiFetchVoid("/api/updates/skip", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: updateStatus.latest_version }),
            });
            onClose();
            onDismiss();
        } catch {
            toast.error("Failed to save skip preference.");
        }
    }, [updateStatus, onClose, onDismiss]);

    const isFrozen = updateStatus?.is_frozen ?? false;

    const buttonBase: CSSProperties = {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "10px 16px",
        fontFamily: sv.mono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.18em",
        textTransform: "uppercase",
        cursor: "pointer",
        transition: "all 0.18s",
        border: "none",
    };

    return (
        <AnimatePresence>
            {open && (
                <motion.div
                    className="fixed inset-0 z-50 flex items-center justify-center p-4"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="update-modal-title"
                >
                    {/* Backdrop */}
                    <motion.div
                        className="absolute inset-0"
                        style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        onClick={onClose}
                    />

                    {/* Modal panel */}
                    <motion.div
                        className="relative w-full max-w-2xl"
                        initial={{ opacity: 0, scale: 0.94, y: 20 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.94, y: 20 }}
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                    >
                        <SvPanel
                            glow
                            pad={0}
                            style={{
                                background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                                boxShadow: `0 0 40px ${sv.cyan}26, inset 0 0 30px ${sv.cyan}0a`,
                                maxHeight: "85vh",
                                display: "flex",
                                flexDirection: "column",
                            }}
                            data-testid="update-modal"
                        >
                            {/* Header */}
                            <div
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 12,
                                    padding: "20px 24px",
                                    borderBottom: `1px solid ${sv.line}`,
                                }}
                            >
                                <ArrowUp
                                    size={20}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}99)` }}
                                />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <h2
                                        id="update-modal-title"
                                        style={{
                                            fontFamily: sv.display,
                                            fontWeight: 700,
                                            fontSize: 16,
                                            letterSpacing: "0.2em",
                                            textTransform: "uppercase",
                                            color: sv.cyanHi,
                                            margin: 0,
                                        }}
                                    >
                                        What's new in {updateStatus?.latest_version ?? "…"}
                                    </h2>
                                </div>
                                <button
                                    onClick={onClose}
                                    aria-label="Close"
                                    style={{
                                        color: sv.inkFaint,
                                        background: "transparent",
                                        border: "none",
                                        cursor: "pointer",
                                        padding: 4,
                                        display: "flex",
                                    }}
                                >
                                    <X size={18} />
                                </button>
                            </div>

                            {/* Release notes body */}
                            <div
                                style={{
                                    flex: 1,
                                    overflowY: "auto",
                                    padding: "20px 24px",
                                }}
                            >
                                {updateStatus?.release_notes ? (
                                    <div
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 13,
                                            color: sv.ink,
                                            lineHeight: 1.65,
                                        }}
                                        className="prose prose-invert prose-sm max-w-none"
                                    >
                                        <ReactMarkdown>{updateStatus.release_notes}</ReactMarkdown>
                                    </div>
                                ) : (
                                    <p
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 12,
                                            color: sv.inkDim,
                                        }}
                                    >
                                        No release notes available.
                                    </p>
                                )}
                                {updateStatus?.release_url && (
                                    <a
                                        href={updateStatus.release_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        style={{
                                            display: "inline-flex",
                                            alignItems: "center",
                                            gap: 6,
                                            marginTop: 16,
                                            fontFamily: sv.mono,
                                            fontSize: 11,
                                            letterSpacing: "0.1em",
                                            color: sv.cyanHi,
                                            textDecoration: "none",
                                            textTransform: "uppercase",
                                        }}
                                    >
                                        <ExternalLink size={12} />
                                        View on GitHub
                                    </a>
                                )}
                            </div>

                            {/* Footer actions */}
                            <div
                                style={{
                                    display: "flex",
                                    justifyContent: "space-between",
                                    alignItems: "center",
                                    padding: "16px 24px",
                                    borderTop: `1px solid ${sv.line}`,
                                    gap: 12,
                                }}
                            >
                                <button
                                    onClick={handleSkip}
                                    style={{
                                        ...buttonBase,
                                        color: sv.inkDim,
                                        background: "transparent",
                                        border: `1px solid ${sv.line}`,
                                    }}
                                >
                                    Skip this version
                                </button>

                                {isFrozen ? (
                                    <button
                                        onClick={handleRestart}
                                        disabled={restarting}
                                        style={{
                                            ...buttonBase,
                                            color: sv.bg0,
                                            background: restarting ? `${sv.cyan}99` : sv.cyan,
                                            opacity: restarting ? 0.8 : 1,
                                        }}
                                    >
                                        {restarting ? "Restarting…" : "Restart to update →"}
                                    </button>
                                ) : (
                                    <a
                                        href={updateStatus?.release_url ?? "#"}
                                        target="_blank"
                                        rel="noreferrer"
                                        style={{
                                            ...buttonBase,
                                            color: sv.bg0,
                                            background: sv.cyan,
                                            textDecoration: "none",
                                        }}
                                    >
                                        Download from GitHub →
                                    </a>
                                )}
                            </div>
                        </SvPanel>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
```

- [ ] **Step 4: Wire into App.tsx**

Open `frontend/src/app/App.tsx`.

**4a.** Add imports at the top (after the existing imports):

```tsx
import { UpdateBanner } from "./components/UpdateBanner";
import UpdateModal from "../components/UpdateModal";
```

**4b.** In `MainDashboard`, destructure `updateStatus` from `useJobManagement`:

```tsx
  const { jobs, titlesMap, isConnected, cancelJob, advanceJob, clearCompleted, setJobName, reIdentifyJob, updateStatus } = useJobManagement(DEV_MODE);
```

**4c.** After the `updateStatus` destructure, add state for banner/modal visibility:

```tsx
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [updateDismissed, setUpdateDismissed] = useState(false);
```

Also add a ref to remember the version pending apply (for the post-restart success toast). Add after those state declarations:

```tsx
  const pendingUpdateVersionRef = useRef<string | null>(null);
```

Add `useRef` to the import at the top of the file if not already there:
```tsx
import { useState, useEffect, useRef } from "react";
```

**4d.** Add post-reconnect success toast. After the `isConnected` `useEffect` that handles `showOfflineSplash`, add:

```tsx
  // When WS reconnects and the update was applied, show success toast.
  useEffect(() => {
    if (isConnected && pendingUpdateVersionRef.current) {
      if (
        updateStatus?.state === "up_to_date" &&
        updateStatus.current_version === pendingUpdateVersionRef.current
      ) {
        toast.success(`Updated to ${pendingUpdateVersionRef.current} ✓`);
        pendingUpdateVersionRef.current = null;
      }
    }
  }, [isConnected, updateStatus]);
```

**4e.** In the JSX return, after the filter strip `</div>` and before the platform guidance banner `<AnimatePresence>`, add:

```tsx
      {/* Auto-update banner */}
      {!updateDismissed && (
        <UpdateBanner
          updateStatus={updateStatus}
          onShowNotes={() => setShowUpdateModal(true)}
          onDismiss={() => setUpdateDismissed(true)}
        />
      )}
```

**4f.** In the modal section (near `ConfigWizard`, `BugReportModal`, etc.), add:

```tsx
      <UpdateModal
        open={showUpdateModal}
        updateStatus={updateStatus}
        onClose={() => setShowUpdateModal(false)}
        onDismiss={() => {
          setUpdateDismissed(true);
          setShowUpdateModal(false);
        }}
      />
```

When the user clicks "Restart now" (in banner or modal), also store the pending version:

In the `UpdateBanner`, the `handleRestart` function calls `apiFetchVoid`. We need to store the pending version before calling restart. The cleanest way: pass a callback prop `onRestart` to `UpdateBanner` and `UpdateModal`.

Add to `UpdateBannerProps`:
```tsx
onRestart?: () => void;
```

In `handleRestart` inside `UpdateBanner`, call `onRestart?.()` before `apiFetchVoid`.

In App.tsx, pass:
```tsx
<UpdateBanner
  updateStatus={updateStatus}
  onShowNotes={() => setShowUpdateModal(true)}
  onDismiss={() => setUpdateDismissed(true)}
  onRestart={() => {
    pendingUpdateVersionRef.current = updateStatus?.latest_version ?? null;
  }}
/>
```

And the same `onRestart` prop for `UpdateModal`. Add to `UpdateModalProps`:
```tsx
onRestart?: () => void;
```

Call it in `handleRestart` inside the modal before `apiFetchVoid`.

In App.tsx, pass to `UpdateModal`:
```tsx
<UpdateModal
  open={showUpdateModal}
  updateStatus={updateStatus}
  onClose={() => setShowUpdateModal(false)}
  onDismiss={() => { setUpdateDismissed(true); setShowUpdateModal(false); }}
  onRestart={() => {
    pendingUpdateVersionRef.current = updateStatus?.latest_version ?? null;
  }}
/>
```

- [ ] **Step 5: TypeScript build check**

```bash
cd frontend
npm run build 2>&1 | tail -30
```
Expected: `built in Xs` with no TypeScript errors. Fix any type errors before continuing.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/UpdateBanner.tsx frontend/src/components/UpdateModal.tsx frontend/src/app/App.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(update): add UpdateBanner, UpdateModal, wire into App.tsx"
```

---

## Task 7: Release workflow — SHA256 checksums

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Add checksum step to the `create-release` job**

Open `.github/workflows/release.yml`. In the `create-release` job, after the three `actions/download-artifact@v8` steps, add a new step before `softprops/action-gh-release`:

```yaml
      - name: Generate SHA256 checksums
        run: |
          sha256sum engram-linux-x64.tar.gz engram-windows-x64.zip engram-macos-arm64.tar.gz \
            > sha256sums.txt
          echo "Checksum file contents:"
          cat sha256sums.txt
```

Then update the `softprops/action-gh-release` step's `files:` list to include `sha256sums.txt`:

```yaml
          files: |
            engram-windows-x64.zip
            engram-linux-x64.tar.gz
            engram-macos-arm64.tar.gz
            sha256sums.txt
```

- [ ] **Step 2: Verify the YAML is valid**

```bash
cd C:\Github\engram\.claude\worktrees\practical-chatterjee-cc7486
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" 2>&1
```
Expected: No output (valid YAML). If you don't have `pyyaml` installed: `pip install pyyaml`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add SHA256 checksum generation to release workflow"
```

---

## Task 8: Final integration check

- [ ] **Step 1: Run all backend tests**

```bash
cd backend
uv run pytest -v --tb=short
```
Expected: All tests pass (except the known pre-existing `test_movie_ambiguous_rip_first_workflow` race).

- [ ] **Step 2: Run frontend build**

```bash
cd frontend
npm run build
```
Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 3: Manual smoke test (optional but recommended)**

Start the backend in dev mode and open the dashboard. Since the app is not frozen, the update banner should appear if a newer version exists on GitHub. The "Restart now" button should be hidden (dev mode guard).

```bash
cd backend
uv run uvicorn app.main:app --port 8000
```

In another terminal:
```bash
cd frontend
npm run dev
```

Open http://localhost:5173 and verify the banner appears if GitHub has a newer tag, or nothing if already at latest.

To manually force the banner for testing: temporarily change `__version__` in `backend/app/__init__.py` to `"0.0.0"`, restart backend, and observe the banner.

- [ ] **Step 4: Commit the spec doc and open a PR**

```bash
git add docs/superpowers/specs/2026-05-26-auto-update-design.md
git commit -m "docs: add auto-update feature spec"
git push origin feat/auto-update
gh pr create --title "feat: auto-update — startup check, silent download, restart-to-apply" \
  --body "Implements the auto-update feature designed in docs/superpowers/specs/2026-05-26-auto-update-design.md.

## Summary
- Backend UpdateChecker checks GitHub Releases on startup
- Downloads platform-correct asset silently in background
- Verifies SHA256 checksum before staging
- WebSocket push to frontend when update is ready
- UpdateBanner + UpdateModal for notification and release notes
- One-click restart: os.execv() on Linux/macOS, .bat helper on Windows
- Dev mode: shows banner but disables restart
- Skip version persisted in AppConfig DB

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-review notes

**Spec coverage check:**
- ✅ Startup check → `update_checker.start()` in lifespan
- ✅ Auto-download → `_download()` runs immediately when new version found + frozen
- ✅ SHA256 verification → `_verify_checksum()`
- ✅ WebSocket push → `broadcast_update_status()` on every state change
- ✅ Banner → `UpdateBanner.tsx`, shown when `state === 'ready'`
- ✅ Release notes modal → `UpdateModal.tsx` with `react-markdown`
- ✅ Restart on Linux/macOS → `_restart_linux_macos()` → `os.execv()`
- ✅ Restart on Windows → `_restart_windows()` → `.bat` + `os._exit(0)`
- ✅ Dev mode guard → `_is_frozen` check in `_check()` and `apply_update()`
- ✅ Active-job guard → `_check_no_active_jobs()` with DB query
- ✅ Skip version → `skip_version()` + `AppConfig.skipped_update_version`
- ✅ Post-restart success toast → `pendingUpdateVersionRef` + `isConnected` effect
- ✅ Release workflow checksums → Task 7
- ✅ Unit tests → Task 2
- ✅ Integration tests → Task 4

**Type consistency check:**
- `UpdateStatus.READY = "ready"` in backend matches `state === 'ready'` in frontend ✅
- `broadcast_update_status()` injects `current_version` from `__version__` ✅
- `UpdateStatusMessage.is_frozen` → `UpdateStatus.is_frozen: boolean` ✅
- `UpdateBanner` `onDismiss` matches App.tsx call site ✅
- `apiFetchVoid` from `../../api/client` (relative path correct for both component locations) ✅
