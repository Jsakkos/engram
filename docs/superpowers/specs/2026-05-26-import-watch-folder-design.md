# Import Watch Folder — Design Spec

**Date:** 2026-05-26
**Status:** Approved

## Problem

Users running AutomaticRippingMachine (ARM) or similar tools on multi-drive systems accumulate
large backlogs of already-ripped MKV files that need episode identification, matching, and
organisation. Engram's disc pipeline handles this automatically for physical discs, but
currently has no way to ingest files that were ripped externally.

## Existing Folder-Watch Functionality

Engram already has a `StagingWatcher` (`backend/app/core/staging_watcher.py`) that watches
the **internal staging path** (`AppConfig.staging_path`). This is Engram's own working
directory — the place where ripped MKV files land while a disc job is in progress.

The existing watcher:

- Polls `staging_path` every ~2 seconds
- Skips subdirectories named `job_*` (those are active ripping jobs managed by the pipeline)
- Detects any **other** subdirectory containing `.mkv` files that has been **stable** for two
  consecutive polls (file count and total size unchanged)
- When stable, fires a `staging_ready` callback → `JobManager.create_job_from_staging()` →
  job enters `IDENTIFYING` state skipping the rip step

This lets a user manually drop a folder of pre-ripped MKVs into `staging_path` and have
Engram pick them up automatically. It is intentionally limited to one level deep (no
subdirectory scanning) and to that single path.

**What it is not:** A general-purpose watch folder for external tools. It shares Engram's
internal working directory, so mixing it with an active ARM output path would interfere with
in-progress ripping jobs.

## New Feature: Import Watch Folder

A separately-configured watch path for externally-ripped content. The user points it at their
ARM output folder (or any library / show folder), Engram detects what's there, and jobs flow
through the normal identification → matching → organisation pipeline.

---

## Structure Detection

ARM produces three common output layouts. The `StagingWatcher` gains an import-path scan mode
that auto-detects which pattern applies:

### Pattern A — Per-disc subfolders (ARM default)

```
ARM_Output/
  THE_OFFICE_S1D1/
    title_t01.mkv
    title_t02.mkv
  THE_OFFICE_S1D2/
    title_t01.mkv
```

Detection: a direct child of the watch root is a directory that **directly contains** `.mkv`
files.

Result: one job per subfolder. Volume label = folder name (uppercased, spaces → underscores).

---

### Pattern B — Show-organised (ARM + Sonarr/Radarr post-processor)

```
ARM_Output/
  The Office/
    Season 1/
      title_t01.mkv
      title_t02.mkv
    Season 2/
      title_t01.mkv
  Band of Brothers/
    Season 1/
      title_t01.mkv
```

Detection: a direct child of the watch root is a directory that **contains subdirectories**
matching `Season N` / `Season NN` and those subdirectories contain `.mkv` files.

Result: one job per Season folder. `detected_title` = show folder name,
`detected_season` = N from "Season N".

---

### Pattern C — Flat (all MKVs directly in the watch root)

```
ARM_Output/
  title_t01.mkv
  title_t02.mkv
  title_t03.mkv
```

Detection: `.mkv` files appear directly inside the watch root itself (not in a subdirectory).

Result: treat the entire watch root as a single job unit. Let identification determine title
and season from audio matching.

---

A single watch root can exhibit multiple patterns simultaneously (e.g. some per-disc
subfolders alongside a show-organised subtree). The scanner handles each child independently.

---

## Destination Modes

Each imported job has a `destination_mode` inherited from the watch path config:

| Mode | Behaviour |
|------|-----------|
| `library` | Same as a disc job — organise into `library_tv_path` / `library_movies_path`. Source folder removed after successful organisation. |
| `in_place` | Organise relative to the **import watch root** instead of the global library path. Result: `{import_watch_path}/TV/The Office/Season 01/The Office - S01E03.mkv`. Source per-disc subfolder removed after successful organisation. |

`in_place` is useful when the user's ARM output folder **is** their library, or when they do
not want Engram to move files to a separate location.

---

## Changes by Layer

### `StagingWatcher` (`backend/app/core/staging_watcher.py`)

Two additions, nothing removed or changed in the existing path:

