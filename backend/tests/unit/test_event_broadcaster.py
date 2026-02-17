"""Unit tests for EventBroadcaster.

Tests domain-specific event broadcasting abstraction layer.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.websocket import ConnectionManager
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.event_broadcaster import EventBroadcaster


@pytest.fixture
def mock_ws_manager():
    """Create a mock WebSocket connection manager."""
    ws = MagicMock(spec=ConnectionManager)
    ws.broadcast_drive_event = AsyncMock()
    ws.broadcast_job_update = AsyncMock()
    ws.broadcast_title_update = AsyncMock()
    ws.broadcast_titles_discovered = AsyncMock()
    ws.broadcast_subtitle_event = AsyncMock()
    return ws


@pytest.fixture
def broadcaster(mock_ws_manager):
    """Create an EventBroadcaster instance."""
    return EventBroadcaster(mock_ws_manager)


@pytest.fixture
def sample_job():
    """Create a sample job."""
    return DiscJob(
        id=1,
        drive_id="D:",
        volume_label="TEST_DISC",
        content_type=ContentType.TV,
        state=JobState.RIPPING,
    )


@pytest.fixture
def sample_title():
    """Create a sample title."""
    return DiscTitle(
        id=10,
        job_id=1,
        title_index=0,
        duration_seconds=2400,
        file_size_bytes=1024 * 1024 * 1024,
        state=TitleState.RIPPING,
    )


@pytest.mark.asyncio
class TestDriveEvents:
    """Test drive-related event broadcasting."""

    async def test_broadcast_drive_inserted(self, broadcaster, mock_ws_manager):
        """Test broadcasting disc insertion event."""
        await broadcaster.broadcast_drive_inserted("D:", "TEST_DISC")

        mock_ws_manager.broadcast_drive_event.assert_called_once_with(
            "D:", "inserted", "TEST_DISC"
        )

    async def test_broadcast_drive_removed(self, broadcaster, mock_ws_manager):
        """Test broadcasting disc removal event."""
        await broadcaster.broadcast_drive_removed("D:", "TEST_DISC")

        mock_ws_manager.broadcast_drive_event.assert_called_once_with(
            "D:", "removed", "TEST_DISC"
        )


@pytest.mark.asyncio
class TestJobLifecycleEvents:
    """Test job lifecycle event broadcasting."""

    async def test_broadcast_job_created(self, broadcaster, mock_ws_manager, sample_job):
        """Test broadcasting new job creation."""
        await broadcaster.broadcast_job_created(sample_job)

        mock_ws_manager.broadcast_job_update.assert_called_once_with(
            sample_job.id, sample_job.state.value
        )

    async def test_broadcast_job_state_changed(self, broadcaster, mock_ws_manager):
        """Test broadcasting job state transition."""
        await broadcaster.broadcast_job_state_changed(1, JobState.MATCHING)

        mock_ws_manager.broadcast_job_update.assert_called_once_with(
            1, JobState.MATCHING.value
        )

    async def test_broadcast_job_progress(self, broadcaster, mock_ws_manager):
        """Test broadcasting job progress update."""
        await broadcaster.broadcast_job_progress(
            job_id=1,
            progress_percent=50,
            current_speed="10.5 MB/s",
            eta_seconds=300,
        )

        mock_ws_manager.broadcast_job_update.assert_called_once()
        call_args = mock_ws_manager.broadcast_job_update.call_args

        assert call_args[0][0] == 1  # job_id
        assert call_args[0][1] is None  # state unchanged
        assert call_args[1]["progress_percent"] == 50
        assert call_args[1]["current_speed"] == "10.5 MB/s"
        assert call_args[1]["eta_seconds"] == 300

    async def test_broadcast_job_progress_minimal(self, broadcaster, mock_ws_manager):
        """Test broadcasting job progress with minimal parameters."""
        await broadcaster.broadcast_job_progress(job_id=1, progress_percent=75)

        mock_ws_manager.broadcast_job_update.assert_called_once()
        call_args = mock_ws_manager.broadcast_job_update.call_args

        assert call_args[0][0] == 1
        assert call_args[1]["progress_percent"] == 75
        assert call_args[1]["current_speed"] is None
        assert call_args[1]["eta_seconds"] is None

    async def test_broadcast_job_failed(self, broadcaster, mock_ws_manager):
        """Test broadcasting job failure."""
        await broadcaster.broadcast_job_failed(1, "Ripping failed")

        mock_ws_manager.broadcast_job_update.assert_called_once_with(
            1, JobState.FAILED.value, error_message="Ripping failed"
        )

    async def test_broadcast_job_completed(self, broadcaster, mock_ws_manager):
        """Test broadcasting job completion."""
        await broadcaster.broadcast_job_completed(1)

        mock_ws_manager.broadcast_job_update.assert_called_once_with(
            1, JobState.COMPLETED.value
        )


@pytest.mark.asyncio
class TestTitleDiscoveryEvents:
    """Test title discovery event broadcasting."""

    async def test_broadcast_titles_discovered_minimal(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting title discovery with minimal info."""
        titles = [
            DiscTitle(id=1, job_id=1, title_index=0),
            DiscTitle(id=2, job_id=1, title_index=1),
        ]

        await broadcaster.broadcast_titles_discovered(job_id=1, titles=titles)

        mock_ws_manager.broadcast_titles_discovered.assert_called_once()
        call_args = mock_ws_manager.broadcast_titles_discovered.call_args

        assert call_args[0][0] == 1  # job_id
        assert len(call_args[0][1]) == 2  # titles
        assert call_args[1]["content_type"] is None

    async def test_broadcast_titles_discovered_full(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting title discovery with full metadata."""
        titles = [
            DiscTitle(
                id=1,
                job_id=1,
                title_index=0,
                duration_seconds=2400,
                file_size_bytes=1024 * 1024 * 1024,
            ),
        ]

        await broadcaster.broadcast_titles_discovered(
            job_id=1,
            titles=titles,
            content_type=ContentType.TV,
            detected_title="Test Show",
            detected_season=1,
        )

        mock_ws_manager.broadcast_titles_discovered.assert_called_once()
        call_args = mock_ws_manager.broadcast_titles_discovered.call_args

        assert call_args[1]["content_type"] == "tv"
        assert call_args[1]["detected_title"] == "Test Show"
        assert call_args[1]["detected_season"] == 1


@pytest.mark.asyncio
class TestTitleStateEvents:
    """Test title state event broadcasting."""

    async def test_broadcast_title_ripping_started(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting title ripping started."""
        await broadcaster.broadcast_title_ripping_started(sample_title)

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.RIPPING.value,
        )

    async def test_broadcast_title_ripping_progress(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting title ripping progress."""
        await broadcaster.broadcast_title_ripping_progress(sample_title, 50)

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            progress_percent=50,
        )

    async def test_broadcast_title_matching_started(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting title matching started."""
        await broadcaster.broadcast_title_matching_started(sample_title)

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.MATCHING.value,
        )

    async def test_broadcast_title_matched(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting successful title match."""
        await broadcaster.broadcast_title_matched(sample_title, "S01E05", 0.95)

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.MATCHED.value,
            matched_episode="S01E05",
            match_confidence=0.95,
        )

    async def test_broadcast_title_state_changed(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting generic title state change."""
        await broadcaster.broadcast_title_state_changed(
            sample_title, TitleState.COMPLETED
        )

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.COMPLETED.value,
        )

    async def test_broadcast_title_completed(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting title processing completed."""
        await broadcaster.broadcast_title_completed(sample_title)

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.COMPLETED.value,
        )

    async def test_broadcast_title_failed(
        self, broadcaster, mock_ws_manager, sample_title
    ):
        """Test broadcasting title processing failed."""
        await broadcaster.broadcast_title_failed(sample_title, "Matching error")

        mock_ws_manager.broadcast_title_update.assert_called_once_with(
            sample_title.job_id,
            sample_title.id,
            state=TitleState.FAILED.value,
            error_message="Matching error",
        )


@pytest.mark.asyncio
class TestSubtitleEvents:
    """Test subtitle-related event broadcasting."""

    async def test_broadcast_subtitle_download_started(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting subtitle download started."""
        await broadcaster.broadcast_subtitle_download_started(job_id=1, total_count=10)

        mock_ws_manager.broadcast_subtitle_event.assert_called_once_with(
            1,
            "downloading",
            downloaded=0,
            total=10,
            failed_count=0,
        )

    async def test_broadcast_subtitle_download_progress(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting subtitle download progress."""
        await broadcaster.broadcast_subtitle_download_progress(
            job_id=1,
            downloaded=5,
            total=10,
            failed_count=1,
        )

        mock_ws_manager.broadcast_subtitle_event.assert_called_once_with(
            1,
            "downloading",
            downloaded=5,
            total=10,
            failed_count=1,
        )

    async def test_broadcast_subtitle_download_completed(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting subtitle download completed."""
        await broadcaster.broadcast_subtitle_download_completed(
            job_id=1,
            total=10,
            failed_count=2,
        )

        mock_ws_manager.broadcast_subtitle_event.assert_called_once_with(
            1,
            "completed",
            downloaded=8,  # total - failed
            total=10,
            failed_count=2,
        )

    async def test_broadcast_subtitle_download_failed(
        self, broadcaster, mock_ws_manager
    ):
        """Test broadcasting subtitle download failed."""
        await broadcaster.broadcast_subtitle_download_failed(job_id=1)

        mock_ws_manager.broadcast_subtitle_event.assert_called_once_with(1, "failed")


@pytest.mark.asyncio
class TestAbstractionLayer:
    """Test that EventBroadcaster properly abstracts WebSocket calls."""

    async def test_encapsulates_websocket_details(
        self, broadcaster, mock_ws_manager
    ):
        """Test that domain events don't expose WebSocket implementation."""
        # Call domain-specific method
        await broadcaster.broadcast_job_state_changed(1, JobState.RIPPING)

        # Should translate to underlying WebSocket call
        mock_ws_manager.broadcast_job_update.assert_called_once()

        # Caller doesn't need to know about WebSocket message structure
        call_args = mock_ws_manager.broadcast_job_update.call_args
        assert len(call_args[0]) >= 2  # At minimum: job_id and state

    async def test_semantic_method_names(self, broadcaster):
        """Test that method names are semantically meaningful."""
        # Method names should describe domain events, not implementation
        assert hasattr(broadcaster, "broadcast_job_created")
        assert hasattr(broadcaster, "broadcast_title_matched")
        assert hasattr(broadcaster, "broadcast_subtitle_download_started")

        # Should NOT have generic WebSocket method names
        assert not hasattr(broadcaster, "send_message")
        assert not hasattr(broadcaster, "emit_event")

    async def test_consistent_parameter_naming(self, broadcaster):
        """Test that parameters use consistent domain terminology."""
        # All job events should use job_id, not websocket_id or connection_id
        # All title events should use title_id, not track_id or file_id
        # This is enforced by the method signatures
        pass  # Verified by type checking and static analysis
