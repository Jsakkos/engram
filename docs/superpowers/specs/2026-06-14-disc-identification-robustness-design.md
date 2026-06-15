# Disc Identification Robustness — Design

- **Date:** 2026-06-14
- **Status:** Approved (design); pending spec review
- **Branch:** `fix/disc-identification` (worktree off `main` @ v0.21.0)
- **Author:** Investigation + design from DS9 S1D1 rip (Job 153)

## Context

A real rip — *Star Trek: Deep Space Nine* Season 1 Disc 1, volume label `DS9S1D1`,
recorded as **Job 153** on 2026-06-14 — completed with three anomalies. All three
originate in the disc-analysis layer (`backend/app/core/analyst.py`), confirmed
against the runtime log (`~/.engram/engram.log`) and job DB (`~/.engram/engram.db`).

The disc had 3 MakeMKV titles:

| idx | duration | identity | what happened |
|----:|---------:|----------|---------------|
| 0 | 5429 s (90.5 min) — "Emissary" feature-length pilot | flagged Play-All → `is_extra=1` | **dropped, never organized** |
| 1 | 2718 s (45.3 min) | "Past Prologue" | matched S01E02, organized |
| 2 | 2715 s (45.25 min) | "A Man Alone" | matched S01E04, organized |

TMDB correctly identified the show (`id=580`, "Star Trek: Deep Space Nine",
`ambiguous_identity=False`), yet the job finished as `state=COMPLETED`,
`detected_title=DS9S1D1`, `final_path=X:\media\series\DS9S1D1 (1993) [tmdbid-580]\…`.

The structural trap: `5429 ≈ 2718 + 2715 = 5433`. The pilot's runtime coincidentally
equals the sum of the two 45-minute episodes — the exact signature of a Play-All
concatenation.

### Root causes (verified)

1. **Name kept as `DS9S1D1` despite correct TMDB match.** `analyst.py` only adopts
   the TMDB name when an on-disc string "corroborates" it via `_names_are_similar()`
   (`analyst.py:80`). That helper has two acceptance paths — word-token Jaccard ≥ 0.5,
   and whitespace/punct-insensitive equality. `DS9` shares no tokens with
   "Star Trek: Deep Space Nine" and is not equal once compacted, so corroboration
   fails (`analyst.py:330-342`) and the disc name is kept. **False negative:** TMDB was
   right; the conservative gate rejected the right answer. `tmdb_id`/`tmdb_name` are
   still propagated, so the organizer mixes the disc name with the TMDB id/year.

2. **Pilot dropped as Play-All.** `_detect_tv_show` finds only 2 TV-range titles
   (< the cluster minimum of 3), so `_detect_play_all_fallback` (`analyst.py:734`)
   runs: it flags any feature-length title whose duration is within ±20% of the *sum*
   of TV-range titles. `5429/5433 = 0.999` → idx 0 flagged. Conflict-resolution rule 2
   (`analyst.py:373`) then treats the "movie" as the Play-All and drops it. Play-All
   indices become `is_extra=True` (`identification_coordinator.py:375`), and finalization
   excludes extras from organizing. **The detector is runtime-blind:** it cannot tell a
   feature-length pilot from a Play-All when the pilot ≈ the sum of the other episodes.

3. **No review despite acknowledged ambiguity.** The corroboration-failure branch
   (`analyst.py:337-342`) only logs a warning; it never sets `needs_review` or
   `review_reason`. `_apply_tmdb_signal` (`analyst.py:478`) escalates to review for
   same-name TMDB collisions and for heuristic/TMDB content-type disagreement — but
   **not** for name/identity-corroboration failure. The job auto-completed under the
   wrong name.

## Goals

- A disc whose label is an abbreviation/initialism of the correct TMDB show
  (e.g. `DS9` ↔ "Deep Space Nine") resolves to the **TMDB name** automatically.
- A legitimate feature-length episode (double-length pilot) is **not** discarded as a
  Play-All / extra.
- When identity genuinely cannot be confirmed, the job goes to **review** instead of
  silently completing under a guessed name.
- All three are reproduced as automated tests, plus an end-to-end check of the DS9
  scenario before merge.

## Non-goals (deferred)

- **Double-length-pilot episode numbering** (the E02/E04 result). Episode numbers come
  from content-based ASR matching, not ordering logic; TMDB "episode groups" are wired
  for output re-ordering only (`episode_ordering.py`), not matching. The E02/E04 result
  stems from the Play-All drop (fixed here) **plus** a missing `S01E01` reference in the
  precomputed subtitle cache **plus** TMDB representing Emissary as a single ~90-min
  episode. A true fix is a deeper matcher change — tracked as follow-up, out of scope.
- Re-ordering / DVD-order projection changes.

## Design posture

