import hashlib
import json
import re
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import chardet
import ctranslate2
import numpy as np
from loguru import logger
from rich.console import Console
from scipy.sparse import load_npz as scipy_load_npz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine_similarity

from app.matcher.asr_models import get_cached_model
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.utils import extract_season_episode
from app.matcher.vectorizer_config import apply_tfidf

console = Console()


def load_precomputed_manifest(cache_dir) -> dict | None:
    """Load and validate the precomputed-cache manifest. Returns the dict or None.

    A missing, unreadable, or version/config-mismatched manifest is treated as
    "no cache" so callers fall back to subtitle scraping. Shared by the matcher's
    load path and the download-skip check so both agree on what counts as valid.
    """
    from app.matcher.vectorizer_config import (
        CACHE_FORMAT_VERSION,
        vectorizer_config_hash,
    )

    manifest_path = Path(cache_dir) / "precomputed" / "manifest.json"
    if not manifest_path.exists():
        return None

    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Precomputed cache manifest unreadable ({e}); using scraping")
        return None

    if manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
        logger.warning(
            f"Precomputed cache format mismatch "
            f"(manifest={manifest.get('cache_format_version')}, code={CACHE_FORMAT_VERSION}); "
            f"ignoring cache"
        )
        return None
    if manifest.get("vectorizer_config_hash") != vectorizer_config_hash():
        logger.warning("Precomputed cache vectorizer-config mismatch; ignoring cache")
        return None

    return manifest


def _tmdb_id_mismatch(expected, entry_id) -> bool:
    """True when both ids are known and disagree (string-compared).

    Shared by the corpus guard's two call sites (this module's
    ``precomputed_covers_season`` and ``EpisodeMatcher._load_precomputed_season``)
    so the comparison can't silently diverge. Returns False when either id is
    unknown — backward-compatible with name-only matching.
    """
    return expected is not None and entry_id is not None and str(entry_id) != str(expected)


def precomputed_covers_season(
    cache_dir, show_name: str, season: int, manifest=None, expected_tmdb_id=None
) -> bool:
    """Return True when the precomputed vector cache fully covers show+season.

    Mirrors the gate EpisodeMatcher applies at match time (manifest validity,
    show/season listing, and on-disk .npz/.index.json files) WITHOUT loading
    vectors, so a True result guarantees the matcher will use the cache. The
    show name must be the canonical name the matcher resolves to.

    ``manifest`` may be a pre-loaded manifest dict to avoid re-reading and
    re-validating manifest.json (callers holding a cached copy pass it in).

    ``expected_tmdb_id`` is the TMDB id the calling job has resolved for the
    show. When supplied, it is compared against the manifest entry's ``tmdb_id``
    (string comparison); a mismatch means the corpus is for a *different*
    same-named show (e.g. Frasier 1993 vs 2023 revival) and this function
    returns False before inspecting any on-disk files. When either id is
    unknown the guard is skipped (backward-compatible).
    """
    if manifest is None:
        manifest = load_precomputed_manifest(cache_dir)
    if not manifest:
        return False

    show_entry = manifest.get("shows", {}).get(show_name)
    if not show_entry or season not in show_entry.get("seasons", []):
        return False

    # Corpus guard: if the job knows its TMDB id and it contradicts the
    # manifest entry's id, this precomputed corpus is for a DIFFERENT same-named
    # show — refuse it so we never match e.g. the Frasier 2023 revival against the
    # 1993 corpus. Skipped when either id is unknown (backward-compatible).
    if _tmdb_id_mismatch(expected_tmdb_id, show_entry.get("tmdb_id")):
        return False

    show_dir = Path(cache_dir) / "precomputed" / sanitize_filename(show_name)
    npz_path = show_dir / f"S{season:02d}.npz"
    index_path = show_dir / f"S{season:02d}.index.json"
    return npz_path.exists() and index_path.exists()


def precomputed_episode_codes(cache_dir, show_name: str, season: int) -> list[str] | None:
    """Episode codes the precomputed cache holds for show+season, else None.

    None means the cache doesn't cover it (caller should download/scrape).
    Lets callers size a result from the cache itself without a TMDB round-trip.
    """
    if not precomputed_covers_season(cache_dir, show_name, season):
        return None

    index_path = (
        Path(cache_dir) / "precomputed" / sanitize_filename(show_name) / f"S{season:02d}.index.json"
    )
    try:
        with open(index_path, encoding="utf-8") as fh:
            codes = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return codes if isinstance(codes, list) and codes else None


class SubtitleCache:
    """Cache for storing parsed subtitle data to avoid repeated loading and parsing."""

    def __init__(self):
        self.subtitles = {}  # {file_path: parsed_content}
        self.chunk_cache = {}  # {(file_path, chunk_idx): text}
        self._full_text_cache = {}  # {file_path: cleaned_full_text}

    def get_subtitle_content(self, srt_file):
        """Get the full raw content of a subtitle file, loading it only once."""
        srt_file = str(srt_file)
        if srt_file not in self.subtitles:
            reader = SubtitleReader()
            self.subtitles[srt_file] = reader.read_srt_file(srt_file)
        return self.subtitles[srt_file]

    def get_chunk(self, srt_file, chunk_idx, chunk_start, chunk_end):
        """Get a specific time chunk from a subtitle file, with caching."""
        srt_file = str(srt_file)
        cache_key = (srt_file, chunk_idx)

        if cache_key not in self.chunk_cache:
            content = self.get_subtitle_content(srt_file)
            reader = SubtitleReader()
            text_lines = reader.extract_subtitle_chunk(content, chunk_start, chunk_end)
            text = " ".join(text_lines)
            text = _clean_subtitle_text(text)
            self.chunk_cache[cache_key] = text

        return self.chunk_cache[cache_key]

    def get_full_text(self, srt_file):
        """
        Get the full cleaned text of an entire subtitle file.

        Extracts all subtitle blocks, joins their text, and applies standard
        cleaning (lowercase, strip tags, collapse stutters, normalize whitespace).
        Result is cached for reuse.
        """
        srt_file = str(srt_file)
        if srt_file not in self._full_text_cache:
            content = self.get_subtitle_content(srt_file)
            if not content:
                self._full_text_cache[srt_file] = ""
            else:
                reader = SubtitleReader()
                # Extract all text from 0 to a very large end time
                text_lines = reader.extract_subtitle_chunk(content, 0, 999999)
                full_text = " ".join(text_lines)
                self._full_text_cache[srt_file] = _clean_subtitle_text(full_text)
        return self._full_text_cache[srt_file]

    def get_subtitle_duration(self, srt_file, content=None):
        """Get the total duration of a subtitle file in seconds."""
        srt_file = str(srt_file)
        if content is None:
            content = self.get_subtitle_content(srt_file)

        if not content:
            return 0.0

        # Content is raw SRT string, use SubtitleReader to parse duration
        return SubtitleReader.get_duration(content)


