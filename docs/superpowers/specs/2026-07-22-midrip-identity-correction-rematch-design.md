# Mid-rip identity correction must re-match already-ripped titles

**Date:** 2026-07-22
**Status:** Design approved, pending spec review

## Problem

The "match at any time" / "correct a title mid-rip" control shipped in 0.26 (#520)
lets a user set or correct a disc's identity while it is still `RIPPING`. Users
report that naming/correcting the show mid-rip **updates the metadata but never
episode-matches the disc**. Only after the rip finishes, when the "match show"
prompt reappears, does answering it actually identify episodes.

### Root cause

The mid-rip and post-rip identity-answer paths resume matching differently:

| When answered | `resume_action` | Behavior |
|---|---|---|
| Mid-rip (`RIPPING`) | `dispatch_matches` | `dispatch_pending_matches()` — dispatches **only `QUEUED` titles** |
| Post-rip (`re_identify` on `REVIEW_NEEDED`) | `rerun_matching` | `_rerun_matching()` — resets **every** selected title to `QUEUED` and re-matches |

`dispatch_pending_matches` ([job_manager.py:3298]) skips any title not in `QUEUED`:

```python
if t.state != TitleState.QUEUED or not t.output_filename:
    continue
```

On a disc that was **confidently (mis)identified** there is no blocking identity
prompt, so titles are dispatched and matched (or sent to `REVIEW`) under the wrong
show as they rip. A mid-rip correction then re-dispatches **nothing** — those
titles are already `MATCHED`/`REVIEW`/`MATCHING`, not `QUEUED`. The correction's
metadata update "takes" but no re-match happens. Only the post-rip path
(`rerun_matching`) resets and re-matches everything, which is why "wait until it
finishes ripping, then the prompt works" is the observed workaround.

**Why existing tests miss it:** the mid-rip unit test seeds titles already
`QUEUED`; the walk-away integration test uses simulation, which never runs real
`match_single_file` (fake filenames don't exist, so dispatch skips them). Neither
exercises an already-matched title corrected mid-rip. The bug was reproduced with
a failing unit test: a `MATCHED` title + mid-rip `re_identify_job` →
`dispatched=[]`, `rerun_matching=[]`.

### Second facet: stale subtitle download

Mid-rip `re_identify` skips the subtitle-download **restart**
(`should_restart_subtitles = not mid_rip and …`, [identification_coordinator.py:1471])
and instead calls `_start_tv_subtitle_prefetch`, which *replaces* the
`_subtitle_ready` event **without cancelling the old show's in-flight download**.
So even titles that do re-dispatch can match against stale/wrong-show references.

## Goals

1. A mid-rip identity **change** re-matches every already-ripped title against the
   corrected show, mirroring the working post-rip `rerun_matching` behavior — but
   rip-safe (must not touch titles still being written).
2. A mid-rip correction restarts the subtitle download for the corrected show,
   cancelling the stale one.
3. A mid-rip answer that does **not** change the identity keeps today's cheap
   behavior (release parked `QUEUED` titles only; preserve good in-flight matches).

## Non-goals

- Changing the post-rip (`rerun_matching`) path — it already works.
- Movie mid-rip correction re-matching. Movies don't episode-match; the rip-end
  movie tail re-resolves with the new `detected_title`. Movie mid-rip keeps
  `release_movie_titles`.
- The blocking-prompt (`name`/`reidentify`) parked-disc flow, which already works
  (titles are held in `QUEUED` and released by `dispatch_matches`).

## Design

### 1. Change detection — `IdentificationCoordinator`

Both `set_name_and_resume` and `re_identify` load the job before mutating it.
Capture the pre-answer identity and compare to the post-answer values.

**"Identity changed" ≡ `detected_title` OR `tmdb_id` differs** from the pre-answer
values (a genuine *show* change, including twin resolution like Frasier 1993 →
2023 where the title is unchanged but `tmdb_id` moves). A **season-only** change
is deliberately excluded from the trigger: pinning the season on a non-blocking
`season`-prompt disc (whose titles are already matching correctly across all
seasons) must not tear those matches down. A season change that rides along with a
show change still triggers via the show change.

- **Mid-rip + show changed (TV)** → return `resume_action = "rematch_ripped"`.
- **Mid-rip + show unchanged (TV)** → keep `"dispatch_matches"` (releases parked
  `QUEUED` titles; season-only refinement lands here).
- Movie mid-rip → unchanged (`"release_movie_titles"`).

`"rematch_ripped"` is added to the `ResumeAction` `Literal` in
`app/services/identity_prompts.py`.

Note: `set_name_and_resume` handles the `name`/`season` prompts, whose discs park
behind a blocking prompt (titles held in `QUEUED`), so its mid-rip titles are
normally already `QUEUED` and the "changed" branch is usually a no-op there. The
detection is applied uniformly to both endpoints for robustness; `re_identify` is
where the change actually bites.

### 2. Re-match executor — `JobManager`

New `_rematch_ripped_titles(job_id)`, wired into `_apply_identity_resume_action`
under `action == "rematch_ripped"`. Rip-safe and scoped:

- Selects selected titles whose **rip is done**: `output_filename` set, file
  exists, state ∈ {`QUEUED`, `MATCHING`, `MATCHED`, `REVIEW`}.
- Leaves `RIPPING`/`PENDING`/`SKIPPED` untouched — they dispatch under the new
  identity when they finish via the existing `_on_title_ripped` gate (the prompt
  is already cleared by the time this runs).
- For each affected title, **cancels any in-flight match task** (see §4) so a
  stale match under the old identity cannot finalize over the corrected one.
- Resets each to `QUEUED`; clears `matched_episode`, `match_confidence`,
  `match_details`; discards the `_inflight_match_dispatch` guard.
- Commits, then re-dispatches each via `_dispatch_title_match`.

Content-type guard: like `dispatch_pending_matches`, return early (log) for a
non-TV job so a misrouted caller can't episode-match movie titles.

### 3. Subtitle restart — `IdentificationCoordinator`

Change the mid-rip branch (in `re_identify`, and `set_name_and_resume` for
symmetry) so that when the season is known it calls `restart_subtitle_download`
(cancels the stale task, clears `subtitle_status`, re-emits progress) instead of
`_start_tv_subtitle_prefetch`. When the season is unknown, cancel the old task
first, then run the all-seasons prefetch. This runs outside the session block
(the restart opens its own session), matching the existing post-rip ordering.

### 4. In-flight match-task tracking + cancellation — `JobManager` (Option A)

- Add `self._match_tasks: dict[int, asyncio.Task]` to `JobManager`.
- In `_dispatch_title_match`, record the spawned task keyed by `title_id`; remove
  the entry in the done-callback (`_on_match_dispatch_done`).
- `_rematch_ripped_titles` cancels the tracked task for each affected title before
  reset + re-dispatch.

Caveat (documented, pre-existing pattern): a task blocked in `asyncio.to_thread`
running ASR observes cancellation only at its next `await`; the worker thread
finishes its current chunk (seconds) before unwinding. This matches the caveat
already documented for `_prewarmer.cancel_for_job`. The fresh re-dispatch owns the
title regardless; the cancelled task's `_inflight_match_dispatch`/`_match_tasks`
cleanup is idempotent.

## Data flow

```
User corrects identity mid-rip
  → POST /api/jobs/{id}/re-identify   (job.state == RIPPING)
  → IdentificationCoordinator.re_identify
       captures old identity tuple; applies new; clears prompt; commits
       identity changed + TV → resume_action = "rematch_ripped"
       subtitle restart (cancel stale download) outside session
  → JobManager.re_identify_job
  → _apply_identity_resume_action("rematch_ripped")
  → _rematch_ripped_titles(job_id)
       cancel in-flight match tasks for ripped titles
       reset ripped titles → QUEUED, clear stale match fields
       re-dispatch each via _dispatch_title_match
  (titles still RIPPING dispatch under new identity on completion)
```

## Testing (TDD)

Write the failing test first (already confirmed to fail), then implement.

Unit (`tests/unit/`):
1. **Repro / core:** confidently-identified TV disc, one title `MATCHED` under the
   wrong show; `re_identify_job` mid-rip with a changed identity → title reset to
   `QUEUED` and re-dispatched to `match_single_file`. (Fails today.)
2. **Show unchanged mid-rip** (same title+tmdb, e.g. season-only pin) → no
   teardown; resume_action stays `dispatch_matches`; in-flight match preserved.
3. **Rip-in-progress titles preserved:** `RIPPING`/`PENDING` titles are not reset
   by `_rematch_ripped_titles`.
4. **In-flight cancellation:** a `MATCHING` title's tracked task is cancelled and
   the title re-dispatched.
5. **Subtitle restart:** mid-rip show change invokes `restart_subtitle_download`
   (stale task cancelled); unchanged identity does not.
6. **Movie mid-rip** correction still routes to `release_movie_titles`.
7. **Regression:** blocking-`name`-prompt parked disc still dispatches via
   `dispatch_matches` (parked `QUEUED` titles), unchanged.

Integration (`tests/integration/`): extend `test_walk_away_workflow.py` or add a
sibling covering a mid-rip correction that reaches matching for a previously
mis-matched title (using the realistic path, not pure simulation, so
`match_single_file` is actually exercised — stub the matcher to record dispatches).

## Risks

- **In-flight match races.** Mitigated by task cancellation (§4) + fresh
  re-dispatch owning the title; cleanup is idempotent.
- **Resetting a title mid-organize.** Excluded by scoping to
  {`QUEUED`,`MATCHING`,`MATCHED`,`REVIEW`} with an existing file — `ORGANIZING`/
  `COMPLETED`/`SKIPPED` are left alone.
- **Double subtitle downloads.** Mitigated by `restart_subtitle_download`
  cancelling the prior task before starting the new one.

## Files touched

- `backend/app/services/identity_prompts.py` — add `"rematch_ripped"` to `ResumeAction`.
- `backend/app/services/identification_coordinator.py` — change detection +
  subtitle restart in the mid-rip branches of `re_identify` / `set_name_and_resume`.
- `backend/app/services/job_manager.py` — `_match_tasks` map, `_dispatch_title_match`
  recording, `_rematch_ripped_titles`, `_apply_identity_resume_action` wiring.
- `backend/tests/unit/…` and `backend/tests/integration/…` — tests above.
- `CHANGELOG.md` — `[Unreleased] / Fixed` entry.
