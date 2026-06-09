# Single-Track Re-Rip After Clean & Reinsert (Feature C) — Design

**Status:** Approved design (2026-06-09). Ready for implementation planning.

**Depends on:** PR #366 (truncated-rip fast-fail) and PR #369 (per-disc `content_hash` at
insert + `_same_disc` dedup), both merged to `main`. This branch is rebased on top of #369.

**Scope:** Let a user recover a single damaged track without re-ripping the whole disc. A
rip-level failure routes the track to review; cleaning and reinserting the **same** disc
(verified by `content_hash`) auto re-rips just the failed track(s), re-matches, and re-checks
job completion.

---

## 1. Motivation

When MakeMKV aborts a title mid-rip on an uncorrectable read error (a scratch/bad sector),
PR #366 now recognizes the truncated-but-stable file within ~90 s and routes that title to
**review** instead of wedging the job for hours. But the only recovery is to re-rip the
**entire disc** as a fresh job. The damaged track is usually one of several; re-ripping all of
them wastes time and drive wear.

Feature C closes the loop: surface a clear "clean the disc and reinsert to re-rip this title"
affordance, and on reinsert of the **same physical disc** (confirmed by the per-disc
`content_hash` from PR #369) re-rip only the failed title index, overwrite the truncated
staging file, re-match that one title, and re-evaluate completion.

---

## 2. Guiding principle (completion semantics)

**COMPLETED means every title succeeded.** This single rule shapes the whole design:

- Any **rip-level** failure (truncated *or* stall/error) routes the title to
  `TitleState.REVIEW`, **not** `TitleState.FAILED`. The job holds in `REVIEW_NEEDED`
  ("a damaged title needs your attention") while the good titles still organize.
- A job reaches `COMPLETED` only once the damaged title is genuinely resolved: a re-rip
  succeeds and it matches, **or** the user explicitly skips it (deliberate "give up on this
  title" → the existing skip action, after which the job can complete).
- Because rip failures never land in a **terminal** job, Feature C **never needs to reopen**
  `COMPLETED`/`FAILED` (which are terminal in `JobStateMachine.VALID_TRANSITIONS`). One clean
  `REVIEW_NEEDED → RIPPING` path covers every re-rip.

Behavior change this introduces: today a disc with 4 good titles + 1 rip-stalled title
auto-completes (good titles organized, stalled one `FAILED`). After this change it holds in
`REVIEW_NEEDED` until the user re-rips or skips the damaged title. This is more honest (the
disc isn't fully done) but means a permanently-dead title needs an explicit skip to clear — so
the review UI must always offer **both** "re-rip" and "skip".

Scope guard: **only rip-level failures** (truncated/stall/error) are rerouted. Match-level
outcomes and deliberate user skips are untouched.

---

## 3. Trigger marking — rip failures become identifiable & non-terminal

A small `match_details` error-code namespace for **rip-level** failures, all routed to
`TitleState.REVIEW`:

| Code | Source today | Change |
|------|--------------|--------|
| `incomplete_rip` | Truncated rip (#366) routes via `_handle_match_failure`, which mislabels it `error: "matching_task_failed"` | Route through a new shared helper that writes `{"error": "incomplete_rip", "message": …, "rerip_eligible": true}` |
| `rip_stalled` | Two stall sites mark `FAILED` with `{"reason": …}`: `_on_title_error` (`job_manager.py:2130`) and the fallback block (`job_manager.py:1775`) | Route to `REVIEW` with `{"error": "rip_stalled", "message": …, "rerip_eligible": true}` |

**Shared routing helper.** Add a single helper (in `MatchingCoordinator`, alongside
`_handle_match_failure`) that, given `(job_id, title_id, error_code, message)`, transitions an
**active or review** title to `REVIEW`, writes the structured `match_details`, broadcasts the
title update, and runs `check_job_completion`. The truncated branch of
`_handle_file_wait_result` and both stall sites call it. This removes the current mislabeling
of truncated rips as `matching_task_failed`.

**Retry counter.** New column `DiscTitle.rerip_attempts: int = 0` (default 0).
- Frozen builds converge via `database.py` `_add_missing_columns` (ALTER TABLE ADD COLUMN);
  add the matching Alembic migration for dev parity (frozen builds skip Alembic — the
  reconciler is what reaches users).

**Auto-re-rippable predicate.** A title is eligible for **auto** re-rip when:
`state == REVIEW` ∧ `match_details.error ∈ {incomplete_rip, rip_stalled}` ∧
`rerip_attempts < RERIP_MAX_ATTEMPTS` (module constant, `2`).

---

## 4. Reinsert interception (the heart) — in `_create_job_for_disc`

The drive sentinel delivers disc inserts to `JobManager._create_job_for_disc`
(`job_manager.py:478`), which already computes `new_hash = await self._compute_disc_hash(...)`
under the per-drive lock (PR #369). Feature C hooks **immediately after** the hash probe and
**before** the dedup/blocking predicate:

```
disc inserted → sentinel → _create_job_for_disc (per-drive lock)
  → new_hash = _compute_disc_hash(drive)
  → rerip_job = await _find_rerip_job(new_hash)            # NEW
      ├─ found → spawn rerip_titles(job_id, eligible_title_ids); return
      └─ none  → existing _same_disc dedup + new-job creation (unchanged)
```

**`_find_rerip_job(new_hash) -> (job_id, [title_id]) | None`** (new): when `new_hash` is
non-null, find a job in `REVIEW_NEEDED` with `content_hash == new_hash` that has ≥1
auto-re-rippable title (§3). Return its id + eligible title ids. Hash match is the gate — a
different disc (different hash, or null) never triggers a re-rip. Scoped to `REVIEW_NEEDED`
so we never race a job that's still actively `MATCHING` other titles.

**Why here, not a separate sentinel callback (chosen vs. alternative):** the insert already
arrives here with the hash computed under the dedup lock. A separate callback would duplicate
the hash probe and race the same lock. Hooking in-place reuses both.

The 15 s creation cooldown (`_last_job_created_at`) is irrelevant — a clean-and-reinsert is
far longer than 15 s — but the interception returns before the cooldown/creation path anyway.

---

## 5. Re-rip orchestration — `rerip_titles(job_id, title_ids)` (new, JobManager)

A focused operation (distinct from the full-disc rip flow) that reuses the existing
rip→match→complete machinery:

1. Transition **job `REVIEW_NEEDED → RIPPING`** (valid transition; also makes any spurious
   sentinel re-fire an unconditional `disc_required` block during the re-rip).
2. For each eligible title (in `title_index` order):
   - `state REVIEW → RIPPING`; `rerip_attempts += 1`; broadcast title update.
   - **Delete the stale staging file** (`title.output_filename`, guarded against `OSError` /
     missing) so MakeMKV writes a fresh file instead of a numbered duplicate.
3. `extractor.rip_titles(drive_id, staging_dir, title_indices=[…], title_complete_callback=…,
   title_error_callback=…, stall_timeout=…, log_dir=…, job_id=job_id)`.
   - **`title_complete_callback` → `_on_title_ripped(job_id, rip_index, path, subset)`** — it
     resolves the title from the ripped **filename** and re-spawns `match_single_file`, so it
     works unchanged for a re-rip.
   - **`title_error_callback` → the §3 shared helper** (reroute to `REVIEW` /
     `rip_stalled`, attempts already incremented). It must map `cmd_idx` against the **re-rip
     subset in command order**, not the job's full sorted title list (`_on_title_error`'s
     `sorted_titles[cmd_idx-1]` is only correct when the subset *is* the command list).
4. Completion is the existing path: `_on_title_ripped → match_single_file →
   check_job_completion` → `COMPLETED` (now all-good) or back to `REVIEW_NEEDED` (re-rip
   failed again, `rerip_attempts` higher).
5. Eject the disc + `notify_ejected` when the re-rip finishes (mirrors the normal flow), so a
   subsequent reinsert is detected.

Run as a background task spawned from `_create_job_for_disc` (mirrors how identification is
spawned), tracked in `_active_jobs`, with the job-log context wrapper.

---

## 6. Failure / retry handling

- Re-rip fails again → title back to `REVIEW` (`incomplete_rip`/`rip_stalled`),
  `rerip_attempts` incremented. Once `rerip_attempts >= RERIP_MAX_ATTEMPTS` the **auto**
  trigger skips it (`_find_rerip_job` excludes it); the review UI message flips to "still
  damaged after N tries — clean again, replace the disc, or skip this title."
- **Manual fallback** — `POST /api/jobs/{job_id}/titles/{title_id}/rerip`: re-rip on demand
  using the disc currently in the drive. Verifies the inserted disc's `content_hash` matches
  the job's (rejects on mismatch or no disc, with a clear error). **Bypasses the cap** (the
  user explicitly asked). Powers both the always-available review button and the post-cap
  path. Internally calls the same `rerip_titles` core.

---

## 7. Frontend

- **ReviewQueue** (`frontend/src/components/ReviewQueue.tsx`): a damaged-track card variant
  when `match_details.error ∈ {incomplete_rip, rip_stalled}` — distinct icon + copy:
  "Clean the disc and reinsert to re-rip this title (automatic), or skip it." Shows the
  attempt count and the cap-reached message. A manual **"Re-rip this title"** button calls the
  §6 endpoint. Skip reuses the existing skip action so the track is never a dead end.
- **DiscCard** (`frontend/src/app/components/DiscCard.tsx`): a damaged-track indicator so the
  needs-attention state is visible on the dashboard, not only inside the review queue.
- **Live updates:** re-rip progress flows through the existing `title_state_changed` /
  `rip_progress` / `job_update` WebSocket events (job → `RIPPING`, title → `RIPPING`, then the
  normal match events). No new WS message type required.

---

## 8. Error handling & edge cases

- **Different disc** (hash mismatch or null hash) → `_find_rerip_job` returns `None` → normal
  dedup/new-job flow. A re-rip is never triggered for a disc we can't positively fingerprint.
- **Reinsert while the job is still `MATCHING`** other titles → not yet `REVIEW_NEEDED`, so no
  interception; the existing `_same_disc` predicate blocks the duplicate. The user reinserts
  again once the job settles to `REVIEW_NEEDED`. (Accepted: a one-time "reinsert again after it
  settles" rather than queuing a re-rip against a busy drive.)
- **Cleared job** (`cleared_at` set) with a `REVIEW` title → still re-rippable; a re-rip clears
  `cleared_at` so the job reappears on the dashboard while it re-processes.
- **All titles unreadable** → every title `REVIEW` → job `REVIEW_NEEDED` ("clean & reinsert"),
  not `FAILED` — a recoverable dead end rather than a terminal one.
- **Manual re-rip with the wrong disc in the drive** → hash check fails → endpoint returns a
  clear error; nothing rips.

---

## 9. Testing

- **Unit:**
  - Trigger rerouting: truncated → `incomplete_rip` + `REVIEW`; both stall sites →
    `rip_stalled` + `REVIEW`; structured `match_details`; `check_job_completion` invoked.
  - `rerip_attempts` increment + cap; the auto-re-rippable predicate truth table.
  - `_find_rerip_job`: hash match + eligible title ⇒ returns; hash mismatch / null ⇒ `None`;
    job in `MATCHING` ⇒ `None`.
  - `rerip_titles` with a **mocked extractor**: stale file deleted; job/title transitions;
    completion re-checked; re-fail increments attempts and returns to `REVIEW`.
  - Completion-semantics change: a stalled title holds the job in `REVIEW_NEEDED` (regression
    on the old auto-complete behavior — update affected stall/completion tests).
- **Integration:** seed a job with a `REVIEW` `incomplete_rip` title directly in the DB and
  exercise the manual `rerip` endpoint with a mocked extractor + mocked hash.
- **Note:** `/api/simulate/insert-disc` bypasses `_create_job_for_disc` (it injects jobs
  directly), so the reinsert interception is **unit-tested with mocked hash/extractor**
  (consistent with the #369 dedup tests), not via the E2E simulation harness.
- **Frontend E2E:** add a DEBUG-only simulation helper to seed an `incomplete_rip` review
  title and drive a re-rip completion, then assert the damaged-track affordance + the post-rip
  matched state in the UI.
- **Lint/format:** `uv run ruff check` / `ruff format` clean; backend `uv run pytest` green
  (modulo the known `test_movie_ambiguous_rip_first_workflow` flake); frontend `npm run build`
  + `npm run lint`.

---

## 10. Out of scope (follow-ups)

- Reopening genuinely-terminal `COMPLETED`/`FAILED` jobs — the §2 completion rule means rip
  failures never reach a terminal job, so this is unnecessary.
- Promoting `RERIP_MAX_ATTEMPTS` / messages to `AppConfig` (module constants for now — YAGNI;
  avoids the config three-way-sync surface).
- DiscDB ungating (`FEATURES.DISCDB` / `DISCDB_ENABLED`) — independent decision.
- Part B of the truncated-rip plan (real ripped-file size in the UI) — separate plan.
