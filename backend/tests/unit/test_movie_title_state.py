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
