# Manual Media Import: Design Spec

Date: 2026-06-27
Status: Approved (pre-implementation)
Author: brainstormed with Jonathan Sakkos

## 1. Context and problem

Engram can ingest pre-ripped MKV files through an "import watch folder": a background
poller (`StagingWatcher`) that scans a configured `import_watch_path` every ~2 seconds,
waits for a directory to look stable, then creates an import job. Two user reports and a
source review exposed three problems.

### Bug 1: discovery is depth-rigid and recursion-blind (two halves)

- **Discovery half.** The scanner only descends into a subfolder whose name matches
  `^[Ss]eason\s*0*(\d+)$` (`staging_watcher.py:24`), and `_count_mkvs` counts files only
  *directly* inside a folder, never recursing (`staging_watcher.py:363`). So a layout like
  `The King of Queens (1998) / Season 1 / Disc 1 / *.mkv` finds the `Season 1` folder but
  counts zero MKVs in it (the files are one level deeper in `Disc 1/`), and nothing is
  imported. The same is true for `Show / Disc x / *.mkv`: `Disc x` is not `Season NN`, so it
  is invisible.
- **Ingestion half.** Even when a unit is created, `identify_from_staging` ingests files
  with a non-recursive `staging_dir.glob("*.mkv")` (`identification_coordinator.py:848`) and
  points each `DiscTitle.output_filename` at the source path in place. So files nested in
  `Disc/` subfolders are never ingested even if discovery had found the season.

Note on the "spaces vs underscores" user theory: there is no filename-content filtering
anywhere in the watcher (`.lower().endswith(".mkv")` is the only test). The user renamed and
flattened at the same time; the real fix was flattening out the `Disc/` level. We are not
fixing a filename problem that does not exist.

### Bug 2: partial imports lock in

While files are still copying in, the stability check can fire on a subset that happens to sit
unchanged for ~4 seconds (for example, a pause mid-copy). The directory is then added to an
in-memory `_processed_dirs` set (`staging_watcher.py:151`) and is never re-scanned, so
late-arriving files are ignored until the app restarts (which clears the in-memory set). This
is the "only grabbed 11 of 60, fixed by reopening Engram" report.

### UX gap: import is silent and invisible

Both users independently asked for the same thing: point Engram at a folder or file and have it
identify now, with visible feedback. The watcher is asynchronous and gives no signal when a
layout is unrecognized or when files are skipped.

## 2. Goals and non-goals

### Goals
- Replace the background watch folder with an explicit, user-triggered manual import.
- Make discovery recursive and layout-tolerant: `Disc` folders and arbitrary intermediate
  nesting are handled; season is inferred from any `Season NN` path segment.
- Show a live preview before import so nothing is silently skipped.
- Support both a folder (recursively) and a single file.
- Map a multi-season folder to one job per season.

### Non-goals
- No background/unattended ingestion (the watcher is removed, per decision 1 below).
- No browser file upload (MKVs are large; the backend reads files in place).
- No multi-file cherry-pick UI in v1 (point at a subfolder instead).
- No change to the matching or organization engines beyond the ingestion fix.

## 3. Decisions made during brainstorming

1. **Replace the watch folder.** Manual import becomes the only ingestion path for pre-ripped
   files. The polling loop, stability heuristic, and `_processed_dirs` lock-in are removed,
   which eliminates Bug 2 by construction.
2. **Server-side directory browser.** A browser cannot hand the backend an absolute path, so
   the backend exposes a read-only "list directory" endpoint and the modal navigates it. This
   is portable even if the browser runs on a different machine than the backend.
3. **Preview then confirm.** A live preview updates as the user navigates; import starts only
   on an explicit action.
4. **Folder (recursive) and single file** are both supported.
5. **One job per season.** A folder spanning seasons 1 to 7 creates 7 jobs, one per season,
   matching how the matcher and organizer already work. The preview states the job count up
   front.
6. **Button placement: top bar**, beside the settings gear, as a first-class action.
7. **Modal: two-pane** (folder navigator on the left, live preview on the right).

Three implementation decisions confirmed with the user:

- **In-place destination becomes per-job.** Today in-place organizes under the single global
  `import_watch_path` (`finalization_coordinator.py:120`). Since imports can come from any
  folder now, in-place organizes under the picked folder (`manifest.root / TV` or
  `manifest.root / Movies`). Library mode is unchanged.
- **Keep both config fields, repurposed.** `import_watch_path` becomes the last/default browse
  location (the modal opens there); `import_destination_mode` becomes the default toggle
  state. No column is dropped (DB compatibility). Both are removed from the watcher-reload
  trigger.
- **Leave the import season-pin guard as-is** (`matching_coordinator.py:978`). Season-scoped
  imports still match correctly; enabling season pinning for them is a later optimization.

## 4. Architecture

