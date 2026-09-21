# Subtitle Cache Numbering Scheme Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the published subtitle cache record, per season, whether its references are numbered the same way TMDB's canonical roster is, and have the backend prefer that recorded marker over the size heuristic PR #673 introduced.

**Architecture:** One shared derivation module (`app/matcher/numbering_scheme.py`) defines the three-value vocabulary. Both builder scripts call it and emit an additive `season_numbering` key into each show's manifest entry, so `CACHE_FORMAT_VERSION` stays `"3"` and old backends keep working. The matcher reads the marker out of the manifest as a per-call local and stamps it into `match_details`; `numbering_schemes_agree` prefers it and falls through to the existing heuristic when it is absent.

**Tech Stack:** Python 3.11+, pytest, ruff, SQLModel/FastAPI backend, standalone builder scripts under `backend/scripts/` loaded in tests via the session-scoped `psc` / `bsc` / `vsc` conftest fixtures.

**Branch:** `feat/subtitle-cache-numbering-scheme`, already cut from `origin/claude/conjoined-tracks-matching-naming-316a8e` (PR #673's tip, commit `1331654a`). The spec commit `556804dd` is already on it.

**Spec:** `docs/superpowers/specs/2026-09-20-subtitle-cache-numbering-scheme-design.md`

---

## Context you need before starting

**Run everything from `backend/`.** The repo uses `uv`, never bare `python` or `pip`:

```bash
uv run pytest tests/unit/test_numbering_scheme.py -v
uv run ruff check .
uv run ruff format .
```

**Ruff config:** line length 100, double quotes, rules E/F/I/UP/B. Run `uv run ruff format .` before every commit.

**House style:** no em dashes (`—`) and no en dashes (`–`) anywhere, including code comments and commit messages. Use a colon, a comma, a semicolon, or parentheses. To check a file:

```bash
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' path/to/file.py
```

Expected output: `0`. (A plain `grep "[—–]"` false-positives on `→` and `…` in Git Bash, which is why the byte alternation is used.)

**Never touch `C:\Users\jonat\.engram\cache`.** Every test in this plan builds synthetic fixtures under `tmp_path`. No test may call the real TMDB API or read the user's cache.

**Terminology used throughout:**
- *reference count* = how many episodes the harvested subtitle corpus holds for a season. Already published as `episode_counts[str(season)]`.
- *roster size* = how many episodes TMDB's canonical aired roster holds for that season, from `fetch_season_details`.
- Dexter's Laboratory (tmdb_id 4229) is the worked example: reference counts 13 / 40 / 13 / 13 against rosters 38 / 108 / 36 / 38.

**`fetch_season_details` contract:** it returns `int`, and returns `0` (not `None`) when the TMDB key is missing or a request fails after retries. A roster of `0` must therefore mean "unknown", never "divergent".

---

## File Structure

**Create:**
- `backend/app/matcher/numbering_scheme.py` - the three scheme constants and `derive_numbering_scheme`. Single definition shared by both builder scripts and the runtime. ~60 lines.
- `backend/tests/unit/test_numbering_scheme.py` - unit tests for the derivation.
- `backend/tests/unit/test_cache_numbering_marker.py` - manifest emission (pack + build scripts) and matcher stamping.

**Modify:**
- `backend/scripts/pack_subtitle_cache.py` - emit `season_numbering` in the manifest entry (the safe publish path).
- `backend/scripts/build_subtitle_cache.py` - emit the same from its harvest loop.
- `backend/app/matcher/episode_identification.py` - new `precomputed_numbering()` method; stamp `numbering_scheme` / `pack_roster_size` into `match_stats`.
- `backend/app/services/matching_coordinator.py:451-476` - `numbering_schemes_agree` prefers the marker.
- `backend/app/services/finalization_coordinator.py:65-73` - make the projection-skip log line marker-aware.
- `backend/scripts/validate_subtitle_cache.py` - validate `season_numbering` shape; count divergent seasons into the summary.
- `backend/tests/unit/test_matching_coordinator.py` - precedence tests beside #673's.
- `backend/tests/unit/test_precomputed_cache.py` and `backend/tests/unit/test_precomputed_cache_service.py` - degrade-safe path tests.
- `backend/tests/unit/test_validate_subtitle_cache.py` - validator tests.
- `CHANGELOG.md` - `[Unreleased]` entry.

**Deliberately NOT modified:** `backend/scripts/audit_subtitle_cache.py`. The spec listed it, but reading the script shows it is a TVsubtitles show-name mislabel auditor that makes one network request per show at ~1/sec and **writes `cache_audit.json` into the user's real cache directory**. Divergence reporting is pure, offline, and already computable from the manifest, so it belongs in the validator's summary (Task 6), which runs in CI against the published release and writes nothing. Adding it to the audit script would mean either a second network-bound pass or a write into `~/.engram/cache`, both of which the spec's constraints rule out. Task 6 delivers the visibility the spec asked for; this is a change of location, not of scope.

---

## Task 1: The shared derivation module

**Files:**
- Create: `backend/app/matcher/numbering_scheme.py`
- Test: `backend/tests/unit/test_numbering_scheme.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_numbering_scheme.py`:

```python
"""The single definition of 'is this season numbered the way TMDB numbers it'.

Two builder scripts write the marker and the runtime reads it, so the
classification has to live in one place or the writer and the reader drift.
These tests pin the vocabulary and, in particular, the unknown-vs-divergent
boundary: fetch_season_details returns 0 for its no-key and transient-failure
paths, and a TMDB outage during a nightly build must not brand healthy seasons
as divergent.
"""

import pytest

from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
    VALID_SCHEMES,
    derive_numbering_scheme,
)


@pytest.mark.unit
class TestDeriveNumberingScheme:
    def test_equal_counts_are_tmdb_aired(self):
        assert derive_numbering_scheme(13, 13) == SCHEME_TMDB_AIRED

    def test_dexters_laboratory_season_one_is_divergent(self):
        # 13 harvested broadcast half-hours against a 38-entry segment roster.
        assert derive_numbering_scheme(13, 38) == SCHEME_DIVERGENT

    def test_corpus_larger_than_roster_is_also_divergent(self):
        assert derive_numbering_scheme(40, 36) == SCHEME_DIVERGENT

    @pytest.mark.parametrize(
        "reference_count,roster_size",
        [
            (13, 0),  # fetch_season_details no-key / transient-failure contract
            (0, 38),
            (13, None),
            (None, 38),
            (None, None),
            (13, -1),
            (-1, 38),
            (13, "38"),
            ("13", 38),
            (13, 38.0),
            (13, True),
            (True, 38),
        ],
    )
    def test_unusable_inputs_are_unknown(self, reference_count, roster_size):
        assert derive_numbering_scheme(reference_count, roster_size) == SCHEME_UNKNOWN

    def test_every_returned_value_is_a_valid_scheme(self):
        for pair in [(13, 13), (13, 38), (13, 0), (None, None)]:
            assert derive_numbering_scheme(*pair) in VALID_SCHEMES

    def test_tvdb_is_not_a_scheme(self):
        # TheTVDB numbers Dexter's Laboratory by segment in both its official
        # (38/108/36/38) and DVD (39 for season 1) orders, so the corpus's
        # 13/40/13/13 is not TVDB numbering. Recording it as such would put a
        # false provenance into a published artifact.
        assert "tvdb" not in VALID_SCHEMES
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_numbering_scheme.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'app.matcher.numbering_scheme'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/matcher/numbering_scheme.py`:

```python
"""How a harvested subtitle-reference season is numbered.

The subtitle providers Engram harvests and TMDB do not always number a season
the same way. For a segment-format show TMDB catalogues each ~7-minute short
(38 entries for Dexter's Laboratory season 1) while the providers index the
22-minute broadcast half-hours those shorts were assembled into (13). The
reference corpus is stored under canonical TMDB season keys either way, so a
code that comes out of the matcher looks like a TMDB coordinate whether or not
it is one. That stays invisible until something dereferences the code, and the
#200 ordering projection is the only thing that does, which is how a track
matched as S01E01 came to be filed as S01E19.mkv.

The published cache records which scheme each season was harvested in so no
consumer has to infer it. This module is the single definition of that
vocabulary, imported by both builder scripts and by the runtime, so the code
that writes the marker and the code that reads it cannot drift.

Deliberately NOT a value here: "tvdb". TheTVDB numbers Dexter's Laboratory by
segment in both its official order (38/108/36/38, identical to TMDB) and its
DVD order (39 for season 1), so the corpus's 13/40/13/13 is not TVDB numbering
and the providers' scheme matches no catalogue Engram can name. Rationale:
docs/superpowers/specs/2026-09-20-subtitle-cache-numbering-scheme-design.md.
"""

# The corpus is numbered the same way the canonical TMDB roster is, so a code
# from it IS a TMDB coordinate and may be dereferenced.
SCHEME_TMDB_AIRED = "tmdb_aired"

# The corpus and the roster disagree on how many episodes the season holds, so
# the corpus is numbered in some other scheme. Which one is not knowable here.
SCHEME_DIVERGENT = "divergent"

# Not enough information to say. An offline pack, a missing TMDB key, an
# unresolved tmdb_id, or a transient roster-lookup failure all land here. Kept
# distinct from DIVERGENT so a consumer falls back to its own heuristic rather
# than reading "we did not look" as "we looked and they disagree".
SCHEME_UNKNOWN = "unknown"

VALID_SCHEMES = frozenset({SCHEME_TMDB_AIRED, SCHEME_DIVERGENT, SCHEME_UNKNOWN})


def _usable_count(value) -> bool:
    """A count is usable only if it is a positive, genuine int.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(True, int)`` is
    True and an accidental boolean would otherwise compare as 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def derive_numbering_scheme(reference_count, roster_size) -> str:
    """Classify a season from its harvested size and its canonical roster size.

    Equal counts are a proxy for "same numbering scheme", not a proof: a season
    could coincidentally hold as many broadcast half-hours as canonical
    segments. It is the same proxy the runtime heuristic uses, computed once at
    build time against a known roster rather than per match against whatever the
    duration pre-filter happened to fetch.

    ``fetch_season_details`` returns 0, not None, for its no-key and
    transient-failure paths, so a non-positive roster must mean UNKNOWN. A TMDB
    outage during a nightly build must never brand healthy seasons DIVERGENT.
    """
    if not _usable_count(reference_count) or not _usable_count(roster_size):
        return SCHEME_UNKNOWN
    return SCHEME_TMDB_AIRED if reference_count == roster_size else SCHEME_DIVERGENT
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_numbering_scheme.py -v
```

Expected: PASS, 17 tests.

- [ ] **Step 5: Lint, dash-check, and commit**

```bash
uv run ruff format app/matcher/numbering_scheme.py tests/unit/test_numbering_scheme.py
uv run ruff check .
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' app/matcher/numbering_scheme.py tests/unit/test_numbering_scheme.py
```

Expected: ruff clean, both grep counts `0`.

```bash
git add backend/app/matcher/numbering_scheme.py backend/tests/unit/test_numbering_scheme.py
git commit -m "feat(cache): single definition of a season's episode-numbering scheme

Two builder scripts write the marker and the runtime reads it, so the
classification of 'does this corpus number the season the way TMDB does' gets a
module rather than three copies. No 'tvdb' value: TheTVDB numbers Dexter's
Laboratory by segment in both its official and DVD orders, so the corpus's
half-hour numbering is not TVDB's either.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: `pack_subtitle_cache.py` emits the marker

This is the safe publish path (packs from already-harvested SRTs on disk), so it goes first.

**Files:**
- Modify: `backend/scripts/pack_subtitle_cache.py`
- Test: `backend/tests/unit/test_cache_numbering_marker.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_cache_numbering_marker.py`:

```python
"""The published manifest records how each harvested season is numbered.

Without the marker no backend can tell a canonical TMDB code from a
broadcast-half-hour code, because both come out of the matcher shaped like
SxxEyy under a canonical season key. These tests pin the emission in both
builder scripts and, crucially, the offline and roster-lookup-failure paths:
those must emit "unknown" rather than guessing, or an offline pack would look
like a corrupt one.

The `psc` and `bsc` fixtures (session-scoped, in conftest.py) load the standalone
scripts as modules. Nothing here touches the network or the real cache.
"""

from unittest.mock import patch

import pytest

from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
)


