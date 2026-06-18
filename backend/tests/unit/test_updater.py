"""Unit tests for UpdateChecker.

Patches async_session so no test touches engram.db.
httpx is mocked via unittest.mock so no real network calls are made.
"""

import os
import shutil
import subprocess
import sys
import time
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

        # A realistic complete onedir layout: launcher + the sentinel files the
        # extraction-integrity check requires (base_library.zip + certifi/cacert.pem).
        fake_archive = io.BytesIO()
        with tarfile.open(fileobj=fake_archive, mode="w:gz") as tar:
            for name, content in (
                ("engram/engram", b"fake binary"),
                ("engram/_internal/base_library.zip", b"fake zip"),
                ("engram/_internal/certifi/cacert.pem", b"fake ca bundle"),
            ):
                info = tarfile.TarInfo(name=name)
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
        # Atomic promote left a complete engram/ dir and cleaned up the temp dir.
        assert (checker.staging_path / "engram" / "engram").exists()
        assert not (checker.staging_path / ".incoming").exists()

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

    async def test_apply_update_allows_review_needed_jobs(self, monkeypatch, tmp_path):
        """REVIEW_NEEDED jobs must NOT block an update restart.

        Disc is already out; the job is parked waiting for user input.
        """
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

        async with test_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST",
                state=JobState.REVIEW_NEEDED,
                content_type="unknown",
            )
            session.add(job)
            await session.commit()

        monkeypatch.setattr(updater_mod, "async_session", test_session_factory)
        monkeypatch.setattr(_sys, "platform", "linux")

        staged = tmp_path / "staged"
        _make_build(staged, complete=True)

        checker = UpdateChecker()
        checker._is_frozen = True
        checker.state = UpdateStatus.READY
        checker.staging_path = staged

        called = {"restart": False}
        monkeypatch.setattr(checker, "_restart_linux_macos", lambda: called.update(restart=True))
        monkeypatch.setattr(checker, "_restart_windows", lambda: called.update(restart=True))

        # Should not raise — REVIEW_NEEDED is no longer a blocking state
        await checker.apply_update()
        assert called["restart"] is True

    async def test_apply_update_allows_cleared_active_jobs(self, monkeypatch, tmp_path):
        """Cleared jobs (dismissed from dashboard) must NOT block an update restart."""
        import sys as _sys
        from datetime import UTC, datetime

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

        async with test_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST",
                state=JobState.MATCHING,
                content_type="unknown",
                cleared_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()

        monkeypatch.setattr(updater_mod, "async_session", test_session_factory)
        monkeypatch.setattr(_sys, "platform", "linux")

        staged = tmp_path / "staged"
        _make_build(staged, complete=True)

        checker = UpdateChecker()
        checker._is_frozen = True
        checker.state = UpdateStatus.READY
        checker.staging_path = staged

        called = {"restart": False}
        monkeypatch.setattr(checker, "_restart_linux_macos", lambda: called.update(restart=True))
        monkeypatch.setattr(checker, "_restart_windows", lambda: called.update(restart=True))

        # Should not raise — dismissed jobs must not ghost-block the update
        await checker.apply_update()
        assert called["restart"] is True

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


def _make_build(root: Path, *, complete: bool = True) -> Path:
    """Create a fake extracted onedir build at root/engram. Returns the engram dir.

    A complete build has the launcher + base_library.zip + certifi/cacert.pem
    (the sentinels _verify_extracted requires). complete=False omits cacert.pem to
    model the truncated 0.14.0 extraction.
    """
    eng = root / "engram"
    (eng / "_internal" / "certifi").mkdir(parents=True, exist_ok=True)
    (eng / "engram").write_bytes(b"bin")
    (eng / "_internal" / "base_library.zip").write_bytes(b"z")
    if complete:
        (eng / "_internal" / "certifi" / "cacert.pem").write_bytes(b"ca")
    return eng