```
User clicks + IMPORT (top bar)
        |
   ImportModal opens
        |
  GET /api/import/browse?path=...   <-- navigate folders (read-only)
        |  (on each folder selection)
  POST /api/import/preview {path}   <-- import_scanner.scan(path) -> units + loose + totals
        |  (user clicks Start)
  POST /api/import/start {path, destination_mode}
        |
  import_scanner.scan(path) -> units
        |  for each unit:
  job_manager.create_job_from_staging(..., import_manifest={root, files})
        |
  identify_from_staging(job_id)  <-- consumes manifest.files (recursive-safe)
        |
  matching -> finalization (library, or in-place under manifest.root)
```

The scanner is the single source of truth for which files belong to which job, so the preview
and the actual import are guaranteed consistent.

## 5. Backend design

### 5.1 New module: `app/core/import_scanner.py`

Extract and harden the folder-structure logic from `staging_watcher.py` (which is deleted).

Public surface:

```python
@dataclass
class ImportUnit:
    show_name: str | None     # derived from picked folder or recognizable parent
    season: int | None        # inferred from any "Season NN" path segment, else None
    files: list[Path]         # absolute MKV paths belonging to this unit
    total_bytes: int

@dataclass
class ImportScan:
    root: Path
    units: list[ImportUnit]       # one per season (or one flat unit)
    loose_files: list[Path]       # MKVs that could not be placed in a unit
    total_files: int
    total_bytes: int
    truncated: bool               # True if a scan cap was hit

def scan(path: Path) -> ImportScan: ...
```

Rules:
- **Recursive.** Walk the tree under `path`. Any non-`Season` intermediate folder (including
  `Disc N`) is transparent: recurse through it.
- **Season inference.** For each MKV, the season is the integer from the nearest ancestor
  segment matching `^[Ss]eason\s*0*(\d+)$`. If none, season is `None`.
- **Show inference.** Two cases, decided per picked root:
  - If the picked folder directly contains `Season NN` subfolders or loose MKVs, the picked
    folder *is* the show: `show_name` is the picked folder name for every unit.
  - Otherwise, each immediate subdirectory of the picked folder is treated as a separate show:
    `show_name` is that subdirectory's name, and seasons are inferred within it. This handles a
    user pointing at a parent that contains several show folders (for example `D:\Rips`
    containing `King of Queens` and `Seinfeld`).
  - A trailing year like `(1998)` is preserved in the raw `show_name` for the later TMDB
    resolver to use. TMDB resolution itself stays in `identify_from_staging`; the scanner does
    no network calls.
- **Grouping.** Units are keyed by `(show_name, season)`: one `ImportUnit` per show per season.
  Files whose season is `None` group into one flat unit per show (matches across all seasons of
  that show). A single-file target produces one flat unit with that one file, `show_name` taken
  from its parent folder.
- **Loose vs structured.** Preserve the existing data-loss safeguard intent: if the picked
  folder has both structured season subfolders and loose top-level MKVs, the loose files are
  reported in `loose_files` (surfaced in the preview), not silently merged.
- **Caps.** Bound the walk by a max file count and max depth; set `truncated=True` and surface
  it in the preview if hit (no silent truncation).
- Pure and synchronous; callers run it via `asyncio.to_thread`.

### 5.2 New endpoints (`app/api/routes.py`)

`GET /api/import/browse?path=<abs path or empty>`
- Returns `{cwd, parent, roots, entries}` where `entries` is a list of
  `{name, path, type: "dir" | "mkv", mkv_count?}`. Empty `path` returns `roots` (drive letters
  on Windows; `/` and home on POSIX) and no `cwd`.
- Read-only: never returns file contents. Directory listing only.
- Security: see section 9.

`POST /api/import/preview` body `{path}`
- Runs `import_scanner.scan` in a thread. Returns
  `{root, units: [{show_name, season, file_count, total_bytes}], loose_files: [...],
  total_jobs, total_files, total_bytes, truncated}`.
- Filesystem-only, no network. Fast enough to call on each folder selection.

`POST /api/import/start` body `{path, destination_mode}`
- Runs the scanner, then for each unit calls
  `job_manager.create_job_from_staging(...)` with the unit's explicit file list packaged as
  an import manifest.
- Persists `import_watch_path = path` (remember last) and `import_destination_mode`.
- Returns `{job_ids: [...]}`. Jobs also broadcast over WebSocket as today.

### 5.3 Job manifest and ingestion fix

Add a nullable column to `DiscJob`:

```python
import_manifest_json: str | None = Field(default=None)
# JSON: {"root": "<picked folder absolute path>", "files": ["<abs mkv>", ...]}
```

- `create_job_from_staging` gains an optional `import_manifest: dict | None` parameter; when
  given, it stores `import_manifest_json` on the job. `staging_path` for a season job is the
  season directory (or the picked folder for a flat unit); cleanup already preserves import
  sources by `drive_id == "import"` (`cleanup_service.py:63`).
