"""Unit tests for WebSocket connection manager.

Tests WebSocket lifecycle, message broadcasting, and error handling.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from app.api.websocket import ConnectionManager
from app.models.disc_job import JobState, TitleState


@pytest.fixture
def connection_manager():
    """Create a fresh ConnectionManager instance."""
    return ConnectionManager()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()  # ConnectionManager uses send_text, not send_text
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
class TestConnectionLifecycle:
    """Test WebSocket connection lifecycle management."""

    async def test_connect_adds_client(self, connection_manager, mock_websocket):
        """Test that connecting adds client to active connections."""
        await connection_manager.connect(mock_websocket)

        assert len(connection_manager.active_connections) == 1
        assert mock_websocket in connection_manager.active_connections
        mock_websocket.accept.assert_called_once()

    async def test_disconnect_removes_client(self, connection_manager, mock_websocket):
        """Test that disconnecting removes client from active connections."""
        await connection_manager.connect(mock_websocket)
        await connection_manager.disconnect(mock_websocket)

        assert len(connection_manager.active_connections) == 0
        assert mock_websocket not in connection_manager.active_connections

    async def test_multiple_connections(self, connection_manager):
        """Test handling multiple simultaneous connections."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)

        await connection_manager.connect(ws1)
        await connection_manager.connect(ws2)
        await connection_manager.connect(ws3)

        assert len(connection_manager.active_connections) == 3

        await connection_manager.disconnect(ws2)
        assert len(connection_manager.active_connections) == 2
        assert ws1 in connection_manager.active_connections
        assert ws3 in connection_manager.active_connections

    async def test_disconnect_idempotent(self, connection_manager, mock_websocket):
        """Test that disconnecting a non-connected client is safe."""
        # Should not raise an error
        await connection_manager.disconnect(mock_websocket)
        assert len(connection_manager.active_connections) == 0


@pytest.mark.asyncio
class TestMessageBroadcasting:
    """Test message broadcasting functionality."""

    async def test_broadcast_job_update(self, connection_manager, mock_websocket):
        """Test broadcasting job state updates."""
        await connection_manager.connect(mock_websocket)

        await connection_manager.broadcast_job_update(
            job_id=1, state=JobState.RIPPING.value, progress=50.0
        )

        mock_websocket.send_text.assert_called_once()
        json_text = mock_websocket.send_text.call_args[0][0]
        call_args = json.loads(json_text)

        assert call_args["type"] == "job_update"
        assert call_args["job_id"] == 1
        assert call_args["state"] == "ripping"
        assert call_args["progress_percent"] == 50.0

    async def test_broadcast_title_update(self, connection_manager, mock_websocket):
        """Test broadcasting title state updates."""
        await connection_manager.connect(mock_websocket)

        await connection_manager.broadcast_title_update(
            job_id=1,
            title_id=10,
            state=TitleState.MATCHING.value,
        )

        mock_websocket.send_text.assert_called_once()
        json_text = mock_websocket.send_text.call_args[0][0]
        call_args = json.loads(json_text)

        assert call_args["type"] == "title_update"
        assert call_args["job_id"] == 1
        assert call_args["title_id"] == 10
        assert call_args["state"] == "matching"

    async def test_broadcast_drive_event(self, connection_manager, mock_websocket):
        """Test broadcasting drive insertion/removal events."""
        await connection_manager.connect(mock_websocket)

        await connection_manager.broadcast_drive_event(
            drive_id="D:", event="inserted", volume_label="TEST_DISC"
        )

        mock_websocket.send_text.assert_called_once()
        json_text = mock_websocket.send_text.call_args[0][0]
        call_args = json.loads(json_text)

        assert call_args["type"] == "drive_event"
        assert call_args["drive_id"] == "D:"
        assert call_args["event"] == "inserted"
        assert call_args["volume_label"] == "TEST_DISC"

    async def test_broadcast_to_multiple_clients(self, connection_manager):
        """Test that broadcasts reach all connected clients."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)

        await connection_manager.connect(ws1)
        await connection_manager.connect(ws2)
        await connection_manager.connect(ws3)

        await connection_manager.broadcast_job_update(job_id=1, state="ripping")

        # All clients should receive the message
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        ws3.send_text.assert_called_once()

    async def test_broadcast_with_no_clients(self, connection_manager):
        """Test that broadcasting with no clients doesn't error."""
        # Should not raise any errors
        await connection_manager.broadcast_job_update(job_id=1, state="ripping")


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in WebSocket operations."""

    async def test_send_failure_disconnects_client(self, connection_manager):
        """Test that send failures result in client disconnection."""
        ws = AsyncMock(spec=WebSocket)
        ws.send_text.side_effect = RuntimeError("Connection closed")

        await connection_manager.connect(ws)
        assert len(connection_manager.active_connections) == 1

        # Broadcasting should handle the error and disconnect the client
        await connection_manager.broadcast_job_update(job_id=1, state="ripping")

        # Client should be removed after send failure
        assert len(connection_manager.active_connections) == 0

    async def test_partial_broadcast_failure(self, connection_manager):
        """Test that one client failure doesn't affect others."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)

        # ws2 will fail to send
        ws2.send_text.side_effect = RuntimeError("Connection closed")

        await connection_manager.connect(ws1)
        await connection_manager.connect(ws2)
        await connection_manager.connect(ws3)

        await connection_manager.broadcast_job_update(job_id=1, state="ripping")

        # ws1 and ws3 should receive messages
        ws1.send_text.assert_called_once()
        ws3.send_text.assert_called_once()

        # ws2 should be disconnected
        assert ws1 in connection_manager.active_connections
        assert ws2 not in connection_manager.active_connections
        assert ws3 in connection_manager.active_connections

    async def test_malformed_message_handling(self, connection_manager, mock_websocket):
        """Test handling of malformed broadcast messages."""
        await connection_manager.connect(mock_websocket)

        # Try to broadcast with missing required fields
        # The implementation should handle this gracefully
        try:
            await connection_manager.broadcast_job_update(job_id=None, state=None)
        except Exception:
            pytest.fail("Broadcast should handle invalid data gracefully")


