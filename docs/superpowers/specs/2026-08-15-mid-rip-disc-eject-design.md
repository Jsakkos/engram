# Mid-Rip Disc Eject: Design

**Date:** 2026-08-15
**Status:** Approved, ready for implementation plan

## Problem

A damaged or dirty disc often reveals itself partway through a rip: read speeds
collapse, MakeMKV grinds, and the user wants the disc out so they can clean it or
try again another day. Today the only mid-rip control is **Cancel**, which fails
the whole job and deletes staged output, losing every track that already ripped
cleanly.

Worse, physically yanking the disc is not a workaround. `_cancel_jobs_for_drive`
(`app/services/job_manager.py`) only cancels jobs in `IDLE`/`IDENTIFYING`; a
`RIPPING` job survives disc removal and then hangs on MakeMKV until the stall
watchdog fires (`ripping_stall_timeout`, default 1200 s).

## Goal

A dedicated **Eject** action that stops the current rip and releases the disc
**without cancelling the job**. Tracks that finished ripping continue through the
standard pipeline (match, then organize); tracks that did not are parked in review
as re-rippable, so the user can clean the disc, reinsert it, and recover them
individually.

Eject is not cancel and not failure. It is a user-chosen early stop with full
salvage.

## Non-goals

- Resuming a partially-ripped **track** from its interrupted byte offset. MakeMKV
  cannot do this; an interrupted track is re-ripped whole.
- Changing the behavior of `auto_eject_enabled` (automatic post-rip eject).
- Any new resume/queue mechanism. Recovery rides the existing single-track
  re-rip flow.

## Design decisions

Four decisions were settled during brainstorming; each is recorded with the
rejected alternative so the reasoning survives.

### D1: unfinished tracks go to REVIEW as re-rippable

Both the in-flight (truncated) track and never-started tracks route to `REVIEW`
via `route_rip_failure_to_review`, making them `rerip_eligible`.

*Rejected:* marking never-started tracks `SKIPPED` (quieter review queue, but the
existing single-track re-rip would not offer them, forcing a whole-disc redo);
marking everything `SKIPPED` and completing (silently drops content with no
record of what is missing).

### D2: the button covers `IDENTIFYING` and `RIPPING`

A damaged disc frequently fails during the *scan*, which is the more common
dirty-disc symptom. The two states get explicitly different behavior:

| State | Behavior |
|-------|----------|
| `RIPPING` | Stop the rip, salvage per D1, job continues to `REVIEW_NEEDED` |
| `IDENTIFYING` | Eject and cancel the job; nothing was produced to salvage |
| anything else | `409`, because the drive is not held |

*Rejected:* `RIPPING` only (misses the common scan-failure case); any non-terminal
state (`MATCHING`/`ORGANIZING` run after auto-eject already fired, so the button
would be a confusing no-op).

### D3: stop the rip first; a failed eject does not roll back

Sequence: terminate MakeMKV, attempt `eject_disc()`, report whether the tray
opened. If the eject fails, the salvage still happens and the UI tells the user to
eject manually. The user's real goal, stopping the drive from hammering a damaged
disc, is met either way, and the disc is now safe to pop by hand because MakeMKV
has released its handle.

*Rejected:* eject first and only stop the rip if the tray opened (looks safer, is
broken, because MakeMKV holds the drive open during a rip so this nearly always
fails); rolling back on eject failure (not implementable, since MakeMKV cannot
resume a terminated pass mid-title, so "rollback" means re-ripping from scratch).

**Sentinel consequence:** call `_drive_monitor.notify_ejected(drive_id)` **only**
on a successful eject. After a failed eject the sentinel must stay armed so it
correctly observes the disc when the user pops it by hand.

### D4: confirmation modal required

Consistent with Cancel and Force, which both use the existing `ConfirmModal` in
`ActionButtons.tsx`. The misclick cost is asymmetric: an accidental eject during a
40-minute Blu-ray rip is expensive, one extra click is not. The modal body also
teaches the non-obvious salvage semantics.

*Rejected:* no modal (the button sits in a row the user hovers constantly); an
"undo" toast (there is nothing to undo, because MakeMKV is already terminated).

## Architecture

### Existing machinery this builds on

The feature has a near-twin already in the codebase. `aborted_for_skip`
(`app/core/extractor.py`) terminates a full-disc MakeMKV pass mid-flight when the
user skips the last wanted track: it keeps titles that finished, deletes the file
being actively written, and returns `success=True`. Eject is the same shape with
one difference: **no per-title fallback pass afterward**, because the disc is
gone.

Likewise, `route_rip_failure_to_review(job_id, title_id, error_code, message)`
already parks a title in `REVIEW` and stamps it `rerip_eligible`. That is the
existing "clean the disc and reinsert" path from the single-track re-rip feature.

### Extractor

`Extractor.eject_abort(job_id)` sets a per-job `_ejected_jobs` flag and terminates
the subprocess, mirroring `cancel()` but distinguishable from it. The rip loop's
existing per-line cancel check already breaks out on terminate.

`RipResult` gains `aborted_for_eject: bool = False`. The return path checks it
**before** the `_cancelled_jobs` branch and returns `success=True` with the
finished output files. Nothing failed; the user chose this.

The truncated in-flight file is deleted using the same "grew since the last poll"
detection `aborted_for_skip` already uses, so a partial `.mkv` never reaches
matching.

