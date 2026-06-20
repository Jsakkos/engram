# DiscDB: enable lookup, keep contribution gated, demote DiscDB episode ordering below ASR

**Date:** 2026-06-20
**Status:** Draft for review
**Scope:** Backend feature flags, frontend feature flags, track-matching precedence, one badge restyle

## Problem

TheDiscDB integration is fully disabled behind a single master switch on each side
(`DISCDB_ENABLED` in the backend, `FEATURES.DISCDB` in the frontend). The original
intent was to gate only the *contribution* feature (uploading disc data to
thediscdb.com) until its API contract and UX were validated, but the master switch
also disables *lookup* (using TheDiscDB to help identify a disc and match its
tracks). Lookup is read-only and safe to ship; contribution should stay off.

A second issue surfaces once lookup is on: TheDiscDB numbers episodes by **physical
disc order**, not **aired order**. Engram's matching network is built entirely on
canonical aired order, so a DiscDB episode mapping frequently disagrees with the
Engram ASR/fingerprint match. Today DiscDB wins that disagreement outright: when a
mapping exists, `try_discdb_assignment` stamps the title MATCHED at confidence 0.99
and **ASR never runs**. This produces wrong episode numbers on discs whose disc
order differs from aired order.

## Goals

1. Enable TheDiscDB **lookup** (disc identification + track matching contribution),
   keep **contribution** (local export + submit/upload) gated off.
2. Surface the lookup-related UI (match-source badges, lookup settings toggle),
   keep the contribute page and submit controls hidden.
3. Make **ASR the preferred episode signal**: ASR always runs; DiscDB episode
   mappings are only used when the ASR confidence is very low.
4. Refresh the stale DiscDB match-source badge to the current icon system.

## Non-goals

- Re-enabling any automated or manual contribution/upload path.
- Changing how DiscDB drives **show identification** (tmdb_id, content type, show
  identity). That stays as-is and is valuable.
- Reworking the manual per-track "use DiscDB match" review action (its behavior is
  preserved).

## Current state (as found)

### Backend gating
- `backend/app/core/features.py`: `DISCDB_ENABLED = False` (single master switch).
- `backend/app/services/identification_coordinator.py:1519`: lookup block gated by
  `if DISCDB_ENABLED and config.discdb_enabled:`. Config default
  `discdb_enabled = True`.
- `backend/app/services/cleanup_service.py:38`: auto-export gated by
  `if DISCDB_ENABLED and state == COMPLETED and config.discdb_contributions_enabled:`.
  Config default `discdb_contributions_enabled = False`.

### Backend lookup is read-only
- `backend/app/core/discdb_classifier.py` `classify_from_discdb` only issues GraphQL
  **queries** (`HASH_LOOKUP_QUERY`, `NAME_SEARCH_QUERY`) via `_graphql_request`
  (`requests.post` of a query, no mutation). Enabling lookup cannot write to
  TheDiscDB.

### Current episode-matching precedence (the bug)
- `backend/app/services/identification_coordinator.py:1059-1062`:
  ```python
  discdb_applied = await self._try_discdb_assignment(job_id, dt, session)
  if not discdb_applied:
      task = asyncio.create_task(self._match_single_file(job_id, dt.id, file_path))
  ```
  DiscDB short-circuits ASR. `try_discdb_assignment`
  (`matching_coordinator.py:317`) explicitly "skips audio matching", sets
  `match_confidence = 0.99`, `match_source = source`, state MATCHED.

### Frontend gating (every `FEATURES.DISCDB` site)
| Site | Concern |
|---|---|
| `frontend/src/app/App.tsx:945` `/contribute` route | contribute |
| `frontend/src/config/routes.ts:39` route registry | contribute |
| `frontend/src/app/navigation.ts:64` nav item | contribute |
| `frontend/src/app/App.tsx:106` contribution stats fetch | contribute |
| `frontend/src/components/ReviewQueue/Inspector.tsx:403` "use DiscDB match" action | lookup |
| `frontend/src/components/HistoryPage.tsx:604` DiscDB metadata section | lookup |
| `frontend/src/components/ConfigWizard.tsx:1220` DiscDB settings group | mixed (split) |
| `frontend/src/app/components/TrackGrid.tsx:60` `discdb` source badge | lookup |
| `frontend/src/app/__tests__/App.routing.test.tsx:66,74` route test | contribute (test) |

### Stale badge
- `TrackGrid.tsx:60`: `discdb: { kind: "text", label: "DISCDB", tone: "#60a5fa", ... }`.
  Off-palette raw hex, plain text chip, while the Engram sources are `kind: "icon"`
  chips (`MarkMono`, palette tones `sv.cyan` / `sv.magenta`).

### Confidence thresholds available to anchor on
- `backend/app/core/curator.py`: `HIGH_CONFIDENCE_THRESHOLD = 0.7`,
  `LOW_CONFIDENCE_THRESHOLD = 0.5`.
- `backend/app/matcher/episode_identification.py`: `CONFIDENCE_ACCEPT_FLOOR = 0.70`.

## Design

### 1. Split the backend master switch

`backend/app/core/features.py`:
```python
DISCDB_LOOKUP_ENABLED = True          # disc identification + track matching (read-only)
DISCDB_CONTRIBUTIONS_ENABLED = False  # local export + submit/upload
```
- `identification_coordinator.py:1519` → `if DISCDB_LOOKUP_ENABLED and config.discdb_enabled:`
- `cleanup_service.py:38` → `if DISCDB_CONTRIBUTIONS_ENABLED and state == COMPLETED and config.discdb_contributions_enabled:`

