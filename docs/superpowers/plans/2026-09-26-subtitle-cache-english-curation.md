# Subtitle Cache English Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the exhausted 448-row `backend/scripts/curated_shows.csv` with an English-only, outcome-aware, priority-ordered list of roughly 1,370 shows, so the nightly server harvest resumes growing the published subtitle cache.

**Architecture:** A new standalone script, `backend/scripts/curate_shows.py`, owns the curation rules as pure functions (language filter, healthy-window outcome exclusion, genre/network priority tiers, row assembly) plus a thin I/O shell (read-only coverage snapshot, published manifest, TMDB discovery through the existing `fetch_shows_by_vote_count` / `fetch_show_details`). It rewrites the CSV in harvest order; `build_subtitle_cache.py` already walks the CSV top to bottom, so row order *is* the nightly budget priority. The generated CSV is committed and reviewed like code, then rolled out to the server checkout by hand.

**Tech Stack:** Python 3.11+, pytest, ruff, `uv`. TMDB v3/v4 via `app.matcher.tmdb_client`. SQLite (`subtitle_coverage` table in `tmdb_cache.sqlite`). Server: Ubuntu, systemd user timer, `gh`.

**Spec:** section 2 "Curation" of `docs/superpowers/specs/2026-08-31-subtitle-cache-expansion-design.md`. Phase 1 (harvester repair, #631 / #635 / #636) and Phase 3 (server deployment, runbook `docs/development/subtitle-cache-server.md`) are already shipped.

**Branch:** `feat/subtitle-cache-english-curation`, cut from `main`. (This plan itself lands on `docs/subtitle-cache-english-curation-plan`.)

---

## Why now

Since 2026-09-18 every nightly run on `jsakkos@192.168.1.122` (`engram-subtitle-cache.timer` -> `deploy/subtitle-cache/harvest.sh`) exits 0 with no growth. The published `subtitle-cache-latest` manifest (built 2026-09-26T03:27Z) holds **524 shows / 42,108 episodes** and has not moved. The 2026-09-26 run spent 7.6 minutes selecting 448 shows and 51 minutes walking them, all served from disk: the list is exhausted, not the quota.

## Measured baseline (2026-09-26)

All numbers below were measured for this plan. Coverage came from a **read-only** open (`?mode=ro`) of the server's `~/.engram/cache/tmdb_cache.sqlite` (authoritative; the laptop copy is a frozen 2026-09-13 backup). TMDB language, genre and network data came from the server's cached `show_details:*` payloads (all 448 rows present) and a direct walk of `/discover/tv?sort_by=vote_count.desc`. No OpenSubtitles call was made.

**Current list against the English filter (`original_language == "en"`):**

| original_language | rows | in published cache | published episodes |
|---|---|---|---|
| en | 438 | 433 | 37,667 |
| es | 8 | 5 | 241 |
| ja | 2 | 2 | 302 |

The 8 Spanish rows are exactly the case the spec warns about: all carry `origin_country` US (one CO/US), for example *El Señor de los Cielos*, *Pasión de Gavilanes*, *La Reina del Sur*, *El Chapo*, *Rubi*. The 2 Japanese rows are *Dragon Ball Z* and *Elfen Lied*, which PR #437 deliberately kept for their disc entries; the spec's hard filter now drops them.

**Outcome exclusion (healthy window, sample >= 20 episodes, coverage < 20%):** 2 English rows.

| Show | tmdb_id | healthy covered / total | in published cache |
|---|---|---|---|
| The Tom and Jerry Show | 7842 | 7 / 48 | yes |
| Ned's Declassified School Survival Guide | 1600 | 0 / 22 | no |

A further 51 English rows have no healthy-window measurement and 65 have a healthy sample under 20 episodes. Both groups stay, per the spec.

**The healthy-window upper bound is load-bearing.** With a 2026-09-01 bound (the purge script's `DEFAULT_UNTIL`) a third show, *Drake & Josh* (2 / 51), is excluded. Its low rows were written on 2026-09-01 and 2026-09-03 by a partially repaired harvester: #631 (quota) had landed but #636 (scraper outage treated as unmeasurable) merged only on 2026-09-04 06:13 UTC. This plan therefore uses **2026-09-05 00:00 UTC** as the start of the repaired window. The 2026-09-04 and 2026-09-05 bounds give the same 2 exclusions.

**Net retained from the current list: 448 - 10 - 2 = 436 rows.**

**The published cache versus the list:** 84 published shows are not in the CSV (22 English, 62 not: 45 ja, 8 es, 5 ko, 1 each tr/fr/de/ca). Across the whole published cache, 69 shows / 3,867 episodes are non-English.

**Candidates from `fetch_shows_by_vote_count`** (TMDB reports 1,001 pages / 20,001 results):

| Discover depth | vote floor | English | English, not in CSV | ...and not published |
|---|---|---|---|---|
| 300 (15 pages) | 1,713 | 238 | 48 | 32 |
| 500 (25 pages) | 1,119 | 384 | 86 | 70 |
| 1,000 (50 pages) | 589 | 715 | 281 | 263 |
| 1,500 (75 pages) | 372 | 1,057 | 622 | 604 |
| **2,000 (100 pages)** | **270** | **1,369** | **934** | **915** |

**Additions at 100 pages, by priority tier** (TMDB `number_of_episodes`):

| Tier | Meaning | shows | episodes |
|---|---|---|---|
| 0 | published English shows missing from the CSV | 22 | 963 |
| 1 | broadcast/cable scripted (incl. adult animation) | 438 | 28,616 |
| 2 | streaming-only networks | 366 | 7,403 |
| 3 | kids, reality, talk, news | 111 | 30,498 |

Tier 3 is dominated by daily/talk shows: *The Daily Show* (4,272 episodes), *Sesame Street* (3,667), *The Tonight Show Starring Jimmy Fallon* (2,421), *The Late Show with Stephen Colbert* (1,816), *Raw* (1,747).

**Projected list: 436 + 22 + 915 = about 1,373 rows**, three times the current list.

**Budget projection.** The 2026-08-31 ER diagnostic cost 94 downloads for 331 episodes (about 0.28 downloads per episode). At 900 downloads a night: tier 1 is about 8,000 downloads (9 nights), tier 2 about 2,100 (2 to 3 nights), tier 3 about 8,500 (10 nights). Scraper-served seasons cost no quota, so these are upper bounds. Tier 3 is not reached for roughly two weeks; see "Decisions for the reviewer".

## Design decisions (and why)

1. **Filter on `original_language`, never `origin_country`.** The 8 Telemundo rows prove `origin_country` wrong.
2. **Dropping a row never shrinks the published cache.** `harvest.sh` packs with `pack_subtitle_cache.py`, which walks every show on disk and never reads the CSV. Removing a row only stops further harvest spend on it. The 10 non-English rows and 2 outcome exclusions stay published (7 + 1 of them are published today), so the publish guard sees growth and `--allow-shrink` is **not** needed. Removing the 69 non-English shows from the tarball is a separate, deliberate shrink (about 13% of shows, 9% of episodes) that needs `--allow-shrink`; it is out of scope here and listed under "Decisions for the reviewer".
3. **Keep the current list's order at the top.** Those 436 rows are nearly all complete on disk and cost no quota; keeping their order makes the CSV diff reviewable. Tier 0 then appends the 22 published-but-unlisted English shows so their new seasons keep arriving.
4. **Tiers are an ordering prior, never a filter.** Kids (TMDB genre 10762), reality (10764), talk (10767) and news (10763) rank last. Animation alone (16) is not demoted, so *Archer* (10283), *Rick and Morty* (60625), *South Park* (2190) and *Futurama* (615) stay in the high-priority body. Talk and news are added to the spec's kids/reality pair because daily topical shows are never sold as season discs and are by far the largest episode counts.
5. **Streaming-only is tier 2, not excluded.** The original 599-row list excluded "streaming-only titles that have no physical disc release" (commit `60386a3c`). Some streaming originals do ship on disc (*Stranger Things*, *Daredevil*), so a show whose every network is a streaming service is demoted, not dropped.
6. **Infrastructure failure never drops a row.** An existing row whose TMDB details cannot be fetched is kept verbatim (the Phase 1 lesson: "we got nothing" is not "there is nothing"). A discover page that returns nothing aborts the run without writing, rather than writing a truncated list.
7. **Coverage is read from a snapshot, read-only.** The implementer copies the server's cache DB with SQLite's backup API from a `mode=ro` connection, and the script opens the copy with `mode=ro` too. TMDB responses fetched during curation go to a scratch cache (`--tmdb-cache`, which refuses the live `~/.engram/cache/tmdb_cache.sqlite`). The laptop never harvests.

## Context you need before starting

**Run everything from `backend/`** with `uv`; never bare `python` or `pip`:

```bash
uv run pytest tests/unit/test_curate_shows.py -v
uv run ruff check .
uv run ruff format .
```

**Ruff config:** line length 100, double quotes, rules E/F/I/UP/B. `scripts/*.py` ignores E402 (imports after the `sys.path` insert are expected).

**Loading scripts in tests:** standalone scripts are loaded through the session fixtures in `backend/tests/unit/conftest.py` (`_load_script_module`), never `from scripts.x import y`. The new script imports from `build_subtitle_cache` and `purge_poisoned_coverage`, so its fixture depends on `bsc` and `ppc` to put those in `sys.modules` first (the same trick `psc` uses).

**The TMDB cache is isolated in tests already:** `backend/tests/conftest.py::_isolate_tmdb_persistent_cache` (autouse) redirects `tmdb_persistent_cache.CACHE_DB_PATH` to `tmp_path` and restores it afterwards, so a test that lets `main()` reassign that global is safe.

**House style:** no em dashes and no en dashes anywhere (code, comments, docs, commits). Check a file with:

```bash
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' path/to/file
```

Expected: `0`. (A plain character-class grep false-positives on arrows and ellipses in Git Bash, which is why the byte alternation is used.)

**Never run `build_subtitle_cache.py` on the laptop.** The OpenSubtitles quota (about 1,000/day) is per account and the server budgets 900 a night. Nothing in this plan downloads subtitles.

**Never delete `backend/engram.db`.** The curation run uses a scratch `DATABASE_URL` so the TMDB key it bootstraps lands in a throwaway DB instead.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/scripts/curate_shows.py` | Create | Curation rules (pure) + CLI that rewrites the CSV |
| `backend/tests/unit/test_curate_shows.py` | Create | Unit tests for every rule, the I/O helpers and `main()` |
| `backend/tests/unit/conftest.py` | Modify | Add the `cur` session fixture |
| `backend/scripts/curated_shows.csv` | Regenerate | The new list (Task 7) |
| `backend/scripts/curated_shows.txt` | Delete | Unreferenced name-only twin of the CSV; would silently drift |
| `docs/development/subtitle-cache.md` | Modify | "Curating the show list" section |
| `docs/development/subtitle-cache-server.md` | Modify | "Updating the show list" section |
| `CHANGELOG.md` | Modify | `[Unreleased]` entry |

New CSV columns (a superset of today's; `build_subtitle_cache._read_show_list` reads only `tmdb_id` and `name`, and `migrate_subtitle_cache_keys.load_curated_map` reads the same two):

`rank,tmdb_id,name,year,origin_country,networks,discdb_discs,original_language,tier,vote_count`

`rank` changes meaning from "TMDB vote rank at first curation" to "harvest position" (1..N). Nothing reads it.

---

## Task 1: Script skeleton, fixture and the language filter

**Files:**
- Create: `backend/scripts/curate_shows.py`
- Create: `backend/tests/unit/test_curate_shows.py`
- Modify: `backend/tests/unit/conftest.py` (after the `psc` fixture)

- [ ] **Step 1: Add the fixture**

Append to `backend/tests/unit/conftest.py`, directly after the `psc` fixture:

```python
@pytest.fixture(scope="session")
def cur(bsc, ppc):
    """The curate_shows.py module, loaded once per pytest session.

    Depends on ``bsc`` and ``ppc`` so ``build_subtitle_cache`` and
    ``purge_poisoned_coverage`` are already in ``sys.modules`` when the
    script's module-level ``from ... import`` lines run (spec-loading does not
    put ``scripts/`` on ``sys.path``).
    """
    return _load_script_module("curate_shows")
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/unit/test_curate_shows.py`:

```python
"""Unit tests for scripts/curate_shows.py (subtitle-cache show-list curation).

Every test builds synthetic TMDB payloads and coverage rows. None calls TMDB
or reads ~/.engram; the `cur` fixture lives in conftest.py.
"""

import datetime
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.config_service as cfg_svc


def _details(
    tid,
    name="Show",
    *,
    lang="en",
    genres=(),
    networks=("ABC",),
    votes=1000,
    year="2005",
    origin=("US",),
):
    """A minimal TMDB /tv/{id} payload with only the fields curation reads."""
    return {
        "id": tid,
        "name": name,
        "original_language": lang,
        "genres": [{"id": g} for g in genres],
        "networks": [{"name": n} for n in networks],
        "vote_count": votes,
        "first_air_date": f"{year}-01-01",
        "origin_country": list(origin),
    }


def _ts(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.UTC).timestamp()


def _cov(cur, day, total, covered, season=1):
    return cur.CoverageRow(season, _ts(day), total, covered)


@pytest.mark.unit
class TestIsEnglish:
    def test_english_original_language_passes(self, cur):
        assert cur.is_english(_details(1, lang="en")) is True

    def test_spanish_show_with_us_origin_is_rejected(self, cur):
        # The Telemundo case: origin_country says US, the audio is Spanish.
        assert cur.is_english(_details(2, lang="es", origin=("US",))) is False

    def test_missing_language_is_rejected(self, cur):
        details = _details(3)
        del details["original_language"]
        assert cur.is_english(details) is False
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: ERROR, `ImportError: Cannot load script module 'curate_shows'` (the file does not exist yet).

- [ ] **Step 4: Create the skeleton with the language filter**

Create `backend/scripts/curate_shows.py`:

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py backend/tests/unit/conftest.py
git commit -m "feat(subtitle-cache): curation script skeleton with English-only filter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Healthy-window outcome exclusion

**Files:**
- Modify: `backend/scripts/curate_shows.py`
- Test: `backend/tests/unit/test_curate_shows.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_curate_shows.py`:

```python
@pytest.mark.unit
class TestHealthyWindow:
    def test_before_the_poisoned_window_is_healthy(self, cur):
        assert cur.is_healthy(_ts("2026-06-10")) is True

    def test_start_of_the_poisoned_window_is_unhealthy(self, cur):
        assert cur.is_healthy(_ts("2026-06-11")) is False

    def test_partially_repaired_days_are_unhealthy(self, cur):
        # 2026-09-01..04: #631 had landed, #636 (scraper outage) had not.
        # Drake & Josh's 2/51 was written on these days.
        assert cur.is_healthy(_ts("2026-09-03")) is False

    def test_repaired_window_is_healthy(self, cur):
        assert cur.is_healthy(_ts("2026-09-05")) is True


@pytest.mark.unit
class TestOutcomeExcluded:
    def test_low_healthy_coverage_with_enough_sample_is_excluded(self, cur):
        # The Tom and Jerry Show: 7 of 48 healthy episodes.
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 48, 7)]) is True

    def test_thin_sample_is_kept(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 19, 0)]) is False

    def test_exactly_twenty_percent_is_kept(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-05-01", 50, 10)]) is False

    def test_poisoned_window_zeros_are_ignored(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-06-12", 200, 0)]) is False

    def test_poisoned_zeros_do_not_dilute_a_healthy_measurement(self, cur):
        rows = [
            _cov(cur, "2026-05-01", 30, 29, season=1),
            _cov(cur, "2026-06-12", 200, 0, season=2),
        ]
        assert cur.outcome_excluded(rows) is False

    def test_low_coverage_measured_after_the_repair_is_excluded(self, cur):
        assert cur.outcome_excluded([_cov(cur, "2026-09-20", 25, 2)]) is True

    def test_no_measurement_is_kept(self, cur):
        assert cur.outcome_excluded([]) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_curate_shows.py -v -k "Healthy or Outcome"`
Expected: FAIL, `AttributeError: module 'curate_shows' has no attribute 'CoverageRow'` (and `is_healthy`).

- [ ] **Step 3: Implement**

In `backend/scripts/curate_shows.py`, replace the import block and the `ENGLISH` line with:

```python
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
```

Then append after `is_english`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py
git commit -m "feat(subtitle-cache): exclude shows only on healthy-window coverage evidence

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 3: Priority tiers

**Files:**
- Modify: `backend/scripts/curate_shows.py`
- Test: `backend/tests/unit/test_curate_shows.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@pytest.mark.unit
class TestPriorityTier:
    def test_adult_animation_on_cable_is_not_demoted(self, cur):
        archer = _details(10283, "Archer", genres=(16, 35), networks=("FX", "FXX"))
        assert cur.priority_tier(archer) == cur.TIER_BROADCAST

    def test_rick_and_morty_is_not_demoted(self, cur):
        rm = _details(60625, "Rick and Morty", genres=(16, 35, 10765, 10759), networks=("Adult Swim",))
        assert cur.priority_tier(rm) == cur.TIER_BROADCAST

    @pytest.mark.parametrize("genre", [10762, 10764, 10767, 10763])
    def test_kids_reality_talk_and_news_rank_last(self, cur, genre):
        assert cur.priority_tier(_details(1, genres=(genre,))) == cur.TIER_LAST

    def test_streaming_only_show_is_tier_two(self, cur):
        assert cur.priority_tier(_details(1, networks=("Netflix",))) == cur.TIER_STREAMING_ONLY

    def test_apple_tv_network_name_counts_as_streaming(self, cur):
        # TMDB names the network "Apple TV", not "Apple TV+".
        assert cur.priority_tier(_details(1, networks=("Apple TV",))) == cur.TIER_STREAMING_ONLY

    def test_streaming_plus_broadcast_is_not_demoted(self, cur):
        details = _details(1, networks=("Netflix", "ABC"))
        assert cur.priority_tier(details) == cur.TIER_BROADCAST

    def test_genre_outranks_network(self, cur):
        kids_on_netflix = _details(1, genres=(10762,), networks=("Netflix",))
        assert cur.priority_tier(kids_on_netflix) == cur.TIER_LAST

    def test_unknown_networks_are_not_demoted(self, cur):
        assert cur.priority_tier(_details(1, networks=())) == cur.TIER_BROADCAST
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_curate_shows.py -v -k PriorityTier`
Expected: FAIL, `AttributeError: module 'curate_shows' has no attribute 'TIER_BROADCAST'`.

- [ ] **Step 3: Implement**

Add after the `MAX_EXCLUDED_RATIO` constant:

```python
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
```

Append after `outcome_excluded`:

```python
def priority_tier(details: dict) -> int:
    """Harvest tier for a newly added show (an ordering prior, never a filter)."""
    genres = {g.get("id") for g in details.get("genres") or []}
    if genres & LAST_TIER_GENRES:
        return TIER_LAST
    networks = [n.get("name", "") for n in details.get("networks") or []]
    if networks and all(n in STREAMING_NETWORKS for n in networks):
        return TIER_STREAMING_ONLY
    return TIER_BROADCAST
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py
git commit -m "feat(subtitle-cache): order new shows by genre and network priority tiers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 4: Row assembly and ordering

**Files:**
- Modify: `backend/scripts/curate_shows.py`
- Test: `backend/tests/unit/test_curate_shows.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _existing(tid, name="Show", discdb="0"):
    """A row as csv.DictReader yields it from today's curated_shows.csv."""
    return {
        "rank": "1",
        "tmdb_id": str(tid),
        "name": name,
        "year": "2005",
        "origin_country": "US",
        "networks": "ABC",
        "discdb_discs": discdb,
    }


def _ids(rows):
    return [int(r["tmdb_id"]) for r in rows]


@pytest.mark.unit
class TestBuildCuratedRows:
    def test_retained_rows_keep_order_and_discdb_count(self, cur):
        existing = [_existing(2, "B", discdb="33"), _existing(1, "A")]
        details = {1: _details(1, "A"), 2: _details(2, "B")}
        rows, report = cur.build_curated_rows(existing, [], [], details, {})
        assert _ids(rows) == [2, 1]
        assert rows[0]["discdb_discs"] == "33"
        assert rows[0]["tier"] == str(cur.TIER_RETAINED)
        assert report.retained == 2

    def test_non_english_retained_row_is_dropped_and_reported(self, cur):
        existing = [_existing(1), _existing(2, "El Chapo")]
        details = {1: _details(1), 2: _details(2, "El Chapo", lang="es")}
        rows, report = cur.build_curated_rows(existing, [], [], details, {})
        assert _ids(rows) == [1]
        assert report.dropped_language == [2]

    def test_outcome_excluded_retained_row_is_dropped_and_reported(self, cur):
        existing = [_existing(7842, "The Tom and Jerry Show")]
        details = {7842: _details(7842, "The Tom and Jerry Show")}
        coverage = {7842: [_cov(cur, "2026-05-01", 48, 7)]}
        rows, report = cur.build_curated_rows(existing, [], [], details, coverage)
        assert rows == []
        assert report.excluded_outcome == [7842]

    def test_retained_row_without_details_is_kept_verbatim(self, cur):
        # TMDB could not describe it: an infrastructure failure, not a fact.
        existing = [_existing(5, "Unreachable", discdb="4")]
        rows, report = cur.build_curated_rows(existing, [], [], {}, {})
        assert _ids(rows) == [5]
        assert rows[0]["name"] == "Unreachable"
        assert rows[0]["discdb_discs"] == "4"
        assert report.kept_unverified == [5]

    def test_published_english_show_missing_from_the_list_joins_tier_zero(self, cur):
        existing = [_existing(1)]
        details = {
            1: _details(1),
            30: _details(30, "Low votes", votes=10),
            31: _details(31, "High votes", votes=900),
        }
        rows, report = cur.build_curated_rows(existing, [1, 30, 31], [], details, {})
        assert _ids(rows) == [1, 31, 30]
        assert all(r["tier"] == str(cur.TIER_RETAINED) for r in rows)
        assert report.added_published == [31, 30]

    def test_published_non_english_show_is_not_added_or_reported_as_dropped(self, cur):
        details = {40: _details(40, "Anime", lang="ja")}
        rows, report = cur.build_curated_rows([], [40], [], details, {})
        assert rows == []
        assert report.dropped_language == []

    def test_new_candidates_order_by_tier_then_discovery_order(self, cur):
        details = {
            50: _details(50, "Kids", genres=(10762,)),
            51: _details(51, "Stream", networks=("Netflix",)),
            52: _details(52, "Drama A"),
            53: _details(53, "Drama B"),
        }
        rows, report = cur.build_curated_rows([], [], [50, 51, 52, 53], details, {})
        assert _ids(rows) == [52, 53, 51, 50]
        assert report.added_by_tier == {
            cur.TIER_BROADCAST: 2,
            cur.TIER_STREAMING_ONLY: 1,
            cur.TIER_LAST: 1,
        }

    def test_new_candidate_with_poor_healthy_coverage_is_excluded(self, cur):
        details = {60: _details(60)}
        coverage = {60: [_cov(cur, "2026-09-20", 40, 1)]}
        rows, report = cur.build_curated_rows([], [], [60], details, coverage)
        assert rows == []
        assert report.excluded_outcome == [60]

    def test_new_candidate_without_details_is_skipped_and_reported(self, cur):
        rows, report = cur.build_curated_rows([], [], [70], {}, {})
        assert rows == []
        assert report.skipped_no_details == [70]

    def test_a_show_appears_once_at_its_first_position(self, cur):
        existing = [_existing(1)]
        details = {1: _details(1), 2: _details(2)}
        rows, _ = cur.build_curated_rows(existing, [1, 2], [2, 1], details, {})
        assert _ids(rows) == [1, 2]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_curate_shows.py -v -k BuildCuratedRows`
Expected: FAIL, `AttributeError: module 'curate_shows' has no attribute 'build_curated_rows'`.

- [ ] **Step 3: Implement**

Add `from collections import Counter` and `from dataclasses import dataclass, field` to the stdlib import block (keep it sorted: `datetime`, `sys`, `from collections ...`, `from dataclasses ...`, `from pathlib ...`, `from typing ...`; `uv run ruff check --fix` will settle the order). Then add after the tier constants:

```python
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
```

Append after `priority_tier`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 35 passed. (`report.added_by_tier == {...}` compares a `Counter` to a dict; that equality holds.)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py
git commit -m "feat(subtitle-cache): assemble the curated list in harvest-priority order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 5: I/O helpers (read-only coverage, manifest, CSV)

**Files:**
- Modify: `backend/scripts/curate_shows.py`
- Test: `backend/tests/unit/test_curate_shows.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _coverage_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE subtitle_coverage (tmdb_id INTEGER NOT NULL, season INTEGER NOT NULL, "
        "attempted_at REAL NOT NULL, total_episodes INTEGER NOT NULL, "
        "covered_episodes INTEGER NOT NULL, coverage_ratio REAL NOT NULL, "
        "PRIMARY KEY (tmdb_id, season))"
    )
    conn.executemany(
        "INSERT INTO subtitle_coverage VALUES (?, ?, ?, ?, ?, ?)",
        [(t, s, a, tot, cov, cov / tot) for t, s, a, tot, cov in rows],
    )
    conn.commit()
    conn.close()


@pytest.mark.unit
class TestLoadCoverage:
    def test_reads_rows_grouped_by_show(self, cur, tmp_path):
        db = tmp_path / "snapshot.sqlite"
        _coverage_db(db, [(7842, 1, _ts("2026-05-01"), 48, 7), (7842, 2, _ts("2026-09-20"), 10, 10)])
        coverage = cur.load_coverage(db)
        assert sorted(coverage[7842]) == [
            cur.CoverageRow(1, _ts("2026-05-01"), 48, 7),
            cur.CoverageRow(2, _ts("2026-09-20"), 10, 10),
        ]

    def test_missing_snapshot_exits_without_creating_a_file(self, cur, tmp_path):
        db = tmp_path / "absent.sqlite"
        with pytest.raises(SystemExit):
            cur.load_coverage(db)
        assert not db.exists()

    def test_opens_the_snapshot_read_only(self, cur, tmp_path, monkeypatch):
        db = tmp_path / "snapshot.sqlite"
        _coverage_db(db, [])
        seen = {}
        real_connect = sqlite3.connect

        def spy(target, *args, **kwargs):
            seen["target"], seen["uri"] = target, kwargs.get("uri")
            return real_connect(target, *args, **kwargs)

        monkeypatch.setattr(cur.sqlite3, "connect", spy)
        cur.load_coverage(db)
        assert seen["uri"] is True
        assert seen["target"].endswith("?mode=ro")


@pytest.mark.unit
class TestLoadPublished:
    def test_returns_numeric_show_ids(self, cur, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"shows": {"10": {}, "20": {}}}), encoding="utf-8")
        assert cur.load_published(manifest) == [10, 20]

    def test_empty_manifest_exits(self, cur, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"shows": {}}), encoding="utf-8")
        with pytest.raises(SystemExit):
            cur.load_published(manifest)