**Constructor:**
```python
def __init__(
    self,
    staging_path: str,
    import_watch_path: str | None = None,
    import_destination_mode: str = "library",
    config=None,
)
```

**New method: `_scan_import_path()`**

Called alongside `_scan_staging_dir()` inside `_check_staging()`. Walks the import path,
applies pattern detection, and emits job units. Each unit is a `(dir_path, mkv_count,
total_size)` tuple compatible with the existing `_known_dirs` / `_processed_dirs` dicts
(keyed by absolute path string — no collision with internal staging entries).

Duplicate-import guard: before firing the callback, check whether a `DiscJob` already exists
in the DB with a matching `staging_path`. This survives server restarts, unlike the in-memory
`_processed_dirs` set (which still guards within a session).

**Extended callback:**

Current signature: `(event, staging_dir_path, volume_label)`

New optional fourth argument: `metadata: dict | None = None`

```python
metadata = {
    "show_name": "The Office",   # Pattern B only; None otherwise
    "season": 1,                 # Pattern B only; None otherwise
    "destination_mode": "library",
    "source": "import",          # Distinguishes from internal staging events
}
```

Existing staging-path callback passes `metadata=None` — no change to current callers.

---

### `AppConfig` (`backend/app/models/app_config.py`)

Two new fields, added via `_add_missing_columns()` (no migration needed):

```python
import_watch_path: str | None = None
import_destination_mode: str = "library"
```

---

### `DiscJob` model

One new field:

```python
destination_mode: str = "library"   # "library" | "in_place"
```

`drive_id` is set to `"import"` for watch-folder-originated jobs (vs `"staging"` for jobs
created via the existing manual-drop path or `POST /staging/import`).

---

### `JobManager` (`backend/app/services/job_manager.py`)

`_on_staging_event()` checks `metadata["source"]` to distinguish import events from internal
staging events, and forwards `show_name`, `season`, and `destination_mode` from metadata to
`create_job_from_staging()`.

`create_job_from_staging()` gains `destination_mode` and `drive_id` override parameters.

---

### `FinalizationCoordinator` (`backend/app/services/finalization_coordinator.py`)

Before calling the organiser, check `job.destination_mode`:

- `"library"` → existing behaviour, use `AppConfig.library_tv_path` / `library_movies_path`
- `"in_place"` → pass the import watch root as the library root override to the organiser

The bare organiser functions (`organize_tv_episode`, `organize_movie`, `organize_tv_extras`)
already accept an optional `library_path` parameter. The `TVOrganizer.organize()` and
`MovieOrganizer.organize()` wrapper methods used by the coordinator do not yet forward it.
For `in_place` mode the coordinator calls the bare functions directly with the override
`library_path`; no change to the wrapper methods is required.

---

### ConfigWizard UI (`frontend/src/components/ConfigWizard.tsx`)

New **"Import Watch Folder"** section (after the library path fields):

- **Watch folder path** — text input with a folder-browse button; shows current value or
  placeholder "Not configured"
- **Destination** — two-option toggle: `Organize into library` / `Organize in place`
  - "Organize into library": files are moved into the configured TV/movie library paths
  - "Organize in place": files are organized within the watch folder itself
- **Clear** button — sets path to null (stops watching) without losing the destination
  setting

---

### Dashboard UI (`frontend/src/app/components/DiscCard.tsx`)

`drive_id = "import"` renders a folder icon badge where disc-originated jobs show a drive
letter (e.g. `E:`). The rest of the card is unchanged — same progress, state, review queue.

---

## What Is Not Changing

- The existing `StagingWatcher` behaviour for `staging_path` is untouched
- The existing `POST /staging/import` API endpoint is untouched
- The `job_*` skip logic in `_scan_staging_dir` is untouched
- No new polling task or service is introduced — the import scan runs on the same loop as the
  existing staging scan

---

## Open Questions / Out of Scope

- **Multiple import watch paths**: the config model stores a single path. If multi-path
  support is needed later, `import_watch_path` can be replaced with
  `import_watch_configs_json` (a JSON list) without changing the watcher logic.
- **Movie imports**: destination mode `in_place` uses the same watch root for both TV and
  movies. The organiser places them under `{watch_root}/Movies/...` by convention.
- **VOB / ISO inputs**: not handled. Only `.mkv` files are detected.
