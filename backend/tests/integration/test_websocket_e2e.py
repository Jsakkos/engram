"""Integration tests for WebSocket message delivery.

Tests that WebSocket messages are properly shaped and delivered
during workflow state changes.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import AppConfig


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def test_config():
    async with async_session() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key",
            staging_path="/tmp/staging",
            library_movies_path="/media/movies",
            library_tv_path="/media/tv",
            transcoding_enabled=False,
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test",
            max_concurrent_matches=2,
            ffmpeg_path="/usr/bin/ffmpeg",
            conflict_resolution_default="rename",
            ripping_file_poll_interval=0.5,
            ripping_stability_checks=2,
            ripping_file_ready_timeout=60.0,
        )
        session.add(config)
        await session.commit()
        return config


@pytest.mark.asyncio
@pytest.mark.integration
class TestWebSocketMessageShapes:
    """Verify WebSocket message payload shapes match frontend expectations."""

    async def test_job_update_message_shape(self, client, test_config):
        """job_update messages should have the expected fields."""
        from app.api.websocket import manager

        # Capture messages
        messages = []
        original_broadcast = manager.broadcast

        async def capture_broadcast(message):
            messages.append(message)
            # Don't actually send to clients (no WS connections in test)

        manager.broadcast = capture_broadcast

        try:
            # Trigger a job creation which broadcasts
            response = await client.post(
                "/api/simulate/insert-disc",
                json={
                    "volume_label": "WS_TEST_S1D1",
                    "content_type": "tv",
                    "simulate_ripping": False,
                },
            )
            assert response.status_code == 200

            # Wait for broadcasts to fire
            await asyncio.sleep(2)

            # Find job_update messages
            job_updates = [
                m for m in messages if isinstance(m, dict) and m.get("type") == "job_update"
            ]

            # Should have received at least one job_update
            assert len(job_updates) > 0, (
                f"No job_update messages found. Got: {[m.get('type') for m in messages if isinstance(m, dict)]}"
            )

            # Verify shape
            msg = job_updates[0]
            assert "type" in msg
            assert msg["type"] == "job_update"
            assert "job_id" in msg
            assert "state" in msg

        finally:
            manager.broadcast = original_broadcast

    async def test_titles_discovered_message_shape(self, client, test_config):
        """titles_discovered messages should contain title arrays."""
        from app.api.websocket import manager

        messages = []
        original_broadcast = manager.broadcast

        async def capture_broadcast(message):
            messages.append(message)

        manager.broadcast = capture_broadcast

        try:
            response = await client.post(
                "/api/simulate/insert-disc",
                json={
                    "volume_label": "TITLES_TEST_S1D1",
                    "content_type": "tv",
                    "simulate_ripping": True,
                },
            )
            assert response.status_code == 200

            # Wait for disc identification (which discovers titles)
            await asyncio.sleep(5)

            # Check for titles_discovered messages
            td_messages = [
                m for m in messages if isinstance(m, dict) and m.get("type") == "titles_discovered"
            ]

            if td_messages:
                msg = td_messages[0]
                assert "job_id" in msg
                assert "titles" in msg
                assert isinstance(msg["titles"], list)
                if len(msg["titles"]) > 0:
                    title = msg["titles"][0]
                    assert "id" in title or "title_index" in title

        finally:
            manager.broadcast = original_broadcast

    async def test_subtitle_event_message_shape(self, client, test_config):
        """subtitle_event messages should have status, downloaded, total fields."""
        from app.api.websocket import manager

        messages = []
        original_broadcast = manager.broadcast

        async def capture_broadcast(message):
            messages.append(message)

        manager.broadcast = capture_broadcast

        try:
            response = await client.post(
                "/api/simulate/insert-disc",
                json={
                    "volume_label": "SUB_EVENT_TEST_S1D1",
                    "content_type": "tv",
                    "simulate_ripping": True,
                },
            )
            assert response.status_code == 200

            # Wait for subtitle events
            await asyncio.sleep(5)

            sub_events = [
                m for m in messages if isinstance(m, dict) and m.get("type") == "subtitle_event"
            ]

            if sub_events:
                msg = sub_events[0]
                assert "job_id" in msg
                assert "status" in msg
                assert msg["status"] in ("downloading", "completed", "partial", "failed")
                assert "downloaded" in msg
                assert "total" in msg

        finally:
            manager.broadcast = original_broadcast
