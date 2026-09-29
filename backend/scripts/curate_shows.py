"""Curate the show list the subtitle-cache harvester walks (scripts/curated_shows.csv).

The nightly harvest (deploy/subtitle-cache/harvest.sh) walks the CSV top to
bottom and stops at its download budget, so ROW ORDER IS HARVEST PRIORITY.
This script rewrites the CSV from four inputs:

- the current CSV (its rows are kept, in order, unless a rule below drops them);
- the published cache's manifest.json (English shows already shipped but
  missing from the list are added so their new seasons keep arriving);
- a READ-ONLY snapshot of the harvester's subtitle_coverage table;
- TMDB discover ranked by lifetime vote count (fetch_shows_by_vote_count).

Rules (spec: docs/superpowers/specs/2026-08-31-subtitle-cache-expansion-design.md,
section 2):

- Hard filter: original_language == "en". NOT origin_country: Telemundo shows
  are origin US and Spanish-language.
- Outcome exclusion only from healthy-window measurements (2026-05-25 up to
  the 2026-06-11 quota poisoning, or after the harvester repair was complete)
  with a sample of at least 20 episodes and coverage under 20%.
- Genre and network are an ORDERING prior, never a filter: kids, reality, talk
  and news rank last; streaming-only networks rank after broadcast/cable.
- A show TMDB could not describe is never dropped from the current list: an
  infrastructure failure is not a content fact.

Dropping a row does not remove the show from the published cache:
pack_subtitle_cache.py packs everything on disk. It only stops further
harvest spend on that show.

Usage (from backend/, with TMDB_API_KEY exported and a scratch DATABASE_URL):
    uv run python scripts/curate_shows.py \\
        --coverage-db <snapshot of the server's tmdb_cache.sqlite> \\
        --published-manifest <manifest.json from subtitle-cache-latest> \\
        --tmdb-cache <scratch dir>/curation-tmdb-cache.sqlite
"""

import argparse
import csv
import datetime
import io
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

# Idempotent path insert so ``app.*`` imports whether run as
# ``python scripts/curate_shows.py`` or loaded in a test.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from build_subtitle_cache import _bootstrap_config_from_env, _ensure_db_schema
from loguru import logger
from purge_poisoned_coverage import DEFAULT_CUTOFF

from app.matcher import tmdb_persistent_cache
from app.matcher.tmdb_client import fetch_show_details, fetch_shows_by_vote_count

ENGLISH = "en"

# Outcome evidence is trusted only outside the poisoned era. It starts where
# the purge script's window starts (the 2026-06-11 quota collapse). It ends
# when the harvester repair was COMPLETE: #636 (a scraper outage is
# unmeasurable, not zero) merged 2026-09-04 06:13 UTC, so rows written before
# the next UTC midnight may still record an outage as a zero. This is later
# than the purge script's DEFAULT_UNTIL (2026-09-01) on purpose: with that
# bound, Drake & Josh (2/51, written 2026-09-01/03) would be excluded on
# evidence the half-repaired harvester produced.
#
# The window also has a lower bound. The first two harvest days measured
# 12.7% (2026-05-23) and 9.7% (2026-05-24) coverage, against 84% to 99.8% on
# every day from 2026-05-25 through 2026-06-10. Those were early-harvester
# failures (before the late-May provider and matcher fixes, e.g. #202), not
# content facts, and they wrongly excluded 8 shows.
TRUSTED_SINCE = "2026-05-25"
POISONED_SINCE = DEFAULT_CUTOFF
REPAIRED_SINCE = "2026-09-05"
MIN_SAMPLE_EPISODES = 20
MAX_EXCLUDED_RATIO = 0.20

# Harvest-order tiers. Lower harvests first. TIER_RETAINED is the current list
# (nearly all complete on disk, so it costs little quota) plus published
# English shows missing from it.
TIER_RETAINED = 0
TIER_BROADCAST = 1
TIER_STREAMING_ONLY = 2
TIER_LAST = 3

