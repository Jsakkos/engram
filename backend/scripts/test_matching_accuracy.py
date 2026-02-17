"""
End-to-End Matching Accuracy Test

Runs real video files from C:\\Media\\Tests through the full EpisodeMatcher
pipeline (FFmpeg -> Whisper ASR -> TF-IDF matching) and measures accuracy,
precision, recall, and performance against ground truth from filenames.

Usage:
    # Quick validation (3 random episodes)
    uv run python scripts/test_matching_accuracy.py --subset 3

    # Full run (all episodes)
    uv run python scripts/test_matching_accuracy.py

    # Specific show only
    uv run python scripts/test_matching_accuracy.py --show "Arrested Development"
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Ensure the backend directory is on sys.path so we can import app.*
_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


# ── Test Configuration ──────────────────────────────────────────────────────

TESTS_DIR = Path(r"C:\Media\Tests")
CACHE_DIR = Path.home() / ".uma" / "cache"
RESULTS_DIR = Path.home() / ".uma" / "test_results"

# Show mappings: directory structure -> (show_name, season, video_subdir)
# The video_subdir is relative to TESTS_DIR
SHOW_CONFIG = [
    {
        "show_name": "Arrested Development",
        "season": 1,
        "video_dir": TESTS_DIR / "Arrested Development" / "Season 1",
    },
    {
        "show_name": "Rick and Morty",
        "season": 2,
        "video_dir": TESTS_DIR / "Rick and Morty" / "Season 2",  # folder is just "Season 2"
    },
    {
        "show_name": "South Park",
        "season": 7,
        "video_dir": TESTS_DIR / "South Park" / "Season 7",
    },
    {
        "show_name": "The Expanse",
        "season": 1,
        "video_dir": TESTS_DIR / "The Expanse" / "Season 1",
    },
]


# ── Ground Truth Extraction ────────────────────────────────────────────────

@dataclass
class TestCase:
    show_name: str
    season: int
    expected_episode: int
    video_path: Path
    label: str  # e.g. "Arrested Development S01E03"


@dataclass
class TestResult:
    test_case: TestCase
    predicted_season: int = 0
    predicted_episode: int = 0
    confidence: float = 0.0
    correct: bool = False
    elapsed_sec: float = 0.0
    error: str = ""
    score_gap: float = 0.0
    vote_count: int = 0


def extract_episode_from_filename(filename: str) -> int:
    """Extract episode number from video filename."""
    # Match S01E03, S07e05, etc.
    m = re.search(r"S\d+E(\d+)", filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


def discover_test_cases(show_filter: str = None) -> list[TestCase]:
    """Auto-discover test videos and build ground truth from filenames."""
    cases = []

    for config in SHOW_CONFIG:
        show_name = config["show_name"]
        season = config["season"]
        video_dir = config["video_dir"]

        if show_filter and show_filter.lower() not in show_name.lower():
            continue

        if not video_dir.exists():
            print(f"  [SKIP] {show_name}: directory not found ({video_dir})")
            continue

        for mkv in sorted(video_dir.glob("*.mkv")):
            ep = extract_episode_from_filename(mkv.name)
            if ep > 0:
                label = f"{show_name} S{season:02d}E{ep:02d}"
                cases.append(TestCase(
                    show_name=show_name,
                    season=season,
                    expected_episode=ep,
                    video_path=mkv,
                    label=label,
                ))

    return cases


# ── Subtitle Availability Check ────────────────────────────────────────────

def check_subtitles(show_name: str, season: int, episode_count: int) -> tuple[bool, int]:
    """Check if subtitles are cached for a show/season. Returns (all_present, count)."""
    data_dir = CACHE_DIR / "data" / show_name
    if not data_dir.exists():
        return False, 0

    found = 0
    for ep in range(1, episode_count + 1):
        pattern = f"*S{season:02d}E{ep:02d}*"
        matches = list(data_dir.glob(pattern))
        if matches:
            found += 1

    return found >= episode_count, found


def ensure_subtitles(show_name: str, season: int, needed_episodes: list[int]) -> bool:
    """Ensure subtitles are available, downloading if necessary."""
    data_dir = CACHE_DIR / "data" / show_name
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)

    # Check which episodes are missing
    missing = []
    for ep in needed_episodes:
        pattern = f"*S{season:02d}E{ep:02d}*"
        if not list(data_dir.glob(pattern)):
            missing.append(ep)

    if not missing:
        return True

    print(f"    Downloading {len(missing)} missing subtitle(s) for {show_name} S{season:02d}...")

    try:
        from app.matcher.testing_service import download_subtitles
        result = download_subtitles(show_name, season)
        downloaded = sum(1 for ep in result.get("episodes", [])
                        if ep.get("status") in ("cached", "downloaded"))
        print(f"    Downloaded/cached: {downloaded} episodes")
        return downloaded >= len(needed_episodes) - len(missing)
    except Exception as e:
        print(f"    [WARN] Subtitle download failed: {e}")
        return False


# ── Matcher Runner ──────────────────────────────────────────────────────────

def run_single_test(test_case: TestCase, matcher) -> TestResult:
    """Run a single episode through the matching pipeline."""
    result = TestResult(test_case=test_case)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            t0 = time.time()
            match = matcher.identify_episode(
                video_file=test_case.video_path,
                temp_dir=Path(temp_dir),
                season_number=test_case.season,
            )
            result.elapsed_sec = time.time() - t0

            if match:
                result.predicted_season = match.get("season", match.season if hasattr(match, "season") else 0)
                result.predicted_episode = match.get("episode", match.episode if hasattr(match, "episode") else 0)
                result.confidence = match.get("confidence", match.confidence if hasattr(match, "confidence") else 0)
                result.correct = (result.predicted_episode == test_case.expected_episode)

                # Extract voting details if available
                details = match.get("match_details", {})
                if isinstance(details, dict):
                    result.vote_count = details.get("vote_count", 0)
            else:
                result.error = "No match returned"

    except Exception as e:
        result.elapsed_sec = time.time() - t0 if 't0' in dir() else 0
        result.error = str(e)

    return result


# ── Metrics ─────────────────────────────────────────────────────────────────

@dataclass
class ShowMetrics:
    show: str = ""
    total: int = 0
    correct: int = 0
    no_match: int = 0
    errors: int = 0
    total_time: float = 0.0
    confidences_correct: list[float] = field(default_factory=list)
    confidences_wrong: list[float] = field(default_factory=list)
    mismatches: list[dict] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def avg_time(self) -> float:
        return self.total_time / self.total if self.total else 0.0


def compute_metrics(results: list[TestResult]) -> dict[str, ShowMetrics]:
    """Compute per-show and overall metrics."""
    by_show = defaultdict(lambda: ShowMetrics())
    overall = ShowMetrics(show="OVERALL")

    for r in results:
        key = f"{r.test_case.show_name} S{r.test_case.season:02d}"
        m = by_show[key]
        m.show = key
        m.total += 1
        overall.total += 1

        m.total_time += r.elapsed_sec
        overall.total_time += r.elapsed_sec

        if r.error:
            m.errors += 1
            overall.errors += 1
            m.mismatches.append({
                "episode": r.test_case.label,
                "error": r.error,
            })
        elif r.correct:
            m.correct += 1
            overall.correct += 1
            m.confidences_correct.append(r.confidence)
            overall.confidences_correct.append(r.confidence)
        elif r.predicted_episode == 0:
            m.no_match += 1
            overall.no_match += 1
            m.mismatches.append({
                "episode": r.test_case.label,
                "predicted": "none",
                "confidence": 0,
            })
        else:
            m.confidences_wrong.append(r.confidence)
            overall.confidences_wrong.append(r.confidence)
            m.mismatches.append({
                "episode": r.test_case.label,
                "expected": f"E{r.test_case.expected_episode:02d}",
                "predicted": f"E{r.predicted_episode:02d}",
                "confidence": round(r.confidence, 3),
            })
            overall.mismatches.append({
                "episode": r.test_case.label,
                "expected": f"E{r.test_case.expected_episode:02d}",
                "predicted": f"E{r.predicted_episode:02d}",
                "confidence": round(r.confidence, 3),
            })

    return dict(by_show), overall


# ── Display ─────────────────────────────────────────────────────────────────

def fmt_pct(val: float) -> str:
    return f"{val*100:.1f}%"


def print_table(headers, rows, title=""):
    if title:
        print(f"\n{'='*90}")
        print(f"  {title}")
        print(f"{'='*90}")

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(f"  {header_line}")
    print(f"  {sep_line}")
    for row in rows:
        line = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
        print(f"  {line}")


def display_results(results: list[TestResult], by_show: dict, overall: ShowMetrics):
    """Print full results summary."""

    # Per-episode timeline
    print_table(
        ["Episode", "Result", "Predicted", "Confidence", "Time"],
        [
            [
                r.test_case.label,
                "[OK]" if r.correct else ("[ERR]" if r.error else "[X]"),
                f"E{r.predicted_episode:02d}" if r.predicted_episode else r.error[:30],
                f"{r.confidence:.3f}" if r.confidence else "-",
                f"{r.elapsed_sec:.1f}s",
            ]
            for r in results
        ],
        "EPISODE-BY-EPISODE RESULTS"
    )

    # Per-show accuracy
    print_table(
        ["Show", "Accuracy", "Correct/Total", "Errors", "Avg Time"],
        [
            [
                m.show,
                fmt_pct(m.accuracy),
                f"{m.correct}/{m.total}",
                str(m.errors),
                f"{m.avg_time:.1f}s",
            ]
            for m in sorted(by_show.values(), key=lambda x: x.show)
        ],
        "ACCURACY BY SHOW"
    )

    # Overall
    print_table(
        ["Metric", "Value"],
        [
            ["Total Episodes", str(overall.total)],
            ["Correct", str(overall.correct)],
            ["Accuracy", fmt_pct(overall.accuracy)],
            ["No Match", str(overall.no_match)],
            ["Errors", str(overall.errors)],
            ["Total Time", f"{overall.total_time:.0f}s ({overall.total_time/60:.1f}min)"],
            ["Avg Time/Episode", f"{overall.avg_time:.1f}s"],
        ],
        "OVERALL RESULTS"
    )

    # Confidence distribution
    if overall.confidences_correct:
        correct_sorted = sorted(overall.confidences_correct)
        n = len(correct_sorted)
        print(f"\n  Confidence (correct matches):")
        print(f"    Min: {correct_sorted[0]:.3f}  Median: {correct_sorted[n//2]:.3f}  Max: {correct_sorted[-1]:.3f}")

    if overall.confidences_wrong:
        wrong_sorted = sorted(overall.confidences_wrong)
        n = len(wrong_sorted)
        print(f"  Confidence (wrong matches):")
        print(f"    Min: {wrong_sorted[0]:.3f}  Median: {wrong_sorted[n//2]:.3f}  Max: {wrong_sorted[-1]:.3f}")

    # Mismatches
    if overall.mismatches:
        print_table(
            ["Episode", "Expected", "Predicted", "Confidence"],
            [
                [
                    m["episode"],
                    m.get("expected", "-"),
                    m.get("predicted", m.get("error", "-")),
                    str(m.get("confidence", "-")),
                ]
                for m in overall.mismatches
            ],
            "MISMATCHES (wrong predictions)"
        )
    else:
        print(f"\n  No mismatches -- all predictions correct!")


def save_results(results: list[TestResult], overall: ShowMetrics):
    """Save results to JSON for later analysis."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"accuracy_{timestamp}.json"

    data = {
        "timestamp": timestamp,
        "total": overall.total,
        "correct": overall.correct,
        "accuracy": round(overall.accuracy, 4),
        "total_time_sec": round(overall.total_time, 1),
        "avg_time_sec": round(overall.avg_time, 1),
        "mismatches": overall.mismatches,
        "episodes": [
            {
                "label": r.test_case.label,
                "expected_episode": r.test_case.expected_episode,
                "predicted_episode": r.predicted_episode,
                "correct": r.correct,
                "confidence": round(r.confidence, 4),
                "elapsed_sec": round(r.elapsed_sec, 1),
                "error": r.error,
            }
            for r in results
        ],
    }

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n  Results saved to: {out_path}")
    return out_path


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="End-to-End Matching Accuracy Test")
    parser.add_argument("--subset", type=int, default=0,
                        help="Test only N random episodes (0 = all)")
    parser.add_argument("--show", type=str, default=None,
                        help="Test only a specific show (partial name match)")
    parser.add_argument("--device", type=str, default=None,
                        help="Force device (cpu/cuda), default: auto-detect")
    parser.add_argument("--model", type=str, default="small",
                        help="Whisper model size (tiny/base/small/medium/large)")
    args = parser.parse_args()

    print("=" * 90)
    print("  END-TO-END MATCHING ACCURACY TEST")
    print("  Full pipeline: FFmpeg -> Whisper ASR -> TF-IDF Cosine Similarity")
    print("=" * 90)

    # 1. Discover test cases
    print("\n[1/5] Discovering test videos...")
    test_cases = discover_test_cases(show_filter=args.show)

    if not test_cases:
        print("  No test cases found! Check C:\\Media\\Tests directory.")
        return

    # Group by show for display
    by_show_cases = defaultdict(list)
    for tc in test_cases:
        by_show_cases[f"{tc.show_name} S{tc.season:02d}"].append(tc)

    for show, cases in sorted(by_show_cases.items()):
        print(f"  {show}: {len(cases)} episodes")

    print(f"\n  Total: {len(test_cases)} episodes")

    # 2. Subset if requested
    if args.subset > 0 and args.subset < len(test_cases):
        import random
        random.seed(42)
        test_cases = random.sample(test_cases, args.subset)
        print(f"\n  [SUBSET] Testing {args.subset} random episodes")

    # 3. Ensure subtitles are available
    print("\n[2/5] Checking subtitle availability...")
    shows_to_test = set()
    for tc in test_cases:
        shows_to_test.add((tc.show_name, tc.season))

    for show_name, season in sorted(shows_to_test):
        needed_eps = [tc.expected_episode for tc in test_cases
                      if tc.show_name == show_name and tc.season == season]
        all_present, count = check_subtitles(show_name, season, max(needed_eps))
        if all_present:
            print(f"  {show_name} S{season:02d}: {count} subtitle(s) cached [OK]")
        else:
            print(f"  {show_name} S{season:02d}: {count} cached, downloading missing...")
            ensure_subtitles(show_name, season, needed_eps)

    # 4. Initialize matcher and run tests
    print("\n[3/5] Initializing matchers...")

    # Detect device
    device = args.device
    if not device:
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    print(f"  Device: {device}")
    print(f"  Model: {args.model}")

    # Import here to avoid slow import at top level
    from app.matcher.episode_identification import EpisodeMatcher

    # Create one matcher per show (each needs its own TF-IDF model)
    matchers = {}
    for show_name, season in sorted(shows_to_test):
        matcher = EpisodeMatcher(
            show_name=show_name,
            cache_dir=CACHE_DIR,
            device=device,
            model_name=args.model,
        )
        matchers[(show_name, season)] = matcher
        print(f"  Initialized matcher for {show_name} S{season:02d}")

    # 5. Run tests
    print(f"\n[4/5] Running {len(test_cases)} episode(s) through pipeline...")
    print(f"  Estimated time: ~{len(test_cases) * 30 // 60}-{len(test_cases) * 90 // 60} minutes")
    print()

    results = []
    start_all = time.time()

    for i, tc in enumerate(test_cases, 1):
        matcher = matchers[(tc.show_name, tc.season)]
        print(f"  [{i}/{len(test_cases)}] {tc.label}...", end=" ", flush=True)

        result = run_single_test(tc, matcher)
        results.append(result)

        if result.correct:
            print(f"[OK] E{result.predicted_episode:02d} ({result.confidence:.3f}) in {result.elapsed_sec:.1f}s")
        elif result.error:
            print(f"[ERR] {result.error[:60]} in {result.elapsed_sec:.1f}s")
        else:
            print(f"[X] predicted E{result.predicted_episode:02d} instead of E{tc.expected_episode:02d} "
                  f"({result.confidence:.3f}) in {result.elapsed_sec:.1f}s")

    total_time = time.time() - start_all

    # 6. Results
    print(f"\n[5/5] Computing results...")
    by_show, overall = compute_metrics(results)

    display_results(results, by_show, overall)

    # Save to JSON
    out_path = save_results(results, overall)

    print(f"\n{'='*90}")
    print(f"  Test complete! Total wall time: {total_time:.0f}s ({total_time/60:.1f}min)")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
