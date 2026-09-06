# Disc backup before rip, and importing existing backups

**Date:** 2026-09-05
**Status:** Design approved, ready for implementation planning
**Origin:** Discord feature request from Ironside7 (2026-09-02)

## Problem

Engram rips directly from the optical drive. Every title is a fresh read of the
physical disc, so a scratched disc is stressed once per title and a read failure
mid-library-build loses the whole disc. The requester's manual workflow avoids
this: MakeMKV writes a full decrypted backup of the disc to local storage first,
then extracts MKVs from that backup.

That workflow solves three problems at once:

1. **Preservation.** Some discs have no in-print replacement (the requester cites
   *The Sweetest Thing*). A backup is the archival copy, kept and moved to a
   separate server.
2. **Disc safety.** The physical disc is read once, sequentially, instead of once
   per title with seeks in between.
3. **Speed and reliability.** Extraction from local storage is faster than from
   an optical drive and does not fail on a marginal read.

The request has a second half: once backups exist, the user wants to point
Engram at one and have it run the normal pipeline. The requester explicitly does
not want a monitored folder for this; a manual import action is the desired
surface.

## Goals

- Optional backup phase between identification and extraction, writing a full
  decrypted disc copy to a separate configurable root.
- Extraction reads from that backup rather than from the drive.
- The disc is released as soon as the backup completes, not after extraction.
- Existing backups (folder or ISO) can be imported through the manual import
  modal and run the full identify, rip, match, organize pipeline.
- Enabling the feature can never make a disc less likely to finish than it is
  today.

## Non-goals

- **Backup-only mode** (copy the disc and stop, producing no MKVs). A different
  workflow from the request, and it would create a terminal path where COMPLETED
  means no media was produced.
- **Per-disc override in the UI.** A global setting only. Deferred until someone
  asks for it.
- **A monitored backups folder.** Explicitly out per the requester.
- **Deleting backups automatically.** Backups are always kept. Engram never
  removes 40 GB the user asked it to create.
- **Concurrent disc handling.** Releasing the drive early makes it possible for
  Engram to accept the next disc while the previous job extracts. This design
  must not break that, but does not build it.

## Approach

### Why a source abstraction

`Extractor` is drive-shaped: `_to_drive_spec()` maps `"E:"` to `dev:E:`, and both
`scan_disc` and `rip_titles` take a `drive: str`. MakeMKV itself accepts three
source forms: `dev:<letter or device>`, `disc:<index>`, and `file:<path>` for a
backup folder or an ISO. Both halves of this feature reduce to the same change:
a job's source may be a path rather than a drive.

Three options were considered.

- **A. A `source_spec` string column, parsed ad hoc.** Smallest diff, but the
  question "is this source a physical drive?" gets asked in roughly six places
  (eject, sentinel re-arm, drive lock, watchdog, `_release_drive`, progress
  labelling) and each would answer it with its own string test.
- **B. A `DiscSource` value object.** One home for that question, pure and
  unit-testable, at the cost of a new module.
- **C. A parallel backup-job pipeline.** Rejected: it duplicates the chain that
  `IdentificationCoordinator`, `MatchingCoordinator` and
  `FinalizationCoordinator` already own.

**Chosen: B stored as A.** One nullable `source_spec` string column on `DiscJob`
(the DB and wire form), parsed through `DiscSource.parse()` wherever behaviour
depends on the source kind.

### Pipeline ordering

Backup runs **after** identification:

```
sentinel -> IDENTIFYING (scan dev:E:, content hash, TMDB/DiscDB lookup)
         -> [REVIEW_NEEDED if identity is unresolved] --+
         -> BACKING_UP  <---------------------------- --+
              free-space preflight
              resolve dev:E: -> disc:N
              makemkvcon backup --decrypt disc:N <dest>.partial
              rename .partial -> dest;  source_spec = file:<dest>
              _release_drive(outcome="Backed up")     <- disc comes out here
         -> RIPPING (re-scan file:<dest>, reconcile titles, extract to staging)
         -> MATCHING -> ORGANIZING -> COMPLETED        (unchanged)
```