# TMDB genre ids ranked last. Animation (16) is deliberately absent: Archer,
# Rick and Morty, South Park and Futurama are commonly ripped from disc.
GENRE_KIDS = 10762
GENRE_NEWS = 10763
GENRE_REALITY = 10764
GENRE_TALK = 10767
LAST_TIER_GENRES = frozenset({GENRE_KIDS, GENRE_NEWS, GENRE_REALITY, GENRE_TALK})

CSV_FIELDS = [
    "rank",
    "tmdb_id",
    "name",
    "year",
    "origin_country",
    "networks",
    "discdb_discs",
    "original_language",
    "tier",
    "vote_count",
]

_DEFAULT_CSV = Path(__file__).parent / "curated_shows.csv"
_LIVE_TMDB_CACHE = Path("~/.engram/cache/tmdb_cache.sqlite").expanduser()


class DiscoveryIncomplete(RuntimeError):
    """A discover page came back empty; the candidate list would be truncated."""


# TMDB network names that only stream. A show whose EVERY network is in this
# set is demoted (streaming originals get disc releases less often), never
# dropped. Names as TMDB spells them, observed in the 2026-09-26 discover walk.
STREAMING_NETWORKS = frozenset(
    {
        "Amazon",
        "Amazon Freevee",
        "AMC+",
        "Apple TV",
        "Apple TV+",
        "BritBox",
        "CBS All Access",
        "Crunchyroll",
        "Disney+",
        "Freevee",
        "HBO Max",
        "Hulu",
        "Max",
        "Netflix",
        "Paramount+",
        "Peacock",
        "Prime Video",
        "Shudder",
        "The Roku Channel",
        "Tubi",
        "YouTube",
        "YouTube Premium",
    }
)


def _utc_ts(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.UTC).timestamp()


_TRUSTED_SINCE_TS = _utc_ts(TRUSTED_SINCE)
_POISONED_SINCE_TS = _utc_ts(POISONED_SINCE)
_REPAIRED_SINCE_TS = _utc_ts(REPAIRED_SINCE)


class CoverageRow(NamedTuple):
    """One ``subtitle_coverage`` row (a season's harvest outcome)."""

    season: int
    attempted_at: float
    total_episodes: int
    covered_episodes: int


def is_english(details: dict) -> bool:
    """True when TMDB says the show's ORIGINAL language is English.

    The matcher pairs English subtitles with English audio, so this is the
    real constraint. ``origin_country`` is deliberately ignored.
    """
    return (details.get("original_language") or "") == ENGLISH


def is_healthy(attempted_at: float) -> bool:
    """True when a coverage row was written by a harvester that measured fairly.

    That is 2026-05-25 up to 2026-06-11, or from the repair onward.
    """
    return (
        _TRUSTED_SINCE_TS <= attempted_at < _POISONED_SINCE_TS or attempted_at >= _REPAIRED_SINCE_TS
    )


def healthy_totals(rows: list[CoverageRow]) -> tuple[int, int]:
    """Return ``(covered, total)`` episodes across the healthy rows only."""
    healthy = [r for r in rows if is_healthy(r.attempted_at)]
    return (
        sum(r.covered_episodes for r in healthy),
        sum(r.total_episodes for r in healthy),
    )


def outcome_excluded(rows: list[CoverageRow]) -> bool:
    """True when healthy evidence says the providers do not carry this show.

    Needs at least MIN_SAMPLE_EPISODES healthy episodes; a thin or absent
    sample keeps the show so the repaired harvester can measure it.
    """
    covered, total = healthy_totals(rows)
    if total < MIN_SAMPLE_EPISODES:
        return False
    return covered / total < MAX_EXCLUDED_RATIO


def priority_tier(details: dict) -> int:
    """Harvest tier for a newly added show (an ordering prior, never a filter)."""
    genres = {g.get("id") for g in details.get("genres") or []}
    if genres & LAST_TIER_GENRES:
        return TIER_LAST
    networks = [n.get("name", "") for n in details.get("networks") or []]
    if networks and all(n in STREAMING_NETWORKS for n in networks):
        return TIER_STREAMING_ONLY
    return TIER_BROADCAST


