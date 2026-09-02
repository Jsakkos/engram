"""One-time maintenance: delete coverage rows written during a known-bad window.

On 2026-06-11/12 an OpenSubtitles quota exhaustion caused the cache builder to
record 664 seasons as zero coverage. Measured rates either side of the cutoff:
94.3% before, 14.5% after. Those rows are already inert (expired by age), but
they corrupt outcome-based show curation, which reads historical coverage to
decide what to keep.

Deleting converts known-bad data into no-data, which the repaired harvester
refills honestly. Legitimately-zero seasons inside the window are re-measured;
that costs quota once and is the point.

Defaults to a dry run. Pass --apply to mutate.

Usage (from backend/):
    uv run python scripts/purge_poisoned_coverage.py
    uv run python scripts/purge_poisoned_coverage.py --apply
"""

import argparse
import datetime
import sys
from pathlib import Path

_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.matcher import tmdb_persistent_cache  # noqa: E402

# 2026-06-11 00:00 local. The first run whose success rate collapsed; see the
# 2026-08-31 harvester-repair spec for the per-day breakdown.
DEFAULT_CUTOFF = "2026-06-11"


def purge(since: float, min_ratio: float, apply: bool) -> int:
    """Delete (or count, when ``apply`` is False) poisoned coverage rows.

    A row is poisoned when it was attempted at or after ``since`` AND fell
    below ``min_ratio``. Rows that succeeded during the window are preserved:
    the outage suppressed results, it did not fabricate them.

    Returns the number of rows matched.
    """
    if not tmdb_persistent_cache.CACHE_DB_PATH.exists():
        return 0

    conn = tmdb_persistent_cache.get_conn()
    matched = conn.execute(
        "SELECT COUNT(*) FROM subtitle_coverage WHERE attempted_at >= ? AND coverage_ratio < ?",
        (since, min_ratio),
    ).fetchone()[0]

    if apply and matched:
        conn.execute(
            "DELETE FROM subtitle_coverage WHERE attempted_at >= ? AND coverage_ratio < ?",
            (since, min_ratio),
        )
        conn.commit()

    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=str,
        default=DEFAULT_CUTOFF,
        help="Cutoff date YYYY-MM-DD; rows attempted on/after this are candidates",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.6,
        help="Rows below this coverage ratio are treated as poisoned (default: 0.6)",
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry run)")
    args = parser.parse_args()

    since = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    n = purge(since, args.min_ratio, args.apply)

    verb = "Deleted" if args.apply else "Would delete (dry run)"
    print(f"{verb} {n} coverage rows attempted on/after {args.since} below {args.min_ratio:.0%}")
    if not args.apply and n:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
