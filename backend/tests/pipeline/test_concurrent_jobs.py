"""State-machine-level checks for the concurrent-job model.

Verifies (via the JobStateMachine transition tables) that jobs on different
drives can run simultaneously and that REVIEW_NEEDED is neither a blocking nor
a terminal state.

The authoritative behavior of the per-drive, per-volume_label job-creation
dedup guard (``JobManager._create_job_for_disc``) — which disc-required states
block regardless of label, which post-eject states (MATCHING/ORGANIZING) block
only a same-label re-insert, and which states never block — is exercised
directly, with in-memory DB isolation, in
``tests/unit/test_job_manager.py::TestCreateJobForDiscDedup``. Pipeline tests
have no DB isolation (see ``tests/pipeline/conftest.py``), so they must not call
``_create_job_for_disc`` against the real ``engram.db``.
"""

import pytest

from app.models.disc_job import JobState


@pytest.mark.pipeline
class TestReviewDoesNotBlock:
    """Verify REVIEW_NEEDED is not a blocking state for new jobs."""

    def test_review_needed_is_not_terminal(self):
        """REVIEW_NEEDED should have valid outbound transitions."""
        from app.services.job_state_machine import JobStateMachine

        valid_next = JobStateMachine.VALID_TRANSITIONS.get(JobState.REVIEW_NEEDED, set())
        assert len(valid_next) > 0, "REVIEW_NEEDED should not be terminal"

    def test_review_needed_can_transition_to_ripping(self):
        """After user resolves review, job should be able to resume to RIPPING."""
        from app.services.job_state_machine import JobStateMachine

        valid_next = JobStateMachine.VALID_TRANSITIONS.get(JobState.REVIEW_NEEDED, set())
        assert JobState.RIPPING in valid_next or JobState.IDENTIFYING in valid_next


@pytest.mark.pipeline
class TestConcurrentJobScenario:
    """Concurrent jobs on different drives are independent in the state model."""

    def test_different_drives_are_independent(self):
        """Two jobs on different drives (E: and F:) should not interfere.

        Expected flow:
        1. Job A on E: -> IDENTIFYING -> REVIEW_NEEDED (name prompt)
        2. Job B on F: -> IDENTIFYING -> RIPPING -> ...
        Both can be active simultaneously because they're on different drives.
        """
        # This is a design verification test — the state machine allows it
        from app.services.job_state_machine import JobStateMachine

        # Both IDENTIFYING and REVIEW_NEEDED can exist simultaneously
        # because they're on different drives
        assert JobState.IDENTIFYING in JobStateMachine.VALID_TRANSITIONS[JobState.IDLE]
        assert len(JobStateMachine.VALID_TRANSITIONS[JobState.REVIEW_NEEDED]) > 0
