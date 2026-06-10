"""WebSocket connection manager for real-time updates."""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds between pings
HEARTBEAT_TIMEOUT = 10  # seconds to wait for pong


class ConnectionManager:
    """Manages WebSocket connections for broadcasting updates."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._heartbeat_tasks: dict[WebSocket, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
            task = asyncio.create_task(self._heartbeat_loop(websocket))
            self._heartbeat_tasks[websocket] = task
        logger.info(f"Client connected. Total connections: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            task = self._heartbeat_tasks.pop(websocket, None)
            if task:
                task.cancel()
        logger.info(f"Client disconnected. Total connections: {len(self.active_connections)}")

    async def _heartbeat_loop(self, websocket: WebSocket) -> None:
        """Send periodic pings to detect stale connections.

        On failure, closes the socket directly instead of calling disconnect()
        to avoid deadlocking on self._lock (which broadcast() may hold).
        The main receive loop in websocket_endpoint will catch the disconnect
        and call disconnect() to clean up.
        """
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                try:
                    await asyncio.wait_for(
                        websocket.send_json({"type": "ping"}),
                        timeout=HEARTBEAT_TIMEOUT,
                    )
                except Exception:
                    logger.warning("Heartbeat failed, closing stale connection")
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    return
        except asyncio.CancelledError:
            return

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return

        json_message = json.dumps(message)
        disconnected = []

        async with self._lock:
            for connection in self.active_connections:
                try:
                    await connection.send_text(json_message)
                except Exception as e:
                    logger.warning(f"Failed to send message: {e}")
                    disconnected.append(connection)

            # Clean up disconnected clients
            for conn in disconnected:
                self.active_connections.remove(conn)

    async def broadcast_job_update(
        self,
        job_id: int,
        state: str,
        progress: float | None = None,
        speed: str | None = None,
        eta: int | None = None,
        current_title: int | None = None,
        total_titles: int | None = None,
        error: str | None = None,
        content_type: str | None = None,
        detected_title: str | None = None,
        detected_season: int | None = None,
        review_reason: str | None = None,
        conflict_status: str | None = None,
        tmdb_degraded_reason: str | None = None,
    ) -> None:
        """Broadcast a job status update.

        Only includes optional fields when they are not None, so the frontend
        merge ({...job, ...message}) won't overwrite existing values with
        defaults.

        Known limitation: tmdb_id/tmdb_name/tmdb_year are intentionally not sent
        here. The client relies on the REST job payload (GET /api/jobs) for those.
        Re-identification normally moves the job out of review_needed, so the
        identity-review UI re-evaluates from the state field alone; the only stale
        window is the rare case where re-identify resolves to *another* same-name
        collision (re-enters review_needed with a new tmdb_id) — the modal's
        "Currently:" line then shows the previous show until the next REST poll.
        """
        data: dict = {
            "type": "job_update",
            "job_id": job_id,
        }
        # state=None means "unchanged" (progress-only updates, e.g. during the
        # organize file-move). Omit it so the frontend merge ({...job, ...message})
        # doesn't blank the current state to null — which would drop the card out
        # of its state-gated render (e.g. the ORGANIZING view) mid-move.
        if state is not None:
            data["state"] = state
        if progress is not None:
            data["progress_percent"] = progress
        if speed is not None:
            data["current_speed"] = speed
        if eta is not None:
            data["eta_seconds"] = eta
        if current_title is not None:
            data["current_title"] = current_title
        if total_titles is not None:
            data["total_titles"] = total_titles
        if error is not None:
            data["error_message"] = error
        if content_type is not None:
            data["content_type"] = content_type
        if detected_title is not None:
            data["detected_title"] = detected_title
        if detected_season is not None:
            data["detected_season"] = detected_season
        if review_reason is not None:
            data["review_reason"] = review_reason
        if conflict_status is not None:
            data["conflict_status"] = conflict_status
        # "" is forwarded deliberately: it CLEARS the field on the frontend merge
        # (e.g. after a re-identify with a now-working key); None means "unchanged".
        if tmdb_degraded_reason is not None:
            data["tmdb_degraded_reason"] = tmdb_degraded_reason
        await self.broadcast(data)

    async def broadcast_drive_event(
        self,
        drive_id: str,
        event: str,
        volume_label: str = "",
    ) -> None:
        """Broadcast a drive insertion/removal event."""
        await self.broadcast(
            {
                "type": "drive_event",
                "drive_id": drive_id,
                "event": event,  # "inserted" or "removed"
                "volume_label": volume_label,
            }
        )

    async def broadcast_title_update(
        self,
        job_id: int,
        title_id: int,
        state: str,
        matched_episode: str | None = None,
        match_confidence: float = 0.0,
        match_stage: str | None = None,
        match_progress: float = 0.0,
        duration_seconds: int | None = None,
        file_size_bytes: int | None = None,
        expected_size_bytes: int | None = None,
        actual_size_bytes: int | None = None,
        matches_found: int | None = None,
        matches_rejected: int | None = None,
        match_details: str | None = None,
        organized_from: str | None = None,
        organized_to: str | None = None,
        output_filename: str | None = None,
        is_extra: bool | None = None,
        match_source: str | None = None,
        error: str | None = None,
    ) -> None:
        """Broadcast a title status update.

        Only includes optional fields when they are not None/zero-default,
        so the frontend merge ({...title, ...message}) won't overwrite
        existing values with null.
        """
        data: dict = {
            "type": "title_update",
            "job_id": job_id,
            "title_id": title_id,
            "state": state,
        }
        # Only include optional fields when explicitly provided
        if matched_episode is not None:
            data["matched_episode"] = matched_episode
        if match_confidence:
            data["match_confidence"] = match_confidence
        if match_stage is not None:
            data["match_stage"] = match_stage
        if match_progress:
            data["match_progress"] = match_progress
        if duration_seconds is not None:
            data["duration_seconds"] = duration_seconds
        if file_size_bytes is not None:
            data["file_size_bytes"] = file_size_bytes
        if expected_size_bytes is not None:
            data["expected_size_bytes"] = expected_size_bytes
        if actual_size_bytes is not None:
            data["actual_size_bytes"] = actual_size_bytes
        if matches_found is not None:
            data["matches_found"] = matches_found
        if matches_rejected is not None:
            data["matches_rejected"] = matches_rejected
        if match_details is not None:
            data["match_details"] = match_details
        if organized_from is not None:
            data["organized_from"] = organized_from
        if organized_to is not None:
            data["organized_to"] = organized_to
        if output_filename is not None:
            data["output_filename"] = output_filename
        if is_extra is not None:
            data["is_extra"] = is_extra
        if match_source is not None:
            data["match_source"] = match_source
        if error is not None:
            data["error"] = error
        await self.broadcast(data)

    async def broadcast_subtitle_event(
        self,
        job_id: int,
        status: str,
        downloaded: int = 0,
        total: int = 0,
        failed_count: int = 0,
    ) -> None:
        """Broadcast subtitle download progress."""
        await self.broadcast(
            {
                "type": "subtitle_event",
                "job_id": job_id,
                "status": status,
                "downloaded": downloaded,
                "total": total,
                "failed_count": failed_count,
            }
        )

    async def broadcast_titles_discovered(
        self,
        job_id: int,
        titles: list[dict],
        content_type: str = "unknown",
        detected_title: str | None = None,
        detected_season: int | None = None,
    ) -> None:
        """Broadcast discovered titles after scanning."""
        await self.broadcast(
            {
                "type": "titles_discovered",
                "job_id": job_id,
                "titles": titles,
                "content_type": content_type,
                "detected_title": detected_title,
                "detected_season": detected_season,
            }
        )


# Singleton instance
manager = ConnectionManager()
