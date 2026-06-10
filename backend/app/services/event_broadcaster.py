"""Domain-specific event broadcasting layer.

Provides semantic event methods that wrap WebSocket broadcasting,
improving code clarity and reducing coupling to WebSocket implementation.
"""

from app.api.websocket import ConnectionManager
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState


class EventBroadcaster:
    """Domain-specific WebSocket event broadcasting."""

    def __init__(self, ws_manager: ConnectionManager):
        self._ws = ws_manager

    # --- Drive Events ---

    async def broadcast_drive_inserted(self, drive_id: str, volume_label: str):
        """Broadcast disc insertion event."""
        await self._ws.broadcast_drive_event(drive_id, "inserted", volume_label)

    async def broadcast_drive_removed(self, drive_id: str, volume_label: str):
        """Broadcast disc removal event."""
        await self._ws.broadcast_drive_event(drive_id, "removed", volume_label)

    async def broadcast_parked_discs(self, discs: list[dict]) -> None:
        """Broadcast the full set of discs parked behind the first-run setup gate.

        Sent whenever the parked set changes (disc parked, ejected, or released
        by setup completion). Full-list replace — the dashboard renders its
        "finish setup to start ripping" banner from exactly this list, with no
        merge logic. Flat envelope (matches the frontend's `message.type` switch).
        """
        await self._ws.broadcast({"type": "parked_discs", "discs": discs})

    # --- Job Lifecycle Events ---

    async def broadcast_job_created(self, job: DiscJob):
        """Broadcast new job creation."""
        await self._ws.broadcast_job_update(job.id, job.state.value)

    async def broadcast_job_state_changed(self, job_id: int, new_state: JobState):
        """Broadcast job state transition."""
        await self._ws.broadcast_job_update(job_id, new_state.value)

    async def broadcast_job_progress(
        self,
        job_id: int,
        progress_percent: int,
        current_speed: str | None = None,
        eta_seconds: int | None = None,
    ):
        """Broadcast job progress update."""
        await self._ws.broadcast_job_update(
            job_id,
            None,  # state unchanged
            progress=progress_percent,
            speed=current_speed,
            eta=eta_seconds,
        )

    async def broadcast_job_failed(self, job_id: int, error_message: str):
        """Broadcast job failure."""
        await self._ws.broadcast_job_update(job_id, JobState.FAILED.value, error=error_message)

    async def broadcast_job_completed(self, job_id: int):
        """Broadcast job completion."""
        await self._ws.broadcast_job_update(job_id, JobState.COMPLETED.value)

    # --- Title Discovery Events ---

    async def broadcast_titles_discovered(
        self,
        job_id: int,
        titles: list[DiscTitle],
        content_type: ContentType | None = None,
        detected_title: str | None = None,
        detected_season: int | None = None,
    ):
        """Broadcast title discovery after disc scan."""
        await self._ws.broadcast_titles_discovered(
            job_id,
            titles,
            content_type=content_type.value if content_type else None,
            detected_title=detected_title,
            detected_season=detected_season,
        )

    # --- Title State Events ---

    async def broadcast_title_ripping_started(self, title: DiscTitle):
        """Broadcast title ripping started."""
        await self._ws.broadcast_title_update(
            title.job_id, title.id, state=TitleState.RIPPING.value
        )

    async def broadcast_title_ripping_progress(self, title: DiscTitle, progress_percent: int):
        """Broadcast title ripping progress."""
        await self._ws.broadcast_title_update(
            title.job_id, title.id, state=TitleState.RIPPING.value, match_progress=progress_percent
        )

    async def broadcast_title_queued(self, title: DiscTitle):
        """Broadcast that a title is enqueued for matching (waiting for a slot)."""
        await self._ws.broadcast_title_update(
            title.job_id,
            title.id,
            state=TitleState.QUEUED.value,
            duration_seconds=title.duration_seconds,
            file_size_bytes=title.file_size_bytes,
        )

    async def broadcast_title_matching_started(self, title: DiscTitle):
        """Broadcast title matching started."""
        await self._ws.broadcast_title_update(
            title.job_id, title.id, state=TitleState.MATCHING.value
        )

    async def broadcast_title_matched(
        self, title: DiscTitle, matched_episode: str, confidence: float
    ):
        """Broadcast successful title match."""
        await self._ws.broadcast_title_update(
            title.job_id,
            title.id,
            state=TitleState.MATCHED.value,
            matched_episode=matched_episode,
            match_confidence=confidence,
        )

    async def broadcast_title_state_changed(self, title: DiscTitle, new_state: TitleState):
        """Broadcast generic title state change."""
        await self._ws.broadcast_title_update(title.job_id, title.id, state=new_state.value)

    async def broadcast_title_completed(self, title: DiscTitle):
        """Broadcast title processing completed."""
        await self._ws.broadcast_title_update(
            title.job_id, title.id, state=TitleState.COMPLETED.value
        )

    async def broadcast_title_failed(self, title: DiscTitle, error: str):
        """Broadcast title processing failed."""
        await self._ws.broadcast_title_update(
            title.job_id,
            title.id,
            state=TitleState.FAILED.value,
            error=error,
        )

    # --- Subtitle Events ---

    async def broadcast_subtitle_download_started(self, job_id: int, total_count: int):
        """Broadcast subtitle download started."""
        await self._ws.broadcast_subtitle_event(
            job_id, "downloading", downloaded=0, total=total_count, failed_count=0
        )

    async def broadcast_subtitle_download_progress(
        self, job_id: int, downloaded: int, total: int, failed_count: int
    ):
        """Broadcast subtitle download progress."""
        await self._ws.broadcast_subtitle_event(
            job_id, "downloading", downloaded=downloaded, total=total, failed_count=failed_count
        )

    async def broadcast_subtitle_download_completed(
        self, job_id: int, total: int, failed_count: int
    ):
        """Broadcast subtitle download completed."""
        await self._ws.broadcast_subtitle_event(
            job_id,
            "completed",
            downloaded=total - failed_count,
            total=total,
            failed_count=failed_count,
        )

    async def broadcast_subtitle_download_failed(self, job_id: int):
        """Broadcast subtitle download failed."""
        await self._ws.broadcast_subtitle_event(job_id, "failed")

    # --- Fingerprint Privacy Events ---

    async def broadcast_fingerprint_disclosure_required(
        self, pending_count: int, pseudonym: str, server_url: str
    ) -> None:
        """Tell the dashboard to show the JIT fingerprint-disclosure modal.

        Fired by the ContributionUploader when fingerprint contributions are
        queued but the user has not yet accepted the privacy disclosure. The
        modal blocks until the user accepts (uploads proceed) or opts out.

        Carries the per-install ``pseudonym`` and ``server_url`` so the modal can
        show the user exactly which identity and endpoint would be used.

        Flat envelope (matches the frontend's `message.type` switch), not a
        nested `data` object.
        """
        await self._ws.broadcast(
            {
                "type": "fingerprint_disclosure_required",
                "pending_count": pending_count,
                "pseudonym": pseudonym,
                "server_url": server_url,
            }
        )

    # --- Update Events ---

    async def broadcast_update_status(
        self,
        state: str,
        latest_version: str | None = None,
        release_notes: str | None = None,
        release_url: str | None = None,
        error: str | None = None,
        last_update_error: str | None = None,
        last_update_success_version: str | None = None,
    ) -> None:
        """Broadcast update availability status to all connected clients.

        current_version and is_frozen are build-level facts injected here so they
        ride every push: the frontend gates the "Restart now" button on is_frozen,
        and it learns update state only from this message. Dropping is_frozen made
        the UI default it to false and hide the button even on frozen builds.
        """
        from app import __version__
        from app.config import is_frozen

        data: dict = {
            "type": "update_status",
            "state": state,
            "current_version": __version__,
            "is_frozen": is_frozen(),
        }
        if latest_version is not None:
            data["latest_version"] = latest_version
        if release_notes is not None:
            data["release_notes"] = release_notes
        if release_url is not None:
            data["release_url"] = release_url
        if error is not None:
            data["error"] = error
        if last_update_error is not None:
            data["last_update_error"] = last_update_error
        if last_update_success_version is not None:
            data["last_update_success_version"] = last_update_success_version
        await self._ws.broadcast(data)

    async def broadcast_gpu_status(self, download_state: dict) -> None:
        """Broadcast CUDA-runtime download progress / GPU-acceleration state to all clients.

        ``download_state`` is the dict from ``cuda_runtime.get_download_state()``:
        ``{"state": "idle|downloading|installing|error", "downloaded": int, "total": int,
        "error": str | None}``. The settings UI uses it to drive the download progress bar.
        """
        await self._ws.broadcast({"type": "gpu_status", "data": download_state})
