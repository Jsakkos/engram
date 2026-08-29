"""Unit tests for positional vote-run decomposition.

Root cause these guard against: a DVD track holding two ~11-minute cartoon segments
matched no single TMDB runtime and was filed as an extra without ever being
transcribed (issue #622). Duration cannot distinguish a conjoined 3-episode track
from a 3-episode Play All, but the POSITION of each chunk vote can: conjoined
episodes produce contiguous, balanced runs along the timeline, and the chunk
straddling the seam abstains (see select_chunk_vote's rank+margin rule).

CI-safe: pure functions over synthetic vote lists. No MKV/audio/Whisper.
"""

import json

import pytest

from app.matcher.multi_episode import decompose_vote_runs


def _votes(*spans: tuple[str, list[float]]) -> list[tuple[float, str]]:
    """Build a (start, code) vote list from (code, [timestamps]) pairs."""
    return [(t, code) for code, times in spans for t in times]


@pytest.mark.unit
class TestDecomposeVoteRuns:
    def test_single_episode_is_not_multi(self):
        v = _votes(("S01E01", [120, 260, 400, 540, 680, 820, 960, 1100, 1240, 1330]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "single_episode"
        assert verdict.codes == ("S01E01",)

    def test_conjoined_pair_with_abstained_seam(self):
        # The bug case: two 11-min segments in a 23-min track. The chunk on the
        # seam (~690s) abstains, so it simply does not appear in the vote list.
        v = _votes(
            ("S01E01", [120, 250, 380, 510, 640]),
            ("S01E02", [820, 950, 1080, 1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=11)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02")
        assert verdict.reason == "contiguous_runs"

    def test_runs_are_ordered_by_position_not_episode_number(self):
        # A disc may pair segments in an order TMDB does not number consecutively.
        # Report them in PLAYBACK order so downstream naming reflects the file.
        v = _votes(
            ("S01E04", [120, 250, 380, 510, 640]),
            ("S01E03", [820, 950, 1080, 1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=11)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E04", "S01E03")

    def test_noise_singletons_are_dropped(self):
        # Two spurious one-off votes inside an otherwise solid single episode.
        v = _votes(
            ("S01E01", [120, 250, 510, 640, 820, 1080, 1210, 1330]),
            ("S01E07", [380]),
            ("S01E13", [950]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "single_episode"
        assert verdict.codes == ("S01E01",)

    def test_interleaved_runs_are_rejected(self):
        # Two codes each clear the vote minimum but their spans overlap: this is
        # ASR confusion between similar episodes, not two episodes in one file.
        v = _votes(
            ("S01E01", [120, 640, 1210]),
            ("S01E02", [380, 950, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "interleaved_runs"

    def test_sparse_coverage_is_rejected(self):
        # One 11-min episode plus 12 minutes of un-matchable bonus content: the
        # runs account for only 4 of 10 scan points, so something unexplained
        # occupies the file. Duration arithmetic cannot catch this case.
        v = _votes(("S01E01", [120, 250]), ("S01E02", [380, 510]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "sparse_coverage"

    def test_unbalanced_runs_are_rejected(self):
        # E02 holds 2 of 9 votes against E01's 7, a "next time on" preview
        # bleeding into the tail, not a genuine second episode.
        v = _votes(
            ("S01E01", [120, 250, 380, 510, 640, 770, 900]),
            ("S01E02", [1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_three_conjoined_segments(self):
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [700, 830, 960, 1090]),
            ("S01E03", [1280, 1410, 1540, 1670]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=14)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02", "S01E03")

    def test_recap_at_head_is_not_a_second_episode(self):
        # A "previously on" recap wins two votes for the PRIOR episode at the head.
        # By vote count it is indistinguishable from a real segment (2 vs 6 clears
        # any mean-relative balance rule); by TERRITORY it owns a sliver, so it is
        # rejected. This is the false positive a vote-count balance check admits.
        v = _votes(
            ("S01E04", [120, 250]),
            ("S01E05", [380, 510, 640, 770, 900, 1030]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_recap_and_preview_are_not_three_episodes(self):
        # Recap at the head plus a "next time on" preview at the tail around one
        # real episode. A mean-relative balance rule can NEVER fire for 3 runs at
        # 10 scan points (its threshold falls below MIN_RUN_VOTES), so this case
        # pins the territory rule specifically.
        v = _votes(
            ("S01E04", [120, 250]),
            ("S01E05", [380, 510, 640, 770]),
            ("S01E06", [1210, 1330]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "unbalanced_runs"

    def test_low_vote_yield_pair_is_still_accepted(self):
        # Near-wordless cartoons are both the target content AND the lowest
        # vote-yield content, so the coverage floor must not reject them. Exactly
        # at MIN_TIMELINE_COVERAGE (5 of 10): accepted, since the bound is strict.
        v = _votes(("S01E01", [120, 250, 380]), ("S01E02", [820, 950]))
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is True
        assert verdict.reason == "contiguous_runs"

    def test_more_votes_than_scan_points_is_refused(self):
        # A caller that double-counts a chunk across two coverages would otherwise
        # push coverage above 1.0 and silently STRENGTHEN the verdict. Refuse.
        v = _votes(("S01E01", [1, 2, 3]), ("S01E02", [4, 5, 6]))
        verdict = decompose_vote_runs(v, total_scan_points=4)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "invalid_input"

    def test_conjoined_pair_at_default_scan_depth(self):
        # canonical_scan_points snaps to the lattice (10/19/37/73/145), so 10 is
        # the depth this actually runs at. The other pair tests use 11, which no
        # real scan produces.
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [820, 950, 1080, 1210]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=10)
        assert verdict.is_multi_episode is True

    def test_three_segments_at_realistic_scan_depth(self):
        # 12 votes is impossible at 10 scan points; 19 is the next lattice level.
        v = _votes(
            ("S01E01", [120, 250, 380, 510]),
            ("S01E02", [700, 830, 960, 1090]),
            ("S01E03", [1280, 1410, 1540, 1670]),
        )
        verdict = decompose_vote_runs(v, total_scan_points=19)
        assert verdict.is_multi_episode is True

    def test_to_dict_is_json_safe_and_playback_ordered(self):
        # to_dict lands in DiscTitle.match_details and is read back by the UI.
        v = _votes(
            ("S01E04", [120, 250, 380, 510]),
            ("S01E03", [820, 950, 1080, 1210]),
        )
        d = decompose_vote_runs(v, total_scan_points=10).to_dict()
        assert json.loads(json.dumps(d)) == d
        assert d["is_multi_episode"] is True
        assert d["codes"] == ["S01E04", "S01E03"]
        assert [r["code"] for r in d["runs"]] == ["S01E04", "S01E03"]
        assert d["runs"][0]["votes"] == 4

    def test_no_votes_is_not_multi(self):
        verdict = decompose_vote_runs([], total_scan_points=10)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "no_votes"
        assert verdict.codes == ()

    def test_zero_scan_points_is_not_multi(self):
        # Guards the coverage division; a caller with no scan points has no evidence.
        verdict = decompose_vote_runs(_votes(("S01E01", [1, 2])), total_scan_points=0)
        assert verdict.is_multi_episode is False
        assert verdict.reason == "no_votes"
