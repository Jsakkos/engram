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

import sys
from pathlib import Path

# Idempotent path insert so ``app.*`` imports whether run as
# ``python scripts/curate_shows.py`` or loaded in a test.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

ENGLISH = "en"


def is_english(details: dict) -> bool:
    """True when TMDB says the show's ORIGINAL language is English.

    The matcher pairs English subtitles with English audio, so this is the
    real constraint. ``origin_country`` is deliberately ignored.
    """
    return (details.get("original_language") or "") == ENGLISH