def _clean_subtitle_text(text: str) -> str:
    """Clean subtitle text: lowercase, strip tags/special chars, collapse stutters, normalize whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[\[{][^\]}]*[\]}]|<.*?>", "", text)  # tolerate mismatched [/{ ]/} delimiters
    text = re.sub(r"([A-Za-z])-\1+", r"\1", text)  # collapse stutters
    text = re.sub(r"[^\w\s']", " ", text)  # remove special chars except apostrophes
    return " ".join(text.split())


def _is_watermark_block(block_text: str, block_lines: list[str], subtitle_start: float) -> bool:
    """Detect subtitle blocks that are watermarks, ads, or non-dialogue annotations.

    Generically identifies watermark content regardless of source by checking for:
    - URLs or domain-like patterns (e.g., www.tvsubtitles.net, opensubtitles.org)
    - Blocks near timestamp 0:00 with non-dialogue content (ad overlays)
    - Font color/size tags wrapping the entire content (styled ads)
    """
    text_lower = block_text.lower().strip()

    # Check for URLs or domain patterns
    if re.search(r"(?:www\.|https?://|\w+\.(?:com|net|org|io|tv|cc|me))", text_lower):
        return True

    # Check for blocks that are only font/styling tags wrapping a URL or brand name
    stripped = re.sub(r"<[^>]+>", "", text_lower).strip()
    if stripped and re.search(r"(?:www\.|https?://|\w+\.(?:com|net|org|io|tv|cc|me))", stripped):
        return True

    # Very short non-dialogue at start (e.g., "sync by", "subtitles by", "corrected by")
    if subtitle_start < 5.0 and len(stripped.split()) <= 8:
        credit_patterns = [
            "sync",
            "subtitles by",
            "corrected by",
            "ripped by",
            "encoded by",
            "transcript by",
            "timing by",
        ]
        if any(p in stripped for p in credit_patterns):
            return True

    return False


# --- Confidence calibration --------------------------------------------------
#
# The matcher's raw ``ranked_voting_score`` is the mean TF-IDF cosine of the
# chunks that voted for the winning episode. Comparing a 30s ASR snippet to a
# full ~22-minute subtitle file ("teaspoon vs bucket") keeps that cosine small
# (~0.15-0.21) even for a correct match, so it is uninterpretable as a
# percentage. ``calibrate_confidence`` translates the raw signals into a 0-1
# confidence a human reviewer can read. It answers "how sure are we this is the
# right episode?" rather than "how much text overlapped?".
#
# Rationale for the constants lives in
# docs/superpowers/specs/2026-05-22-match-confidence-calibration-design.md.

# Cosine at which a matched chunk counts as full-quality. Votes require >0.15
# and observed-correct chunks cluster ~0.15-0.21, so the discriminative band is
# razor-thin; normalized_score is a mild guard, mostly reported for transparency.
QUALITY_REF_COSINE = 0.18
# Processed fraction treated as full coverage. ~22-min episodes sample ~0.23, so
# typical TV reads 1.0 while longer episodes (covered proportionally less) read
# lower -- the correct direction (the rejected score/coverage design did the
# opposite, leaking episode length into confidence).
COVERAGE_REF = 0.15
# Evidence is a weighted blend of the three independent reported metrics.
# Consensus (how many sampled chunks agreed) is the strongest, so it dominates.
W_CONSENSUS = 0.5
W_NORMALIZED_SCORE = 0.25
W_COVERAGE = 0.25
# Floor so a decisive sweep with thin evidence still reads meaningfully, while
# minimal evidence lands just under the 0.7 review gate.
EVIDENCE_FLOOR = 0.30
# Vote-boost parameters: when many independent chunks agree on a winner AND
# the match is at least somewhat decisive (separation > NEAR_TIE_FLOOR), vote
# evidence can relax the separation requirement.
#   NEAR_TIE_FLOOR  — below this separation, no boost is applied. A close
#     runner-up at any vote count is genuinely ambiguous.
#   HIGH_CONSENSUS_REF — proportion of scan points for "full consensus" credit.
#   HIGH_VOTE_REF   — absolute chunk count for "full vote" credit. Deep re-match
#     uses 25 scan points; 15 chunks agreeing is strong absolute evidence.
#   MAX_VOTE_BOOST  — maximum separation added by combined consensus + vote credit.
#     At max boost, separation can be lifted by up to 0.30, which allows a match
#     with separation ~0.52 to clear the 0.7 review gate (vs ~0.82 previously).
# Vote-ratio path: a second independent confidence route that fires when the
# winner matched far more chunks than the runner-up. score_gap (mean-cosine
# difference) misses this signal because both candidates may score similarly
# per-chunk even when one matched 3× more chunks overall.
#   HIGH_VOTE_RATIO         — winner must have this many times the runner-up's
#     vote count for full ratio credit (e.g. 103 vs 31 = 3.3×).
#   MIN_CONSENSUS_FOR_RATIO — minimum winner consensus before the ratio path
#     activates; prevents very sparse matches (e.g. 3/119) from passing.
NEAR_TIE_FLOOR = 0.15
HIGH_CONSENSUS_REF = 0.60
HIGH_VOTE_REF = 15
MAX_VOTE_BOOST = 0.30
HIGH_VOTE_RATIO = 3.0
MIN_CONSENSUS_FOR_RATIO = 0.50

# Rank+margin chunk-vote gate. A 30s ASR chunk (~30-120 words) compared against a
# full-episode TF-IDF vector (~3-5k words) yields a structurally low absolute
# cosine even for a perfect match (~0.08-0.22): both vectors are L2-normalized, so
# the short, sparse chunk can only overlap a small fraction of the episode's mass.
# An absolute gate (the historical `cosine > 0.15`) therefore rejected most
# correct chunks, returning episode=None. The *ranking* is reliable instead -- the
# correct episode leads the runner-up by ~1.8-5.6x -- so a chunk votes for its top
# episode when that lead is clear. See select_chunk_vote and the empirical scale
# measurement in docs/superpowers/reviews/2026-05-29-asr-chunk-vote-scale-mismatch.md.
CHUNK_VOTE_FLOOR = 0.06  # below this top-1 cosine, treat the chunk as noise
CHUNK_VOTE_MARGIN_RATIO = 1.8  # top-1 must lead the runner-up by this ratio to vote


def select_chunk_vote(
    tfidf_results: list[tuple[str, float]],
    floor: float = CHUNK_VOTE_FLOOR,
    ratio: float = CHUNK_VOTE_MARGIN_RATIO,
) -> tuple[str, float] | None:
    """Pick the single episode a transcribed chunk votes for, or None.

    Replaces the miscalibrated absolute cosine gate with a rank+margin rule that
    survives the chunk-vs-full-episode scale mismatch (see CHUNK_VOTE_FLOOR notes).
    A chunk votes for its top-ranked episode iff that episode (a) clears ``floor``
    (guards pure-noise/near-silent chunks) and (b) leads the runner-up by
    ``ratio``x (so recap or shared-dialogue chunks, where two episodes score
    similarly, abstain instead of casting a confident-but-wrong vote).

    Args:
        tfidf_results: ``(reference, cosine)`` pairs sorted by cosine descending,
            as returned by ``TfidfMatcher.match()``.
        floor: minimum top-1 cosine required to consider voting.
        ratio: required ``top1 / runner_up`` lead. A lone candidate (no runner-up)
            only needs to clear the floor.

    Returns:
        ``(reference, cosine)`` for the winning episode, or None when no episode
        clearly leads.
    """
    if not tfidf_results:
        return None
    top_ref, top_score = tfidf_results[0]
    if top_score < floor:
        return None
    runner_up_score = tfidf_results[1][1] if len(tfidf_results) > 1 else 0.0
    if top_score < ratio * runner_up_score:
        return None
    return top_ref, top_score


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def calibrate_confidence(
    *,
    score: float,
    score_gap: float,
    vote_count: int,
    target_votes: int,
    processed_coverage: float,
    runner_up_votes: int = 0,
    chromaprint_signal: dict | None = None,
) -> tuple[float, dict[str, float]]:
    """Translate raw match signals into a 0-1 reviewer-facing confidence.

    Args:
        score: winner's ranked_voting_score (mean matched-chunk cosine).
        score_gap: top1 - top2 (decisiveness; == score when uncontested).
        vote_count: chunks that voted for the winner.
        target_votes: total chunks sampled.
        processed_coverage: fraction of the file run through the matcher
            (samples * chunk_len / video_duration), NOT the matched fraction.
        runner_up_votes: chunks that voted for the best alternative candidate.
            When provided, enables the vote-ratio confidence path. Default 0
            (ratio path disabled) for backward compatibility.

    Returns:
        (confidence, components) where components reports the independent
        metrics (separation, consensus, normalized_score, coverage, evidence,
        vote_boost, effective_separation, vote_ratio_score, ratio_confidence),
        each in [0, 1].  ``vote_ratio`` is also included as a *raw* diagnostic
        (winner_votes / runner_up_votes) and is NOT bounded to [0, 1] — it
        equals 0.0 when the ratio path is inactive and can exceed 1.0
        (e.g. 3.32 for 103 vs 31 votes) when it fires.

    Two independent confidence paths — the higher wins:

    Path 1 — separation-based (score_gap signal):
        effective_separation = separation + vote_boost
        vote_boost = MAX_VOTE_BOOST * (consensus / HIGH_CONSENSUS_REF)
                                     * (vote_count / HIGH_VOTE_REF)
                     only when separation > NEAR_TIE_FLOOR, else 0
        base_confidence = effective_separation * evidence

    Path 2 — vote-ratio (chunk-count signal, requires runner_up_votes > 0):
        Fires when winner matched HIGH_VOTE_RATIO× more chunks than runner-up
        AND overall consensus >= MIN_CONSENSUS_FOR_RATIO.
        ratio_confidence = evidence
                         * clamp(vote_ratio_score)
                         * clamp(consensus / HIGH_CONSENSUS_REF)
        where vote_ratio_score scales (vote_count/runner_up_votes) from 1× to
        HIGH_VOTE_RATIO× into [0, 1].

    confidence = max(base_confidence, ratio_confidence)

    Rationale for Path 2: ranked_voting_score is a per-chunk mean cosine, so
    a runner-up that matched ⅓ as many chunks as the winner may still produce
    a similar mean cosine (and thus a small score_gap). Path 1 alone then
    underestimates confidence. The vote-ratio path captures this: 103 vs 31
    chunk matches is decisive regardless of whether the mean cosines are close.
    """
    eps = 1e-9
    separation = _clamp01(score_gap / score) if score > eps else 0.0
    consensus = _clamp01(vote_count / target_votes) if target_votes > 0 else 0.0
    normalized_score = _clamp01(score / QUALITY_REF_COSINE)
    coverage_norm = _clamp01(processed_coverage / COVERAGE_REF)

    evidence_raw = (
        W_CONSENSUS * consensus + W_NORMALIZED_SCORE * normalized_score + W_COVERAGE * coverage_norm
    )
    evidence = EVIDENCE_FLOOR + (1.0 - EVIDENCE_FLOOR) * evidence_raw

    # Path 1 — separation-based: near-ties are excluded (runner-up too close
    # to trust vote count alone). Above the floor, both consensus fraction and
    # absolute vote count must be high to earn the full boost — requiring many
    # independent chunks to agree, not just a high proportion of a small sample.
    if separation > NEAR_TIE_FLOOR:
        vote_boost = (
            MAX_VOTE_BOOST
            * _clamp01(consensus / HIGH_CONSENSUS_REF)
            * _clamp01(vote_count / HIGH_VOTE_REF)
        )
        effective_separation = _clamp01(separation + vote_boost)
    else:
        vote_boost = 0.0
        effective_separation = separation
    base_confidence = _clamp01(effective_separation * evidence)

    # Path 2 — vote-ratio: fires when the winner matched significantly more
    # chunks than the runner-up AND the overall consensus is strong. This
    # captures cases where score_gap is small despite decisive chunk-count
    # dominance (e.g. 103 vs 31 votes with similar per-chunk cosines).
    vote_ratio = 0.0
    vote_ratio_score = 0.0
    ratio_confidence = 0.0
    if runner_up_votes > 0 and consensus >= MIN_CONSENSUS_FOR_RATIO:
        vote_ratio = vote_count / runner_up_votes
        # Scale from [1×, HIGH_VOTE_RATIO×] → [0, 1]; below 1× impossible, above clamps to 1.
        vote_ratio_score = _clamp01((vote_ratio - 1.0) / (HIGH_VOTE_RATIO - 1.0))
        ratio_confidence = _clamp01(
            evidence * vote_ratio_score * _clamp01(consensus / HIGH_CONSENSUS_REF)
        )

    confidence = max(base_confidence, ratio_confidence)

    # Chromaprint path (Phase 3) — additive. Absent signal is a no-op; a present
    # signal can only raise confidence (max), never lower an ASR-strong result.
    cp_overlap = cp_temporal = cp_rarity = cp_confidence = 0.0
    if chromaprint_signal:
        cp_overlap = _clamp01(float(chromaprint_signal.get("hash_overlap", 0.0)))
        cp_temporal = _clamp01(float(chromaprint_signal.get("temporal_coherence", 0.0)))
        cp_rarity = _clamp01(float(chromaprint_signal.get("rarity_weighted_score", 0.0)))
        cp_evidence = EVIDENCE_FLOOR + (1.0 - EVIDENCE_FLOOR) * cp_temporal
        cp_confidence = _clamp01(cp_overlap * cp_evidence * (0.5 + 0.5 * cp_rarity))
        confidence = max(confidence, cp_confidence)

    components = {
        "separation": separation,
        "vote_boost": vote_boost,
        "effective_separation": effective_separation,
        "consensus": consensus,
        "normalized_score": normalized_score,
        # Report the actual processed fraction (the metric), not the normalized
        # form used inside the formula.
        "coverage": _clamp01(processed_coverage),
        "evidence": evidence,
        "vote_ratio": vote_ratio,
        "vote_ratio_score": vote_ratio_score,
        "ratio_confidence": ratio_confidence,
        "hash_overlap": cp_overlap,
        "temporal_coherence": cp_temporal,
        "rarity_weighted_score": cp_rarity,
        "cp_confidence": cp_confidence,
    }
    return confidence, components


def _attach_calibrated_confidence(
    best_match: dict,
    results_summary: list[dict],
    video_duration: float,
    chunk_len: int = 30,
    chromaprint_signal: dict | None = None,
) -> None:
    """Mutate ``best_match`` in place with calibrated confidence + leaderboard.

    Sets ``best_match["confidence"]`` to the calibrated value (this is what flows
    to ``DiscTitle.match_confidence``) while leaving ``best_match["score"]`` and
    ``match_details["score"]`` as the raw ranked_voting_score that the accept-vs-
    fallback gate and conflict resolution depend on. Adds the reported metrics to
    ``match_details`` and builds ``runner_ups`` where each entry keeps its raw
    ``score`` (for cascading reassignment) and gains a calibrated ``confidence``
    scaled to the winner so the winner's leaderboard entry equals the headline.
    """
    if not results_summary:
        return

    # results_summary is sorted by score descending by the caller.
    top1 = results_summary[0]["score"]
    if len(results_summary) >= 2:
        score_gap = top1 - results_summary[1]["score"]
        runner_up_votes = results_summary[1].get("vote_count", 0)
    else:
        score_gap = top1
        runner_up_votes = 0

    md = best_match.setdefault("match_details", {})
    target_votes = md.get("target_votes", 0)
    vote_count = md.get("vote_count", 0)
    processed_coverage = (target_votes * chunk_len / video_duration) if video_duration > 0 else 0.0

    # Use top1 (not best_match["score"]) as the denominator so separation
    # (score_gap / score) is self-consistent: both derive from results_summary.
    # They are equal by construction (best_match is the top-scoring candidate),
    # but sourcing both here removes the implicit cross-reference invariant.
    confidence, components = calibrate_confidence(
        score=top1,
        score_gap=score_gap,
        vote_count=vote_count,
        target_votes=target_votes,
        processed_coverage=processed_coverage,
        runner_up_votes=runner_up_votes,
        chromaprint_signal=chromaprint_signal,
    )

    best_match["confidence"] = confidence
    md["confidence"] = confidence
    md["score_gap"] = score_gap
    md["separation"] = components["separation"]
    md["vote_boost"] = components["vote_boost"]
    md["effective_separation"] = components["effective_separation"]
    md["normalized_score"] = components["normalized_score"]
    md["consensus"] = components["consensus"]
    md["vote_ratio"] = components["vote_ratio"]
    md["ratio_confidence"] = components["ratio_confidence"]
    md["coverage"] = components["coverage"]

    runner_ups = []
    for r in results_summary:
        if r["score"] <= 0:
            continue
        ru_confidence = _clamp01(confidence * (r["score"] / top1)) if top1 > 0 else 0.0
        runner_ups.append(
            {
                "episode": r["episode"],
                "score": r["score"],  # raw, for conflict resolution
                "confidence": ru_confidence,  # calibrated, for display
                "vote_count": r["vote_count"],
                "target_votes": r.get("target_votes", target_votes),
            }
        )
    runner_ups = runner_ups[:5]
    # Distinct list objects: curator shallow-copies match_details but not the
    # inner list, so a shared reference could let one mutation corrupt the other.
    best_match["runner_ups"] = runner_ups
    md["runner_ups"] = runner_ups[:]


class TfidfMatcher:
    """
    Episode matcher using TF-IDF cosine similarity.

    Pre-computes TF-IDF vectors for all reference episode texts (full subtitle content),
    then matches transcribed chunks via cosine similarity — ~1ms per query vs ~465ms
    for the previous sliding-window RapidFuzz approach, with higher accuracy (97.9% vs 96.6%).
    """

    def __init__(self):
        self.vectorizer = None
        self.ref_matrix = None
        self.ref_file_order = []  # ordered list of reference file paths (or episode codes)
        self._prepared = False
        self._precomputed = False  # True when loaded from the shipped vector cache
        self._idf = None  # global IDF array, only set in precomputed mode

    def load_precomputed(self, ref_matrix, ref_episode_codes, idf_array) -> None:
        """Load a precomputed hashed TF-IDF cache instead of fitting from SRT.

        Args:
            ref_matrix: scipy CSR matrix, one L2-normalized TF-IDF row per episode.
            ref_episode_codes: episode codes ("S01E03"), aligned to matrix rows.
            idf_array: global IDF array used to project queries into the same space.
        """
        self.ref_matrix = ref_matrix
        self.ref_file_order = list(ref_episode_codes)
        self._idf = idf_array
        self._precomputed = True
        self._prepared = True
        logger.info(
            f"TF-IDF loaded from precomputed cache: {len(self.ref_file_order)} episodes, "
            f"{self.ref_matrix.shape[1]} features"
        )

    def prepare(self, reference_files, subtitle_cache: SubtitleCache):
        """
        Fit TF-IDF vectorizer on all reference episode full texts.

        Args:
            reference_files: List of paths to reference SRT files
            subtitle_cache: SubtitleCache instance for loading/caching SRT content
        """
        self.ref_file_order = [str(rf) for rf in reference_files]
        corpus = []
        for rf in self.ref_file_order:
            full_text = subtitle_cache.get_full_text(rf)
            corpus.append(full_text)
            logger.debug(f"  TF-IDF ref: {Path(rf).stem} ({len(full_text)} chars)")

        self.vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            max_features=10000,
            sublinear_tf=True,
        )
        self.ref_matrix = self.vectorizer.fit_transform(corpus)
        self._prepared = True
        logger.info(
            f"TF-IDF prepared: {len(self.ref_file_order)} references, "
            f"{self.ref_matrix.shape[1]} features"
        )

    def match(self, query_text: str) -> list[tuple[str, float]]:
        """
        Match a transcribed text chunk against all reference episodes.

        Args:
            query_text: Cleaned transcription text from a video chunk

        Returns:
            List of (reference_file_path, cosine_score) sorted by score descending
        """
        if not self._prepared:
            raise RuntimeError("TfidfMatcher.prepare() must be called before match()")

        if self._precomputed:
            from app.matcher.vectorizer_config import transform_query

            q_vec = transform_query(query_text, self._idf)
        else:
            q_vec = self.vectorizer.transform([query_text])
        sims = sklearn_cosine_similarity(q_vec, self.ref_matrix)[0]

        results = list(zip(self.ref_file_order, sims.tolist(), strict=False))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    @property
    def is_prepared(self) -> bool:
        return self._prepared

    def reference_signature(self) -> tuple | None:
        """Fingerprint of what this matcher was prepared against; None if not prepared."""
        if not self._prepared:
            return None
        # Mode distinguishes precomputed-codes from scraping-paths so a stale
        # matcher from a prior precomputed call can't keep returning codes
        # while the new call's `coverages` is path-keyed → KeyError.
        mode = "precomputed" if self._precomputed else "scraping"
        return (mode, tuple(self.ref_file_order))


class MatchCoverage:
    """Tracks match coverage for an episode against a video file."""

    def __init__(self, episode_name: str, reference_duration: float, video_duration: float):
        self.episode_name = episode_name
        self.reference_duration = reference_duration
        self.video_duration = video_duration
        self.matched_chunks = []  # List of {start, duration, confidence}

    def add_match(self, start_time, duration, confidence):
        self.matched_chunks.append(
            {"start": start_time, "duration": duration, "confidence": confidence}
        )

    @property
    def avg_confidence(self) -> float:
        if not self.matched_chunks:
            return 0.0
        return sum(c["confidence"] for c in self.matched_chunks) / len(self.matched_chunks)

    @property
    def file_coverage(self) -> float:
        if self.video_duration <= 0:
            return 0.0
        # Assume non-overlapping chunks for simplicity
        matched_duration = sum(c["duration"] for c in self.matched_chunks)
        return min(1.0, matched_duration / self.video_duration)

    @property
    def episode_coverage(self) -> float:
        """Percentage of the episode referenced that was found."""
        if self.reference_duration <= 0:
            return 0.0
        matched_duration = sum(c["duration"] for c in self.matched_chunks)
        return min(1.0, matched_duration / self.reference_duration)

    @property
    def weighted_score(self) -> float:
        # Legacy method: avg_confidence × file_coverage
        # Kept for backward compatibility and comparison
        return self.avg_confidence * self.file_coverage

    @property
    def total_vote_weight(self) -> float:
        """Sum of coverage weights for all matched chunks."""
        if not self.matched_chunks:
            return 0.0
        return sum(c["duration"] / self.video_duration for c in self.matched_chunks)

    @property
    def ranked_voting_score(self) -> float:
        """
        Ranked voting score: weighted average of chunk confidences.

        Formula: sum(confidence × weight) / sum(weights)
        Where weight = chunk_duration / video_duration

        This provides consensus-based matching that considers evidence from all chunks,
        weights each chunk's vote by its contribution, and produces more stable confidence scores.
        """
        if not self.matched_chunks or self.video_duration <= 0:
            return 0.0

        weighted_sum = sum(
            c["confidence"] * (c["duration"] / self.video_duration) for c in self.matched_chunks
        )
        total_weight = self.total_vote_weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def get_voting_details(self) -> dict:
        """Get detailed voting information for logging/debugging."""
        return {
            "episode": self.episode_name,
            "vote_count": len(self.matched_chunks),
            "ranked_score": self.ranked_voting_score,
            "avg_confidence": self.avg_confidence,
            "total_weight": self.total_vote_weight,
            "file_coverage": self.file_coverage,
            "legacy_weighted_score": self.weighted_score,
        }


class EpisodeMatcher:
    """
    Episode matcher using audio fingerprinting and ranked voting.

    Uses sparse sampling strategy (dense: 30s intervals, sparse: 150s intervals)
    with ranked-choice voting to select the best matching episode based on
    weighted confidence consensus across all matched chunks.
    """

    def __init__(
        self,
        cache_dir,
        show_name,
        min_confidence=0.6,
        device=None,
        use_ranked_voting=True,
        min_vote_count=2,
        match_threshold=0.10,
        model_name="small",
        expected_tmdb_id=None,
    ):
        self.cache_dir = Path(cache_dir)
        self.min_confidence = min_confidence
        self.show_name = show_name
        self.expected_tmdb_id = expected_tmdb_id
        self.chunk_duration = 30
        self.skip_initial_duration = (
            90  # Minimal skip for title cards; ranked voting handles intro noise
        )
        self.model_name = model_name
        self.device = device or ("cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu")
        self.temp_dir = Path(tempfile.gettempdir()) / "whisper_chunks"
        self.temp_dir.mkdir(exist_ok=True)
        # Initialize subtitle cache
        self.subtitle_cache = SubtitleCache()
        # TF-IDF matcher (lazily initialized per season)
        self.tfidf_matcher = None
        # Cache for extracted audio chunks
        self.audio_chunks = {}
        # Store reference files to avoid repeated glob operations
        self.reference_files_cache = {}
        # Precomputed subtitle-vector cache (lazily loaded; False once known-absent)
        self._precomputed_manifest = None
        self._precomputed_idf = None
        # Ranked voting parameters
        self.min_vote_count = min_vote_count
        self.match_threshold = match_threshold
        # Rank+margin chunk-vote gate parameters (see select_chunk_vote).
        self.chunk_vote_floor = CHUNK_VOTE_FLOOR
        self.chunk_vote_margin_ratio = CHUNK_VOTE_MARGIN_RATIO
        # Enable/disable ranked voting (default: True for improved confidence scores)
        self.use_ranked_voting = use_ranked_voting

    def clean_text(self, text):
        """Clean transcription text to match TF-IDF vocabulary expectations."""
        return _clean_subtitle_text(text)

    @staticmethod
    def _resolve_source(mkv_file) -> str:
        """Canonical source-path form shared by chunk hash + in-memory cache key."""
        return str(Path(mkv_file).resolve())

    def _chunk_path(self, mkv_file, start_time, duration):
        """Hash resolved source path into the filename so concurrent threads don't collide."""
        src_hash = hashlib.sha1(self._resolve_source(mkv_file).encode("utf-8")).hexdigest()[:16]
        return self.temp_dir / f"chunk_{src_hash}_{start_time}_{duration}.wav"

    def extract_audio_chunk(self, mkv_file, start_time, duration=None):
        """Extract a chunk of audio from MKV file with caching."""
        duration = duration or self.chunk_duration
        # Resolve once so cache_key matches what _chunk_path hashes.
        cache_key = (self._resolve_source(mkv_file), start_time, duration)

        if cache_key in self.audio_chunks:
            return self.audio_chunks[cache_key]

        chunk_path = self._chunk_path(mkv_file, start_time, duration)
        if not chunk_path.exists():
            cmd = [
                "ffmpeg",
                "-ss",
                str(start_time),
                "-t",
                str(duration),
                "-i",
                str(mkv_file),
                "-vn",  # Disable video
                "-sn",  # Disable subtitles
                "-dn",  # Disable data streams
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-y",  # Overwrite output files without asking
                str(chunk_path),
            ]

            try:
                logger.debug(
                    f"Extracting audio segment from {mkv_file} at {start_time}s (duration: {duration}s) using FFmpeg"
                )
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)

                if result.returncode != 0:
                    error_msg = f"FFmpeg failed with return code {result.returncode}"
                    if result.stderr:
                        error_msg += f". Error: {result.stderr.strip()}"
                    logger.error(error_msg)
                    logger.debug(f"FFmpeg command: {' '.join(cmd)}")
                    raise RuntimeError(error_msg)

                # Check if the output file was actually created and has content
                if not chunk_path.exists():
                    error_msg = f"FFmpeg completed but output file was not created: {chunk_path}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

                # Check if the file has meaningful content (at least 1KB)
                if chunk_path.stat().st_size < 1024:
                    error_msg = f"Generated audio chunk is too small ({chunk_path.stat().st_size} bytes), likely corrupted"
                    logger.warning(error_msg)
                    # Don't raise an error for small files, but log the warning

                logger.debug(f"Successfully extracted {chunk_path.stat().st_size} byte audio file")

            except subprocess.TimeoutExpired as e:
                error_msg = f"FFmpeg timed out while extracting audio from {mkv_file}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e

            except Exception as e:
                error_msg = f"Failed to extract audio from {mkv_file}: {str(e)}"
                logger.error(error_msg)
                # Clean up partial file if it exists
                if chunk_path.exists():
                    try:
                        chunk_path.unlink()
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Failed to clean up partial file {chunk_path}: {cleanup_error}"
                        )
                raise RuntimeError(error_msg) from e

        chunk_path_str = str(chunk_path)
        self.audio_chunks[cache_key] = chunk_path_str
        return chunk_path_str

    def _load_precomputed_manifest(self):
        """Load and validate the precomputed-cache manifest once. Returns dict or None.

        A missing, unreadable, or version/config-mismatched manifest is treated as
        "no cache" -- the caller falls back to subtitle scraping.
        """
        if self._precomputed_manifest is not None:
            return self._precomputed_manifest or None

        self._precomputed_manifest = load_precomputed_manifest(self.cache_dir) or False
        return self._precomputed_manifest or None

    def load_precomputed_season(self, season_number):
        """Public entry point over the precomputed-cache loader.

        Exposed so the cache packager's publish-gate verification can exercise
        the real load path without reaching into a private method. Returns
        (ref_matrix, episode_codes, idf_array) when the shipped cache covers
        this show+season, otherwise None.
        """
        return self._load_precomputed_season(season_number)

    def _load_precomputed_season(self, season_number):
        """Load precomputed hashed TF-IDF vectors for this show/season.

        Returns (ref_matrix, episode_codes, idf_array) when the shipped cache
        covers the show+season, otherwise None (caller falls back to scraping).
        """
        # Use the instance-cached manifest (warms the sentinel on first call) and
        # reuse it for the coverage gate so we read+validate manifest.json at most
        # once per matcher instance instead of once per title.
        manifest = self._load_precomputed_manifest()
        # Corpus guard: a positive tmdb_id mismatch means the manifest entry is a
        # different same-named show. Bail BEFORE the stale-prune branch so we don't
        # wrongly drop a valid entry whose files are present.
        show_entry = (manifest or {}).get("shows", {}).get(self.show_name)
        entry_id = show_entry.get("tmdb_id") if show_entry else None
        if _tmdb_id_mismatch(self.expected_tmdb_id, entry_id):
            logger.warning(
                f"Precomputed corpus for '{self.show_name}' is tmdb_id {entry_id} but this "
                f"job resolved tmdb_id {self.expected_tmdb_id}; skipping precomputed (wrong show)"
            )
            return None
        if not precomputed_covers_season(
            self.cache_dir,
            self.show_name,
            season_number,
            manifest=manifest,
            expected_tmdb_id=self.expected_tmdb_id,
        ):
            # Prune the stale season in-memory so the warning fires at most once per matcher.
            if show_entry and season_number in show_entry.get("seasons", []):
                logger.warning(
                    f"Precomputed cache lists {self.show_name} S{season_number:02d} "
                    f"but its files are missing; using scraping"
                )
                show_entry["seasons"] = [s for s in show_entry["seasons"] if s != season_number]
                if not show_entry["seasons"]:
                    manifest["shows"].pop(self.show_name, None)
            return None

        precomputed_dir = self.cache_dir / "precomputed"
        show_dir = precomputed_dir / sanitize_filename(self.show_name)
        npz_path = show_dir / f"S{season_number:02d}.npz"
        index_path = show_dir / f"S{season_number:02d}.index.json"

        try:
            if self._precomputed_idf is None:
                self._precomputed_idf = np.load(precomputed_dir / "idf.npy")
            # Cache v2 ships uint16 hashed counts; apply TF-IDF here so the
            # matcher gets the same L2-normalized float32 matrix v1 read
            # directly from disk. Done once per (show, season) and cached
            # downstream — the cost is negligible vs. the ~85% size win
            # (~8 KB/episode vs. ~66 KB/episode for v1 float64 rows).
            counts = scipy_load_npz(npz_path)
            ref_matrix = apply_tfidf(counts, self._precomputed_idf)
            with open(index_path, encoding="utf-8") as fh:
                episode_codes = json.load(fh)
        except (OSError, ValueError) as e:
            logger.warning(
                f"Failed to load precomputed cache for {self.show_name} "
                f"S{season_number:02d} ({e}); using scraping"
            )
            return None

        if ref_matrix.shape[0] != len(episode_codes):
            logger.warning(
                f"Precomputed cache row/index mismatch for {self.show_name} "
                f"S{season_number:02d}; using scraping"
            )
            return None

        return ref_matrix, episode_codes, self._precomputed_idf

    def get_reference_files(self, season_number):
        """Get reference subtitle files with caching."""
        cache_key = (self.show_name, season_number)
        logger.debug(f"Reference cache key: {cache_key}")

        if cache_key in self.reference_files_cache:
            logger.debug("Returning cached reference files")
            return self.reference_files_cache[cache_key]

        reference_dir = self.cache_dir / "data" / sanitize_filename(self.show_name)
        patterns = [
            f"S{season_number:02d}E",
            f"S{season_number}E",
            f"{season_number:02d}x",
            f"{season_number}x",
        ]

        reference_files = []
        for pattern in patterns:
            # Use case-insensitive file extension matching by checking both .srt and .SRT
            srt_files = list(reference_dir.glob("*.srt")) + list(reference_dir.glob("*.SRT"))
            files = [f for f in srt_files if re.search(f"{pattern}\\d+", f.name, re.IGNORECASE)]
            reference_files.extend(files)

        # Remove duplicates while preserving order
        reference_files = list(dict.fromkeys(reference_files))
        logger.debug(f"Found {len(reference_files)} reference files for season {season_number}")
        self.reference_files_cache[cache_key] = reference_files
        return reference_files

    def transcribe_full(self, video_file) -> str | None:
        """Whisper-transcribe the entire video file, returning the cleaned text.

        Returns None when extraction or transcription fails, or when the
        returned text has fewer than 50 characters (matches the existing
        _match_full_file guard).
        """
        try:
            duration = get_video_duration(str(video_file))
        except Exception as e:
            logger.error(
                f"transcribe_full: duration lookup failed for {video_file}: {e}",
                exc_info=True,
            )
            return None

        model_config = {"type": "whisper", "name": self.model_name, "device": self.device}
        try:
            model = get_cached_model(model_config)
            audio_path = self.extract_audio_chunk(video_file, start_time=0, duration=duration)
            result = model.transcribe(audio_path)
            full = (result.get("text") or "").strip()
        except Exception as e:
            logger.warning(
                f"transcribe_full: transcription failed for {video_file}: {e}",
                exc_info=True,
            )
            return None

        if len(full) < 50:
            logger.info(f"transcribe_full: too little text ({len(full)} chars) for {video_file}")
            return None
        return full

    def _match_full_file(self, video_file, model_config, reference_files, duration):
        """
        Fallback: matching by transcribing the ENTIRE file.
        This is resource intensive but necessary if chunk matching fails.
        """
        logger.warning(f"Starting FULL FILE transcription fallback for {video_file}...")

        full_transcription = self.transcribe_full(video_file)
        if not full_transcription:
            logger.warning("Full file transcription yielded too little text.")
            return None

        logger.info(f"Full transcription complete ({len(full_transcription)} chars). Comparing...")

        best_confidence = 0
        best_match = None

        # Use TF-IDF for full-file matching too (fast and accurate)
        if self.tfidf_matcher is None or not self.tfidf_matcher.is_prepared:
            self.tfidf_matcher = TfidfMatcher()
            self.tfidf_matcher.prepare(reference_files, self.subtitle_cache)

        cleaned_transcription = self.clean_text(full_transcription)
        tfidf_results = self.tfidf_matcher.match(cleaned_transcription)

        if tfidf_results:
            best_rf, best_confidence = tfidf_results[0]
            best_match = Path(best_rf)

        logger.info(f"Fallback classification complete. Best confidence: {best_confidence:.2f}")

        if best_confidence > self.min_confidence:
            try:
                season, episode = extract_season_episode(best_match.stem)
                return {
                    "season": season,
                    "episode": episode,
                    "confidence": best_confidence,
                    "reference_file": str(best_match),
                    "matched_at": 0,
                    "method": "full_transcription",
                    "transcript": full_transcription,
                }
            except Exception as e:
                logger.error(f"Error extracting s/e from matched file {best_match}: {e}")

        return None

    def identify_episode(
        self,
        video_file,
        temp_dir,
        season_number,
        progress_callback=None,
        num_points=None,
        min_vote_count=None,
    ):
        """
        Identify episode using ranked voting with weighted confidence scoring.

        ``num_points`` overrides the number of evenly-spaced audio scan points
        (default 10); a denser scan yields more robust votes and a clearer
        score gap — used by the "deep re-match" path to disambiguate conflicts.
        ``min_vote_count`` overrides the minimum matched-chunk count required to
        accept a match (default ``self.min_vote_count``).

        Process:
        1. Extract audio chunks using sparse sampling strategy
        2. Transcribe each chunk and match against reference subtitles
        3. Accumulate votes (matches > 0.6 threshold) for each reference episode
        4. Calculate ranked voting score: weighted average of chunk confidences
        5. Select episode with highest ranked voting score (threshold: 0.15)
        6. Fallback to full-file transcription if no confident match

        Ranked voting formula:
            score = sum(confidence × weight) / sum(weights)
            where weight = chunk_duration / video_duration

        Args:
            video_file: Path to MKV file
            temp_dir: Temporary directory for audio extraction
            season_number: Season number to search
            progress_callback: Optional callable(stage: str, percent: float)

        Returns:
            Dict with season, episode, confidence, score, match_details
            None if no match found

            match_details includes:
            - matches_found: int
            - matches_rejected: int
            - total_chunks: int
            - candidate_scores: dict {episode: score}
        """
        logger.info(
            f"[Matcher] identify_episode starting for {video_file} (Season {season_number})"
        )

        # Cleanup temp files when done
        temp_files_to_remove = []

        try:
            if progress_callback:
                progress_callback("analyzing", 0.0)

            # 1. Get References - shipped precomputed vectors, else scraped SRT
            precomputed = self._load_precomputed_season(season_number)
            using_precomputed = precomputed is not None

            if using_precomputed:
                ref_matrix, ref_episode_codes, idf_array = precomputed
                reference_files = []  # no SRT files on disk in precomputed mode
                logger.info(
                    f"[Matcher] using precomputed subtitle-vector cache for "
                    f"'{self.show_name}' season {season_number} "
                    f"({len(ref_episode_codes)} episodes)"
                )
            else:
                reference_files = self.get_reference_files(season_number)
                if not reference_files:
                    reference_dir = self.cache_dir / "data" / sanitize_filename(self.show_name)
                    logger.error(
                        f"No reference subtitle files found for '{self.show_name}' "
                        f"season {season_number}. Expected directory: {reference_dir}. "
                        f"This usually means subtitle download failed. "
                        f"Check subtitle download status and retry if needed."
                    )
                    return None

            if progress_callback:
                progress_callback("analyzing", 5.0)

            # 2. Get Video Duration
            try:
                video_duration = get_video_duration(str(video_file))
            except Exception as e:
                logger.error(f"Failed to get video duration for {video_file}: {e}")
                return None

            # 3. Initialize Coverages
            coverages = {}
            if using_precomputed:
                # No SRT durations are shipped; assume reference duration == video
                # duration for penalty-free coverage (same as the ref_dur==0 fallback).
                for code in ref_episode_codes:
                    coverages[code] = MatchCoverage(code, video_duration, video_duration)
            else:
                ref_durations = {}
                for rf in reference_files:
                    try:
                        content = self.subtitle_cache.get_subtitle_content(rf)
                        ref_durations[str(rf)] = SubtitleReader.get_duration(content)
                    except Exception as e:
                        logger.warning(f"Could not get duration for reference {rf}: {e}")
                        ref_durations[str(rf)] = 0.0

                for rf in reference_files:
                    ref_dur = ref_durations.get(str(rf), video_duration)
                    # Missing ref duration -> assume video duration for penalty-free matching
                    if ref_dur == 0:
                        ref_dur = video_duration

                    ep_name = Path(rf).stem
                    coverages[str(rf)] = MatchCoverage(ep_name, ref_dur, video_duration)

            # 5. Scan Chunks - Evenly Spaced Strategy
            # Distribute scan points evenly across the episode (after skipping intro)
            # Scales automatically based on media length

            chunk_len = 30
            skip_initial = self.skip_initial_duration  # 300s - skip opening credits
            skip_final = 120  # Skip closing credits/black frames

            available_duration = video_duration - skip_initial - skip_final

            # TF-IDF matching is fast (~1ms/query) and accurate. More sample
            # points reduce false positives from commentary/alternate audio tracks.
            # Caller may request a denser scan (deep re-match) for disambiguation.
            num_points = num_points if num_points and num_points > 1 else 10

            # Calculate actual interval to evenly distribute points across available duration
            interval = available_duration / (num_points - 1)

            # Generate evenly-spaced scan points
            scan_points = []
            for i in range(num_points):
                point = int(skip_initial + i * interval)
                if point < video_duration - chunk_len:
                    scan_points.append(point)

            model_config = {
                "type": "whisper",
                "name": self.model_name,
                "device": self.device,
            }
            model = get_cached_model(model_config)

            # Rebuild the cached matcher when the reference set changes — a stale
            # precomputed-mode matcher returning codes would KeyError in path-keyed coverages.
            expected_signature: tuple = (
                ("precomputed", tuple(ref_episode_codes))
                if using_precomputed
                else ("scraping", tuple(str(rf) for rf in reference_files))
            )
            needs_rebuild = (
                self.tfidf_matcher is None
                or not self.tfidf_matcher.is_prepared
                or self.tfidf_matcher.reference_signature() != expected_signature
            )
            if needs_rebuild:
                logger.info("Initializing TF-IDF matcher for reference episodes...")
                if progress_callback:
                    progress_callback("preparing_model", 10.0)
                self.tfidf_matcher = TfidfMatcher()
                if using_precomputed:
                    self.tfidf_matcher.load_precomputed(ref_matrix, ref_episode_codes, idf_array)
                else:
                    self.tfidf_matcher.prepare(reference_files, self.subtitle_cache)

            logger.info(
                f"Scanning {len(scan_points)} chunks using {model_config['name']} + TF-IDF matching "
                f"(~{interval:.0f}s intervals from {skip_initial}s to {video_duration - skip_final}s)"
            )
            logger.debug(
                f"Scan points: {scan_points[:5]}... {scan_points[-3:]} (showing first 5 and last 3)"
            )

            matches_found_count = 0  # Total matched chunks
            matches_rejected_count = 0  # Total rejected chunks

            for i, start_time in enumerate(scan_points, 1):
                # Calculate progress: 10% to 90% allocated for scanning
                scan_percent = 10.0 + (i / len(scan_points)) * 80.0

                try:
                    audio_path = self.extract_audio_chunk(
                        video_file, start_time, duration=chunk_len
                    )
                    temp_files_to_remove.append(audio_path)  # Track for cleanup

                    # Transcribe
                    result = model.transcribe(audio_path)
                    text = result["text"]

                    if len(text) < 10:
                        logger.debug(
                            f"Chunk {i}/{len(scan_points)} @ {start_time}s: transcription too short ({len(text)} chars), skipping"
                        )
                        matches_rejected_count += 1
                        if progress_callback:
                            progress_callback("transcribing", scan_percent)
                        continue

                    logger.debug(
                        f"Chunk {i}/{len(scan_points)} @ {start_time}s: transcribed {len(text)} chars, matching via TF-IDF..."
                    )

                    # TF-IDF cosine similarity against full episode texts. The
                    # chunk-vs-full-episode cosine is structurally low even for a
                    # perfect match, so vote on rank+margin rather than an absolute
                    # cosine gate (see select_chunk_vote). One vote per chunk: its
                    # clearly-leading top episode, or none.
                    tfidf_results = self.tfidf_matcher.match(text)
                    vote = select_chunk_vote(
                        tfidf_results,
                        floor=self.chunk_vote_floor,
                        ratio=self.chunk_vote_margin_ratio,
                    )

                    if vote is not None:
                        rf_str, score = vote
                        coverages[rf_str].add_match(start_time, chunk_len, score)
                        matches_found_count += 1
                        logger.debug(
                            f"  {Path(rf_str).stem}: VOTE @ video={start_time}s "
                            f"(cosine={score:.3f}, clear margin over runner-up)"
                        )
                    else:
                        matches_rejected_count += 1
                        if tfidf_results:
                            top_ref, top_score = tfidf_results[0]
                            runner = tfidf_results[1][1] if len(tfidf_results) > 1 else 0.0
                            logger.debug(
                                f"Chunk {i}/{len(scan_points)} @ {start_time}s: no clear vote "
                                f"(top {Path(top_ref).stem}={top_score:.3f}, "
                                f"runner-up={runner:.3f})"
                            )

                except Exception as e:
                    logger.warning(
                        f"Error processing chunk {i}/{len(scan_points)} at {start_time}s: {e}"
                    )
                    matches_rejected_count += 1
                    if progress_callback:
                        progress_callback("transcribing", scan_percent)
                    continue

                # Build interim vote standings after each chunk
                if progress_callback:
                    interim_standings = []
                    for _rf_str_cov, cov in coverages.items():
                        if cov.matched_chunks:
                            ep_season, ep_episode = extract_season_episode(cov.episode_name)
                            interim_standings.append(
                                {
                                    "episode": f"S{ep_season:02d}E{ep_episode:02d}",
                                    "score": cov.ranked_voting_score,
                                    "vote_count": len(cov.matched_chunks),
                                    "target_votes": len(scan_points),
                                }
                            )
                    interim_standings.sort(key=lambda x: x["score"], reverse=True)
                    progress_callback("matching", scan_percent, interim_standings[:5])

            logger.info(
                f"Sparse sampling complete: {matches_found_count} matched / {matches_rejected_count} rejected chunks"
            )

            # 6. Evaluate Results using Ranked Voting
            # Each reference episode accumulates weighted votes from matched chunks.
            # Winner: highest weighted consensus score (not just highest single match).
            best_score = 0
            best_match = None

            results_summary = []

            for rf_str, cov in coverages.items():
                # Select scoring method based on configuration
                if self.use_ranked_voting:
                    score = cov.ranked_voting_score
                else:
                    score = cov.weighted_score  # Legacy method for comparison

                season, episode = extract_season_episode(cov.episode_name)

                match_info = {
                    "episode": f"S{season}E{episode}",
                    "score": score,
                    "ranked_score": cov.ranked_voting_score,
                    "avg_conf": cov.avg_confidence,
                    "file_cov": cov.file_coverage,
                    "vote_count": len(cov.matched_chunks),
                    "target_votes": len(scan_points),
                    "total_weight": cov.total_vote_weight,
                }
                results_summary.append(match_info)

                if score > best_score:
                    best_score = score
                    best_match = {
                        "season": season,
                        "episode": episode,
                        "confidence": score,  # Use ranked voting score as confidence
                        "score": score,
                        "reference_file": rf_str,
                        "matched_at": cov.matched_chunks[0]["start"] if cov.matched_chunks else 0,
                        "match_details": match_info,
                        "voting_details": cov.get_voting_details(),
                    }

            # Prepare detailed stats for return
            match_stats = {
                "matches_found": matches_found_count,
                "matches_rejected": matches_rejected_count,
                "total_chunks": len(scan_points),
            }

            if best_match:
                # Merge stats into match_details before calibration so the helper
                # operates on the full per-winner detail dict.
                best_match["match_details"].update(match_stats)

                # Sort candidates so the calibrator sees top1/top2 in order, then
                # translate the raw signals into a 0-1 reviewer-facing confidence
                # and build the runner-up leaderboard. This sets
                # best_match["confidence"] (calibrated, -> DiscTitle.match_confidence)
                # while leaving best_match["score"] raw for the accept-gate and
                # conflict resolution. All candidates (incl. the winner) appear in
                # runner_ups so the UI can show the full leaderboard. See
                # calibrate_confidence for rationale.
                results_summary.sort(key=lambda x: x["score"], reverse=True)
                _attach_calibrated_confidence(best_match, results_summary, video_duration)

                score_gap = best_match["match_details"].get("score_gap", 0.0)

                # Log top candidates with voting + calibration analysis
                voting_method = "ranked voting" if self.use_ranked_voting else "weighted score"
                logger.info(f"{voting_method.capitalize()} results for {video_file.name}:")
                logger.info(
                    f"  Calibrated confidence: {best_match['confidence']:.3f} "
                    f"(raw score={best_match['score']:.3f}, "
                    f"score_gap={score_gap:.4f} "
                    f"{'decisive' if score_gap > 0.01 else 'LOW-uncertain'})"
                )

                for i, result in enumerate(results_summary[:5], 1):
                    logger.info(
                        f"  {i}. {result['episode']}: "
                        f"score={result['score']:.3f}, "
                        f"votes={result['vote_count']}, "
                        f"avg_conf={result['avg_conf']:.3f}, "
                        f"coverage={result['file_cov']:.1%}, "
                        f"total_weight={result['total_weight']:.4f}"
                    )

                effective_min_votes = (
                    min_vote_count if min_vote_count is not None else self.min_vote_count
                )

                if best_match["score"] > self.match_threshold:
                    vote_count = best_match["match_details"]["vote_count"]

                    logger.info(
                        f"Best match evaluation: "
                        f"score {best_match['score']:.3f} vs threshold {self.match_threshold}, "
                        f"votes {vote_count} vs minimum {effective_min_votes}"
                    )

                    if vote_count < effective_min_votes:
                        logger.warning(
                            f"⚠ Match rejected: insufficient evidence. "
                            f"Episode: {best_match['match_details']['episode']}, "
                            f"score: {best_match['score']:.3f}, "
                            f"votes: {vote_count}/{effective_min_votes}, "
                            f"coverage: {best_match['match_details']['file_cov']:.1%}, "
                            f"matched_at: {best_match['matched_at']}s"
                        )
                        # Fall through to fallback
                    else:
                        logger.info(
                            f"Ranked voting match: S{best_match['season']:02d}E{best_match['episode']:02d} "
                            f"(score: {best_match['score']:.3f}, votes: {vote_count})"
                        )
                        return best_match
            else:
                # Zero chunks cleared the rank+margin vote gate. Crucially, do NOT
                # early-return episode=None here: the chunk cosine scale is
                # structurally low, while the full-file fallback compares
                # whole-vs-whole on a higher, properly-calibrated scale. Falling
                # through reaches it. (Historically the early return made that
                # fallback dead code in exactly the total-miss case it exists for.)
                logger.warning(
                    f"No chunks cleared the vote gate for {video_file}; "
                    f"attempting full-file fallback"
                )

            # --- FALLBACK ---
            # Full-file fallback when chunk voting produced no acceptable match
            # (no votes at all, score below threshold, or too few votes).
            logger.info(
                f"Ranked voting matching failed "
                f"(best score: {best_score:.3f} < {self.match_threshold} threshold "
                f"or insufficient votes). "
                f"Attempting FULL FILE fallback..."
            )
            match = self._match_full_file(video_file, model_config, reference_files, video_duration)

            if match:
                # Intentional exception to calibration: the full-file fallback
                # compares whole transcription vs whole subtitle ("bucket vs
                # bucket"), so its cosine lands on a higher scale than chunk
                # cosines and is already > min_confidence by construction. We pass
                # it through uncalibrated; curator's review gate reads it directly.
                match["score"] = match[
                    "confidence"
                ]  # Full file score is just confidence (coverage=1.0)
                match["match_details"] = {
                    "method": "full_transcription",
                    "score": match["confidence"],
                }
                return match

            # Nothing matched, even via the fallback. Return the no-episode result
            # with scan stats preserved (not a bare None) so the UI/diagnostics
            # show what was attempted.
            logger.warning(f"No episode matches found for {video_file}")
            return {
                "season": season_number,
                "episode": None,
                "confidence": 0.0,
                "score": 0.0,
                "match_details": match_stats,
                "runner_ups": [],
            }

        except Exception as e:
            logger.error(
                f"Unexpected error during episode identification for {video_file}: {e}",
                exc_info=True,
            )
            return None

        finally:
            # Cleanup temp files
            for p in temp_files_to_remove:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
            # Also clean cached chunks
            self.audio_chunks.clear()