def _streaming_client(archive_bytes: bytes, sums_text: str = ""):
    """An httpx.AsyncClient mock: .get returns sums_text, .stream yields archive_bytes."""
    mock_sums = MagicMock()
    mock_sums.raise_for_status = MagicMock()
    mock_sums.text = sums_text

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
    mock_client.get = AsyncMock(return_value=mock_sums)
    mock_client.stream = MagicMock(return_value=FakeStream())
    return mock_client


def _manifest_client(
    archive_bytes: bytes, manifest_url: str, manifest_text: str, sums_text: str = ""
):
    """Like _streaming_client, but .get routes by URL: manifest_url -> manifest_text,
    anything else (the checksum file) -> sums_text. Used to exercise the manifest
    fetch + enforcement path inside _download."""

    def _resp(text):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.text = text
        return r

    async def _get(url, *args, **kwargs):
        return _resp(manifest_text if url == manifest_url else sums_text)

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
    mock_client.get = AsyncMock(side_effect=_get)
    mock_client.stream = MagicMock(return_value=FakeStream())
    return mock_client


class TestExtractionIntegrity:
    """Atomic extraction + completeness verification (regression: truncated 0.14.0)."""

    def test_verify_extracted_ok_with_sentinels(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        eng = _make_build(tmp_path, complete=True)
        UpdateChecker()._verify_extracted(eng, None)  # must not raise

    def test_verify_extracted_missing_certifi_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        eng = _make_build(tmp_path, complete=False)  # no cacert.pem
        with pytest.raises(UpdateError, match="incomplete"):
            UpdateChecker()._verify_extracted(eng, None)

    def test_verify_extracted_manifest_missing_file_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        eng = _make_build(tmp_path, complete=True)
        # Manifest lists a file (onnxruntime) that wasn't extracted.
        manifest = (
            "aaaa  engram\n"
            "bbbb  _internal/base_library.zip\n"
            "cccc  _internal/certifi/cacert.pem\n"
            "dddd  _internal/onnxruntime/onnxruntime.dll\n"
        )
        with pytest.raises(UpdateError, match="manifest file"):
            UpdateChecker()._verify_extracted(eng, manifest)

    def test_verify_extracted_manifest_all_present_ok(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        eng = _make_build(tmp_path, complete=True)
        manifest = (
            "aaaa  engram\n"
            "bbbb  _internal/base_library.zip\n"
            "cccc *_internal/certifi/cacert.pem\n"  # binary-mode '*' prefix tolerated
        )
        UpdateChecker()._verify_extracted(eng, manifest)  # must not raise

    async def test_partial_extraction_does_not_become_ready(self, monkeypatch, tmp_path):
        """An incomplete extraction must end ERROR (never READY) and promote nothing."""
        checker = UpdateChecker()
        monkeypatch.setattr(checker, "_is_frozen", True)
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")

        # Extraction yields a launcher-only tree (no _internal) -> incomplete.
        def fake_extract(archive_path, dest_dir):
            eng = dest_dir / "engram"
            eng.mkdir(parents=True, exist_ok=True)
            (eng / "engram").write_bytes(b"bin")

        monkeypatch.setattr(checker, "_extract", fake_extract)

        mock_client = _streaming_client(b"archive-bytes")
        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                await checker._download(FAKE_RELEASE)

        assert checker.state == UpdateStatus.ERROR
        assert checker.staging_path is None
        # No half-extracted build was promoted under the staging base.
        assert not any(p.name == "engram" for p in tmp_path.rglob("engram") if p.is_dir())

    async def test_apply_update_reverifies_and_refuses_incomplete(self, monkeypatch, tmp_path):
        """apply_update re-checks the staged dir and refuses to swap an incomplete build."""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        monkeypatch.setattr("app.core.updater.async_session", factory)
        monkeypatch.setattr(sys, "platform", "linux")

        staged = tmp_path / "staged"
        _make_build(staged, complete=False)  # missing cacert.pem

        checker = UpdateChecker()
        checker._is_frozen = True
        checker.state = UpdateStatus.READY
        checker.staging_path = staged

        called = {"restart": False}
        monkeypatch.setattr(checker, "_restart_linux_macos", lambda: called.update(restart=True))
        monkeypatch.setattr(checker, "_restart_windows", lambda: called.update(restart=True))

        with pytest.raises(UpdateError, match="incomplete"):
            await checker.apply_update()
        assert called["restart"] is False
        # A failed pre-apply re-verify must drop out of READY and clear the staged
        # path, so the UI stops offering a broken update and retries don't loop.
        assert checker.state == UpdateStatus.ERROR
        assert checker.staging_path is None

    async def test_apply_update_complete_build_reaches_restart(self, monkeypatch, tmp_path):
        """A complete staged build passes re-verification and proceeds to restart."""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        monkeypatch.setattr("app.core.updater.async_session", factory)
        monkeypatch.setattr(sys, "platform", "linux")

        staged = tmp_path / "staged"
        _make_build(staged, complete=True)

        checker = UpdateChecker()
        checker._is_frozen = True
        checker.state = UpdateStatus.READY
        checker.staging_path = staged

        called = {"restart": False}
        monkeypatch.setattr(checker, "_restart_linux_macos", lambda: called.update(restart=True))

        await checker.apply_update()
        assert called["restart"] is True

    async def test_download_fetches_and_enforces_manifest(self, monkeypatch, tmp_path):
        """A release that ships a manifest: _download fetches it and rejects a build
        missing a listed file — proving the manifest path is wired end to end."""
        import io
        import tarfile

        checker = UpdateChecker()
        monkeypatch.setattr(checker, "_is_frozen", True)
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")

        # Complete-sentinel archive, but the manifest lists an extra file it lacks.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, content in (
                ("engram/engram", b"bin"),
                ("engram/_internal/base_library.zip", b"z"),
                ("engram/_internal/certifi/cacert.pem", b"ca"),
            ):
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        buf.seek(0)
        archive_bytes = buf.read()

        manifest_url = "https://example.com/engram-linux-x64.manifest.sha256"
        manifest_text = (
            "aaaa  engram\n"
            "bbbb  _internal/base_library.zip\n"
            "cccc  _internal/certifi/cacert.pem\n"
            "dddd  _internal/onnxruntime/onnxruntime.dll\n"  # not in the archive
        )
        release = {
            **FAKE_RELEASE,
            "assets": FAKE_RELEASE["assets"]
            + [{"name": "engram-linux-x64.manifest.sha256", "browser_download_url": manifest_url}],
        }
        mock_client = _manifest_client(archive_bytes, manifest_url, manifest_text)

        with patch("app.core.updater.httpx.AsyncClient", return_value=mock_client):
            with patch.object(checker, "_broadcast", AsyncMock()):
                await checker._download(release)

        # The manifest was fetched ...
        assert any(
            call.args and call.args[0] == manifest_url for call in mock_client.get.call_args_list
        )
        # ... and enforced: the sentinel files all exist, so the only thing that can
        # fail is the manifest's missing onnxruntime entry -> rejected, never READY.
        assert checker.state == UpdateStatus.ERROR
        assert checker.staging_path is None


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

    def test_renames_aside_before_delete(self, monkeypatch, tmp_path):
        """Prune must rename a doomed dir aside before deleting it.

        Regression: prune used shutil.rmtree in place; an interrupted rmtree left a
        half-deleted *real-version* dir (we saw a 181-of-825-file ``0.15.2`` tree that
        looked like a staged build). Renaming to a ``.pruning-*`` sibling first means a
        crash mid-delete can only ever leave an obviously-temporary dir, never a
        partial version dir. Simulate rmtree failing to remove anything and assert the
        original version name is gone regardless.
        """
        monkeypatch.setattr("app.core.updater.STAGING_BASE", tmp_path)
        (tmp_path / "0.11.0").mkdir()
        # rmtree can't finish (e.g. a locked file) — the doomed dir must already have
        # been renamed out of the way so it's never seen as "0.11.0" again.
        monkeypatch.setattr("app.core.updater.shutil.rmtree", lambda *a, **k: None)

        checker = UpdateChecker()
        checker._current_version = "0.12.1"
        checker._prune_staging()

        names = sorted(p.name for p in tmp_path.iterdir())
        assert "0.11.0" not in names  # renamed aside, not left as a partial version dir
        assert names and all(".pruning-" in n for n in names)  # leftover clearly marked


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

    def test_spawns_with_neutral_cwd(self, monkeypatch):
        """Helper must be spawned with an explicit cwd OFF the install dir.

        Regression (confirmed in an isolated swap harness): Popen was called with no
        ``cwd=``, so the detached ``cmd /c bat`` inherited engram's working directory —
        the install dir for a double-clicked onedir exe. A process whose cwd is a
        directory holds an open handle on it, so the helper's own ``move install -> .old``
        failed with "being used by another process" and every restart silently rolled
        back. The spawn must pin a neutral, existing cwd.
        """
        seen = []
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **kw: seen.append(kw.get("cwd")) or MagicMock()
        )
        UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

        assert seen, "Popen was never called"
        assert seen[0] is not None, "helper spawned with no cwd — inherits the install dir"
        assert Path(seen[0]).is_dir()

    def test_fallback_spawn_also_gets_neutral_cwd(self, monkeypatch):
        """The plain-detached fallback path must also pin the neutral cwd."""
        breakaway = self._breakaway_flag()
        seen = []

        def fake_popen(*a, **kw):
            seen.append(kw.get("cwd"))
            if kw.get("creationflags", 0) & breakaway:
                raise OSError("Access is denied")
            return MagicMock()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

        assert len(seen) == 2
        assert all(c is not None and Path(c).is_dir() for c in seen)


