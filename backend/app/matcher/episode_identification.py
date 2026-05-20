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

console = Console()


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
    text = re.sub(r"\[.*?\]|<.*?>", "", text)  # remove [tags] and <tags>
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
    ):
        self.cache_dir = Path(cache_dir)
        self.min_confidence = min_confidence
        self.show_name = show_name
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
        # Enable/disable ranked voting (default: True for improved confidence scores)
        self.use_ranked_voting = use_ranked_voting

    def clean_text(self, text):
        """Clean transcription text to match TF-IDF vocabulary expectations."""
        return _clean_subtitle_text(text)

    def extract_audio_chunk(self, mkv_file, start_time, duration=None):
        """Extract a chunk of audio from MKV file with caching."""
        duration = duration or self.chunk_duration
        cache_key = (str(mkv_file), start_time, duration)

        if cache_key in self.audio_chunks:
            return self.audio_chunks[cache_key]

        chunk_path = self.temp_dir / f"chunk_{start_time}_{duration}.wav"
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

        from app.matcher.vectorizer_config import (
            CACHE_FORMAT_VERSION,
            vectorizer_config_hash,
        )

        manifest_path = self.cache_dir / "precomputed" / "manifest.json"
        if not manifest_path.exists():
            self._precomputed_manifest = False
            return None

        try:
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Precomputed cache manifest unreadable ({e}); using scraping")
            self._precomputed_manifest = False
            return None

        if manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
            logger.warning(
                f"Precomputed cache format mismatch "
                f"(manifest={manifest.get('cache_format_version')}, code={CACHE_FORMAT_VERSION}); "
                f"ignoring cache"
            )
            self._precomputed_manifest = False
            return None
        if manifest.get("vectorizer_config_hash") != vectorizer_config_hash():
            logger.warning("Precomputed cache vectorizer-config mismatch; ignoring cache")
            self._precomputed_manifest = False
            return None

        self._precomputed_manifest = manifest
        return manifest

    def _load_precomputed_season(self, season_number):
        """Load precomputed hashed TF-IDF vectors for this show/season.

        Returns (ref_matrix, episode_codes, idf_array) when the shipped cache
        covers the show+season, otherwise None (caller falls back to scraping).
        """
        manifest = self._load_precomputed_manifest()
        if not manifest:
            return None

        show_entry = manifest.get("shows", {}).get(self.show_name)
        if not show_entry or season_number not in show_entry.get("seasons", []):
            return None

        precomputed_dir = self.cache_dir / "precomputed"
        show_dir = precomputed_dir / sanitize_filename(self.show_name)
        npz_path = show_dir / f"S{season_number:02d}.npz"
        index_path = show_dir / f"S{season_number:02d}.index.json"
        if not npz_path.exists() or not index_path.exists():
            logger.warning(
                f"Precomputed cache lists {self.show_name} S{season_number:02d} "
                f"but its files are missing; using scraping"
            )
            return None

        try:
            from app.matcher.vectorizer_config import apply_tfidf

            if self._precomputed_idf is None:
                self._precomputed_idf = np.load(precomputed_dir / "idf.npy")
            # Cache v2 ships uint16 hashed counts; apply TF-IDF here so the
            # matcher gets the same L2-normalized float32 matrix v1 read
            # directly from disk. Done once per (show, season) and cached
            # downstream — the cost is negligible vs. the 57% size win.
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

    def _match_full_file(self, video_file, model_config, reference_files, duration):
        """
        Fallback: matching by transcribing the ENTIRE file.
        This is resource intensive but necessary if chunk matching fails.
        """
        logger.warning(f"Starting FULL FILE transcription fallback for {video_file}...")

        # Handle backward compatibility for string model names
        if isinstance(model_config, str):
            model_config = {
                "type": "whisper",
                "name": model_config,
                "device": self.device,
            }
        elif isinstance(model_config, dict):
            if "device" not in model_config:
                model_config = model_config.copy()
                model_config["device"] = self.device

        # Use cached model
        model = get_cached_model(model_config)

        try:
            # Extract the FULL audio
            # We use a slightly different path logic handled by extract_audio_chunk with duration
            audio_path = self.extract_audio_chunk(video_file, start_time=0, duration=duration)

            logger.info(f"Transcribing full audio ({duration}s)...")
            result = model.transcribe(audio_path)
            full_transcription = result["text"]

            if not full_transcription or len(full_transcription) < 50:
                logger.warning("Full file transcription yielded too little text.")
                return None

            logger.info(
                f"Full transcription complete ({len(full_transcription)} chars). Comparing..."
            )

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
                    }
                except Exception as e:
                    logger.error(f"Error extracting s/e from matched file {best_match}: {e}")

            return None

        except Exception as e:
            logger.error(f"Error during full file fallback: {e}", exc_info=True)
            return None

    def identify_episode(self, video_file, temp_dir, season_number, progress_callback=None):
        """
        Identify episode using ranked voting with weighted confidence scoring.

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
            num_points = 10

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

            # Initialize TF-IDF matcher for this season (lazy, once per set of references)
            if self.tfidf_matcher is None or not self.tfidf_matcher.is_prepared:
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

                    # TF-IDF cosine similarity against full episode texts
                    tfidf_results = self.tfidf_matcher.match(text)
                    chunk_matches = 0
                    for rf_str, score in tfidf_results:
                        if score > 0.15:  # TF-IDF cosine threshold (range ~0.05-0.5)
                            coverages[rf_str].add_match(start_time, chunk_len, score)
                            chunk_matches += 1
                            logger.debug(
                                f"  {Path(rf_str).stem}: MATCH @ video={start_time}s "
                                f"(cosine={score:.3f})"
                            )
                        elif score > 0.08:  # Log near-misses
                            logger.debug(
                                f"  {Path(rf_str).stem}: near-miss @ video={start_time}s "
                                f"(cosine={score:.3f})"
                            )

                    if chunk_matches > 0:
                        matches_found_count += 1
                    else:
                        logger.debug(
                            f"Chunk {i}/{len(scan_points)} @ {start_time}s: no matches found (best cosine < 0.15)"
                        )
                        matches_rejected_count += 1

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

            if not best_match:
                logger.warning(f"No episode matches found for {video_file}")
                return {
                    "season": season_number,
                    "episode": None,
                    "confidence": 0.0,
                    "score": 0.0,
                    "match_details": match_stats,
                    "runner_ups": [],
                }

            # Add ALL candidates (including the best match) so the UI can display
            # the full voting leaderboard. Previously excluded the best match, but
            # for decisive matches with a single candidate this left runner_ups empty.
            runner_ups = []
            if results_summary:
                results_summary.sort(key=lambda x: x["score"], reverse=True)
                runner_ups = [
                    {
                        "episode": r["episode"],
                        "score": r["score"],
                        "vote_count": r["vote_count"],
                        "target_votes": len(scan_points),
                    }
                    for r in results_summary
                    if r["score"] > 0
                ][:5]
                best_match["runner_ups"] = runner_ups

            # Merge stats into match_details
            best_match["match_details"].update(match_stats)
            best_match["match_details"]["runner_ups"] = runner_ups

            # Log top candidates with voting details and score gap analysis
            voting_method = "ranked voting" if self.use_ranked_voting else "weighted score"
            logger.info(f"{voting_method.capitalize()} results for {video_file.name}:")

            # Compute score gap between top-1 and top-2 (strong correctness signal)
            score_gap = 0.0
            if len(results_summary) >= 2:
                score_gap = results_summary[0]["score"] - results_summary[1]["score"]
                logger.info(
                    f"  Score gap (top1-top2): {score_gap:.4f} {'(decisive)' if score_gap > 0.01 else '(LOW - uncertain match)'}"
                )
            elif len(results_summary) == 1:
                # Only one candidate, gap equals its score
                score_gap = results_summary[0]["score"]

            # Add score_gap to match_details for UI transparency
            if best_match and best_match.get("match_details"):
                best_match["match_details"]["score_gap"] = score_gap

            for i, result in enumerate(results_summary[:5], 1):
                logger.info(
                    f"  {i}. {result['episode']}: "
                    f"score={result['score']:.3f}, "
                    f"votes={result['vote_count']}, "
                    f"avg_conf={result['avg_conf']:.3f}, "
                    f"coverage={result['file_cov']:.1%}, "
                    f"total_weight={result['total_weight']:.4f}"
                )

            if best_match and best_match["score"] > self.match_threshold:
                vote_count = best_match["match_details"]["vote_count"]

                logger.info(
                    f"Best match evaluation: "
                    f"score {best_match['score']:.3f} vs threshold {self.match_threshold}, "
                    f"votes {vote_count} vs minimum {self.min_vote_count}"
                )

                if vote_count < self.min_vote_count:
                    logger.warning(
                        f"⚠ Match rejected: insufficient evidence. "
                        f"Episode: {best_match['match_details']['episode']}, "
                        f"score: {best_match['score']:.3f}, "
                        f"votes: {vote_count}/{self.min_vote_count}, "
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

            # --- FALLBACK ---
            # Standard full file fallback if no good match
            logger.info(
                f"Ranked voting matching failed "
                f"(best score: {best_score:.3f} < {self.match_threshold} threshold "
                f"or insufficient votes). "
                f"Attempting FULL FILE fallback..."
            )
            match = self._match_full_file(video_file, model_config, reference_files, video_duration)

            if match:
                match["score"] = match[
                    "confidence"
                ]  # Full file score is just confidence (coverage=1.0)
                match["match_details"] = {
                    "method": "full_transcription",
                    "score": match["confidence"],
                }
                return match

            return None

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