A scan costs about a minute, so the disc leaves the drive barely later than in a
backup-first ordering, and in exchange Engram only spends 40 GB and 25 minutes of
disc reads on a job it has already decided to run. Identity prompts still fire
early, and content-hash and DiscDB lookup are untouched.

Backing up and scanning concurrently was rejected: two `makemkvcon` processes on
one drive is exactly what `Extractor`'s per-drive `asyncio.Lock` exists to
prevent.

### Why a distinct job state

`BACKING_UP` becomes a real `JobState` rather than a sub-phase of `RIPPING`.
Reusing `RIPPING` would be cheaper (215 `JobState.` references in the backend,
about 41 frontend files carrying state literals) but every log line, history row
and notification would say "ripping" during a copy that produces no MKV, and a
single watchdog timeout would have to cover a 40 GB sequential copy and a
per-title extraction. Folding it into `IDENTIFYING` is the wrong shape too:
identification is minutes with no progress UI, a backup is tens of minutes with
byte-level progress.

## Data model

### New config fields

Adding a config field requires the three-way sync: `AppConfig`, `ConfigUpdate`,
`ConfigResponse`, and `ConfigWizard`. Missing any one of them makes Pydantic drop
the value silently.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `backup_before_rip` | bool | `False`, `server_default "0"` | Opt-in, so a NULL on an upgraded DB must read as disabled. Mirrors `discord_notify_ripped`. |
| `backup_path` | str | `""` | Empty means the feature cannot run. Validated like the library roots. |

### New `DiscJob` columns

| Column | Type | Notes |
| --- | --- | --- |
| `source_spec` | `str \| None` | MakeMKV source string. `None` on every existing row, meaning "legacy: derive from `drive_id`". |
| `backup_path` | `str \| None` | Where this job's backup landed. Shown in history, used for re-import. |
| `backup_status` | `str \| None` | `pending`, `completed`, `failed:<reason>`, `skipped:<reason>`. |

`drive_id` keeps its current meaning (a physical drive, or the literal
`"import"`). It stays the key for eject, sentinel re-arm and disc-side job
lookup. `source_spec` is what MakeMKV is pointed at, and the two diverge exactly
once: after a successful backup, when `drive_id` is still `E:` and `source_spec`
has become `file:<backup>`.

### New job state

`JobState.BACKING_UP`, with these `VALID_TRANSITIONS` edits:

- `IDENTIFYING -> BACKING_UP`
- `BACKING_UP -> {RIPPING, REVIEW_NEEDED, FAILED}`
- `REVIEW_NEEDED -> BACKING_UP` (a disc parked for a name prompt still needs its
  backup once the user answers)

`BACKING_UP` is non-terminal, so the job-visibility invariant exempts it from the
dashboard's terminal-job cap automatically.

## Components

### `app/core/disc_source.py` (new)

Pure module, no I/O except the drive-index resolver.

- `DiscSource` value object: `kind` (`drive` | `backup` | `iso`), `value`, and
  derived `spec` (the MakeMKV argument), `lock_key`, `is_physical`.
- `DiscSource.parse(spec: str) -> DiscSource`, and
  `DiscSource.from_job(job)` which falls back to `drive_id` when `source_spec`
  is `None`.
- `resolve_disc_index(drive: str) -> str | None`: runs `makemkvcon -r info
  disc:9999` (the enumeration form) and maps a drive letter or device path to
  `disc:N`. Required because `makemkvcon backup` accepts only `disc:N`, not
  `dev:`. Engram has never needed this mapping before.

`lock_key` is the important one. `Extractor._get_drive_lock` currently keys on
`drive.replace("dev:", "").replace("disc:", "")`. A `file:` source must not
contend for the physical drive lock, or extracting from a backup would block the
next disc from being scanned. Backup and ISO sources key on their own resolved
path instead.

### `Extractor.backup_disc()` (new)

Sibling of `rip_titles`, reusing the same robot-mode line parsing, stall
watchdog, cancellation handling and `_save_makemkv_log` behaviour.

```python
async def backup_disc(
    self,
    source: DiscSource,
    dest: Path,
    progress_callback: BackupProgressCallback | None = None,
    stall_timeout: float | None = None,
    log_dir: Path | None = None,
    *,
    job_id: int = 0,
) -> BackupResult
```

