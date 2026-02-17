"""
Evaluate different episode matching methods using cached transcription data.

This script loads cached transcriptions and tests multiple matching algorithms
to compare accuracy, precision, recall, and performance.

Usage:
    # Run all matching methods
    uv run python -m app.matcher.scripts.evaluate_matching_methods

    # Run specific methods only
    uv run python -m app.matcher.scripts.evaluate_matching_methods \
        --methods ranked_voting sparse_sampling

    # Use specific transcription cache
    uv run python -m app.matcher.scripts.evaluate_matching_methods \
        --transcriptions investigation_output/transcriptions/
"""

import argparse
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

from app.matcher.core.utils import clean_text


@dataclass
class ChunkData:
    """Data for a single audio chunk."""

    chunk_index: int
    start_time: float
    duration: float
    end_time: float
    coverage_weight: float
    raw_text: str
    cleaned_text: str
    language: str
    segments: list[dict[str, Any]]


@dataclass
class FileData:
    """Complete transcription data for a video file."""

    file_path: str
    show_name: str
    season: int
    episode: int
    video_duration: float
    chunks: list[ChunkData]


@dataclass
class ReferenceChunk:
    """Reference subtitle chunk."""

    start_time: float
    duration: float
    text: str


@dataclass
class ReferenceEpisode:
    """Reference subtitle data for an episode."""

    episode: int
    file_path: str
    duration: float
    full_text: str
    chunks: list[ReferenceChunk]


@dataclass
class MatchResult:
    """Result of a matching attempt."""

    method_name: str
    file_path: str
    show_name: str
    season: int
    episode_actual: int
    episode_matched: int | None
    confidence: float
    correct: bool
    processing_time: float
    chunks_used: int
    total_chunks: int
    weighted_score: float
    fallback_used: bool
    vote_breakdown: dict[int, dict[str, Any]] | None = None


class MatchingMethod:
    """Base class for matching methods."""

    def __init__(self, name: str):
        self.name = name

    def calculate_similarity(
        self, text1: str, text2: str, weights: tuple[float, float] = (0.7, 0.3)
    ) -> float:
        """
        Calculate similarity between two texts using RapidFuzz.

        Args:
            text1: First text (cleaned)
            text2: Second text (cleaned)
            weights: (token_sort_weight, partial_weight) - must sum to 1.0
        """
        if not text1 or not text2:
            return 0.0

        token_sort_score = fuzz.token_sort_ratio(text1, text2) / 100.0
        partial_score = fuzz.partial_ratio(text1, text2) / 100.0

        return weights[0] * token_sort_score + weights[1] * partial_score

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        """Match a file to reference episodes. Must be implemented by subclasses."""
        raise NotImplementedError


class SparseMethod(MatchingMethod):
    """Current production sparse sampling method (baseline)."""

    def __init__(self):
        super().__init__("sparse_sampling")

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        start_time = time.time()

        # Simulate sparse sampling: use only chunks at strategic positions
        # Dense (300-900s): every 30s, Sparse (900s+): every 150s
        duration = file_data.video_duration
        sparse_chunks = []

        if duration <= 900:
            # Dense: every chunk (but in production this would be every 30s from skip_initial)
            # For fair comparison, sample every 30s
            sparse_chunks = [c for c in file_data.chunks]
        else:
            # Sparse: every 150s
            sparse_chunks = [c for c in file_data.chunks if c.chunk_index % 5 == 0]

        # Track matches
        episode_scores = defaultdict(lambda: {"total_confidence": 0.0, "match_count": 0})

        chunks_used = 0
        for chunk in sparse_chunks:
            if not chunk.cleaned_text:
                continue

            best_match_score = 0.0
            best_match_episode = None

            # Find best matching reference chunk
            for ref_episode in references:
                for ref_chunk in ref_episode.chunks:
                    if not ref_chunk.text:
                        continue

                    # Calculate similarity
                    score = self.calculate_similarity(chunk.cleaned_text, ref_chunk.text)

                    if score > best_match_score:
                        best_match_score = score
                        best_match_episode = ref_episode.episode

            # Record match if above threshold
            if best_match_score > 0.6 and best_match_episode is not None:
                episode_scores[best_match_episode]["total_confidence"] += best_match_score
                episode_scores[best_match_episode]["match_count"] += 1
                chunks_used += 1

            # Early exit on high confidence match (current production behavior)
            if best_match_score > 0.92:
                break

        # Calculate weighted scores
        best_episode = None
        best_score = 0.0
        if episode_scores:
            for episode, data in episode_scores.items():
                if data["match_count"] > 0:
                    avg_confidence = data["total_confidence"] / data["match_count"]
                    file_coverage = data["match_count"] / len(sparse_chunks) if sparse_chunks else 0
                    weighted_score = avg_confidence * file_coverage

                    if weighted_score > best_score:
                        best_score = weighted_score
                        best_episode = episode

        # Determine if fallback needed
        threshold = kwargs.get("threshold", 0.15)
        fallback = best_score < threshold

        processing_time = time.time() - start_time

        return MatchResult(
            method_name=self.name,
            file_path=file_data.file_path,
            show_name=file_data.show_name,
            season=file_data.season,
            episode_actual=file_data.episode,
            episode_matched=best_episode,
            confidence=best_score,
            correct=best_episode == file_data.episode if best_episode else False,
            processing_time=processing_time,
            chunks_used=chunks_used,
            total_chunks=len(sparse_chunks),
            weighted_score=best_score,
            fallback_used=fallback,
        )


