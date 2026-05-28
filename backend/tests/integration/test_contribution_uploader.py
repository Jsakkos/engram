"""Integration tests for Phase 2: ContributionUploader + privacy endpoints."""

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import app.services.contribution_uploader as uploader_mod
from app.database import async_session, init_db
from app.main import app
from app.models.app_config import AppConfig
from app.models.fingerprint import FingerprintContribution
from app.services.contribution_uploader import _MAX_ATTEMPTS, ContributionUploader


def _make_valid_blob() -> bytes:
    """Create a minimal valid ChromaprintResult blob for tests."""
    from app.matcher.chromaprint_extractor import ChromaprintResult

    return ChromaprintResult(
        hashes=[1, 2, 3], duration_seconds=42.0, fpcalc_version="test"
    ).to_blob()


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM fingerprint_contributions"))
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Async HTTP client backed by the FastAPI app under test."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def test_fingerprint_contribution_has_upload_status_fields():
    """FingerprintContribution model has upload_status and upload_error_msg."""
    row = FingerprintContribution(
        chromaprint_blob=b"\x01\x02",
        tmdb_id=1399,
        season=1,
        episode=7,
        match_confidence=0.9,
        match_source="engram_asr",
        pseudonym="11111111-1111-4111-8111-111111111111",
    )
    assert hasattr(row, "upload_status"), "FingerprintContribution missing upload_status"
    assert hasattr(row, "upload_error_msg"), "FingerprintContribution missing upload_error_msg"
    assert row.upload_status is None
    assert row.upload_error_msg is None


def test_app_config_has_fingerprint_server_url():
    """AppConfig exposes fingerprint_server_url (None by default)."""
    cfg = AppConfig()
    assert hasattr(cfg, "fingerprint_server_url")
    assert cfg.fingerprint_server_url is None


