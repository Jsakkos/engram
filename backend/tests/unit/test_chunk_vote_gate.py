"""Unit tests for the rank+margin chunk-vote gate and full-file fallback reach.

Root cause these guard against: a 30s ASR chunk compared against a full-episode
TF-IDF vector scores a structurally low absolute cosine (~0.08-0.22) even for a
perfect match, because both vectors are L2-normalized and the chunk is far
sparser. The old absolute `score > 0.15` gate therefore rejected most correct
chunks, returning episode=None. The ranking, however, is reliable (correct
episode leads the runner-up by ~1.8-5.6x), so we vote on rank+margin instead.

CI-safe: no MKV/audio/Whisper; the ffmpeg + faster-whisper paths are stubbed.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.matcher import episode_identification as ei
from app.matcher.episode_identification import EpisodeMatcher, select_chunk_vote
from app.matcher.vectorizer_config import apply_tfidf, build_hashing_vectorizer


@pytest.mark.unit
class TestSelectChunkVote:
    def test_low_absolute_but_clear_margin_votes(self):
        # The regression: 0.10 is BELOW the old 0.15 absolute gate, but it leads
        # the runner-up (0.04) by 2.5x — a confident chunk that must vote.
        vote = select_chunk_vote([("S01E01", 0.10), ("S01E02", 0.04)], floor=0.06, ratio=1.8)
        assert vote == ("S01E01", 0.10)

    def test_ambiguous_top_two_no_vote(self):
        # Two episodes nearly tied (0.12 vs 0.11): no clear winner -> abstain,
        # even though both clear the floor. Prevents recap/shared-dialogue chunks
        # from casting a confident-but-wrong vote.
        assert select_chunk_vote([("a", 0.12), ("b", 0.11)], floor=0.06, ratio=1.8) is None

    def test_below_floor_no_vote(self):
        # Top candidate clears the ratio (0.05 vs 0.0) but is below the noise floor.
        assert select_chunk_vote([("a", 0.05)], floor=0.06, ratio=1.8) is None

    def test_empty_results_no_vote(self):
        assert select_chunk_vote([], floor=0.06, ratio=1.8) is None

    def test_single_candidate_votes_when_above_floor(self):
        # Only one reference episode in the season: no runner-up to out-margin,
        # so clearing the floor is sufficient.
        assert select_chunk_vote([("only", 0.09)], floor=0.06, ratio=1.8) == ("only", 0.09)

    def test_just_below_ratio_abstains(self):
        # 0.17 vs 0.10 is a 1.7x lead, under the 1.8x bar -> not decisive enough.
        assert select_chunk_vote([("a", 0.17), ("b", 0.10)], floor=0.06, ratio=1.8) is None

    def test_clearly_above_ratio_votes(self):
        # 0.20 vs 0.10 is a 2.0x lead, comfortably over the 1.8x bar.
        assert select_chunk_vote([("a", 0.20), ("b", 0.10)], floor=0.06, ratio=1.8) == ("a", 0.20)


@pytest.mark.unit
class TestFallbackReachWhenNoVotes:
    """When zero chunks clear the vote gate, identify_episode must still reach the
    full-file fallback instead of returning episode=None (the early-return bug
    that made the fallback dead code in the total-miss case)."""

    def _refs_in_hashed_space(self, texts):
        counts = build_hashing_vectorizer().transform(texts)
        idf = np.ones(counts.shape[1], dtype=np.float32)
        return apply_tfidf(counts, idf), idf

    def test_no_votes_reaches_full_file_fallback(self, tmp_path):
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Demo", device="cpu")

        ref_matrix, idf = self._refs_in_hashed_space(["alpha beta", "gamma delta", "epsilon zeta"])
        codes = ["S01E01", "S01E02", "S01E03"]

        fallback_result = {
            "season": 1,
            "episode": 2,
            "confidence": 0.83,
            "reference_file": "S01E02",
            "matched_at": 0,
            "method": "full_transcription",
        }

        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": "this transcription wins no vote at all"}

        with (
            patch.object(
                matcher, "_load_precomputed_season", return_value=(ref_matrix, codes, idf)
            ),
            patch.object(ei, "get_video_duration", return_value=1800.0),
            patch.object(ei, "get_cached_model", return_value=fake_model),
            patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "c.wav")),
            # Force the total-miss condition deterministically (audio/ASR are
            # unavoidable to stub; this isolates the control-flow fix).
            patch.object(ei, "select_chunk_vote", return_value=None),
            patch.object(matcher, "_match_full_file", return_value=dict(fallback_result)) as mff,
        ):
            result = matcher.identify_episode(tmp_path / "demo.mkv", tmp_path, season_number=1)

        mff.assert_called_once()
        assert result is not None
        assert result["episode"] == 2
        assert result["match_details"]["method"] == "full_transcription"

    def test_total_miss_without_fallback_returns_stats_not_none(self, tmp_path):
        # When even the fallback finds nothing, the result still carries scan
        # stats (not a bare None) so the UI/diagnostics show what was attempted.
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Demo", device="cpu")
        ref_matrix, idf = self._refs_in_hashed_space(["alpha beta", "gamma delta"])
        codes = ["S01E01", "S01E02"]
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": "no vote here whatsoever friend"}

        with (
            patch.object(
                matcher, "_load_precomputed_season", return_value=(ref_matrix, codes, idf)
            ),
            patch.object(ei, "get_video_duration", return_value=1800.0),
            patch.object(ei, "get_cached_model", return_value=fake_model),
            patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "c.wav")),
            patch.object(ei, "select_chunk_vote", return_value=None),
            patch.object(matcher, "_match_full_file", return_value=None),
        ):
            result = matcher.identify_episode(tmp_path / "demo.mkv", tmp_path, season_number=1)

        assert result is not None
        assert result["episode"] is None
        assert "total_chunks" in result["match_details"]
