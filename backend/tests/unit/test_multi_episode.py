"""Unit tests for positional vote-run decomposition.

Root cause these guard against: a DVD track holding two ~11-minute cartoon segments
matched no single TMDB runtime and was filed as an extra without ever being
transcribed (issue #622). Duration cannot distinguish a conjoined 3-episode track
from a 3-episode Play All, but the POSITION of each chunk vote can: conjoined
episodes produce contiguous, balanced runs along the timeline, and the chunk
straddling the seam abstains (see select_chunk_vote's rank+margin rule).

CI-safe: pure functions over synthetic vote lists. No MKV/audio/Whisper.
"""

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