**Balanced — confident guess with a review net.** Auto-resolve when we have a
defensible signal (abbreviation match, runtime-confirmed episode); route genuine
ambiguity to review. The review net (Fix 3) is what makes it safe to loosen the
matcher (Fix 1) and to keep ambiguous-length titles (Fix 2): a wrong guess surfaces
to the user instead of shipping silently.

All three changes live in `backend/app/core/analyst.py` plus a thin caller change in
`backend/app/services/identification_coordinator.py`. The Analyst stays a pure,
synchronous function over its inputs — new data (expected runtimes) is **passed in**,
not fetched inside it, matching the existing `tmdb_signal` injection pattern.

---

## Fix 1 — Abbreviation-aware name corroboration

**Where:** `_names_are_similar()` (`analyst.py:80`) and its callers in the corroboration
block (`analyst.py:330-334`). Add a dedicated helper `_abbreviation_matches(candidate, tmdb_name)`
and wire it as a third acceptance path.

**Algorithm (explicit):**

1. **Disc candidate** — the compacted on-disc token: take the volume-label name and the
   DINFO disc title, strip season/disc suffixes (`S1D1`, `SEASON 1`, etc.) and all
   non-alphanumerics, uppercase. `DS9S1D1` → `DS9`.
2. **TMDB acronym candidates** — from `tmdb_name`:
   - Consider both the full name and the post-colon segment as variants
     ("Star Trek: Deep Space Nine" → also "Deep Space Nine").
   - For each variant, drop stopwords (`the, of, and, a, an`), then build the initialism
     from remaining words' first letters: "Deep Space Nine" → `DSN`.
   - Map whole number-words to digits *before* initialing (`one…nine`→`1…9`, `ten`→`10`),
     producing a second initialism: "Deep Space **Nine**" → `DS9`.
   - Emit both letter and digit-mapped initialisms for every variant.
3. **Match** if the disc candidate equals any emitted acronym (case-insensitive,
   alphanumeric-only). `DS9` == `DS9` → corroborated.
4. **False-positive guards:**
   - Only apply when the disc candidate is "abbreviation-shaped": length ≤ 5 **and**
     (contains a digit **or** has no vowels among letters). Avoids loosely matching a
     normal short title.
   - Require the source acronym to derive from ≥ 2 significant words (no 1-letter matches).

**Alternatives rejected:** edit-distance/fuzzy (too loose → false positives);
consonant-skeleton only (misses the `9`-for-Nine digit).

**Test obligations:** `DS9` ↔ "Star Trek: Deep Space Nine" matches; negative cases —
`DS9` must **not** match "Star Trek: The Next Generation" (`TNG`), and a normal short
label (e.g. `HOUSE`) must not spuriously match an unrelated multi-word title.

---

## Fix 2 — Runtime-aware Play-All / Extras

**Where:** `_detect_play_all` (`analyst.py:691`) and `_detect_play_all_fallback`
(`analyst.py:734`); `analyze()` signature (`analyst.py:267`); caller at
`identification_coordinator.py:1647`.

**Data flow change:** add an optional `expected_episode_runtimes: list[int] | None`
parameter to `analyze()` (minutes, TMDB order, may contain `0` for unknown). The caller
resolves `(tmdb_id, season)` — `tmdb_id` from the TMDB signal, `season` from the
volume-label parse / disc-name fallback (the static `_parse_volume_label`, or the
already-computed `disc_name_season`) — and fetches
runtimes via the existing `fetch_season_episode_runtimes()` (`tmdb_client.py:765`,
best-effort; note: this function is not memoized today, unlike its sibling
`fetch_season_details` — a small follow-up). The sync TMDB call is invoked through the
existing async-safe path (thread executor) so it never blocks the event loop. On any miss (no id/season, empty
list, fetch error) the parameter is `None` and behavior falls back to today's heuristic.

**Rule (explicit):** before appending a feature-length title `t` to `play_all` in either
detector:

- If `expected_episode_runtimes` is available and **`t.duration_seconds` matches any single
  expected episode runtime** within tolerance → `t` is a legitimate (possibly
  double-length) episode → **do not** flag it; continue.
- Otherwise apply the existing sum-ratio check (`0.8 ≤ dur / episode_total ≤ 1.2`).

**Tolerance:** compare minutes; accept `abs(actual_min − expected_min) ≤ max(5, 0.15 × expected_min)`
to absorb TMDB rounding and rip/runtime drift.

**Edge case (documented, tested):** if TMDB lists a two-parter as two ~45-min episodes
while the disc carries it as one ~90-min title, the single-runtime check won't match.
Secondary guard: also treat `t` as legitimate if its duration matches the **sum of two
consecutive** expected runtimes within tolerance. (For DS9 id=580, Emissary is a single
~90-min episode, so the primary single-runtime check suffices; the sum-of-two guard
covers the general two-parter case.)