@dataclass
class CurationReport:
    """What changed, by tmdb_id, for the run summary and the PR description."""

    retained: int = 0
    kept_unverified: list[int] = field(default_factory=list)
    dropped_language: list[int] = field(default_factory=list)
    excluded_outcome: list[int] = field(default_factory=list)
    added_published: list[int] = field(default_factory=list)
    added_by_tier: Counter = field(default_factory=Counter)
    skipped_no_details: list[int] = field(default_factory=list)


def _row(details: dict, *, tier: int, discdb_discs: str = "") -> dict:
    """A CSV row from a TMDB details payload. ``rank`` is filled at write time."""
    return {
        "rank": "",
        "tmdb_id": str(details["id"]),
        "name": details.get("name") or str(details["id"]),
        "year": (details.get("first_air_date") or "")[:4],
        "origin_country": "/".join(details.get("origin_country") or []),
        "networks": "; ".join(n.get("name", "") for n in details.get("networks") or []),
        "discdb_discs": discdb_discs,
        "original_language": details.get("original_language") or "",
        "tier": str(tier),
        "vote_count": str(details.get("vote_count") or 0),
    }


def _verbatim_row(existing_row: dict) -> dict:
    """Carry a current-list row forward unchanged (TMDB could not describe it)."""
    row = {key: (existing_row.get(key) or "") for key in CSV_FIELDS}
    row["tier"] = str(TIER_RETAINED)
    return row


def _exclusion(tid: int, details: dict, coverage_by_id: dict) -> str | None:
    """``"language"``, ``"outcome"``, or None when the show belongs on the list."""
    if not is_english(details):
        return "language"
    if outcome_excluded(coverage_by_id.get(tid, [])):
        return "outcome"
    return None


def build_curated_rows(
    existing: list[dict],
    published_ids: list[int],
    discovered_ids: list[int],
    details_by_id: dict[int, dict],
    coverage_by_id: dict[int, list[CoverageRow]],
) -> tuple[list[dict], CurationReport]:
    """Assemble the new list in harvest order.

    1. Current rows, in their current order, minus non-English and
       outcome-excluded shows. A row TMDB could not describe is kept verbatim.
    2. Published shows missing from the list (English, not excluded), most
       voted first. Tier 0: they are mostly complete on disk.
    3. Discovered candidates, by tier, then by discovery (vote-count) order.

    A tmdb_id appears once, at its first position.
    """
    report = CurationReport()
    rows: list[dict] = []
    seen: set[int] = set()

    for existing_row in existing:
        tid = int(existing_row["tmdb_id"])
        if tid in seen:
            continue
        seen.add(tid)
        details = details_by_id.get(tid)
        if details is None:
            report.kept_unverified.append(tid)
            rows.append(_verbatim_row(existing_row))
            continue
        reason = _exclusion(tid, details, coverage_by_id)
        if reason == "language":
            report.dropped_language.append(tid)
        elif reason == "outcome":
            report.excluded_outcome.append(tid)
        else:
            report.retained += 1
            rows.append(
                _row(
                    details,
                    tier=TIER_RETAINED,
                    discdb_discs=existing_row.get("discdb_discs") or "",
                )
            )

    published_new = [tid for tid in published_ids if tid not in seen]
    seen.update(published_new)
    report.skipped_no_details.extend(tid for tid in published_new if tid not in details_by_id)
    described = [tid for tid in published_new if tid in details_by_id]
    described.sort(key=lambda tid: -(details_by_id[tid].get("vote_count") or 0))
    for tid in described:
        reason = _exclusion(tid, details_by_id[tid], coverage_by_id)
        if reason == "outcome":
            report.excluded_outcome.append(tid)
        if reason is not None:
            # A published non-English show was never on the list; not a drop.
            continue
        report.added_published.append(tid)
        rows.append(_row(details_by_id[tid], tier=TIER_RETAINED))

    candidates: list[tuple[int, int, dict]] = []
    for order, tid in enumerate(discovered_ids):
        if tid in seen:
            continue
        seen.add(tid)
        details = details_by_id.get(tid)
        if details is None:
            report.skipped_no_details.append(tid)
            continue
        reason = _exclusion(tid, details, coverage_by_id)
        if reason == "outcome":
            report.excluded_outcome.append(tid)
        if reason is not None:
            continue
        tier = priority_tier(details)
        report.added_by_tier[tier] += 1
        candidates.append((tier, order, _row(details, tier=tier)))

    candidates.sort(key=lambda c: (c[0], c[1]))
    rows.extend(row for _, _, row in candidates)
    return rows, report


