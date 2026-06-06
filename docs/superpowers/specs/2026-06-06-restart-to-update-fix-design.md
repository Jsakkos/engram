# Restart-to-update fix + restart UX — design

**Date:** 2026-06-06
**Status:** Approved (design); ready for implementation plan.
**Root-cause investigation:** `docs/superpowers/reviews/2026-06-06-restart-to-update-tasklist-hang.md`

## Problem

Windows "Restart to update" has never completed on a real install. The update downloads,
verifies, and stages correctly (reaches `READY`); the failure is entirely in the restart helper.

The helper `.bat` is spawned `DETACHED_PROCESS` (no console). Its first action is a `:wait`
loop that polls for the old process to exit with `tasklist /FI "PID eq N" | find /I "N"`.
**A `cmd` pipe deadlocks in a console-less process**, so that first `tasklist | find` hangs
forever. The helper never robocopies, swaps, or relaunches — the app simply disappears and the
user re-downloads manually. Reproduced deterministically 2026-06-06 (a `CREATE_NO_WINDOW` spawn
of the same bat works; `DETACHED_PROCESS` hangs, and console-less `tasklist` emits no output at
all even without a pipe).

Three prior fixes (#285 Job Object, #322 atomic swap, #338 cwd handle) each addressed a step the
helper never reaches, so the bug persisted. #338 was "verified" in an isolated swap harness that
ran the swap steps **with a console**, so it never reproduced the console-less hang.

Secondary issue: every frozen startup calls `webbrowser.open(...)` unconditionally
(`backend/run.py`), so each restart spawns a *duplicate* browser tab (on all platforms — POSIX
`os.execv` re-runs `run.py` too).

## Goals

1. Windows "Restart to update" completes the swap and relaunches the new build.
2. The app **always** comes back — on failure it rolls back to the working old build and tells
   the user (non-blocking) that the update couldn't apply.
3. No duplicate browser tab on restart; the existing tab is reused.
4. The swap is tested **in the real detached/no-window spawn context**, closing the blind spot
   that let three fixes ship without touching the real failure.

## Non-goals / out of scope

- Rewriting the helper in PowerShell (direction 2). Noted as a future maintainability option;
  not this change.
- Any change to download/verify/staging (works correctly today).
- POSIX swap mechanics (POSIX overwrites the running binary + `os.execv` in place — no helper,
  no `tasklist`, no console; nothing to fix there).
- Code signing / SmartScreen.

## Cross-platform applicability

| Change | Windows | macOS / Linux |
|--------|---------|---------------|
| Console + bounded wait (§1) | yes — the fix | N/A — no helper process exists |
| Always-relaunch + notify (§2) | yes (marker + rollback) | already adequate — failure raises, app keeps running |
| Browser-tab suppression (§3) | yes | **yes** — `os.execv` re-runs `run.py` and re-opens a tab |
| Tests (§5) | yes | suppression test applies; swap tests are Windows-only |

---

## 1. Fix the hang — console + bounded wait

File: `backend/app/core/updater.py`.

### 1a. `_spawn_detached_helper` — give the helper a console
Replace `DETACHED_PROCESS` with `CREATE_NO_WINDOW` in the creation flags. Keep
`CREATE_BREAKAWAY_FROM_JOB` (with the existing OSError fallback to non-breakaway) and the
neutral `cwd=tempfile.gettempdir()`.

- `DETACHED_PROCESS` and `CREATE_NO_WINDOW` are mutually exclusive; this is a swap, not an add.
- Rationale: `CREATE_NO_WINDOW` runs the helper in a **hidden** console (no flash), which makes
  `tasklist`, the `|` pipe, and every other console-dependent command in the bat behave
  normally. This de-risks the whole bat, not just the wait line.
- `CREATE_NEW_PROCESS_GROUP` may be kept (harmless) or dropped; not load-bearing here.

### 1b. `_render_update_bat` — bound the wait and harden the PID check
- **Bounded loop:** add an iteration counter (`set /a COUNT+=1`) with a **~10s cap** (≈10
  iterations at ~1s each). Normal exit is 1–2s, so the cap never fires in practice but guarantees
  the loop terminates fast. The cap is a backstop, not expected latency — the legitimate restart
  is dominated by robocopy, not the wait. If `os._exit(0)` hasn't dropped the process in 10s,
  that *is* a failure, so on cap jump to the failure path (§2: rollback + relaunch old + marker),
  never an infinite spin.
- **Harden the liveness check:** filter on both PID and image name, using the bat's existing
  `exe` parameter (the launcher basename, e.g. `engram.exe`), so a reused PID held by an
  unrelated process can't keep the loop alive:
  `tasklist /FI "PID eq <pid>" /FI "IMAGENAME eq <exe>"`. (The bounded cap is the ultimate
  backstop.)
- The loop logic otherwise stays the same; with §1a the existing `tasklist | find` idiom works.

---

## 2. Always-relaunch + notify contract

Chosen failure contract: **relaunch the old version + notify.** The app never disappears; if the
update can't apply, it rolls back to the working old build, relaunches it, and surfaces a
non-blocking notice. The pending update auto-retries on the next check.

### 2a. Bat writes a result marker (success and failure)
The bat writes a single `~/.engram/update_result.json` before relaunching, on every terminal
path:

- **Failure** labels (`:fail`, `:fail_no_swap`, `:restore_old`, new wait-timeout path):
  `{ "result": "failed", "version": "<target>", "step": "<label>", "ts": "..." }`
- **Success** path: `{ "result": "success", "version": "<target>", "ts": "..." }`

Written with `echo ... > "%RESULT%"` (file redirection works console-less; with §1a it is fully
reliable). Startup reads it once and branches on `result` (§2b).

### 2b. Startup reads the marker into status fields
In `UpdateChecker`:
- New instance fields (init in `__init__`): `self.last_update_error: str | None = None` and
  `self.last_update_success_version: str | None = None`.
- `start()` reads `~/.engram/update_result.json` if present, **deletes it** (so it's consumed
  once), then branches:
  - `"failed"` → `last_update_error` = human message ("Update to {version} couldn't be applied
    automatically (step: {step}). It'll retry on the next check, or download manually.").
  - `"success"` → `last_update_success_version` = `version` (used for a "you're now on vX" toast).
- The normal `_check()` still runs and may re-stage `READY`. Both fields are **separate from
  `state`** so a subsequent `READY` doesn't clobber the failure notice or the success toast.

### 2c. Surface both fields in BOTH REST and WS
> ⚠️ Known footgun (see `project_update_status_ws_rest_drift.md`): update fields must be added to
> **both** `get_status` (REST) and `broadcast_update_status` (WS), and the UI reads update state
> only from WS. Missing either side silently drops the field.

- `UpdateChecker.get_status()` — add `"last_update_error"` and `"last_update_success_version"`.
- `EventBroadcaster.broadcast_update_status(...)` — add `last_update_error` and
  `last_update_success_version` params, included in `data` (same conditional pattern as `error`).
- The `_broadcast()` helper in `updater.py` passes both.
- `frontend/src/types/index.ts` — add `last_update_error?: string | null` and
  `last_update_success_version?: string | null` to `UpdateStatus`.

### 2d. Frontend: failure banner + success toast
- **Failure variant** in `frontend/src/app/components/UpdateBanner.tsx`: currently renders only
  when `state === "ready"`. Extend it to also render a magenta (`sv.magenta`) variant when
  `updateStatus.last_update_error` is set — message + dismiss (X) + "Download manually" link to
  `release_url`. Independent of the `ready` banner (both can show: "last attempt failed" +
  "new build ready to retry"). Update `UpdateBanner.test.tsx`.
- **Success toast:** when `updateStatus.last_update_success_version` arrives, fire
  `toast.success("You're now on engram v{version}")` (sonner). Fired as a side-effect in the
  update-status consumer (the hook/effect that handles the `update_status` WS message + the
  initial `/api/updates/status` fetch), **deduped by version** via a ref/localStorage so it shows
  once even though the field rides multiple status payloads (backend stays stateless — no
  clear-after-broadcast needed).

---

## 3. Stop the duplicate browser tab (all platforms)

### 3a. Pass `--updated` through the relaunch
- Windows (`_render_update_bat`): both relaunch lines become
  `start "" /D "%INSTALL%" "%EXE%" --updated` (success and rollback).
- POSIX (`_restart_linux_macos`): `os.execv(sys.executable, sys.argv + ["--updated"])` (append
  once; idempotent since `run.py` only checks presence).

### 3b. `run.py` suppresses the tab when relaunched
- In `main()`, detect `--updated` in `sys.argv`. When present, skip the
  `threading.Timer(1.5, webbrowser.open, ...)` call. (`--updated` is otherwise inert; ensure it
  doesn't interfere with `multiprocessing.freeze_support()` — it won't, freeze_support intercepts
  before `main()`.)

### 3c. Safeguard: never strand the user without UI
When `--updated` is set, `run.py` schedules a `threading.Timer(5.0, ...)` that opens a tab **only
if no WebSocket client has connected** by then — i.e. `len(manager.active_connections) == 0`,
reading the singleton `from app.api.websocket import manager`. Covers the two cases where the
existing tab can't reconnect: the user closed it, or `_find_free_port` picked a different port
than the old instance used. (Confirmed cleanly importable; not awkward.)

Rationale recorded: a backend cannot close a tab it opened via `webbrowser.open`, and browsers
block `window.close()` on non-script-opened tabs — so "close the old tab" is unreliable.
Suppress-new + reconnect reaches one-tab through a supported path; the 5s safeguard covers the
only case it breaks.

---

## 4. Data flow (Windows)

```
User clicks Restart now → POST /api/updates/restart
  → apply_update() re-verifies staged build → _restart_windows()
    → render bat (bounded 10s wait, PID+IMAGENAME filter, --updated relaunch, result marker)
    → _spawn_detached_helper(CREATE_NO_WINDOW | BREAKAWAY)
    → os._exit(0)
  helper: wait for <exe>(PID) to exit (bounded ~10s)
    → robocopy staged → .new ; verify sentinels ; move install→.old ; move .new→install
    → on SUCCESS: write update_result.json{success,version}; start NEW exe --updated; del .old; del self
    → on ANY failure (incl. wait-timeout): write update_result.json{failed,step}; restore old;
      start OLD exe --updated
relaunched engram (old or new), no new tab:
  UpdateChecker.start() reads + deletes update_result.json →
      failed  → last_update_error set
      success → last_update_success_version set
  → existing tab reconnects (or 5s safeguard opens one if none) →
      magenta failure banner  OR  green "you're now on vX" toast
  → _check() runs; if update still pending → re-stage READY → cyan "ready" banner (retry)
```

## 5. Testing (closes the blind spot)

- **End-to-end Windows swap in the real spawn context** (the key test): spawn the actual helper
  via `_spawn_detached_helper`, pass a throwaway parent PID that exits, against a dummy install
  dir with a dummy `engram.exe` + sentinel tree. Assert: swap completes, new tree in place,
  relaunch invoked, **success marker written**. Inject a failure (e.g. unwritable target) and
  assert: rollback + **failure marker written**. Windows-only
  (`@pytest.mark.skipif(sys.platform != "win32")`). This is the automated form of the manual
  2026-06-06 reproduction.
- **`_render_update_bat` unit assertions:** bounded loop counter (~10 cap) present; PID +
  `IMAGENAME eq <exe>` filter; `--updated` on both relaunch lines; a marker write on each failure
  label **and** the success path.
- **`_spawn_detached_helper` flags:** asserts `CREATE_NO_WINDOW` is set and `DETACHED_PROCESS` is
  not (via the same getattr-based flag resolution so it runs on non-Windows CI).
- **`run.py` suppression + safeguard:** with `--updated` + `is_frozen` mocked True,
  `webbrowser.open` is not scheduled immediately; without `--updated`, it is. Safeguard: with
  `--updated` and `manager.active_connections` empty after the timer, a tab is opened; non-empty,
  it is not.
- **Marker → fields:** `start()` reading a `failed` marker sets `last_update_error`; a `success`
  marker sets `last_update_success_version`; the marker file is deleted after read.
- **Field plumbing:** `last_update_error` and `last_update_success_version` present in REST
  `get_status()` and in the WS `update_status` payload built by `broadcast_update_status`.
- **Frontend:** success toast fires once per version (deduped) when
  `last_update_success_version` is present; failure banner renders when `last_update_error` is set.
- Existing updater unit tests continue to pass.

## Files touched

- `backend/app/core/updater.py` — `_spawn_detached_helper`, `_render_update_bat`,
  `_restart_linux_macos`, `__init__`, `start`, `get_status`, `_broadcast`.
- `backend/app/services/event_broadcaster.py` — `broadcast_update_status` signature + payload.
- `backend/run.py` — `--updated` suppression + no-client safeguard (imports `manager`).
- `frontend/src/types/index.ts` — `UpdateStatus.last_update_error` + `last_update_success_version`.
- `frontend/src/app/components/UpdateBanner.tsx` (+ test) — failure variant.
- Frontend update-status consumer (hook/effect handling the `update_status` WS message +
  `/api/updates/status` fetch) — success toast, deduped by version.
- `backend/tests/unit/test_updater.py` (+ a Windows-gated swap test).

## Risks / open notes

- `CREATE_NO_WINDOW` + breakaway must still outlive the parent — verified in principle; the
  end-to-end test confirms it on CI.
- Chicken-and-egg: the fix only runs once the user is on a build containing it. Benign here — the
  user manually installs each latest release, so the first release with the fix is hand-installed
  and auto-update works from there forward.