- `identify_from_staging` (`identification_coordinator.py:848`): when
  `job.import_manifest_json` is present, build `mkv_files` from `manifest["files"]` (sorted)
  instead of the non-recursive glob. The glob path remains as the fallback for any other
  caller (simulation, residual staging). This is the fix for Bug 1's ingestion half.

Schema convergence:
- Add the column via the `database.py` `_add_missing_columns` reconciler (the path that
  reaches frozen end-user DBs, which skip Alembic) AND add an Alembic migration for dev
  parity. See the frozen-build note in project memory.

### 5.4 Finalization: per-job in-place root

`finalization_coordinator.py` currently computes the in-place root from
`cfg.import_watch_path` (lines 119 to 122). Change in-place mode to read the per-job root from
`import_manifest_json["root"]`:

- in-place: organize under `manifest_root / ("Movies" if movie else "TV")`.
- If a job has `destination_mode == "in_place"` but no manifest root (should not happen for new
  imports), fall back to library mode and log a warning.
- library mode: unchanged (uses configured library paths).

### 5.5 Removals and config

- Delete `app/core/staging_watcher.py` and `tests/unit/test_staging_watcher.py` (replaced by
  `test_import_scanner.py`).
- `job_manager.py`: remove the `StagingWatcher` import, the `_staging_watcher` field, its init
  in `start()` (lines ~282 to 292), `reload_staging_watcher` (lines ~397 to 420), the stop in
  shutdown (lines ~425 to 426). Remove `_on_staging_event` as well: the watcher callback was
  its only caller. `create_job_from_staging` stays (reused by the new start endpoint and
  simulation).
- `routes.py`: remove `import_watch_path` and `import_destination_mode` from the
  watcher-reload trigger set (lines ~1456 to 1465) and remove the `reload_staging_watcher`
  call there. Keep both fields in `ConfigResponse`, `ConfigUpdate`, and the GET constructor
  (now meaning "default browse location" and "default destination").
- `config_service.py`: keep `import_watch_path` in `_nullable_fields`.
- `staging_watch_enabled` becomes vestigial (it only gated the now-removed staging scan). Leave
  the column for DB compatibility; remove any UI for it. Document it as inert.
- Update the comments in `cleanup_service.py` (lines 49 to 51, 129) that reference
  `import_watch_path` as the watched path, since its meaning changed.

## 6. Frontend design

### 6.1 Top-bar button
- Add a `+ IMPORT` action to `SvTopBar` (used by `app/App.tsx`), beside the settings gear.
  Style with the existing Synapse action pattern (mono uppercase, cyan accent). A new `IcoImport`
  (or reuse `IcoLibrary`/`IcoDrive`) under `app/components/icons/`.
- Optional non-exclusive add: a primary "Import files" CTA in the empty-dashboard state.

### 6.2 `ImportModal.tsx`
Two-pane modal following the Framer Motion pattern of `NamePromptModal.tsx` (backdrop + scale,
`role="dialog"`, Escape to close), Synapse styling (`SvPanel`, corner ticks, `sv` tokens):

- **Left pane (navigator):** breadcrumb of the current path; a list with `..`, folders (with a
  per-folder MKV count badge), and selectable `.mkv` files. Selecting a folder or file drives
  the preview. Backed by `GET /api/import/browse`.
- **Right pane (live preview):** grouped by show (one section per show when the picked folder
  holds several), each with per-season rows (`SEASON n - N files -> 1 job`),
  the loose/unplaceable notice (amber) when present, a `truncated` notice if the scan was
  capped, and the destination toggle (`Organize into library` / `Organize in place`,
  defaulting from config). Backed by `POST /api/import/preview`.
- **Footer:** running totals (`N jobs - N files - ~size`), Cancel, and
  `START IMPORT - N JOBS` (calls `POST /api/import/start`, then closes; jobs appear via the
  normal WebSocket job feed).

### 6.3 API client (`api/client.ts`)
Add helpers: `browseDir(path)`, `previewImport(path)`, `startImport(path, destinationMode)`,
matching the existing `apiFetch` pattern.

### 6.4 ConfigWizard
Remove the Import Watch Folder section (lines ~731 to 795 in `ConfigWizard.tsx`) and its
`importWatchPath` / `importDestinationMode` form wiring. The destination default is now set in
the import modal itself (and persisted to the same config fields).

## 7. Data flow summary

1. Browse: `GET /api/import/browse` returns directory entries with MKV counts.
2. Preview: `POST /api/import/preview` runs the scanner and returns units, loose files, totals.
3. Start: `POST /api/import/start` runs the scanner, creates one job per unit with an explicit
   manifest, and persists the last path + destination.
