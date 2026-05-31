# TMDB-ID Show-Identity Spine + Same-Name Collision Review

- **Date:** 2026-05-30
- **Status:** Design — approved approach, pending spec review
- **Scope:** Item 1 of a 3-item decomposition (this doc). Items 2 and 3 sketched under "Sequencing".

## Problem

Episode matching can silently abstain when a disc's show name maps to the **wrong TMDB
show**. Discovered with a "Frasier Season 1" disc that is actually the **Frasier (2023)
revival** (TMDB `195241`), not the original **Frasier (1993)** (TMDB `3452`). The subtitle
corpus contains only the original, so every ASR chunk scores at the noise floor
(~0.03–0.05 cosine) against all 263 original episodes → no match → generic "needs review",
with no explanation.

The matcher is behaving correctly (it should abstain — there is no right answer in the
reference set). The defect is **upstream**: identity is carried as a **show-name string**,
and the `tmdb_id` that *would* disambiguate is computed, stored on the job, and then
**never used downstream**. Subtitle download (`download_subtitles(show_name, season)`) and
matching (`match_single_file(series_name=detected_title, …)`) both key on the name only.
Two same-named shows are indistinguishable by name, so neither automatic nor manual
re-identification can currently fix it.

### How big is the problem? (empirical, measured against the real 181-show corpus)

| Bucket | Count | Notes |
|---|---:|---|
| Any same-name TMDB twin | 54 / 181 (29%) | ≥2 distinct TMDB TV shows normalize to the same name |
| **Tier A** — both variants substantial (twin popularity ≥ 10) | **~8–9** | Real reboots / international remakes, both with physical media |
| Tier B — real-but-smaller twin (pop 3–10) | 15 | Includes the reported **Frasier** (75.6 vs 5.7) |
| Tier C — twin is noise (pop < 3) | 31 | Coincidental / foreign one-offs; effectively never owned or picked |
| Today's heuristic **actively** mispicks (top-popularity ≠ corpus id) | **1** | `ONE PIECE` (corpus = 2023 live-action; popularity favors 1999 anime) |

Tier A: Avatar: The Last Airbender (2005 vs 2024), Battlestar Galactica (2004/1978/2003),
Charmed (1998 vs 2018), Doctor Who (2005 vs 1963), One Piece (1999 anime vs 2023
live-action), Rebelde, Shameless (US 2011 vs UK 2004), The Flash (2014 vs 1990), plus The
Office (UK/US — scan errored, known collision). Scaled to the full ~599-show curated list,
expect ~25–30 Tier A and ~50 Tier B.

**Key consequence for the design.** The bug splits into two populations needing *different*
triggers:

- **Comparable-popularity twins (Tier A):** genuinely ambiguous *at identify time*. A
  materiality-gated collision check can catch these and route to review. This is item 1's
  collision-detection win (~8–9 shows).
- **Dominant-twin mispicks (Frasier, most of Tier B):** popularity *confidently* picks the
  wrong-for-this-disc show and the disc carries no year — **there is no identify-time
  signal**. A "flag any 2nd same-name entry" rule would instead nag on 29% of all shows.
  These are only catchable **downstream**, when matching fails at the noise floor →
  **item 3 is the real trigger** for the reported Frasier case.

So the reported failure is fixed by the chain: **item 3 explains it → user re-identifies →
item 1's spine makes the `tmdb_id` pick drive download + matching → corpus then contains the
right subtitles** (on-demand download by `tmdb_id`; on-disk re-key hardened by item 2).

## Goals

1. Make `tmdb_id` a **first-class identity** that flows through subtitle download → matcher
   reference selection → corpus lookup, alongside (not replacing) the human-facing show name.
2. Detect **genuinely-ambiguous same-name collisions** (Tier A) at identify time and route
   them into the **existing** re-identify review workflow — no new UI.
3. Add a **corpus guard**: never match a disc against a precomputed corpus whose `tmdb_id`
   contradicts the job's resolved `tmdb_id` (prevents wrong-corpus false matches; produces a
   clean "no coverage" outcome that item 3 will explain).
4. Make manual **re-identify** with a `tmdb_id` actually re-key subtitle download + matching,
   so a human pick distinguishes 1993 from 2023.

## Non-goals (explicit scope boundaries)

