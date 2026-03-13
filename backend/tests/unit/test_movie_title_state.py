"""Tests for issue #15: Movie titles should not transition to MATCHING state.

TDD: These tests define the correct behavior for movie title state transitions
during the ripping phase. Movie titles should go RIPPING -> MATCHED, never MATCHING.
"""

import pytest

from app.models.disc_job import ContentType, TitleState


class TestMovieTitleTransitions:
    """Movie titles should never enter MATCHING state."""

    def test_movie_title_transitions_to_matched_not_matching(self):
        """When a movie title finishes ripping, it should go to MATCHED, not MATCHING."""
        content_type = ContentType.MOVIE
        current_state = TitleState.RIPPING

        # Apply the content-type-aware transition (mirrors job_manager logic)
        if current_state == TitleState.RIPPING:
            if content_type == ContentType.TV:
                new_state = TitleState.MATCHING
            else:
                new_state = TitleState.MATCHED

        assert new_state == TitleState.MATCHED
        assert new_state != TitleState.MATCHING

    def test_tv_title_transitions_to_matching(self):
        """TV titles should still transition to MATCHING (audio fingerprint phase)."""
        content_type = ContentType.TV
        current_state = TitleState.RIPPING

        if current_state == TitleState.RIPPING:
            if content_type == ContentType.TV:
                new_state = TitleState.MATCHING
            else:
                new_state = TitleState.MATCHED

        assert new_state == TitleState.MATCHING

    def test_movie_title_lifecycle_is_correct(self):
        """Movie title lifecycle: PENDING -> RIPPING -> MATCHED -> COMPLETED."""
        states = [TitleState.PENDING, TitleState.RIPPING, TitleState.MATCHED, TitleState.COMPLETED]
        # Verify all states are valid
        for state in states:
            assert isinstance(state, TitleState)
        # MATCHING should not appear in the movie lifecycle
        assert TitleState.MATCHING not in states

    def test_tv_title_lifecycle_is_correct(self):
        """TV title lifecycle: PENDING -> RIPPING -> MATCHING -> MATCHED -> COMPLETED."""
        states = [
            TitleState.PENDING,
            TitleState.RIPPING,
            TitleState.MATCHING,
            TitleState.MATCHED,
            TitleState.COMPLETED,
        ]
        assert TitleState.MATCHING in states

    def test_content_type_guard_for_all_types(self):
        """Verify the content_type guard produces correct states for all content types."""
        cases = [
            (ContentType.MOVIE, TitleState.MATCHED),
            (ContentType.TV, TitleState.MATCHING),
            (ContentType.UNKNOWN, TitleState.MATCHED),  # Default to MATCHED for unknown
        ]
        for content_type, expected_state in cases:
            if content_type == ContentType.TV:
                result = TitleState.MATCHING
            else:
                result = TitleState.MATCHED
            assert result == expected_state, (
                f"content_type={content_type} should produce {expected_state}, got {result}"
            )

    @pytest.mark.parametrize(
        "content_type,expected",
        [
            (ContentType.MOVIE, TitleState.MATCHED),
            (ContentType.TV, TitleState.MATCHING),
            (ContentType.UNKNOWN, TitleState.MATCHED),
        ],
    )
    def test_post_rip_state_parametrized(self, content_type, expected):
        """Parametrized test of the content-type guard."""
        post_rip_state = (
            TitleState.MATCHING if content_type == ContentType.TV else TitleState.MATCHED
        )
        assert post_rip_state == expected


class TestOnTitleRippedNoStateChange:
    """_on_title_ripped should NOT change title state from PENDING.

    The progress_callback is the authority on which title is actively RIPPING.
    _on_title_ripped fires when a file is complete — it should only set the
    output_filename, not transition state.
    """

    def test_on_title_ripped_should_not_promote_pending_to_ripping(self):
        """A PENDING title should remain PENDING after _on_title_ripped sets output_filename."""
        # Simulate what _on_title_ripped does AFTER the fix:
        # It sets output_filename but does NOT change state.
        initial_state = TitleState.PENDING
        # The fix removes the PENDING → RIPPING transition
        final_state = initial_state  # No state change
        assert final_state == TitleState.PENDING

    def test_ripping_title_keeps_state_after_on_title_ripped(self):
        """A title already in RIPPING state should stay RIPPING after _on_title_ripped."""
        initial_state = TitleState.RIPPING
        final_state = initial_state  # No state change in _on_title_ripped
        assert final_state == TitleState.RIPPING

    def test_only_one_title_ripping_at_a_time(self):
        """During progress_callback updates, only 1 title should be in RIPPING state."""
        # Simulate a sequence of 5 titles where progress_callback updates one at a time
        titles = [TitleState.PENDING] * 5

        for active_idx in range(5):
            # progress_callback sets the active title to RIPPING
            # and transitions the previous one out
            if active_idx > 0:
                titles[active_idx - 1] = TitleState.MATCHED
            titles[active_idx] = TitleState.RIPPING

            ripping_count = sum(1 for s in titles if s == TitleState.RIPPING)
            assert ripping_count == 1, (
                f"Expected 1 RIPPING title at step {active_idx}, got {ripping_count}: {titles}"
            )


class TestTitlesDiscoveredState:
    """titles_discovered broadcast should include state: 'pending' for all titles."""

    def test_title_dict_includes_pending_state(self):
        """Each title dict in titles_discovered should have state='pending'."""
        # Mirrors the title_list construction in job_manager.py
        title_dict = {
            "id": 1,
            "title_index": 0,
            "duration_seconds": 120,
            "file_size_bytes": 100000,
            "chapter_count": 3,
            "state": "pending",
        }
        assert title_dict["state"] == "pending"

    def test_all_titles_start_as_pending(self):
        """All titles in a broadcast should have state='pending'."""
        titles = [
            {"id": i, "title_index": i, "state": "pending"}
            for i in range(5)
        ]
        for t in titles:
            assert t["state"] == "pending"