# Representative real-world paths: the user runs out of a parens'd Downloads folder
# and staging lives under ~/.engram on a (possibly) different volume.
_BAT_ARGS = dict(
    src=r"C:\Users\jonat\.engram\update\0.16.0\engram",
    install=r"C:\Users\jonat\Downloads\engram-windows-x64(8)\engram",
    new_dir=r"C:\Users\jonat\Downloads\engram-windows-x64(8)\engram.new-0.16.0",
    old_dir=r"C:\Users\jonat\Downloads\engram-windows-x64(8)\engram.old-0.16.0",
    log_path=r"C:\Users\jonat\.engram\update_helper.log",
    exe="engram.exe",
    pid=28628,
    version="0.16.0",
    result_path=r"C:\Users\jonat\.engram\update_result.txt",
)


class TestRenderUpdateBat:
    """The Windows update helper must swap atomically, verify, roll back, and log.

    The bat is rendered as a pure string so it's testable on non-Windows CI without
    executing it. These assertions pin the failure modes that bricked the live
    install: in-place overwrite, unchecked exit codes, no verification, no rollback,
    no logging.
    """

    def _bat(self):
        return UpdateChecker._render_update_bat(**_BAT_ARGS)

    def test_copies_to_sibling_not_in_place(self):
        """robocopy must target the .new sibling, never the live install dir."""
        bat = self._bat()
        assert "xcopy" not in bat.lower()  # the old, in-place, merge-y approach is gone
        assert "robocopy" in bat
        assert "/MIR" in bat  # true replace, not a merge
        assert 'robocopy "%SRC%" "%NEWDIR%"' in bat
        # robocopy's destination is the sibling, not the install:
        assert 'robocopy "%SRC%" "%INSTALL%"' not in bat

    def test_verifies_all_four_sentinels_in_new_dir(self):
        bat = self._bat()
        for sentinel in (
            r"engram.exe",
            r"_internal\base_library.zip",
            r"_internal\certifi\cacert.pem",
            r"_internal\app\static\index.html",  # the real frontend location
        ):
            assert f'if not exist "%NEWDIR%\\{sentinel}" goto fail' in bat

    def test_atomic_two_rename_swap(self):
        bat = self._bat()
        assert 'move "%INSTALL%" "%OLDDIR%"' in bat  # install aside
        assert 'move "%NEWDIR%" "%INSTALL%"' in bat  # new into place

    def test_clears_stale_old_dir_before_swap(self):
        """A leftover .old from a prior failed run must be cleared before the swap,
        or `move install -> old` fails and a retry can't succeed."""
        bat = self._bat()
        assert bat.index('rmdir /S /Q "%OLDDIR%"') < bat.index('move "%INSTALL%" "%OLDDIR%"')

    def test_checks_exit_codes(self):
        bat = self._bat()
        # robocopy's exit is captured first (echo resets ERRORLEVEL) then gated; success
        # is < 8. The gate may read the captured var rather than %ERRORLEVEL% directly.
        assert "GEQ 8 goto fail" in bat
        assert bat.count("if errorlevel 1") >= 2  # one per move

    def test_rollback_restores_previous_install(self):
        bat = self._bat()
        assert ":restore_old" in bat
        assert ":relaunch_old" in bat
        assert 'move "%OLDDIR%" "%INSTALL%"' in bat  # put the old install back
        # The failed-move branch jumps to the restore path:
        assert "goto restore_old" in bat

    def test_logs_every_step(self):
        bat = self._bat()
        assert "update_helper.log" in bat
        assert '>> "%LOG%" 2>&1' in bat  # robocopy/move output captured

    def test_deletes_bat_only_on_success(self):
        bat = self._bat()
        assert bat.count('del "%~f0"') == 1
        # The single delete sits in the success path, before the rollback labels:
        assert bat.index('del "%~f0"') < bat.index(":restore_old")
        # ...and the rollback/relaunch tail never deletes the evidence:
        assert 'del "%~f0"' not in bat[bat.index(":relaunch_old") :]

    def test_waits_for_pid_then_grace_using_ping(self):
        bat = self._bat()
        assert f'tasklist /FI "PID eq {_BAT_ARGS["pid"]}"' in bat
        assert "ping -n" in bat  # PID poll + post-exit grace
        # The `timeout` CMD built-in aborts in a console-less context; must not be used as a
        # command. The word may appear in log messages (e.g. "timed out"), so check for it as
        # a standalone command token (newline + "timeout ") rather than a bare substring.
        assert "\ntimeout " not in bat  # timeout cmd aborts in a console-less context

    def test_preserves_parens_in_paths(self):
        bat = self._bat()
        assert r'set "INSTALL=C:\Users\jonat\Downloads\engram-windows-x64(8)\engram"' in bat

    def test_changes_cwd_off_install_before_swap(self):
        """The helper must cd off the install dir before the swap renames.

        Regression (reproduced in an isolated swap harness): the detached helper
        inherited engram's cwd (the install dir). A directory that is any process's
        current directory can't be renamed, so ``move install -> .old`` failed and the
        update silently rolled back on every attempt. Changing the helper's own cwd to a
        neutral dir releases that handle. Must occur before the first ``move``.
        """
        bat = self._bat()
        assert 'cd /d "%TEMP%"' in bat
        assert bat.index('cd /d "%TEMP%"') < bat.index('move "%INSTALL%" "%OLDDIR%"')

    def test_logs_diagnostic_context_before_swap(self):
        """The helper echoes its cwd + key paths so a field failure is diagnosable
        from update_helper.log alone (the capability that was missing every prior time)."""
        bat = self._bat()
        assert "%CD%" in bat  # the resolved working directory
        assert "%INSTALL%" in bat
        # robocopy's real exit code is recorded before the GEQ-8 gate
        assert "%ERRORLEVEL%" in bat

    def test_emits_done_marker_on_terminal_paths(self):
        """Every terminal branch writes a final marker, so a log that stops mid-step is
        distinguishable from a clean (if failed) finish."""
        bat = self._bat()
        assert "[engram-update] done" in bat


