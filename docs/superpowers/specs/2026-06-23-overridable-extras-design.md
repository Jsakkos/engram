# Design: Overridable auto-detected extras

**Date:** 2026-06-23
**Status:** Approved (pending implementation plan)

## Problem

Auto-detected extras are effectively locked: a user cannot override them from the
review panel. The only workarounds today are to let the disc finish processing and
then reassign/move tracks manually.

The motivating case is a **dual-episode track** (e.g. a single ~90-minute title
containing two ~45-minute episodes). Its duration falls outside the episode-runtime
window, so the duration pre-filter misclassifies it as an extra and it gets filed
into `Extras/` with no chance for the user to correct it.

## Root cause

The app already **defers organization** for normal tracks: a matched episode rests
in `TitleState.MATCHED` ("matched but not yet organized",
`backend/app/models/disc_job.py:38`) and is only filed at the very end of the disc by
`check_job_completion` → `finalize_disc_job`
(`backend/app/services/finalization_coordinator.py:739`). While *any* title needs
review, the whole disc is held in staging and **nothing is organized**.

Extras are the one exception that breaks this pattern. Under the default
`extras_policy="keep"`, `matching_coordinator._handle_extras` organizes the extra
**mid-matching** and jumps it straight to `COMPLETED`
(`backend/app/services/matching_coordinator.py:1550-1623`). Consequences:

1. The file is physically moved into `Extras/` immediately.
2. The title is `COMPLETED`, so the review UI renders it in the read-only, greyed-out
   "Processed" list (`frontend/src/components/ReviewQueue.tsx:1160`) — the
   `activeTitles`/`completedTitles` split at line 631 makes only non-completed titles
   editable. That is the "lock."

`finalize_disc_job` already knows how to file `matched_episode == "extra"` titles at
the end (it skips them during episode-conflict detection at
`finalization_coordinator.py:813` and routes them to `organize_tv_extras`). So a
deferred extra needs **no new organizing code** — it just rides the existing rails.

## Goal

Make auto-detected extras behave like every other track: keep them in the normal,
editable track set (labelled as an extra) and **defer all organizing to the end of
the disc**. The disc then either:

- **completes automatically** (the extra is filed into `Extras/` at finalize, as
  today — same end result, just filed at the end), or
- **goes to review** (because some other title needs it), where the extra now appears
  as an ordinary, selectable track pre-labelled "extra" that the user can reassign to
  an episode before saving.

## Non-goals (explicit scope guardrails)

These were considered and deliberately excluded:

- **No change to the duration heuristic.** Detection logic is untouched.
- **No multi-episode support.** A single track still maps to one episode code; we do
  not add `S01E01-E02` handling.
- **Extras do not independently force review.** A cleanly-matched disc still
  auto-completes and files its extras automatically without prompting. (Overriding a
  misdetected extra is therefore only possible when the disc goes to review for some
  other reason — an accepted consequence.)
- **No new "reassign an already-organized extra" file-move path**, and no changes to
  the History page or dashboard job card. Because extras are never organized early
  anymore, the "move a file out of `Extras/`" problem does not arise.

## Design

### Backend — defer the `keep` branch

In `matching_coordinator._handle_extras`, the `keep` branch
(`backend/app/services/matching_coordinator.py:1550-1623`) stops organizing
immediately. When a track is detected as an extra under `keep`, set:

- `state = TitleState.MATCHED` (not `COMPLETED`)
- `matched_episode = "extra"`
- `is_extra = True`
- `match_details = {"auto_sorted": "extras", "action": "deferred", "reason": "<duration reason>"}`
- **no** `organize_tv_extras` call, **no** `organized_to`, **no** `organized_from`

Then call `check_job_completion` as before. The extra is now a resolved-but-unorganized
title and flows through the normal end-of-disc path:

- All other titles matched, none in review → `finalize_disc_job` files the extra into
  `Extras/` and the job auto-completes.
- A title needs review → the whole disc parks in review with nothing organized; the
  extra is `MATCHED` and editable.

