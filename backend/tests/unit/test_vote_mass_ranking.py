"""A lone stray chunk vote must not outrank or tie a run of votes.

``ranked_voting_score`` is the mean cosine of an episode's votes. The winner used
to be the highest mean, and separation came from the gap between the top two
means, so one vote with a slightly higher cosine tied or beat a run of votes:
19 votes against 1 calibrated to 0.003. Ranking and separation now use the vote
mass, the summed cosine.
"""

from collections import deque
from types import SimpleNamespace

import pytest

import app.matcher.episode_identification as ei
from app.matcher.episode_identification import (
    EpisodeMatcher,
    MatchCoverage,
    _attach_calibrated_confidence,
)


def _attach(rows):
    """Calibrate an (episode, mean cosine, votes) leaderboard; returns match_details."""
    summary = [{"episode": e, "score": s, "vote_count": v, "target_votes": 73} for e, s, v in rows]
    summary.sort(key=lambda r: r["score"] * r["vote_count"], reverse=True)
    best = {"score": summary[0]["score"], "match_details": dict(summary[0])}
    _attach_calibrated_confidence(best, summary, 2754.0)
    return best["match_details"]


@pytest.mark.unit
class TestVoteMassRanking:
    def test_vote_mass_sums_confidences(self):
        cov = MatchCoverage("S01E03", 1800, 1800)
        for start in (100, 200, 300):
            cov.add_match(start, 30, 0.1)
        assert cov.vote_mass == pytest.approx(0.3)
        assert cov.ranked_voting_score == pytest.approx(0.1)

    def test_a_stray_vote_does_not_read_as_a_tie(self):
        md = _attach([("S1E3", 0.1604, 19), ("S1E2", 0.1598, 1)])
        assert md["separation"] > 0.9
        assert md["confidence"] >= ei.CONFIDENCE_ACCEPT_FLOOR

    def test_a_real_tie_still_reads_as_one(self):
        md = _attach([("S1E3", 0.160, 10), ("S1E2", 0.155, 10)])
        assert md["separation"] < 0.05

    def test_more_votes_beat_a_higher_mean(self, tmp_path, monkeypatch):
        e01, e02, e03 = (str(tmp_path / f"Show - S01E0{i}.srt") for i in (1, 2, 3))
        rankings = deque(
            [[(e02, 0.25), (e01, 0.05)]]  # one strong stray vote
            + [[(e03, 0.20), (e01, 0.10)]] * 3  # a run of 2x leads
            + [[(e01, 0.03), (e02, 0.02)]] * 6  # below the vote floor
        )
        tfidf = SimpleNamespace(
            match=lambda text: rankings.popleft(), ref_file_order=[e01, e02, e03]
        )
        m = EpisodeMatcher(cache_dir=tmp_path, show_name="Show", device="cpu")
        m._load_precomputed_season = lambda season: None
        m.get_reference_files = lambda season, **kwargs: [e01, e02, e03]
        m._get_tfidf_matcher = lambda *a, **k: tfidf
        m.transcribe_chunk_cached = lambda video, start, *a, **k: f"transcript at {start}"
        m._match_full_file = lambda *a, **k: None
        monkeypatch.setattr(ei, "get_video_duration", lambda *a, **k: 1800.0)

        result = m.identify_episode(tmp_path / "disc_t00.mkv", tmp_path, season_number=1)
        assert result["episode"] == 3