- **No cache re-key / migration.** The on-disk corpus layout
  (`precomputed/<sanitize_filename(name)>/…`, `data/<sanitize_filename(name)>/…`) stays
  name-keyed. Making two same-named shows coexist on disk by keying paths on `tmdb_id`
  (plus rebuilding/migrating the existing cache) is **item 2**.
- **No new "wrong show / content doesn't resemble reference" UX signal.** That is **item 3**.
- **No automatic year-based auto-pick.** No disc-year signal exists today (volume labels like
  "Frasier Season 1" carry none; MakeMKV metadata has no year field), so it would be
  speculative. Deferred.

## Approach

**Approach A — `tmdb_id` as an optional authoritative companion to the show name.** Thread an
optional `tmdb_id` parameter alongside the existing name through the download and matching
call chains. Where `tmdb_id` is present it is authoritative (OpenSubtitles `parent_tmdb_id`,
`fetch_season_details(tmdb_id)`, corpus guard); where absent (legacy paths, unresolved id)
behavior falls back to today's name-based logic. Backward-compatible, no cache migration.

Rejected: **B** — a `ShowIdentity{tmdb_id,name,year}` value object passed everywhere (cleaner
long-term but large signature churn, overlaps item 2); **C** — resolve name→id only at the
two entry points (smallest, but keeps name-as-identity and can't power the corpus guard).

## Detailed Design

### 1. Identity model

`tmdb_id` becomes an optional parameter threaded through the chain. The show name is retained
for human display and as the fallback search key. Comparison against the manifest normalizes
both to `str` (the manifest stores `tmdb_id` as a string, e.g. `"3452"`; the job stores it as
`int`).

### 2. Collision detection + materiality gate (`backend/app/core/tmdb_classifier.py`)

- Extend the TMDB TV search so `classify_from_tmdb` can see **all same-name candidates**, not
  just the single best. A candidate is "same-name" when its normalized name (or
  `original_name`) equals the normalized query.
- Extend `TmdbSignal` with `ambiguous_identity: bool = False` and
  `candidates: list[dict] | None = None` (each `{tmdb_id, name, year, popularity}`).
- **Materiality gate** — flag `ambiguous_identity=True` only when **both** hold for the top
  two same-name candidates:
  - `second.popularity >= AMBIGUOUS_POPULARITY_FLOOR` (default **10.0**)
  - `first.popularity / second.popularity <= AMBIGUOUS_POPULARITY_RATIO` (default **4.0**)

  Thresholds are module-level tunable constants (documented). Behaviour on the real corpus:

  | Show | top vs 2nd popularity | ratio | flagged? |
  |---|---|---:|:--:|
  | Avatar: The Last Airbender | 26.9 / 23.0 | 1.17 | ✅ |
  | Doctor Who | 109.9 / 62.7 | 1.75 | ✅ |
  | One Piece | 60.0 / 38.3 | 1.57 | ✅ (fixes the 1 active mispick) |
  | Battlestar Galactica | 38.7 / 13.0 | 2.98 | ✅ |
  | Rebelde | 30.4 / 15.2 | 2.00 | ✅ |
  | Charmed | 84.0 / 15.2 | 5.5 | ❌ (dominant → trust popularity; item 3 covers mispick) |
  | Shameless | 153.5 / 23.4 | 6.6 | ❌ |
  | **Frasier** | 75.6 / 5.7 | 13.3 | ❌ (by design → item 3) |
  | Tier C long tail | * / <3 | — | ❌ (floor excludes) |

- When ambiguous, `classify_from_tmdb` still reports the best tentative pick (so non-TV logic
  is unaffected) but with the flag + candidate list set.

### 3. Review routing (reuse existing workflow — `identification_coordinator.py`)

In `_run_classification`, when the TMDB signal is `ambiguous_identity`:

- **Do not** set `job.tmdb_id` (leave it `None` so nothing downstream commits to a guess).
- Set `job.review_reason` to a human-readable message naming the candidates, e.g.
  *"Multiple shows match \"Frasier\" — the 1993 original (#3452) and a 2023 series (#195241).
  Pick the correct one."* (built from `signal.candidates`).
- `await self._state_machine.transition_to_review(job, session, reason=…)`.

No frontend work: the dashboard already shows the **Re-Identify** affordance when
`needsReview && title`, and `ReIdentifyModal` already provides a live `GET /api/tmdb/search`
candidate picker (name + year + poster + type) that returns `tmdb_id`. The user picks; the
existing re-identify round trip (§7) carries the chosen id.

### 4. Spine — subtitle download path

Thread `tmdb_id` through:

- `identification_coordinator._start_subtitle_download(job_id, title, season)` → add
  `tmdb_id` param; pass `job.tmdb_id`.
- `MatchingCoordinator.download_subtitles(show_name, season, tmdb_id=None)` → pass through.
- `backend/app/matcher/testing_service.download_subtitles(show_name, season, tmdb_id=None)`:
  when `tmdb_id` is provided, **bypass `fetch_show_id(name)`** (the collision point — it
  returns the first name search result) and use `tmdb_id` directly for
  `fetch_season_details(tmdb_id)` and provider search. `OpenSubtitlesProvider.get_subtitles`
  already accepts `tmdb_id` and searches by `parent_tmdb_id`; ensure the id is forwarded.

When `tmdb_id` is `None`, the path is unchanged (today's name-based `fetch_show_id`).

### 5. Spine — matching path

- `MatchingCoordinator._match_single_file_inner` → call
  `episode_curator.match_single_file(file_path, series_name=detected_title,
  season=detected_season, tmdb_id=job.tmdb_id, …)`.
- `curator.match_single_file(…, tmdb_id=None)` → forward into the matcher / `EpisodeMatcher`
  so the identifier carries `tmdb_id` alongside `show_name`.

### 6. Corpus guard (`backend/app/matcher/episode_identification.py`)

The precomputed manifest entry already records `tmdb_id` per show
(`manifest["shows"]["Frasier"]["tmdb_id"] == "3452"`). Add an `expected_tmdb_id` to the
identifier and apply it in `precomputed_covers_season` / `_load_precomputed_season`:

- If `expected_tmdb_id` is set **and** the manifest entry has a `tmdb_id` **and**
  `str(entry.tmdb_id) != str(expected_tmdb_id)` → treat as **no precomputed coverage**
  (return `False` / `None`), and log a warning that the corpus is for a different show.
- If `expected_tmdb_id` is `None`, or the manifest entry lacks a `tmdb_id` (older builds) →
  **skip the guard** (today's name-only behavior). Backward-compatible; the only behavioral
  change is refusing a corpus whose id positively contradicts the job's id.

Effect: a revival disc never matches against the original's corpus. With no precomputed
coverage, the matcher falls back to the live reference path (`data/<name>/`), where the
`tmdb_id`-keyed download (§4) has placed the *correct* subtitles.

### 7. Re-identify round trip (already wired; make it id-driven)

`POST /api/jobs/{job_id}/re-identify` (`ReIdentifyRequest` already has `tmdb_id`) →
`identification_coordinator.re_identify` already restarts subtitle download and re-matches.
The only change: the restarted download (§4) and re-match (§5) now pass the **chosen**
`tmdb_id`, so re-identifying "Frasier" to `195241` fetches the 2023 subtitles and the guard
(§6) skips the 1993 precomputed corpus. The Frasier disc is post-rip (files already staged),
so `re_identify` routes to `_rerun_matching`.

## Data Flow — Frasier walkthrough (end-to-end, with item 1 only)

1. Disc "Frasier Season 1" (2023 revival, no year). Classifier: 1993 dominates (ratio 13.3,
   **not** flagged ambiguous) → `job.tmdb_id = 3452`.
2. Matching: precomputed "Frasier" entry `tmdb_id == "3452"` == job id → guard **passes** →
   match against 1993 corpus → all chunks at noise floor → **no match → needs review**.
   *(Item 3 will later attach a "content doesn't resemble reference show" reason here.)*
3. User opens Re-Identify, searches "Frasier", sees both entries with years, picks **2023
   (#195241)** → `re-identify {tmdb_id: 195241}`.
4. `job.tmdb_id = 195241`. Subtitle download keyed by `195241` → fetches 2023 subtitles into
   `data/Frasier/`. Re-match: precomputed "Frasier" `tmdb_id "3452" != 195241` → guard skips
   precomputed → live path reads the 2023 subtitles → **matches**. ✅

A Tier-A example (e.g. Battlestar Galactica) diverges at step 1: classifier flags
`ambiguous_identity` → review *before* any wrong-corpus match, user picks at the outset.

## Edge Cases & Error Handling

- **Manifest entry missing `tmdb_id`** (pre-v2 builds): guard skipped, name-only behavior,
  warning logged.
- **`job.tmdb_id` unresolved (`None`)**: entire chain falls back to today's name-based logic.
  No regression for the 127/181 shows with no collision.
- **String/int `tmdb_id` mismatch**: always compare as `str`.
- **On-disk dir collision (shared `data/Frasier/` or `precomputed/Frasier/`)**: acknowledged
  residual. The guard prevents *false matches*; correct subtitles land via id-keyed download.
  Full isolation (paths keyed by `tmdb_id`) is **item 2**. Until then, a re-identified twin
  works as long as the shared name-dir isn't polluted by the other twin's files.
- **Ambiguous flag false-positives**: thresholds are tunable; default ratio 4.0 / floor 10.0
  flags ~5–6 Tier-A shows and stays silent on the 29% tail.

## Testing

- **Unit (`tmdb_classifier`):** mock TMDB returning two "Frasier"-named shows — assert the
  gate flags ambiguous only when floor+ratio satisfied; assert Frasier-with-dominant-original
  is **not** flagged; assert candidate list shape.
- **Unit (corpus guard):** manifest entry `tmdb_id "3452"`; identifier `expected_tmdb_id
  195241` → `precomputed_covers_season` returns `False`; `3452` → `True`; `None` → `True`
  (skip).
- **Unit (download bypass):** `download_subtitles(..., tmdb_id=195241)` does **not** call
  `fetch_show_id`; uses the id for season details + `parent_tmdb_id` search.
- **Integration:** ambiguous-collision job → lands in `REVIEW_NEEDED` with a `review_reason`
  naming candidates; re-identify with `tmdb_id` re-keys download + match (assert the id is
  threaded into `match_single_file`). *(Per project guidance, integration tests run against
  the real app DB — set up job state directly in the DB; do not let it organize real files.)*

## Files Touched (item 1)

| File | Change |
|---|---|
| `backend/app/core/tmdb_classifier.py` | same-name candidate collection; `TmdbSignal.ambiguous_identity` + `candidates`; materiality gate + tunable constants |
| `backend/app/services/identification_coordinator.py` | route ambiguous → review (reason + no id); thread `tmdb_id` into `_start_subtitle_download` and re-identify restart |
| `backend/app/services/matching_coordinator.py` | `tmdb_id` params on `download_subtitles` + `_match_single_file_inner` → `match_single_file` |
| `backend/app/matcher/testing_service.py` | `download_subtitles(..., tmdb_id=None)`; bypass `fetch_show_id` when id known |
| `backend/app/matcher/subtitle_provider.py` | ensure `tmdb_id` forwarded to `OpenSubtitlesProvider` search |
| `backend/app/core/curator.py` | `match_single_file(..., tmdb_id=None)` forward |
| `backend/app/matcher/episode_identification.py` | `expected_tmdb_id` on identifier; guard in `precomputed_covers_season` / `_load_precomputed_season` |
| `backend/tests/unit/…`, `backend/tests/integration/…` | tests above |

No DB migration, no cache rebuild, **no frontend changes**.

## Sequencing — how items 2 and 3 slot in

- **Item 1 (this doc):** identity spine + Tier-A collision review + corpus guard. Makes the
  re-identify pick functional and prevents wrong-corpus false matches.
- **Item 2 (next spec):** re-key the corpus by `tmdb_id` — `build_subtitle_cache.py` writes
  `precomputed/<tmdb_id>/…` and `data/<tmdb_id>/…`, manifest keyed by id; migrate the
  existing on-disk cache; matcher lookup by id. Removes the residual shared-dir caveat and
  lets same-named twins coexist. Rides on item 1's `tmdb_id` plumbing.
- **Item 3 (next spec):** the load-bearing fix for Frasier-class — in
  `episode_identification.identify_episode`'s no-match path, detect "all sampled chunks at the
  noise floor with near-zero top1/top2 margin" and emit a distinct reason
  (`no_match_reason = "wrong_show_or_season"`); carry it on `MatchResult.match_details`
  → `DiscTitle.match_details` → review UI banner. Self-explains the failure so users know to
  re-identify. (NB: confirm the current shape of the chunk-vote gate / `select_chunk_vote` in
  `episode_identification.py` when speccing — the no-match return already carries per-episode
  best-cosine data needed to compute the signal.)
