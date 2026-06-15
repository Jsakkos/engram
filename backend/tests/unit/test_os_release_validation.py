"""Unit tests for OpenSubtitles release-vs-episode validation.

OpenSubtitles returns DS9 Season 2 subtitles tagged with season_number=1 — the
`release` field reveals the truth ("Episode 6 - Melora" for a request that should
be S01E06 "Q-Less"). Validate the release against the requested episode's TMDB
title and reject the mislabeled ones before they poison the cache.
"""

from app.matcher.testing_service import _release_matches_episode


def test_rejects_wrong_season_title_in_episode_dash_title_release():
    # OS returns S2's "Melora" tagged as S01E06; expected S01E06 is "Q-Less".
    assert _release_matches_episode("Episode 6 - Melora", "Q-Less", season=1) is False


def test_accepts_release_whose_title_matches_expected():
    assert _release_matches_episode("Episode 2 - Past Prologue", "Past Prologue", season=1) is True


def test_rejects_cross_season_sxxexx_in_release():
    assert _release_matches_episode("Star.Trek.DS9.S02E06.Melora.720p", "Q-Less", season=1) is False


def test_accepts_matching_season_sxxexx_release_without_title():
    # Season matches and there's no explicit conflicting title — can't disprove it.
    assert _release_matches_episode("Star.Trek.DS9.S01E06.1080p.BluRay", "Q-Less", season=1) is True


def test_accepts_uninformative_release():
    # A bare hash/filename carries no episode identity — don't over-reject.
    assert _release_matches_episode("a3f9c1d2e8.srt", "Q-Less", season=1) is True


def test_accepts_when_no_release_or_expected_title():
    assert _release_matches_episode("", "Q-Less", season=1) is True
    assert _release_matches_episode("Episode 6 - Melora", "", season=1) is True


def test_title_spelling_variation_is_not_a_conflict():
    # Punctuation/spacing differences must not trip a false rejection.
    assert _release_matches_episode("Episode 6 - Qless", "Q-Less", season=1) is True
