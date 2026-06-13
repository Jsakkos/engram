"""Integration tests for Phase 2: ContributionUploader + privacy endpoints."""

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlmodel import select

import app.services.contribution_uploader as uploader_mod
from app.database import async_session, init_db
from app.main import app
from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL, AppConfig
from app.models.fingerprint import DiscContribution, FingerprintContribution

ContributionUploader = uploader_mod.ContributionUploader
_MAX_ATTEMPTS = uploader_mod._MAX_ATTEMPTS


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
        await session.execute(text("DELETE FROM disc_contributions"))
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
    """AppConfig defaults fingerprint_server_url to the network base origin.

    Asserted constant-relative (not the literal string) so de-personalizing the
    URL is a one-line edit to DEFAULT_FINGERPRINT_SERVER_URL. The default must be
    the BASE (no /v1 suffix) — the uploader appends /v1/contribute, so a /v1 here
    would double to /v1/v1/... and 404.
    """
    cfg = AppConfig()
    assert hasattr(cfg, "fingerprint_server_url")
    assert cfg.fingerprint_server_url == DEFAULT_FINGERPRINT_SERVER_URL
    assert not cfg.fingerprint_server_url.endswith("/v1")


def test_curator_routes_fallback_through_constant():
    """curator.py must use DEFAULT_FINGERPRINT_SERVER_URL for its server-URL
    fallback, not a re-hardcoded literal. Guarantees the URL value lives in
    exactly one place (app_config.py), so de-personalizing is a one-line edit.
    """
    import inspect

    import app.core.curator as curator_mod

    source = inspect.getsource(curator_mod)
    # The durable guard: curator must reference the shared constant by name. This
    # holds regardless of the URL's value, so it survives the upcoming rename.
    assert "DEFAULT_FINGERPRINT_SERVER_URL" in source, (
        "curator.py should reference the shared constant for its server-URL fallback"
    )
    # Belt-and-suspenders catch for the *current* hostname while it still ends in
    # .workers.dev. DURABILITY LIMIT (revisit at URL migration): getsource() also
    # scans comments/strings, and once the URL no longer ends in .workers.dev this
    # check can no longer catch a re-hardcoded literal — the assertion above is the
    # one that keeps protecting the single-source-of-truth invariant.
    assert ".workers.dev" not in source, (
        "curator.py must not hardcode a fingerprint host literal; route through "
        "DEFAULT_FINGERPRINT_SERVER_URL instead"
    )


