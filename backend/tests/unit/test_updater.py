"""Unit tests for UpdateChecker.

Patches async_session so no test touches engram.db.
httpx is mocked via unittest.mock so no real network calls are made.
"""

import subprocess
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
        # Force linux platform so _select_asset picks the .tar.gz asset that matches
        # the fake archive created below (platform-independent test).
        monkeypatch.setattr(sys, "platform", "linux")

        # Mock the GitHub API response
        mock_api_response = MagicMock()
        mock_api_response.raise_for_status = MagicMock()
        mock_api_response.json.return_value = FAKE_RELEASE

        # Mock the checksum file response
        mock_sums_response = MagicMock()
        mock_sums_response.raise_for_status = MagicMock()
        mock_sums_response.text = ""  # No checksum entries — verification skipped

        # Simulate a tiny archive download
        import io
        import tarfile

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
        import sys as _sys

        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        updater_mod = _sys.modules["app.core.updater"]
        from app.models import DiscJob, JobState

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


class TestPruneStaging:
    """Staging holds only not-yet-installed updates; older ones are pruned."""

    def test_prunes_installed_versions_keeps_newer(self, monkeypatch, tmp_path):
        """Staged dirs <= current version are removed; strictly newer ones kept."""
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path)
        for v in ("0.11.0", "0.12.1", "0.13.0"):
            (tmp_path / v).mkdir()

        checker = UpdateChecker()
        checker._current_version = "0.12.1"
        checker._prune_staging()

        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert remaining == ["0.13.0"]

    def test_prune_missing_base_is_noop(self, monkeypatch, tmp_path):
        """No staging dir yet → prune does nothing and doesn't raise."""
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path / "nope")
        checker = UpdateChecker()
        checker._current_version = "0.12.1"
        checker._prune_staging()  # must not raise


class TestSpawnDetachedHelper:
    """The Windows update helper must escape a kill-on-close Job Object."""

    def _breakaway_flag(self):
        # app.core.updater calls subprocess.Popen (module-qualified), so the stdlib
        # subprocess module here is the same object it uses.
        return getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)

    def test_spawns_with_breakaway_from_job(self, monkeypatch):
        """First attempt must include CREATE_BREAKAWAY_FROM_JOB so a job can't kill it."""
        flags_seen = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: flags_seen.append(kw.get("creationflags", 0)) or MagicMock(),
        )
        UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

        assert flags_seen, "Popen was never called"
        assert flags_seen[0] & self._breakaway_flag()

    def test_falls_back_without_breakaway_on_oserror(self, monkeypatch):
        """If the job forbids breakaway (CreateProcess raises), retry plain-detached."""
        breakaway = self._breakaway_flag()
        flags_seen = []

        def fake_popen(*a, **kw):
            flags = kw.get("creationflags", 0)
            flags_seen.append(flags)
            if flags & breakaway:
                raise OSError("Access is denied")
            return MagicMock()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

        assert len(flags_seen) == 2
        assert flags_seen[0] & breakaway  # tried with breakaway
        assert not (flags_seen[1] & breakaway)  # then fell back without