Writes to `<dest>.partial` and renames on success, so a killed or crashed backup
never leaves something that looks complete. On failure the `.partial` is left in
place rather than deleted: a partial backup of a dying disc has salvage value,
and the user is the one who should decide to discard it.

### `Extractor` source threading

`_to_drive_spec` becomes `_to_source_spec`, passing through any known scheme.
`scan_disc` and `rip_titles` take a `DiscSource` (or a string coerced through
`parse`, for call-site compatibility during the migration).

### `JobManager` backup phase

A `_run_backup(job_id)` coroutine wrapped in `with_job_log_context` the same way
`_run_ripping` is, so its lines carry the `job=<id>` tag the diagnostics bundle
greps for. Structure mirrors `_run_ripping`: a short-lived setup session that is
released before the long await, then post-backup transitions on fresh sessions.

### `import_scanner` disc-image detection

A directory containing `BDMV/` or `VIDEO_TS/` is a disc image and is emitted as a
`DiscImageUnit` instead of being walked for MKVs. An `.iso` file is likewise a
unit. Detection short-circuits the walk at that directory, so a backup's
thousands of `.m2ts` files never count toward `_MAX_FILES`.

A scan can return both kinds of unit. `ImportScan` gains `disc_images:
list[DiscImageUnit]` alongside `units`.

## Backup destination naming

Computed after identification, mirroring the library layout so the backup shelf
browses the same way the library does:

- Movie: `<backup_path>/Movies/<movie folder>/`
- TV: `<backup_path>/TV/<show folder>/<season folder>/<disc slug>/`
- Unidentified: `<backup_path>/Unidentified/<sanitized volume label>/`

The folder names are not built here. Library naming is user-configurable
(`naming_movie_format`, `naming_tv_show_format`, `naming_season_format`), so
`backup_destination` delegates to the Organizer's own `format_movie_folder`,
`format_tv_show_folder` and `format_season_folder`, passing `tmdb_id` through so
a backup folder carries the same media-server disambiguation tag its library
folder would. Reproducing the default shape locally would give any user with a
customized format a backup tree that no longer mirrored their library, which is
the one outcome this section exists to prevent. It follows that a TV backup
folder carries no year under the shipped default `naming_tv_show_format`
(`"{show}"`), exactly as the library folder does not.

Only the disc level is built here, because the library has no per-disc level:
`discdb_disc_slug` when known (for example `S01D01`), otherwise
`Disc <disc_number>`, sanitized with the Organizer's `sanitize_filename`.

A name that survives sanitization as an empty string falls back to `job-<id>`
rather than a shared literal, so two unnameable discs cannot collide in one
folder. If the
destination already exists and is non-empty, the job treats the backup as
already done (`backup_status = "completed"`, `source_spec` pointed at it) rather
than overwriting: re-inserting a disc that was backed up before should not cost
another 40 GB copy.

Backup destinations are derived from config plus sanitized identification data,
never from client input.

## Title reconciliation

`DiscTitle` rows are created from the *disc* scan and carry `title_index`, which
is what `_build_rip_commands` passes to MakeMKV. Extraction now runs against
`file:<backup>`, so those indices must be re-validated rather than assumed.

After the backup completes, re-scan the backup and reconcile:

1. **Identical enumeration** (same title count, same durations in the same
   order): reuse indices directly. Expected case, since a backup is a byte copy.
2. **Reordered or shifted**: re-map by `source_filename` plus `segment_map`,
   both already captured at scan time for TheDiscDB contributions, with duration
   as a tiebreaker. Update `title_index` on the affected rows.
3. **Ambiguous**: transition to `REVIEW_NEEDED` with an explicit reason. Do not
   fall back to the disc, which may already be ejected. Nothing is lost: the
   backup is on disk and the disc is safe, which is the point of the feature.

## Failure handling

Per the approved fallback policy, a backup problem degrades to today's behaviour
rather than failing the job. Each case records `backup_status` and surfaces a
note on the job card and in history.

