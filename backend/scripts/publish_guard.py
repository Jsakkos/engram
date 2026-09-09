"""Refuse to publish a subtitle cache that is materially smaller than the live one.

The nightly harvest publishes unattended. A regression that resolves zero shows,
or a corpus directory that failed to mount, still produces a structurally VALID
tarball -- verification passes, the manifest is well-formed, the upload succeeds,
and every Engram install silently downgrades to a smaller cache. Only a size
comparison against what is already published catches that class of failure.

Shows and episodes are guarded independently because they fail independently: a
TMDB-resolution regression collapses the show count while barely moving the
episode total, and a truncated harvest does the reverse.

Usage (from backend/):
    uv run python scripts/publish_guard.py --candidate manifest.json
    uv run python scripts/publish_guard.py --candidate manifest.json --allow-shrink

Exit codes:
    0  Publishing is allowed.
    1  Publishing is blocked (shrink beyond tolerance, or an empty candidate).
    2  The guard could not decide (unreadable candidate manifest).
"""

import argparse
import enum
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAG = "subtitle-cache-latest"
DEFAULT_TOLERANCE = 0.02


class ShrinkVerdict(enum.Enum):
    """Why the guard reached its decision. Values are log-facing strings."""

    GROWTH = "growth"
    WITHIN_TOLERANCE = "within-tolerance"
    EPISODES_SHRANK = "episodes-shrank"
    SHOWS_SHRANK = "shows-shrank"
    NO_BASELINE = "no-baseline"
    EMPTY_CANDIDATE = "empty-candidate"


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    verdict: ShrinkVerdict
    reason: str


def manifest_totals(manifest: dict) -> tuple[int, int]:
    """Return ``(show_count, episode_count)`` for a release manifest."""
    shows = manifest.get("shows") or {}
    episodes = 0
    for entry in shows.values():
        episodes += sum((entry.get("episode_counts") or {}).values())
    return len(shows), episodes


def verdict_for(
    candidate: tuple[int, int],
    published: tuple[int, int] | None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> GuardResult:
    """Decide whether ``candidate`` may replace ``published``.

    ``tolerance`` is the fraction a count may fall by and still be accepted; a
    provider dropping a handful of episodes between runs is normal churn, a 20%
    collapse is a defect.
    """
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError(f"tolerance must be in [0, 1], got {tolerance}")

    cand_shows, cand_eps = candidate
    if cand_shows == 0 or cand_eps == 0:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.EMPTY_CANDIDATE,
            reason=f"candidate is empty ({cand_shows} shows, {cand_eps} episodes)",
        )

    if published is None:
        return GuardResult(
            allowed=True,
            verdict=ShrinkVerdict.NO_BASELINE,
            reason=(
                f"no published baseline to compare against; allowing "
                f"{cand_shows} shows / {cand_eps} episodes"
            ),
        )

    pub_shows, pub_eps = published
    floor_shows = pub_shows * (1.0 - tolerance)
    floor_eps = pub_eps * (1.0 - tolerance)

    if cand_shows < floor_shows:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.SHOWS_SHRANK,
            reason=(
                f"show count fell from {pub_shows} to {cand_shows}, below the "
                f"{tolerance:.0%} tolerance floor of {floor_shows:.0f}"
            ),
        )
    if cand_eps < floor_eps:
        return GuardResult(
            allowed=False,
            verdict=ShrinkVerdict.EPISODES_SHRANK,
            reason=(
                f"episode count fell from {pub_eps} to {cand_eps}, below the "
                f"{tolerance:.0%} tolerance floor of {floor_eps:.0f}"
            ),
        )

    if cand_shows >= pub_shows and cand_eps >= pub_eps:
        return GuardResult(
            allowed=True,
            verdict=ShrinkVerdict.GROWTH,
            reason=(f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes"),
        )
    return GuardResult(
        allowed=True,
        verdict=ShrinkVerdict.WITHIN_TOLERANCE,
        reason=(
            f"{pub_shows} -> {cand_shows} shows, {pub_eps} -> {cand_eps} episodes "
            f"(inside the {tolerance:.0%} tolerance)"
        ),
    )


def fetch_published_totals(tag: str) -> tuple[int, int] | None:
    """Download the live release manifest and total it. ``None`` if unavailable.

    A missing or unreadable published manifest is NOT an error: the very first
    publish has no baseline. Only the candidate side is required to be readable.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                ["gh", "release", "download", tag, "--pattern", "manifest.json", "--dir", tmp],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        path = Path(tmp) / "manifest.json"
        if not path.is_file():
            return None
        try:
            return manifest_totals(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard against publishing a shrunken cache")
    parser.add_argument("--candidate", required=True, help="Path to the candidate manifest.json")
    parser.add_argument("--cache-tag", default=DEFAULT_TAG, help="Release tag to compare against")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Fraction a count may fall and still publish (default: {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="Report the comparison but always exit 0 (deliberate corpus pruning)",
    )
    args = parser.parse_args()

    try:
        candidate = manifest_totals(json.loads(Path(args.candidate).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"publish-guard: cannot read candidate manifest {args.candidate}: {exc}")
        return 2

    published = fetch_published_totals(args.cache_tag)
    result = verdict_for(candidate, published, args.tolerance)
    print(f"publish-guard: {result.verdict.value}: {result.reason}")

    if result.allowed:
        return 0
    if args.allow_shrink:
        print("publish-guard: --allow-shrink set; publishing anyway")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