@pytest.mark.asyncio
async def test_uploader_falls_back_to_default_url_when_unset(setup_db, monkeypatch):
    """A NULL stored URL resolves to DEFAULT_FINGERPRINT_SERVER_URL (existing
    installs whose column predates this feature still upload)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.database import async_session

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        session.add(row)
        await session.commit()

    # Stored URL is None — the uploader must fall back to the default base.
    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url=None,
                contribution_pseudonym="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()

    mock_client.post.assert_called_once()
    posted_url = mock_client.post.call_args[0][0]
    assert posted_url == f"{DEFAULT_FINGERPRINT_SERVER_URL}/v1/contribute"


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
                    enable_fingerprint_contributions=True,
                    fingerprint_disclosure_accepted=True,
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._drain()

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
                    enable_fingerprint_contributions=True,
                    fingerprint_disclosure_accepted=True,
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._drain()

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
async def test_forget_endpoint_rejects_in_flight_contribution(setup_db, client):
    """Cannot delete a row with upload_attempts > 0 (may be in-flight)."""
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            row = FingerprintContribution(
                chromaprint_blob=b"\x05",
                tmdb_id=77,
                season=1,
                episode=1,
                match_confidence=0.7,
                match_source="engram_asr",
                pseudonym="hhhhhhhh-hhhh-4hhh-8hhh-hhhhhhhhhhhh",
                upload_attempts=1,  # attempted at least once → treat as in-flight
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            contrib_id = row.id

        resp = await client.delete(f"/api/fingerprint/contributions/{contrib_id}")
        assert resp.status_code == 409
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
async def test_uploader_posts_wire_format_v1(setup_db, tmp_path, monkeypatch):
    """_upload_one POSTs the v1 wire format: fingerprint_b64 (zstd-varint), sha256, version."""
    import base64
    from unittest.mock import AsyncMock, MagicMock, patch

    import app as app_mod
    from app.database import async_session
    from app.services.zstd_varint_codec import decode_zstd_varint

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=2,
            episode=5,
            match_confidence=0.88,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            disc_content_hash=b"\x01\x02\x03\x04",
        )
        session.add(row)
        await session.commit()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    captured_payload: dict = {}

    async def fake_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return mock_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        MockClient.return_value = mock_client

        monkeypatch.setattr(
            uploader_mod,
            "get_config",
            AsyncMock(
                return_value=MagicMock(
                    fingerprint_server_url="https://fp.example.com",
                    contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    enable_fingerprint_contributions=True,
                    fingerprint_disclosure_accepted=True,
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._drain()

    # The payload must have exactly these keys
    expected_keys = {
        "wire_format_version",
        "pseudonym",
        "tmdb_id",
        "season",
        "episode",
        "fingerprint_b64",
        "fingerprint_sha256_b64",
        "disc_content_hash_b64",
        "match_confidence",
        "match_source",
        "client_version",
    }
    assert set(captured_payload.keys()) == expected_keys, (
        f"Payload keys mismatch. Got: {set(captured_payload.keys())}"
    )

    # wire_format_version must be 1
    assert captured_payload["wire_format_version"] == 1

    # fingerprint_b64 decodes → zstd-varint → [1, 2, 3]
    fp_bytes = base64.b64decode(captured_payload["fingerprint_b64"])
    assert decode_zstd_varint(fp_bytes) == [1, 2, 3]

    # disc_content_hash_b64 decodes to the raw bytes (not hex)
    assert base64.b64decode(captured_payload["disc_content_hash_b64"]) == b"\x01\x02\x03\x04"

    # client_version matches the running app version
    assert captured_payload["client_version"] == app_mod.__version__


@pytest.mark.asyncio
async def test_uploader_increments_attempts_on_5xx(setup_db, monkeypatch):
    """A 5xx response consumes the per-drain retry budget but leaves the row
    recoverable (upload_status stays None) — transient errors never permanently
    burn a row, so a later drain can retry once the server recovers."""
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
                    enable_fingerprint_contributions=True,
                    fingerprint_disclosure_accepted=True,
                )
            ),
        )
        uploader = ContributionUploader()
        await uploader._drain()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # 5xx is transient: the per-drain budget is consumed (attempts == _MAX_ATTEMPTS)
    # but the row stays pending (None), not permanently "failed" — a later drain
    # retries it once the upstream outage clears.
    assert refreshed.upload_status is None
    assert refreshed.upload_attempts == _MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_uploader_skips_when_opted_out(setup_db, monkeypatch):
    """If enable_fingerprint_contributions is False, _drain is a no-op."""
    from unittest.mock import AsyncMock, MagicMock, patch

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
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                enable_fingerprint_contributions=False,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock()
        MockClient.return_value = mock_client

        uploader = ContributionUploader()
        await uploader._drain()

        mock_client.post.assert_not_called()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)
    assert refreshed.upload_status is None


@pytest.mark.asyncio
async def test_uploader_prompts_when_disclosure_not_accepted(setup_db, monkeypatch):
    """When disclosure is not accepted, fires WS event and uploads nothing."""
    from unittest.mock import AsyncMock, MagicMock, patch

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
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=False,
                contribution_pseudonym="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            )
        ),
    )

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch(
            "app.services.event_broadcaster.EventBroadcaster.broadcast_fingerprint_disclosure_required",
            new_callable=AsyncMock,
        ) as mock_broadcast,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock()
        MockClient.return_value = mock_client

        uploader = ContributionUploader()
        await uploader._drain()

        mock_client.post.assert_not_called()

    mock_broadcast.assert_called_once_with(
        1, "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "https://fp.example.com"
    )

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)
    assert refreshed.upload_status is None


@pytest.mark.asyncio
async def test_uploader_uploads_when_all_gates_pass(setup_db, tmp_path, monkeypatch):
    """When all three privacy gates pass, _drain uploads and marks success."""
    from unittest.mock import AsyncMock, MagicMock, patch

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

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        uploader = ContributionUploader()
        await uploader._drain()

        mock_client.post.assert_called_once()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)
    assert refreshed.upload_status == "success"


@pytest.mark.asyncio
async def test_server_forget_calls_remote_rotates_and_resets(setup_db, client):
    """POST /api/fingerprint/forget calls the remote server, wipes pending rows,
    rotates pseudonym, and resets disclosure consent."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app
    from app.services.config_service import update_config as update_db_config

    old_pseudonym = "11111111-1111-4111-8111-111111111111"
    await update_db_config(
        contribution_pseudonym=old_pseudonym,
        fingerprint_server_url="https://fp.example.com",
        fingerprint_disclosure_accepted=True,
    )

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=b"\x01",
            tmdb_id=99,
            season=1,
            episode=1,
            match_confidence=0.8,
            match_source="engram_asr",
            pseudonym=old_pseudonym,
        )
        session.add(row)
        await session.commit()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"rows_deleted": 5, "canonical_unaffected": True})

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client

            resp = await client.post("/api/fingerprint/forget")

        assert resp.status_code == 200
        data = resp.json()
        assert data["server_rows_deleted"] == 5
        assert data["old_pseudonym"] == old_pseudonym
        assert data["new_pseudonym"] != old_pseudonym
        assert data["local_rows_deleted"] >= 1

        # GET /api/config must reflect the new pseudonym and reset consent
        config_resp = await client.get("/api/config")
        assert config_resp.status_code == 200
        config_data = config_resp.json()
        assert config_data["fingerprint_disclosure_accepted"] is False
        assert config_data["contribution_pseudonym"] == data["new_pseudonym"]
    finally:
        app.dependency_overrides.pop(require_localhost, None)