The old `DISCDB_ENABLED` name is removed. The two config flags
(`discdb_enabled`, `discdb_contributions_enabled`) are unchanged; the master switch
simply stops collapsing the distinction the config layer already drew.

### 2. Split the frontend master switch

`frontend/src/config/constants.ts`:
```ts
export const FEATURES = {
  /** TheDiscDB lookups: match-source badges, history metadata, lookup settings toggle. */
  DISCDB_LOOKUP: true,
  /** TheDiscDB contributions: contribute page, nav item, stats badge, submit controls. */
  DISCDB_CONTRIBUTE: false,
} as const;
```
Each site is re-gated per the table above. The `ConfigWizard` DiscDB group is split:
the lookup toggle (`discdb_enabled`) renders under `DISCDB_LOOKUP`; the contributions
toggle (`discdb_contributions_enabled`) and tier control render under
`DISCDB_CONTRIBUTE`.

### 3. Invert ASR <-> DiscDB episode precedence

ASR always runs. DiscDB episode mappings become a post-ASR low-confidence fallback.

- Remove the pre-dispatch short-circuit at `identification_coordinator.py:1059-1062`.
  Every title with an `output_filename` dispatches `_match_single_file`
  unconditionally.
- Apply the DiscDB fallback after the ASR result is evaluated inside
  `_match_single_file_inner` (`matching_coordinator.py:1013`), keyed on the ASR
  confidence:

| ASR confidence | Outcome |
|---|---|
| `>= 0.7` | ASR result auto-organizes (DiscDB ignored even if it disagrees) |
| `0.5 <= conf < 0.7` | ASR result -> Needs Review (unchanged) |
| `conf < 0.5` AND DiscDB mapping exists | DiscDB mapping auto-organizes at high confidence |
| `conf < 0.5` AND no DiscDB mapping | ASR guess -> Needs Review (unchanged) |

- Floor constant: reuse `0.5` (curator `LOW_CONFIDENCE_THRESHOLD`). Define a named
  constant for the DiscDB-fallback floor so the intent is explicit at the call site.
- `try_discdb_assignment` is repurposed: instead of being called pre-dispatch to
  skip matching, it is invoked from the low-confidence branch of
  `_match_single_file_inner` to apply the stored mapping. Its existing behavior
  (find mapping by `title_index`, set episode code / confidence 0.99 /
  `match_source` / `discdb_match_details`, MATCHED, broadcast) is unchanged; only
  the **call site and the condition** change.
- `rematch_single_title(source_preference=...)` is untouched, so the manual
  per-track "use DiscDB match" button still restores the stored DiscDB match on
  demand.

Show identification is unaffected: DiscDB's tmdb_id / content-type / show-identity
contributions in `identify_disc` still apply. Only the per-track episode number
precedence changes.

### 4. Badge icon refresh

`TrackGrid.tsx` `SOURCE_DESC.discdb` becomes a `kind: "icon"` chip rendered through
the existing icon path, using `IcoDisc` (from `icons/media.tsx`) on a palette token
instead of the raw `#60a5fa`. The chip keeps its `data-testid="source-badge-discdb"`
and tooltip ("Matched from TheDiscDB"). The icon-path branch of `SourceChip`
currently hardcodes `MarkMono`; it is generalized to render the descriptor's icon so
DiscDB can use `IcoDisc` while Engram sources keep `MarkMono`.

## Error handling

- DiscDB lookup failure (network, GraphQL error, contract drift) already returns
  `None` and degrades to TMDB/heuristic identification and ASR matching. No change.
- DiscDB fallback in the low-confidence branch: if no mapping applies, fall through
  to the existing ASR-low-confidence review path. The fallback must never raise into
  `_match_single_file_inner`'s result handling (guard like the current
  `try_discdb_assignment` call).

## Testing

Backend:
- Update flag references: `test_identification_coordinator.py` (patches
  `DISCDB_ENABLED` -> `DISCDB_LOOKUP_ENABLED`), `test_disc_name_identification.py`
  (7 patch sites).
- New precedence coverage: high-confidence ASR wins over a disagreeing DiscDB
  mapping; mid-confidence ASR goes to review; very-low ASR with a DiscDB mapping
  auto-organizes from DiscDB; very-low ASR with no mapping goes to review.
- Confirm auto-export stays gated when lookup is on and contributions off.

Frontend:
- `TrackGrid.test.tsx`: assert the DiscDB chip renders via the icon path (and keep
  the `source-badge-discdb` testid assertion).
- Routing test: `FEATURES.DISCDB` -> `FEATURES.DISCDB_CONTRIBUTE` for the
  `/contribute` gate.

## Open items to confirm during planning

- Re-confirm there is no automated TheDiscDB uploader (only the fingerprint-network
  contribution_queue, which is a separate system, plus the manual contribute-page
  submit). If an automated DiscDB uploader exists, it must be gated under
  `DISCDB_CONTRIBUTIONS_ENABLED`.
- Verify the exact local in `_match_single_file_inner` carrying the ASR confidence
  and matched episode, to place the fallback branch precisely.

## Risks

- Enabling lookup means trusting TheDiscDB's GraphQL contract live; failures degrade
  to current behavior, so worst case equals today.
- DiscDB-as-fallback at `conf < 0.5` can still assign a disc-order episode number
  when ASR fails. This is the accepted trade-off (chosen over routing the fallback
  to review): it only applies when ASR could not produce a usable match, where the
  alternative is a manual-review hand-off with no automatic guess.