class TestRestartWindowsWiring:
    """_restart_windows wires the renderer to a detached, job-breakaway helper."""

    def test_writes_bat_and_spawns_detached(self, monkeypatch, tmp_path):
        # Pretend a complete staged build and an install dir both exist on disk.
        staging = tmp_path / "update" / "0.16.0"
        (staging / "engram").mkdir(parents=True)
        install = tmp_path / "install"
        install.mkdir()
        temp = tmp_path / "temp"
        temp.mkdir()

        monkeypatch.setenv("TEMP", str(temp))
        # Path.home() resolves via HOME/USERPROFILE — point both at tmp so the helper
        # log lands under the test dir, not the real ~/.engram.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setattr("app.core.updater.sys.executable", str(install / "engram.exe"))

        checker = UpdateChecker()
        checker.staging_path = staging
        checker.latest_version = "0.16.0"

        spawned = {}
        monkeypatch.setattr(
            checker, "_spawn_detached_helper", lambda args: spawned.update(args=args)
        )

        def fake_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr("app.core.updater.os._exit", fake_exit)

        with pytest.raises(SystemExit):
            checker._restart_windows()

        bat_path = temp / "engram_update.bat"
        assert bat_path.exists()
        content = bat_path.read_text()
        assert "robocopy" in content
        # Helper is launched via cmd /c <bat>, breakaway handled by _spawn_detached_helper.
        assert spawned["args"][:2] == ["cmd", "/c"]
        assert spawned["args"][2] == str(bat_path)


