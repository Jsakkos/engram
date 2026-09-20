# Subtitle cache: record the numbering scheme of every harvested season

**Date:** 2026-09-20
**Status:** design approved, not yet implemented
**Origin:** item 5 of `docs/superpowers/reviews/2026-09-20-dexters-lab-numbering-namespace.md`
**Depends on:** PR #673 (`claude/conjoined-tracks-matching-naming-316a8e`), which introduces
`numbering_schemes_agree`. This branch is cut from #673's tip.

## Problem

The published subtitle cache stores reference vectors under canonical TMDB season keys while the
references themselves are numbered in the subtitle providers' scheme. For any show TMDB
catalogues by segment, the two disagree.

From the live manifest (`subtitle-cache-latest`, content version 2026-09-20):

```json
{"tmdb_id": 4229, "name": "Dexter's Laboratory",
 "seasons": [1, 2, 3, 4],
 "episode_counts": {"1": 13, "2": 40, "3": 13, "4": 13}}
```

TMDB's aired rosters for those seasons are 38 / 108 / 36 / 38. The corpus is numbered by
22-minute broadcast half-hours; TMDB numbers ~7-minute shorts. So `S01E01` in the pack means
"half-hour 1" while every consumer reads it as "canonical segment 1". That is what filed a track
matched as `S01E01` to `S01E19.mkv`.

PR #673 contains the damage with a size heuristic: a season whose `reference_count` differs from
its `roster_size` is treated as unverified, and the ordering projection is skipped. That is the
right containment, but it is an inference drawn at match time from two numbers that happen to be
lying around. **No backend fix can distinguish the two schemes while the pack does not record
which one it used.** This design makes the pack say.

## Finding: TheTVDB is not the namespace

The review inferred that the corpus lives in a TVDB-shaped namespace, reasoning from the
providers' indexing and the pack counts, and flagged that the claim was unverified. It was
checked against TheTVDB during this design and **it is false**.

| Source | Dexter's Laboratory, seasons 1-4 |
|---|---|
| TMDB aired | 38 / 108 / 36 / 38 |
| TheTVDB official (aired) order | 38 / 108 / 36 / 38 |
| TheTVDB DVD order, season 1 | 39 |
| The published subtitle corpus | 13 / 40 / 13 / 13 |

TheTVDB numbers this show by segment in both its official and its DVD order, identically to TMDB.
The corpus's half-hour numbering matches no catalogue Engram could name.

Two consequences:

1. The scheme tag must not carry a `"tvdb"` value. It would encode a provenance the builder
   cannot observe and that the evidence contradicts, into a published artifact.
2. "Add TheTVDB as the reconciliation anchor", the review's proposed *real* fix for its item 2,
   does not work for this class of show. Reconciliation would have to derive from TMDB runtimes
   and air dates, or from the show's DVD episode group. That is out of scope here and should not
   be re-proposed on the TVDB rationale.

Recorded here so the falsified inference does not get picked back up from the review doc.

## Scope

**In scope**

- A per-season numbering marker in the published manifest, emitted by both builder scripts.
- One shared derivation helper so the four call sites cannot drift.
- Backend preference for the recorded marker over #673's size heuristic.
- Validator and audit reporting of divergent seasons.
- Tests pinning the degrade-safe path for an unknown cache format version.

**Out of scope**

- Reconciling the two namespaces (mapping half-hour *k* to its canonical segments). That is the
  review's item 2 "real version" and is a much larger piece of work.
- Re-harvesting or republishing the cache. Publishing runs from a nightly timer on a separate
  host; this branch only changes what the produced artifact carries.
- Anything in the ordering projection, the conjoined-hint gate, or the cross-season retry. Those
  are #673's and inherit this change through `numbering_schemes_agree` alone.
- A `"tvdb"` scheme value, per the finding above.

## Design

### 1. Scheme vocabulary

Three values, each one something the builder can actually observe:

| Value | Condition | Consumer reads it as |
|---|---|---|
| `tmdb_aired` | harvested reference count equals the canonical TMDB roster count for that season | codes are canonical coordinates; dereferencing is safe |
| `divergent` | the two counts differ | codes are in an unnamed scheme; do not dereference |
| `unknown` | roster size unobtainable | no information; fall back to the runtime heuristic |

`unknown` arises from `--offline`, a missing TMDB key, an unresolved `tmdb_id`, or a TMDB failure.
It is deliberately distinct from `divergent` so an offline-packed cache is not mistaken for a
broken one, and so a transient TMDB outage during a nightly build cannot mark a healthy season
as unverified.

