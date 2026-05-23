# Match-confidence calibration — design

**Date:** 2026-05-22
**Status:** Approved (design); implementation in progress

## Problem

The episode-match "confidence" shown in the review UI is the raw
`ranked_voting_score` from `app/matcher/episode_identification.py`
(`MatchCoverage.ranked_voting_score`): the weighted mean cosine of the chunks
that voted for the winning episode. Because each vote is a TF-IDF cosine between
a 30-second ASR snippet and a full ~22-minute subtitle file ("teaspoon vs
bucket"), a *correct* match scores only ~0.15–0.21. Stored verbatim in
`DiscTitle.match_confidence` and rendered as a percentage, a correct top match
reads **~18%**, and the review gates (curator `0.7`, frontend `0.85/0.7/0.5`)
are effectively unreachable, so every match looks low-confidence.

The raw score answers *"how much text overlapped"* (a magnitude). The UI slot
asks *"how sure are we this is the right episode"* (a probability-like
judgment). The fix is a **translation** between the two, not a rescale.

## Signals (grounded in real AD S1 logs)

| File | Episode (correct) | raw `score` | `score_gap` (top1−top2) | votes/samples |
|------|------|------|------|------|
| B1_t06 | S01E13 | 0.165 | 0.1652 | 8/10 |
| B1_t05 | S01E12 | 0.180 | 0.1795 | 5/10 |
| C1_t07 | S01E14 | 0.180 | 0.1804 | 4/10 |

Key observation: in every correct case `score_gap ≈ score`, i.e. **top2 ≈ 0**.
Since a chunk only votes at cosine > 0.15, top2 ≈ 0 means *no other episode got a
single vote* — the winner swept the field. The certainty lives in the
**separation**, not the magnitude.

## Three independent reported metrics (each 0–1)

1. **normalized_score** = `clamp(score / 0.18, 0, 1)` — magnitude normalized
   against the *vote threshold band* (votes need >0.15; correct ≈ 0.15–0.21),
   **not** against coverage. Normalizing by coverage was rejected: `score` is
   already a mean over *matched* chunks (not diluted by misses), and
   `score/coverage` is algebraically `score × duration / 300`, leaking episode
   length into confidence (a 44-min episode would read *more* confident than a
   22-min one at the same cosine). Discriminative power is small (the cosine band
   is razor-thin) — this is a mild guard, kept mostly for transparency.
2. **consensus** = `clamp(votes / samples, 0, 1)` — of the chunks examined, how
   many agreed with the winner.
3. **coverage** = `clamp(samples × 30 / video_duration, 0, 1)` — the fraction of
   the file actually run through the matcher (processed, *not* matched). This is
   independent of consensus: coverage is "how much we looked at," consensus is
   "of what we looked at, how much agreed." Currently not computed (the code only
   has *matched* coverage `file_cov`/`total_weight`); we add it.

## The missing signal: separation

None of the three metrics above looks at the runner-up, so a **decisive sweep**
(top2 = 0) and a **near-tie** (top1 0.18 vs top2 0.16) produce identical numbers
— yet the near-tie is the dangerous case (two-parters, recaps, wrong episode in
the reference set). We add a separation term:

```
separation = clamp(score_gap / max(score, eps), 0, 1)   # ≈1.0 when uncontested
```

## Calibration formula

```
evidence_raw = 0.50·consensus + 0.25·normalized_score + 0.25·coverage
evidence     = EVIDENCE_FLOOR + (1 − EVIDENCE_FLOOR)·evidence_raw   # FLOOR=0.30
confidence   = separation · evidence
```

`confidence = decisiveness × evidence-strength`. Separation is the safety gate
(near-ties crater regardless of evidence); the three user metrics form the
evidence term, with consensus weighted highest. Constants:
`QUALITY_REF_COSINE=0.18`, `COVERAGE_REF` handled by the `/0.15`-style band via
`clamp(coverage/0.15,…)` is folded into `coverage` above (REF=0.15 → typical TV
≈1.0, longer episodes lower — the correct direction).

### Behavior on real + synthetic cases

| Case | separation | consensus | conf | outcome |
|------|------|------|------|------|
| S01E13 (8/10, swept) | 1.0 | 0.8 | **0.92** | auto-organize |
| S01E12 (5/10, swept) | 1.0 | 0.5 | **0.83** | auto-organize |
| S01E14 (4/10, swept) | 1.0 | 0.4 | **0.79** | auto-organize |
| near-tie 0.18/0.16, 6/10 | 0.11 | 0.6 | **~0.09** | review |
| 2× contest 0.20/0.10, 7/10 | 0.50 | 0.7 | **~0.45** | review |
| weak uncontested 0.15, 2/10 | 1.0 | 0.2 | **~0.69** | review (borderline) |

Correct decisive matches move from ~18% to **79–92%**; contested/thin matches
stay in review. A longer episode at the same score/gap/votes reads *lower* (less
coverage), eliminating the length artifact.

## Wiring (keep raw `score` intact)

`match_details["score"]` and `runner_ups[].score` are raw signals the matcher's
accept-vs-fallback gate AND `finalization_coordinator` conflict resolution
depend on — so calibration is added as a **new** field, never an overwrite.

- **`episode_identification.py`**: add pure `calibrate_confidence(...)` (returns
  `(confidence, components)`) + a `_attach_calibrated_confidence(best_match,
  results_summary, video_duration, chunk_len)` helper (testable without ASR).
  Set `best_match["confidence"]` = calibrated (flows to `match_confidence`);
  keep `best_match["score"]` raw. Add `confidence/separation/normalized_score/
  consensus/coverage` to `match_details`. Each runner-up gets
  `confidence = winner_conf · (ru_score / top1)` (keeps raw `score`), so the
  winner's leaderboard entry equals the headline and near-ties degrade
  gracefully.
- **`curator.py`**: unchanged logic — `confidence` is now calibrated, so
  `needs_review = confidence < 0.7` finally means something.
- **`finalization_coordinator.py`**: conflict ranking/reassignment stays on raw
  `score`; reassignment writes `loser.match_confidence = ru["confidence"]`
  (calibrated, falling back to `ru["score"]`) so reassigned titles also show a
  meaningful percentage.
- **frontend `adapters.ts`**: `extractMatchCandidates` and
  `extractFinalMatchInfo` prefer `confidence ?? score`.

## Thresholds

- **Accept-vs-fallback gate stays on raw `score`** (`> 0.10`, votes ≥ 2) — proven,
  don't destabilize which discs match.
- **needs_review / auto-organize moves to the calibrated scale** (curator `0.7`,
  frontend `0.85/0.7/0.5` unchanged numerically, now meaningful).

## Tests (`backend/tests/unit/test_confidence_calibration.py`)

- Real AD cases → high (E13 ≥ 0.85; E12, E14 ≥ 0.75).
- Near-tie / 2× contest → review band (< 0.7).
- Monotonic in votes and in score_gap.
- Length-artifact guard: same score/gap/votes, longer file (lower coverage) →
  confidence not higher.
- Components in [0,1]; winner runner-up confidence == headline; edge cases
  (single candidate, zero target_votes, score 0).
- Frontend `adapters.test.ts`: runner-up + final confidence prefer calibrated.