def test_spawn_detached_helper_uses_create_no_window(monkeypatch):
    """The helper must run in a hidden console (CREATE_NO_WINDOW), not console-less
    DETACHED_PROCESS — a cmd pipe (tasklist | find) deadlocks without a console."""
    captured = {}

    def fake_popen(args, **kwargs):
        captured["flags"] = kwargs.get("creationflags", 0)

        class _P:  # minimal stand-in
            pass

        return _P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    assert captured["flags"] & CREATE_NO_WINDOW, "must use CREATE_NO_WINDOW"
    assert not (captured["flags"] & DETACHED_PROCESS), "must NOT use DETACHED_PROCESS"


def _bat():
    return UpdateChecker._render_update_bat(
        src=r"C:\stage\engram",
        install=r"C:\app\engram",
        new_dir=r"C:\app\engram.new",
        old_dir=r"C:\app\engram.old",
        log_path=r"C:\u\.engram\update_helper.log",
        exe="engram.exe",
        pid=4321,
        version="9.9.9",
        result_path=r"C:\u\.engram\update_result.txt",
    )


def test_render_bat_wait_is_bounded_and_image_filtered():
    bat = _bat()
    assert "IMAGENAME eq engram.exe" in bat  # don't wedge on a reused PID
    assert "set /a WAITED" in bat  # bounded counter exists
    assert "if %WAITED% GEQ 10" in bat  # ~10s cap