class CompleteCoverageMethod(MatchingMethod):
    """Complete coverage with current weighted scoring."""

    def __init__(self):
        super().__init__("complete_coverage")

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        start_time = time.time()

        # Use ALL chunks
        episode_scores = defaultdict(lambda: {"total_confidence": 0.0, "match_count": 0})

        chunks_used = 0
        for chunk in file_data.chunks:
            if not chunk.cleaned_text:
                continue

            best_match_score = 0.0
            best_match_episode = None

            # Find best matching reference chunk
            for ref_episode in references:
                for ref_chunk in ref_episode.chunks:
                    if not ref_chunk.text:
                        continue

                    score = self.calculate_similarity(chunk.cleaned_text, ref_chunk.text)

                    if score > best_match_score:
                        best_match_score = score
                        best_match_episode = ref_episode.episode

            # Record match if above threshold
            if best_match_score > 0.6 and best_match_episode is not None:
                episode_scores[best_match_episode]["total_confidence"] += best_match_score
                episode_scores[best_match_episode]["match_count"] += 1
                chunks_used += 1

        # Calculate weighted scores (same as sparse method)
        best_episode = None
        best_score = 0.0
        if episode_scores:
            for episode, data in episode_scores.items():
                if data["match_count"] > 0:
                    avg_confidence = data["total_confidence"] / data["match_count"]
                    file_coverage = data["match_count"] / len(file_data.chunks)
                    weighted_score = avg_confidence * file_coverage

                    if weighted_score > best_score:
                        best_score = weighted_score
                        best_episode = episode

        threshold = kwargs.get("threshold", 0.15)
        fallback = best_score < threshold

        processing_time = time.time() - start_time

        return MatchResult(
            method_name=self.name,
            file_path=file_data.file_path,
            show_name=file_data.show_name,
            season=file_data.season,
            episode_actual=file_data.episode,
            episode_matched=best_episode,
            confidence=best_score,
            correct=best_episode == file_data.episode if best_episode else False,
            processing_time=processing_time,
            chunks_used=chunks_used,
            total_chunks=len(file_data.chunks),
            weighted_score=best_score,
            fallback_used=fallback,
        )


