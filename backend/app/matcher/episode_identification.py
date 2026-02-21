import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import chardet
import ctranslate2
import numpy as np
from loguru import logger
from rich import print
from rich.console import Console
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine_similarity

from app.matcher.asr_models import get_cached_model
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
        self.ref_file_order = []  # ordered list of reference file paths
        self._prepared = False

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

    def load_reference_chunk(self, srt_file, chunk_idx):
        """
        Load reference subtitles for a specific time chunk with caching.

        Args:
            srt_file (str or Path): Path to the SRT file
            chunk_idx (int): Index of the chunk to load

        Returns:
            str: Combined text from the subtitle chunk
        """
        try:
            # Apply the same offset as in _try_match_with_model
            chunk_start = self.skip_initial_duration + (chunk_idx * self.chunk_duration)
            chunk_end = chunk_start + self.chunk_duration

            return self.subtitle_cache.get_chunk(srt_file, chunk_idx, chunk_start, chunk_end)

        except Exception as e:
            logger.error(f"Error loading reference chunk from {srt_file}: {e}")
            return ""

    def get_reference_files(self, season_number):
        """Get reference subtitle files with caching."""
        cache_key = (self.show_name, season_number)
        logger.debug(f"Reference cache key: {cache_key}")

        if cache_key in self.reference_files_cache:
            logger.debug("Returning cached reference files")
            return self.reference_files_cache[cache_key]

        reference_dir = self.cache_dir / "data" / self.show_name
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

    def _try_match_with_model(self, video_file, model_config, max_duration, reference_files):
        """
        Attempt to match using specified model, checking multiple chunks starting from skip_initial_duration
        and continuing up to max_duration.

        Args:
            video_file: Path to the video file
            model_config: Dictionary with ASR model configuration or string for backward compatibility
            max_duration: Maximum duration in seconds to check
            reference_files: List of reference subtitle files
        """
        # Handle backward compatibility for string model names
        if isinstance(model_config, str):
            # Convert old Whisper model names to new format
            model_config = {
                "type": "whisper",
                "name": model_config,
                "device": self.device,
            }
        elif isinstance(model_config, dict):
            # Ensure device is set if not specified
            if "device" not in model_config:
                model_config = model_config.copy()
                model_config["device"] = self.device

        # Use cached model
        model = get_cached_model(model_config)

        # Calculate number of chunks to check
        num_chunks = min(
            max_duration // self.chunk_duration, 10
        )  # Limit to 10 chunks for initial check

        # Pre-load all reference chunks for the chunks we'll check
        for chunk_idx in range(num_chunks):
            for ref_file in reference_files:
                self.load_reference_chunk(ref_file, chunk_idx)

        for chunk_idx in range(num_chunks):
            # Start at self.skip_initial_duration and check subsequent chunks
            start_time = self.skip_initial_duration + (chunk_idx * self.chunk_duration)
            model_name = (
                model_config.get("name", "unknown")
                if isinstance(model_config, dict)
                else model_config
            )
            logger.debug(f"Trying {model_name} model at {start_time} seconds")

            try:
                audio_path = self.extract_audio_chunk(video_file, start_time)
                logger.debug(f"Extracted audio chunk: {audio_path}")
            except RuntimeError as e:
                logger.warning(f"Failed to extract audio chunk at {start_time}s: {e}")
                continue  # Skip this chunk and try the next one
            except Exception as e:
                logger.error(f"Unexpected error extracting audio chunk at {start_time}s: {e}")
                continue  # Skip this chunk and try the next one

            try:
                logger.debug(
                    f"[Matcher] Transcribing audio chunk at {start_time}s with model {model_name}"
                )
                result = model.transcribe(audio_path)
                logger.debug(f"[Matcher] Transcription complete at {start_time}s")
            except Exception as e:
                logger.error(f"ASR transcription failed for chunk at {start_time}s: {e}")
                continue  # Skip this chunk and try the next one

            chunk_text = result["text"]
            logger.debug(f"Transcription result: {chunk_text} ({len(chunk_text)} characters)")
            if len(chunk_text) < 10:
                logger.debug(
                    f"Transcription result too short: {chunk_text} ({len(chunk_text)} characters)"
                )
                continue
            best_confidence = 0
            best_match = None

            # Compare with reference chunks
            # Compare with reference chunks
            for ref_file in reference_files:
                ref_text = self.load_reference_chunk(ref_file, chunk_idx)

                # Use model's internal scoring logic
                confidence = model.calculate_match_score(chunk_text, ref_text)

                if confidence > best_confidence:
                    logger.debug(f"New best confidence: {confidence} for {ref_file}")
                    best_confidence = confidence
                    best_match = Path(ref_file)

                if confidence > self.min_confidence:
                    print(f"Matched with {best_match} (confidence: {best_confidence:.2f})")
                    try:
                        season, episode = extract_season_episode(best_match.stem)
                    except Exception as e:
                        print(f"Error extracting season/episode: {e}")
                        continue
                    print(
                        f"Season: {season}, Episode: {episode} (confidence: {best_confidence:.2f})"
                    )
                    if season and episode:
                        return {
                            "season": season,
                            "episode": episode,
                            "confidence": best_confidence,
                            "reference_file": str(best_match),
                            "matched_at": start_time,
                        }

            logger.info(
                f"No match found at {start_time} seconds (best confidence: {best_confidence:.2f})"
            )
        return None

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

            # 1. Get Reference Files
            reference_files = self.get_reference_files(season_number)
            if not reference_files:
                reference_dir = self.cache_dir / "data" / self.show_name
                logger.error(
                    f"No reference subtitle files found for '{self.show_name}' season {season_number}. "
                    f"Expected directory: {reference_dir}. "
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

            # 3. Get Reference Durations
            ref_durations = {}
            for rf in reference_files:
                try:
                    content = self.subtitle_cache.get_subtitle_content(rf)
                    ref_durations[str(rf)] = SubtitleReader.get_duration(content)
                except Exception as e:
                    logger.warning(f"Could not get duration for reference {rf}: {e}")
                    ref_durations[str(rf)] = 0.0

            # 4. Initialize Coverages
            coverages = {}
            for rf in reference_files:
                ref_dur = ref_durations.get(str(rf), video_duration)
                # If ref duration is missing, assume same as video for penalty-free matching (fallback)
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

            # TF-IDF matching is fast (~1ms/query) and accurate, so we only
            # need a few evenly-spaced samples to identify the episode.
            num_points = 5

            # Calculate actual interval to evenly distribute points across available duration
            if num_points > 1:
                interval = available_duration / (num_points - 1)
            else:
                interval = 0

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
                self.tfidf_matcher.prepare(reference_files, self.subtitle_cache)

            logger.info(
                f"Scanning {len(scan_points)} chunks using {model_config['name']} + TF-IDF matching "
                f"(~{interval:.0f}s intervals from {skip_initial}s to {video_duration - skip_final}s)"
            )
            logger.debug(
                f"Scan points: {scan_points[:5]}... {scan_points[-3:]} (showing first 5 and last 3)"
            )

            matches_found_count = 0  # Total matched chunks
            matches_found = 0
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
                        matches_found += chunk_matches
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
            results_summary.sort(key=lambda x: x["score"], reverse=True)
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
                    import time as _time

                    _time.sleep(_retry_delay)
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