4. Identify: `identify_from_staging` reads `import_manifest_json["files"]`, probes durations,
   classifies, resolves TMDB (via the existing `_resolve_missing_tmdb_id` path), and creates
   `DiscTitle` rows pointing at the in-place source files.
5. Match and finalize: unchanged, except in-place organize uses `manifest_root`.

## 8. Edge cases and error handling

- **Empty folder / no MKVs:** preview shows zero units and a clear "no MKV files found"
  message; Start is disabled.
- **Permission denied / unreadable dir:** browse and scan catch `OSError` per entry and skip,
  logging at debug; the endpoint returns what it could read.
- **Non-existent path on start:** return HTTP 400 with a clear message.
- **Mixed loose + structured:** loose files surface in `loose_files` and the preview notice;
  they are not imported in the structured units (consistent with the old safeguard).
- **Duplicate import:** `create_job_from_staging` already dedups by `staging_path` for
  non-FAILED jobs under a per-path lock; re-importing the same season is a no-op while a job is
  active, and re-runnable after failure.
- **Very large tree:** scanner caps depth and file count, sets `truncated`, and the preview
  shows a notice (no silent truncation).
- **Single file:** one flat unit; season `None`; matches across all seasons.

## 9. Security

The browse endpoint lists arbitrary server directories, so it is a real surface even though the
app is single-user and localhost-bound.

- The API already restricts CORS to the Vite dev origin and is bound to localhost; the browse
  and preview endpoints inherit that.
- Read-only: directory listings and MKV counts only; never file contents.
- Normalize and resolve the requested path; reject obviously malformed input. Model the
  symlink-escape awareness used by the confined `rglob` at `routes.py:1956` to 1970 so symlink
  loops or escapes do not cause unbounded or surprising traversal during counting.
- Sanitize any path written to logs with the existing `sanitize_log_value`
  (`app/core/security.py:136`).
- `import/start` only ever reads the chosen files and organizes into the configured library or
  the picked in-place root; it never deletes the import source (`cleanup_service.py:63`).

## 10. Testing

Backend unit (`tests/unit/test_import_scanner.py`):
- `Show / Season NN / Disc N / *.mkv` produces one unit per season with all disc files
  (the King of Queens case).
- `Show / Disc x / *.mkv` (no season) produces one flat unit with all files.
- Flat folder of loose MKVs produces one flat unit.
- Mixed loose + season subfolders: season units plus reported `loose_files`.
- Single `.mkv` target: one flat unit, season `None`.
- Deep/arbitrary nesting: files found regardless of depth; season from the nearest
  `Season NN` ancestor.
- Cap behavior sets `truncated`.

Backend integration:
- `import/preview` returns expected units/totals for a temp tree.
- `import/start` on a `Season/Disc` tree creates per-season jobs whose `identify_from_staging`
  ingests every file (guards against the non-recursive-glob regression).
- `import/start` persists last path and destination mode.

Frontend E2E (`frontend/e2e`):
- Open the modal from the top bar, navigate a seeded folder, see the preview, start the import,
  and observe job cards appear.

## 11. Migration and rollout

- New column `import_manifest_json` added through both the `database.py` reconciler and an
  Alembic migration (frozen builds skip Alembic, so the reconciler is what reaches users).
- Removing the watcher removes background/unattended ingestion (including the secondary
  staging-folder auto-pickup gated by `staging_watch_enabled`). This is intended.
- Existing `import_watch_path` values are preserved and reinterpreted as the default browse
  location, so a user who had a watch folder set will find the modal opens there.

## 12. Files to change

Backend:
- add `app/core/import_scanner.py`
- delete `app/core/staging_watcher.py`, `tests/unit/test_staging_watcher.py`
- `app/services/job_manager.py` (remove watcher wiring; extend `create_job_from_staging`)
- `app/services/identification_coordinator.py` (manifest-aware ingestion)
- `app/services/finalization_coordinator.py` (per-job in-place root)
- `app/services/cleanup_service.py` (comment updates)
- `app/api/routes.py` (3 new endpoints; remove watcher-reload trigger)
- `app/models/app_config.py` (comment updates; field meaning)
- `app/models/` DiscJob (`import_manifest_json` column)
- `app/database.py` (reconciler) + new Alembic migration
- add `tests/unit/test_import_scanner.py`, integration tests

Frontend:
- `frontend/src/app/components/synapse/SvTopBar.tsx` (+ `app/App.tsx`) for the button
- add `frontend/src/components/ImportModal.tsx`
- `frontend/src/api/client.ts` (3 helpers)
- `frontend/src/components/ConfigWizard.tsx` (remove watch-folder section)
- add `frontend/src/app/components/icons/` import icon if new
- add an E2E spec

## 13. Future work
- Enable season pinning for season-scoped imports (matching optimization).
- Optional multi-file cherry-pick in the navigator.
- Optional empty-state CTA.
