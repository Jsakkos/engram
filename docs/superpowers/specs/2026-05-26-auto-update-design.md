# Auto-Update Feature Design

**Date:** 2026-05-26
**Status:** Approved
**Author:** Claude (brainstorming session with Jonathan Sakkos)

---

## Context

Engram is distributed as frozen PyInstaller builds (`.zip` on Windows, `.tar.gz` on Linux/macOS) via GitHub Releases. Users currently have no way to know when a new version is available without manually checking GitHub. Linux accounts for ~67% of downloads, Windows ~33%.

This feature adds automatic startup version checking, silent background download of new versions, and a seamless restart-to-apply flow. The goal is to reduce friction for users to stay current without requiring any manual polling of the releases page.

---

## Decision Summary

| Dimension | Decision |
|---|---|
| Check trigger | Backend startup (async background task) |
| Download trigger | Automatic, silent, immediately on version detection |
| User notification | WebSocket push → `update_status` message |
| Skip preference | Stored in `AppConfig.skipped_update_version` (DB-persisted) |
| Checksum | SHA256 verification via `sha256sums.txt` release asset |
| Release notes | Shown in a modal (`react-markdown` rendered) |
| Dev mode | Check and notify (banner shown, "Restart" button hidden), never download or restart |

---

## Architecture

```
FastAPI lifespan startup
  └─ asyncio.create_task(update_checker.start())
                    │
         GitHub API: releases/latest
                    │
         Compare tag_name vs __version__
                    │
         New version AND not skipped?
              ├── No  → state: up_to_date
              └── Yes → state: downloading
                         │
                 Download sha256sums.txt
                 Stream-download platform asset
                 Verify SHA256
                 Extract to ~/.engram/update/<version>/
                         │
                 state: ready
                         │
              broadcast_update_status() via EventBroadcaster
                         │
              Frontend receives update_status WS message
              UpdateBanner appears in App.tsx
                         │
              ┌──────────┴────────────────┐
       "What's new" → UpdateModal    "Skip"
              │                           │
       "Restart now"              POST /api/updates/skip
              │                   AppConfig.skipped_update_version = version
    POST /api/updates/restart
              │
    platform_restart() dispatch
```

**Update state machine** (in-memory on `UpdateChecker`):

```
idle → checking → up_to_date
                → downloading → ready
                              → error
       (any state) → skipped  (if skip called)
```

---

## Backend Components

### `backend/app/core/updater.py` (new file)

**`UpdateStatus` enum:**
```python
class UpdateStatus(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    DOWNLOADING = "downloading"
    READY = "ready"
    SKIPPED = "skipped"
    ERROR = "error"
```

**`UpdateChecker` class** — module-level singleton (mirrors the `job_manager`, `curator` singleton pattern):

| Method | Responsibility |
|---|---|
| `start()` | Entry point from lifespan. Loads `skipped_update_version` from `AppConfig`, calls `_check()`. |
| `_check()` | `GET https://api.github.com/repos/Jsakkos/engram/releases/latest`. Parses `tag_name`. Compares vs `__version__`. |
| `_download(release)` | Downloads `sha256sums.txt`, then streams the platform asset (`.zip` / `.tar.gz`) to `~/.engram/update/<version>/`. Verifies SHA256. Calls `_extract()`. Sets `state = READY`. Broadcasts. |
| `_extract(archive_path, dest_dir)` | Extracts archive. On failure, deletes staging dir, sets `state = ERROR`. |
| `_verify_checksum(file_path, checksums_text)` | Parses `sha256sums.txt`, finds the matching filename line, computes SHA256, compares. Raises `UpdateError` on mismatch. |
| `get_status() → dict` | Returns serializable status dict for the API. |
| `skip_version(version)` | Writes `AppConfig.skipped_update_version = version`. Sets `state = SKIPPED`. Broadcasts. |
| `apply_update()` | Guards: not frozen → raise `ConfigurationError`. Active jobs → raise 409. Dispatches to `_restart_linux_macos()` or `_restart_windows()`. |
| `_restart_linux_macos()` | `shutil.copy2(new_binary, sys.executable)` + `os.chmod(+x)` + `os.execv(sys.executable, sys.argv)` |
| `_restart_windows()` | `install_dir = Path(sys.executable).parent`. Writes `%TEMP%\engram_update.bat` (PID-wait loop + `xcopy /Y /E /I "{staging}\*" "{install_dir}\"` + relaunch + self-delete). `subprocess.Popen(['cmd', '/c', bat_path])`. `sys.exit(0)`. |

