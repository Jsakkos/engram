# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""
Chromaprint windowed-voting spike.

Question being tested:
  In the Shazam-style use case, we have a short audio clip and a reference
  catalog that *contains* the parent media. Does per-window chromaprint
  matching with AcoustID-style time-offset-bucket voting correctly identify
  the parent episode and the right timestamp within it?

  This is the production scenario:
    - Post-rip identification: we extract ~15 windowed chromaprints from a
      ripped MKV and look each one up against a catalog that contains the
      canonical fingerprint for that episode.
    - Short-clip query: a 5-15 s clip queried against the same catalog.
  In both cases the correct answer exists in the catalog. The algorithm's
  job is to find it.

Method:
  1. Recursively find labeled MKVs matching "Show - SnnEnn.mkv". Build full
     chromaprint hash streams via ./bin/fpcalc.exe -raw.
  2. The reference catalog is ALL episodes (including the source of every
     query). This mirrors production.
  3. For each episode, sample N_QUERIES windows of WINDOW_SECS seconds each
     at evenly-spaced offsets *inside* the episode's own hash stream. Each
     window becomes a query.
  4. For each query window run the AcoustID time-offset-bucket vote:
        - For each query hash, find reference hashes within Hamming <= H.
        - Bucket each match by (ref_episode, ref_offset - query_offset).
        - The (episode, dt) bucket with the most matches wins this window.
  5. A window-level success = the winning episode IS the source episode AND
     the winning dt bucket corresponds to the true offset (within tolerance).
  6. Report per-window success rate, mean overlap with the source vs the
     runner-up (the separation metric the production cascade would use),
     and inter-show vs intra-show confusion patterns.

Usage:
  uv run spike.py "C:/Users/jonat/Engram/TV" [--shows "Show1,Show2"]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SPIKE_DIR = Path(__file__).resolve().parent
FPCALC = SPIKE_DIR / "bin" / "fpcalc.exe"

CHROMAPRINT_FRAME_SECONDS = 0.12380952  # 4096 / 11025 * 1/3
FRAMES_PER_SEC = 1.0 / CHROMAPRINT_FRAME_SECONDS

WINDOW_SECS = 30
N_QUERIES = 8        # query windows sampled per episode
HAMMING_THRESHOLD = 6  # bits; AcoustID convention is 4-6
DT_BUCKET_FRAMES = 8   # ~1 second tolerance per match-cluster
OFFSET_TOLERANCE_FRAMES = 24  # ~3 seconds; "is this dt close to the true offset?"

EP_REGEX = re.compile(r"(?P<show>.+?)\s*-\s*S(?P<season>\d{1,2})E(?P<ep>\d{1,3})", re.IGNORECASE)
POPCOUNT_LUT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


@dataclass
class Episode:
    show: str
    season: int
    episode: int
    path: Path
    hashes: np.ndarray  # uint32 array of chromaprint frames

    @property
    def label(self) -> str:
        return f"{self.show} S{self.season:02d}E{self.episode:02d}"


def parse_label(path: Path) -> tuple[str, int, int] | None:
    m = EP_REGEX.search(path.stem)
    if not m:
        return None
    return m["show"].strip(), int(m["season"]), int(m["ep"])