class RankedVotingMethod(MatchingMethod):
    """Ranked-choice voting with weighted confidence."""

    def __init__(self):
        super().__init__("ranked_voting")

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        start_time = time.time()

        # Collect votes from all chunks
        votes = defaultdict(list)

        for chunk in file_data.chunks:
            if not chunk.cleaned_text:
                continue

            # Find all matching episodes for this chunk
            for ref_episode in references:
                best_score = 0.0

                # Find best matching chunk in this reference episode
                for ref_chunk in ref_episode.chunks:
                    if not ref_chunk.text:
                        continue

                    score = self.calculate_similarity(chunk.cleaned_text, ref_chunk.text)
                    if score > best_score:
                        best_score = score

                # Record vote if above threshold
                if best_score > 0.6:
                    votes[ref_episode.episode].append(
                        {"confidence": best_score, "weight": chunk.coverage_weight}
                    )

        # Calculate weighted confidence scores
        weighted_scores = {}
        vote_breakdown = {}
        for episode, vote_list in votes.items():
            if not vote_list:
                continue

            total_weight = sum(v["weight"] for v in vote_list)
            weighted_confidence = (
                sum(v["confidence"] * v["weight"] for v in vote_list) / total_weight
            )
            weighted_scores[episode] = weighted_confidence

            vote_breakdown[episode] = {
                "vote_count": len(vote_list),
                "total_weight": total_weight,
                "weighted_confidence": weighted_confidence,
                "avg_confidence": sum(v["confidence"] for v in vote_list) / len(vote_list),
            }

        # Winner: highest weighted confidence
        best_episode = None
        best_score = 0.0
        if weighted_scores:
            best_episode = max(weighted_scores, key=weighted_scores.get)
            best_score = weighted_scores[best_episode]

        threshold = kwargs.get("threshold", 0.15)
        fallback = best_score < threshold

        chunks_used = sum(len(v) for v in votes.values())

        processing_time = time.time() - start_time

        return MatchResult(
            method_name=self.name,
            file_path=file_data.file_path,
            show_name=file_data.show_name,
            season=file_data.season,
            episode_actual=file_data.episode,
            episode_matched=best_episode,
            confidence=best_score,
            correct=best_episode == file_data.episode if best_episode else False,
            processing_time=processing_time,
            chunks_used=chunks_used,
            total_chunks=len(file_data.chunks),
            weighted_score=best_score,
            fallback_used=fallback,
            vote_breakdown=vote_breakdown,
        )


class SimpleVoteMethod(MatchingMethod):
    """Simple unweighted vote count."""

    def __init__(self):
        super().__init__("simple_vote")

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        start_time = time.time()

        # Count votes (each chunk match = 1 vote)
        vote_counts = defaultdict(lambda: {"count": 0, "total_confidence": 0.0})

        for chunk in file_data.chunks:
            if not chunk.cleaned_text:
                continue

            best_score = 0.0
            best_episode = None

            for ref_episode in references:
                for ref_chunk in ref_episode.chunks:
                    if not ref_chunk.text:
                        continue

                    score = self.calculate_similarity(chunk.cleaned_text, ref_chunk.text)
                    if score > best_score:
                        best_score = score
                        best_episode = ref_episode.episode

            if best_score > 0.6 and best_episode is not None:
                vote_counts[best_episode]["count"] += 1
                vote_counts[best_episode]["total_confidence"] += best_score

        # Winner: most votes
        best_episode = None
        best_score = 0.0
        if vote_counts:
            best_episode = max(vote_counts, key=lambda e: vote_counts[e]["count"])
            vote_data = vote_counts[best_episode]
            best_score = vote_data["total_confidence"] / vote_data["count"] if vote_data["count"] > 0 else 0.0

        threshold = kwargs.get("threshold", 0.15)
        fallback = best_score < threshold

        chunks_used = sum(v["count"] for v in vote_counts.values())

        processing_time = time.time() - start_time

        return MatchResult(
            method_name=self.name,
            file_path=file_data.file_path,
            show_name=file_data.show_name,
            season=file_data.season,
            episode_actual=file_data.episode,
            episode_matched=best_episode,
            confidence=best_score,
            correct=best_episode == file_data.episode if best_episode else False,
            processing_time=processing_time,
            chunks_used=chunks_used,
            total_chunks=len(file_data.chunks),
            weighted_score=best_score,
            fallback_used=fallback,
        )