@pytest.mark.unit
class TestShowListFile:
    def test_non_numeric_tmdb_id_in_the_current_list_exits(self, cur, tmp_path):
        csv_path = tmp_path / "curated_shows.csv"
        csv_path.write_text("rank,tmdb_id,name\n1,,Mystery Show\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            cur.load_existing(csv_path)

    def test_written_list_round_trips_through_the_build_script(self, cur, bsc, tmp_path):
        rows = [cur._row(_details(2, "B"), tier=0), cur._row(_details(1, "A"), tier=1)]
        out = tmp_path / "curated_shows.csv"
        cur.write_csv(rows, out)

        written = cur.load_existing(out)
        assert [r["rank"] for r in written] == ["1", "2"]
        assert list(written[0].keys()) == cur.CSV_FIELDS
        # The harvester's own reader sees the same ids in the same order, with
        # no TMDB lookup (every id is numeric).
        assert bsc._read_show_list(str(out)) == [{"name": "B", "id": 2}, {"name": "A", "id": 1}]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_curate_shows.py -v -k "LoadCoverage or LoadPublished or ShowListFile"`
Expected: FAIL, `AttributeError: module 'curate_shows' has no attribute 'load_coverage'`.

- [ ] **Step 3: Implement**

Add `csv`, `io`, `json` and `sqlite3` to the stdlib imports. Add after `CSV_FIELDS`:

```python
_DEFAULT_CSV = Path(__file__).parent / "curated_shows.csv"
```

Append:

```python
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
    return [int(key) for key in shows if str(key).isdigit()]


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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 42 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py
git commit -m "feat(subtitle-cache): read-only coverage snapshot and CSV I/O for curation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 6: The CLI (`main`) with TMDB discovery

**Files:**
- Modify: `backend/scripts/curate_shows.py`
- Test: `backend/tests/unit/test_curate_shows.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@pytest.fixture
def curation_inputs(cur, tmp_path):
    """Current list (en 10, es 20), manifest (10 + unlisted en 30), coverage
    (40 has poor healthy coverage), and the TMDB payloads for all of them."""
    show_list = tmp_path / "curated_shows.csv"
    show_list.write_text(
        "rank,tmdb_id,name,year,origin_country,networks,discdb_discs\n"
        "1,10,Kept,2005,US,ABC,3\n"
        "2,20,El Chapo,2017,US,Univision,0\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"shows": {"10": {}, "30": {}}}), encoding="utf-8")
    coverage = tmp_path / "snapshot.sqlite"
    _coverage_db(coverage, [(40, 1, _ts("2026-09-20"), 40, 1)])
    details = {
        10: _details(10, "Kept"),
        20: _details(20, "El Chapo", lang="es"),
        30: _details(30, "Published extra"),
        40: _details(40, "Unharvestable"),
        50: _details(50, "New drama"),
        70: _details(70, "New kids", genres=(10762,)),
    }
    discover_page = [
        {"id": 70, "original_language": "en"},
        {"id": 60, "original_language": "fr"},
        {"id": 40, "original_language": "en"},
        {"id": 50, "original_language": "en"},
    ]
    return SimpleNamespace(
        show_list=show_list,
        manifest=manifest,
        coverage=coverage,
        details=details,
        discover_page=discover_page,
        tmdb_cache=tmp_path / "scratch-tmdb.sqlite",
        output=tmp_path / "out.csv",
    )


def _patch_seams(cur, monkeypatch, inputs, *, discover_pages):
    fetched: list[int] = []

    def fake_details(tid):
        fetched.append(tid)
        return inputs.details.get(tid)

    monkeypatch.setattr(cur, "_ensure_db_schema", lambda: None)
    monkeypatch.setattr(cur, "_bootstrap_config_from_env", lambda: None)
    monkeypatch.setattr(cfg_svc, "get_config_sync", lambda: SimpleNamespace(tmdb_api_key="k"))
    monkeypatch.setattr(cur, "fetch_shows_by_vote_count", lambda page: discover_pages.get(page, []))
    monkeypatch.setattr(cur, "fetch_show_details", fake_details)
    return fetched


def _argv(inputs, *extra):
    return [
        "--show-list", str(inputs.show_list),
        "--output", str(inputs.output),
        "--coverage-db", str(inputs.coverage),
        "--published-manifest", str(inputs.manifest),
        "--tmdb-cache", str(inputs.tmdb_cache),
        "--pages", "1",
        "--sleep", "0",
        *extra,
    ]


@pytest.mark.unit
class TestMain:
    def test_writes_the_ordered_english_list(self, cur, monkeypatch, curation_inputs, capsys):
        fetched = _patch_seams(
            cur, monkeypatch, curation_inputs, discover_pages={1: curation_inputs.discover_page}
        )
        assert cur.main(_argv(curation_inputs)) == 0

        rows = cur.load_existing(curation_inputs.output)
        # 10 retained; 20 dropped (Spanish); 30 added from the published cache;
        # 40 excluded on coverage; 50 (tier 1) before 70 (tier 3); 60 is French.
        assert [int(r["tmdb_id"]) for r in rows] == [10, 30, 50, 70]
        assert rows[0]["discdb_discs"] == "3"
        # A non-English discover hit is filtered on the discover payload and
        # never costs a details call.
        assert 60 not in fetched
        out = capsys.readouterr().out
        assert "El Chapo" in out
        assert "Unharvestable" in out

    def test_empty_discover_page_aborts_without_writing(self, cur, monkeypatch, curation_inputs):
        _patch_seams(cur, monkeypatch, curation_inputs, discover_pages={})
        assert cur.main(_argv(curation_inputs)) == 1
        assert not curation_inputs.output.exists()

    def test_refuses_the_live_tmdb_cache(self, cur, monkeypatch, curation_inputs):
        _patch_seams(
            cur, monkeypatch, curation_inputs, discover_pages={1: curation_inputs.discover_page}
        )
        live = Path("~/.engram/cache/tmdb_cache.sqlite").expanduser()
        argv = _argv(curation_inputs)
        argv[argv.index("--tmdb-cache") + 1] = str(live)
        with pytest.raises(SystemExit) as exc:
            cur.main(argv)
        assert exc.value.code == 2
        assert not curation_inputs.output.exists()

    def test_missing_tmdb_key_exits_without_writing(self, cur, monkeypatch, curation_inputs):
        _patch_seams(
            cur, monkeypatch, curation_inputs, discover_pages={1: curation_inputs.discover_page}
        )
        monkeypatch.setattr(
            cfg_svc, "get_config_sync", lambda: SimpleNamespace(tmdb_api_key=None)
        )
        assert cur.main(_argv(curation_inputs)) == 1
        assert not curation_inputs.output.exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_curate_shows.py -v -k TestMain`
Expected: FAIL, `AttributeError: module 'curate_shows' has no attribute 'main'` (or `_ensure_db_schema`).

- [ ] **Step 3: Implement**

Add `argparse` and `time` to the stdlib imports, and extend the post-`sys.path` import block to:

```python
from build_subtitle_cache import _bootstrap_config_from_env, _ensure_db_schema
from loguru import logger
from purge_poisoned_coverage import DEFAULT_CUTOFF

from app.matcher import tmdb_persistent_cache
from app.matcher.tmdb_client import fetch_show_details, fetch_shows_by_vote_count
```

Add after `_DEFAULT_CSV`:

```python
_LIVE_TMDB_CACHE = Path("~/.engram/cache/tmdb_cache.sqlite").expanduser()


class DiscoveryIncomplete(RuntimeError):
    """A discover page came back empty; the candidate list would be truncated."""
```

Append:

```python
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
    parser.add_argument("--coverage-db", type=Path, required=True,
                        help="READ-ONLY snapshot of the harvester's tmdb_cache.sqlite")
    parser.add_argument("--published-manifest", type=Path, required=True,
                        help="manifest.json from the subtitle-cache-latest release")
    parser.add_argument("--tmdb-cache", type=Path, required=True,
                        help="Scratch TMDB response cache (never the live one)")
    parser.add_argument("--show-list", type=Path, default=_DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=_DEFAULT_CSV)
    parser.add_argument("--pages", type=int, default=100,
                        help="TMDB discover pages (20 shows each) to consider")
    parser.add_argument("--sleep", type=float, default=0.25,
                        help="Seconds between uncached TMDB calls")
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

    wanted = list(dict.fromkeys(
        [int(r["tmdb_id"]) for r in existing] + published + discovered_en
    ))
    details = fetch_details(wanted, args.sleep)

    rows, report = build_curated_rows(existing, published, discovered_en, details, coverage)
    write_csv(rows, args.output)
    print(render_report(report, details, coverage, before=len(existing), after=len(rows)))
    print(f"  discover walk: {len(discovered)} shows, {len(discovered_en)} English")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Then run `uv run ruff format scripts/curate_shows.py` (it reflows the `add_argument` calls) and `uv run ruff check --fix scripts/curate_shows.py tests/unit/test_curate_shows.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_curate_shows.py -v`
Expected: 46 passed.

- [ ] **Step 5: Run the neighbouring script suites (shared fixtures and imports)**

Run: `uv run pytest tests/unit/test_build_subtitle_cache.py tests/unit/test_precomputed_cache.py -q`
Expected: all pass (unchanged behaviour; this proves the new fixture and module-level imports did not disturb them).

- [ ] **Step 6: Lint and dash check**

```bash
uv run ruff check scripts/curate_shows.py tests/unit/test_curate_shows.py tests/unit/conftest.py
uv run ruff format --check scripts/curate_shows.py tests/unit/test_curate_shows.py tests/unit/conftest.py
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' scripts/curate_shows.py tests/unit/test_curate_shows.py
```

Expected: `All checks passed!`, `3 files already formatted`, and `0` for each file.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/curate_shows.py backend/tests/unit/test_curate_shows.py
git commit -m "feat(subtitle-cache): curate_shows CLI with fail-closed TMDB discovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 7: Generate and review the new list

This is the only step that touches live data. It reads the server's coverage (read-only, via a snapshot) and calls TMDB. It makes **no** OpenSubtitles call.

**Files:**
- Regenerate: `backend/scripts/curated_shows.csv`
- Delete: `backend/scripts/curated_shows.txt`

- [ ] **Step 1: Snapshot the server's coverage DB (read-only source)**

SQLite's backup API copies a consistent snapshot even while the WAL is live; the source connection is `mode=ro`. Run in Git Bash, with `SCRATCH` set to your session scratch directory:

```bash
ssh jsakkos@192.168.1.122 'python3 - <<"EOF"
import os, sqlite3
src = sqlite3.connect("file:" + os.path.expanduser("~/.engram/cache/tmdb_cache.sqlite") + "?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/engram-coverage-snapshot.sqlite")
src.backup(dst)
print(dst.execute("SELECT COUNT(*) FROM subtitle_coverage").fetchone()[0], "coverage rows")
dst.close(); src.close()
EOF'
scp jsakkos@192.168.1.122:/tmp/engram-coverage-snapshot.sqlite "$SCRATCH/server-tmdb-cache.sqlite"
ssh jsakkos@192.168.1.122 'rm -f /tmp/engram-coverage-snapshot.sqlite'
```

Expected: a row count of at least 2,676 (the 2026-09-26 count; it only grows).

- [ ] **Step 2: Fetch the published manifest**

```bash
gh release download subtitle-cache-latest --repo Jsakkos/engram --pattern manifest.json --dir "$SCRATCH" --clobber
```

- [ ] **Step 3: Run the curation**

The user exports `TMDB_API_KEY` in their own terminal (never paste it into chat, a file, or a command line you log). The scratch `DATABASE_URL` keeps the bootstrapped key out of `backend/engram.db`.

```bash
cd backend
DATABASE_URL=sqlite+aiosqlite:///./engram-curation.db uv run python scripts/curate_shows.py \
  --coverage-db "$SCRATCH/server-tmdb-cache.sqlite" \
  --published-manifest "$SCRATCH/manifest.json" \
  --tmdb-cache "$SCRATCH/curation-tmdb-cache.sqlite"
```

Expected (TMDB rankings drift daily, so allow small differences from the 2026-09-26 measurement):

```
curated list: ~1373 rows (was 448)
  retained from the current list: 436
  kept unverified (no TMDB details): 0
  dropped, not English: 10
    - El Señor de los Cielos (tmdb ..., es)
    ... (8 es, plus Dragon Ball Z and Elfen Lied, ja)
  excluded on healthy-window coverage: 2
    - The Tom and Jerry Show (tmdb 7842, 7/48)
    - Ned's Declassified School Survival Guide (tmdb 1600, 0/22)
  added from the published cache: 22
  added from discovery: ~915 (tier 1: ~438, tier 2: ~366, tier 3: ~111)
  skipped, no TMDB details: 0
  discover walk: 2000 shows, ~1369 English
```

Stop and investigate if `kept unverified` or `skipped, no TMDB details` is non-zero, or if `retained` is far from 436.

- [ ] **Step 4: Verify the generated file**

```bash
uv run python - <<'EOF'
import csv
rows = list(csv.DictReader(open("scripts/curated_shows.csv", encoding="utf-8")))
ids = [int(r["tmdb_id"]) for r in rows]
assert len(ids) == len(set(ids)), "duplicate tmdb_id"
assert all(r["original_language"] == "en" for r in rows), "non-English row"
for tid in (10283, 60625, 2190, 615):  # Archer, Rick and Morty, South Park, Futurama
    assert tid in ids, tid
assert 7842 not in ids and 1600 not in ids
tiers = [int(r["tier"]) for r in rows]
assert tiers == sorted(tiers), "rows not in tier order"
print(len(rows), "rows; tiers", {t: tiers.count(t) for t in sorted(set(tiers))})
EOF
```

Expected: `~1373 rows; tiers {0: 458, 1: ~438, 2: ~366, 3: ~111}` and no assertion error.

Also confirm the retained block kept its order: `git diff --stat scripts/curated_shows.csv` shows the whole file rewritten (new columns), so compare the id sequence instead:

```bash
uv run python - <<'EOF'
import csv, subprocess
old = [r["tmdb_id"] for r in csv.DictReader(subprocess.run(
    ["git", "show", "HEAD:backend/scripts/curated_shows.csv"], capture_output=True,
    text=True, encoding="utf-8").stdout.splitlines())]
new = [r["tmdb_id"] for r in csv.DictReader(open("scripts/curated_shows.csv", encoding="utf-8"))]
kept = [t for t in old if t in set(new)]
assert new[: len(kept)] == kept, "retained rows reordered"
print("retained order preserved for", len(kept), "rows")
EOF
```

Expected: `retained order preserved for 436 rows`.

- [ ] **Step 5: Clean up the scratch DB and delete the twin list**

`engram-curation.db` holds the TMDB key; it is gitignored (`backend/*.db`) but must not linger. It is **not** `engram.db`.

```bash
rm -f engram-curation.db engram-curation.db-shm engram-curation.db-wal
git rm scripts/curated_shows.txt
```

`curated_shows.txt` is a name-only copy of the old CSV that nothing reads (`grep -rn "curated_shows.txt"` finds no reference outside git history); keeping it would leave a stale 448-row list beside the real one.

- [ ] **Step 6: Commit**

```bash
git add scripts/curated_shows.csv
git commit -m "chore(subtitle-cache): English-only curated list, extended to ~1,370 shows

Drops 10 non-English rows (8 Spanish-language US/Telemundo shows, Dragon Ball
Z, Elfen Lied) and 2 shows with healthy-window coverage under 20%. Adds the
22 published English shows the list was missing, then the English shows in
TMDB's top 2,000 by vote count, ordered broadcast/cable, streaming-only,
then kids/reality/talk/news. Dropped shows stay in the published cache: the
packer ships everything on disk.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Paste the Step 3 report into the PR description (Task 9).

---

## Task 8: Documentation and CHANGELOG

**Files:**
- Modify: `docs/development/subtitle-cache.md` (new section before `## Cache format versioning`)
- Modify: `docs/development/subtitle-cache-server.md` (new section before `## Monitoring`)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Add "Curating the show list" to `docs/development/subtitle-cache.md`**

Insert before `## Cache format versioning`:

````markdown
## Curating the show list

`scripts/curated_shows.csv` is the list the harvester walks, **top to bottom**, until
the night's download budget runs out, so row order is harvest priority.
`scripts/curate_shows.py` regenerates it:

- **English only**, by TMDB `original_language`. Never `origin_country`: Telemundo
  shows are origin US and Spanish-language.
- **Outcome exclusion** only on healthy-window coverage (before 2026-06-11, or from
  2026-09-05 when the harvester repair was complete), with at least 20 episodes
  measured and under 20% covered. Anything thinner stays and gets measured.
- **Order:** the current list first (mostly complete on disk), then published English
  shows missing from it, then new shows by tier: broadcast/cable, streaming-only,
  then kids/reality/talk/news. Genre is a priority, never a filter, so adult
  animation (Archer, South Park) stays in the body of the list.
- **Dropping a row never shrinks the published cache.** The packer ships everything on
  disk; a dropped row only stops further harvest spend on that show.

Run it on the laptop against a read-only snapshot of the server's coverage DB, then
commit the CSV through a PR. It calls TMDB only, never OpenSubtitles:

```bash
# Snapshot + manifest: see docs/superpowers/plans/2026-09-26-subtitle-cache-english-curation.md, Task 7
DATABASE_URL=sqlite+aiosqlite:///./engram-curation.db uv run python scripts/curate_shows.py \
  --coverage-db <snapshot>.sqlite --published-manifest manifest.json \
  --tmdb-cache <scratch>/curation-tmdb-cache.sqlite
```

Then roll it out with "Updating the show list" in `subtitle-cache-server.md`.
````

- [ ] **Step 2: Add "Updating the show list" to `docs/development/subtitle-cache-server.md`**

Insert before `## Monitoring`:

````markdown
## Updating the show list

The server's checkout is **not** auto-updated: the timer reads
`~/engram/backend/scripts/curated_shows.csv` from whatever commit is checked out. After a
curated-list PR merges, update it by hand, between runs (the timer fires at 02:00 UTC
plus up to 15 minutes; its harvest and pack take about 90 minutes).

```bash
ssh jsakkos@192.168.1.122
systemctl --user is-active engram-subtitle-cache.service   # expect: inactive
cd ~/engram && git fetch origin && git checkout main && git pull --ff-only
cd backend && uv sync --no-install-project
```

The packer resolves show dirs through TMDB with the key already stored in the server's
`backend/engram.db` (every harvest bootstraps it from the EnvironmentFile), so no secrets
need sourcing. Dry pack and guard, holding the harvest lock so nothing can overlap (the packer
rebuilds `~/.engram/cache/precomputed`, which the harvest also writes):

```bash
mkdir -p ~/.engram/curation-dry
flock -n ~/.engram/harvest/.harvest.lock \
  uv run python scripts/pack_subtitle_cache.py --output ~/.engram/curation-dry/engram-subtitle-cache.tar.gz
uv run python scripts/publish_guard.py --candidate ~/.engram/curation-dry/manifest.json \
  --cache-tag subtitle-cache-latest --repo Jsakkos/engram
echo "guard exit: $?"   # expect 0; the dry artifact is NOT uploaded
rm -rf ~/.engram/curation-dry
```

The next nightly run then logs `Loaded <N> shows` with the new count. With new work
on the list, a budget halt (`harvest.sh` exit 10) is the expected, healthy outcome.
````

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]`, add (create the `### Changed` heading if the section has none):

```markdown
### Changed

- **Bigger, English-only subtitle cache.** The show list the nightly cache build
  walks was exhausted, so the published cache had stopped growing at 524 shows. It
  is now English-only (by original language, which catches Spanish-language shows
  from US networks) and extended to about 1,370 shows, ordered so the shows most
  likely to be ripped from disc are harvested first. Shows already in the cache stay
  in it. Episode matching for newly covered shows works without a live subtitle
  download.
```

- [ ] **Step 4: Dash check and docs build**

```bash
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' docs/development/subtitle-cache.md docs/development/subtitle-cache-server.md CHANGELOG.md
```

Expected: `0` for the two docs. (CHANGELOG may already contain dashes in older entries; confirm only that your added lines have none: `git diff -U0 CHANGELOG.md | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'` prints `0`.)

From the repo root: `uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build` (no `--strict`; about 18 warnings are expected and pre-existing). Expected: `Documentation built`.

- [ ] **Step 5: Commit**

```bash
git add docs/development/subtitle-cache.md docs/development/subtitle-cache-server.md CHANGELOG.md
git commit -m "docs(subtitle-cache): curation workflow and server list-update runbook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 9: Full verification and PR

- [ ] **Step 1: Run the affected suites**

```bash
cd backend
uv run pytest tests/unit/test_curate_shows.py tests/unit/test_build_subtitle_cache.py tests/unit/test_precomputed_cache.py tests/unit/test_validate_subtitle_cache.py tests/unit/test_cache_numbering_marker.py -q
uv run ruff check .
uv run ruff format --check .
```

Expected: all pass, `All checks passed!`, no files to reformat. (The full unit suite takes several minutes; CI runs it.)

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/subtitle-cache-english-curation
gh pr create --repo Jsakkos/engram --title "feat(subtitle-cache): English-only curated show list, extended to ~1,370 shows" --body-file <body.md>
```

The body contains: the Why-now paragraph, the Task 7 Step 3 report verbatim, a note that no `--allow-shrink` is needed (dropped rows stay published), the rollout checklist from Task 10, and ends with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then post `@claude please review this PR` as a PR comment (the review workflow only fires on `opened`).

---

## Task 10: Server rollout (after the PR merges)

- [ ] **Step 1: Update the server checkout between runs**

Follow "Updating the show list" in `docs/development/subtitle-cache-server.md` (added in Task 8): confirm the service is inactive, `git pull --ff-only`, `uv sync --no-install-project`.

- [ ] **Step 2: Confirm the server sees the new list**

```bash
ssh jsakkos@192.168.1.122 'cd ~/engram && git log --oneline -1 && wc -l backend/scripts/curated_shows.csv'
```

Expected: the merge commit, and about 1,374 lines (header + rows).

- [ ] **Step 3: Dry pack and publish guard**

Run the dry pack and `publish_guard.py` block from the runbook. Expected: guard exit `0` with a `growth` or `within-tolerance` verdict against the published 524 shows / 42,108 episodes. The list change cannot move this number (the packer ignores the CSV); the check proves the upgraded checkout still packs what is on disk before the timer publishes with it. If the guard blocks, do **not** reach for `--allow-shrink`; stop and investigate.

- [ ] **Step 4: Let the timer run, then verify the next morning**

```bash
ssh jsakkos@192.168.1.122 'journalctl --user -u engram-subtitle-cache.service --since today --no-pager | grep -E "harvest:|Loaded|Selected"'
gh release download subtitle-cache-latest --repo Jsakkos/engram --pattern manifest.json --dir "$SCRATCH" --clobber
```

Expected: `Loaded ~1373 shows`, `harvest halted on quota (exit 2)`, the unit finishing with exit 10, and a published manifest with more than 524 shows. The selection phase grows from about 8 to about 25 minutes (one `fetch_show_details` plus `--sleep 1.0` per row); the 10-hour `TimeoutStartSec` has ample room.

- [ ] **Step 5: Watch the first two weeks**

Growth should be monotonic while tiers 1 and 2 drain (about 11 to 13 nights at the projection above). Revisit "Decisions for the reviewer" item 1 before the harvest reaches tier 3.

---

## Decisions for the reviewer

1. **Tier 3 (kids/reality/talk/news, ~111 shows, ~30,500 episodes).** Per the spec it is ordered last, not excluded. Its episode mass is mostly daily talk and news shows (*The Daily Show* alone is 4,272 episodes) that are never sold as season discs, and harvesting it would roughly double the tarball for little matching value. Recommended: before the harvest reaches tier 3 (about two weeks in), either accept it or truncate the talk/news rows with a small follow-up PR. Nothing in this plan forecloses either choice.
2. **Non-English shows already published (69 shows, 3,867 episodes).** The spec's goal says the shipped cache is English-only, but removing them is a deliberate shrink (about 13% of shows) that the publish guard blocks without `--allow-shrink`. This plan keeps them, per the "keep what is published unless there is a strong reason" constraint. Pruning them would be a separate, explicitly called-out change: delete their `data/<tmdb_id>/` dirs on the server, then publish once with `--allow-shrink`.
3. **Tarball size.** 343 MB today for 42,108 episodes. Tiers 0 to 2 add about 37,000 episodes (roughly +300 MB); all tiers about +550 MB. The spec leaves size uncapped and names sharding as the escape hatch; flagged here so it is a conscious choice.

## Notes for the implementer

- The measured numbers in this plan are a 2026-09-26 snapshot. TMDB's vote ranking and the server's coverage table both move daily; small differences in Task 7 are expected, large ones are a reason to stop and ask.
- `fetch_shows_by_vote_count` caches each page for 24 hours in the persistent cache; because `main()` points that cache at `--tmdb-cache`, reruns within a day are cheap and never touch `~/.engram/cache`.
- Do not add the curation rules to `build_subtitle_cache.py`. The harvester stays a dumb, ordered walker of a reviewed list; curation is an occasional, human-reviewed act.
- If `ruff` reorders the imports in `curate_shows.py`, accept its order.
