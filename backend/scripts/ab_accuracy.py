"""A/B accuracy comparison: scraped TF-IDF path vs. precomputed-vector cache.

Runs scripts/test_matching_accuracy.py twice against the same ground-truth
videos -- once with the precomputed cache absent (baseline: scraped SRT ->
TF-IDF) and once with it present (precomputed hashed-vector path) -- then
diffs accuracy, per-episode correctness, and timing.

EpisodeMatcher auto-selects the precomputed path whenever
``CACHE_DIR/precomputed/`` covers the show/season, so the only thing this
script toggles between runs is the presence of that directory.

Usage (from backend/):
    uv run python scripts/ab_accuracy.py --precomputed /path/to/precomputed
    uv run python scripts/ab_accuracy.py --precomputed cache.tar.gz --show "The Expanse"

``--precomputed`` accepts either an extracted ``precomputed/`` directory or the
``engram-subtitle-cache.tar.gz`` produced by build_subtitle_cache.py. It must
cover the shows under test, otherwise run B silently falls back to scraping and
the comparison is meaningless.

Exit code is non-zero if the precomputed path regresses accuracy beyond
``--tolerance`` (default 2 percentage points).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts_dir))

from test_matching_accuracy import CACHE_DIR  # noqa: E402


def _resolve_precomputed(src: Path, workdir: Path) -> Path:
    """Return a path to a ``precomputed/`` directory, extracting a tarball if needed."""
    if src.is_dir():
        if src.name == "precomputed":
            return src
        inner = src / "precomputed"
        if inner.is_dir():
            return inner
        raise SystemExit(f"No 'precomputed/' directory found in {src}")
    if src.is_file() and (src.suffix == ".gz" or tarfile.is_tarfile(src)):
        with tarfile.open(src) as tar:
            tar.extractall(workdir, filter="data")
        inner = workdir / "precomputed"
        if not inner.is_dir():
            raise SystemExit(f"Tarball {src} has no top-level 'precomputed/' directory")
        return inner
    raise SystemExit(f"--precomputed must be an existing directory or .tar.gz: {src}")


def _run_accuracy(passthrough: list[str], label: str) -> dict:
    """Run test_matching_accuracy.py as a subprocess; stream output; return its result JSON."""
    print(f"\n{'#' * 70}\n#  {label}\n{'#' * 70}", flush=True)
    cmd = [sys.executable, str(_scripts_dir / "test_matching_accuracy.py"), *passthrough]
    results_path: str | None = None

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if "Results saved to:" in line:
            results_path = line.split("Results saved to:", 1)[1].strip()
    proc.wait()

    if proc.returncode != 0:
        raise SystemExit(f"accuracy run failed (exit {proc.returncode})")
    if not results_path:
        raise SystemExit("accuracy run did not report a results file")
    return json.loads(Path(results_path).read_text())


def _report(baseline: dict, precomputed: dict, tolerance: float) -> int:
    """Print the A/B comparison; return a non-zero exit code on regression."""

    def _row(label: str, a: str, b: str) -> None:
        print(f"  {label:<22}{a:>16}{b:>16}")

    a_acc = baseline.get("accuracy", 0.0)
    b_acc = precomputed.get("accuracy", 0.0)

    print(f"\n{'=' * 60}\n  A/B ACCURACY COMPARISON\n{'=' * 60}")
    _row("metric", "baseline", "precomputed")
    _row("accuracy", f"{a_acc * 100:.1f}%", f"{b_acc * 100:.1f}%")
    _row(
        "correct/total",
        f"{baseline.get('correct')}/{baseline.get('total')}",
        f"{precomputed.get('correct')}/{precomputed.get('total')}",
    )
    _row(
        "avg time/episode",
        f"{baseline.get('avg_time_sec')}s",
        f"{precomputed.get('avg_time_sec')}s",
    )

    a_eps = {e["label"]: e for e in baseline.get("episodes", [])}
    b_eps = {e["label"]: e for e in precomputed.get("episodes", [])}
    disagreements = [
        (label, a_eps[label]["correct"], b_eps[label]["correct"])
        for label in sorted(a_eps.keys() & b_eps.keys())
        if a_eps[label]["correct"] != b_eps[label]["correct"]
    ]
    if disagreements:
        print("\n  Episodes where the two paths disagree:")
        for label, a_ok, b_ok in disagreements:
            print(
                f"    {label}: baseline={'OK' if a_ok else 'X'}  "
                f"precomputed={'OK' if b_ok else 'X'}"
            )
    else:
        print("\n  Both paths agree on every episode.")

    delta = b_acc - a_acc
    print(f"\n  Accuracy delta (precomputed - baseline): {delta * 100:+.1f} pp")
    if delta < -tolerance:
        print(f"  REGRESSION: precomputed is more than {tolerance * 100:.0f} pp worse.")
        return 1
    print(f"  OK: precomputed is within the {tolerance * 100:.0f} pp tolerance.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B accuracy: scraped vs. precomputed")
    parser.add_argument(
        "--precomputed",
        required=True,
        help="Path to a precomputed/ directory or engram-subtitle-cache.tar.gz",
    )
    parser.add_argument("--show", default=None, help="Limit to one show (partial name match)")
    parser.add_argument("--subset", type=int, default=0, help="Test only N random episodes")
    parser.add_argument("--model", default="small", help="Whisper model size")
    parser.add_argument("--device", default=None, help="Force device (cpu/cuda)")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        help="Max accuracy drop (fraction) before flagging a regression",
    )
    args = parser.parse_args()

    passthrough: list[str] = []
    if args.show:
        passthrough += ["--show", args.show]
    if args.subset:
        passthrough += ["--subset", str(args.subset)]
    passthrough += ["--model", args.model]
    if args.device:
        passthrough += ["--device", args.device]

    live = CACHE_DIR / "precomputed"
    stash = CACHE_DIR / "precomputed.ab-stash"

    with tempfile.TemporaryDirectory() as tmp:
        src = _resolve_precomputed(Path(args.precomputed).expanduser().resolve(), Path(tmp))

        # Stash any installed precomputed cache so run A cannot see it.
        if live.exists():
            shutil.rmtree(stash, ignore_errors=True)
            shutil.move(str(live), str(stash))
        try:
            baseline = _run_accuracy(passthrough, "A: BASELINE (scraped SRT -> TF-IDF)")

            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, live)
            try:
                precomputed = _run_accuracy(passthrough, "B: PRECOMPUTED (hashed-vector cache)")
            finally:
                shutil.rmtree(live, ignore_errors=True)
        finally:
            # Restore the user's original cache state.
            if stash.exists():
                shutil.move(str(stash), str(live))

    return _report(baseline, precomputed, args.tolerance)


if __name__ == "__main__":
    sys.exit(main())