def get_video_duration(video_file, _retries: int = 6, _retry_delay: float = 5.0):
    """Get video duration using ffprobe, with retry on Windows file-lock errors.

    Retries up to `_retries` times with `_retry_delay` seconds between attempts,
    to handle the window where MakeMKV has finished writing but still holds the
    file handle open (causing PermissionError / EACCES in ffprobe on Windows).
    """
    last_error = None
    for attempt in range(1, _retries + 1):
        try:
            logger.debug(
                f"Getting duration for video file: {video_file} (attempt {attempt}/{_retries})"
            )
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_file),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                error_msg = f"ffprobe failed with return code {result.returncode}"
                if result.stderr:
                    error_msg += f". Error: {result.stderr.strip()}"
                # Retry on permission-related errors (Windows file lock)
                if "Permission denied" in (result.stderr or "") and attempt < _retries:
                    logger.warning(
                        f"[MATCH] ffprobe permission denied for {video_file}, "
                        f"retrying in {_retry_delay}s (attempt {attempt}/{_retries})..."
                    )
                    time.sleep(_retry_delay)
                    last_error = RuntimeError(error_msg)
                    continue
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            duration_str = result.stdout.strip()
            if not duration_str:
                raise RuntimeError("ffprobe returned empty duration")

            duration = float(duration_str)
            if duration <= 0:
                raise RuntimeError(f"Invalid duration: {duration}")

            result_duration = int(np.ceil(duration))
            logger.debug(f"Video duration: {result_duration} seconds")
            return result_duration

        except subprocess.TimeoutExpired as e:
            error_msg = f"ffprobe timed out while getting duration for {video_file}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except ValueError as e:
            error_msg = f"Failed to parse duration from ffprobe output for {video_file}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except RuntimeError:
            raise
        except Exception as e:
            error_msg = f"Unexpected error getting video duration for {video_file}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    # All retries exhausted
    raise last_error or RuntimeError(
        f"Failed to get duration for {video_file} after {_retries} attempts"
    )


