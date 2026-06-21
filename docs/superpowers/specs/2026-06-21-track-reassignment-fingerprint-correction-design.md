# Post-Completion Track Reassignment with Fingerprint Correction

**Date:** 2026-06-21
**Status:** Design — pending review
**Motivating incident:** Breaking Bad S3 D2 (job 214) had a 56-minute featurette (title 2265) erroneously
organized as `S03E10.mkv`. This blocked disc 3 (job 221, title 2440 — the *real* E10) with a `file_exists`
conflict. There was no way to correct the completed job, and no way to retract a fingerprint from the shared
network once contributed.

## Problem

1. Once a job reaches `COMPLETED`, track assignments are locked. `reassign_episode()` explicitly blocks
   `COMPLETED`/`ORGANIZING`/`FAILED` states ([job_manager.py:1509](../../../backend/app/services/job_manager.py)).
2. A wrong assignment leaves a mis-organized library file that can collide with a later, correct disc.
3. If the wrong track was contributed to the fingerprint network, there is no per-fingerprint retraction.
   The only tool is `POST /fingerprint/forget`, which wipes **all** contributions for a pseudonym, and the
   server comment claims canonical fingerprints "can't be recalled."

For the correctness of the fingerprint network, users must be able to (a) reassign a track after the fact,
(b) retract the erroneous fingerprint, and (c) contribute the corrected one.

## Key findings that shape the design

- **The server's promotion is fully re-derivable.** `runPromotion` ([engram-fingerprint-server/src/workers/promotion.ts](../../../../engram-fingerprint-server/src/workers/promotion.ts))
  loads *all* raw contributions for an episode, dedupes to one-vote-per-pseudonym, and recomputes the
  canonical fingerprint and tier from scratch. So "fix the network" = **delete the bad raw row + re-run
  promotion for that episode**. The "canonical can't be recalled" limitation is only because `/forget`
  never re-promotes — not a fundamental constraint.
- **The retraction key must be the exact fingerprint, never the episode.** A good E10 and a bad E10 are both
  `(tmdb_id, season, episode)` and differ only by `fingerprint_sha256`. The server's dedup key is
  `(pseudonym, tmdb_id, season, episode, fingerprint_sha256)`; retraction targets that exact tuple. The
  client's clean handle is the `FingerprintContribution.title_id` link.
- **`user_review` is already a first-class, highest-trust `match_source`** in the server allowlist. A user
  correction is the strongest signal the network can receive.
- **In the live incident the network was never poisoned.** Title 2265 has `match_source=None` and produced
  no `FingerprintContribution` row, so the live cleanup needs no network round-trip — but the feature must
  still implement full retraction for the general (ASR-matched) case.

## Architecture: in-place amendment (Approach B)

A completed job is treated as an immutable historical record being *corrected in place*. The job stays
`COMPLETED` throughout; we never re-enter the job state machine. Three shared units:

1. Server `POST /v1/retract` endpoint.
2. Client `ContributionCorrectionService` (retract old fingerprint, re-contribute new).
3. Client `organizer.amend_organized_file` (move/rename the organized library file).

Wired together by a new `JobManager.amend_title_assignment()` behind a new REST endpoint, surfaced in the
History detail panel.

### 1. Server: `POST /v1/retract`

Request (`engram-fingerprint-server`):

```json
{ "wire_format_version": 1, "pseudonym": "...", "tmdb_id": 1396, "season": 3, "episode": 10,
  "fingerprint_sha256_b64": "..." }
```

Behaviour:

1. `DELETE FROM contribution WHERE pseudonym=? AND tmdb_id=? AND season=? AND episode=? AND
   fingerprint_sha256=?` (cascades `overlap_observation`). The `pseudonym` predicate enforces that a caller
   can only retract its own contributions — identical trust model to `/forget`.
2. Heal canonical by re-derivation for that `(tmdb_id, season, episode)`:
   - If contributions remain, set their `promoted_at = NULL` so the promotion cron recomputes consensus
     without the retracted vote.
   - If none remain, delete the `episode_canonical` and `canonical_sketch` rows.
3. Idempotent: a missing row returns `200` with `deleted: 0`.

Response: `{ "deleted": <int>, "canonical": "requeued" | "removed" | "untouched" }`.

Schema/validation mirrors `contribute.ts` (zod schema in `src/schemas.ts`, route registered in
`src/index.ts`). New unit tests under `test/`.

**Scope cut:** v1 retracts *episode* fingerprints only. Disc-layout (`disc_contribution`) correction is a
follow-up — it is lower-stakes, re-derivable, and self-heals when the disc is next contributed with a
different `titles_digest`.

### 2. Client: `ContributionCorrectionService`

New service in `backend/app/services/contribution_correction.py`:

```python
async def correct_title_contribution(session, title, *, new_target) -> CorrectionResult
```

- Find local `FingerprintContribution` rows by `title_id`.
- **Pending row** (`upload_status != "success"`): delete the local row, never touch the network (reuses the
  existing `DELETE /fingerprint/contributions/{id}` logic path).
- **Uploaded row** (`upload_status == "success"`): POST `/v1/retract` with the row's stored
  `{pseudonym, tmdb_id, season, episode, uploaded_fingerprint_sha256}`. On success, delete the row and write
  a `contribution_log.jsonl` audit line. On transient failure, mark the row `retraction_status="pending"`
  and let the drain loop retry.
- **Re-contribute:** if `new_target.kind == "episode"`, enqueue a fresh `FingerprintContribution` from
  `title.chromaprint_blob` with the new `(tmdb_id, season, episode)`, `match_source="user_review"`,
  `match_confidence=1.0`. For `extra`/`discard`, re-contribute nothing.

