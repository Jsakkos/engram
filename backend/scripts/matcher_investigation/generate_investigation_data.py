"""
Generate complete-coverage transcription data for episode matching investigation.

This script processes video files with complete audio chunk coverage (every 30s),
transcribes each chunk, and caches the results for fast matching experimentation.

Usage:
    # Process subset of episodes (default: 5 Arrested Dev + 3 Expanse)
    uv run python -m app.matcher.scripts.generate_investigation_data --subset

    # Process specific show and episodes
    uv run python -m app.matcher.scripts.generate_investigation_data \
        --show "Arrested Development" --episodes 1-5

    # Process all files in test directory
    uv run python -m app.matcher.scripts.generate_investigation_data --all

    # Force re-processing of already cached files
    uv run python -m app.matcher.scripts.generate_investigation_data --subset --force
"""

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from app.matcher.asr_models import get_cached_model
from app.matcher.srt_utils import SubtitleReader, clean_text, extract_audio_chunk, get_video_duration


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
class ReferenceEpisode:
    """Reference subtitle data for an episode."""

    episode: int
    file_path: str
    duration: float
    full_text: str
    chunks: list[dict[str, str]]


@dataclass
class ReferenceData:
    """Collection of reference subtitles for a show/season."""

    show_name: str
    season: int
    references: list[ReferenceEpisode]


