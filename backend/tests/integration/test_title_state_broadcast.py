"""Integration tests for title_update broadcasts during simulated ripping.

Verifies that the backend sends title_update messages with state="ripping"
when simulating a multi-track disc. This proves the bug is frontend-only —
the backend correctly broadcasts per-title state transitions.
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
class TestTitleStateBroadcast:
    """Verify backend broadcasts title_update with state=ripping during simulation."""

    async def test_title_update_ripping_broadcast(self, client, test_config):
        """At least one title_update with state='ripping' should be broadcast
        during a multi-track simulated rip."""
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
                    "volume_label": "TITLE_STATE_TEST",
                    "content_type": "movie",
                    "detected_title": "Title State Test",
                    "simulate_ripping": True,
                    "rip_speed_multiplier": 50,
                    "titles": [
                        {"duration_seconds": 170, "file_size_bytes": 100000000, "chapter_count": 1},
                        {
                            "duration_seconds": 1101,
                            "file_size_bytes": 800000000,
                            "chapter_count": 5,
                        },
                        {
                            "duration_seconds": 1269,
                            "file_size_bytes": 900000000,
                            "chapter_count": 6,
                        },
                    ],
                },
            )
            assert response.status_code == 200

            # Wait for ripping simulation to produce title_update messages
            await asyncio.sleep(8)

            # Filter for title_update messages
            title_updates = [
                m for m in messages if isinstance(m, dict) and m.get("type") == "title_update"
            ]

            # At least one title_update should have state="ripping"
            ripping_updates = [m for m in title_updates if m.get("state") == "ripping"]
            assert len(ripping_updates) > 0, (
                f"Expected at least one title_update with state='ripping'. "
                f"Got {len(title_updates)} title_updates: "
                f"{[m.get('state') for m in title_updates]}"
            )

        finally:
            manager.broadcast = original_broadcast

    async def test_no_simultaneous_ripping_titles(self, client, test_config):
        """No two titles should be set to ripping state simultaneously
        in sequential title_update messages."""
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
                    "volume_label": "SIMULTANEOUS_TEST",
                    "content_type": "movie",
                    "detected_title": "Simultaneous Test",
                    "simulate_ripping": True,
                    "rip_speed_multiplier": 50,
                    "titles": [
                        {"duration_seconds": 300, "file_size_bytes": 200000000, "chapter_count": 2},
                        {"duration_seconds": 600, "file_size_bytes": 400000000, "chapter_count": 3},
                        {"duration_seconds": 400, "file_size_bytes": 300000000, "chapter_count": 2},
                    ],
                },
            )
            assert response.status_code == 200

            await asyncio.sleep(8)

            # Build a running snapshot of title states from title_update messages
            title_states: dict[int, str] = {}
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "title_update" and "title_id" in msg and "state" in msg:
                    title_states[msg["title_id"]] = msg["state"]

                    # Count how many titles are currently in "ripping" state
                    ripping_count = sum(1 for s in title_states.values() if s == "ripping")
                    assert ripping_count <= 1, (
                        f"Found {ripping_count} titles simultaneously in 'ripping' state: "
                        f"{title_states}"
                    )

        finally:
            manager.broadcast = original_broadcast