def load_existing(path: Path) -> list[dict]:
    """Read the current list. Every row must carry a numeric tmdb_id.

    A name-only row would need a fuzzy TMDB lookup to curate; stop and let a
    human resolve it rather than guess.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    bad = [r.get("name") or "?" for r in rows if not (r.get("tmdb_id") or "").strip().isdigit()]
    if bad:
        raise SystemExit(f"{path}: rows without a numeric tmdb_id: {bad}")
    return rows


def load_published(manifest_path: Path) -> list[int]:
    """Show ids in the published cache's manifest.json (v3 keys are tmdb ids)."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    shows = data.get("shows") if isinstance(data, dict) else None
    if not isinstance(shows, dict) or not shows:
        raise SystemExit(f"{manifest_path}: no shows; is this the published manifest.json?")
    ids = [int(key) for key in shows if str(key).isdigit()]
    if not ids:
        raise SystemExit(
            f"{manifest_path}: no numeric show ids; expected a v3 (tmdb_id-keyed) manifest"
        )
    return ids


def load_coverage(db_path: Path) -> dict[int, list[CoverageRow]]:
    """Read ``subtitle_coverage`` from a snapshot, strictly read-only.

    ``mode=ro`` means a wrong path fails instead of creating an empty DB, and
    nothing here can write to the harvester's record.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"coverage snapshot not found: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        fetched = conn.execute(
            "SELECT tmdb_id, season, attempted_at, total_episodes, covered_episodes "
            "FROM subtitle_coverage"
        ).fetchall()
    finally:
        conn.close()
    coverage: dict[int, list[CoverageRow]] = {}
    for tmdb_id, season, attempted_at, total, covered in fetched:
        coverage.setdefault(int(tmdb_id), []).append(
            CoverageRow(int(season), float(attempted_at), int(total), int(covered))
        )
    return coverage


def write_csv(rows: list[dict], path: Path) -> None:
    """Write the list with ``rank`` = harvest position (1..N), LF line endings."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for rank, row in enumerate(rows, 1):
        writer.writerow({**row, "rank": str(rank)})
    Path(path).write_text(buf.getvalue(), encoding="utf-8", newline="")


def discover(pages: int, sleep: float) -> list[dict]:
    """Walk TMDB discover by lifetime vote count, deduped, in rank order.

    Fails closed: ``fetch_shows_by_vote_count`` returns ``[]`` on a network
    failure, and a silently short walk would look like "TMDB has nothing more".
    """
    seen: dict[int, dict] = {}
    for page in range(1, pages + 1):
        results = fetch_shows_by_vote_count(page)
        if not results:
            raise DiscoveryIncomplete(f"TMDB discover page {page} returned no results")
        for show in results:
            if show.get("id") and show["id"] not in seen:
                seen[show["id"]] = show
        time.sleep(sleep)
    return list(seen.values())


def fetch_details(ids: list[int], sleep: float) -> dict[int, dict]:
    """TMDB details for each id; a failed fetch is simply absent from the result."""
    details: dict[int, dict] = {}
    for tid in ids:
        cached = tmdb_persistent_cache.is_cached(f"show_details:{tid}")
        payload = fetch_show_details(tid)
        if payload:
            details[tid] = payload
        else:
            logger.warning(f"No TMDB details for {tid}")
        if not cached:
            time.sleep(sleep)
    return details