@pytest.mark.unit
class TestPackSeasonNumberingEntry:
    """`_season_numbering_entry` in pack_subtitle_cache.py."""

    def test_agreeing_counts_emit_tmdb_aired_with_roster_size(self, psc):
        with patch.object(psc, "fetch_season_details", return_value=13) as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=1396, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_TMDB_AIRED, "roster_size": 13}
        mock_fetch.assert_called_once_with("1396", 1)

    def test_dexters_laboratory_emits_divergent_with_roster_size(self, psc):
        with patch.object(psc, "fetch_season_details", return_value=38):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_offline_emits_unknown_and_never_calls_tmdb(self, psc):
        with patch.object(psc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=True
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_unresolved_show_emits_unknown_and_never_calls_tmdb(self, psc):
        with patch.object(psc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=None, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_roster_lookup_returning_zero_emits_unknown_not_divergent(self, psc):
        # fetch_season_details returns 0 for a missing key or a failed request.
        # Treating that as divergent would brand healthy seasons unverified for
        # a whole nightly build whenever TMDB blips.
        with patch.object(psc, "fetch_season_details", return_value=0):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry

    def test_roster_lookup_raising_emits_unknown(self, psc):
        with patch.object(psc, "fetch_season_details", side_effect=RuntimeError("boom")):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py -v
```

Expected: FAIL, `AttributeError: module 'pack_subtitle_cache' has no attribute '_season_numbering_entry'`.

- [ ] **Step 3: Implement the helper**

In `backend/scripts/pack_subtitle_cache.py`, extend the existing import of `tmdb_client` and add the numbering import. Find this block near the top:

```python
from app.matcher.tmdb_client import fetch_show_details, fetch_show_id
```

Replace it with:

```python
from app.matcher.numbering_scheme import (
    SCHEME_UNKNOWN,
    derive_numbering_scheme,
)
from app.matcher.tmdb_client import fetch_season_details, fetch_show_details, fetch_show_id
```

Then add this function immediately above `def _discover_shows(` :

```python
def _season_numbering_entry(
    tmdb_id: int | None, season: int, reference_count: int, offline: bool
) -> dict:
    """Describe how one harvested season is numbered, for the manifest.

    The corpus is numbered by whatever the subtitle providers index; TMDB may
    number the same season differently (a segment-format show catalogues
    ~7-minute shorts while the providers index 22-minute broadcast half-hours).
    Recording which one a season was harvested in is the only way a consumer can
    tell whether a code from it is a canonical TMDB coordinate, because both
    schemes are stored under the same canonical season key.

    Offline packs and unresolved shows emit UNKNOWN with no ``roster_size``:
    there is nothing to compare against, and an unknown season must stay
    distinguishable from a genuinely divergent one.
    """
    if offline or tmdb_id is None:
        return {"scheme": SCHEME_UNKNOWN}
    try:
        # Persistent-cached (TTL_SEASON), so a season already fetched during
        # harvest costs nothing. Returns 0, not None, on a missing key or a
        # failed request; derive_numbering_scheme maps that to UNKNOWN.
        roster_size = fetch_season_details(str(tmdb_id), season)
    except Exception as e:
        # A roster lookup must never abort a pack run that may cover 500 shows.
        # Widest catch is deliberate: fetch_season_details already swallows its
        # own network errors, so anything reaching here is unanticipated and the
        # season simply becomes UNKNOWN.
        logger.warning(f"  roster lookup failed for tmdb {tmdb_id} S{season:02d}: {e}")
        roster_size = 0
    scheme = derive_numbering_scheme(reference_count, roster_size)
    if scheme == SCHEME_UNKNOWN:
        return {"scheme": SCHEME_UNKNOWN}
    return {"scheme": scheme, "roster_size": roster_size}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py -v
```

Expected: PASS, 6 tests.

- [ ] **Step 5: Wire the helper into the manifest loop**

In `backend/scripts/pack_subtitle_cache.py`, find this block inside `main()`:

```python
        show_seasons: list[int] = []
        episode_counts: dict[str, int] = {}
        for season in sorted(by_season):
            episodes = sorted(by_season[season], key=lambda x: x[0])
            texts, codes = [], []
            for _ep, code, path in episodes:
                text = subtitle_cache.get_full_text(str(path))
                if text:
                    texts.append(text)
                    codes.append(code)
            if not texts:
                continue
            counts = hv.transform(texts)  # raw hashed term counts
            blocks.append((corpus_key, season, codes, counts))
            show_seasons.append(season)
            episode_counts[str(season)] = len(codes)

        if show_seasons:
            manifest_shows[corpus_key] = {
                "tmdb_id": tmdb_id,
                "name": canonical,
                "seasons": show_seasons,
                "episode_counts": episode_counts,
            }
```

Replace it with:

```python
        show_seasons: list[int] = []
        episode_counts: dict[str, int] = {}
        season_numbering: dict[str, dict] = {}
        for season in sorted(by_season):
            episodes = sorted(by_season[season], key=lambda x: x[0])
            texts, codes = [], []
            for _ep, code, path in episodes:
                text = subtitle_cache.get_full_text(str(path))
                if text:
                    texts.append(text)
                    codes.append(code)
            if not texts:
                continue
            counts = hv.transform(texts)  # raw hashed term counts
            blocks.append((corpus_key, season, codes, counts))
            show_seasons.append(season)
            episode_counts[str(season)] = len(codes)
            season_numbering[str(season)] = _season_numbering_entry(
                tmdb_id, season, len(codes), args.offline
            )

        if show_seasons:
            manifest_shows[corpus_key] = {
                "tmdb_id": tmdb_id,
                "name": canonical,
                "seasons": show_seasons,
                "episode_counts": episode_counts,
                # Additive: a backend that predates the marker ignores this key
                # and keeps loading the pack, which is why CACHE_FORMAT_VERSION
                # does not move. See the design doc for why a bump is the wrong
                # trade (every shipped backend would fall back to scraping).
                "season_numbering": season_numbering,
            }
```

- [ ] **Step 6: Add the wiring test**

Append to `backend/tests/unit/test_cache_numbering_marker.py`:

```python
@pytest.mark.unit
class TestPackManifestWiring:
    """The helper's output reaches the manifest entry under string season keys."""

    def test_season_numbering_keys_match_episode_counts_keys(self, psc):
        # The manifest's season keys are strings because JSON has no integer
        # keys; season_numbering must agree with episode_counts or a consumer
        # looking up str(season) silently misses.
        entry = {
            "tmdb_id": 4229,
            "name": "Dexter's Laboratory",
            "seasons": [1, 2],
            "episode_counts": {"1": 13, "2": 40},
            "season_numbering": {
                "1": psc._season_numbering_entry(4229, 1, 13, offline=True),
                "2": psc._season_numbering_entry(4229, 2, 40, offline=True),
            },
        }
        assert set(entry["season_numbering"]) == set(entry["episode_counts"])
        assert all(isinstance(k, str) for k in entry["season_numbering"])
```

- [ ] **Step 7: Run the full file and lint**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py -v
uv run ruff format scripts/pack_subtitle_cache.py tests/unit/test_cache_numbering_marker.py
uv run ruff check .
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' scripts/pack_subtitle_cache.py tests/unit/test_cache_numbering_marker.py
```

Expected: 7 tests PASS, ruff clean, grep counts `0`.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/pack_subtitle_cache.py backend/tests/unit/test_cache_numbering_marker.py
git commit -m "feat(cache): pack script records each season's numbering scheme

Every harvested season now carries a scheme tag and, when known, the canonical
TMDB roster size it was measured against. Offline packs and unresolved shows
emit 'unknown' rather than guessing, and a roster lookup that returns 0 (the
no-key and transient-failure contract of fetch_season_details) is unknown too,
so a TMDB blip during a nightly build cannot brand healthy seasons divergent.

Additive: CACHE_FORMAT_VERSION stays \"3\" so shipped backends keep loading the
pack and simply ignore the new key.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: `build_subtitle_cache.py` emits the marker

**Files:**
- Modify: `backend/scripts/build_subtitle_cache.py`
- Test: `backend/tests/unit/test_cache_numbering_marker.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_cache_numbering_marker.py`:

```python
@pytest.mark.unit
class TestBuildSeasonNumberingEntry:
    """build_subtitle_cache.py emits the identical marker from its harvest loop.

    The two scripts publish to the same rolling release, so a pack-built and a
    build-built artifact must be indistinguishable to a consumer.
    """

    def test_agreeing_counts_emit_tmdb_aired_with_roster_size(self, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=13) as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=1396, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_TMDB_AIRED, "roster_size": 13}
        mock_fetch.assert_called_once_with("1396", 1)

    def test_dexters_laboratory_emits_divergent(self, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=38):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_missing_tmdb_id_emits_unknown_without_calling_tmdb(self, bsc):
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=None, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_roster_lookup_returning_zero_emits_unknown(self, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=0):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_UNKNOWN}

    def test_roster_lookup_raising_emits_unknown(self, bsc):
        with patch.object(bsc, "fetch_season_details", side_effect=RuntimeError("boom")):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_UNKNOWN}

    def test_both_scripts_agree_on_the_same_inputs(self, bsc, psc):
        with patch.object(bsc, "fetch_season_details", return_value=38), patch.object(
            psc, "fetch_season_details", return_value=38
        ):
            built = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
            packed = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert built == packed
```

Note `build_subtitle_cache._season_numbering_entry` has **no** `offline` parameter: that script always has TMDB configured (it downloads subtitles), so there is no offline mode to represent. `tmdb_id=None` is its unknown path.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py::TestBuildSeasonNumberingEntry -v
```

Expected: FAIL, `AttributeError: module 'build_subtitle_cache' has no attribute '_season_numbering_entry'`.

- [ ] **Step 3: Implement**

In `backend/scripts/build_subtitle_cache.py`, confirm `fetch_season_details` is imported (it is used during harvest; if the import is not already at module level, add it to the existing `from app.matcher.tmdb_client import ...` block). Add the numbering import alongside it:

```python
from app.matcher.numbering_scheme import (
    SCHEME_UNKNOWN,
    derive_numbering_scheme,
)
```

Add this function at module level, above `main()`:

```python
def _season_numbering_entry(tmdb_id: int | None, season: int, reference_count: int) -> dict:
    """Describe how one harvested season is numbered, for the manifest.

    Mirrors ``pack_subtitle_cache._season_numbering_entry`` exactly, minus its
    ``offline`` branch: this script always has TMDB configured because it
    downloads subtitles, so a missing ``tmdb_id`` is its only unknown path. Both
    scripts publish to the same rolling release, so a pack-built and a
    build-built artifact must be indistinguishable to a consumer.
    """
    if tmdb_id is None:
        return {"scheme": SCHEME_UNKNOWN}
    try:
        roster_size = fetch_season_details(str(tmdb_id), season)
    except Exception as e:
        # A roster lookup must never abort a build that can run for 12 hours.
        logger.warning(f"  roster lookup failed for tmdb {tmdb_id} S{season:02d}: {e}")
        roster_size = 0
    scheme = derive_numbering_scheme(reference_count, roster_size)
    if scheme == SCHEME_UNKNOWN:
        return {"scheme": SCHEME_UNKNOWN}
    return {"scheme": scheme, "roster_size": roster_size}
```

If `build_subtitle_cache.py` uses `console.log` rather than a `logger` at that point in the file, use whichever is already imported at module level; do not add a new logging dependency.

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py -v
```

Expected: PASS, 13 tests.

- [ ] **Step 5: Wire it into the manifest loop**

In `backend/scripts/build_subtitle_cache.py`, find:

```python
                show_seasons: list[int] = []
                episode_counts: dict[str, int] = {}
                for season in sorted(by_season):
                    episodes = sorted(by_season[season], key=lambda x: x[0])
                    texts, codes = [], []
                    for code, path in episodes:
                        text = subtitle_cache.get_full_text(str(path))
                        if text:
                            texts.append(text)
                            codes.append(code)
                    if not texts:
                        continue
                    counts = hv.transform(texts)  # raw hashed term counts
                    blocks.append((show["tmdb_id"], show["name"], season, codes, counts))
                    show_seasons.append(season)
                    episode_counts[str(season)] = len(codes)

                if show_seasons:
                    # v3: keyed by tmdb_id so same-named shows don't collide; the name
                    # is stored so the runtime can still resolve when no id is known.
                    manifest_shows[str(show["tmdb_id"])] = {
                        "tmdb_id": show["tmdb_id"],
                        "name": show["name"],
                        "seasons": show_seasons,
                        "episode_counts": episode_counts,
                    }
```

Replace it with:

```python
                show_seasons: list[int] = []
                episode_counts: dict[str, int] = {}
                season_numbering: dict[str, dict] = {}
                for season in sorted(by_season):
                    episodes = sorted(by_season[season], key=lambda x: x[0])
                    texts, codes = [], []
                    for code, path in episodes:
                        text = subtitle_cache.get_full_text(str(path))
                        if text:
                            texts.append(text)
                            codes.append(code)
                    if not texts:
                        continue
                    counts = hv.transform(texts)  # raw hashed term counts
                    blocks.append((show["tmdb_id"], show["name"], season, codes, counts))
                    show_seasons.append(season)
                    episode_counts[str(season)] = len(codes)
                    season_numbering[str(season)] = _season_numbering_entry(
                        show["tmdb_id"], season, len(codes)
                    )

                if show_seasons:
                    # v3: keyed by tmdb_id so same-named shows don't collide; the name
                    # is stored so the runtime can still resolve when no id is known.
                    manifest_shows[str(show["tmdb_id"])] = {
                        "tmdb_id": show["tmdb_id"],
                        "name": show["name"],
                        "seasons": show_seasons,
                        "episode_counts": episode_counts,
                        # Additive: a backend predating the marker ignores this
                        # key, which is why CACHE_FORMAT_VERSION does not move.
                        "season_numbering": season_numbering,
                    }
```

- [ ] **Step 6: Run the existing build-script tests for regressions**

```bash
uv run pytest tests/unit/test_build_subtitle_cache.py tests/unit/test_cache_numbering_marker.py -v
```

Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format scripts/build_subtitle_cache.py tests/unit/test_cache_numbering_marker.py
uv run ruff check .
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' scripts/build_subtitle_cache.py
```

Expected: ruff clean, grep count `0`.

```bash
git add backend/scripts/build_subtitle_cache.py backend/tests/unit/test_cache_numbering_marker.py
git commit -m "feat(cache): build script records each season's numbering scheme

Mirrors the pack script so a pack-built and a build-built artifact are
indistinguishable to a consumer. Pinned by a test that runs the same inputs
through both.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: The matcher reads the marker and stamps it

**Files:**
- Modify: `backend/app/matcher/episode_identification.py`
- Test: `backend/tests/unit/test_cache_numbering_marker.py`

**Critical constraint:** the `EpisodeMatcher` singleton is shared across concurrent `identify_episode` threads (parallel ASR). There is an existing comment at `episode_identification.py:1740` explaining that a season-scoped value in a mutable instance slot gets clobbered by a sibling thread's scan. The marker is season-scoped, so it **must** be a per-call local, never `self.<something>`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_cache_numbering_marker.py`:

```python
import json


def _write_manifest(cache_dir, shows):
    """Write a minimal valid precomputed manifest under ``cache_dir``."""
    from app.matcher.vectorizer_config import (
        CACHE_FORMAT_VERSION,
        HASHING_N_FEATURES,
        vectorizer_config_hash,
    )

    precomputed = cache_dir / "precomputed"
    precomputed.mkdir(parents=True, exist_ok=True)
    (precomputed / "manifest.json").write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "vectorizer_config_hash": vectorizer_config_hash(),
                "content_version": "test",
                "n_features": HASHING_N_FEATURES,
                "shows": shows,
            }
        ),
        encoding="utf-8",
    )


def _matcher(cache_dir, show_name="Dexter's Laboratory", tmdb_id=4229):
    from app.matcher.episode_identification import EpisodeMatcher

    m = EpisodeMatcher.__new__(EpisodeMatcher)
    m.cache_dir = cache_dir
    m.show_name = show_name
    m.expected_tmdb_id = tmdb_id
    m._precomputed_manifest = None
    m._precomputed_idf = None
    return m


_DEXTER_SHOWS = {
    "4229": {
        "tmdb_id": 4229,
        "name": "Dexter's Laboratory",
        "seasons": [1, 2],
        "episode_counts": {"1": 13, "2": 40},
        "season_numbering": {
            "1": {"scheme": SCHEME_DIVERGENT, "roster_size": 38},
            "2": {"scheme": SCHEME_TMDB_AIRED, "roster_size": 40},
        },
    }
}


@pytest.mark.unit
class TestPrecomputedNumbering:
    """`EpisodeMatcher.precomputed_numbering` reads the marker out of the manifest.

    It returns None for anything a caller must not treat as an answer, so the
    caller stamps nothing and the runtime heuristic stays in charge.
    """

    def test_divergent_season_returns_its_marker(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(1) == {
            "scheme": SCHEME_DIVERGENT,
            "roster_size": 38,
        }

    def test_agreeing_season_returns_its_marker(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(2) == {
            "scheme": SCHEME_TMDB_AIRED,
            "roster_size": 40,
        }

    def test_season_absent_from_the_marker_returns_none(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(3) is None

    def test_pack_predating_the_marker_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_unknown_scheme_returns_none_so_the_heuristic_keeps_control(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": {"scheme": SCHEME_UNKNOWN}},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_garbage_scheme_value_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": {"scheme": "tvdb", "roster_size": 38}},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_non_dict_marker_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": "divergent"},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_no_manifest_at_all_returns_none(self, tmp_path):
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_unknown_show_returns_none(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        matcher = _matcher(tmp_path, show_name="Some Other Show", tmdb_id=99999)
        assert matcher.precomputed_numbering(1) is None

    def test_marker_is_not_cached_on_the_instance(self, tmp_path):
        # The matcher singleton is shared across concurrent identify_episode
        # threads, so a season-scoped value in an instance slot would be
        # clobbered by a sibling thread's scan. Two different seasons must give
        # two different answers from the same instance, in either order.
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        matcher = _matcher(tmp_path)
        assert matcher.precomputed_numbering(1)["scheme"] == SCHEME_DIVERGENT
        assert matcher.precomputed_numbering(2)["scheme"] == SCHEME_TMDB_AIRED
        assert matcher.precomputed_numbering(1)["scheme"] == SCHEME_DIVERGENT
        assert not any("numbering" in a for a in vars(matcher))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py::TestPrecomputedNumbering -v
```

Expected: FAIL, `AttributeError: 'EpisodeMatcher' object has no attribute 'precomputed_numbering'`.

- [ ] **Step 3: Implement `precomputed_numbering`**

In `backend/app/matcher/episode_identification.py`, add to the imports near the other `app.matcher` imports:

```python
from app.matcher.numbering_scheme import SCHEME_UNKNOWN, VALID_SCHEMES
```

Add this method immediately **above** `def _load_precomputed_season(self, season_number):` (around line 1320):

```python
    def precomputed_numbering(self, season_number) -> dict | None:
        """The numbering marker the shipped pack records for this show + season.

        Returns ``{"scheme": ..., "roster_size": ...}`` when the manifest carries
        a usable one, else ``None``. ``None`` covers every case a caller must not
        treat as an answer: a pack built before the marker existed, a season the
        pack does not describe, an explicitly ``unknown`` season, and a malformed
        entry. The caller then stamps nothing and the runtime size heuristic
        stays in charge, which is exactly the pre-marker behaviour.

        Read as a per-call local by ``identify_episode``, never cached on the
        instance. The matcher singleton is shared across concurrent
        ``identify_episode`` threads (parallel ASR), so a season-scoped value in
        an instance slot would be clobbered by a sibling thread's scan, the same
        hazard the per-call TF-IDF matcher below exists to avoid.
        """
        manifest = self._load_precomputed_manifest()
        _key, show_entry = _resolve_corpus_entry(manifest, self.show_name, self.expected_tmdb_id)
        if not show_entry:
            return None
        marker = (show_entry.get("season_numbering") or {}).get(str(season_number))
        if not isinstance(marker, dict):
            return None
        scheme = marker.get("scheme")
        if scheme not in VALID_SCHEMES or scheme == SCHEME_UNKNOWN:
            return None
        return marker
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_cache_numbering_marker.py::TestPrecomputedNumbering -v
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Stamp the marker into `match_stats`**

In `backend/app/matcher/episode_identification.py`, inside `identify_episode`, find:

```python
            # 1. Get References - shipped precomputed vectors, else scraped SRT
            precomputed = self._load_precomputed_season(season_number)
            using_precomputed = precomputed is not None
```

Insert directly after it:

```python
            # Per-call local, never an instance slot: see precomputed_numbering.
            # A scraped season has no marker, so this stays None and every later
            # reader falls back to the runtime size heuristic.
            numbering = self.precomputed_numbering(season_number) if using_precomputed else None
```

Then find the `match_stats` dict (around line 2049) and add the stamp immediately after the closing brace:

```python
            match_stats = {
                "matches_found": matches_found_count,
                "matches_rejected": matches_rejected_count,
                "total_chunks": len(scan_points),
                "multi_episode": multi_detail,
                # How many episodes the corpus claims for this season. Recorded on
                # every result, not just the refusal path above, because callers
                # need it to tell whether the corpus is numbered like the TMDB
                # roster: a segment-format show has a 13-entry half-hour corpus
                # against a 38-entry segment roster, and a code from the former
                # must not be read as a coordinate in the latter.
                "reference_count": total,
            }
            # The pack's own statement about this season's numbering, when it
            # makes one. Strictly better than inferring it from the two counts
            # above: those are a proxy computed at match time, and grafting
            # scraped SRTs onto a precomputed season (see
            # _augment_with_downloaded_srts) inflates reference_count enough to
            # push an agreeing season into a false divergent verdict.
            if numbering:
                match_stats["numbering_scheme"] = numbering["scheme"]
                if isinstance(numbering.get("roster_size"), int):
                    match_stats["pack_roster_size"] = numbering["roster_size"]
```

- [ ] **Step 6: Add the stamping test**

Append to `backend/tests/unit/test_cache_numbering_marker.py`:

```python
@pytest.mark.unit
class TestMatchStatsStamping:
    """The marker reaches match_details, which is what consumers read.

    Exercised at the dict level rather than by driving identify_episode end to
    end (that needs audio, ffmpeg and a real vector corpus). The contract these
    pin is the shape numbering_schemes_agree consumes.
    """

    def _stamp(self, numbering):
        """Reproduce the stamping branch from identify_episode."""
        match_stats = {"reference_count": 13}
        if numbering:
            match_stats["numbering_scheme"] = numbering["scheme"]
            if isinstance(numbering.get("roster_size"), int):
                match_stats["pack_roster_size"] = numbering["roster_size"]
        return match_stats

    def test_marked_season_stamps_scheme_and_roster(self):
        stats = self._stamp({"scheme": SCHEME_DIVERGENT, "roster_size": 38})
        assert stats["numbering_scheme"] == SCHEME_DIVERGENT
        assert stats["pack_roster_size"] == 38

    def test_scraped_season_stamps_nothing(self):
        stats = self._stamp(None)
        assert "numbering_scheme" not in stats
        assert "pack_roster_size" not in stats

    def test_marker_without_roster_size_stamps_only_the_scheme(self):
        stats = self._stamp({"scheme": SCHEME_TMDB_AIRED})
        assert stats["numbering_scheme"] == SCHEME_TMDB_AIRED
        assert "pack_roster_size" not in stats
```

- [ ] **Step 7: Run the matcher's existing tests for regressions**

```bash
uv run pytest tests/unit/test_precomputed_cache.py tests/unit/test_precomputed_augmentation.py tests/unit/test_cache_numbering_marker.py -v
```

Expected: all PASS.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff format app/matcher/episode_identification.py tests/unit/test_cache_numbering_marker.py
uv run ruff check .
grep -c $'\xe2\x80\x94\|\xe2\x80\x93' app/matcher/episode_identification.py
```

Note: `episode_identification.py` may already contain dashes from earlier commits. Only your added lines must be clean. Check with:

```bash
git diff -U0 -- app/matcher/episode_identification.py | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```

Expected: `0`.

```bash
git add backend/app/matcher/episode_identification.py backend/tests/unit/test_cache_numbering_marker.py
git commit -m "feat(matcher): stamp the pack's numbering marker into match_details

precomputed_numbering() reads the marker the pack records for this show and
season and identify_episode stamps it beside reference_count. A scraped season
stamps nothing, so consumers fall back to the runtime size heuristic exactly as
before.

Held as a per-call local, never an instance slot: the matcher singleton is
shared across concurrent identify_episode threads and a season-scoped value in
an instance slot gets clobbered by a sibling thread's scan.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: `numbering_schemes_agree` prefers the marker

**Files:**
- Modify: `backend/app/services/matching_coordinator.py:451-476`
- Modify: `backend/app/services/finalization_coordinator.py:65-73`
- Test: `backend/tests/unit/test_matching_coordinator.py`

- [ ] **Step 1: Write the failing test**

Append a new class to `backend/tests/unit/test_matching_coordinator.py` (beside PR #673's existing `numbering_schemes_agree` tests):

```python
@pytest.mark.unit
class TestNumberingSchemesAgreePrefersTheMarker:
    """A recorded marker beats the size heuristic.

    The heuristic infers agreement from two counts at match time. The marker is
    the pack's own statement, made at build time against a known roster. Where
    they disagree the marker wins, and the heuristic remains the answer for
    packs that predate it and for scraped seasons that never came from a pack.
    """

    def test_marker_wins_when_counts_disagree(self):
        # _augment_with_downloaded_srts grafts scraped SRTs onto a precomputed
        # season, inflating reference_count. The heuristic would call this
        # divergent; the marker knows better.
        from app.services.matching_coordinator import numbering_schemes_agree

        details = {
            "numbering_scheme": "tmdb_aired",
            "reference_count": 14,
            "roster_size": 13,
        }
        assert numbering_schemes_agree(details) is True

    def test_marker_wins_when_counts_coincidentally_agree(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        details = {
            "numbering_scheme": "divergent",
            "reference_count": 13,
            "roster_size": 13,
        }
        assert numbering_schemes_agree(details) is False

    def test_absent_marker_falls_through_to_the_heuristic(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        assert numbering_schemes_agree({"reference_count": 13, "roster_size": 38}) is False
        assert numbering_schemes_agree({"reference_count": 13, "roster_size": 13}) is True

    def test_unknown_marker_falls_through_to_the_heuristic(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        details = {
            "numbering_scheme": "unknown",
            "reference_count": 13,
            "roster_size": 38,
        }
        assert numbering_schemes_agree(details) is False

    def test_unrecognised_marker_falls_through_to_the_heuristic(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        details = {
            "numbering_scheme": "tvdb",
            "reference_count": 13,
            "roster_size": 13,
        }
        assert numbering_schemes_agree(details) is True

    def test_fall_through_still_returns_none_when_counts_are_unusable(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        assert numbering_schemes_agree({"numbering_scheme": "unknown"}) is None
        assert numbering_schemes_agree({}) is None

    def test_marker_alone_is_enough_without_any_counts(self):
        from app.services.matching_coordinator import numbering_schemes_agree

        assert numbering_schemes_agree({"numbering_scheme": "divergent"}) is False
        assert numbering_schemes_agree({"numbering_scheme": "tmdb_aired"}) is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_matching_coordinator.py::TestNumberingSchemesAgreePrefersTheMarker -v
```

Expected: FAIL on `test_marker_wins_when_counts_disagree` (returns `False`, not `True`) and on `test_marker_alone_is_enough_without_any_counts` (returns `None`, not `False`).

- [ ] **Step 3: Implement the precedence**

In `backend/app/services/matching_coordinator.py`, add to the imports:

```python
from app.matcher.numbering_scheme import SCHEME_DIVERGENT, SCHEME_TMDB_AIRED
```

Replace the whole of `numbering_schemes_agree` (currently at line 451) with:

```python
def numbering_schemes_agree(details: dict) -> bool | None:
    """Whether the reference corpus is numbered like the TMDB roster for this season.

    Two consumers ask this: the #200 ordering projection, which must not
    dereference a code that is not a canonical coordinate, and the conjoined
    hint, whose runtime estimate is computed against the TMDB roster while the
    verdict that would confirm it is voted over the reference corpus.

    Segment-format shows are where the two diverge: TMDB catalogues each
    ~7-minute short (38 entries for Dexter's Laboratory season 1) while the
    subtitle corpus is numbered by 22-minute broadcast half-hours (13).

    Answered in two ways, in this order:

    1. ``numbering_scheme``, the marker the published pack records for the
       season. This is the pack's own statement, made at build time against a
       known roster, and it is the only source that does not have to infer.
    2. Failing that, equal ``reference_count`` and ``roster_size``. A proxy, not
       a proof, but a corpus that disagrees with the roster on how many episodes
       a season HAS cannot be speaking the roster's language.

    The fallback is not vestigial: it is the answer for packs published before
    the marker existed, for seasons the builder could not resolve a roster for,
    and for scraped-SRT seasons that never came from a pack at all.

    Returns None when neither source can answer, so callers keep their prior
    behaviour rather than inferring agreement.
    """
    scheme = details.get("numbering_scheme")
    if scheme == SCHEME_TMDB_AIRED:
        return True
    if scheme == SCHEME_DIVERGENT:
        return False

    reference_count = details.get("reference_count")
    roster_size = details.get("roster_size")
    if not isinstance(reference_count, int) or not isinstance(roster_size, int):
        return None
    if reference_count <= 0 or roster_size <= 0:
        return None
    return reference_count == roster_size
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_matching_coordinator.py -v
```

Expected: all PASS, including PR #673's existing `numbering_schemes_agree` tests, which must still pass unchanged.

- [ ] **Step 5: Make the two log lines marker-aware**

Both consumers currently explain a `False` verdict by quoting `reference_count` and `roster_size`. When the verdict came from the marker those may be absent or (in the augmented-SRT case) actively misleading.

In `backend/app/services/finalization_coordinator.py`, replace:

```python
    if numbering_schemes_agree(details) is False:
        logger.info(
            f"Keeping aired numbering for this title instead of the {ordering} ordering: "
            f"its episode code came from a {details.get('reference_count')}-episode "
            f"reference corpus against a {details.get('roster_size')}-episode TMDB "
            f"roster, so it is not a canonical coordinate the episode group can "
            f"resolve."
        )
        return "aired"
```

with:

```python
    if numbering_schemes_agree(details) is False:
        if details.get("numbering_scheme"):
            why = (
                f"the published subtitle cache records this season as "
                f"{details['numbering_scheme']!r} numbering against a "
                f"{details.get('pack_roster_size')}-episode TMDB roster"
            )
        else:
            why = (
                f"its episode code came from a {details.get('reference_count')}-episode "
                f"reference corpus against a {details.get('roster_size')}-episode TMDB roster"
            )
        logger.info(
            f"Keeping aired numbering for this title instead of the {ordering} ordering: "
            f"{why}, so it is not a canonical coordinate the episode group can resolve."
        )
        return "aired"
```

In `backend/app/services/matching_coordinator.py`, replace the conjoined-hint log inside `_apply_multi_episode_review`:

```python
    if not confirmed_multi and numbering_schemes_agree(details) is False:
        logger.info(
            f"Title {sanitize_log_value(getattr(title, 'id', None))}: runtime hint of "
            f"~{conjoined_hint} conjoined episodes not actionable -- the reference "
            f"corpus holds {details.get('reference_count')} episodes for this season "
            f"against a roster of {details.get('roster_size')}, so the two use "
            f"different episode numbering and no vote run could confirm the hint. "
            f"Leaving the match as-is."
        )
        return False
```

with:

```python
    if not confirmed_multi and numbering_schemes_agree(details) is False:
        if details.get("numbering_scheme"):
            why = (
                f"the published subtitle cache records this season as "
                f"{details['numbering_scheme']!r} numbering against a "
                f"{details.get('pack_roster_size')}-episode roster"
            )
        else:
            why = (
                f"the reference corpus holds {details.get('reference_count')} episodes "
                f"for this season against a roster of {details.get('roster_size')}"
            )
        logger.info(
            f"Title {sanitize_log_value(getattr(title, 'id', None))}: runtime hint of "
            f"~{conjoined_hint} conjoined episodes not actionable: {why}, so the two use "
            f"different episode numbering and no vote run could confirm the hint. "
            f"Leaving the match as-is."
        )
        return False
```

- [ ] **Step 6: Run both coordinators' test files**

```bash
uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_finalization_coordinator.py -v
```

Expected: all PASS. If a #673 test asserts on the exact old log text, update that assertion to match the new heuristic-branch wording (the heuristic branch keeps the same facts, only the sentence frame moved).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format app/services/matching_coordinator.py app/services/finalization_coordinator.py tests/unit/test_matching_coordinator.py
uv run ruff check .
git diff -U0 -- app/services/ | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```

Expected: ruff clean, grep count `0`.

```bash
git add backend/app/services/matching_coordinator.py backend/app/services/finalization_coordinator.py backend/tests/unit/test_matching_coordinator.py
git commit -m "feat(matching): prefer the pack's numbering marker over the size heuristic

numbering_schemes_agree now answers from numbering_scheme when the pack records
one, and falls back to the reference_count/roster_size comparison otherwise. The
fallback is not vestigial: it is the answer for packs published before the
marker, for seasons with no resolvable roster, and for scraped-SRT seasons that
never came from a pack.

Both consumers, the ordering-projection skip and the conjoined-hint gate,
inherit this without change because they already route through one function.
Their log lines now explain which source decided.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Validator checks the marker and reports divergence

**Files:**
- Modify: `backend/scripts/validate_subtitle_cache.py`
- Test: `backend/tests/unit/test_validate_subtitle_cache.py`

Divergence is legitimate data, not corruption: a segment-format show's corpus genuinely is numbered differently, and saying so is the whole point of the marker. The validator therefore **warns and counts**, never fails, on divergence. It fails only on a malformed marker.

This is also where the spec's "visible from an audit run" requirement lands. See the File Structure note for why `audit_subtitle_cache.py` is not the right home.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_validate_subtitle_cache.py`:

```python
@pytest.mark.unit
class TestSeasonNumberingValidation:
    """The marker is checked for shape, and divergence is reported, not failed.

    A validator that failed on divergence would block every future publish of
    every segment-format show, which is precisely the data the marker exists to
    carry.
    """

    def _shows(self, season_numbering):
        return {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": season_numbering,
            }
        }

    def test_divergent_season_passes_and_is_counted(self, vsc, tmp_path):
        _make_assets(
            vsc,
            tmp_path,
            manifest_overrides={
                "shows": self._shows({"1": {"scheme": "divergent", "roster_size": 38}})
            },
        )
        result = vsc.validate(tmp_path)
        assert result.failures == []
        assert result.summary["n_divergent_seasons"] == 1

    def test_agreeing_season_is_not_counted_as_divergent(self, vsc, tmp_path):
        _make_assets(
            vsc,
            tmp_path,
            manifest_overrides={
                "shows": self._shows({"1": {"scheme": "tmdb_aired", "roster_size": 13}})
            },
        )
        result = vsc.validate(tmp_path)
        assert result.failures == []
        assert result.summary["n_divergent_seasons"] == 0

    def test_pack_predating_the_marker_passes_with_zero_divergent(self, vsc, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
            }
        }
        _make_assets(vsc, tmp_path, manifest_overrides={"shows": shows})
        result = vsc.validate(tmp_path)
        assert result.failures == []
        assert result.summary["n_divergent_seasons"] == 0

    def test_unrecognised_scheme_fails(self, vsc, tmp_path):
        _make_assets(
            vsc,
            tmp_path,
            manifest_overrides={
                "shows": self._shows({"1": {"scheme": "tvdb", "roster_size": 38}})
            },
        )
        result = vsc.validate(tmp_path)
        assert any("unrecognised numbering scheme" in f for f in result.failures)

    def test_marker_for_a_season_not_in_seasons_fails(self, vsc, tmp_path):
        _make_assets(
            vsc,
            tmp_path,
            manifest_overrides={
                "shows": self._shows(
                    {
                        "1": {"scheme": "tmdb_aired", "roster_size": 13},
                        "9": {"scheme": "tmdb_aired", "roster_size": 13},
                    }
                )
            },
        )
        result = vsc.validate(tmp_path)
        assert any("season 9" in f for f in result.failures)

    def test_tmdb_aired_roster_contradicting_episode_counts_fails(self, vsc, tmp_path):
        # "tmdb_aired" means the two counts are equal by definition. A roster
        # that disagrees with episode_counts means the builder emitted a
        # self-contradictory marker.
        _make_assets(
            vsc,
            tmp_path,
            manifest_overrides={
                "shows": self._shows({"1": {"scheme": "tmdb_aired", "roster_size": 38}})
            },
        )
        result = vsc.validate(tmp_path)
        assert any("contradicts" in f for f in result.failures)

    def test_non_dict_marker_fails(self, vsc, tmp_path):
        _make_assets(
            vsc, tmp_path, manifest_overrides={"shows": self._shows({"1": "divergent"})}
        )
        result = vsc.validate(tmp_path)
        assert any("not a dict" in f for f in result.failures)

    def test_non_dict_season_numbering_fails(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, manifest_overrides={"shows": self._shows(["divergent"])})
        result = vsc.validate(tmp_path)
        assert any("season_numbering" in f for f in result.failures)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_validate_subtitle_cache.py::TestSeasonNumberingValidation -v
```

Expected: FAIL, `KeyError: 'n_divergent_seasons'` on the first three, and empty `failures` on the rest.

- [ ] **Step 3: Implement**

In `backend/scripts/validate_subtitle_cache.py`, add to the imports:

```python
from app.matcher.numbering_scheme import SCHEME_DIVERGENT, SCHEME_TMDB_AIRED, VALID_SCHEMES
```

Add this function above `def validate(`:

```python
def _check_season_numbering(shows: dict) -> tuple[list[str], int, list[str]]:
    """Validate the per-season numbering markers across the whole manifest.

    Returns ``(failures, n_divergent, divergent_labels)``.

    Divergence is NOT a failure. A segment-format show's corpus genuinely is
    numbered differently from the canonical TMDB roster (Dexter's Laboratory:
    13 harvested broadcast half-hours against a 38-entry segment roster), and
    recording that is the entire purpose of the marker. Failing on it would
    block every future publish of every such show. It is counted and named
    instead, so the corpus problem is visible from a publish-gate log rather
    than only from a user's diagnostic bundle.

    What IS a failure is a self-contradictory or malformed marker, because that
    means the builder is emitting something no consumer can trust.
    """
    failures: list[str] = []
    divergent: list[str] = []

    for corpus_key, entry in shows.items():
        if not isinstance(entry, dict):
            continue  # already reported by the caller's shape check
        numbering = entry.get("season_numbering")
        if numbering is None:
            continue  # pack predates the marker; the runtime falls back
        show_display = entry.get("name") or corpus_key
        if not isinstance(numbering, dict):
            failures.append(
                f"manifest shows entry {corpus_key!r} has a 'season_numbering' that "
                f"is not a dict: {type(numbering).__name__}"
            )
            continue

        seasons = {str(s) for s in entry.get("seasons", [])}
        episode_counts = entry.get("episode_counts") or {}
        for season_key, marker in numbering.items():
            if season_key not in seasons:
                failures.append(
                    f"{show_display!r} has numbering for season {season_key} but "
                    f"season {season_key} is not in its 'seasons' list"
                )
                continue
            if not isinstance(marker, dict):
                failures.append(
                    f"{show_display!r} S{season_key} numbering marker is not a dict: "
                    f"{type(marker).__name__}"
                )
                continue
            scheme = marker.get("scheme")
            if scheme not in VALID_SCHEMES:
                failures.append(
                    f"{show_display!r} S{season_key} has an unrecognised numbering "
                    f"scheme {scheme!r}; expected one of {sorted(VALID_SCHEMES)}"
                )
                continue
            roster_size = marker.get("roster_size")
            reference_count = episode_counts.get(season_key)
            if (
                scheme == SCHEME_TMDB_AIRED
                and isinstance(roster_size, int)
                and isinstance(reference_count, int)
                and roster_size != reference_count
            ):
                failures.append(
                    f"{show_display!r} S{season_key} is marked {SCHEME_TMDB_AIRED!r} but "
                    f"its roster_size {roster_size} contradicts its episode_counts "
                    f"{reference_count}"
                )
            if scheme == SCHEME_DIVERGENT:
                divergent.append(
                    f"{show_display} S{season_key} ({reference_count} refs vs "
                    f"{roster_size} roster)"
                )

    return failures, len(divergent), sorted(divergent)
```

Then wire it into `validate()`. Find this block near the end:

```python
    summary = {
        "cache_format_version": manifest.get("cache_format_version"),
        "vectorizer_config_hash": manifest.get("vectorizer_config_hash"),
        "n_features": manifest.get("n_features"),
        "n_shows": n_shows,
        "tarball_size_bytes": tarball_size,
        "tarball_sha256": actual_sha,
    }
    return ValidationResult(failures=failures, summary=summary)
```

Replace it with:

```python
    numbering_failures, n_divergent, divergent_labels = _check_season_numbering(shows)
    failures.extend(numbering_failures)

    summary = {
        "cache_format_version": manifest.get("cache_format_version"),
        "vectorizer_config_hash": manifest.get("vectorizer_config_hash"),
        "n_features": manifest.get("n_features"),
        "n_shows": n_shows,
        # Seasons whose corpus is numbered differently from the canonical TMDB
        # roster. Informational, never a failure: see _check_season_numbering.
        "n_divergent_seasons": n_divergent,
        "divergent_seasons": divergent_labels,
        "tarball_size_bytes": tarball_size,
        "tarball_sha256": actual_sha,
    }
    return ValidationResult(failures=failures, summary=summary)
```

Finally, make `main()` print the divergent list readably rather than as a raw Python list. In `main()`, find:

```python
            if key == "tarball_size_bytes":
```

and insert this branch immediately before it:

```python
            if key == "divergent_seasons":
                # Informational, not a failure. Printed so the publish-gate log
                # shows which shows ship a corpus numbered differently from
                # TMDB's roster, without anyone needing a diagnostic bundle.
                if value:
                    print(f"divergent seasons ({len(value)}):")
                    for label in value:
                        print(f"  - {label}")
                else:
                    print("divergent seasons: none")
                continue
            if key == "tarball_size_bytes":
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_validate_subtitle_cache.py -v
```

Expected: all PASS, including the pre-existing validator tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format scripts/validate_subtitle_cache.py tests/unit/test_validate_subtitle_cache.py
uv run ruff check .
git diff -U0 -- scripts/validate_subtitle_cache.py | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```

Expected: ruff clean, grep count `0`.

```bash
git add backend/scripts/validate_subtitle_cache.py backend/tests/unit/test_validate_subtitle_cache.py
git commit -m "feat(cache): publish gate validates and reports season numbering

The validator checks the marker for shape (unknown scheme values, markers for
seasons the show does not list, a tmdb_aired marker whose roster contradicts its
own episode count) and counts divergent seasons into the summary, naming them in
the CI log.

Divergence itself never fails: a segment-format show's corpus genuinely is
numbered differently, and failing on it would block every future publish of
every such show.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Pin the degrade-safe path

The design chose not to bump `CACHE_FORMAT_VERSION`, so the refusal path that would have made an old backend degrade safely will not be exercised in production. These tests pin it so it still works when a future change does need a bump, and pin the forwards-compatibility guarantee the no-bump decision rests on.

**Files:**
- Test: `backend/tests/unit/test_precomputed_cache.py`
- Test: `backend/tests/unit/test_precomputed_cache_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_precomputed_cache.py`:

```python
@pytest.mark.unit
class TestFormatVersionGate:
    """The no-bump decision rests on these two properties.

    `season_numbering` was added additively so CACHE_FORMAT_VERSION could stay
    "3" and every shipped backend would keep loading the pack. That trade is only
    sound if (a) an unknown format version really is refused, so a FUTURE bump
    still degrades to scraping rather than loading incompatible vectors, and (b)
    an unrecognised extra key at the CURRENT version really is ignored.
    """

    def test_unknown_format_version_is_refused(self, tmp_path):
        import json

        from app.matcher.episode_identification import load_precomputed_manifest
        from app.matcher.vectorizer_config import HASHING_N_FEATURES, vectorizer_config_hash

        precomputed = tmp_path / "precomputed"
        precomputed.mkdir(parents=True)
        (precomputed / "manifest.json").write_text(
            json.dumps(
                {
                    "cache_format_version": "999",
                    "vectorizer_config_hash": vectorizer_config_hash(),
                    "n_features": HASHING_N_FEATURES,
                    "shows": {},
                }
            ),
            encoding="utf-8",
        )
        assert load_precomputed_manifest(tmp_path) is None

    def test_unrecognised_extra_key_at_the_current_version_still_loads(self, tmp_path):
        import json

        from app.matcher.episode_identification import load_precomputed_manifest
        from app.matcher.vectorizer_config import (
            CACHE_FORMAT_VERSION,
            HASHING_N_FEATURES,
            vectorizer_config_hash,
        )

        precomputed = tmp_path / "precomputed"
        precomputed.mkdir(parents=True)
        (precomputed / "manifest.json").write_text(
            json.dumps(
                {
                    "cache_format_version": CACHE_FORMAT_VERSION,
                    "vectorizer_config_hash": vectorizer_config_hash(),
                    "n_features": HASHING_N_FEATURES,
                    "shows": {
                        "4229": {
                            "tmdb_id": 4229,
                            "name": "Dexter's Laboratory",
                            "seasons": [1],
                            "episode_counts": {"1": 13},
                            "season_numbering": {"1": {"scheme": "divergent"}},
                            "some_future_key": {"anything": True},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        manifest = load_precomputed_manifest(tmp_path)
        assert manifest is not None
        assert manifest["shows"]["4229"]["name"] == "Dexter's Laboratory"
```

Append to `backend/tests/unit/test_precomputed_cache_service.py`:

```python
@pytest.mark.unit
class TestDownloaderFormatVersionGate:
    """The downloader refuses an unknown remote format before fetching anything.

    Half the no-bump safety argument: an old backend pointed at a pack it cannot
    read must skip, not download 500 MB and then fail to use it.
    """

    async def test_unknown_remote_format_skips_without_downloading(self, monkeypatch, tmp_path):
        from app.services import precomputed_cache_service as svc

        class _Config:
            precomputed_cache_enabled = True
            subtitles_cache_path = str(tmp_path)
            precomputed_cache_version = ""

        async def _fake_get_config():
            return _Config()

        downloaded = []

        async def _fake_download(*args, **kwargs):
            downloaded.append(args)
            return True

        monkeypatch.setattr(
            "app.services.config_service.get_config", _fake_get_config, raising=False
        )
        monkeypatch.setattr(
            svc, "_fetch_remote_manifest", lambda: _async_value({"cache_format_version": "999"})
        )
        monkeypatch.setattr(svc, "_download_and_extract", _fake_download)

        await svc._ensure_precomputed_cache_inner()
        assert downloaded == []


async def _async_value(value):
    return value
```

If `test_precomputed_cache_service.py` already has a monkeypatching idiom for `get_config` and `_fetch_remote_manifest`, follow that file's existing pattern rather than the sketch above. The assertion that matters is: an unknown `cache_format_version` means `_download_and_extract` is never called.

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/unit/test_precomputed_cache.py::TestFormatVersionGate tests/unit/test_precomputed_cache_service.py::TestDownloaderFormatVersionGate -v
```

Expected: PASS. Both gates already exist in the code (`episode_identification.py:141` and `precomputed_cache_service.py`'s `remote_format != CACHE_FORMAT_VERSION`), so these should pass on first run. **If any fails, that is a real finding**: the no-bump decision depends on it. Stop and report rather than weakening the test.

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff format tests/unit/test_precomputed_cache.py tests/unit/test_precomputed_cache_service.py
uv run ruff check .
```

```bash
git add backend/tests/unit/test_precomputed_cache.py backend/tests/unit/test_precomputed_cache_service.py
git commit -m "test(cache): pin the format-version gate the no-bump decision rests on

season_numbering was added additively so CACHE_FORMAT_VERSION could stay \"3\"
and every shipped backend would keep loading the pack. That trade is sound only
if an unknown format version is genuinely refused by both the matcher's manifest
loader and the downloader, and an unrecognised extra key at the current version
is genuinely ignored. Neither path will be exercised in production now, so both
are pinned by test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Documentation and CHANGELOG

**Files:**
- Modify: `CHANGELOG.md` (repo root)
- Modify: `docs/subtitle-cache.md` if it documents the manifest shape

- [ ] **Step 1: Check whether the manifest shape is documented**

```bash
grep -rn "episode_counts" docs/ .github/
```

If `docs/subtitle-cache.md` (or any other doc) shows an example manifest entry, add `season_numbering` to that example with a one-paragraph explanation of the three values and why `tvdb` is not among them. If nothing documents the shape, skip to Step 2.

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, under the `## [Unreleased]` heading, add to the existing `### Added` subsection (create it if absent, placing it before `### Changed`):

```markdown
- The published subtitle cache now records, for every harvested season, whether its
  episode numbering matches TMDB's canonical roster, along with the roster size it was
  measured against. Some shows are catalogued by TMDB as individual short segments while
  the subtitle sources number the longer broadcast episodes those segments were assembled
  into, and until now nothing in the published cache said which of the two a given season
  used. Engram reads the recorded answer instead of guessing from episode counts, so a
  track matched as the first episode of a season is no longer at risk of being filed under
  an unrelated episode number. Caches published before this change keep working unchanged.
```

Note: `CHANGELOG.md` has `merge=union` in `.gitattributes`, so a concurrent PR's `[Unreleased]` entry merges automatically on rebase. Do not reorder existing bullets.

- [ ] **Step 3: Dash check the CHANGELOG entry**

```bash
git diff -U0 -- CHANGELOG.md | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```

Expected: `0`.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/
git commit -m "docs: changelog entry for the subtitle-cache numbering marker

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Full verification and PR

- [ ] **Step 1: Run the full backend unit tier**

```bash
uv run pytest tests/unit/ -q
```

Expected: all PASS. This tier takes several minutes. Do not use `-p no:logging` to speed it up: it errors roughly ten caplog-dependent tests.

- [ ] **Step 2: Run the integration and pipeline tiers**

```bash
uv run pytest tests/integration/ tests/pipeline/ -q
```

Expected: all PASS. If a pipeline test fails with `no such table`, the worktree's `engram.db` is a zero-byte stub; run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first and re-run.

- [ ] **Step 3: Lint the whole backend**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: both clean.

- [ ] **Step 4: Dash check every file this branch touched**

```bash
cd ..
git diff -U0 origin/claude/conjoined-tracks-matching-naming-316a8e...HEAD | grep '^+' | grep -c $'\xe2\x80\x94\|\xe2\x80\x93'
```

Expected: `0`.

- [ ] **Step 5: Confirm the format version did not move**

```bash
grep -n 'CACHE_FORMAT_VERSION = ' backend/app/matcher/vectorizer_config.py
```

Expected: `CACHE_FORMAT_VERSION = "3"`. If this reads anything else, the additive-compatibility argument in the design and in the Task 7 commit message is void. Stop and report.

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin feat/subtitle-cache-numbering-scheme
```

Open the PR **against `main`**, not against #673's branch, and state the dependency in the body. Include:
- what the marker is and the three values
- the TVDB finding and that it falsifies the review's inference, so `tvdb` is deliberately not a value
- that `CACHE_FORMAT_VERSION` stays `"3"` and why
- that this depends on #673 and should merge after it (rebase onto main once #673 lands)
- that `audit_subtitle_cache.py` was not touched, and the divergence reporting lives in the validator instead, with the reason
- that publishing runs from a nightly timer on a separate host and no publish was triggered

End the PR body with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 7: Trigger the review bot**

`code-review.yml` only fires on the `opened` event, so after `gh pr create` post the comment explicitly:

```bash
gh pr comment <PR_NUMBER> --body "@claude please review this PR"
```

- [ ] **Step 8: Confirm no servers were left running**

This branch starts no backend or frontend, so there should be nothing to clean up. Verify:

```bash
tasklist | grep -i "uvicorn\|makemkvcon"
```

Expected: no output. If a process appears, it belongs to another session; do not kill it.

---

## Notes for the implementer

**If a #673 test breaks in Task 5**, read it before changing it. The precedence change must not alter any behaviour when `numbering_scheme` is absent, which is the case every one of #673's tests constructs. A genuine break there means the precedence was implemented wrong, not that the test is stale. The one legitimate update is a test asserting on exact log text, because Task 5 Step 5 reframes both sentences.

**If `fetch_season_details` is not already imported at module level in `build_subtitle_cache.py`**, check how the script currently calls it before adding an import. It may be imported inside a function to defer the config lookup; if so, follow that pattern in `_season_numbering_entry` rather than hoisting it.

**Do not add a `tvdb` scheme value**, even if it seems like a natural extension. TheTVDB numbers Dexter's Laboratory by segment in both its official order (38/108/36/38, identical to TMDB) and its DVD order (39 for season 1). The corpus's 13/40/13/13 is not TVDB numbering. This was verified during design and is recorded in the spec.

**Do not reconcile the namespaces.** Mapping half-hour *k* onto its canonical segments is explicitly out of scope. This branch records what scheme a season uses; it does not translate between schemes.