**Platform asset selection** (based on `sys.platform`):
- `win32` → asset matching `*windows*.zip`
- `linux` → asset matching `*linux*.tar.gz`
- `darwin` → asset matching `*macos*.tar.gz` or `*darwin*.tar.gz`

### `AppConfig` model additions

```python
skipped_update_version: str | None = None   # e.g. "0.8.2"
last_update_check: datetime | None = None   # informational, not used for rate-limiting in V1
```

Both are nullable; the `database.py` `_add_missing_columns()` reconciler handles schema drift without a migration.

### API Routes (added to `backend/app/api/routes.py`)

```
GET  /api/updates/status
     → 200: UpdateStatusResponse (state, current_version, latest_version,
              release_notes, release_url, download_progress, error)

POST /api/updates/skip
     body: {version: str}
     → 200: {ok: true}

POST /api/updates/restart
     → 200: {ok: true}          (process will exit; client reconnects)
     → 400: {error: "..."} if not frozen
     → 409: {error: "..."} if active jobs present
```

### `EventBroadcaster` addition

New method following the existing `broadcast_*` pattern. `current_version` is always `__version__` (the running build's version) and is injected by the broadcaster, not by `UpdateChecker`:
```python
async def broadcast_update_status(
    self,
    state: str,
    latest_version: str | None = None,
    release_notes: str | None = None,
    release_url: str | None = None,
    error: str | None = None,
) -> None:
    # Injects current_version=__version__ into the data payload automatically
    ...
```

WebSocket envelope: `{"type": "update_status", "data": {...}}`

---

## Release Workflow Changes

**`.github/workflows/release.yml`** — add checksum generation step after all platform builds complete:

```yaml
- name: Generate SHA256 checksums
  run: |
    cd dist
    sha256sum engram-linux-*.tar.gz engram-windows-*.zip engram-macos-*.tar.gz \
      > sha256sums.txt
    cat sha256sums.txt

- name: Upload checksums to release
  uses: actions/upload-release-asset@v1
  with:
    asset_path: dist/sha256sums.txt
    asset_name: sha256sums.txt
    asset_content_type: text/plain
```

> **Implementation note on multi-platform CI:** The checksum step must run after all platform assets exist in one place. Concretely: each platform matrix job uploads its artifact via `actions/upload-artifact`; a final `publish` job downloads all three artifacts, runs `sha256sum` (Linux runner), generates `sha256sums.txt` in the standard `<hash>  <filename>` two-column format, then uploads it alongside the other assets to the GitHub Release.
>
> `sha256sums.txt` format (standard `sha256sum` output):
> ```
> a1b2c3...  engram-linux-x64-0.8.2.tar.gz
> d4e5f6...  engram-windows-x64-0.8.2.zip
> 7890ab...  engram-macos-arm64-0.8.2.tar.gz
> ```

---

## Frontend Components

### `UpdateBanner.tsx` (new, `frontend/src/app/components/`)

Rendered in `App.tsx` immediately below the header, using the existing Synapse v2 panel style. Only visible when `updateStatus.state === 'ready'`.

```
┌────────────────────────────────────────────────────────────────────┐
│  ↑  engram 0.8.2 is ready to install    [What's new]  [Restart]  [Skip] │
└────────────────────────────────────────────────────────────────────┘
```

- "What's new" → opens `UpdateModal`
- "Restart" → stores `latest_version` in a `useRef` as `pendingUpdateVersion`, then `POST /api/updates/restart`; on 200, shows "Restarting…"; the existing WebSocket reconnect loop handles re-connection; on reconnect, `GET /api/updates/status` returns `state: 'up_to_date'` — frontend compares the new `current_version` against the stored `pendingUpdateVersion` ref and shows a Sonner success toast: *"Updated to 0.8.2 ✓"*
- "Skip" → `POST /api/updates/skip`; dismisses banner

### `UpdateModal.tsx` (new, `frontend/src/app/components/`)

Follows `BugReportModal.tsx` overlay pattern. Opens from `UpdateBanner`.

```
┌─────────────────────────────────────────┐
│  What's new in engram 0.8.2           ✕ │
│  ─────────────────────────────────────  │
│  [rendered markdown release notes]      │
│                                         │
│  [Skip this version]        [Restart →] │
└─────────────────────────────────────────┘
```

- Renders `release_notes` markdown via `react-markdown` (new dependency)
- Same "Restart" and "Skip" actions as the banner

### WebSocket handler (`useJobManagement.ts`)

```typescript
case 'update_status': {
  setUpdateStatus(data as UpdateStatus);
  break;
}
```

**`UpdateStatus` TypeScript type:**
```typescript
interface UpdateStatus {
  state: 'idle' | 'checking' | 'up_to_date' | 'downloading' | 'ready' | 'skipped' | 'error';
  current_version: string;
  latest_version: string | null;
  release_notes: string | null;
  release_url: string | null;
  download_progress: number | null;  // 0.0–1.0, only while downloading
  error: string | null;
}
```

`updateStatus` state lives in `App.tsx` (same level as job state), passed down as prop or via context.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| GitHub API unreachable / rate-limited | Silent fail. State stays `idle`. Logged at `DEBUG`. No UI shown. |
| Download interrupted | State → `ERROR`. Partial file deleted. WebSocket push. Frontend shows dismissible error toast. |
| SHA256 mismatch | State → `ERROR`. Staged files deleted. Toast: *"Update verification failed. Will retry next startup."* |
| `apply_update()` while job active | API → 409. Frontend shows: *"A rip is in progress — please wait before restarting."* |
| `apply_update()` in dev mode | API → 400. Frontend shows: *"Updates can't be applied in dev mode."* |
| Windows: `.bat` blocked by policy | `PermissionError` caught → API → 500. Frontend falls back to showing `release_url` link. |

---

## Testing

### Backend unit tests (`tests/unit/test_updater.py`)

- Mock `httpx.AsyncClient` to return fake GitHub releases API JSON
- State transition: `idle → up_to_date` when version matches
- State transition: `idle → downloading → ready` when newer version available
- Checksum match succeeds, mismatch raises `UpdateError` and cleans up staging
- `skip_version()` writes to `AppConfig` and sets state to `SKIPPED`
- `apply_update()` raises `ConfigurationError` when `sys.frozen` is falsy
- `apply_update()` raises 409-equivalent when jobs are active
- `get_status()` returns serializable dict in all states

### Integration tests (`tests/integration/test_update_workflow.py`)

- `GET /api/updates/status` returns shape with all required fields
- `POST /api/updates/skip` with `{"version": "0.0.0"}` returns 200 and persists to DB
- `POST /api/updates/restart` returns 400 in the non-frozen test environment

### What is NOT tested

- Actual binary replacement and `os.execv()` restart — unit-tested with mocks only
- Windows `.bat` execution — unit-tested with mock `subprocess.Popen`
- Live GitHub API — no network calls in CI

---

## Out of Scope (V1)

- Delta/patch updates (binary diff)
- Rollback to previous version
- Auto-update on a schedule (only on startup)
- ConfigWizard toggle to disable update checks
- Signing/notarizing the downloaded binary
- Progress indicator during download (silent by design)