def render_report(
    report: CurationReport,
    details_by_id: dict[int, dict],
    coverage_by_id: dict[int, list[CoverageRow]],
    before: int,
    after: int,
) -> str:
    def name(tid: int) -> str:
        return (details_by_id.get(tid) or {}).get("name") or str(tid)

    lines = [f"curated list: {after} rows (was {before})"]
    lines.append(f"  retained from the current list: {report.retained}")
    lines.append(f"  kept unverified (no TMDB details): {len(report.kept_unverified)}")
    lines.append(f"  dropped, not English: {len(report.dropped_language)}")
    for tid in report.dropped_language:
        lang = (details_by_id.get(tid) or {}).get("original_language")
        lines.append(f"    - {name(tid)} (tmdb {tid}, {lang})")
    lines.append(f"  excluded on healthy-window coverage: {len(report.excluded_outcome)}")
    for tid in report.excluded_outcome:
        covered, total = healthy_totals(coverage_by_id.get(tid, []))
        lines.append(f"    - {name(tid)} (tmdb {tid}, {covered}/{total})")
    lines.append(f"  added from the published cache: {len(report.added_published)}")
    tiers = ", ".join(f"tier {t}: {n}" for t, n in sorted(report.added_by_tier.items()))
    lines.append(f"  added from discovery: {sum(report.added_by_tier.values())} ({tiers})")
    lines.append(f"  skipped, no TMDB details: {len(report.skipped_no_details)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Curate the subtitle-cache show list")
    parser.add_argument(
        "--coverage-db",
        type=Path,
        required=True,
        help="READ-ONLY snapshot of the harvester's tmdb_cache.sqlite",
    )
    parser.add_argument(
        "--published-manifest",
        type=Path,
        required=True,
        help="manifest.json from the subtitle-cache-latest release",
    )
    parser.add_argument(
        "--tmdb-cache",
        type=Path,
        required=True,
        help="Scratch TMDB response cache (never the live one)",
    )
    parser.add_argument("--show-list", type=Path, default=_DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=_DEFAULT_CSV)
    parser.add_argument(
        "--pages", type=int, default=100, help="TMDB discover pages (20 shows each) to consider"
    )
    parser.add_argument(
        "--sleep", type=float, default=0.25, help="Seconds between uncached TMDB calls"
    )
    args = parser.parse_args(argv)
    if args.pages <= 0:
        parser.error("--pages must be positive")
    if args.tmdb_cache.expanduser().resolve() == _LIVE_TMDB_CACHE.resolve():
        parser.error("--tmdb-cache must not be the live ~/.engram/cache/tmdb_cache.sqlite")

    existing = load_existing(args.show_list)
    published = load_published(args.published_manifest)
    coverage = load_coverage(args.coverage_db)

    # Every TMDB response this run fetches lands in the scratch cache, so the
    # harvester's cache (and the laptop's frozen backup) are never written.
    tmdb_persistent_cache.close()
    tmdb_persistent_cache.CACHE_DB_PATH = args.tmdb_cache.expanduser()

    _ensure_db_schema()
    _bootstrap_config_from_env()
    from app.services.config_service import get_config_sync

    if not get_config_sync().tmdb_api_key:
        logger.error("TMDB API key not configured (export TMDB_API_KEY); nothing written")
        return 1

    try:
        discovered = discover(args.pages, args.sleep)
    except DiscoveryIncomplete as e:
        logger.error(f"{e}; refusing to write a truncated list")
        return 1
    discovered_en = [s["id"] for s in discovered if s.get("original_language") == ENGLISH]

    wanted = list(dict.fromkeys([int(r["tmdb_id"]) for r in existing] + published + discovered_en))
    details = fetch_details(wanted, args.sleep)

    rows, report = build_curated_rows(existing, published, discovered_en, details, coverage)
    write_csv(rows, args.output)
    print(render_report(report, details, coverage, before=len(existing), after=len(rows)))
    print(f"  discover walk: {len(discovered)} shows, {len(discovered_en)} English")
    return 0


if __name__ == "__main__":
    sys.exit(main())
