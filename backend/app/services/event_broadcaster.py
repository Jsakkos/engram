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