The marker is **per season, not per show**: a show can be `tmdb_aired` in one season and
`divergent` in another.

### 2. Artifact shape

Additive. Each show entry in `manifest.json` gains one optional key:

```json
"4229": {
  "tmdb_id": 4229,
  "name": "Dexter's Laboratory",
  "seasons": [1, 2, 3, 4],
  "episode_counts": {"1": 13, "2": 40, "3": 13, "4": 13},
  "season_numbering": {
    "1": {"scheme": "divergent", "roster_size": 38},
    "2": {"scheme": "divergent", "roster_size": 108},
    "3": {"scheme": "divergent", "roster_size": 36},
    "4": {"scheme": "divergent", "roster_size": 38}
  }
}
```

The reference count is not duplicated: it is already `episode_counts[season]`. `roster_size` is
omitted from an entry whose scheme is `unknown`.

Season keys are strings, matching `episode_counts`, because JSON has no integer keys and the
existing entry already made that choice.

### 3. `CACHE_FORMAT_VERSION` stays `"3"`

Per-season on-disk index files (`S01.index.json`) remain bare JSON lists of episode codes. Only
`manifest.json` changes, and only by gaining a key. Nothing about the existing shape moves, so
the change is backwards compatible in both directions:

- An **old backend** reading a **new pack** ignores `season_numbering` and keeps loading the
  cache exactly as before. It retains #673's heuristic.
- A **new backend** reading an **old pack** finds no `season_numbering`, and falls through to the
  heuristic.

A bump would be semantically cleaner but is the wrong trade here. `load_precomputed_manifest`
(`episode_identification.py`) and `_ensure_precomputed_cache_inner`
(`precomputed_cache_service.py`) both hard-compare the manifest's `cache_format_version` against
the code constant and refuse on mismatch. A bump therefore means every already-shipped 0.36.x
backend stops using the precomputed cache entirely, and falls back to scraping, the moment the
nightly publishes, until its user updates. Recording a marker does not justify that.

Because the refusal path will not be exercised in production, it gets pinned by test instead.
See section 7.

### 4. Shared derivation: `app/matcher/numbering_scheme.py`

A new module, small and stateless, holding the three constants and:

```python
def derive_numbering_scheme(reference_count: int | None, roster_size: int | None) -> str
```

returning one of the three values. Both builder scripts and the backend import it, so the
definition of "these schemes agree" exists once. This follows the pattern CLAUDE.md already
applies to `episode_codes.py` and `disc_source.py`: when several sites must answer the same
question, the answer gets a module rather than a copy.

Rules:

- either count missing, non-integer, or non-positive -> `unknown`
- counts equal -> `tmdb_aired`
- otherwise -> `divergent`

### 5. Builder, validator, audit

**`backend/scripts/pack_subtitle_cache.py`** (the safe publish path). In the per-season loop that
currently sets `episode_counts[str(season)] = len(codes)`, resolve the roster with
`fetch_season_details(str(tmdb_id), season)` and derive the scheme. That call is backed by the
persistent TMDB cache (`tmdb_persistent_cache`, TTL_SEASON), so it is one cached lookup per
season and no new API surface. When `--offline` is set or `tmdb_id` is `None`, skip the call
entirely and emit `unknown`.

**`backend/scripts/build_subtitle_cache.py`**. Same emission in its manifest-assembly loop. It
already calls `fetch_season_details` during harvest, so the roster count is available.

**`backend/scripts/validate_subtitle_cache.py`**. Divergence is legitimate data, not corruption:
a segment-format show's corpus *is* numbered differently, and that is exactly what the marker
exists to say. The validator therefore **warns and summarizes**, and never fails on it. A failing
validator would block every future publish of every segment-format show.

It does fail on genuine inconsistency: a `season_numbering` entry for a season not in `seasons`,
a scheme value outside the three, or a `tmdb_aired` entry whose `roster_size` contradicts
`episode_counts`.

**`backend/scripts/audit_subtitle_cache.py`**. Lists divergent seasons so the corpus problem is
visible from an audit run rather than only from a user's diagnostic bundle.

### 6. Backend consumption

Three small changes on top of #673.

**`EpisodeMatcher._load_precomputed_season`** already resolves `show_entry` from the manifest. It
records that season's `{scheme, roster_size}` on the matcher instance when the load succeeds, and
clears it on every path that returns `None` so a scraped season cannot inherit a previous
season's marker.

