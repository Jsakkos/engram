# Manual Track Skipping (Queued / Not-Yet-Ripped Tracks)

**Date:** 2026-07-03
**Status:** Approved design, pending implementation plan

## Problem

Play-all track detection is not always perfect. A disc's "play all" concatenation
title (or another unwanted track) can slip into the selected-to-rip set, wasting rip
time on a large duplicate. Users want to manually skip a track while it is queued, or
during an active rip before MakeMKV reaches it.

## Scope (decided)

- **In scope:** skipping tracks that are `PENDING` or `QUEUED` (not yet ripped),
  both before the rip starts (pre-rip review window) and during an active rip.
- **Out of scope:** aborting the track MakeMKV is actively writing right now
  (would require killing/restarting the makemkvcon process and lose the in-flight
  file). Explicitly excluded.
- **Skip guarantee:** best-effort.
  - Per-title (subset) rips: guaranteed to skip, because the extractor checks a
    live skip-set before each title's command.
  - Full-disc `all` pass already underway: MakeMKV writes the bytes regardless;
    the track is excluded from matching/library and its file is deleted.
- **Skipped outcome:** a distinct terminal `SKIPPED` disposition. Excluded from
  matching/library, does not block job completion, is NOT counted as a failure,
  and is reversible (un-skip) until MakeMKV reaches/writes it.

## Key technical constraint

MakeMKV rips serially. When every title is selected, Engram issues one
`mkv ... all` command (single disc open, much faster). When a subset is selected,
it loops one makemkvcon command per title. There is no way to surgically drop a
single title mid-stream inside an `all` pass without killing the whole process.
This is why "skip during ripping" is best-effort for the `all`-pass case and
guaranteed only for the per-title loop.

A useful consequence: a *pre-rip* skip shrinks the selection below "all," which
routes the rip to the per-title loop automatically. So pre-rip skips are always
honored with zero extractor involvement; only a skip issued *mid-`all`-pass* needs
the delete fallback.

## Design

### 1. Data model

Add `SKIPPED = "skipped"` to `TitleState` (`backend/app/models/disc_job.py:31`).

- No new column: the existing `is_selected` (bool, default `True`) pairs with the
  new state. A skipped track has `is_selected=False` and `state=SKIPPED`.
- No Alembic migration: `TitleState` is stored as a string in the existing `state`
  column. Confirm there is no CHECK constraint on the column before relying on this.
- Record the reason in `match_details`:
  `{"reason": "Skipped by user", "skipped": true}` for history/diagnostics.

`SKIPPED` is terminal. It must be:
- Excluded from the `active_states` completion gate
  (`backend/app/services/finalization_coordinator.py:689`).
- Excluded from the `all_failed` computation (a partially-skipped disc still
  completes normally).

### 2. Backend service

New methods on `JobManager`, distinct from the existing `skip_title` (which forces
*stuck active* tracks to REVIEW/FAILED, a different intent that must be preserved):

**`skip_rip_title(job_id, title_id)`**
- Guard: acts only on titles in `PENDING` or `QUEUED`. Rejects `RIPPING`,
  matched, or terminal titles.
- Effect: `is_selected=False`, `state=SKIPPED`, write `match_details` reason,
  register the `title_index` in a live skip-set on the extractor
  (`{job_id: {title_index}}`), broadcast a `title_update`, then re-check job
  completion.

**`unskip_rip_title(job_id, title_id)`**
- Guard: allowed while `state==SKIPPED` and the title's output file has not been
  written.
- Effect: `state=PENDING`, `is_selected=True`, clear the skip reason, remove the
  `title_index` from the skip-set, broadcast a `title_update`.

### 3. Extractor

Honor the live skip-set (`MakeMKVExtractor`, `backend/app/core/extractor.py`):

- Add a per-job skip-set: `_skipped_indices: dict[int, set[int]]` plus
  `skip_title_index(job_id, idx)` / `unskip_title_index(job_id, idx)` methods.