def run_fpcalc(path: Path) -> np.ndarray:
    """Return the full chromaprint hash stream for a media file as uint32 ndarray."""
    proc = subprocess.run(
        [str(FPCALC), "-raw", "-length", "99999", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    fp_line = next((l for l in proc.stdout.splitlines() if l.startswith("FINGERPRINT=")), None)
    if fp_line is None:
        raise RuntimeError(f"fpcalc produced no FINGERPRINT line for {path}")
    raw = fp_line.removeprefix("FINGERPRINT=")
    return np.array([int(x) for x in raw.split(",") if x], dtype=np.uint32)


def hamming32(q: np.uint32, refs: np.ndarray) -> np.ndarray:
    """Vectorised Hamming distance between scalar q and each uint32 in refs."""
    x = np.bitwise_xor(refs, np.uint32(q))
    b0 = POPCOUNT_LUT[(x & 0xFF).astype(np.uint8)]
    b1 = POPCOUNT_LUT[((x >> 8) & 0xFF).astype(np.uint8)]
    b2 = POPCOUNT_LUT[((x >> 16) & 0xFF).astype(np.uint8)]
    b3 = POPCOUNT_LUT[((x >> 24) & 0xFF).astype(np.uint8)]
    return (b0 + b1 + b2 + b3).astype(np.int16)


def collect_episodes(root: Path, allowed_shows: set[str] | None) -> list[Episode]:
    episodes: list[Episode] = []
    for mkv in sorted(root.rglob("*.mkv")):
        if "Extras" in mkv.parts:
            continue
        label = parse_label(mkv)
        if label is None:
            continue
        show, season, ep = label
        if allowed_shows and show not in allowed_shows:
            continue
        print(f"  fingerprinting {show} S{season:02d}E{ep:02d} ...", flush=True)
        t0 = time.time()
        hashes = run_fpcalc(mkv)
        dt = time.time() - t0
        print(f"    {len(hashes)} frames in {dt:.1f}s ({len(hashes)/dt:.0f} fr/s)")
        episodes.append(Episode(show=show, season=season, episode=ep, path=mkv, hashes=hashes))
    return episodes


@dataclass
class WindowResult:
    source_label: str
    source_start_frame: int
    predicted_label: str
    predicted_dt_frames: int
    predicted_match_count: int
    runner_up_label: str
    runner_up_match_count: int
    # True (episode, dt) match strength if it ranked anywhere
    source_match_count: int


def query_single_window(
    window_hashes: np.ndarray,
    window_start_in_source: int,
    source_label: str,
    ref_hashes: np.ndarray,
    ref_owner: np.ndarray,
    ref_offset: np.ndarray,
    ref_labels: list[str],
) -> WindowResult:
    """Run the time-bucket vote for a single window against the catalog."""
    bucket: dict[tuple[int, int], int] = defaultdict(int)
    for q_idx, q_hash in enumerate(window_hashes):
        dists = hamming32(q_hash, ref_hashes)
        hits = np.where(dists <= HAMMING_THRESHOLD)[0]
        if len(hits) == 0:
            continue
        q_pos_in_query = q_idx  # query coordinate space starts at 0
        for h in hits:
            dt = (int(ref_offset[h]) - q_pos_in_query) // DT_BUCKET_FRAMES
            bucket[(int(ref_owner[h]), dt)] += 1

    if not bucket:
        return WindowResult(
            source_label=source_label, source_start_frame=window_start_in_source,
            predicted_label="<no-match>", predicted_dt_frames=0, predicted_match_count=0,
            runner_up_label="<no-match>", runner_up_match_count=0, source_match_count=0,
        )

    sorted_buckets = sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)
    (best_ref, best_dt), best_count = sorted_buckets[0]
    runner_up_label = "<none>"
    runner_up_count = 0
    if len(sorted_buckets) > 1:
        (ru_ref, _ru_dt), ru_count = sorted_buckets[1]
        runner_up_label = ref_labels[ru_ref]
        runner_up_count = ru_count

    # Find the best bucket that corresponds to the TRUE source (if any)
    source_ref_idx = ref_labels.index(source_label)
    expected_dt_bucket = window_start_in_source // DT_BUCKET_FRAMES
    source_match_count = max(
        (
            cnt for (ep, dt), cnt in bucket.items()
            if ep == source_ref_idx and abs(dt - expected_dt_bucket) <= OFFSET_TOLERANCE_FRAMES // DT_BUCKET_FRAMES
        ),
        default=0,
    )

    return WindowResult(
        source_label=source_label,
        source_start_frame=window_start_in_source,
        predicted_label=ref_labels[best_ref],
        predicted_dt_frames=best_dt * DT_BUCKET_FRAMES,
        predicted_match_count=best_count,
        runner_up_label=runner_up_label,
        runner_up_match_count=runner_up_count,
        source_match_count=source_match_count,
    )


def build_catalog(episodes: list[Episode]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    ref_hashes = np.concatenate([e.hashes for e in episodes])
    ref_owner = np.concatenate([np.full(len(e.hashes), i, dtype=np.int32) for i, e in enumerate(episodes)])
    ref_offset = np.concatenate([np.arange(len(e.hashes), dtype=np.int32) for e in episodes])
    ref_labels = [e.label for e in episodes]
    return ref_hashes, ref_owner, ref_offset, ref_labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library_root", type=Path)
    parser.add_argument("--shows", type=str, default=None, help="comma-separated allow-list")
    args = parser.parse_args()

    if not FPCALC.exists():
        print(f"ERROR: fpcalc not found at {FPCALC}", file=sys.stderr)
        return 2

    allowed = set(s.strip() for s in args.shows.split(",")) if args.shows else None
    print(f"Scanning {args.library_root}{' (shows: ' + ', '.join(sorted(allowed)) + ')' if allowed else ''}")
    t0 = time.time()
    episodes = collect_episodes(args.library_root, allowed)
    print(f"\nFingerprinted {len(episodes)} episodes in {time.time() - t0:.1f}s\n")

    if len(episodes) < 2:
        print("Need at least 2 labeled episodes; aborting.")
        return 1

    print(f"\n=== Building catalog index over all {len(episodes)} episodes ===")
    ref_hashes, ref_owner, ref_offset, ref_labels = build_catalog(episodes)
    print(f"  Total reference frames: {len(ref_hashes):,}\n")

    print(f"=== Per-window query classification "
          f"(N_QUERIES={N_QUERIES} per ep, WINDOW_SECS={WINDOW_SECS}, HAMMING<={HAMMING_THRESHOLD}) ===\n")

    win_frames = max(1, int(WINDOW_SECS * FRAMES_PER_SEC))
    all_results: list[WindowResult] = []
    t0 = time.time()
    for ep in episodes:
        n_frames = len(ep.hashes)
        starts = np.linspace(win_frames, max(win_frames, n_frames - 2*win_frames), N_QUERIES, dtype=np.int64)
        wins_correct = 0
        for s in starts:
            window = ep.hashes[s : s + win_frames]
            r = query_single_window(window, int(s), ep.label, ref_hashes, ref_owner, ref_offset, ref_labels)
            all_results.append(r)
            if r.predicted_label == ep.label:
                wins_correct += 1
        print(f"  {ep.label:55} {wins_correct}/{N_QUERIES} windows correctly identified")
    print(f"\nQueries finished in {time.time() - t0:.1f}s")

    n = len(all_results)
    correct = sum(1 for r in all_results if r.predicted_label == r.source_label)
    no_match = sum(1 for r in all_results if r.predicted_label == "<no-match>")
    intra = sum(
        1 for r in all_results
        if r.predicted_label not in (r.source_label, "<no-match>")
        and r.predicted_label.split(" S")[0] == r.source_label.split(" S")[0]
    )
    inter = sum(
        1 for r in all_results
        if r.predicted_label not in (r.source_label, "<no-match>")
        and r.predicted_label.split(" S")[0] != r.source_label.split(" S")[0]
    )

    correct_matches = [r.predicted_match_count for r in all_results if r.predicted_label == r.source_label]
    wrong_matches = [r.predicted_match_count for r in all_results if r.predicted_label != r.source_label and r.predicted_label != "<no-match>"]
    separations = [
        r.predicted_match_count - r.runner_up_match_count
        for r in all_results if r.predicted_label == r.source_label
    ]

    print(f"\n=== Summary ===")
    print(f"  Total query windows:      {n}")
    print(f"  Correct (right ep):       {correct}/{n} = {100*correct/n:.1f}%")
    print(f"  Intra-show errors:        {intra}/{n} ({100*intra/n:.1f}%)")
    print(f"  Inter-show errors:        {inter}/{n} ({100*inter/n:.1f}%)")
    print(f"  No match at all:          {no_match}/{n} ({100*no_match/n:.1f}%)")
    if correct_matches:
        print(f"  Correct match strength  : mean={np.mean(correct_matches):.1f}  "
              f"min={min(correct_matches)}  max={max(correct_matches)}")
    if wrong_matches:
        print(f"  Wrong match strength    : mean={np.mean(wrong_matches):.1f}  "
              f"min={min(wrong_matches)}  max={max(wrong_matches)}")
    if separations:
        print(f"  Separation (correct - runner-up): "
              f"mean={np.mean(separations):.1f}  min={min(separations)}  max={max(separations)}")
    # How many correct-classifications also nailed the temporal offset?
    correct_with_offset = sum(
        1 for r in all_results
        if r.predicted_label == r.source_label
        and abs(r.predicted_dt_frames - r.source_start_frame) <= OFFSET_TOLERANCE_FRAMES
    )
    if correct:
        print(f"  Of the correct ones, {correct_with_offset}/{correct} ({100*correct_with_offset/correct:.1f}%) "
              f"also predicted the right in-episode offset (±3s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
