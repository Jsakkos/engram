"""One-time maintenance: delete coverage rows written during a known-bad window.

On 2026-06-11/12 an OpenSubtitles quota exhaustion caused the cache builder to
record 664 seasons as zero coverage. Measured rates either side of the cutoff:
94.3% before, 14.5% after. Those rows are already inert (expired by age), but
they corrupt outcome-based show curation, which reads historical coverage to
decide what to keep.

Deleting converts known-bad data into no-data, which the repaired harvester
refills honestly. Legitimately-zero seasons inside the window are re-measured;
that costs quota once and is the point.

THE POISONED ERA ENDS WHEN THE REPAIR LANDS, NOT WHEN THE WORST DAYS END.
2026-06-11/12 were the worst of it, but the harvester stayed broken until the
2026-08-31 repair branch, so EVERY run in between could record an
infrastructure failure as a content fact. The evidence is the 2026-07-06 run:
it retrieved 1 episode out of 291, a 0.3% rate against a healthy baseline of
94.3%. That is not a corpus with no subtitles, it is a broken harvest. Per-run
sub-60% rows and each run's own overall rate:

    2026-06-11   46 sub-60      8.9%   poisoned
    2026-06-12  716 sub-60      9.5%   poisoned
    2026-06-15    0 sub-60    100.0%   healthy, untouched
    2026-06-21    3 sub-60     72.5%   poisoned
    2026-06-22   43 sub-60     40.8%   poisoned
    2026-07-06    9 sub-60      0.3%   poisoned
    2026-08-31    0 sub-60     99.7%   healthy, untouched

Note the two healthy runs contribute ZERO sub-60% rows, so covering them with
the window costs nothing: the ratio condition preserves them exactly as it
always did. Widening the window does not widen what is deleted.

THE WINDOW IS STILL BOUNDED ON BOTH SIDES, AND THAT IS LOAD-BEARING. From the
repair onward the harvester records HONEST low-coverage rows, and those rows
ARE the skip-list that stops dead seasons burning quota on every nightly run.
A purge with no upper bound would delete exactly those and undo the repair.
That, not the June days, is the risk ``--until`` exists to close off.

Dates are parsed as UTC, matching the UTC epoch ``attempted_at`` is written
with, so a laptop and a server compute the same window.

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

# 2026-06-11 00:00 UTC. The first run whose success rate collapsed; see the
# 2026-08-31 harvester-repair spec for the per-day breakdown.
DEFAULT_CUTOFF = "2026-06-11"
# 2026-09-01 00:00 UTC, exclusive: the date the harvester repair lands. The
# window closes when the repair ships, NOT when the two worst outage days end,
# because every run before it used a harvester that could not distinguish "the
# provider has nothing" from "we got nothing" -- see the per-run table in the
# module docstring, and the 0.3% run of 2026-07-06 in particular. From this
# date the harvester records ``degraded`` instead of a false zero, so a
# low-coverage row is a content fact and must survive.
DEFAULT_UNTIL = "2026-09-01"

_SQL_WHERE = "attempted_at >= ? AND attempted_at < ? AND coverage_ratio < ?"


def purge(since: float, until: float, min_ratio: float, apply: bool) -> int:
    """Delete (or count, when ``apply`` is False) poisoned coverage rows.

    A row is poisoned when it was attempted at or after ``since``, BEFORE
    ``until``, AND fell below ``min_ratio``. Rows that succeeded during the
    window are preserved: the outage suppressed results, it did not fabricate
    them. Rows at or after ``until`` are preserved because honest low coverage
    is the skip-list the repaired harvester depends on.

    ``until`` is required, not defaulted: an unbounded purge is a foot-gun,
    and a caller should have to say out loud that it wants one.

    Returns the number of rows matched.
    """
    if not tmdb_persistent_cache.CACHE_DB_PATH.exists():
        return 0

    conn = tmdb_persistent_cache.get_conn()
    params = (since, until, min_ratio)
    matched = conn.execute(
        f"SELECT COUNT(*) FROM subtitle_coverage WHERE {_SQL_WHERE}", params
    ).fetchone()[0]

    if apply and matched:
        conn.execute(f"DELETE FROM subtitle_coverage WHERE {_SQL_WHERE}", params)
        conn.commit()

    return matched


def _parse_utc_date(value: str) -> float:
    """Parse YYYY-MM-DD as midnight UTC and return a POSIX timestamp.

    ``attempted_at`` is a UTC epoch from ``time.time()``. Parsing with the
    naive ``.timestamp()`` would interpret the date in the machine's LOCAL
    zone, shifting the window by the UTC offset and making a laptop and a
    headless server disagree about which rows are in scope.
    """
    parsed = datetime.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return parsed.timestamp()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=str,
        default=DEFAULT_CUTOFF,
        help=f"Window start YYYY-MM-DD (UTC), inclusive (default: {DEFAULT_CUTOFF})",
    )
    parser.add_argument(
        "--until",
        type=str,
        default=DEFAULT_UNTIL,
        help=(
            f"Window end YYYY-MM-DD (UTC), EXCLUSIVE (default: {DEFAULT_UNTIL}). "
            "Rows on or after this date are honest post-outage measurements and "
            "are preserved."
        ),
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.6,
        help="Rows below this coverage ratio are treated as poisoned (default: 0.6)",
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry run)")
    args = parser.parse_args()

    if not 0 <= args.min_ratio < 1:
        # 1.0 would match every row below 100% coverage, i.e. very nearly the
        # whole table, and --apply on a first invocation would do it without
        # anyone having seen a dry run.
        parser.error("--min-ratio must be >= 0 and < 1")

    since = _parse_utc_date(args.since)
    until = _parse_utc_date(args.until)
    if until <= since:
        parser.error("--until must be after --since")

    n = purge(since, until, args.min_ratio, args.apply)

    verb = "Deleted" if args.apply else "Would delete (dry run)"
    print(
        f"{verb} {n} coverage rows below {args.min_ratio:.0%} coverage "
        f"in the window {args.since} (inclusive) .. {args.until} (exclusive), UTC"
    )
    print("Rows outside that window are preserved, including honest post-outage low coverage.")
    if not args.apply and n:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
