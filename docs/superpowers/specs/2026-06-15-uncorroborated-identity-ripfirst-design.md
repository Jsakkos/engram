# Uncorroborated TV identity should rip-first, not block

**Date:** 2026-06-15
**Status:** Approved (design)
**Area:** `backend/app/core/analyst.py`, `backend/app/services/identification_coordinator.py`

## Problem

A disc whose TMDB identity is found but cannot be textually corroborated against
the on-disc label parks in `REVIEW_NEEDED` *before* ripping, waiting for the user
to confirm the title. This contradicts the walk-away workflow (Phase B, #398),
whose promise is "drop a disc and walk away, one pooled review at the end."

### Live repro

User ripping Star Trek: Deep Space Nine, 2026-06-15, jobs 164–170 (same binary):

| Job | Label | Outcome |
|-----|-------|---------|
| 164 | `DS9S2D3 ok` | parked → `identifying -> review_needed`, user re-identified 15 min later, then ripped |
| 165 | `DS9S2D4` | ripped immediately |
| 166–169 | `DS9S2D5`…`DS9S3D1` | ripped immediately |
| 170 | `DS9S3D2 ok` | parked → blocked the rip until the user manually re-identified |

Only the two `" ok"`-suffixed labels blocked.

## Root cause

Two mechanisms, both introduced by PR #403 (`35b293ac`, *fix(analyst):
abbreviation-aware naming, runtime-aware Play-All, review escalation*), which
landed **after** the walk-away Phase B rip-first gates (#398):

1. **The `" ok"` suffix breaks corroboration.** `_parse_volume_label` strips the
   `S3D2` season/disc tokens but leaves the trailing `ok`, yielding the show name
   `Ds9 Ok`. That collapses to `ds9ok`, which is not equal to the strict
   initialism `ds9` of "Deep Space **Nine**" (`_abbreviation_matches`,
   `nine→9`). Clean labels parse to `Ds9`, which corroborates and is renamed to
   the canonical "Star Trek: Deep Space Nine". Failed corroboration keeps the raw
   label name.

2. **The uncorroborated-identity review reason isn't a rip-first gate.** When
   corroboration fails for a TV disc, `_apply_tmdb_signal` escalates to
   `needs_review=True` with `_uncorroborated_review_reason` ("Couldn't confirm
   disc '…' is '…'. Confirm or correct the title."). The walk-away rip-first set
   is exactly four gates — (A) unreadable-label, (B) TV-without-TMDB, (C)
   same-name-collision, (D) unknown-season — and "every OTHER review path still
   parks before ripping" (`test_identify_rip_first_gates.py` docstring). The #403
   escalation is `_collision=False` (no `ambiguous_identity`, no no-year twin), so
   it falls into the catch-all park branch
   (`identification_coordinator.py` ~578-589).

## Design

### Fix 1 — Route uncorroborated-but-known TV identity through Gate C (rip-first)

The fix's safety net. Uses a typed flag rather than matching the reason string,
mirroring the existing `is_ambiguous_movie` flag on `DiscAnalysisResult`.

- **`analyst.py`** — add `identity_unconfirmed: bool = False` to
  `DiscAnalysisResult`. Set it `True` at the two sites that assign
  `_uncorroborated_review_reason`:
  - the TMDB-only path (`analyze`, ~576-577)
  - `_apply_tmdb_signal` (~724-728)

  Both sites are already TV-scoped and carry a `tmdb_id`, so the flag implies
  "known content type + best-guess identity."

- **`identification_coordinator.py`** — extend the Gate C branch from
  `if _collision:` to `if _collision or analysis.identity_unconfirmed:`, routing
  to `_rip_first_with_prompt(kind="reidentify", reason=analysis.review_reason)`.

  Keying off `identity_unconfirmed` *separately* from `_collision` is deliberate:
  collisions skip subtitle prefetch, but an uncorroborated single identity has a
  real `tmdb_id`, so the existing `not _collision` prefetch block still runs.

Because `reidentify` is a `BLOCKING_KIND`, ripped titles park in `QUEUED` (B3
matching gate) and converge to a pooled `REVIEW_NEEDED` at rip end (B4) if the
user never answers — reproducing today's review UX without blocking the rip.

A genuine content-type conflict (heuristic-movie vs TMDB-tv) never sets
`identity_unconfirmed`, so it still parks. No behavior change for movie discs
(both escalation sites are TV-only).

### Fix 2 — Strip trailing rip-annotation tokens in `_parse_volume_label`

The optimization. After season/disc extraction and the existing `strip()`
(`analyst.py` ~1018), drop trailing tokens from a small explicit denylist so the
disc corroborates cleanly and shows no prompt at all:

```python
_LABEL_JUNK_TOKENS = frozenset({"OK", "DONE", "RIP", "RIPPED", "COPY", "BACKUP", "BAK", "FINAL"})
```

- **Trailing-only**, and only while a non-junk token remains (a pathological
  `OK`-only label is not nuked; a legitimate leading token like `OK K.O.!` is
  untouched).

Framing: **Fix 1 is the safety net, Fix 2 is the optimization.** Because Fix 1
guarantees any uncorroborated disc rips-first, the denylist need not be
exhaustive — an unlisted junk token still rips-first-and-defers; it just shows the
"Confirm title" CTA the user can ignore until the pooled review.

## Testing (TDD)

- **Analyst unit** (`tests/unit/test_analyst*.py`):
  - `_parse_volume_label("DS9S3D2 ok")` → name `"Ds9"` (or `"DS9"` pre-title-case), season 3, disc 2.
  - Trailing junk strip preserves leading/embedded tokens; `OK`-only label not emptied.
  - Analysis of an uncorroborated TV disc sets `identity_unconfirmed=True`; a
    corroborated clean label sets it `False`.
- **Coordinator unit** (`tests/unit/test_identify_rip_first_gates.py`):
  - an `identity_unconfirmed` analysis routes to
    `_rip_first_with_prompt(kind="reidentify")` and does **not** park.
  - a content-type-conflict review (no `identity_unconfirmed`) still parks.

## Out of scope

- Re-tuning `_abbreviation_matches` strictness.
- The pre-#403 mis-organization of job 153 (`DS9S1D1 (1993)` folder) — already
  fixed by #403's abbreviation-aware naming for clean labels.
- Disc-hash (Phase C) recognition interactions.
```
