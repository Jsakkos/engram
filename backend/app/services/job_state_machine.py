"""Job state machine for managing job state transitions.

Centralizes state transition logic, validation, and persistence.
"""

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiscJob, JobState
from app.services.event_broadcaster import EventBroadcaster

logger = logging.getLogger(__name__)


class JobStateMachine:
    """Manages job state transitions with validation and persistence."""

    # Define valid state transitions
    VALID_TRANSITIONS = {
        JobState.IDLE: {JobState.IDENTIFYING, JobState.FAILED},
        JobState.IDENTIFYING: {
            JobState.RIPPING,
            JobState.REVIEW_NEEDED,
            JobState.FAILED,
        },
        JobState.REVIEW_NEEDED: {
            JobState.RIPPING,
            JobState.COMPLETED,
            JobState.FAILED,
        },
        JobState.RIPPING: {
            JobState.MATCHING,
            JobState.ORGANIZING,
            JobState.REVIEW_NEEDED,
            JobState.COMPLETED,
            JobState.FAILED,
        },
        JobState.MATCHING: {
            JobState.ORGANIZING,
            JobState.REVIEW_NEEDED,
            JobState.COMPLETED,
            JobState.FAILED,
        },
        JobState.ORGANIZING: {
            JobState.REVIEW_NEEDED,
            JobState.COMPLETED,
            JobState.FAILED,
        },
        JobState.COMPLETED: set(),  # Terminal state
        JobState.FAILED: set(),  # Terminal state
    }

    def __init__(self, event_broadcaster: EventBroadcaster):
        self._broadcaster = event_broadcaster

    def can_transition(self, from_state: JobState, to_state: JobState) -> bool:
        """Validate if state transition is allowed.

        Args:
            from_state: Current job state
            to_state: Desired job state

        Returns:
            True if transition is valid, False otherwise
        """
        # Allow staying in same state
        if from_state == to_state:
            return True

        # Check if transition is in valid transitions map
        return to_state in self.VALID_TRANSITIONS.get(from_state, set())

    async def transition(
        self,
        job: DiscJob,
        to_state: JobState,
        session: AsyncSession,
        error_message: str | None = None,
        broadcast: bool = True,
    ) -> bool:
        """Perform validated state transition with persistence and broadcasting.

        Args:
            job: Job to transition
            to_state: Target state
            session: Database session
            error_message: Error message if transitioning to FAILED state
            broadcast: Whether to broadcast the state change

        Returns:
            True if transition succeeded, False if invalid

        Raises:
            ValueError: If transition is invalid and strict mode is enabled
        """
        from_state = job.state

        # Validate transition
        if not self.can_transition(from_state, to_state):
            logger.warning(
                f"Invalid state transition for job {job.id}: {from_state.value} -> {to_state.value}"
            )
            return False

        # Log transition
        logger.info(f"Job {job.id} state transition: {from_state.value} -> {to_state.value}")

        # Update job state
        job.state = to_state
        job.updated_at = datetime.utcnow()

        # Set error message if transitioning to failed state
        if to_state == JobState.FAILED and error_message:
            job.error_message = error_message

        # Persist to database
        await session.commit()

        # Broadcast state change if requested (failure is non-fatal since DB is committed)
        if broadcast:
            try:
                if to_state == JobState.FAILED:
                    await self._broadcaster.broadcast_job_failed(
                        job.id, error_message or "Unknown error"
                    )
                elif to_state == JobState.COMPLETED:
                    await self._broadcaster.broadcast_job_completed(job.id)
                else:
                    await self._broadcaster.broadcast_job_state_changed(job.id, to_state)
            except Exception as e:
                logger.error(
                    f"Job {job.id}: broadcast failed after committing {to_state.value}: {e}",
                    exc_info=True,
                )

        return True

    async def transition_to_failed(
        self,
        job: DiscJob,
        session: AsyncSession,
        error_message: str,
        broadcast: bool = True,
    ) -> bool:
        """Convenience method to transition to FAILED state.

        Args:
            job: Job to fail
            session: Database session
            error_message: Reason for failure
            broadcast: Whether to broadcast the failure

        Returns:
            True if transition succeeded
        """
        return await self.transition(
            job, JobState.FAILED, session, error_message=error_message, broadcast=broadcast
        )

    async def transition_to_review(
        self,
        job: DiscJob,
        session: AsyncSession,
        reason: str | None = None,
        broadcast: bool = True,
    ) -> bool:
        """Convenience method to transition to REVIEW_NEEDED state.

        Args:
            job: Job requiring review
            session: Database session
            reason: Optional reason for review
            broadcast: Whether to broadcast the state change

        Returns:
            True if transition succeeded
        """
        if reason:
            job.review_reason = reason

        return await self.transition(job, JobState.REVIEW_NEEDED, session, broadcast=broadcast)

    async def transition_to_completed(
        self,
        job: DiscJob,
        session: AsyncSession,
        broadcast: bool = True,
    ) -> bool:
        """Convenience method to transition to COMPLETED state.

        Args:
            job: Job to complete
            session: Database session
            broadcast: Whether to broadcast the completion

        Returns:
            True if transition succeeded
        """
        return await self.transition(job, JobState.COMPLETED, session, broadcast=broadcast)

    def get_next_states(self, current_state: JobState) -> set[JobState]:
        """Get valid next states from current state.

        Args:
            current_state: Current job state

        Returns:
            Set of valid next states
        """
        return self.VALID_TRANSITIONS.get(current_state, set())