class AlternativeSimilarityMethod(MatchingMethod):
    """Test alternative similarity algorithm weights."""

    def __init__(self, weights: tuple[float, float], name_suffix: str):
        super().__init__(f"alt_similarity_{name_suffix}")
        self.similarity_weights = weights

    def match(
        self, file_data: FileData, references: list[ReferenceEpisode], **kwargs
    ) -> MatchResult:
        start_time = time.time()

        # Use complete coverage with custom similarity weights
        episode_scores = defaultdict(lambda: {"total_confidence": 0.0, "match_count": 0})

        chunks_used = 0
        for chunk in file_data.chunks:
            if not chunk.cleaned_text:
                continue

            best_match_score = 0.0
            best_match_episode = None

            for ref_episode in references:
                for ref_chunk in ref_episode.chunks:
                    if not ref_chunk.text:
                        continue

                    score = self.calculate_similarity(
                        chunk.cleaned_text, ref_chunk.text, self.similarity_weights
                    )

                    if score > best_match_score:
                        best_match_score = score
                        best_match_episode = ref_episode.episode

            if best_match_score > 0.6 and best_match_episode is not None:
                episode_scores[best_match_episode]["total_confidence"] += best_match_score
                episode_scores[best_match_episode]["match_count"] += 1
                chunks_used += 1

        best_episode = None
        best_score = 0.0
        if episode_scores:
            for episode, data in episode_scores.items():
                if data["match_count"] > 0:
                    avg_confidence = data["total_confidence"] / data["match_count"]
                    file_coverage = data["match_count"] / len(file_data.chunks)
                    weighted_score = avg_confidence * file_coverage

                    if weighted_score > best_score:
                        best_score = weighted_score
                        best_episode = episode

        threshold = kwargs.get("threshold", 0.15)
        fallback = best_score < threshold

        processing_time = time.time() - start_time

        return MatchResult(
            method_name=self.name,
            file_path=file_data.file_path,
            show_name=file_data.show_name,
            season=file_data.season,
            episode_actual=file_data.episode,
            episode_matched=best_episode,
            confidence=best_score,
            correct=best_episode == file_data.episode if best_episode else False,
            processing_time=processing_time,
            chunks_used=chunks_used,
            total_chunks=len(file_data.chunks),
            weighted_score=best_score,
            fallback_used=fallback,
        )


def load_transcription_cache(cache_dir: Path) -> dict[str, FileData]:
    """Load all cached transcriptions."""
    transcriptions = {}

    for json_file in cache_dir.rglob("*.json"):
        if json_file.name == "transcription_index.json":
            continue

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            file_data = FileData(
                file_path=data["file_path"],
                show_name=data["show_name"],
                season=data["season"],
                episode=data["episode"],
                video_duration=data["video_duration"],
                chunks=[ChunkData(**chunk) for chunk in data["chunks"]],
            )
            transcriptions[file_data.file_path] = file_data

        except Exception as e:
            print(f"Error loading {json_file}: {e}")

    return transcriptions


def load_references(ref_dir: Path) -> dict[tuple[str, int], list[ReferenceEpisode]]:
    """Load all reference subtitle data."""
    references = {}

    for json_file in ref_dir.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            show_name = data["show_name"]
            season = data["season"]
            ref_episodes = []

            for ref in data["references"]:
                chunks = [ReferenceChunk(**chunk) for chunk in ref["chunks"]]
                ref_episodes.append(
                    ReferenceEpisode(
                        episode=ref["episode"],
                        file_path=ref["file_path"],
                        duration=ref["duration"],
                        full_text=ref["full_text"],
                        chunks=chunks,
                    )
                )

            references[(show_name, season)] = ref_episodes

        except Exception as e:
            print(f"Error loading {json_file}: {e}")

    return references


