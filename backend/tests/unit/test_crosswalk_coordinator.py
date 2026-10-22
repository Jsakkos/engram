"""Coordinator handling of results matched through a half-hour crosswalk (#760).

A crosswalked reference already names every TMDB segment its half-hour holds
("S01E01-E03"), so a conjoined-runtime hint on such a track is explained by the
result rather than contradicted by it.
"""

import json
from types import SimpleNamespace

import pytest

from app.models.disc_job import TitleState
from app.services.matching_coordinator import (
    _NON_REMATCHABLE_REVIEW_ERRORS,
    MULTI_EPISODE_ERROR_CODE,
    NUMBERING_DIVERGENT_ERROR_CODE,
    _apply_multi_episode_review,
    _combined_code,
    _may_contribute_fingerprint,
    _route_unconfirmable_title,
)


def _title(matched: str, **details):
    base = {
        "multi_episode": {"is_multi_episode": False, "reason": "single_episode", "codes": []},
        "numbering_scheme": "tmdb_aired",
    }
    return SimpleNamespace(
        id=1,
        state=TitleState.MATCHED,
        matched_episode=matched,
        match_details=json.dumps({**base, **details}),
        match_source="engram",
    )


@pytest.mark.unit
class TestCrosswalkExplainsTheHint:
    def test_crosswalked_combined_code_explains_matching_hint(self):
        title = _title("S01E01-E03", numbering_crosswalk=True)
        assert _apply_multi_episode_review(title, conjoined_hint=3) is False
        assert title.state == TitleState.MATCHED
        assert "error" not in json.loads(title.match_details)

    def test_crosswalked_code_shorter_than_the_hint_still_parks(self):
        # The track runs longer than the half-hour its code covers.
        title = _title("S01E01-E02", numbering_crosswalk=True)
        assert _apply_multi_episode_review(title, conjoined_hint=3) is True
        assert title.state == TitleState.REVIEW
        assert json.loads(title.match_details)["error"] == MULTI_EPISODE_ERROR_CODE

    def test_hint_is_a_lower_bound_so_more_parts_explain_it(self):
        # Dexter's Laboratory: a 22-minute track against 7-minute segments gets
        # the smallest fitting hint (2), while its half-hour holds 3 segments.
        title = _title("S01E01-E03", numbering_crosswalk=True)
        assert _apply_multi_episode_review(title, conjoined_hint=2) is False
        assert title.state == TitleState.MATCHED

    def test_combined_code_without_crosswalk_still_parks(self):
        title = _title("S01E01-E03")
        assert _apply_multi_episode_review(title, conjoined_hint=3) is True
        assert title.state == TitleState.REVIEW

    def test_crosswalked_single_code_with_a_hint_still_parks(self):
        title = _title("S01E13", numbering_crosswalk=True)
        assert _apply_multi_episode_review(title, conjoined_hint=2) is True


@pytest.mark.unit
class TestCombinedCodeFlattening:
    def test_combined_code_flattens_combined_parts(self):
        assert _combined_code(["S02E11-E12", "S02E13"]) == "S02E11-E13"

    def test_combined_parts_across_seasons_have_no_combined_code(self):
        assert _combined_code(["S02E11-E12", "S03E01"]) is None

    def test_single_codes_unchanged(self):
        assert _combined_code(["S01E01", "S01E02"]) == "S01E01-E02"
        assert _combined_code(["S01E01"]) is None


@pytest.mark.unit
def test_crosswalked_two_segment_track_is_not_contributed():
    details = {"numbering_crosswalk": True, "multi_episode": {"is_multi_episode": False}}
    assert _may_contribute_fingerprint("S02E11-E12", None, details) is False
    assert _may_contribute_fingerprint("S02E13", None, details) is True


