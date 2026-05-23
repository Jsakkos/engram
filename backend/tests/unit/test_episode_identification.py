"""Unit tests for episode_identification pure/targeted logic.

Targets only logic exercisable without real MKV/audio files: subtitle text
cleaning, watermark filtering, confidence calibration, TF-IDF match scoring,
ranked-voting aggregation, SRT parsing, and precomputed-cache load/fallback.
The ASR (faster-whisper) and ffmpeg subprocess paths are NOT exercised here.
"""

import json

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from app.matcher import episode_identification as ei
from app.matcher.episode_identification import (
    MatchCoverage,
    SubtitleCache,
    SubtitleReader,
    TfidfMatcher,
    _attach_calibrated_confidence,
    _clamp01,
    _clean_subtitle_text,
    _is_watermark_block,
    calibrate_confidence,
    load_precomputed_manifest,
    precomputed_covers_season,
    precomputed_episode_codes,
)

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello there, <i>friend</i>.

2
00:00:05,000 --> 00:00:08,000
[door creaks]
W-w-what was that?

3
00:00:10,000 --> 00:00:14,000
www.tvsubtitles.net
"""


@pytest.mark.unit
class TestCleanSubtitleText:
    def test_lowercases_and_strips(self):
        assert _clean_subtitle_text("  Hello WORLD  ") == "hello world"

    def test_removes_tags_and_brackets(self):
        assert _clean_subtitle_text("Hi <i>there</i> [music]") == "hi there"

    def test_collapses_stutters(self):
        # "I-I think" -> the "I-I" stutter collapses to a single "i".
        assert _clean_subtitle_text("I-I think") == "i think"

    def test_keeps_apostrophes(self):
        assert _clean_subtitle_text("don't stop") == "don't stop"

    def test_normalizes_whitespace(self):
        assert _clean_subtitle_text("a\n\n  b\t c") == "a b c"


@pytest.mark.unit
class TestIsWatermarkBlock:
    def test_detects_url(self):
        assert _is_watermark_block("www.opensubtitles.org", [], 50.0) is True

    def test_detects_domain(self):
        assert _is_watermark_block("Visit subscene.com", [], 50.0) is True

    def test_detects_credit_line_near_start(self):
        assert _is_watermark_block("sync by someone", [], 1.0) is True

    def test_credit_line_ignored_when_late(self):
        # Credit patterns only count near timestamp 0 (< 5s).
        assert _is_watermark_block("sync by someone", [], 120.0) is False

    def test_normal_dialogue_not_watermark(self):
        assert _is_watermark_block("Hello, how are you today?", [], 60.0) is False


@pytest.mark.unit
class TestClamp01:
    def test_within_range(self):
        assert _clamp01(0.5) == 0.5

    def test_below_zero(self):
        assert _clamp01(-3.0) == 0.0

    def test_above_one(self):
        assert _clamp01(2.5) == 1.0


@pytest.mark.unit
class TestCalibrateConfidence:
    def test_decisive_strong_match_high_confidence(self):
        conf, comp = calibrate_confidence(
            score=0.20,
            score_gap=0.18,
            vote_count=8,
            target_votes=10,
            processed_coverage=0.20,
        )
        assert 0.0 <= conf <= 1.0
        assert conf > 0.7  # decisive, strong evidence
        assert comp["separation"] == pytest.approx(0.18 / 0.20)
        assert comp["consensus"] == pytest.approx(0.8)

    def test_near_tie_craters_confidence(self):
        """A small score_gap (two plausible episodes) gates confidence low."""
        conf, comp = calibrate_confidence(
            score=0.20,
            score_gap=0.01,
            vote_count=8,
            target_votes=10,
            processed_coverage=0.20,
        )
        assert conf < 0.2
        assert comp["separation"] == pytest.approx(0.05)

    def test_zero_score_yields_zero_separation(self):
        conf, comp = calibrate_confidence(
            score=0.0,
            score_gap=0.0,
            vote_count=0,
            target_votes=10,
            processed_coverage=0.0,
        )
        assert comp["separation"] == 0.0
        assert conf == 0.0

    def test_zero_target_votes_no_consensus(self):
        _, comp = calibrate_confidence(
            score=0.2,
            score_gap=0.1,
            vote_count=0,
            target_votes=0,
            processed_coverage=0.1,
        )
        assert comp["consensus"] == 0.0

    def test_components_clamped_to_unit_range(self):
        _, comp = calibrate_confidence(
            score=1.0,
            score_gap=2.0,  # gap > score -> separation clamps to 1.0
            vote_count=100,
            target_votes=10,  # consensus clamps to 1.0
            processed_coverage=1.0,
        )
        assert comp["separation"] == 1.0
        assert comp["consensus"] == 1.0
        assert comp["coverage"] == 1.0


@pytest.mark.unit
class TestAttachCalibratedConfidence:
    def test_no_results_summary_is_noop(self):
        best = {"score": 0.5}
        _attach_calibrated_confidence(best, [], video_duration=1000.0)
        assert "confidence" not in best

    def test_attaches_confidence_and_runner_ups(self):
        best = {
            "score": 0.20,
            "match_details": {"target_votes": 10, "vote_count": 8},
        }
        results_summary = [
            {"episode": 1, "score": 0.20, "vote_count": 8},
            {"episode": 2, "score": 0.05, "vote_count": 2},
        ]
        _attach_calibrated_confidence(best, results_summary, video_duration=1300.0)

        assert "confidence" in best
        assert 0.0 <= best["confidence"] <= 1.0
        # score_gap = 0.20 - 0.05
        assert best["match_details"]["score_gap"] == pytest.approx(0.15)
        # Raw score preserved (gate/conflict resolution depend on it).
        assert best["score"] == 0.20
        # Runner-ups carry both raw score and calibrated confidence.
        assert len(best["runner_ups"]) == 2
        assert best["runner_ups"][0]["score"] == 0.20
        assert "confidence" in best["runner_ups"][1]

    def test_runner_ups_drops_zero_score_entries(self):
        best = {"score": 0.20, "match_details": {"target_votes": 10, "vote_count": 8}}
        results_summary = [
            {"episode": 1, "score": 0.20, "vote_count": 8},
            {"episode": 2, "score": 0.0, "vote_count": 0},
        ]
        _attach_calibrated_confidence(best, results_summary, video_duration=1300.0)
        assert all(ru["score"] > 0 for ru in best["runner_ups"])

    def test_runner_ups_capped_at_five(self):
        best = {"score": 0.20, "match_details": {"target_votes": 10, "vote_count": 8}}
        results_summary = [
            {"episode": i, "score": 0.20 - i * 0.01, "vote_count": 8} for i in range(8)
        ]
        _attach_calibrated_confidence(best, results_summary, video_duration=1300.0)
        assert len(best["runner_ups"]) == 5

    def test_match_details_runner_ups_is_distinct_list(self):
        best = {"score": 0.20, "match_details": {"target_votes": 10, "vote_count": 8}}
        results_summary = [{"episode": 1, "score": 0.20, "vote_count": 8}]
        _attach_calibrated_confidence(best, results_summary, video_duration=1300.0)
        # Distinct list objects so a mutation of one cannot corrupt the other.
        assert best["runner_ups"] is not best["match_details"]["runner_ups"]
        assert best["runner_ups"] == best["match_details"]["runner_ups"]


@pytest.mark.unit
class TestTfidfMatcher:
    def _prepared_matcher(self):
        cache = SubtitleCache()
        # Inject pre-cleaned full texts directly so we avoid file I/O.
        cache._full_text_cache = {
            "ep1": "the quick brown fox jumps",
            "ep2": "a slow green turtle swims",
        }
        matcher = TfidfMatcher()
        matcher.prepare(["ep1", "ep2"], cache)
        return matcher

    def test_match_before_prepare_raises(self):
        with pytest.raises(RuntimeError):
            TfidfMatcher().match("anything")

    def test_is_prepared_flag(self):
        matcher = self._prepared_matcher()
        assert matcher.is_prepared is True

    def test_match_returns_sorted_scores(self):
        matcher = self._prepared_matcher()
        results = matcher.match("the quick brown fox")

        assert len(results) == 2
        # Best match is ep1 (closest text), scores sorted descending.
        assert results[0][0] == "ep1"
        assert results[0][1] >= results[1][1]
        assert results[0][1] > 0.0

    def test_load_precomputed_sets_state(self):
        matcher = TfidfMatcher()
        ref = csr_matrix(np.eye(2))
        matcher.load_precomputed(ref, ["S01E01", "S01E02"], np.ones(2))

        assert matcher.is_prepared is True
        assert matcher._precomputed is True
        assert matcher.ref_file_order == ["S01E01", "S01E02"]


@pytest.mark.unit
class TestMatchCoverage:
    def test_empty_coverage_zero(self):
        mc = MatchCoverage("S01E01", reference_duration=1300.0, video_duration=1300.0)
        assert mc.avg_confidence == 0.0
        assert mc.file_coverage == 0.0
        assert mc.ranked_voting_score == 0.0

    def test_avg_confidence(self):
        mc = MatchCoverage("S01E01", 1300.0, 1300.0)
        mc.add_match(0, 30, 0.6)
        mc.add_match(60, 30, 0.8)
        assert mc.avg_confidence == pytest.approx(0.7)

    def test_file_coverage_capped_at_one(self):
        mc = MatchCoverage("S01E01", 100.0, 100.0)
        mc.add_match(0, 80, 0.9)
        mc.add_match(80, 80, 0.9)  # total 160 > 100 -> capped
        assert mc.file_coverage == 1.0

    def test_ranked_voting_score_weighted(self):
        mc = MatchCoverage("S01E01", 1000.0, 1000.0)
        # Equal-duration chunks -> ranked score equals plain mean of confidences.
        mc.add_match(0, 50, 0.6)
        mc.add_match(100, 50, 1.0)
        assert mc.ranked_voting_score == pytest.approx(0.8)

    def test_ranked_voting_zero_duration_guard(self):
        mc = MatchCoverage("S01E01", 1000.0, 0.0)
        mc.add_match(0, 50, 0.9)
        assert mc.ranked_voting_score == 0.0

    def test_voting_details_structure(self):
        mc = MatchCoverage("S01E03", 1300.0, 1300.0)
        mc.add_match(0, 30, 0.7)
        details = mc.get_voting_details()
        assert details["episode"] == "S01E03"
        assert details["vote_count"] == 1
        assert "ranked_score" in details


@pytest.mark.unit
class TestSubtitleReader:
    def test_parse_timestamp(self):
        assert SubtitleReader.parse_timestamp("00:01:30,500") == pytest.approx(90.5)

    def test_extract_chunk_in_window(self):
        lines = SubtitleReader.extract_subtitle_chunk(SAMPLE_SRT, 0, 4)
        assert any("Hello there" in line for line in lines)

    def test_extract_chunk_filters_watermark(self):
        # Block 3 is a URL -> filtered out even though in the time window.
        lines = SubtitleReader.extract_subtitle_chunk(SAMPLE_SRT, 0, 999)
        assert not any("tvsubtitles" in line for line in lines)

    def test_get_duration_uses_max_end(self):
        # Last real subtitle ends at 14s.
        assert SubtitleReader.get_duration(SAMPLE_SRT) == pytest.approx(14.0)

    def test_get_duration_empty(self):
        assert SubtitleReader.get_duration("") == 0.0


@pytest.mark.unit
class TestSubtitleCacheFullText:
    def test_get_full_text_caches_and_cleans(self):
        cache = SubtitleCache()
        cache.subtitles = {"ep.srt": SAMPLE_SRT}
        text = cache.get_full_text("ep.srt")

        assert "hello there" in text
        assert "tvsubtitles" not in text  # watermark filtered
        # Cached on second call (same object identity not required, value stable).
        assert cache.get_full_text("ep.srt") == text

    def test_get_full_text_empty_content(self):
        cache = SubtitleCache()
        cache.subtitles = {"empty.srt": ""}
        assert cache.get_full_text("empty.srt") == ""


@pytest.mark.unit
class TestPrecomputedCache:
    """Manifest load + season-coverage gate with on-disk fixtures."""

    def _write_manifest(self, cache_dir, *, valid=True, shows=None):
        from app.matcher.vectorizer_config import (
            CACHE_FORMAT_VERSION,
            vectorizer_config_hash,
        )

        precomputed = cache_dir / "precomputed"
        precomputed.mkdir(parents=True, exist_ok=True)
        manifest = {
            "cache_format_version": CACHE_FORMAT_VERSION if valid else "BOGUS",
            "vectorizer_config_hash": vectorizer_config_hash(),
            "shows": shows or {},
        }
        (precomputed / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_missing_manifest_returns_none(self, tmp_path):
        assert load_precomputed_manifest(tmp_path) is None

    def test_unreadable_manifest_returns_none(self, tmp_path):
        precomputed = tmp_path / "precomputed"
        precomputed.mkdir()
        (precomputed / "manifest.json").write_text("{not json", encoding="utf-8")
        assert load_precomputed_manifest(tmp_path) is None

    def test_version_mismatch_returns_none(self, tmp_path):
        self._write_manifest(tmp_path, valid=False)
        assert load_precomputed_manifest(tmp_path) is None

    def test_valid_manifest_loads(self, tmp_path):
        self._write_manifest(tmp_path, valid=True, shows={"Show": {"seasons": [1]}})
        manifest = load_precomputed_manifest(tmp_path)
        assert manifest is not None
        assert "Show" in manifest["shows"]

    def test_covers_season_false_without_files(self, tmp_path):
        # Manifest lists the season but the .npz/.index.json files are missing.
        self._write_manifest(tmp_path, shows={"Show": {"seasons": [1]}})
        assert precomputed_covers_season(tmp_path, "Show", 1) is False

    def test_covers_season_true_with_files(self, tmp_path):
        from app.matcher.subtitle_utils import sanitize_filename

        self._write_manifest(tmp_path, shows={"Show": {"seasons": [1]}})
        show_dir = tmp_path / "precomputed" / sanitize_filename("Show")
        show_dir.mkdir(parents=True, exist_ok=True)
        (show_dir / "S01.npz").write_bytes(b"x")
        (show_dir / "S01.index.json").write_text(json.dumps(["S01E01"]), encoding="utf-8")

        assert precomputed_covers_season(tmp_path, "Show", 1) is True
        assert precomputed_episode_codes(tmp_path, "Show", 1) == ["S01E01"]

    def test_episode_codes_none_when_uncovered(self, tmp_path):
        self._write_manifest(tmp_path, shows={})
        assert precomputed_episode_codes(tmp_path, "Show", 1) is None

    def test_covers_season_unknown_show(self, tmp_path):
        self._write_manifest(tmp_path, shows={"Other": {"seasons": [1]}})
        assert precomputed_covers_season(tmp_path, "Show", 1) is False


@pytest.mark.unit
class TestModuleHelpers:
    def test_detect_file_encoding_handles_error(self, tmp_path):
        # Nonexistent file -> safe utf-8 fallback (no raise).
        assert ei.detect_file_encoding(tmp_path / "nope.srt") == "utf-8"

    def test_read_file_with_fallback(self, tmp_path):
        f = tmp_path / "sub.srt"
        f.write_text("héllo", encoding="utf-8")
        assert "llo" in ei.read_file_with_fallback(str(f))