### JobManager: post-rip fork

In `_run_ripping`, `result.aborted_for_eject` takes an early branch that:

1. **Skips the per-title fallback pass.** The disc is gone; re-ripping each
   missing title would cost a full stall timeout apiece.
2. **Skips the auto-eject block.** Already ejected, and re-issuing would
   spuriously reset sentinel state.
3. **Routes every still-unfinished selected title to REVIEW** with
   `route_rip_failure_to_review(job_id, t.id, "rip_ejected", EJECTED_RIP_MESSAGE)`.
4. **Falls through to the normal post-rip flow:** backfill, reconcile, matching
   dispatch, organize. The job settles in `REVIEW_NEEDED`.

#### Ordering constraint (highest-risk detail)

**Step 3 must run before `reconcile_stuck_titles`.**

`reconcile_stuck_titles` marks any selected `PENDING`/`RIPPING` title **with no
file** as `FAILED`. Because the truncated in-flight file is deleted by the abort, a
half-ripped track and a never-started track are indistinguishable to it: both are
fileless. If reconcile runs first, every unfinished track lands `FAILED` and
non-re-rippable, which is the exact opposite of D1.

This ordering gets a dedicated regression test.

### New error code

`RIP_FAILURE_ERROR_CODES` (`app/services/matching_coordinator.py`) gains
`"rip_ejected"`. That one line buys three behaviors:

- the title becomes `rerip_eligible` for the existing single-track re-rip flow;
- it is excluded from auto re-match via `_NON_REMATCHABLE_REVIEW_ERRORS`, which is
  derived from that set;
- `_reset_review_titles_on_identity_change` leaves its re-rip bookkeeping intact.

A distinct code (rather than reusing `rip_stalled`) also lets the review UI say
"disc ejected" instead of falsely reporting a stall. Registering it is mandatory,
not cosmetic: an unregistered REVIEW error code gets auto-escalated and has its
`match_details` overwritten by the re-match pass.

### API

`POST /api/jobs/{job_id}/eject`, alongside the existing cancel route in
`app/api/routes.py`.

Response: `{"ejected": bool, "action": "rip_stopped" | "job_cancelled"}`.
`ejected` reports whether the tray actually opened, so the UI can surface the
"eject manually" message on failure.

`JobManager.eject_disc_for_job(job_id)` branches on job state per D2. In the
`RIPPING` case it returns as soon as the abort and eject are issued. The in-flight
`_run_ripping` task performs the salvage on its own; the endpoint does not await it.

### Frontend

An **Eject** button in `DiscCard/ActionButtons.tsx`, shown for
`state ∈ {"scanning", "ripping"}`, built from the existing `ToneButton` and
`ConfirmModal` primitives. Requires a new `IcoEject` in
`app/components/icons/action.tsx`.

Visual treatment (tone, since it is neither destructive-red nor routine-cyan; icon
form; labeled vs. icon-only) is specified by the frontend-design pass in the
implementation plan rather than guessed here.

`useJobManagement` gains `ejectJob(id)`; the API client gains the call. No new
WebSocket message type is needed: existing `job_update` and `title_state_changed`
traffic already carries the outcome.

## Edge cases

| Case | Behavior |
|------|----------|
| Eject with zero finished tracks | All tracks to `REVIEW`, job `REVIEW_NEEDED`, not `FAILED`. Same shape as the existing `disc_unreadable` branch. |
| Eject races the last title finishing | Nothing unfinished to route; job completes normally. Benign. |
| Eject during the per-title fallback pass | Same handling, because that pass's `rip_titles` call sees the same flag. |
| `auto_eject_enabled = false` | Ignored by the manual button. That config governs *automatic* post-rip eject; an explicit click is not that. |
| Eject fails (tray held, headless Linux) | Rip still stopped, salvage still runs, `notify_ejected` **not** called, UI shows manual-eject message. |
| Simulation (`DEBUG=true`) | `simulation_service` gains an eject path so E2E can drive it without a real drive, matching its existing `SKIPPED` special-casing. |

## Testing

**Unit**

- `eject_abort` sets the flag and terminates the subprocess.
- `RipResult.aborted_for_eject` returns `success=True` with finished files.
- The truncated in-flight file is deleted.
- `"rip_ejected"` is a member of `RIP_FAILURE_ERROR_CODES`.
- **Ordering guard:** a job ejected mid-rip leaves never-started titles in
  `REVIEW` and `rerip_eligible`, **not** `FAILED`.
- Eject during `IDENTIFYING` cancels the job; eject during `MATCHING` returns 409.
- A failed `eject_disc()` still stops the rip and does not call `notify_ejected`.

**Integration**

- Full simulated eject-mid-rip: finished tracks organize, unfinished park in
  review, job ends `REVIEW_NEEDED`.

**E2E**

- Button visible during rip, confirm modal appears, card lands in review.

**Frontend unit**

- Button visibility per state; confirm-then-call wiring.

## Success criteria

1. Clicking Eject during a rip stops MakeMKV within seconds, with no
   stall-timeout wait.
2. The job is not cancelled and staged output is not deleted.
3. Every cleanly-ripped track matches and organizes normally.
4. Every unfinished track is in review, re-rippable after reinserting the disc.
5. A failed tray-open degrades to a clear message, never a lost job.