def test_render_bat_relaunch_passes_updated_flag():
    bat = _bat()
    # both the success relaunch and the rollback relaunch suppress the new tab
    assert bat.count('"%EXE%" --updated') == 2


def test_render_bat_writes_result_marker():
    bat = _bat()
    assert 'echo result=success>"%RESULT%"' in bat
    assert 'echo result=failed>"%RESULT%"' in bat
    assert 'echo version=%VER%>>"%RESULT%"' in bat
    assert 'echo step=%STEP%>>"%RESULT%"' in bat


def test_restart_posix_appends_updated_flag(monkeypatch, tmp_path):
    """POSIX exec-in-place re-runs run.py, which would open a 2nd browser tab; the
    --updated flag suppresses it."""
    import os
    import shutil
    import sys

    from app.core.updater import UpdateChecker

    staging = tmp_path / "stage"
    (staging / "engram").mkdir(parents=True)
    (staging / "engram" / "engram").write_text("binary")

    checker = UpdateChecker()
    checker.staging_path = staging

    monkeypatch.setattr(shutil, "copy2", lambda *a, **k: None)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["/app/engram"])
    captured = {}
    monkeypatch.setattr(os, "execv", lambda path, argv: captured.update(path=path, argv=argv))

    checker._restart_linux_macos()
    assert "--updated" in captured["argv"]


def test_consume_marker_success(tmp_path):
    from app.core.updater import UpdateChecker

    marker = tmp_path / "update_result.txt"
    marker.write_text("result=success\nversion=9.9.9\n", encoding="utf-8")
    c = UpdateChecker()
    c._consume_update_result_marker(marker)
    assert c.last_update_success_version == "9.9.9"
    assert c.last_update_error is None
    assert not marker.exists()  # consumed once