@pytest.mark.asyncio
async def test_uploader_skips_when_no_server_url(setup_db):
    """If fingerprint_server_url is not set, _process_batch is a no-op."""
    from unittest.mock import AsyncMock, patch

    from app.database import async_session

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=b"\xde\xad",
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        session.add(row)
        await session.commit()

    uploader = ContributionUploader()
    post_mock = AsyncMock()
    with patch("httpx.AsyncClient.post", post_mock):
        await uploader._process_batch()

    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_uploader_posts_pending_contributions(setup_db, tmp_path, monkeypatch):
    """Successful POST marks row upload_status='success' and writes audit log."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.database import async_session

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=7,
            match_confidence=0.95,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        monkeypatch.setattr(
            uploader_mod,
            "get_config",
            AsyncMock(
                return_value=MagicMock(
                    fingerprint_server_url="https://fp.example.com",
                    contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._process_batch()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    assert refreshed.upload_status == "success"
    assert refreshed.uploaded_at is not None

    log_path = tmp_path / "contrib.jsonl"
    assert log_path.exists()
    line = json.loads(log_path.read_text().strip())
    assert line["contrib_id"] == contrib_id
    assert len(line["pseudonym_prefix"]) == 8


@pytest.mark.asyncio
async def test_uploader_marks_failed_on_4xx(setup_db, monkeypatch):
    """A 4xx response permanently marks the row upload_status='failed'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.database import async_session

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1,
            season=1,
            episode=1,
            match_confidence=0.5,
            match_source="engram_asr",
            pseudonym="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    exc = httpx.HTTPStatusError("422", request=MagicMock(), response=MagicMock(status_code=422))

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=exc)
        MockClient.return_value = mock_client

        monkeypatch.setattr(
            uploader_mod,
            "get_config",
            AsyncMock(
                return_value=MagicMock(
                    fingerprint_server_url="https://fp.example.com",
                    contribution_pseudonym="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._process_batch()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    assert refreshed.upload_status == "failed"
    assert "422" in (refreshed.upload_error_msg or "")


@pytest.mark.asyncio
async def test_uploader_starts_and_stops_cleanly():
    """ContributionUploader.start() spawns a task; stop() cancels it cleanly.

    This validates the lifespan interface: main.py calls start() on startup
    and stop() on shutdown. ASGITransport does not trigger lifespan events,
    so we test the uploader's own lifecycle directly.
    """
    uploader = ContributionUploader(poll_interval_seconds=3600)
    await uploader.start()
    assert uploader._task is not None
    assert not uploader._task.done()
    await uploader.stop()
    assert uploader._task.done()


@pytest.mark.asyncio
async def test_forget_endpoint_deletes_pending_contribution(setup_db, client):
    """DELETE /api/fingerprint/contributions/{id} removes a pending row."""
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            row = FingerprintContribution(
                chromaprint_blob=b"\x01",
                tmdb_id=99,
                season=1,
                episode=1,
                match_confidence=0.8,
                match_source="engram_asr",
                pseudonym="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            contrib_id = row.id

        resp = await client.delete(f"/api/fingerprint/contributions/{contrib_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Second delete → 404
        resp2 = await client.delete(f"/api/fingerprint/contributions/{contrib_id}")
        assert resp2.status_code == 404
    finally:
        app.dependency_overrides.pop(require_localhost, None)


@pytest.mark.asyncio
async def test_forget_endpoint_rejects_uploaded_contribution(setup_db, client):
    """Cannot delete an already-uploaded contribution (data already on server)."""
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            row = FingerprintContribution(
                chromaprint_blob=b"\x02",
                tmdb_id=88,
                season=2,
                episode=3,
                match_confidence=0.9,
                match_source="engram_asr",
                pseudonym="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                upload_status="success",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            contrib_id = row.id

        resp = await client.delete(f"/api/fingerprint/contributions/{contrib_id}")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(require_localhost, None)


@pytest.mark.asyncio
async def test_rotate_pseudonym_resets_pending_rows(setup_db, client):
    """POST rotate-pseudonym updates pending rows and app_config; leaves uploaded rows."""
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app
    from app.services.contribution_pseudonym import validate_pseudonym

    old_pseudonym = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            pending = FingerprintContribution(
                chromaprint_blob=b"\x03",
                tmdb_id=7,
                season=1,
                episode=1,
                match_confidence=0.9,
                match_source="engram_asr",
                pseudonym=old_pseudonym,
            )
            uploaded = FingerprintContribution(
                chromaprint_blob=b"\x04",
                tmdb_id=8,
                season=1,
                episode=2,
                match_confidence=0.9,
                match_source="engram_asr",
                pseudonym=old_pseudonym,
                upload_status="success",
            )
            session.add(pending)
            session.add(uploaded)
            await session.commit()
            await session.refresh(pending)
            await session.refresh(uploaded)
            pending_id = pending.id
            uploaded_id = uploaded.id

        resp = await client.post("/api/fingerprint/contributions/rotate-pseudonym")
        assert resp.status_code == 200
        data = resp.json()
        assert validate_pseudonym(data["pseudonym"])
        assert data["pseudonym"] != old_pseudonym
        assert data["pending_retagged"] >= 1

        async with async_session() as session:
            p = await session.get(FingerprintContribution, pending_id)
            u = await session.get(FingerprintContribution, uploaded_id)

        assert p.pseudonym == data["pseudonym"]  # retagged
        assert u.pseudonym == old_pseudonym  # unchanged
    finally:
        app.dependency_overrides.pop(require_localhost, None)


def test_append_audit_log_writes_correct_fields(tmp_path, monkeypatch):
    """_append_audit_log writes a JSON line with expected fields; pseudonym_prefix is 8 chars."""
    from datetime import UTC, datetime

    log_path = tmp_path / "contrib.jsonl"
    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", log_path)

    contrib = FingerprintContribution(
        id=42,
        chromaprint_blob=b"\x00",
        tmdb_id=1399,
        season=3,
        episode=5,
        match_confidence=0.97,
        match_source="bootstrap",
        pseudonym="12345678-1234-4234-8234-123456789abc",
        uploaded_at=datetime.now(UTC),
    )
    ContributionUploader._append_audit_log(contrib)

    assert log_path.exists()
    line = json.loads(log_path.read_text().strip())
    assert line["contrib_id"] == 42
    assert line["tmdb_id"] == 1399
    assert line["season"] == 3
    assert line["episode"] == 5
    assert line["pseudonym_prefix"] == "12345678"  # first 8 chars only
    assert "ts" in line


@pytest.mark.asyncio
async def test_uploader_increments_attempts_on_5xx(setup_db, monkeypatch):
    """A 5xx response exhausts retries and marks upload_status='failed'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.database import async_session

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=2,
            season=1,
            episode=1,
            match_confidence=0.8,
            match_source="engram_asr",
            pseudonym="gggggggg-gggg-4ggg-8ggg-gggggggggggg",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    exc = httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock(status_code=503))
    with patch("httpx.AsyncClient") as MockClient, patch("asyncio.sleep", AsyncMock()):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=exc)
        MockClient.return_value = mock_client

        monkeypatch.setattr(
            uploader_mod,
            "get_config",
            AsyncMock(
                return_value=MagicMock(
                    fingerprint_server_url="https://fp.example.com",
                    contribution_pseudonym="gggggggg-gggg-4ggg-8ggg-gggggggggggg",
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._process_batch()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # After _MAX_ATTEMPTS transient failures the row is permanently failed
    assert refreshed.upload_status == "failed"
    assert refreshed.upload_attempts == _MAX_ATTEMPTS