**Result assembly** (`episode_identification.py`, the dict that #673 extended with
`reference_count`) stamps `numbering_scheme` and `pack_roster_size` beside it. A scraped season
stamps neither.

**`numbering_schemes_agree(details)`** gains a precedence step ahead of its existing comparison:

```
scheme = details.get("numbering_scheme")
  "tmdb_aired" -> True
  "divergent"  -> False
  otherwise    -> fall through to the existing reference_count == roster_size heuristic
```

The heuristic is untouched and remains the answer for packs that predate the marker, for
`unknown` seasons, and for scraped-SRT seasons that never came from a pack at all. When it too
has nothing to work with it still returns `None`, and callers keep their prior behaviour.

Both of #673's consumers, the ordering-projection skip in `finalization_coordinator.py` and the
conjoined-hint gate in `matching_coordinator.py`, inherit this without modification, because they
already route through this one function.

One consequence worth stating: `_augment_with_downloaded_srts` grafts scraped SRTs onto a
precomputed season, which inflates `reference_count` and can push an actually-agreeing season
into a false `divergent` verdict under the heuristic. The recorded marker describes the pack and
is immune to that, so preferring it is a correctness improvement, not only a confidence one.

### 7. Testing

TDD throughout: each behaviour below gets its failing test first.

**Derivation** (`tests/unit/test_numbering_scheme.py`, new)
- equal counts -> `tmdb_aired`; unequal -> `divergent`
- `None`, zero, negative, and non-integer inputs on either side -> `unknown`

**Manifest emission** (`tests/unit/`, extending the existing pack-script coverage)
- a packed show emits `season_numbering` keyed by the same string season keys as `episode_counts`
- `--offline` emits `scheme: "unknown"` with no `roster_size`, and makes no TMDB call
- a TMDB roster lookup returning 0 (the no-key / transient-failure contract of
  `fetch_season_details`) yields `unknown`, not `divergent`
- a Dexter-shaped fixture (13 references, 38 roster) yields `divergent`; an agreeing show yields
  `tmdb_aired`

**Precedence** (`tests/unit/test_matching_coordinator.py`, extending #673's tests)
- `numbering_scheme: "tmdb_aired"` returns `True` even when the counts disagree, which is the
  augmented-SRT case
- `numbering_scheme: "divergent"` returns `False` even when the counts agree
- `unknown` and absent both fall through to the heuristic, whose existing tests must still pass
- the fall-through still returns `None` when the counts are unusable

**Matcher stamping** (`tests/unit/`, against the precomputed load path)
- a precomputed season carrying a marker stamps `numbering_scheme` into the result
- a scraped season stamps nothing
- a matcher that loads a marked season and then a scraped one does not leak the first marker onto
  the second

**Validator**
- a divergent season warns and exits 0
- a malformed `season_numbering` entry fails

**Degrade-safe path** (section 3's substitute for a version bump)
- `load_precomputed_manifest` refuses a manifest whose `cache_format_version` is unknown and
  returns `None`
- `_ensure_precomputed_cache_inner` skips a remote manifest whose `cache_format_version` is
  unknown, and does not download the tarball
- a manifest at version `"3"` carrying an unrecognised extra key still loads, which is the
  forwards-compatibility guarantee the no-bump decision rests on

### 8. Constraints observed

- Nothing reads or writes `C:\Users\jonat\.engram\cache` beyond a directory listing. Fixtures are
  synthetic; the live manifest used during design was fetched from the public release URL into a
  scratchpad.
- `uv run pytest` and `uv run ruff check .` from `backend/`.
- `[Unreleased]` CHANGELOG entry in user-facing prose.
- No em dashes or en dashes.

## Risks

**The marker can be wrong in the `tmdb_aired` direction.** Equal counts are a proxy: a season
could coincidentally have the same number of half-hours as canonical segments and still be
numbered differently. This is the same proxy #673 already relies on, now computed once at build
time against a known roster rather than per match against whatever the duration pre-filter
happened to fetch. It is strictly better evidence, not proof.

**Roster size drifts.** TMDB rosters change as a show airs. A marker is a statement about the
roster at build time, and the nightly republish refreshes it. A stale `tmdb_aired` on a season
that has since gained episodes would be wrong, but in the safe direction only when the counts
stay equal, which is why `roster_size` is recorded: a consumer can compare it to a live roster
without a TMDB round trip if it ever needs to.

**Per-season TMDB calls lengthen the pack run.** Mitigated by the persistent cache; a season
whose details were fetched during harvest costs nothing. `--offline` skips them entirely.