def test_consume_marker_failed(tmp_path):
    from app.core.updater import UpdateChecker

    marker = tmp_path / "update_result.txt"
    marker.write_text("result=failed\nversion=9.9.9\nstep=verify\n", encoding="utf-8")
    c = UpdateChecker()
    c._consume_update_result_marker(marker)
    assert "9.9.9" in c.last_update_error and "verify" in c.last_update_error
    assert c.last_update_success_version is None
    assert not marker.exists()


def test_consume_marker_absent_is_noop(tmp_path):
    from app.core.updater import UpdateChecker

    c = UpdateChecker()
    c._consume_update_result_marker(tmp_path / "nope.txt")
    assert c.last_update_error is None and c.last_update_success_version is None


def test_consume_marker_unlink_error_is_swallowed(tmp_path, monkeypatch):
    """A non-FileNotFound OSError on unlink (e.g. PermissionError) must not escape the
    best-effort consumer — the parsed result is still applied and start() can't crash."""
    from pathlib import Path

    from app.core.updater import UpdateChecker

    marker = tmp_path / "update_result.txt"
    marker.write_text("result=success\nversion=9.9.9\n", encoding="utf-8")

    def boom(self, missing_ok=False):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", boom)

    c = UpdateChecker()
    c._consume_update_result_marker(marker)  # must not raise
    assert c.last_update_success_version == "9.9.9"


def test_get_status_includes_marker_fields():
    from app.core.updater import UpdateChecker

    c = UpdateChecker()
    c.last_update_error = "boom"
    c.last_update_success_version = "9.9.9"
    s = c.get_status()
    assert s["last_update_error"] == "boom"
    assert s["last_update_success_version"] == "9.9.9"


from app.services.event_broadcaster import EventBroadcaster  # noqa: E402


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
async def test_broadcast_update_status_carries_marker_fields():
    ws = _FakeWS()
    eb = EventBroadcaster(ws)
    await eb.broadcast_update_status(
        state="ready", last_update_error="boom", last_update_success_version="9.9.9"
    )
    payload = ws.sent[-1]
    assert payload["last_update_error"] == "boom"
    assert payload["last_update_success_version"] == "9.9.9"


# ---------------------------------------------------------------------------
# Task 8: End-to-end Windows swap via the real _spawn_detached_helper
# ---------------------------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows helper only")


