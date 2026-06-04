# Per-track provenance rendering — design

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Scope:** Frontend UI polish (one adapter + one component, plus a small brand-mark variant). No backend behavior change.

## Problem

The per-track cards inside a `DiscCard` render match provenance inconsistently. On one
Gilmore Girls S2 job, three matched tracks rendered three different ways:

- `S02E17` — `ENGRAM` chip **and** `71%  3/10` votes.
- `S02E18` / `S02E19` — only `→ S02Exx`, **no** confidence, **no** votes, **no** chip (looks broken/empty).
- A bonus track — only an `EXTRA` badge.

### Verified root causes

1. **Confidence + votes are read from `match_details`, not the reliable column.**
   The adapter's `extractFinalMatchInfo` ([frontend/src/types/adapters.ts:92](../../../frontend/src/types/adapters.ts))
   returns confidence/votes only when `match_details` has **both** `score` **and** `vote_count`:
   - **Chunk-vote path** writes `vote_count` → card shows `71%  3/10`.
   - **Full-file fallback** ([backend/app/matcher/episode_identification.py:1667](../../../backend/app/matcher/episode_identification.py))
     writes `match_details = {"method": "full_transcription", "score": ...}` with **no `vote_count`**
     → `extractFinalMatchInfo` returns `undefined` → matched card shows a bare `→ S02E18`.

   The `match_confidence` **column** is reliably populated on *every* path (`result.confidence`
   for ASR at [matching_coordinator.py:906](../../../backend/app/services/matching_coordinator.py),
   `0.99` for DiscDB, `1.0` for user/manual), but the UI ignores it for the displayed number.

2. **The provider chip is gated on `match_source`, which varies by path/vintage.**
   The chip renders only when `track.matchSource` is truthy ([TrackGrid.tsx:146](../../../frontend/src/app/components/TrackGrid.tsx)).
   `match_source` is `"engram"` for MATCHED, `None` for REVIEW, `"discdb"`/`"user"`/`"engram_chromaprint"`/`"ai_llm"`
   elsewhere, and absent on older/edge matched rows. So the chip appears unevenly across tracks of one job.

### Why full-file fallbacks genuinely have no votes (not a bug)

Engram has two distinct matchers. **Ranked voting** samples ~10 chunks, each chunk "votes" for an
episode, and accepts on `(score_ok OR confidence_ok) AND votes_ok`. **Full-file fallback** is reached
*because* ranked voting failed the vote gate; it transcribes the whole file and does a single
whole-vs-whole TF-IDF comparison → one confidence number, **no chunks, no votes by construction**.
Showing confidence-without-votes is the *correct* representation of a full-file match. The deeper
question — *why so many tracks drop to full-file* (chunk-cosine scale / sparse sampling) — is a
matching-quality concern and is **out of scope** here.

## Goal

Every track renders its provenance consistently. A matched track is never bare. "No votes" is shown
as a meaningful method signal, not an empty gap. Fabricate nothing.

### Non-goals

- No change to backend matching, scoring, vote thresholds, or the full-file fallback rate.
- No change to the Review-queue UI or the History detail panel.
- Extras keep their existing `EXTRA` treatment; Needs-Review keeps its existing best-guess treatment.

## Rendering policy (per provenance)

| Provenance (`match_source` / state) | Episode line | Confidence % | Votes `N/M` | Method tag | Chip |
|---|---|---|---|---|---|
| Chunk-vote — `engram`, has `vote_count` | `→ S02E17` | ✅ column | ✅ | — | Engram mark, **cyan**, tooltip "Matched by Engram (ASR)" |
| Full-file — `engram`, no `vote_count` | `→ S02E18` | ✅ column | — | `FULL-FILE` (muted) | Engram mark, **cyan**, tooltip "Matched by Engram (ASR)" |
| Chromaprint — `engram_chromaprint` | `→ S02E18` | ✅ column | ✅ if present, else — | `FULL-FILE` only if no votes | Engram mark + node, **magenta**, tooltip "Matched by Engram (audio fingerprint)" |
| DiscDB — `discdb` | `→ S02E05` | ✅ column (0.99) | — | — | `DISCDB` text chip (blue) |
| AI — `ai_llm` | `→ S02E06` | ✅ column | — | — | `AI` text chip (purple) |
| User — `user` | `→ S02E07` | ✅ column (1.0) | — | — | `MANUAL` text chip (green) |
| Matched, `match_source` missing (legacy/edge) | `→ S02Exx` | ✅ column | ✅ if present | `FULL-FILE` if no votes | Engram mark, **cyan** (default), tooltip "Matched by Engram" |
| Extra — `matched_episode == "extra"` | `→ [EXTRA]` | — | — | — | `EXTRA` (unchanged) |
| Needs review — `REVIEW` | best-guess + `NEEDS REVIEW` | best-guess % (from `match_details`) | if present | — | none (unchanged) |

Decisions locked with the user: confidence always shows from the column; votes only when they exist;
chip always present on matched/done; **icon-only Engram mark** for Engram-engine sources (cyan ASR /
magenta fingerprint) with a hover tooltip; **`FULL-FILE`** method tag on voteless full-file matches.

