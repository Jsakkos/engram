"""Unit tests for episode_identification pure/targeted logic.

Targets only logic exercisable without real MKV/audio files: subtitle text
cleaning, watermark filtering, confidence calibration, TF-IDF match scoring,
ranked-voting aggregation, SRT parsing, and precomputed-cache load/fallback.
The ASR (faster-whisper) and ffmpeg subprocess paths are NOT exercised here.
"""

import contextlib
import json
from unittest.mock import MagicMock, patch

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

    def test_strips_mismatched_open_brace_annotation(self):
        # Some sources (e.g. tvsubtitles.net) render the opening "[" of a stage
        # direction as "{", producing tokens like "{ Sighs]". The annotation must
        # still be stripped despite the mismatched delimiters.
        assert _clean_subtitle_text("{ Sighs] Hello there") == "hello there"

    def test_leaves_unclosed_annotation_words(self):
        # No closing bracket -> cannot be safely stripped by regex (would need a
        # sound-effect wordlist), so the words survive; only the stray brace goes.
        assert _clean_subtitle_text("{ Scoffs I haven't slept") == "scoffs i haven't slept"


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

    def test_low_raw_score_decisive_match_clears_accept_floor(self):
        """A structurally-low raw score (chunk-vs-full-episode cosine ~0.1) that is
        decisive (score_gap ~= score, i.e. no real runner-up) with solid votes must
        still calibrate ABOVE the 0.70 accept floor. This is the real reason the
        ranked-voting gate consults calibrated confidence: it rescues correct
        matches like True Detective S1E5 (0.808) / S1E7 (0.712) from the fallback.
        Numbers mirror the observed live run.
        """
        s1e5, _ = calibrate_confidence(
            score=0.100, score_gap=0.0996, vote_count=17, target_votes=25, processed_coverage=0.148
        )
        s1e7, _ = calibrate_confidence(
            score=0.094, score_gap=0.0941, vote_count=7, target_votes=10, processed_coverage=0.065
        )
        assert s1e5 >= 0.70
        assert s1e7 >= 0.70

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
        # v3 manifest entries carry a "name" used to resolve a show when no
        # tmdb_id is supplied; inject it from the key for these minimal fixtures.
        shows = {k: ({"name": k, **v}) for k, v in (shows or {}).items()}
        manifest = {
            "cache_format_version": CACHE_FORMAT_VERSION if valid else "BOGUS",
            "vectorizer_config_hash": vectorizer_config_hash(),
            "shows": shows,
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
class TestStaleManifestPruning:
    """When the manifest lists a show/season but the on-disk vector files
    are missing, `_load_precomputed_season` must:

    1. Return None (so the caller falls back to scraping).
    2. Prune the stale season from the in-memory cached manifest so the
       same warning doesn't re-fire for every title in the disc.

    The persistent manifest.json on disk is left untouched — the next
    `ensure_precomputed_cache` run owns that.
    """

    def _write_manifest_with_show(self, cache_dir, shows):
        from app.matcher.vectorizer_config import (
            CACHE_FORMAT_VERSION,
            vectorizer_config_hash,
        )

        precomputed = cache_dir / "precomputed"
        precomputed.mkdir(parents=True, exist_ok=True)
        # v3 manifest entries carry a "name" used to resolve a show when no
        # tmdb_id is supplied; inject it from the key for these minimal fixtures.
        shows = {k: ({"name": k, **v}) for k, v in shows.items()}
        manifest = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "vectorizer_config_hash": vectorizer_config_hash(),
            "shows": shows,
        }
        (precomputed / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _make_matcher(self, cache_dir, show_name):
        # The EpisodeMatcher constructor pulls in ctranslate2, which is heavy
        # but always available in this project's test env. We never actually
        # call identify_episode — only the pure manifest-handling method.
        from app.matcher.episode_identification import EpisodeMatcher

        return EpisodeMatcher(
            cache_dir=cache_dir,
            show_name=show_name,
            model_name="tiny",  # never loaded; we don't call ASR
        )

    def test_missing_files_prunes_season_from_cached_manifest(self, tmp_path):
        self._write_manifest_with_show(
            tmp_path,
            {"Star Trek: The Next Generation": {"tmdb_id": 1, "seasons": [6, 7]}},
        )
        matcher = self._make_matcher(tmp_path, "Star Trek: The Next Generation")
        # First call: S07 listed in manifest, no files on disk.
        assert matcher._load_precomputed_season(7) is None
        # The cached manifest must now report S07 as gone, so a second call
        # (e.g., next title on the same disc) does NOT re-fire the warning.
        manifest = matcher._precomputed_manifest
        seasons = manifest["shows"]["Star Trek: The Next Generation"]["seasons"]
        assert 7 not in seasons
        # S06 was never touched.
        assert 6 in seasons

    def test_missing_files_prunes_show_when_no_seasons_left(self, tmp_path):
        self._write_manifest_with_show(
            tmp_path,
            {"Star Trek: The Next Generation": {"tmdb_id": 1, "seasons": [7]}},
        )
        matcher = self._make_matcher(tmp_path, "Star Trek: The Next Generation")
        matcher._load_precomputed_season(7)
        # When the last season for a show is pruned, drop the show entirely so
        # the in-memory manifest stays internally consistent.
        assert "Star Trek: The Next Generation" not in matcher._precomputed_manifest["shows"]

    def test_repeat_call_does_not_re_warn(self, tmp_path, caplog):
        """The warning must fire at most once per (show, season), even when
        every title on a disc triggers a fresh _load_precomputed_season call.
        """
        import logging

        from loguru import logger as loguru_logger

        self._write_manifest_with_show(
            tmp_path,
            {"Star Trek: The Next Generation": {"tmdb_id": 1, "seasons": [7]}},
        )
        matcher = self._make_matcher(tmp_path, "Star Trek: The Next Generation")

        # Bridge loguru -> caplog so pytest's WARNING capture sees the warning.
        class _Sink:
            def __init__(self):
                self.messages = []

            def __call__(self, message):
                record = message.record
                if record["level"].no >= logging.WARNING:
                    self.messages.append(record["message"])

        sink = _Sink()
        sink_id = loguru_logger.add(sink, level="WARNING")
        try:
            matcher._load_precomputed_season(7)  # first call -> warns
            matcher._load_precomputed_season(7)  # second call -> silent
            matcher._load_precomputed_season(7)  # third call -> silent
        finally:
            loguru_logger.remove(sink_id)

        warnings_about_missing = [m for m in sink.messages if "files are missing" in m]
        assert len(warnings_about_missing) == 1, (
            f"Expected exactly one stale-cache warning, got {len(warnings_about_missing)}: "
            f"{warnings_about_missing}"
        )


@pytest.mark.unit
class TestTfidfMatcherStaleness:
    """The matcher's cached TfidfMatcher must not silently reuse the wrong
    reference set across identify_episode calls.

    Background: when the precomputed cache covers S07, the matcher's
    `ref_file_order` is populated with episode codes ("S07E01"...). If a
    later call falls back to scraping (e.g. precomputed files vanished
    between titles, or different season requested), the new call's
    `coverages` dict is keyed by SRT file paths — but the cached matcher
    still returns episode codes. Hitting `coverages[rf_str]` raises
    KeyError("S07E07"), which the chunk loop swallows as a rejected
    chunk — surfacing as the user's "0 matched / N rejected" symptom
    even when the audio + subtitles are both fine.

    The fix: track a fingerprint of what the matcher was prepared for
    and rebuild when it changes.
    """

    def test_load_precomputed_changes_ref_signature(self):
        ref_matrix = csr_matrix(np.eye(2))
        m = TfidfMatcher()
        m.load_precomputed(ref_matrix, ["S07E01", "S07E02"], np.array([1.0, 1.0]))
        sig1 = m.reference_signature()
        m2 = TfidfMatcher()
        m2.load_precomputed(ref_matrix, ["S08E01", "S08E02"], np.array([1.0, 1.0]))
        sig2 = m2.reference_signature()
        assert sig1 != sig2, "Reference signature must change with reference codes"

    def test_signature_distinguishes_precomputed_from_scraping(self, tmp_path):
        # Same season number, same show, but different MODE: precomputed
        # (codes-keyed) vs scraping (path-keyed). Signature must differ so
        # the cached matcher gets rebuilt.
        ref_matrix = csr_matrix(np.eye(1))
        m_pre = TfidfMatcher()
        m_pre.load_precomputed(ref_matrix, ["S07E01"], np.array([1.0]))

        srt = tmp_path / "show.S07E01.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
        m_scrape = TfidfMatcher()
        m_scrape.prepare([srt], SubtitleCache())
        assert m_pre.reference_signature() != m_scrape.reference_signature()


@pytest.mark.unit
class TestUniqueChunkPaths:
    """Chunk + preprocessed tempfile paths must be unique per source file.

    Two matcher threads (running under max_concurrent_matches >= 2) on the
    same disc samples chunks at the same offsets. If both threads write to
    the same on-disk path, the second writer corrupts the first thread's
    file mid-PyAV-decode -> av.error.InvalidDataError, OR the second reader
    silently picks up the first writer's audio (wrong audio, wrong match).

    These tests pin down the property: chunk and preprocessed paths must
    differ when the source MKV differs, even at identical (start, duration).
    """

    def _make_matcher(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        return EpisodeMatcher(
            cache_dir=tmp_path,
            show_name="Test Show",
            model_name="tiny",
        )

    def test_chunk_path_differs_per_source_mkv(self, tmp_path):
        matcher = self._make_matcher(tmp_path)
        path_a = matcher._chunk_path("/some/dir/title_t00.mkv", 1473, 30)
        path_b = matcher._chunk_path("/some/dir/title_t01.mkv", 1473, 30)
        assert path_a != path_b, (
            "Concurrent matches on different titles must not collide on the same on-disk chunk path"
        )

    def test_chunk_path_stable_for_same_source(self, tmp_path):
        # Same source, same offset/duration -> same path (so the on-disk
        # cache hit guard `if not chunk_path.exists()` still saves work).
        matcher = self._make_matcher(tmp_path)
        path_a = matcher._chunk_path("/some/dir/title_t00.mkv", 1473, 30)
        path_b = matcher._chunk_path("/some/dir/title_t00.mkv", 1473, 30)
        assert path_a == path_b

    def test_preprocessed_path_differs_per_source(self, tmp_path):
        # The Whisper preprocessor writes to a temp dir whose filename used to
        # be derived only from the input file's stem. Two source chunks with
        # the same stem (different parent dirs) would collide there.
        from app.matcher.asr_models import FasterWhisperModel

        model = FasterWhisperModel.__new__(FasterWhisperModel)
        a = model._preprocessed_path_for("/job_A/whisper_chunks/chunk_1473_30.wav")
        b = model._preprocessed_path_for("/job_B/whisper_chunks/chunk_1473_30.wav")
        assert a != b


@pytest.mark.unit
class TestModuleHelpers:
    def test_detect_file_encoding_handles_error(self, tmp_path):
        # Nonexistent file -> safe utf-8 fallback (no raise).
        assert ei.detect_file_encoding(tmp_path / "nope.srt") == "utf-8"

    def test_read_file_with_fallback(self, tmp_path):
        f = tmp_path / "sub.srt"
        f.write_text("héllo", encoding="utf-8")
        assert "llo" in ei.read_file_with_fallback(str(f))


@pytest.mark.unit
class TestTranscribeFull:
    def test_invokes_whisper_and_returns_text(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": " hello world from the episode " * 10}

        with (
            patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "a.wav")),
            patch("app.matcher.episode_identification.get_cached_model", return_value=fake_model),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            text = matcher.transcribe_full(tmp_path / "fake.mkv")

        assert text is not None
        assert "hello world" in text

    def test_returns_none_on_extraction_failure(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")

        with (
            patch.object(matcher, "extract_audio_chunk", side_effect=RuntimeError("ffmpeg boom")),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            text = matcher.transcribe_full(tmp_path / "fake.mkv")

        assert text is None


@pytest.mark.unit
class TestMatchFullFileSurfacesTranscript:
    def test_match_dict_includes_transcript(self, tmp_path):
        """When _match_full_file produces a transcript, the returned dict should expose it."""
        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")
        # The per-call TF-IDF matcher is passed in (no longer read from shared state).
        tfidf_mock = MagicMock()
        tfidf_mock.match.return_value = [("S01E03.srt", 0.85)]
        tfidf_mock.is_prepared = True
        # Stub the underlying transcription to a known value
        with patch.object(matcher, "transcribe_full", return_value="long fake transcript " * 50):
            result = matcher._match_full_file(
                video_file=tmp_path / "x.mkv",
                reference_files=[tmp_path / "S01E03.srt"],
                tfidf_matcher=tfidf_mock,
            )

        assert result is not None
        assert "transcript" in result
        assert result["transcript"].startswith("long fake transcript")


@pytest.mark.unit
class TestRankedVotingAcceptsHighCalibratedConfidence:
    """A decisive match whose RAW score sits at/below match_threshold but whose
    CALIBRATED confidence clears the floor must be accepted directly, NOT thrown
    into the expensive full-file fallback.

    Reproduces the True Detective S1 symptom: S1E7 matched at raw score 0.094 /
    calibrated 0.745 (7 decisive votes) but the old absolute raw-score gate
    (> 0.10) rejected it, forcing a slow — and sometimes worse — full-file
    re-transcription. The raw chunk cosine is structurally ~0.1 (30s ASR snippet
    vs full-episode TF-IDF), so the calibrated confidence is the real signal.
    """

    def _matcher(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        return EpisodeMatcher(cache_dir=tmp_path, show_name="True Detective", model_name="tiny")

    def _drive(self, matcher, tmp_path, per_chunk_match, pinned_confidence=None):
        """Run identify_episode with the ASR/TF-IDF seams mocked so every scan
        point yields ``per_chunk_match`` from the TF-IDF matcher.

        When ``pinned_confidence`` is set, ``_attach_calibrated_confidence`` is
        replaced with a stub that pins ``best["confidence"]`` to that value. This
        decouples the *gate* assertion (does it accept on calibrated confidence?)
        from the calibration math (which is tested directly in
        TestCalibrateConfidence) — a change to the calibration weights can't
        silently break this test. Returns (result, fallback_mock).
        """
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {
            "text": "you want a confession everybody wants some cathartic narrative " * 3
        }

        tfidf = MagicMock()
        tfidf.is_prepared = True
        tfidf.reference_signature.return_value = ("precomputed", ("S01E05", "S01E07"))
        tfidf.match.return_value = per_chunk_match

        precomputed = (csr_matrix(np.eye(2)), ["S01E05", "S01E07"], np.ones(2))
        fallback = MagicMock(return_value=None)

        def _pin_confidence(best, results_summary, video_duration):
            best["confidence"] = pinned_confidence
            best.setdefault("runner_ups", [])
            best["match_details"]["score_gap"] = best.get("score", 0.0)

        with contextlib.ExitStack() as stack:
            # Inject the fake TF-IDF matcher via the per-call seam (identify_episode
            # no longer reads a shared self.tfidf_matcher slot).
            stack.enter_context(patch.object(matcher, "_get_tfidf_matcher", return_value=tfidf))
            stack.enter_context(
                patch.object(matcher, "_load_precomputed_season", return_value=precomputed)
            )
            stack.enter_context(
                patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "a.wav"))
            )
            stack.enter_context(patch.object(matcher, "_match_full_file", fallback))
            stack.enter_context(
                patch(
                    "app.matcher.episode_identification.get_cached_model", return_value=fake_model
                )
            )
            stack.enter_context(
                patch("app.matcher.episode_identification.get_video_duration", return_value=3300)
            )
            if pinned_confidence is not None:
                stack.enter_context(
                    patch(
                        "app.matcher.episode_identification._attach_calibrated_confidence",
                        _pin_confidence,
                    )
                )
            result = matcher.identify_episode(tmp_path / "5.mkv", tmp_path, 1)
        return result, fallback

    def test_low_raw_high_confidence_accepted_without_fallback(self, tmp_path):
        matcher = self._matcher(tmp_path)
        # Each chunk votes S01E07 at a low absolute cosine (~0.094) but with a clear
        # per-chunk margin over the runner-up, so the chunk-vote gate passes. The
        # calibrated confidence is pinned high (0.81) so this asserts the GATE's
        # behaviour, not the calibration math.
        result, fallback = self._drive(
            matcher, tmp_path, [("S01E07", 0.094), ("S01E05", 0.03)], pinned_confidence=0.81
        )

        assert result is not None
        assert result["episode"] == 7
        # Accepted via the calibrated path: raw score at/below threshold (real),
        # calibrated confidence above the floor (pinned) — and crucially, the
        # fallback never ran.
        assert result["score"] <= matcher.match_threshold
        assert result["confidence"] == pytest.approx(0.81)
        assert result["confidence"] >= matcher.confidence_accept_floor
        fallback.assert_not_called()

    def test_indecisive_chunks_still_fall_through_to_fallback(self, tmp_path):
        matcher = self._matcher(tmp_path)
        # Near-tie per chunk (margin 0.05/0.045 < 1.8) — no chunk clears the
        # vote gate, so there is no acceptable ranked match and the full-file
        # fallback must still run (the calibrated path doesn't rescue noise).
        result, fallback = self._drive(matcher, tmp_path, [("S01E07", 0.05), ("S01E05", 0.045)])

        assert result is not None  # no-episode result preserved (not a bare None)
        assert result["episode"] is None
        fallback.assert_called_once()


@pytest.mark.unit
class TestTranscriptionCache:
    """ASR transcripts are memoized by (source, start, duration) and the cache
    SURVIVES across identify_episode() calls.

    Background: identify_episode()'s `finally` clears audio_chunks and deletes the
    chunk WAVs every call, so when the season is unknown and the curator matches a
    file against several candidate seasons (one identify_episode per season), every
    season re-extracts AND re-runs Whisper over the exact same audio offsets — the
    dominant cost behind the multi-hour season-unknown runaway. A persistent
    transcript cache collapses seasons 2..N to a TF-IDF-only pass.
    """

    def _matcher(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        return EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show", model_name="tiny")

    def test_transcribe_full_reuses_transcript_across_calls(self, tmp_path):
        matcher = self._matcher(tmp_path)
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": " hello world from the episode " * 10}

        with (
            patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "a.wav")),
            patch("app.matcher.episode_identification.get_cached_model", return_value=fake_model),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            first = matcher.transcribe_full(tmp_path / "fake.mkv")
            second = matcher.transcribe_full(tmp_path / "fake.mkv")

        assert first == second
        # Whisper ran once; the second call was served from the transcript cache.
        assert fake_model.transcribe.call_count == 1

    def test_identify_episode_reuses_chunk_transcripts_across_seasons(self, tmp_path):
        """End-to-end across-seasons win: matching the same file against two
        seasons transcribes each scan-point offset once, not once per season.
        """
        matcher = self._matcher(tmp_path)
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": "the quick brown fox jumps over " * 4}

        # Pre-seed a prepared TF-IDF matcher so identify_episode skips the real
        # rebuild + hashed-query path; only the transcript-cache behaviour is exercised.
        tfidf = MagicMock()
        tfidf.is_prepared = True
        tfidf.reference_signature.return_value = ("precomputed", ("S01E01",))
        tfidf.match.return_value = [("S01E01", 0.9)]

        precomputed = (csr_matrix(np.eye(1)), ["S01E01"], np.ones(1))

        with (
            # Inject the fake TF-IDF matcher via the per-call seam.
            patch.object(matcher, "_get_tfidf_matcher", return_value=tfidf),
            patch.object(matcher, "_load_precomputed_season", return_value=precomputed),
            patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "a.wav")),
            patch("app.matcher.episode_identification.get_cached_model", return_value=fake_model),
            patch("app.matcher.episode_identification.get_video_duration", return_value=2000),
        ):
            matcher.identify_episode(tmp_path / "file.mkv", tmp_path, 1)
            after_first = fake_model.transcribe.call_count
            matcher.identify_episode(tmp_path / "file.mkv", tmp_path, 2)
            after_second = fake_model.transcribe.call_count

        assert after_first > 0, "first pass must transcribe the scan-point chunks"
        # Second pass over the SAME file (different season) reused every transcript.
        assert after_second == after_first

    def test_distinct_segments_each_transcribe(self, tmp_path):
        matcher = self._matcher(tmp_path)
        calls = []

        def compute():
            calls.append(1)
            return f"text-{len(calls)}"

        key_a = matcher._transcription_key(tmp_path / "f.mkv", 300, 30)
        key_b = matcher._transcription_key(tmp_path / "f.mkv", 600, 30)
        # Same key reuses; different (start) recomputes.
        assert matcher.transcriptions.get(key_a) is None
        matcher._remember_transcription(key_a, compute())
        assert matcher.transcriptions.get(key_a) == "text-1"
        matcher._remember_transcription(key_b, compute())
        assert len(calls) == 2
        assert key_a != key_b

    def test_cache_is_bounded(self, tmp_path):
        matcher = self._matcher(tmp_path)
        matcher._max_transcription_cache = 2
        matcher._remember_transcription(("s", 0, 30), "a")
        matcher._remember_transcription(("s", 30, 30), "b")
        # Third insert exceeds the cap -> cache is cleared before storing.
        matcher._remember_transcription(("s", 60, 30), "c")
        assert ("s", 60, 30) in matcher.transcriptions
        assert len(matcher.transcriptions) == 1

    def test_key_is_source_addressed(self, tmp_path):
        matcher = self._matcher(tmp_path)
        # Same offset/duration on different source files must not collide.
        a = matcher._transcription_key("/d/title_t00.mkv", 300, 30)
        b = matcher._transcription_key("/d/title_t01.mkv", 300, 30)
        assert a != b