def _t8_make_build(root: Path, exe_name: str, marker_text: str) -> None:
    """Create a fake onedir build at *root* with all four sentinels present."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.environ["COMSPEC"], root / exe_name)  # cmd clone -> IMAGENAME == exe_name
    (root / "_internal" / "certifi").mkdir(parents=True, exist_ok=True)
    (root / "_internal" / "app" / "static").mkdir(parents=True, exist_ok=True)
    (root / "_internal" / "base_library.zip").write_text("z")
    (root / "_internal" / "certifi" / "cacert.pem").write_text("ca")
    (root / "_internal" / "app" / "static" / "index.html").write_text("<html>")
    (root / "VERSION.txt").write_text(marker_text)


def _t8_run_helper(
    checker: UpdateChecker,
    *,
    src: Path,
    install: Path,
    new_dir: Path,
    old_dir: Path,
    log: Path,
    result: Path,
    exe: str,
    pid: int,
    version: str,
) -> None:
    """Render the bat, rewrite the self-delete to use the literal bat path, and spawn it."""
    bat = checker._render_update_bat(
        src=str(src),
        install=str(install),
        new_dir=str(new_dir),
        old_dir=str(old_dir),
        log_path=str(log),
        exe=exe,
        pid=pid,
        version=version,
        result_path=str(result),
    )
    bat_path = os.path.join(os.environ["TEMP"], f"engram_update_test_{pid}.bat")
    # The rendered bat has '(goto) 2>nul & del "%~f0"' for the success self-delete.
    # Rewrite it to delete THIS test bat by its literal path so it doesn't conflict
    # with other runs.
    bat = bat.replace('del "%~f0"', f'del "{bat_path}"')
    with open(bat_path, "w", newline="\r\n") as f:
        f.write(bat)
    checker._spawn_detached_helper(["cmd", "/c", bat_path])


def _t8_wait_for(predicate, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return False


@windows_only
def test_windows_swap_succeeds_end_to_end(tmp_path):
    """The real helper, spawned the real way, completes the swap and writes a success marker.

    This is the keystone regression test: the original bug was that the helper hung
    forever in the :wait loop because DETACHED_PROCESS (no console) made tasklist emit
    nothing, so the PID-check pipe never matched and the wait never exited. The fix
    switched to CREATE_NO_WINDOW (hidden console). This test spawns the real helper via
    the real _spawn_detached_helper and asserts it finishes within 30 seconds.
    """
    exe = "engram_t8a.exe"
    src = tmp_path / "stage" / "engram"
    install = tmp_path / "app" / "engram"
    _t8_make_build(src, exe, "NEW")
    _t8_make_build(install, exe, "OLD")
    log = tmp_path / "helper.log"
    result = tmp_path / "update_result.txt"

    # Launch a long-lived dummy "parent" whose IMAGENAME matches exe exactly (cmd clone).
    parent = subprocess.Popen([str(install / exe), "/c", "ping -n 60 127.0.0.1 >nul"])
    try:
        checker = UpdateChecker()
        _t8_run_helper(
            checker,
            src=src,
            install=install,
            new_dir=tmp_path / "app" / "engram.new",
            old_dir=tmp_path / "app" / "engram.old",
            log=log,
            result=result,
            exe=exe,
            pid=parent.pid,
            version="9.9.9",
        )
        time.sleep(2)  # let the helper enter the :wait loop before we kill the parent
        parent.terminate()  # the :wait loop observes the exit and proceeds with the swap
        ok = _t8_wait_for(lambda: result.exists())
        log_text = log.read_text() if log.exists() else "<no log>"
        assert ok, f"no result marker written within 30s; helper log:\n{log_text}"
        text = result.read_text()
        assert "result=success" in text, (
            f"unexpected result:\n{text}\n--- helper log ---\n{log_text}"
        )
        assert (install / "VERSION.txt").read_text() == "NEW", "swap did not happen"
    finally:
        if parent.poll() is None:
            parent.terminate()
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)


@windows_only
def test_windows_swap_failure_writes_marker_and_keeps_install(tmp_path):
    """A missing sentinel causes verify to fail -> failure marker written, install left intact."""
    exe = "engram_t8b.exe"
    src = tmp_path / "stage" / "engram"
    install = tmp_path / "app" / "engram"
    _t8_make_build(src, exe, "NEW")
    # Remove the index.html sentinel so the verify step in the bat fails.
    (src / "_internal" / "app" / "static" / "index.html").unlink()
    _t8_make_build(install, exe, "OLD")
    log = tmp_path / "helper.log"
    result = tmp_path / "update_result.txt"

    parent = subprocess.Popen([str(install / exe), "/c", "ping -n 60 127.0.0.1 >nul"])
    try:
        checker = UpdateChecker()
        _t8_run_helper(
            checker,
            src=src,
            install=install,
            new_dir=tmp_path / "app" / "engram.new",
            old_dir=tmp_path / "app" / "engram.old",
            log=log,
            result=result,
            exe=exe,
            pid=parent.pid,
            version="9.9.9",
        )
        time.sleep(2)
        parent.terminate()
        ok = _t8_wait_for(lambda: result.exists())
        log_text = log.read_text() if log.exists() else "<no log>"
        assert ok, f"no result marker written within 30s; helper log:\n{log_text}"
        text = result.read_text()
        assert "result=failed" in text, (
            f"unexpected result:\n{text}\n--- helper log ---\n{log_text}"
        )
        assert (install / "VERSION.txt").read_text() == "OLD", (
            "install was modified (should be untouched)"
        )
    finally:
        if parent.poll() is None:
            parent.terminate()
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