- Per-title loop (`_rip_titles_unlocked`, around `extractor.py:775`): the loop
  already checks `job_id in self._cancelled_jobs` before each command. Add a
  symmetric check: before running a title's command, if its `title_index` is in
  the skip-set, skip that command (`continue`). Refactor the `commands` list to
  carry `(title_index, cmd)` tuples so the loop can map command to title. This
  guarantees the skip saves rip time.
- Full-disc `all` pass: no extractor change. The single command rips everything.
  The delete happens downstream (see below).

### 4. Rip orchestration and completion

- `_run_ripping` selection and safety-net logic
  (`backend/app/services/job_manager.py:2114`) already handles deselected titles.
  Update it to treat `SKIPPED` like deselected (skip it in the selected set)
  rather than re-marking it extra/COMPLETED.
- `_on_title_ripped` (`backend/app/services/job_manager.py:2899`): when a
  completed file belongs to a title whose current DB state is `SKIPPED` (the
  mid-`all`-pass case), delete the output file and do not dispatch matching; leave
  the title `SKIPPED`.
- Completion (`check_job_completion`): with `SKIPPED` excluded from `active_states`
  and `all_failed`, a partially-skipped disc completes normally.
- Edge case: every title skipped. The job should complete as `COMPLETED` with
  nothing organized. Add an explicit guard/log so this does not read as a silent
  failure.

### 5. API

`backend/app/api/routes.py`:

- `POST /jobs/{job_id}/titles/{title_id}/skip-rip`
- `POST /jobs/{job_id}/titles/{title_id}/unskip-rip`

Both return 400 if the job is terminal or the title is not in a skippable
(`PENDING`/`QUEUED`) / un-skippable (`SKIPPED`, unwritten) state. Kept separate
from the existing `/skip` endpoint to preserve its stuck-track semantics.

### 6. Frontend (implemented via the frontend-design skill)

`frontend/src/app/components/TrackGrid.tsx` and supporting types/adapters:

- A **skip control** (small ✕ / "skip" affordance) on each `PENDING`/`QUEUED`
  track card, revealed on hover, wired to `skip-rip`.
- A new **`skipped`** entry in the `STATE` map and the `TrackState` type, plus
  `frontend/src/types/adapters.ts`, rendered as a muted "SKIPPED" badge with an
  **un-skip** affordance while the track is still reversible.
- API client helpers `skipRipTitle` / `unskipRipTitle` in
  `frontend/src/api/client.ts`.
- The same control serves the pre-rip review-selection window (job parked in
  `REVIEW_NEEDED`), since those are the same `PENDING` tracks.

### 7. Testing

- **Backend unit:** extractor skip-set honored in the per-title loop;
  `skip_rip_title` / `unskip_rip_title` state transitions and guards
  (reject `RIPPING`/terminal; un-skip only while unwritten).
- **Backend integration:** simulate insert, skip a queued title mid-rip, assert it
  ends `SKIPPED`, its file is absent, the job reaches `COMPLETED`, and other titles
  match. Un-skip round-trip. All-skipped edge case.
- **Frontend E2E:** skip and un-skip button flow via simulation endpoints.

## Touch-point summary

| Layer | File | Change |
|-------|------|--------|
| Model | `backend/app/models/disc_job.py` | Add `TitleState.SKIPPED` |
| Extractor | `backend/app/core/extractor.py` | Live skip-set + per-title-loop check; `(idx, cmd)` tuples |
| Service | `backend/app/services/job_manager.py` | `skip_rip_title` / `unskip_rip_title`; `_run_ripping` + `_on_title_ripped` handle `SKIPPED` |
| Completion | `backend/app/services/finalization_coordinator.py` | Exclude `SKIPPED` from `active_states` and `all_failed`; all-skipped guard |
| API | `backend/app/api/routes.py` | `skip-rip` / `unskip-rip` endpoints |
| Frontend | `TrackGrid.tsx`, `types`, `adapters.ts`, `client.ts` | Skip/un-skip controls + `skipped` state |

## Open items to confirm during implementation

- Verify no CHECK constraint on `disc_titles.state` (so no migration is needed).
- Confirm any other enumerations of active/terminal title states outside
  `finalization_coordinator` (for example `reconcile_stuck_titles`, force-progress
  paths) also treat `SKIPPED` as terminal.