@pytest.mark.asyncio
async def test_server_forget_400_when_no_pseudonym(setup_db, client):
    """POST /api/fingerprint/forget returns 400 when no pseudonym is configured."""
    from sqlalchemy import text

    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    # Explicitly null out the pseudonym via raw SQL to ensure it's empty
    await init_db()
    async with async_session() as session:
        await session.execute(text("UPDATE app_config SET contribution_pseudonym = NULL"))
        await session.commit()

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        resp = await client.post("/api/fingerprint/forget")
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(require_localhost, None)


@pytest.mark.asyncio
async def test_contributions_endpoint_includes_audit_log(setup_db, client, tmp_path, monkeypatch):
    """?include_log=true tails the JSONL upload log into an audit_log key."""
    from app.api.routes import require_localhost
    from app.main import app

    log_path = tmp_path / "contrib.jsonl"
    log_path.write_text(
        json.dumps({"ts": "2026-05-28T00:00:00+00:00", "contrib_id": 1, "tmdb_id": 99}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", log_path)

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        # Without the flag, no audit_log key is present.
        resp_plain = await client.get("/api/fingerprint/contributions")
        assert resp_plain.status_code == 200
        assert "audit_log" not in resp_plain.json()

        resp = await client.get("/api/fingerprint/contributions?include_log=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "audit_log" in data
        assert any(e.get("tmdb_id") == 99 for e in data["audit_log"])
    finally:
        app.dependency_overrides.pop(require_localhost, None)


def test_retry_after_seconds_parses_integer():
    """A plain integer Retry-After header parses to float seconds."""
    assert uploader_mod._retry_after_seconds("60") == 60.0
    assert uploader_mod._retry_after_seconds(" 30 ") == 30.0
    assert uploader_mod._retry_after_seconds("0") == 0.0


def test_retry_after_seconds_returns_none_for_unparseable():
    """Absent or non-integer (e.g. HTTP-date) Retry-After falls back to None."""
    assert uploader_mod._retry_after_seconds(None) is None
    assert uploader_mod._retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert uploader_mod._retry_after_seconds("") is None
    assert uploader_mod._retry_after_seconds("-5") is None


@pytest.mark.asyncio
async def test_drain_uploads_all_rows_across_multiple_batches(setup_db, tmp_path, monkeypatch):
    """_drain loops batches until the queue empties, uploading every pending row."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from sqlmodel import select

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    # Small batch size so 5 rows span 3 batches (2 + 2 + 1).
    monkeypatch.setattr(uploader_mod, "_BATCH_SIZE", 2)

    async with async_session() as session:
        for i in range(5):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        drained = await ContributionUploader()._drain()

        # Every row uploaded, and a single shared client was constructed once.
        assert mock_client.post.call_count == 5
        assert MockClient.call_count == 1

    assert drained == 5
    async with async_session() as session:
        rows = (await session.execute(select(FingerprintContribution))).scalars().all()
    assert all(r.upload_status == "success" for r in rows)


@pytest.mark.asyncio
async def test_drain_bounds_concurrency_to_semaphore(setup_db, tmp_path, monkeypatch):
    """No more than _CONCURRENCY uploads are in flight at once."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    monkeypatch.setattr(uploader_mod, "_CONCURRENCY", 3)

    async with async_session() as session:
        for i in range(12):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    in_flight = 0
    max_in_flight = 0

    async def tracking_post(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await _asyncio.sleep(0.01)
        in_flight -= 1
        return mock_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=tracking_post)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    assert max_in_flight <= 3, f"concurrency exceeded semaphore: {max_in_flight}"


@pytest.mark.asyncio
async def test_upload_loop_drains_then_idles(monkeypatch):
    """_upload_loop drains, then sleeps the idle poll interval."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    uploader = ContributionUploader(poll_interval_seconds=900)
    drain_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(uploader, "_drain", drain_mock)

    sleep_calls: list[float] = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        raise _asyncio.CancelledError  # break the loop after one iteration

    monkeypatch.setattr(uploader_mod.asyncio, "sleep", fake_sleep)

    await uploader._upload_loop()  # CancelledError is caught → returns

    drain_mock.assert_awaited_once()
    assert sleep_calls == [900]


@pytest.mark.asyncio
async def test_uploader_retries_on_429_then_succeeds(setup_db, tmp_path, monkeypatch):
    """429 is transient: the row retries (honoring Retry-After) and can still succeed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    # First call → 429 with Retry-After: 30; second call → success.
    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={"Retry-After": "30"}),
    )
    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()

    sleep_durations: list[float] = []

    async def fake_sleep(duration):
        sleep_durations.append(duration)

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # Not marked permanently failed; eventually succeeded after retrying.
    assert refreshed.upload_status == "success"
    # The 429 backoff honored Retry-After (30s), not the 2**0 = 1s exponential default.
    assert 30.0 in sleep_durations


@pytest.mark.asyncio
async def test_uploader_429_falls_back_to_exponential_without_retry_after(
    setup_db, tmp_path, monkeypatch
):
    """429 without a Retry-After header falls back to exponential backoff and,
    like a 5xx, stays recoverable (upload_status=None) — never permanently failed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=2,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        contrib_id = row.id

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    # Always 429 with no Retry-After → exhausts the attempt budget, then fails.
    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={}),
    )

    with patch("httpx.AsyncClient") as MockClient, patch("asyncio.sleep", AsyncMock()):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=rate_limited)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    async with async_session() as session:
        refreshed = await session.get(FingerprintContribution, contrib_id)

    # 429 is transient: it consumes the per-drain budget like a 5xx but leaves the
    # row recoverable (None), not an instant 4xx fail and not a permanent "failed".
    assert refreshed.upload_status is None
    assert refreshed.upload_attempts == _MAX_ATTEMPTS


def test_uploader_default_poll_interval_is_900():
    """The default idle poll interval is 15 minutes (900s), not an hour."""
    assert ContributionUploader().poll_interval == 900


@pytest.mark.asyncio
async def test_uploader_429_retry_after_zero_is_immediate(setup_db, tmp_path, monkeypatch):
    """Retry-After: 0 means retry immediately — it must not be swallowed by `or`."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={"Retry-After": "0"}),
    )
    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()

    sleep_durations: list[float] = []

    async def fake_sleep(duration):
        sleep_durations.append(duration)

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    # Retry-After: 0 → backoff 0.0 (immediate), NOT the 2**0 = 1.0 exponential default.
    assert 0.0 in sleep_durations
    assert 1.0 not in sleep_durations