The `extra_index` numbering that the old early-organize branch computed inline is no
longer needed here — `finalize_disc_job` / `_finalize_tv_if_resolved` already assign a
monotonic `extra_index` across the final organize sweep.

The `skip` (discard) and `ask` (always-review) branches are **unchanged**.

### Backend — reassignment correctness

When a user reassigns a deferred extra to a real episode, the final organize must file
it as an episode, not an extra:

- `_finalize_tv_if_resolved` already routes by `matched_episode` and recomputes
  `is_extra = (matched_episode == "extra")` at organize time
  (`finalization_coordinator.py:1497`), so extra→episode reassignment files correctly.
- For clarity/robustness, `_apply_decision_fields`
  (`finalization_coordinator.py:1365`) should clear `is_extra` when `episode_code` is a
  real episode code (not `"extra"`/`"skip"`), so the in-DB flag is not transiently
  stale before finalize. (Confirm `finalize_disc_job`'s organize loop also sets
  `is_extra` from the final code, mirroring `_finalize_tv_if_resolved`.)

### Frontend — review pre-fill

The review UI already treats `MATCHED` titles as active/editable, renders an "extra"
badge (`ReviewQueue/TitleList.tsx`), and exposes an "Extra" action button
(`ReviewQueue/Inspector.tsx`). The single gap is the pre-fill in `fetchJobDetails`
(`frontend/src/components/ReviewQueue.tsx:283-290`): when `matched_episode === "extra"`,
stage the action as `'extra'` (and selection `'extra'`) rather than `'episode'`, so:

- the "Extra" button shows as the selected action,
- the TitleList badge reads "extra",
- a no-op save re-files the title as an extra.

Picking an episode from the Inspector dropdown overrides this (sets action
`'episode'`), and saving reassigns the title to that episode.

## Data flow (after change)

```
[Title detected as extra — keep policy]
    → state = MATCHED, matched_episode = "extra", is_extra = True   (NOT organized)
    → check_job_completion
        ├─ no title in REVIEW → finalize_disc_job
        │     → organize_tv_extras (filed to Extras/) → job COMPLETED
        └─ a title in REVIEW → park whole disc in review (organize nothing)
              → extra appears in editable track list, pre-labelled "extra"
              → user keeps as extra  → finalize files to Extras/
                 OR user picks an episode → finalize files as that episode
```

## Testing

**Backend (`backend/tests/`):**

- Update existing `extras_policy` tests in `tests/unit/test_matching_coordinator.py`:
  `keep` now yields `MATCHED` / `matched_episode == "extra"` / not organized / not
  `COMPLETED` (previously asserted immediate organize + `COMPLETED`).
- New: `keep` + a clean disc (all other titles matched) → extra is organized at
  finalize, file lands in `Extras/`, job `COMPLETED`.
- New: `keep` + a review-needed title → job `REVIEW`; the extra is `MATCHED`,
  `is_extra = True`, not yet organized; reassigning it to an episode and saving files
  it as that episode (and `is_extra` becomes `False`).
- Verify `skip` and `ask` behaviour is unchanged.

**Frontend (`frontend/src`):**

- Unit test (vitest) for the `extra` pre-fill: a `MATCHED` title with
  `matched_episode === "extra"` renders with the "Extra" action selected and the
  "extra" badge.

## Risks / regressions to watch

- **Happy-path completion timing.** Extras now file at the end instead of mid-stream.
  End result is identical, but progress accounting shifts slightly (an extra counts as
  `MATCHED` until finalize rather than `COMPLETED` early). Acceptable.
- **Auto-escalation guards.** Deferred extras are `MATCHED`, not `REVIEW`, so the
  review-escalation / deep-rematch guards (which target `REVIEW` titles and already
  exclude extras) do not act on them. Confirm no path deep-rematches a `MATCHED`
  extra.
- **Simulation path.** Check `simulation_service` extra handling so E2E/simulation
  flows reflect the deferred behaviour.
