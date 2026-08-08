# Import re-import recovery (issue #571)

**Date:** 2026-08-04
**Status:** Approved design, ready for planning
**Scope:** The manual-import dedup guard, the `/api/import/start` conflict contract,
the ImportModal conflict panel, and the failed-job dismissal gap on the dashboard.

## Problem

[Issue #571](https://github.com/Jsakkos/engram/issues/571) reports that a mistaken
import cannot be undone or retried. The reporter imported a Season 3 folder that was
identified as Season 1, and then could not delete, reset, or retry it. Their only
escape was to rename the source folder repeatedly so Engram would treat it as new.

The issue reads as eight feature requests. It is one bug plus two pre-existing gaps.
Two of the eight requests already ship.

### Root cause

`JobManager.create_job_from_staging` refuses to create a job when any prior job for
the same staging path is not `FAILED` (`backend/app/services/job_manager.py:806`):

```python
existing = await session.execute(
    sa_select(DiscJob).where(
        DiscJob.staging_path == str(staging_dir),
        DiscJob.state != JobState.FAILED,
    )
)
```

`git log -L` attributes the surrounding block to `b0312a78 feat: manual media import
(replace watch folder) (#463)`. The guard predates that commit. When a *watch folder*
was polled every few seconds, "never re-import a path that already completed" was
correct: the poller fires unbidden and forever, so it needs a permanent memory. Once
the trigger became a human browsing to a folder and pressing Import, that same rule
became an override of stated user intent.

The comment above the query still describes the old intent and contradicts the code
it documents: it says "only an active or review-pending job should dedup", but
`state != JobState.FAILED` also matches `COMPLETED`.

### Why there is no escape hatch

Three things that look like exits are not:

1. `DELETE /api/jobs/{id}` (`backend/app/api/routes.py:1886`) is a soft delete. It
   sets `cleared_at` and nothing else. The row keeps its `staging_path` and its
   `COMPLETED` state, so the dedup query still matches it. The guard never consults
   `cleared_at`. This is the sharpest trap: the job vanishes from the dashboard while
   continuing to block re-import, so the blocker becomes invisible.
2. `JobManager.cancel_job` (`backend/app/services/job_manager.py:1151`) is a no-op on
   terminal states, so a user cannot demote a `COMPLETED` job to the one state
   (`FAILED`) the guard permits.
3. `DELETE /api/simulate/reset-all-jobs` is `DEBUG`-only
   (`backend/app/api/routes.py:2649`).

The folder rename really was the only remaining move.

### Why the reporter also saw "files already exist"

The organizer uses `shutil.move` (`backend/app/core/organizer.py:402`, `:594`, `:681`),
so a `destination_mode="library"` import moves the source files out of the picked
folder. That produces the reported loop: import misdetects the season and moves files
to `Season 01`; the source folder is now empty and its path permanently blocked; the
user moves files back and renames the folder to dodge the guard; the re-import now
collides with the `Season 01` copies Engram itself created.

### A second, quieter instance of the same bug

`import_start` raises 409 only when *every* unit was skipped
(`backend/app/api/routes.py:2966`). A partial skip returns **200** with a `skipped`
count. The client type is `{ job_ids: number[] }` (`frontend/src/api/client.ts:370`)
and `ImportModal` awaits `startImport` then immediately calls `onClose()`
(`frontend/src/components/ImportModal.tsx:123`) without reading `skipped`. So
importing a show folder where seasons 1 and 2 were done and season 3 is new closes
the modal silently, having imported less than the user asked for, with no error at
all.

### A test pins the bug in place

`backend/tests/unit/test_create_job_from_staging_dedup.py:95` asserts the current
behavior and calls it deliberate:

> a watch folder is polled continuously, so a completed import must not re-spawn a
> duplicate job while its files still sit in the watch folder. This is a deliberate
> policy [...] and this test pins it so a future change that mirrors the disc filter
> can't silently regress it.

The test is well written. Its *premise* expired: there is no watch folder. It even
names the change this spec proposes and guards against it, so the rewrite must be
deliberate, and must carry a docstring explaining what replaced the old rationale.

### Precedent: the disc path already solved this differently

`backend/app/services/job_manager.py:641-686` blocks a new disc job only when the
drive is genuinely occupied (`IDLE`, `IDENTIFYING`, `RIPPING`, plus a same-disc check
for post-eject `MATCHING` and `ORGANIZING`). Its comment states outright that
`COMPLETED`, `FAILED`, and `REVIEW_NEEDED` are non-blocking. The disc guard asks
"is this resource busy right now?"; the import guard asks "has this path ever been
touched?". The import path is the outlier.

## Decisions

**D1. Path ownership replaces path history.** A staging path is *owned* by an
in-flight job and merely *recorded* by a finished one.

| Prior job state | Tier | Behavior |
|---|---|---|
| `IDLE`, `IDENTIFYING`, `RIPPING`, `MATCHING`, `ORGANIZING`, `REVIEW_NEEDED` | Hard | Blocked, never overridable. |
| `COMPLETED` | Soft | Blocked, overridable by explicit confirmation. |
| `FAILED` | None | Non-blocking today, unchanged. |

The hard tier is exactly `state not in TERMINAL_JOB_STATES`, a frozenset already
defined at `backend/app/models/disc_job.py:23` and already imported into
`job_manager.py`.

This is deliberately **not** a copy of the disc guard. The disc path lets
`REVIEW_NEEDED` through because a disc in review has been ejected and the drive is
free. An import in review still has its files on disk awaiting a decision, so it
retains ownership. Same principle, different answer, because the resource differs.

**D2. Requests #3 and #4 from the issue collapse into D1.** With a forceable soft
tier there is no separate "reset the stored folder-path association" concept, and the
"Re-import Anyway" confirmation is the soft tier's resolution step.

**D3. `cleared_at` stays cosmetic.** Soft delete remains "hide from the dashboard"
and gains no re-import semantics. Making a clear silently free a folder would be
surprising, and D1 already removes the dead end for users who never think to clear.

**D4. Conflicts are reported per unit, and there are no silent skips.** Whenever any
unit is blocked, the response names each blocked unit, its reason, and the prior job
ids. One code path serves the fully-blocked and partly-blocked cases.

**D5. The conflict identity is opaque on the wire.** The response carries `unit_key`
and the request carries `force_keys`. The client treats these as tokens it received
and echoes back, never as filesystem paths. A human-readable `display_path` is
provided separately for rendering only.

Rationale: the key is path-derived in this change, but content-derived identity is
the correct long-term model (see Follow-ups). Naming the field after the value's
*role* rather than its *derivation* means that swap is a backend-only change, with no
API churn, no UI churn, and no test churn in the conflict flow.

**D6. `force_keys` is a list, not a blanket `force: bool`.** Scoping the override to
the exact units the user was shown means a unit that became blocked between the two
calls cannot be silently forced by a confirmation the user gave about something else.
Hard-tier blocks ignore `force_keys` entirely.

**D7. `/api/import/start` no longer returns 409.** A conflict stops being an error and
becomes a normal outcome carrying its own remedy. Returning 409 alongside partial
success would also invite a blind client retry that double-starts the units that
succeeded. Cost: a scripted caller loses a non-2xx signal and must check
`job_ids == []`. Accepted, because this is a single-user local app with no published
API contract.

**D8. The conflict UI is an inline panel, not a nested modal.** Engram has a known
issue where a dismissed modal can leave a stuck `opacity: 0` full-screen overlay that
swallows clicks under automation. Nesting a confirm dialog inside `ImportModal` would
place that hazard directly in the path of this feature's E2E test.

## Backend changes

### `create_job_from_staging` returns a result object

The current `int` return uses `-1` for "blocked". That was adequate with one reason to
block; there are now two, and the caller must distinguish them to know whether to
offer a retry. The sentinel is also why the existing 409 message has to say the vague
"active or completed": by the time the route sees `-1`, the reason is gone, and the
route can only reconstruct a count via `skipped = len(scan.units) - len(job_ids)`.

```python
class ImportBlock(StrEnum):
    IN_FLIGHT = "in_flight"
    ALREADY_IMPORTED = "already_imported"

@dataclass(frozen=True)
class StagingJobResult:
    job_id: int | None            # set iff a job was created
    block: ImportBlock | None     # set iff blocked
    blocking_job_ids: list[int]   # prior jobs responsible for the block
```

The method gains `force: bool = False`, which suppresses the soft tier only and can
never suppress the hard tier. Classification stays inside the existing per-path lock
(`backend/app/services/job_manager.py:798`) so the check and the insert remain atomic
and no TOCTOU window opens.

Production call sites are only `backend/app/api/routes.py:2939` (`import_start`) and
`backend/app/api/routes.py:3007` (`/api/staging/import`), so the blast radius is small.

`/api/staging/import` has its own instance of the sentinel bug: it returns
`{"status": "created", "job_id": -1, "titles_count": N}` when the guard blocks
(`backend/app/api/routes.py:3015`), reporting success with an impossible id. It adopts
the same two-tier semantics and returns the block reason and blocking job ids in its
body instead.

### Unit key construction

`unit_key` is `sha256(dedup_path).hexdigest()[:16]`, where `dedup_path` is the value
already used as the dedup key: `commonpath(files)`, or the parent directory for a
single-file unit, as computed at `backend/app/api/routes.py:2932`. It is never
`scan.root`.

Hashing rather than sending the raw path serves two purposes. It makes D5's opacity
enforceable instead of merely documented, since a client cannot parse a digest into a
path and start depending on its structure. It also keeps absolute filesystem paths out
of a value the UI round-trips, consistent with `display_path` being the only
path-shaped field.

The mapping is stateless and needs no server-side storage: the key is a pure function
of the dedup path, so the forced second call re-scans, re-derives a key per unit, and
matches incoming `force_keys` against those. An implementer must derive the key from
the same `dedup_path` value in both calls; deriving it from `scan.root`, or from the
user-supplied `req.path` before `resolve()`, breaks the round-trip.

## API contract

`POST /api/import/start`

Request gains one optional field:

```jsonc
{ "path": "...", "destination_mode": "library", "force_keys": ["9f2c…"] }
```

Response, always 200:

```jsonc
{
  "job_ids": [14, 15],
  "blocked": [
    { "unit_key": "9f2c…",
      "show_name": "Seinfeld", "season": 3,
      "display_path": "D:\\rips\\Seinfeld\\Season 3",
      "reason": "already_imported",
      "job_ids": [7] },
    { "unit_key": "a41b…",
      "show_name": "Seinfeld", "season": 4,
      "display_path": "D:\\rips\\Seinfeld\\Season 4",
      "reason": "in_flight",
      "job_ids": [9] }
  ]
}
```

`blocked` is `[]` on a clean import. The existing `skipped` field is removed; it is
strictly less informative than `len(blocked)` and nothing reads it.

`/api/import/preview` stays filesystem-only and DB-unaware, per its docstring. Moving
conflict detection into preview was considered and rejected: it would still need a
guard at start time against the TOCTOU window, so it adds a second code path without
removing the first.

## Frontend changes

### ImportModal conflict panel

`startImport` gains a `forceKeys` parameter and its return type widens to include
`blocked`. On a response with `blocked.length > 0`, `onStart` no longer calls
`onClose()`. It renders an inline panel below the preview:

- A "Started N of M" line when `job_ids` is non-empty.
- Hard-blocked units (`reason: "in_flight"`), listed with their job id and no action
  offered, since a second job would race the first.
- Soft-blocked units (`reason: "already_imported"`), listed with a single **Re-import
  Anyway** button that re-calls `startImport` with `force_keys` set to those units'
  `unit_key` values only.

Styled with existing `sv` tokens, matching the modal's current panels.

### Failed-job dismissal

Three independent changes, unrelated to the import flow:

- `frontend/src/app/hooks/useJobManagement.ts:254`: `clearCompleted` filters
  `state === 'completed'`. Include `'error'`, which is what the adapter maps
  `JobState.FAILED` to (`frontend/src/types/adapters.ts:20`). The API already accepts
  `FAILED` (`backend/app/api/routes.py:1892`), so only the client filter is wrong.
- `frontend/src/app/hooks/useDiscFilters.ts:51`: add `failedCount`.
  `frontend/src/app/App.tsx:419` gates the button on `completedCount > 0`, so a
  dashboard holding only failed jobs renders no button at all.
- Rename the control and its tooltip from "Clear Completed" to "Clear Finished".

## Testing

**Backend unit.** Rewrite `backend/tests/unit/test_create_job_from_staging_dedup.py`,
including its module docstring, which still describes the deleted watch folder.
`test_failed_job_does_not_block_reimport`, `test_active_job_still_blocks_reimport`,
and `test_review_needed_job_still_blocks_reimport` survive unchanged, which is the
signal that the guard was narrowed rather than removed.
`test_completed_job_still_blocks_reimport` becomes a pair: soft-blocks without
`force`, succeeds with it. New cases:

- `force` never defeats a hard block.
- `force_keys` is scoped: forcing unit A leaves unit B blocked.
- A partial conflict returns started job ids and blocked entries together.
- Each blocked entry carries the prior job ids.

**Frontend unit (vitest).** Extend `frontend/src/components/ImportModal.test.tsx`: the
panel renders for a partial conflict, the modal stays open, and Re-import Anyway
re-submits with exactly the soft-blocked keys. Separately, `clearCompleted` reaches
failed jobs.

**E2E.** One Playwright spec: import a folder, import it again, see the conflict
panel, press Re-import Anyway, see the job start.

**Docs.** `CHANGELOG.md` under `[Unreleased]`, referencing `(#571)`. Update the
Import section of `CLAUDE.md` if it describes the old dedup semantics.

## Non-goals

Stated explicitly so the implementation plan cannot drift into them:

- **Post-completion metadata correction** (issue requests #2 and #5). Correcting a
  completed job means un-organizing files already moved into the library, which is
  destructive and needs its own confirmation design. Separate issue.
- **Reset Database / History** (issue request #8). Declined: the database holds API
  keys and credentials, and the request is only attractive because the recovery paths
  above did not exist.
- **Conflict handling for existing destination files** (issue requests #6 and #7).
  Already shipped as `conflict_resolution_default` with all four options
  (`backend/app/models/app_config.py:85`, surfaced at
  `frontend/src/components/ConfigWizard.tsx:1416`), plus conflict escalation to review
  at `backend/app/services/finalization_coordinator.py:1727`. The reporter not finding
  the setting is a discoverability signal, addressed with a docs pointer, not code.
- **The "Done" filter tab excluding failed jobs.** Real, but a separate question from
  dismissal.

## Follow-ups

**Per-file content identity.** The correct long-term model replaces "did I import
this folder?" with "which of these files have I already imported?", so adding one
newly-ripped episode to a season folder imports exactly that episode and leaves the
other eight untouched.

Recorded here because two intermediate designs were considered and rejected:

- A *full* cryptographic hash is the wrong tool. A season of Blu-ray remuxes is
  roughly 30 GB, which is tens of seconds on local NVMe and minutes over a gigabit
  NAS, paid on every Import press. A sparse identity (file size plus small head and
  tail samples) is milliseconds and is amply discriminating, since two different rips
  differ in both size and EBML header. `import_scanner` already collects sizes
  (`backend/app/core/import_scanner.py:71`).
- A *set-level* hash of a unit's files is **worse than the path key**. Adding an
  episode changes the set hash, so the guard sees a brand-new unit and silently
  re-imports all nine files with no warning. The path key catches that case.

Sequencing note: the folder rename is currently users' only escape hatch. Tightening
the key in the same release that introduces `force_keys` would close the workaround
and the fallback at once. Shipping path keys plus force first degrades more
gracefully.

`DiscJob.content_hash` cannot be reused. It is TheDiscDB's disc-structure fingerprint,
MD5 over `BDMV/STREAM` or `VIDEO_TS` file sizes, and takes a drive letter
(`backend/app/core/extractor.py:445`). Content identity for imported MKVs needs its
own column.