class TranscriptionCache:
    """Manages caching of transcription data."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.transcriptions_dir = output_dir / "transcriptions"
        self.transcriptions_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = output_dir / "transcription_index.json"
        self.index = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        """Load the transcription index."""
        if self.index_file.exists():
            with open(self.index_file, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self) -> None:
        """Save the transcription index."""
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self.index, f, indent=2)

    def is_cached(self, file_path: str) -> bool:
        """Check if a file has already been transcribed."""
        return file_path in self.index

    def get_cached_path(self, show: str, season: int, episode: int) -> Path:
        """Get the cache file path for an episode."""
        show_dir = self.transcriptions_dir / show / f"Season {season:02d}"
        show_dir.mkdir(parents=True, exist_ok=True)
        return show_dir / f"S{season:02d}E{episode:02d}.json"

    def save_transcription(self, data: FileData) -> None:
        """Save transcription data to cache."""
        cache_path = self.get_cached_path(data.show_name, data.season, data.episode)

        # Convert to dict
        data_dict = {
            "file_path": data.file_path,
            "show_name": data.show_name,
            "season": data.season,
            "episode": data.episode,
            "video_duration": data.video_duration,
            "chunks": [asdict(chunk) for chunk in data.chunks],
        }

        # Save to file
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, indent=2)

        # Update index
        self.index[data.file_path] = {
            "show": data.show_name,
            "season": data.season,
            "episode": data.episode,
            "cache_path": str(cache_path),
            "chunk_count": len(data.chunks),
            "duration": data.video_duration,
        }
        self._save_index()

    def load_transcription(self, file_path: str) -> FileData | None:
        """Load cached transcription data."""
        if file_path not in self.index:
            return None

        cache_path = Path(self.index[file_path]["cache_path"])
        if not cache_path.exists():
            return None

        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)

        return FileData(
            file_path=data["file_path"],
            show_name=data["show_name"],
            season=data["season"],
            episode=data["episode"],
            video_duration=data["video_duration"],
            chunks=[ChunkData(**chunk) for chunk in data["chunks"]],
        )


def parse_filename(file_path: Path) -> tuple[str, int, int] | None:
    """
    Extract show name, season, and episode from filename.

    Expected format: "Show Name - S01E02.mkv" or "Show Name/Season 1/Show - S01E02.mkv"
    """
    # Try to match S01E02 pattern
    match = re.search(r"S(\d{2})E(\d{2})", file_path.stem, re.IGNORECASE)
    if not match:
        return None

    season = int(match.group(1))
    episode = int(match.group(2))

    # Extract show name from parent directory or filename
    # Try parent directory first (TV/Show Name/Season X/)
    if "Season" in file_path.parent.name:
        show_name = file_path.parent.parent.name
    else:
        # Extract from filename (before " - S01E02")
        show_match = re.match(r"^(.+?)\s*-\s*S\d{2}E\d{2}", file_path.stem, re.IGNORECASE)
        if show_match:
            show_name = show_match.group(1).strip()
        else:
            show_name = file_path.parent.name

    return show_name, season, episode


def discover_test_files(
    test_dir: Path, show_filter: str | None = None, episode_range: tuple[int, int] | None = None
) -> list[Path]:
    """
    Discover all .mkv files in the test directory.

    Args:
        test_dir: Root directory to search
        show_filter: Optional show name to filter by
        episode_range: Optional (start, end) episode range to filter by
    """
    files = []
    for mkv_file in test_dir.rglob("*.mkv"):
        parsed = parse_filename(mkv_file)
        if not parsed:
            continue

        show_name, season, episode = parsed

        # Apply filters
        if show_filter and show_filter.lower() not in show_name.lower():
            continue

        if episode_range:
            start, end = episode_range
            if not (start <= episode <= end):
                continue

        files.append(mkv_file)

    return sorted(files)


def transcribe_chunk(
    model,
    audio_path: Path,
    chunk_index: int,
    start_time: float,
    duration: float,
    video_duration: float,
) -> ChunkData:
    """Transcribe a single audio chunk."""
    end_time = start_time + duration
    coverage_weight = duration / video_duration

    # Transcribe with Faster-Whisper
    result = model.transcribe(str(audio_path))

    # Extract segments
    segments = []
    raw_texts = []
    for segment in result["segments"]:
        segments.append(
            {
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
            }
        )
        raw_texts.append(segment["text"])

    raw_text = " ".join(raw_texts)
    cleaned = clean_text(raw_text)

    return ChunkData(
        chunk_index=chunk_index,
        start_time=start_time,
        duration=duration,
        end_time=end_time,
        coverage_weight=coverage_weight,
        raw_text=raw_text,
        cleaned_text=cleaned,
        language=result.get("language", "unknown"),
        segments=segments,
    )


async def process_file(
    file_path: Path, cache: TranscriptionCache, force: bool = False
) -> FileData | None:
    """Process a single video file with complete chunk coverage."""
    # Check cache first
    if not force and cache.is_cached(str(file_path)):
        print(f"  Skipping (already cached): {file_path.name}")
        return cache.load_transcription(str(file_path))

    # Parse filename
    parsed = parse_filename(file_path)
    if not parsed:
        print(f"  Skipping (cannot parse): {file_path.name}")
        return None

    show_name, season, episode = parsed

    # Get video duration
    try:
        duration = get_video_duration(str(file_path))
    except Exception as e:
        print(f"  Error getting duration for {file_path.name}: {e}")
        return None

    print(f"\nProcessing: {show_name} S{season:02d}E{episode:02d} ({duration:.1f}s)")

    # Generate chunk times (every 30s from 0 to duration-30)
    chunk_duration = 30.0
    chunk_times = []
    current_time = 0.0
    while current_time + chunk_duration <= duration:
        chunk_times.append(current_time)
        current_time += chunk_duration

    print(f"  Total chunks: {len(chunk_times)}")

    # Load ASR model once (force CPU to avoid CUDA issues)
    model_config = {
        "type": "faster-whisper",
        "name": "small",
        "device": "cpu",
    }
    model = get_cached_model(model_config)

    # Process each chunk
    chunks = []
    temp_dir = Path("temp")
    temp_dir.mkdir(exist_ok=True)

    with tqdm(total=len(chunk_times), desc="  Transcribing", leave=False) as pbar:
        for idx, start_time in enumerate(chunk_times):
            # Extract audio chunk
            audio_path = temp_dir / f"chunk_{idx}.wav"
            try:
                extract_audio_chunk(str(file_path), start_time, chunk_duration, audio_path)

                # Transcribe
                chunk_data = transcribe_chunk(
                    model, audio_path, idx, start_time, chunk_duration, duration
                )
                chunks.append(chunk_data)

            except Exception as e:
                print(f"    Error processing chunk {idx} at {start_time}s: {e}")
                continue
            finally:
                # Clean up temp audio file
                if audio_path.exists():
                    audio_path.unlink()

            pbar.update(1)

    # Create file data
    file_data = FileData(
        file_path=str(file_path),
        show_name=show_name,
        season=season,
        episode=episode,
        video_duration=duration,
        chunks=chunks,
    )

    # Save to cache
    cache.save_transcription(file_data)
    print(f"  ✓ Saved {len(chunks)} chunks to cache")

    return file_data


def load_reference_subtitles(show_name: str, season: int) -> ReferenceData:
    """Load all reference subtitles for a show/season."""
    reader = SubtitleReader()

    # Find all subtitle files for this show/season
    # Subtitle cache path: ~/.engram/cache/data/{show}/{show} - S{season}E{episode}.srt
    # Default location is ~/.engram/cache, could be overridden by config
    cache_root = Path.home() / ".engram" / "cache"
    cache_dir = cache_root / "data" / show_name
    if not cache_dir.exists():
        print(f"  Warning: No subtitle cache found for {show_name} at {cache_dir}")
        return ReferenceData(show_name=show_name, season=season, references=[])

    references = []

    # Find subtitle files - support both S01E01 and 1x01 formats
    srt_files = []
    # Pattern 1: S01E01 format
    srt_files.extend(cache_dir.glob(f"*S{season:02d}E*.srt"))
    # Pattern 2: 1x01 format (e.g., "Show - 1x01 - Title.srt")
    srt_files.extend(cache_dir.glob(f"*{season}x*.srt"))

    for srt_file in sorted(set(srt_files)):  # Remove duplicates
        # Parse episode number - try both formats
        # Format 1: S01E01
        match = re.search(r"S\d{2}E(\d{2})", srt_file.stem, re.IGNORECASE)
        if match:
            episode = int(match.group(1))
        else:
            # Format 2: 1x01
            match = re.search(rf"{season}x(\d{{2}})", srt_file.stem, re.IGNORECASE)
            if not match:
                continue
            episode = int(match.group(1))

        # Read subtitle file
        try:
            # Read SRT file content
            srt_content = reader.read_srt_file(srt_file)
            if not srt_content:
                continue

            # Extract full text by parsing SRT blocks
            full_text_lines = []
            last_timestamp = 0.0

            for block in srt_content.strip().split("\n\n"):
                lines = block.split("\n")
                if len(lines) < 3 or "-->" not in lines[1]:
                    continue
                try:
                    # Parse timestamp to get duration
                    timestamp = lines[1]
                    time_parts = timestamp.split(" --> ")
                    end_time = reader.parse_timestamp(time_parts[1].strip())
                    if end_time > last_timestamp:
                        last_timestamp = end_time

                    # Add subtitle text
                    text = " ".join(lines[2:])
                    full_text_lines.append(text)
                except (IndexError, ValueError):
                    continue

            full_text = " ".join(full_text_lines)
            duration = last_timestamp

            # Create chunks (every 30s)
            chunks = []
            chunk_duration = 30.0
            current_time = 0.0
            while current_time + chunk_duration <= duration:
                # Get subtitles in this time window using reader method
                chunk_text_lines = reader.extract_subtitle_chunk(
                    srt_content, current_time, current_time + chunk_duration
                )
                chunk_text = " ".join(chunk_text_lines)

                chunks.append(
                    {"start_time": current_time, "duration": chunk_duration, "text": chunk_text}
                )

                current_time += chunk_duration

            references.append(
                ReferenceEpisode(
                    episode=episode,
                    file_path=str(srt_file),
                    duration=duration,
                    full_text=full_text,
                    chunks=chunks,
                )
            )

        except Exception as e:
            print(f"  Error reading subtitle {srt_file.name}: {e}")
            continue

    print(f"  Loaded {len(references)} reference episodes for {show_name} S{season:02d}")
    return ReferenceData(show_name=show_name, season=season, references=references)


async def main():
    parser = argparse.ArgumentParser(
        description="Generate complete-coverage transcription data for matching investigation"
    )
    parser.add_argument(
        "--subset",
        action="store_true",
        help="Process subset: 5 Arrested Development + 3 Expanse episodes (default)",
    )
    parser.add_argument("--all", action="store_true", help="Process all files in test directory")
    parser.add_argument("--show", type=str, help="Filter by show name")
    parser.add_argument(
        "--episodes", type=str, help="Episode range (e.g., '1-5' or '3' for single episode)"
    )
    parser.add_argument("--force", action="store_true", help="Force re-processing of cached files")
    parser.add_argument(
        "--test-dir",
        type=str,
        default=r"C:\Media\Tests",
        help="Test directory to scan (default: C:\\Media\\Tests)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="investigation_output",
        help="Output directory for cache (default: investigation_output)",
    )

    args = parser.parse_args()

    # Setup paths
    test_dir = Path(args.test_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    # Initialize cache
    cache = TranscriptionCache(output_dir)

    # Parse episode range if provided
    episode_range = None
    if args.episodes:
        if "-" in args.episodes:
            start, end = args.episodes.split("-")
            episode_range = (int(start), int(end))
        else:
            ep = int(args.episodes)
            episode_range = (ep, ep)

    # Discover files
    if args.subset and not args.all and not args.show:
        # Default subset: Process specific episodes
        print("Processing subset (5 Arrested Development + 3 Expanse)...")
        files = []
        # Arrested Development S01E01-E05
        ad_files = discover_test_files(test_dir, "Arrested Development", (1, 5))
        files.extend(ad_files)
        # The Expanse S01E01-E03
        expanse_files = discover_test_files(test_dir, "The Expanse", (1, 3))
        files.extend(expanse_files)
    else:
        files = discover_test_files(test_dir, args.show, episode_range)

    print(f"\nFound {len(files)} files to process")

    # Process each file
    processed_files = []
    for file_path in files:
        result = await process_file(file_path, cache, args.force)
        if result:
            processed_files.append(result)

    print(f"\n✓ Processed {len(processed_files)} files")
    print(f"✓ Transcription cache saved to: {cache.transcriptions_dir}")
    print(f"✓ Index saved to: {cache.index_file}")

    # Load reference subtitles for each show/season combination
    print("\n--- Loading Reference Subtitles ---")
    show_seasons = {}
    for file_data in processed_files:
        key = (file_data.show_name, file_data.season)
        if key not in show_seasons:
            show_seasons[key] = True
            ref_data = load_reference_subtitles(file_data.show_name, file_data.season)

            # Save reference data
            ref_dir = output_dir / "references"
            ref_dir.mkdir(exist_ok=True)
            ref_file = ref_dir / f"{file_data.show_name}_S{file_data.season:02d}.json"

            with open(ref_file, "w", encoding="utf-8") as f:
                json.dump(asdict(ref_data), f, indent=2)

            print(f"  ✓ Saved references to: {ref_file}")

    print("\n=== Data Generation Complete ===")
    print("Next step: Run matching evaluation with cached transcriptions")
    print("  uv run python -m app.matcher.scripts.evaluate_matching_methods")


if __name__ == "__main__":
    asyncio.run(main())
