"""Phase 2 end-to-end lifecycle tests: seed → drain → upload → forget.

Covers I1.2: Full lifecycle integration test for the fingerprint contribution flow.
Three scenarios:
  1. Debug drain endpoint wires through to _drain successfully.
  2. Full lifecycle: seed → upload (drain) → forget → pseudonym rotated.
  3. Disclosure gate: drain fires WS event and uploads nothing when consent absent.

Note: ASGITransport does not trigger lifespan events, so app.state.contribution_uploader
is not set by the lifespan handler. Tests that call the drain endpoint attach a
ContributionUploader instance directly to app.state before the call and remove it after.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import require_debug, require_localhost
from app.database import async_session, init_db
from app.main import app
from app.models.fingerprint import FingerprintContribution
from app.services.config_service import update_config as update_db_config
from app.services.contribution_uploader import ContributionUploader


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


# ---------------------------------------------------------------------------
# Test 1 — debug drain endpoint runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_drain_endpoint_runs(client):
    """POST /api/debug/uploader/drain calls _drain; seeded row → success."""
    app.dependency_overrides[require_localhost] = lambda: None
    app.dependency_overrides[require_debug] = lambda: None
    # ASGITransport does not trigger lifespan; attach uploader directly.
    app.state.contribution_uploader = ContributionUploader()
    try:
        await update_db_config(
            contribution_pseudonym="11111111-1111-4111-8111-111111111111",
            fingerprint_server_url="https://fp.example.com",
            fingerprint_disclosure_accepted=True,
        )

        # Seed one pending contribution via the debug endpoint
        seed_resp = await client.post("/api/debug/fingerprint/seed")
        assert seed_resp.status_code == 200, seed_resp.text
        seed_data = seed_resp.json()
        assert seed_data["ok"] is True
        contrib_id = seed_data["contribution_id"]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client

            drain_resp = await client.post("/api/debug/uploader/drain")
            assert drain_resp.status_code == 200, drain_resp.text
            assert drain_resp.json() == {"ok": True}

        # Seeded row should now be marked as uploaded
        async with async_session() as session:
            row = await session.get(FingerprintContribution, contrib_id)
        assert row is not None
        assert row.upload_status == "success"
    finally:
        app.dependency_overrides.pop(require_localhost, None)
        app.dependency_overrides.pop(require_debug, None)
        del app.state.contribution_uploader


# ---------------------------------------------------------------------------
# Test 2 — full lifecycle: seed → drain/upload → forget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_lifecycle_seed_drain_forget(client):
    """End-to-end: seed a contribution, upload it, forget (rotate pseudonym)."""
    old_pseudonym = "22222222-2222-4222-8222-222222222222"
    app.dependency_overrides[require_localhost] = lambda: None
    app.dependency_overrides[require_debug] = lambda: None
    # ASGITransport does not trigger lifespan; attach uploader directly.
    app.state.contribution_uploader = ContributionUploader()
    try:
        await update_db_config(
            contribution_pseudonym=old_pseudonym,
            fingerprint_server_url="https://fp.example.com",
            fingerprint_disclosure_accepted=True,
        )

        # Seed one pending contribution
        seed_resp = await client.post("/api/debug/fingerprint/seed")
        assert seed_resp.status_code == 200, seed_resp.text
        contrib_id = seed_resp.json()["contribution_id"]

        # Phase A: upload via drain
        mock_resp_upload = MagicMock()
        mock_resp_upload.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp_upload)
            MockClient.return_value = mock_client

            drain_resp = await client.post("/api/debug/uploader/drain")
            assert drain_resp.status_code == 200, drain_resp.text

        async with async_session() as session:
            row = await session.get(FingerprintContribution, contrib_id)
        assert row.upload_status == "success"

        # Phase B: forget — rotates pseudonym and resets disclosure
        mock_resp_forget = MagicMock()
        mock_resp_forget.raise_for_status = MagicMock()
        mock_resp_forget.json = MagicMock(
            return_value={"rows_deleted": 1, "canonical_unaffected": True}
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp_forget)
            MockClient.return_value = mock_client

            forget_resp = await client.post("/api/fingerprint/forget")
            assert forget_resp.status_code == 200, forget_resp.text
            forget_data = forget_resp.json()

        assert forget_data["server_rows_deleted"] == 1
        new_pseudonym = forget_data["new_pseudonym"]
        assert new_pseudonym != old_pseudonym

        # Config must reflect new pseudonym and disclosure reset
        config_resp = await client.get("/api/config")
        assert config_resp.status_code == 200
        config_data = config_resp.json()
        assert config_data["fingerprint_disclosure_accepted"] is False
        assert config_data["contribution_pseudonym"] == new_pseudonym
    finally:
        app.dependency_overrides.pop(require_localhost, None)
        app.dependency_overrides.pop(require_debug, None)
        del app.state.contribution_uploader


# ---------------------------------------------------------------------------
# Test 3 — disclosure WS event fires when consent absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disclosure_event_fires_when_not_accepted(client):
    """Drain with no disclosure accepted: WS event fires, nothing uploaded."""
    app.dependency_overrides[require_localhost] = lambda: None
    app.dependency_overrides[require_debug] = lambda: None
    # ASGITransport does not trigger lifespan; attach uploader directly.
    app.state.contribution_uploader = ContributionUploader()
    try:
        await update_db_config(
            contribution_pseudonym="33333333-3333-4333-8333-333333333333",
            fingerprint_server_url="https://fp.example.com",
            fingerprint_disclosure_accepted=False,
        )

        # Seed one pending contribution
        seed_resp = await client.post("/api/debug/fingerprint/seed")
        assert seed_resp.status_code == 200, seed_resp.text
        contrib_id = seed_resp.json()["contribution_id"]

        with (
            patch(
                "app.services.event_broadcaster.EventBroadcaster"
                ".broadcast_fingerprint_disclosure_required",
                new_callable=AsyncMock,
            ) as mock_broadcast,
            patch("httpx.AsyncClient") as MockClient,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()
            MockClient.return_value = mock_client

            drain_resp = await client.post("/api/debug/uploader/drain")
            assert drain_resp.status_code == 200, drain_resp.text

            # Broadcast was called, httpx POST was not
            mock_broadcast.assert_called_once()
            mock_client.post.assert_not_called()

        # Seeded row still pending
        async with async_session() as session:
            row = await session.get(FingerprintContribution, contrib_id)
        assert row.upload_status is None
    finally:
        app.dependency_overrides.pop(require_localhost, None)
        app.dependency_overrides.pop(require_debug, None)
        del app.state.contribution_uploader