| Condition | `backup_status` | Behaviour |
| --- | --- | --- |
| `backup_path` empty or unwritable | `skipped:not_configured` | Direct rip from the drive |
| Free space < needed | `skipped:insufficient_space` | Direct rip |
| `disc:N` resolution fails | `skipped:no_disc_index` | Direct rip |
| Disc type unsupported by `backup` | `skipped:unsupported_disc` | Direct rip |
| `makemkvcon backup` non-zero or stalls out | `failed:<reason>` | `.partial` retained, direct rip |
| Backup re-scan cannot be reconciled | `completed` | `REVIEW_NEEDED`, no drive fallback |

Free-space preflight: sum the scanned titles' `file_size_bytes` as the disc-size
estimate, multiply by 1.15 for margin and container overhead, and compare against
`shutil.disk_usage(backup_path).free`. A conservative estimate is correct here;
the cost of a false negative (one direct rip) is far below the cost of a false
positive (a half-written 40 GB folder and a failed job).

Cancellation during `BACKING_UP` terminates `makemkvcon`, leaves the `.partial`,
and follows the existing cancel path.

## Drive release and notifications

`_release_drive` is the documented single chokepoint for "Engram is done with
this disc", and it fires the Discord `RIPPED_EVENT` with a `rip_outcome`. Under
backup-first, that moment moves from end-of-rip to end-of-backup. This is a
faithful use of the event, not a stretch: CLAUDE.md defines `RIPPED_EVENT` as a
hardware milestone meaning the disc is copied and out of the drive, deliberately
outside the `JobState`-keyed `EVENTS` map. A backup satisfies that literally.

`rip_outcome` gains the value `"Backed up"` alongside Complete, Stopped early and
Re-rip.

No new Discord event and no new toggle. `BACKING_UP` is a progress phase, not a
notification-worthy milestone.

## Watchdog

`_phase_timeout` gains a `BACKING_UP` entry. The timeout is stall-based (no
growth in the destination directory), not wall-clock: a scratched disc backs up
slowly by design, and that is precisely the disc this feature exists for. The
existing `_stall_watchdog` machinery in `Extractor` provides this; the config
knob mirrors the ripping phase timeout.

## WebSocket contract

New server-to-client message:

| Type | Data fields | Description |
| --- | --- | --- |
| `backup_progress` | `{"job_id": int, "current_bytes": int, "total_bytes": int, "speed": str, "eta": int}` | Backup copy progress |

A distinct type rather than overloading `rip_progress`, because a client that
renders "ripping" from a `rip_progress` message would be reporting a phase that
produces no MKV. Parameter names must match exactly across `EventBroadcaster`,
`ConnectionManager` and the message, per the documented WebSocket contract
validation rule; integration tests assert the chain end to end.

## Import from backup

Surfaced through the existing manual-import modal with automatic detection, so
there is one entry point and pointing at a backups root queues every disc under
it.

- `GET /api/import/browse` labels a directory containing `BDMV/` or `VIDEO_TS/`
  as `type: "disc_image"`, and lists `.iso` files as `type: "iso"`, alongside
  today's `dir` and `mkv` entries.
- `POST /api/import/preview` returns disc-image units next to MKV units, with
  total bytes.
- `POST /api/import/start` creates one job per disc image, with
  `drive_id="import"` and `source_spec` set to `file:<path>` or `iso:<path>`.

Such a job enters `IDENTIFYING` (scan the image, compute the content hash, run
TMDB and DiscDB lookup) and then goes straight to `RIPPING`. It never enters
`BACKING_UP`: it already is a backup. Extraction writes to staging and the rest
of the pipeline is unchanged.

The content hash is the MD5 of the Int64 file sizes of `BDMV/STREAM/*.m2ts`
sorted by filename, which is computable directly from a backup folder without
MakeMKV, so DiscDB lookup works at least as well for an imported backup as for a
physical disc.

Import ownership rules are unchanged: `import_guard.classify_staging_path`
treats the image path like any other staging path, so an in-flight job hard
blocks and a completed one soft blocks with `force_keys` as the override.

## Frontend

- `ConfigWizard`: a "Back up disc before ripping" toggle and a `backup_path`
  picker in the paths section, with the same validation treatment as the library
  roots.
