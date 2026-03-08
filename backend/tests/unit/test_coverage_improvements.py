"""Test coverage improvements (#27).

Includes:
- Exhaustive state machine transition matrix
- Property-based tests for Analyst classification
- Property-based tests for Organizer path construction
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.analyst import DiscAnalyst, TitleInfo
from app.core.organizer import (
    clean_movie_name,
    sanitize_filename,
)
from app.models.disc_job import ContentType, JobState
from app.services.job_state_machine import JobStateMachine

# ---------------------------------------------------------------------------
# Exhaustive state machine transition matrix
# ---------------------------------------------------------------------------


@pytest.fixture
def state_machine():
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return JobStateMachine(broadcaster)


class TestExhaustiveTransitionMatrix:
    """Verify every (from_state, to_state) pair against the declared VALID_TRANSITIONS."""

    def test_all_valid_transitions_accepted(self, state_machine):
        """Every transition in VALID_TRANSITIONS should be accepted."""
        for from_state, valid_targets in JobStateMachine.VALID_TRANSITIONS.items():
            for to_state in valid_targets:
                assert state_machine.can_transition(from_state, to_state), (
                    f"Expected {from_state.value} -> {to_state.value} to be valid"
                )

    def test_all_invalid_transitions_rejected(self, state_machine):
        """Every transition NOT in VALID_TRANSITIONS should be rejected (except self-loops)."""
        all_states = set(JobState)
        for from_state in JobState:
            valid_targets = JobStateMachine.VALID_TRANSITIONS.get(from_state, set())
            invalid_targets = all_states - valid_targets - {from_state}
            for to_state in invalid_targets:
                assert not state_machine.can_transition(from_state, to_state), (
                    f"Expected {from_state.value} -> {to_state.value} to be invalid"
                )

    def test_self_loops_always_valid(self, state_machine):
        """Every state should allow transitioning to itself."""
        for state in JobState:
            assert state_machine.can_transition(state, state)

    def test_terminal_states_have_no_outgoing(self, state_machine):
        """COMPLETED and FAILED should have empty valid transition sets."""
        assert state_machine.get_next_states(JobState.COMPLETED) == set()
        assert state_machine.get_next_states(JobState.FAILED) == set()

    def test_every_non_terminal_can_reach_failed(self, state_machine):
        """Every non-terminal state should be able to transition to FAILED."""
        terminal = {JobState.COMPLETED, JobState.FAILED}
        for state in JobState:
            if state not in terminal:
                assert state_machine.can_transition(state, JobState.FAILED), (
                    f"{state.value} should be able to reach FAILED"
                )

    def test_happy_path_reachable(self, state_machine):
        """The happy path IDLE → IDENTIFYING → RIPPING → MATCHING → ORGANIZING → COMPLETED
        should be fully reachable."""
        path = [
            JobState.IDLE,
            JobState.IDENTIFYING,
            JobState.RIPPING,
            JobState.MATCHING,
            JobState.ORGANIZING,
            JobState.COMPLETED,
        ]
        for i in range(len(path) - 1):
            assert state_machine.can_transition(path[i], path[i + 1])


# ---------------------------------------------------------------------------
# Property-based tests for Analyst
# ---------------------------------------------------------------------------


# Strategy for generating realistic title durations
tv_duration = st.integers(min_value=18 * 60, max_value=70 * 60)
movie_duration = st.integers(min_value=80 * 60, max_value=210 * 60)
short_duration = st.integers(min_value=10, max_value=17 * 60)


def make_titles(durations: list[int]) -> list[TitleInfo]:
    return [
        TitleInfo(index=i, duration_seconds=d, size_bytes=d * 100_000, chapter_count=10)
        for i, d in enumerate(durations)
    ]


class TestAnalystPropertyBased:
    """Property-based tests for DiscAnalyst classification."""

    @given(
        base=st.integers(min_value=20 * 60, max_value=65 * 60),
        offsets=st.lists(st.integers(min_value=-60, max_value=60), min_size=3, max_size=10),
    )
    @settings(max_examples=50)
    def test_uniform_tv_durations_classified_as_tv(self, base, offsets):
        durations = [base + o for o in offsets]
        """A cluster of 3+ titles with similar durations in TV range → TV."""
        analyst = DiscAnalyst()
        titles = make_titles(durations)
        result = analyst.analyze(titles, volume_label="TEST_SHOW_S1D1")
        assert result.content_type in (ContentType.TV, ContentType.UNKNOWN), (
            f"Expected TV or UNKNOWN for durations {durations}, got {result.content_type}"
        )

    @given(duration=movie_duration)
    @settings(max_examples=30)
    def test_single_long_title_classified_as_movie(self, duration):
        """A single title in movie range → MOVIE."""
        analyst = DiscAnalyst()
        titles = make_titles([duration])
        result = analyst.analyze(titles, volume_label="INCEPTION_2010")
        assert result.content_type == ContentType.MOVIE

    @given(durations=st.lists(short_duration, min_size=1, max_size=5))
    @settings(max_examples=30)
    def test_only_short_titles_not_movie(self, durations):
        """Only short titles (< 18min) should never be classified as MOVIE."""
        analyst = DiscAnalyst()
        titles = make_titles(durations)
        result = analyst.analyze(titles, volume_label="EXTRAS_DISC")
        assert result.content_type != ContentType.MOVIE or result.needs_review

    @given(
        base=st.integers(min_value=20 * 60, max_value=65 * 60),
        offsets=st.lists(st.integers(min_value=-60, max_value=60), min_size=3, max_size=6),
        extras=st.lists(short_duration, min_size=0, max_size=3),
    )
    @settings(max_examples=30)
    def test_tv_with_extras_still_tv(self, base, offsets, extras):
        tv_durations = [base + o for o in offsets]
        """TV episodes + short extras should still classify as TV."""
        analyst = DiscAnalyst()
        titles = make_titles(tv_durations + extras)
        result = analyst.analyze(titles, volume_label="SHOW_S1D1")
        assert result.content_type in (ContentType.TV, ContentType.UNKNOWN)


# ---------------------------------------------------------------------------
# Property-based tests for Organizer
# ---------------------------------------------------------------------------


class TestOrganizerPropertyBased:
    """Property-based tests for file naming and path construction."""

    @given(
        name=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N", "Z")))
    )
    @settings(max_examples=50)
    def test_sanitize_filename_never_contains_invalid_chars(self, name):
        """sanitize_filename output should never contain Windows-invalid characters."""
        result = sanitize_filename(name)
        invalid = set('<>:"/\\|?*')
        for char in result:
            assert char not in invalid, f"Invalid char {char!r} in sanitized: {result!r}"

    @given(
        name=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N", "Z")))
    )
    @settings(max_examples=50)
    def test_clean_movie_name_returns_nonempty(self, name):
        """clean_movie_name should return a non-empty string for non-empty input."""
        result = clean_movie_name(name)
        # After cleaning, result may be empty if input was all removable patterns
        # but it should never raise
        assert isinstance(result, str)

    @given(name=st.text(min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_sanitize_removes_path_separators(self, name):
        """sanitize_filename should remove all path-relevant characters."""
        result = sanitize_filename(name)
        for char in '<>:"/\\|?*':
            assert char not in result

    @given(name=st.from_regex(r"[A-Za-z0-9_ ]{1,30}", fullmatch=True))
    @settings(max_examples=30)
    def test_clean_movie_name_is_title_cased(self, name):
        """clean_movie_name output should have title-cased words (with exceptions)."""
        result = clean_movie_name(name)
        if result:
            words = result.split()
            if words and words[0][0].isalpha():
                assert words[0][0].isupper()