@pytest.mark.unit
class TestDivergentWithoutCrosswalkIsHeld:
    """A half-hour-numbered match with no crosswalk is held for review, not filed.

    Its code names a half-hour of the pack's own numbering, which TMDB uses for an
    unrelated segment. Filing it was the job 100 outcome: five wrong files, all at
    high confidence.
    """

    def _divergent(self, matched="S02E06", state=TitleState.MATCHED, **details):
        title = _title(
            matched,
            numbering_scheme="divergent",
            numbering_segmented=True,
            pack_roster_size=37,
            **details,
        )
        title.state = state
        return title

    def test_divergent_uncrosswalked_match_is_parked(self):
        title = self._divergent()
        assert _route_unconfirmable_title(title, None) == NUMBERING_DIVERGENT_ERROR_CODE
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S02E06"  # kept as the reviewer's pre-fill
        details = json.loads(title.match_details)
        assert details["error"] == NUMBERING_DIVERGENT_ERROR_CODE
        assert details["message"] == (
            "The reference subtitles for this season are numbered differently from TMDB "
            "(usually by broadcast half-hour instead of by segment), so S02E06 may name "
            "the wrong episode. Check it and assign the TMDB episode(s) this track holds."
        )

    def test_low_confidence_divergent_match_gets_the_notice_too(self):
        title = self._divergent(state=TitleState.REVIEW)
        assert _route_unconfirmable_title(title, None) == NUMBERING_DIVERGENT_ERROR_CODE
        assert json.loads(title.match_details)["error"] == NUMBERING_DIVERGENT_ERROR_CODE

    def test_crosswalked_match_is_not_parked(self):
        title = _title("S02E11-E12", numbering_crosswalk=True, pack_numbering_scheme="divergent")
        assert _route_unconfirmable_title(title, None) is None
        assert title.state == TitleState.MATCHED

    def test_tmdb_aired_match_is_not_parked(self):
        title = _title("S02E06")
        assert _route_unconfirmable_title(title, None) is None
        assert title.state == TitleState.MATCHED

    def test_count_heuristic_alone_does_not_park(self):
        # A scraped corpus that is merely incomplete also has fewer references
        # than the roster; only the pack's explicit marker may hold a match.
        title = _title("S02E06", numbering_scheme=None, reference_count=20, roster_size=37)
        assert _route_unconfirmable_title(title, None) is None

    def test_title_without_an_episode_is_not_touched(self):
        title = self._divergent(matched=None, state=TitleState.REVIEW)
        assert _route_unconfirmable_title(title, None) is None

    def test_an_earlier_routing_error_wins(self):
        title = self._divergent(error="file_exists", message="S02E06 is already organized.")
        title.state = TitleState.REVIEW
        assert _route_unconfirmable_title(title, None) is None
        assert json.loads(title.match_details)["error"] == "file_exists"

    def test_incomplete_ordinary_season_is_not_parked(self):
        # divergent without segmented: a pack merely missing episodes of an
        # ordinary season. Its codes are TMDB coordinates; holding them would
        # bury every disc of the season in review.
        title = _title("S02E06", numbering_scheme="divergent", pack_roster_size=37)
        assert _route_unconfirmable_title(title, None) is None
        assert title.state == TitleState.MATCHED

    def test_held_match_is_not_contributed(self):
        details = {
            "numbering_scheme": "divergent",
            "numbering_segmented": True,
            "multi_episode": {"is_multi_episode": False},
        }
        assert _may_contribute_fingerprint("S02E06", None, details) is False

    def test_incomplete_ordinary_season_match_is_contributed(self):
        details = {"numbering_scheme": "divergent", "multi_episode": {"is_multi_episode": False}}
        assert _may_contribute_fingerprint("S02E06", None, details) is True

    def test_numbering_divergent_is_not_rematchable(self):
        assert NUMBERING_DIVERGENT_ERROR_CODE in _NON_REMATCHABLE_REVIEW_ERRORS


@pytest.mark.unit
def test_confirmed_multi_verdict_in_untranslated_segmented_season_keeps_the_notice():
    # The multi-episode routing takes the error slot first, so its message must
    # carry the numbering warning too: the pre-filled codes are half-hour numbers.
    title = _title(
        "S02E06",
        numbering_scheme="divergent",
        numbering_segmented=True,
        multi_episode={"is_multi_episode": True, "codes": ["S02E06", "S02E07"]},
    )
    assert _route_unconfirmable_title(title, None) == MULTI_EPISODE_ERROR_CODE
    message = json.loads(title.match_details)["message"]
    assert "numbered differently from TMDB" in message