- `StateIndicator` and `DiscCard`: a `BACKING_UP` phase labelled "Backing up",
  reusing `CyberpunkProgressBar` driven by `backup_progress`, showing GB copied,
  speed and ETA.
- `DiscCard` and `HistoryPage` detail panel: a note when `backup_status` starts
  with `skipped:` or `failed:`, naming the reason, and the backup path when the
  backup succeeded.
- `ImportModal`: disc-image and ISO entries with a distinct icon, and a
  preview line that says these will be scanned and extracted rather than filed.

## Diagnostics

The backup `makemkvcon` log is written through `_save_makemkv_log` next to the
scan and rip logs, and is picked up by the per-job diagnostics bundle
(`GET /api/diagnostics/report/{job_id}/bundle`) with the same sanitization as
the other MakeMKV logs. `backup_status`, `backup_path` and `source_spec` appear
in the job-detail JSON via `build_job_detail`.

## Testing

**Unit**

- `disc_source`: parse round-trips for all four spec forms, `lock_key`
  separation between physical and file sources, `from_job` legacy fallback when
  `source_spec` is `None`.
- Backup destination naming: movie, TV with and without a DiscDB disc slug,
  unidentified fallback, name sanitization, existing-non-empty-destination
  short-circuit.
- Free-space preflight: sufficient, insufficient, unreadable destination.
- Title reconciliation: identical enumeration, reordered enumeration resolved by
  `source_filename` and `segment_map`, ambiguous enumeration routing to
  `REVIEW_NEEDED`.
- `_build_backup_command` argv construction.
- `import_scanner`: BDMV folder, VIDEO_TS folder, bare `.iso`, disc image nested
  under a show folder, a tree mixing disc images and loose MKVs, and the
  short-circuit that keeps `.m2ts` files out of the `_MAX_FILES` budget.

**State machine**

- The three new transitions are valid, and the ones deliberately absent (for
  example `BACKING_UP -> MATCHING`) are rejected.

**Integration**

- `POST /api/simulate/insert-disc` gains `simulate_backup: true`, and
  `SimulationService` fakes a `BACKING_UP` phase with synthetic progress so the
  full flow is exercisable without 40 GB. Follows the existing pattern of
  simulation living entirely in `SimulationService` and gated on DEBUG.
- WebSocket contract test for `backup_progress` across `EventBroadcaster` and
  `ConnectionManager`.
- Import-from-backup job creation, including the guard's in-flight and
  already-imported tiers.

**E2E**

- A simulated disc shows Backing up, then Ripping, on the card.
- The import modal lists a fixture backup folder (empty `BDMV/STREAM/`
  directories, no media needed) as a disc image and starts a job from it.

**Manual verification, one backend only, real discs**

- A Blu-ray with the setting on: backup lands in the right folder, disc ejects
  after the backup, extraction reads from the backup.
- A DVD with the setting on: see the open question below.
- An existing backup folder and an ISO imported through the modal.

## Open questions

1. **Does `makemkvcon backup` accept DVDs on current MakeMKV builds?** MakeMKV's
   backup path is historically Blu-ray-oriented. The design already degrades to
   a direct rip on an unsupported disc, so a negative answer costs only the
   wording of the `skipped:unsupported_disc` message and a documentation note.
   Verify against a real DVD before finalizing that string.
2. **Does `makemkvcon backup` emit `PRGV` progress lines in robot mode the same
   way `mkv` does?** If it does not, backup progress falls back to polling the
   destination directory's size against the estimated disc size, which the
   filesystem-progress monitor in `_run_ripping` already demonstrates. Confirm
   during implementation; either way the `backup_progress` contract is unchanged.

## Consequences

- The drive is free during extraction, which makes concurrent disc handling
  possible for the first time. Not built here, but the design must not preclude
  it: hence `lock_key` separation between physical and file sources.
- Disk pressure moves. A user with backups enabled needs room for the backup plus
  staging plus the library. The free-space preflight covers the backup itself;
  the existing staging cleanup is untouched and never looks at `backup_path`.
- `backup_path` is deliberately absent from `is_within_configured_roots`. That
  guard exists for the review-playback media endpoints, which serve MKVs from
  the DB. Backups are never served over HTTP, so widening the guard would grant
  reach without a consumer.
