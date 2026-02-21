"""Unit tests for JobStateMachine.

Tests state transition validation, persistence, and broadcasting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiscJob
from app.models.disc_job import JobState
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine


@pytest.fixture
def mock_broadcaster():
    """Create a mock EventBroadcaster."""
    broadcaster = MagicMock(spec=EventBroadcaster)
    broadcaster.broadcast_job_state_changed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    return broadcaster


@pytest.fixture
def state_machine(mock_broadcaster):
    """Create a JobStateMachine instance."""
    return JobStateMachine(mock_broadcaster)


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    return session


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    job = DiscJob(
        id=1,
        drive_id="D:",
        volume_label="TEST_DISC",
        state=JobState.IDLE,
    )
    return job


class TestStateTransitionValidation:
    """Test state transition validation logic."""

    def test_can_transition_valid(self, state_machine):
        """Test that valid transitions are allowed."""
        # IDLE -> IDENTIFYING is valid
        assert state_machine.can_transition(JobState.IDLE, JobState.IDENTIFYING)

        # IDENTIFYING -> RIPPING is valid
        assert state_machine.can_transition(JobState.IDENTIFYING, JobState.RIPPING)

        # RIPPING -> MATCHING is valid
        assert state_machine.can_transition(JobState.RIPPING, JobState.MATCHING)

        # MATCHING -> ORGANIZING is valid
        assert state_machine.can_transition(JobState.MATCHING, JobState.ORGANIZING)

        # ORGANIZING -> COMPLETED is valid
        assert state_machine.can_transition(JobState.ORGANIZING, JobState.COMPLETED)

    def test_can_transition_to_failed_from_any_state(self, state_machine):
        """Test that FAILED can be reached from any non-terminal state."""
        non_terminal_states = [
            JobState.IDLE,
            JobState.IDENTIFYING,
            JobState.REVIEW_NEEDED,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        ]

        for state in non_terminal_states:
            assert state_machine.can_transition(state, JobState.FAILED)

    def test_can_transition_to_review_from_appropriate_states(self, state_machine):
        """Test transitions to REVIEW_NEEDED state."""
        # Valid transitions to REVIEW_NEEDED
        assert state_machine.can_transition(JobState.IDENTIFYING, JobState.REVIEW_NEEDED)
        assert state_machine.can_transition(JobState.RIPPING, JobState.REVIEW_NEEDED)
        assert state_machine.can_transition(JobState.MATCHING, JobState.REVIEW_NEEDED)

    def test_can_transition_invalid(self, state_machine):
        """Test that invalid transitions are rejected."""
        # Cannot go backwards
        assert not state_machine.can_transition(JobState.RIPPING, JobState.IDENTIFYING)
        assert not state_machine.can_transition(JobState.MATCHING, JobState.RIPPING)

        # Cannot skip states
        assert not state_machine.can_transition(JobState.IDLE, JobState.RIPPING)
        assert not state_machine.can_transition(JobState.IDLE, JobState.COMPLETED)

        # Cannot transition from terminal states
        assert not state_machine.can_transition(JobState.COMPLETED, JobState.RIPPING)
        assert not state_machine.can_transition(JobState.FAILED, JobState.IDENTIFYING)

    def test_can_transition_same_state(self, state_machine):
        """Test that staying in the same state is always allowed."""
        for state in JobState:
            assert state_machine.can_transition(state, state)

    def test_get_next_states(self, state_machine):
        """Test getting valid next states."""
        # IDLE can go to IDENTIFYING or FAILED
        next_states = state_machine.get_next_states(JobState.IDLE)
        assert JobState.IDENTIFYING in next_states
        assert JobState.FAILED in next_states

        # COMPLETED has no next states (terminal)
        next_states = state_machine.get_next_states(JobState.COMPLETED)
        assert len(next_states) == 0


@pytest.mark.asyncio
class TestStateTransitions:
    """Test actual state transition execution."""

    async def test_transition_updates_state(self, state_machine, sample_job, mock_session):
        """Test that transition updates job state."""
        result = await state_machine.transition(sample_job, JobState.IDENTIFYING, mock_session)

        assert result is True
        assert sample_job.state == JobState.IDENTIFYING
        assert sample_job.updated_at is not None

    async def test_transition_commits_to_database(self, state_machine, sample_job, mock_session):
        """Test that transition commits changes."""
        await state_machine.transition(sample_job, JobState.IDENTIFYING, mock_session)

        mock_session.commit.assert_called_once()

    async def test_transition_broadcasts_by_default(
        self, state_machine, sample_job, mock_session, mock_broadcaster
    ):
        """Test that transition broadcasts state change."""
        await state_machine.transition(sample_job, JobState.IDENTIFYING, mock_session)

        mock_broadcaster.broadcast_job_state_changed.assert_called_once_with(
            sample_job.id, JobState.IDENTIFYING
        )

    async def test_transition_no_broadcast_when_disabled(
        self, state_machine, sample_job, mock_session, mock_broadcaster
    ):
        """Test that broadcast can be disabled."""
        await state_machine.transition(
            sample_job, JobState.IDENTIFYING, mock_session, broadcast=False
        )

        mock_broadcaster.broadcast_job_state_changed.assert_not_called()

    async def test_transition_rejects_invalid_transition(
        self, state_machine, sample_job, mock_session
    ):
        """Test that invalid transitions are rejected."""
        result = await state_machine.transition(sample_job, JobState.MATCHING, mock_session)

        assert result is False
        assert sample_job.state == JobState.IDLE  # State unchanged
        mock_session.commit.assert_not_called()


@pytest.mark.asyncio
class TestConvenienceMethods:
    """Test convenience methods for common transitions."""

    async def test_transition_to_failed(
        self, state_machine, sample_job, mock_session, mock_broadcaster
    ):
        """Test transition_to_failed convenience method."""
        result = await state_machine.transition_to_failed(
            sample_job, mock_session, error_message="Test error"
        )

        assert result is True
        assert sample_job.state == JobState.FAILED
        assert sample_job.error_message == "Test error"
        mock_broadcaster.broadcast_job_failed.assert_called_once_with(sample_job.id, "Test error")

    async def test_transition_to_review(self, state_machine, sample_job, mock_session):
        """Test transition_to_review convenience method."""
        # First move to a state that can transition to REVIEW_NEEDED
        sample_job.state = JobState.IDENTIFYING

        result = await state_machine.transition_to_review(
            sample_job, mock_session, reason="Ambiguous content"
        )

        assert result is True
        assert sample_job.state == JobState.REVIEW_NEEDED
        assert sample_job.error_message == "Ambiguous content"

    async def test_transition_to_completed(
        self, state_machine, sample_job, mock_session, mock_broadcaster
    ):
        """Test transition_to_completed convenience method."""
        # Move to a state that can transition to COMPLETED
        sample_job.state = JobState.ORGANIZING

        result = await state_machine.transition_to_completed(sample_job, mock_session)

        assert result is True
        assert sample_job.state == JobState.COMPLETED
        mock_broadcaster.broadcast_job_completed.assert_called_once_with(sample_job.id)

    async def test_transition_to_failed_with_broadcast_disabled(
        self, state_machine, sample_job, mock_session, mock_broadcaster
    ):
        """Test that broadcast can be disabled in convenience methods."""
        await state_machine.transition_to_failed(
            sample_job, mock_session, error_message="Test", broadcast=False
        )

        mock_broadcaster.broadcast_job_failed.assert_not_called()


@pytest.mark.asyncio
class TestErrorCases:
    """Test error handling in state machine."""

    async def test_transition_with_none_job(self, state_machine, mock_session):
        """Test handling of None job."""
        with pytest.raises(AttributeError):
            await state_machine.transition(None, JobState.IDENTIFYING, mock_session)

    async def test_transition_logs_invalid_transition(
        self, state_machine, sample_job, mock_session
    ):
        """Test that invalid transitions are logged."""
        with patch("app.services.job_state_machine.logger") as mock_logger:
            result = await state_machine.transition(sample_job, JobState.MATCHING, mock_session)

            assert result is False
            mock_logger.warning.assert_called_once()
            assert "Invalid state transition" in str(mock_logger.warning.call_args)


@pytest.mark.asyncio
class TestStateTransitionSequences:
    """Test realistic state transition sequences."""

    async def test_happy_path_tv_workflow(self, state_machine, sample_job, mock_session):
        """Test a complete happy path workflow for TV content."""
        transitions = [
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
            JobState.COMPLETED,
        ]

        for target_state in transitions:
            result = await state_machine.transition(sample_job, target_state, mock_session)
            assert result is True
            assert sample_job.state == target_state

    async def test_review_needed_workflow(self, state_machine, sample_job, mock_session):
        """Test workflow that requires review."""
        # IDLE -> IDENTIFYING -> REVIEW_NEEDED
        await state_machine.transition(sample_job, JobState.IDENTIFYING, mock_session)
        result = await state_machine.transition_to_review(
            sample_job, mock_session, reason="Ambiguous"
        )

        assert result is True
        assert sample_job.state == JobState.REVIEW_NEEDED

        # REVIEW_NEEDED -> RIPPING (after user resolves)
        result = await state_machine.transition(sample_job, JobState.RIPPING, mock_session)
        assert result is True

    async def test_failure_from_any_state(self, state_machine, sample_job, mock_session):
        """Test that jobs can fail from any non-terminal state."""
        states_to_test = [
            JobState.IDLE,
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
        ]

        for state in states_to_test:
            # Reset job state
            sample_job.state = state

            result = await state_machine.transition_to_failed(
                sample_job, mock_session, error_message="Test failure"
            )

            assert result is True
            assert sample_job.state == JobState.FAILED

            # Reset for next iteration
            sample_job.state = JobState.IDLE