**Classification corollary:** keeping the pilot out of the Play-All bucket is not
sufficient on its own — with only two short episodes (below the 3-title TV cluster
minimum), the lone feature-length title would make `_detect_movie` classify the disc as
a MOVIE. So when a feature-length title matches an expected episode runtime on a
TV-labeled disc, the Analyst suppresses the movie result and classifies as TV, with that
title kept as a normal episode (this is what "treated as a real episode" requires).

**Extras:** because Play-All indices become `is_extra` (`identification_coordinator.py:375`),
this fix also stops legitimate long episodes from being mislabeled extras — no separate
change needed.

**Alternatives rejected:** structural-only heuristics (chapter counts, require ≥3
episodes) — the DS9 coincidence defeats them; TMDB runtimes are the robust signal.

---

## Fix 3 — Escalate uncorroborated identity to review

**Where:** corroboration outcome in `analyze()` (`analyst.py:330-342`) feeding the result
built via `_tv_result` / `_apply_tmdb_signal` (`analyst.py:460,478`).

**Change:** when the TMDB name fails corroboration **even after** Fix 1's abbreviation
path, mark the analysis `needs_review = True` with a candidate-confirming `review_reason`,
e.g.: *"Couldn't confirm disc `DS9S1D1` is `Star Trek: Deep Space Nine` (TMDB #580).
Confirm or correct the title."* The TMDB `tmdb_id`/`tmdb_name` remain attached as the
**suggested** identity (balanced posture), reusing the existing identity/candidate review
plumbing (`identification_coordinator` already routes `needs_review` + `review_reason` to
the review UI). The job holds for confirmation instead of organizing under the disc name.

**Interaction with Fix 1:** Fix 3 only fires when Fix 1 cannot corroborate. For DS9, Fix 1
succeeds (`DS9` ↔ "Deep Space Nine") → auto-resolves, **no** review. Fix 3 catches the
residual cases Fix 1 can't verify — closing the asymmetry where content-type ambiguity
already escalates but name ambiguity did not.

---

## Testing strategy

**Unit (`backend/tests/unit/`):**
- `_abbreviation_matches` / `_names_are_similar`: `DS9` ↔ "Star Trek: Deep Space Nine"
  positive; `DS9` vs "The Next Generation" negative; abbreviation-shape guard negatives.
- Runtime-aware Play-All: DS9 fixture (durations `5429/2718/2715`, expected runtimes
  `[~90,45,45,…]`) → idx 0 **not** in `play_all_title_indices`; a genuine Play-All disc
  (real concatenation, no matching single runtime) → still detected. No-runtime fallback
  preserves current behavior.
- Review escalation: uncorroborated name (runtimes/abbrev both fail) → `needs_review=True`
  with candidate `review_reason`; corroborated name → `needs_review` unchanged.

**Pipeline/integration (`backend/tests/pipeline/`, extend `test_play_all_detection.py`):**
- Reproduce Job 153 end-to-end through the Analyst + identification path: TMDB signal
  id=580, 3 titles. Assert: `detected_name == "Star Trek: Deep Space Nine"`,
  title 0 `is_extra == False`, and review behavior matches posture (no review, since
  Fix 1 corroborates).

**E2E before merge (required):**
- `DEBUG` simulation: `POST /api/simulate/insert-disc` with `volume_label=DS9S1D1`,
  `content_type=tv` and the DS9 title layout; verify the job resolves to the TMDB name,
  keeps the pilot, and completes (or reviews) as designed via the dashboard.
- Run the existing Playwright E2E suite (no regressions).
- Final confidence check: a real re-rip of the physical DS9 S1D1 disc.

## Risks & mitigations

- **Abbreviation false positives** (adopting a wrong TMDB name): bounded by the
  abbreviation-shape + ≥2-word guards, and backstopped by Fix 3 (genuine mismatches go to
  review, not silent organize).
- **TMDB runtime data quality** (missing/rounded/odd runtimes): tolerance window + `0`/empty
  → fall back to existing heuristic; never *more* aggressive than today.
- **Added TMDB fetch in the identification path:** executor-wrapped, best-effort; failures are
  non-fatal (degrade to current behavior). `fetch_season_episode_runtimes` is not memoized today
  (unlike `fetch_season_details`) — a noted follow-up; repeated same-season lookups re-hit TMDB
  but are network-safe.
- **Behavior change for existing libraries:** these only affect *new* rips; no migration.

## Out of scope / follow-ups

- Double-length-pilot episode numbering (E01/E03 correctness) — deeper matcher work.
- Missing `S01E01` reference handling in the precomputed subtitle-vector cache.

## Rollout

Single PR on `fix/disc-identification`, squash-merged after unit + pipeline tests pass and
the E2E DS9 reproduction is verified. Changelog entry under `### Fixed`.
