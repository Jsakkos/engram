"""Tests for staged file cleanup policies (#28)."""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import JobState
from app.services.job_state_machine import JobStateMachine

# ---------------------------------------------------------------------------
# State machine terminal callback
# ---------------------------------------------------------------------------


@pytest.fixture
def state_machine():
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return JobStateMachine(broadcaster)


class TestTerminalCallback:
    """Verify that the state machine fires callbacks on COMPLETED/FAILED."""

    @pytest.mark.asyncio
    async def test_callback_fires_on_completed(self, state_machine):
        callback = AsyncMock()
        state_machine.on_terminal_state(callback)

        job = MagicMock()
        job.id = 1
        job.state = JobState.ORGANIZING

        session = AsyncMock()

        await state_machine.transition(job, JobState.COMPLETED, session)
        callback.assert_awaited_once_with(1, JobState.COMPLETED)

    @pytest.mark.asyncio
    async def test_callback_fires_on_failed(self, state_machine):
        callback = AsyncMock()
        state_machine.on_terminal_state(callback)

        job = MagicMock()
        job.id = 2
        job.state = JobState.RIPPING

        session = AsyncMock()

        await state_machine.transition(job, JobState.FAILED, session, error_message="test error")
        callback.assert_awaited_once_with(2, JobState.FAILED)

    @pytest.mark.asyncio
    async def test_callback_not_fired_on_non_terminal(self, state_machine):
        callback = AsyncMock()
        state_machine.on_terminal_state(callback)

        job = MagicMock()
        job.id = 3
        job.state = JobState.IDENTIFYING

        session = AsyncMock()

        await state_machine.transition(job, JobState.RIPPING, session)
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_error_does_not_prevent_transition(self, state_machine):
        callback = AsyncMock(side_effect=RuntimeError("boom"))
        state_machine.on_terminal_state(callback)

        job = MagicMock()
        job.id = 4
        job.state = JobState.ORGANIZING

        session = AsyncMock()

        result = await state_machine.transition(job, JobState.COMPLETED, session)
        assert result is True  # Transition still succeeds


# ---------------------------------------------------------------------------
# Policy-based cleanup (_on_job_terminal)
# ---------------------------------------------------------------------------


class TestCleanupPolicy:
    """Test that _on_job_terminal respects the configured policy."""

    def _make_cleanup_service(self):
        """Create a CleanupService with mocked delete_staging."""
        from app.services.cleanup_service import CleanupService

        svc = CleanupService()
        svc.delete_staging = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_on_success_cleans_completed(self):
        svc = self._make_cleanup_service()
        config = MagicMock(staging_cleanup_policy="on_success", discdb_contributions_enabled=False)
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(1, JobState.COMPLETED)
        svc.delete_staging.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_on_success_skips_failed(self):
        svc = self._make_cleanup_service()
        config = MagicMock(staging_cleanup_policy="on_success", discdb_contributions_enabled=False)
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(2, JobState.FAILED)
        svc.delete_staging.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_completion_cleans_completed(self):
        svc = self._make_cleanup_service()
        config = MagicMock(
            staging_cleanup_policy="on_completion", discdb_contributions_enabled=False
        )
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(3, JobState.COMPLETED)
        svc.delete_staging.assert_awaited_once_with(3)

    @pytest.mark.asyncio
    async def test_on_completion_cleans_failed(self):
        svc = self._make_cleanup_service()
        config = MagicMock(
            staging_cleanup_policy="on_completion", discdb_contributions_enabled=False
        )
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(4, JobState.FAILED)
        svc.delete_staging.assert_awaited_once_with(4)

    @pytest.mark.asyncio
    async def test_manual_never_cleans(self):
        svc = self._make_cleanup_service()
        config = MagicMock(staging_cleanup_policy="manual", discdb_contributions_enabled=False)
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(5, JobState.COMPLETED)
            await svc.on_job_terminal(6, JobState.FAILED)
        svc.delete_staging.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_after_days_defers_to_background(self):
        svc = self._make_cleanup_service()
        config = MagicMock(staging_cleanup_policy="after_days", discdb_contributions_enabled=False)
        with patch("app.services.config_service.get_config", return_value=config):
            await svc.on_job_terminal(7, JobState.COMPLETED)
        svc.delete_staging.assert_not_awaited()


# ---------------------------------------------------------------------------
# Timed cleanup (_run_timed_cleanup)
# ---------------------------------------------------------------------------


class TestTimedCleanup:
    """Test the background timed cleanup task."""

    @pytest.mark.asyncio
    async def test_deletes_old_directories(self, tmp_path):
        """Directories older than max_age_days should be deleted."""
        # Create a "job_1" dir and backdate it
        old_dir = tmp_path / "job_1"
        old_dir.mkdir()
        (old_dir / "title_0.mkv").write_text("fake")
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(old_dir, (old_time, old_time))

        # Create a "job_2" dir that's recent
        new_dir = tmp_path / "job_2"
        new_dir.mkdir()
        (new_dir / "title_0.mkv").write_text("fake")

        from app.services.cleanup_service import CleanupService

        svc = CleanupService()

        # Monkey-patch sleep: first call returns immediately (lets cleanup run),
        # second call cancels.
        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return  # Let the first iteration proceed
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await svc.run_timed_cleanup(str(tmp_path), max_age_days=7)
            except asyncio.CancelledError:
                pass

        assert not old_dir.exists(), "Old staging dir should be deleted"
        assert new_dir.exists(), "Recent staging dir should be preserved"

    @pytest.mark.asyncio
    async def test_ignores_non_job_directories(self, tmp_path):
        """Directories not matching job_* prefix should be ignored."""
        other_dir = tmp_path / "other_stuff"
        other_dir.mkdir()
        old_time = time.time() - (30 * 86400)
        os.utime(other_dir, (old_time, old_time))

        from app.services.cleanup_service import CleanupService

        svc = CleanupService()

        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await svc.run_timed_cleanup(str(tmp_path), max_age_days=7)
            except asyncio.CancelledError:
                pass

        assert other_dir.exists(), "Non-job directory should not be deleted"