def detect_file_encoding(file_path):
    """
    Detect the encoding of a file using chardet.

    Args:
        file_path (str or Path): Path to the file

    Returns:
        str: Detected encoding, defaults to 'utf-8' if detection fails
    """
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(min(1024 * 1024, Path(file_path).stat().st_size))  # Read up to 1MB
        result = chardet.detect(raw_data)
        encoding = result["encoding"]
        confidence = result["confidence"]

        logger.debug(
            f"Detected encoding {encoding} with {confidence:.2%} confidence for {file_path}"
        )
        return encoding if encoding else "utf-8"
    except Exception as e:
        logger.warning(f"Error detecting encoding for {file_path}: {e}")
        return "utf-8"


@lru_cache(maxsize=100)
def read_file_with_fallback(file_path, encodings=None):
    """
    Read a file trying multiple encodings in order of preference.

    Args:
        file_path (str or Path): Path to the file
        encodings (list): List of encodings to try, defaults to common subtitle encodings

    Returns:
        str: File contents

    Raises:
        ValueError: If file cannot be read with any encoding
    """
    if encodings is None:
        # First try detected encoding, then fallback to common subtitle encodings
        detected = detect_file_encoding(file_path)
        encodings = [detected, "utf-8", "latin-1", "cp1252", "iso-8859-1"]

    file_path = Path(file_path)
    errors = []

    for encoding in encodings:
        try:
            with open(file_path, encoding=encoding) as f:
                content = f.read()
            logger.debug(f"Successfully read {file_path} using {encoding} encoding")
            return content
        except UnicodeDecodeError as e:
            errors.append(f"{encoding}: {str(e)}")
            continue

    error_msg = f"Failed to read {file_path} with any encoding. Errors:\n" + "\n".join(errors)
    logger.error(error_msg)
    raise ValueError(error_msg)