@pytest.mark.asyncio
async def test_uploader_caps_oversized_retry_after(setup_db, tmp_path, monkeypatch):
    """An absurd Retry-After is capped to _MAX_RETRY_AFTER so a slot can't stall for hours."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        row = FingerprintContribution(
            chromaprint_blob=_make_valid_blob(),
            tmdb_id=1399,
            season=1,
            episode=3,
            match_confidence=0.9,
            match_source="engram_asr",
            pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        session.add(row)
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    rate_limited = httpx.HTTPStatusError(
        "429",
        request=MagicMock(),
        response=MagicMock(status_code=429, headers={"Retry-After": "86400"}),
    )

    sleep_durations: list[float] = []

    async def fake_sleep(duration):
        sleep_durations.append(duration)

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=rate_limited)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    assert sleep_durations, "expected at least one backoff sleep"
    assert max(sleep_durations) <= uploader_mod._MAX_RETRY_AFTER
    assert 86400.0 not in sleep_durations


@pytest.mark.asyncio
async def test_drain_respects_midstream_optout(setup_db, tmp_path, monkeypatch):
    """Opting out mid-drain stops further batches (config is re-checked per batch)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from sqlmodel import select

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    monkeypatch.setattr(uploader_mod, "_BATCH_SIZE", 2)

    async with async_session() as session:
        for i in range(4):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    enabled = MagicMock(
        fingerprint_server_url="https://fp.example.com",
        contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        enable_fingerprint_contributions=True,
        fingerprint_disclosure_accepted=True,
    )
    disabled = MagicMock(
        fingerprint_server_url="https://fp.example.com",
        contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        enable_fingerprint_contributions=False,
        fingerprint_disclosure_accepted=True,
    )
    # pre-check (enabled), batch 1 (enabled), batch 2 (disabled → stop).
    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(side_effect=[enabled, enabled, disabled])
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        drained = await ContributionUploader()._drain()

    # Only the first batch of 2 uploaded; opt-out stopped the rest.
    assert drained == 2
    assert mock_client.post.call_count == 2
    async with async_session() as session:
        pending = (
            (
                await session.execute(
                    select(FingerprintContribution).where(
                        FingerprintContribution.upload_status.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_sustained_5xx_does_not_loop_and_recovers_on_later_drain(
    setup_db, tmp_path, monkeypatch
):
    """A sustained 5xx outage must NOT permanently burn rows.

    Regression guard for the lifetime-cap bug: during a server-side incident
    where every request 503s, a row used to exhaust _MAX_ATTEMPTS (a *lifetime*
    cap persisted across drains) and stick at upload_status='failed' with no
    automatic recovery — requiring a manual SQL reset. Now transient exhaustion
    leaves the row pending (upload_status=None) so a later drain re-picks it once
    the server recovers.

    _BATCH_SIZE=1 forces the drain to advance past each transiently-failed row.
    Without an id-cursor the still-None rows would be re-selected forever
    (in-drain infinite loop), so this also guards drain termination.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from sqlmodel import select

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")
    monkeypatch.setattr(uploader_mod, "_BATCH_SIZE", 1)

    async with async_session() as session:
        for i in range(2):
            session.add(
                FingerprintContribution(
                    chromaprint_blob=_make_valid_blob(),
                    tmdb_id=1399,
                    season=1,
                    episode=i + 1,
                    match_confidence=0.9,
                    match_source="engram_asr",
                    pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                )
            )
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    server_down = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=MagicMock(status_code=503)
    )

    # Drain #1: the server 503s on every request. The drain must terminate (no
    # infinite in-drain loop) and leave both rows recoverable, not failed.
    with patch("httpx.AsyncClient") as MockClient, patch("asyncio.sleep", AsyncMock()):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=server_down)
        MockClient.return_value = mock_client

        drained_during_outage = await ContributionUploader()._drain()

    assert drained_during_outage == 0
    async with async_session() as session:
        rows = (await session.execute(select(FingerprintContribution))).scalars().all()
    assert all(r.upload_status is None for r in rows), (
        "sustained 5xx must not permanently fail rows"
    )
    assert all(r.upload_attempts == _MAX_ATTEMPTS for r in rows)

    # Drain #2: the server has recovered. The previously-stuck rows must re-pick
    # and upload successfully — automatic recovery, no manual SQL reset needed.
    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=ok_resp)
        MockClient.return_value = mock_client

        drained_after_recovery = await ContributionUploader()._drain()

    assert drained_after_recovery == 2
    async with async_session() as session:
        rows = (await session.execute(select(FingerprintContribution))).scalars().all()
    assert all(r.upload_status == "success" for r in rows)
    # The transient-error message from drain #1 must not linger on a row that
    # later uploaded cleanly — the listing endpoint returns it verbatim, so a
    # stale message would surface a phantom error next to a "success" status.
    assert all(r.upload_error_msg is None for r in rows), (
        "upload_error_msg must be cleared when a previously-transient row succeeds"
    )


@pytest.mark.asyncio
async def test_force_delete_removes_retrying_contribution(setup_db, client):
    """`?force=true` deletes a transiently-stuck row that the plain delete 409s.

    Escape hatch for the new resilience behavior: transient failures keep a row
    pending (`upload_status=None`, `upload_attempts>0`) and retry forever, so the
    plain delete's in-flight guard would otherwise block it permanently. force
    lets the user retract a single row that won't upload.
    """
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            row = FingerprintContribution(
                chromaprint_blob=b"\x06",
                tmdb_id=66,
                season=1,
                episode=1,
                match_confidence=0.7,
                match_source="engram_asr",
                pseudonym="iiiiiiii-iiii-4iii-8iii-iiiiiiiiiiii",
                upload_attempts=5,  # attempted and retrying: status None, attempts>0
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            contrib_id = row.id

        # Plain delete is blocked — the row has been attempted and keeps retrying.
        blocked = await client.delete(f"/api/fingerprint/contributions/{contrib_id}")
        assert blocked.status_code == 409

        # force=true overrides the retry guard and removes the row.
        forced = await client.delete(f"/api/fingerprint/contributions/{contrib_id}?force=true")
        assert forced.status_code == 200
        assert forced.json()["status"] == "deleted"

        async with async_session() as session:
            assert await session.get(FingerprintContribution, contrib_id) is None
    finally:
        app.dependency_overrides.pop(require_localhost, None)


@pytest.mark.asyncio
async def test_force_delete_still_rejects_uploaded_contribution(setup_db, client):
    """`?force=true` must NOT delete an already-uploaded row — the data is on the
    server and a local delete can't recall it (use /fingerprint/forget instead)."""
    from app.api.routes import require_localhost
    from app.database import async_session
    from app.main import app

    app.dependency_overrides[require_localhost] = lambda: None
    try:
        async with async_session() as session:
            row = FingerprintContribution(
                chromaprint_blob=b"\x07",
                tmdb_id=55,
                season=2,
                episode=4,
                match_confidence=0.9,
                match_source="engram_asr",
                pseudonym="jjjjjjjj-jjjj-4jjj-8jjj-jjjjjjjjjjjj",
                upload_status="success",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            contrib_id = row.id

        resp = await client.delete(f"/api/fingerprint/contributions/{contrib_id}?force=true")
        assert resp.status_code == 400

        async with async_session() as session:
            assert await session.get(FingerprintContribution, contrib_id) is not None
    finally:
        app.dependency_overrides.pop(require_localhost, None)


# ---------------------------------------------------------------------------
# Disc-layout contributions (Phase C) — DiscContribution rows drained in _drain.
# ---------------------------------------------------------------------------

_DISC_TITLES = [
    {
        "title_index": 0,
        "duration_seconds": 1400,
        "size_bytes": 1_000_000,
        "assignment": "episode",
        "season": 1,
        "episode": 1,
        "match_confidence": 0.95,
        "match_source": "engram_asr",
    },
    {
        "title_index": 1,
        "duration_seconds": 1410,
        "size_bytes": 1_010_000,
        "assignment": "episode",
        "season": 1,
        "episode": 2,
        "match_confidence": 0.91,
        "match_source": "engram_asr",
    },
]


def _make_disc_row(
    *,
    pseudonym: str = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    disc_content_hash: bytes = b"\xaa\xbb\xcc\xdd",
    tmdb_id: int = 1399,
    content_type: str = "tv",
    season: int | None = 1,
    titles: list | None = None,
    upload_status: str | None = None,
) -> DiscContribution:
    return DiscContribution(
        disc_content_hash=disc_content_hash,
        tmdb_id=tmdb_id,
        content_type=content_type,
        season=season,
        titles_json=json.dumps(_DISC_TITLES if titles is None else titles),
        pseudonym=pseudonym,
        upload_status=upload_status,
    )


def _all_gates_pass_config(*, pseudonym: str = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"):
    from unittest.mock import MagicMock

    return MagicMock(
        fingerprint_server_url="https://fp.example.com",
        contribution_pseudonym=pseudonym,
        enable_fingerprint_contributions=True,
        fingerprint_disclosure_accepted=True,
    )


@pytest.mark.asyncio
async def test_disc_contribution_uploads_and_matches_contract(setup_db, tmp_path, monkeypatch):
    """A pending DiscContribution uploads to /v1/contribute-disc and the body
    matches the server contract: b64 hash decodes to raw bytes, titles parsed,
    content_type/season/tmdb_id correct, wire_format_version 1, client_version set."""
    import base64
    from unittest.mock import AsyncMock, MagicMock, patch

    import app as app_mod

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    disc_hash = b"\x01\x02\x03\x04\x05\x06"
    async with async_session() as session:
        session.add(_make_disc_row(disc_content_hash=disc_hash))
        await session.commit()

    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(return_value=_all_gates_pass_config())
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    captured: dict = {}
    captured_url: dict = {}

    async def fake_post(url, **kwargs):
        captured_url["url"] = url
        captured.update(kwargs.get("json", {}))
        return mock_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        MockClient.return_value = mock_client

        await ContributionUploader()._drain()

    assert captured_url["url"] == "https://fp.example.com/v1/contribute-disc"
    assert set(captured.keys()) == {
        "wire_format_version",
        "pseudonym",
        "disc_content_hash_b64",
        "tmdb_id",
        "content_type",
        "season",
        "titles",
        "client_version",
    }
    assert captured["wire_format_version"] == 1
    assert base64.b64decode(captured["disc_content_hash_b64"]) == disc_hash
    assert captured["tmdb_id"] == 1399
    assert captured["content_type"] == "tv"
    assert captured["season"] == 1
    assert captured["titles"] == _DISC_TITLES
    assert captured["client_version"] == app_mod.__version__

    async with async_session() as session:
        rows = (await session.execute(select(DiscContribution))).scalars().all()
    assert len(rows) == 1
    assert rows[0].upload_status == "success"
    assert rows[0].uploaded_at is not None


@pytest.mark.asyncio
async def test_disc_contribution_writes_disc_audit_entry(setup_db, tmp_path, monkeypatch):
    """A successful disc upload appends a JSONL audit line tagged kind='disc'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    log_path = tmp_path / "contrib.jsonl"
    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", log_path)

    async with async_session() as session:
        session.add(_make_disc_row())
        await session.commit()

    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(return_value=_all_gates_pass_config())
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()

    assert log_path.exists()
    line = json.loads(log_path.read_text().strip())
    assert line["kind"] == "disc"
    assert line["tmdb_id"] == 1399
    assert line["season"] == 1
    assert line["content_type"] == "tv"
    # Privacy: no raw pseudonym, only an 8-char prefix; no full titles payload.
    assert len(line["pseudonym_prefix"]) == 8
    assert "titles" not in line
    assert "pseudonym" not in line


@pytest.mark.asyncio
async def test_disc_contribution_marks_failed_on_4xx(setup_db, monkeypatch):
    """A 4xx (other than 429) permanently marks the disc row upload_status='failed'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    async with async_session() as session:
        session.add(_make_disc_row())
        await session.commit()

    exc = httpx.HTTPStatusError("400", request=MagicMock(), response=MagicMock(status_code=400))
    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(return_value=_all_gates_pass_config())
    )
    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=exc)
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()

    async with async_session() as session:
        row = (await session.execute(select(DiscContribution))).scalars().one()
    assert row.upload_status == "failed"
    assert "400" in (row.upload_error_msg or "")


@pytest.mark.asyncio
async def test_disc_contribution_stays_pending_on_5xx(setup_db, monkeypatch):
    """A 5xx is transient: the row stays recoverable (None) and attempts are bumped."""
    from unittest.mock import AsyncMock, MagicMock, patch

    async with async_session() as session:
        session.add(_make_disc_row())
        await session.commit()

    exc = httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock(status_code=503))
    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(return_value=_all_gates_pass_config())
    )
    with patch("httpx.AsyncClient") as MockClient, patch("asyncio.sleep", AsyncMock()):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=exc)
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()

    async with async_session() as session:
        row = (await session.execute(select(DiscContribution))).scalars().one()
    assert row.upload_status is None
    assert row.upload_attempts == _MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_disc_only_queue_triggers_disclosure_and_uploads_nothing(setup_db, monkeypatch):
    """With ONLY a disc row queued and disclosure not accepted, the drain fires the
    JIT disclosure event and uploads nothing (the gate considers disc rows too)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    async with async_session() as session:
        session.add(_make_disc_row())
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                contribution_pseudonym="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                enable_fingerprint_contributions=True,
                fingerprint_disclosure_accepted=False,
            )
        ),
    )

    with (
        patch("httpx.AsyncClient") as MockClient,
        patch(
            "app.services.event_broadcaster.EventBroadcaster.broadcast_fingerprint_disclosure_required",
            new_callable=AsyncMock,
        ) as mock_broadcast,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock()
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()
        mock_client.post.assert_not_called()

    mock_broadcast.assert_called_once()
    assert mock_broadcast.call_args[0][0] == 1  # one item queued
    assert mock_broadcast.call_args[0][1] == "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

    async with async_session() as session:
        row = (await session.execute(select(DiscContribution))).scalars().one()
    assert row.upload_status is None


@pytest.mark.asyncio
async def test_disc_contribution_skipped_when_opted_out(setup_db, monkeypatch):
    """enable_fingerprint_contributions=False → no disc upload at all."""
    from unittest.mock import AsyncMock, MagicMock, patch

    async with async_session() as session:
        session.add(_make_disc_row())
        await session.commit()

    monkeypatch.setattr(
        uploader_mod,
        "get_config",
        AsyncMock(
            return_value=MagicMock(
                fingerprint_server_url="https://fp.example.com",
                enable_fingerprint_contributions=False,
                fingerprint_disclosure_accepted=True,
            )
        ),
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock()
        MockClient.return_value = mock_client
        await ContributionUploader()._drain()
        mock_client.post.assert_not_called()

    async with async_session() as session:
        row = (await session.execute(select(DiscContribution))).scalars().one()
    assert row.upload_status is None


@pytest.mark.asyncio
async def test_mixed_drain_uploads_episode_and_disc_and_counts_both(
    setup_db, tmp_path, monkeypatch
):
    """A mixed drain uploads BOTH episode and disc rows; the returned count reflects both."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(uploader_mod, "CONTRIBUTION_LOG_PATH", tmp_path / "contrib.jsonl")

    async with async_session() as session:
        session.add(
            FingerprintContribution(
                chromaprint_blob=_make_valid_blob(),
                tmdb_id=1399,
                season=1,
                episode=7,
                match_confidence=0.95,
                match_source="engram_asr",
                pseudonym="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            )
        )
        session.add(_make_disc_row())
        await session.commit()

    monkeypatch.setattr(
        uploader_mod, "get_config", AsyncMock(return_value=_all_gates_pass_config())
    )

    posted_urls: list[str] = []
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    async def fake_post(url, **kwargs):
        posted_urls.append(url)
        return mock_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=fake_post)
        MockClient.return_value = mock_client

        drained = await ContributionUploader()._drain()

    assert drained == 2
    assert any(u.endswith("/v1/contribute") for u in posted_urls)
    assert any(u.endswith("/v1/contribute-disc") for u in posted_urls)

    async with async_session() as session:
        ep = (await session.execute(select(FingerprintContribution))).scalars().one()
        disc = (await session.execute(select(DiscContribution))).scalars().one()
    assert ep.upload_status == "success"
    assert disc.upload_status == "success"