## Design

### 1. Adapter — source the displayed confidence from the column; derive method

`frontend/src/types/adapters.ts`, `transformDiscTitleToTrack`:

- **Displayed matched confidence** comes from the `match_confidence` **column** when it is `> 0`
  (covers every matched/done path), and falls back to the `match_details`-derived value when the
  column is `0` (covers the REVIEW best-guess, where the column is `0.0` but `match_details` carries
  the best-guess score). Concretely: `finalMatchConfidence = title.match_confidence > 0 ? title.match_confidence : extractFinalMatchInfo(title)?.confidence`.
- **Votes** (`finalMatchVotes` / `finalMatchTargetVotes`) continue to come from `match_details`
  and are populated **only** when `vote_count` is present. (Unchanged semantics, just no longer
  the gate for confidence.)
- **New derived field `matchMethod?: 'chunk_vote' | 'full_file'`** on `Track`, computed from
  `match_details`: `vote_count` present → `'chunk_vote'`; `method === 'full_transcription'`
  (or `score` present without `vote_count`) → `'full_file'`; otherwise `undefined` (DiscDB/AI/manual
  carry their own chip and need no method tag).

`extractMatchCandidates` and the runner-up rendering are unchanged.

### 2. `Track` type

Add `matchMethod?: 'chunk_vote' | 'full_file'` to the `Track` interface in `DiscCard.tsx`. No other
field changes; `matchSource`, `finalMatch*`, `isExtra` stay as-is.

### 3. Brand mark — chromaprint "fingerprint" variant

`MarkMono` (three open arcs) already serves ASR. Add an optional `node?: boolean` prop that renders
a small filled center node, giving the fingerprint variant. (Single prop on the existing component;
no new file.) The chip uses `MarkMono` at ~12px: cyan for ASR, `magenta` + `node` for chromaprint.

### 4. TrackGrid — source chip + confidence + method rendering

`frontend/src/app/components/TrackGrid.tsx`:

- **Provider chip** (`badges` row): for `engram` / `engram_chromaprint` / missing-source-but-matched,
  render an **icon chip** — `MarkMono` wrapped so it has an accessible label and a hover tooltip
  (reuse `components/ui/tooltip`), with `aria-label`/`title` for non-hover/AT users. For
  `discdb` / `ai_llm` / `user`, keep the existing **text** `SvBadge` (`DISCDB` / `AI` / `MANUAL`).
  Replace the current `matchSourceLabel`/`matchSourceColor` helpers with a single source-descriptor
  map that returns `{ kind: 'icon' | 'text', label, tone, tooltip, node? }`.
- **Method tag**: when `track.matchMethod === 'full_file'`, render a muted `SvBadge`
  (`tone: sv.inkDim`) reading `FULL-FILE` next to the source chip. Not shown for chunk-vote.
- **Matched body** ([TrackGrid.tsx:346](../../../frontend/src/app/components/TrackGrid.tsx)): the
  confidence `%` now renders whenever `finalMatchConfidence !== undefined` (which is now true for
  full-file/DiscDB/manual via the column). Votes still render only when `finalMatchVotes !== undefined`.
  Existing color thresholds (`≥0.7` green, `≥0.4` yellow, else red) unchanged.

### Edge cases

- **REVIEW best-guess** confidence still comes from `match_details` (column is `0.0` there) — the
  `> 0 ? column : match_details` rule handles this without a special case.
- **Legacy matched rows without `match_source`**: default to the cyan Engram (ASR) icon, since a
  MATCHED ASR title is the only way to reach that state historically. Tooltip omits the method
  qualifier ("Matched by Engram").
- **Extras** are detected by `is_extra` / `matched_episode == "extra"` and keep the `EXTRA` badge;
  no provider chip, no confidence.

## Testing

- **Adapter unit tests** (`frontend/src/types/__tests__/adapters.test.ts`): extend with fixtures for
  each provenance — chunk-vote (votes + confidence + `chunk_vote`), full-file (`{method, score}` →
  confidence from column + `full_file` + no votes), DiscDB (`0.99`, no votes, no method), manual
  (`1.0`), REVIEW (column `0` → confidence from `match_details`), extra (no confidence). Assert
  `finalMatchConfidence`, `finalMatchVotes`, and `matchMethod`.
- **Component test** (`DiscCard.test.tsx` or a `TrackGrid` test): assert the icon chip + tooltip for
  `engram`/`engram_chromaprint`, the text chip for `discdb`/`ai_llm`/`user`, the `FULL-FILE` tag on a
  voteless full-file track, and that a full-file matched track shows a confidence `%` (no longer bare).
- **No new E2E** required; existing track-grid E2E selectors (`sv-track-card`, `source-badge-*`)
  must continue to resolve — keep stable `data-testid`s on the chip.

## Out of scope (flagged follow-ups)

- The high **full-file fallback rate** on sparse-sampling discs (chunk-cosine scale) — a separate
  matching-quality investigation, not a rendering fix.
- Backend normalization of `match_source` on legacy rows (the UI default covers it for display).