### 3. Client data model changes

`FingerprintContribution` ([backend/app/models/fingerprint.py](../../../backend/app/models/fingerprint.py)):

- Add `uploaded_fingerprint_sha256: bytes | None` — persisted at upload time so retraction targets the exact
  bytes the server holds (avoids reserialization drift between contribute and retract).
- Add `retraction_status: str | None` (`None` | `"pending"` | `"done"`) and `retracted_at: datetime | None`
  for the retry queue.

Drained by the existing `ContributionUploader` loop (same two-phase pattern as contributions) — a pending
retraction is retried on each drain until the server acknowledges.

Schema convergence: add columns via `database.py` reconcilers (`_add_missing_columns`) so frozen builds pick
them up, plus an Alembic migration for dev parity. (Frozen builds skip Alembic; the reconciler is what
reaches users.)

### 4. Client: organizer amendment helper

New `amend_organized_file(current_path, target, *, show, season, ...)` in
[backend/app/core/organizer.py](../../../backend/app/core/organizer.py), reusing existing format/move/conflict
helpers:

- **→ episode:** rename the library file to the new episode path
  (`format_episode_filename`). If the target path already exists, **abort with a structured conflict error**
  (`error="file_exists"`) — never silently overwrite.
- **→ extra** and **→ discard:** move the file into the show's `Extras/` folder using the
  `organize_tv_extras` naming convention. (Per decision, discards are not deleted and not given a separate
  quarantine directory; they land in `Extras/` and differ from a legitimate extra only as a record/UI intent
  flag.)

Returns `{ success, final_path, error }`, matching the existing organizer contract.

### 5. Client: endpoint + orchestration

`POST /api/jobs/{job_id}/titles/{title_id}/amend`

```json
{ "target": { "kind": "episode" | "extra" | "discard", "episode_code": "S03E10" } }
```

Allowed only when `job.state == COMPLETED`. `JobManager.amend_title_assignment()` runs in order:

1. Validate the target (parse `episode_code`; confirm the title belongs to the job).
2. Move/rename the organized library file via `amend_organized_file`. Abort on a real conflict or missing
   source file with a clear error.
3. Update the `DiscTitle`: `matched_episode`, `is_extra`, `organized_to`/`organized_from`,
   `match_source="user"`, `match_confidence=1.0`, strip stale review-reason keys, set a `discarded` marker for
   the discard kind.
4. Fire `ContributionCorrectionService.correct_title_contribution` (best-effort, independently retryable).
5. Broadcast `title_update` + `job_update` over WebSocket.

Ordering rationale: file + DB correctness (user-visible) commits first; network hygiene is retryable so a
server hiccup never blocks the local fix.

Error handling:

- Target episode path already occupied → abort, surface "target occupied; resolve that conflict first."
- Source library file missing (user moved/deleted it) → abort with a clear error.
- `chromaprint_blob` absent on the title (older job) → skip re-contribution with a logged note.

### 6. Frontend: History detail panel

In the `HistoryPage` slide-out detail panel, add a per-track **Reassign** action. It opens a small modal:

- Pick an episode (season episode dropdown), **Mark as Extra**, or **Discard (move to Extras)**.
- When the track has an uploaded fingerprint, show a one-line note: *"This will retract the previous
  fingerprint from the shared network and submit your correction."*
- On submit → `POST .../amend` → optimistic update reconciled by the `title_update`/`job_update` WS messages.

No reassignment UI is added for jobs already in `REVIEW_NEEDED` — that path already exists in `ReviewQueue`.

### 7. Live cleanup of jobs 214 / 221 (final dogfood step)

Run after the feature is built and tested, against the real running backend:

1. Amend job 214 / `title 2265` → **Extra**. Moves `X:\...\Season 3\Breaking Bad - S03E10.mkv` into
   `Season 3\Extras\`, clears the episode assignment. No network retraction (the track was never contributed).
2. Job 221 / `title 2440` (the real E10) is in `REVIEW_NEEDED` with `file_exists`. With the path now free,
   resolve it through the **existing review flow** (reassign/confirm E10) so it organizes cleanly.
3. Verify: `Season 3` holds correct `S03E06`–`S03E13`, the disc-2 featurette sits in `Extras/`, and the
   network's E10 vote (contrib 1813, the disc-3 fingerprint) is untouched and correct.

## Testing

- **Server (`engram-fingerprint-server/test/`):** `/v1/retract` — deletes the targeted row, requeues
  promotion when votes remain, removes canonical + sketch when none remain, is idempotent, and rejects/ignores
  cross-pseudonym deletion.
- **Client unit:**
  - `ContributionCorrectionService` — pending row deletes locally with no network call; uploaded row calls
    retract then deletes; episode target re-contributes as `user_review`; extra/discard re-contribute nothing.
  - `amend_organized_file` — episode rename, extra move, discard→Extras, conflict abort, missing-source abort.
  - `uploaded_fingerprint_sha256` persisted at upload and reused verbatim by retraction.
- **Client integration (`tests/integration/`):** amend endpoint end-to-end via simulation — completed TV job,
  amend a track to a different episode, assert file moved + `DiscTitle` updated + WS broadcast + correction
  enqueued. Amend-to-extra and amend-twice (idempotency) cases.

## Out of scope (v1)

- Disc-layout (`disc_contribution`) correction.
- Cross-show reassignment (wrong `tmdb_id`) — the heavy cross-namespace retraction case.
- Cross-job auto-resolution (fixing job A automatically re-attempting blocked job B). The freed path lets the
  user resolve the blocked job through the normal flow.
- Movie reassignment after completion (this design targets TV episodes; movie amendment can reuse the same
  scaffolding in a follow-up).
