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
    MULTI_EPISODE_ERROR_CODE,
    _apply_multi_episode_review,
    _combined_code,
    _may_contribute_fingerprint,
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

    def test_crosswalked_code_with_wrong_part_count_still_parks(self):
        title = _title("S01E01-E02", numbering_crosswalk=True)
        assert _apply_multi_episode_review(title, conjoined_hint=3) is True
        assert title.state == TitleState.REVIEW
        assert json.loads(title.match_details)["error"] == MULTI_EPISODE_ERROR_CODE

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