@pytest.mark.asyncio
class TestConcurrency:
    """Test concurrent WebSocket operations."""

    async def test_concurrent_connections(self, connection_manager):
        """Test handling multiple connections concurrently."""
        connections = [AsyncMock(spec=WebSocket) for _ in range(10)]

        # Connect all clients concurrently
        await asyncio.gather(*[connection_manager.connect(ws) for ws in connections])

        assert len(connection_manager.active_connections) == 10

    async def test_concurrent_broadcasts(self, connection_manager, mock_websocket):
        """Test multiple concurrent broadcasts."""
        await connection_manager.connect(mock_websocket)

        # Send multiple broadcasts concurrently
        broadcasts = [
            connection_manager.broadcast_job_update(job_id=i, state="ripping") for i in range(10)
        ]

        await asyncio.gather(*broadcasts)

        # Should have sent 10 messages
        assert mock_websocket.send_text.call_count == 10

    async def test_connect_disconnect_race(self, connection_manager):
        """Test race conditions between connect and disconnect."""
        ws = AsyncMock(spec=WebSocket)

        # Rapidly connect and disconnect
        for _ in range(5):
            await connection_manager.connect(ws)
            await connection_manager.disconnect(ws)

        # Should end in disconnected state
        assert len(connection_manager.active_connections) == 0


@pytest.mark.asyncio
class TestSpecializedBroadcasts:
    """Test specialized broadcast methods."""

    async def test_broadcast_titles_discovered(self, connection_manager, mock_websocket):
        """Test broadcasting title discovery events."""
        await connection_manager.connect(mock_websocket)

        titles = [
            {"id": 1, "title_index": 0, "duration_seconds": 2400},
            {"id": 2, "title_index": 1, "duration_seconds": 2500},
        ]

        await connection_manager.broadcast_titles_discovered(
            job_id=1,
            titles=titles,
            content_type="tv",
            detected_title="Test Show",
            detected_season=1,
        )

        mock_websocket.send_text.assert_called_once()
        json_text = mock_websocket.send_text.call_args[0][0]
        call_args = json.loads(json_text)

        assert call_args["type"] == "titles_discovered"
        assert call_args["job_id"] == 1
        assert len(call_args["titles"]) == 2

    async def test_broadcast_subtitle_event(self, connection_manager, mock_websocket):
        """Test broadcasting subtitle download events."""
        await connection_manager.connect(mock_websocket)

        await connection_manager.broadcast_subtitle_event(
            job_id=1,
            status="downloading",
            downloaded=5,
            total=10,
            failed_count=1,
        )

        mock_websocket.send_text.assert_called_once()
        json_text = mock_websocket.send_text.call_args[0][0]
        call_args = json.loads(json_text)

        assert call_args["type"] == "subtitle_event"
        assert call_args["job_id"] == 1
        assert call_args["status"] == "downloading"
        assert call_args["downloaded"] == 5
        assert call_args["total"] == 10