def calculate_metrics(results: list[MatchResult] | list[dict]) -> dict[str, Any]:
    """Calculate evaluation metrics from results (MatchResult objects or dicts)."""
    if not results:
        return {}

    # Handle both MatchResult objects and dicts
    def get_attr(r, attr):
        if isinstance(r, dict):
            return r.get(attr)
        return getattr(r, attr)

    total = len(results)
    correct = sum(1 for r in results if get_attr(r, "correct"))
    confident = sum(1 for r in results if not get_attr(r, "fallback_used"))
    confident_correct = sum(1 for r in results if get_attr(r, "correct") and not get_attr(r, "fallback_used"))

    accuracy = correct / total if total > 0 else 0
    recall = confident / total if total > 0 else 0
    precision = confident_correct / confident if confident > 0 else 0
    fallback_rate = (total - confident) / total if total > 0 else 0

    avg_confidence = (
        sum(get_attr(r, "confidence") for r in results if get_attr(r, "episode_matched") is not None) / total
        if total > 0
        else 0
    )
    avg_processing_time = sum(get_attr(r, "processing_time") for r in results) / total if total > 0 else 0
    avg_chunk_utilization = (
        sum(get_attr(r, "chunks_used") / get_attr(r, "total_chunks") for r in results if get_attr(r, "total_chunks") > 0) / total
        if total > 0
        else 0
    )

    return {
        "total_files": total,
        "correct_matches": correct,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "fallback_rate": fallback_rate,
        "avg_confidence": avg_confidence,
        "avg_processing_time": avg_processing_time,
        "avg_chunk_utilization": avg_chunk_utilization,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate episode matching methods")
    parser.add_argument(
        "--transcriptions",
        type=str,
        default="investigation_output/transcriptions",
        help="Path to transcription cache",
    )
    parser.add_argument(
        "--references",
        type=str,
        default="investigation_output/references",
        help="Path to reference subtitles",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="investigation_output/matching_results.csv",
        help="Output CSV file",
    )
    parser.add_argument(
        "--methods",
        type=str,
        nargs="+",
        help="Specific methods to run (default: all)",
    )
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=[0.1, 0.15, 0.2, 0.25, 0.3],
        help="Confidence thresholds to test",
    )

    args = parser.parse_args()

    # Load data
    print("Loading transcription cache...")
    transcriptions = load_transcription_cache(Path(args.transcriptions))
    print(f"  Loaded {len(transcriptions)} transcribed files")

    print("Loading reference subtitles...")
    references = load_references(Path(args.references))
    print(f"  Loaded references for {len(references)} show/season combinations")

    # Initialize methods
    all_methods = [
        SparseMethod(),
        CompleteCoverageMethod(),
        RankedVotingMethod(),
        SimpleVoteMethod(),
        AlternativeSimilarityMethod((0.5, 0.5), "balanced"),
        AlternativeSimilarityMethod((1.0, 0.0), "pure_token_sort"),
        AlternativeSimilarityMethod((0.8, 0.2), "token_set_heavy"),
    ]

    # Filter methods if specified
    if args.methods:
        all_methods = [m for m in all_methods if m.name in args.methods]

    print(f"\nRunning {len(all_methods)} methods with {len(args.thresholds)} thresholds...")

    # Run evaluation
    all_results = []

    for method in all_methods:
        print(f"\n--- {method.name} ---")

        for threshold in args.thresholds:
            method_results = []

            for file_path, file_data in transcriptions.items():
                # Get references for this show/season
                key = (file_data.show_name, file_data.season)
                if key not in references:
                    print(f"  Warning: No references for {key}")
                    continue

                # Run matching
                result = method.match(file_data, references[key], threshold=threshold)
                method_results.append(result)

            # Calculate metrics for this method/threshold combo
            metrics = calculate_metrics(method_results)
            print(
                f"  Threshold {threshold:.2f}: "
                f"Accuracy={metrics['accuracy']:.1%}, "
                f"Recall={metrics['recall']:.1%}, "
                f"Fallback={metrics['fallback_rate']:.1%}"
            )

            all_results.extend(method_results)

    # Save results to CSV
    print(f"\nSaving results to {args.output}...")
    df = pd.DataFrame([asdict(r) for r in all_results])
    df.to_csv(args.output, index=False)

    # Print summary
    print("\n=== Summary ===")
    summary_df = df.groupby("method_name").apply(
        lambda g: pd.Series(calculate_metrics(g.to_dict("records")))
    )
    print(summary_df.to_string())

    print(f"\n✓ Results saved to: {args.output}")
    print("Next step: Export results for manual review")
    print("  uv run python -m app.matcher.scripts.export_investigation_results")


if __name__ == "__main__":
    main()