class SubtitleReader:
    """Helper class for reading and parsing subtitle files."""

    @staticmethod
    def parse_timestamp(timestamp):
        """Parse SRT timestamp into seconds."""
        hours, minutes, seconds = timestamp.replace(",", ".").split(":")
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    @staticmethod
    def read_srt_file(file_path):
        """
        Read an SRT file and return its contents with robust encoding handling.

        Args:
            file_path (str or Path): Path to the SRT file

        Returns:
            str: Contents of the SRT file
        """
        return read_file_with_fallback(file_path)

    @staticmethod
    def extract_subtitle_chunk(content, start_time, end_time):
        """
        Extract subtitle text for a specific time window.

        Args:
            content (str): Full SRT file content
            start_time (float): Chunk start time in seconds
            end_time (float): Chunk end time in seconds

        Returns:
            list: List of subtitle texts within the time window
        """
        text_lines = []

        for block in content.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines) < 3 or "-->" not in lines[1]:
                continue

            try:
                timestamp = lines[1]
                time_parts = timestamp.split(" --> ")
                start_stamp = time_parts[0].strip()
                end_stamp = time_parts[1].strip()

                subtitle_start = SubtitleReader.parse_timestamp(start_stamp)
                subtitle_end = SubtitleReader.parse_timestamp(end_stamp)

                # Check if this subtitle overlaps with our chunk
                if subtitle_end >= start_time and subtitle_start <= end_time:
                    text = " ".join(lines[2:])

                    # Skip watermark/ad blocks (URLs, credit lines, etc.)
                    if _is_watermark_block(text, lines, subtitle_start):
                        logger.debug(
                            f"Filtered watermark/ad block at {subtitle_start:.1f}s: {text[:80]}"
                        )
                        continue

                    text_lines.append(text)

            except (IndexError, ValueError) as e:
                logger.warning(f"Error parsing subtitle block: {e}")
                continue

        return text_lines

    @staticmethod
    def get_duration(content):
        """
        Get the duration of the subtitle file (max end timestamp across all blocks).

        Uses max() instead of last-block because some subtitle files have
        watermark/ad blocks appended at the end with timestamps near 0:00,
        which would incorrectly report the duration as ~2 seconds.

        Args:
            content (str): Full SRT file content

        Returns:
            float: Duration in seconds, or 0 if parsing fails
        """
        try:
            blocks = content.strip().split("\n\n")
            if not blocks:
                return 0.0

            max_end = 0.0
            for block in blocks:
                lines = block.split("\n")
                if len(lines) >= 2 and "-->" in lines[1]:
                    try:
                        time_parts = lines[1].split(" --> ")
                        end_stamp = time_parts[1].strip()
                        end_time = SubtitleReader.parse_timestamp(end_stamp)
                        if end_time > max_end:
                            max_end = end_time
                    except (IndexError, ValueError):
                        continue

            return max_end
        except Exception as e:
            logger.warning(f"Error getting duration from subtitle content: {e}")
            return 0.0


# Note: Model caching is now handled by the ASR abstraction layer in asr_models.py
