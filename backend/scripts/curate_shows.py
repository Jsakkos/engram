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
- Outcome exclusion only from healthy-window measurements (before the
  2026-06-11 quota poisoning, or after the harvester repair was complete) with
  a sample of at least 20 episodes and coverage under 20%.
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

import datetime
import sys
from pathlib import Path
from typing import NamedTuple

# Idempotent path insert so ``app.*`` imports whether run as
# ``python scripts/curate_shows.py`` or loaded in a test.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from purge_poisoned_coverage import DEFAULT_CUTOFF

ENGLISH = "en"

# Outcome evidence is trusted only outside the poisoned era. It starts where
# the purge script's window starts (the 2026-06-11 quota collapse). It ends
# when the harvester repair was COMPLETE: #636 (a scraper outage is
# unmeasurable, not zero) merged 2026-09-04 06:13 UTC, so rows written before
# the next UTC midnight may still record an outage as a zero. This is later
# than the purge script's DEFAULT_UNTIL (2026-09-01) on purpose: with that
# bound, Drake & Josh (2/51, written 2026-09-01/03) would be excluded on
# evidence the half-repaired harvester produced.
POISONED_SINCE = DEFAULT_CUTOFF
REPAIRED_SINCE = "2026-09-05"
MIN_SAMPLE_EPISODES = 20
MAX_EXCLUDED_RATIO = 0.20


def _utc_ts(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.UTC).timestamp()


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
    """True when a coverage row was written by a harvester that measured fairly."""
    return attempted_at < _POISONED_SINCE_TS or attempted_at >= _REPAIRED_SINCE_TS


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
