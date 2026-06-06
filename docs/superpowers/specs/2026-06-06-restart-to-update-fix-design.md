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

### 2a. Bat writes a result marker on failure
Each failure label in the bat (`:fail`, `:fail_no_swap`, `:restore_old`, and the new
wait-timeout path) writes `~/.engram/update_result.json` before relaunching the old build:

```
{ "result": "failed", "version": "<target>", "step": "<label>", "ts": "<bat %DATE% %TIME%>" }
```

Written with `echo ... > "%RESULT%"` (file redirection works console-less; with §1a it is fully
reliable). A success marker (`"result":"success"`) is **optional / nice-to-have** to drive a
"You're now on vX" toast — not required for this change.

### 2b. Startup reads the marker into a sticky field
In `UpdateChecker`:
- New instance field `self.last_update_error: str | None = None` (init in `__init__`).
- `start()` reads `~/.engram/update_result.json` if present; on `"failed"`, set
  `last_update_error` to a human message ("Update to {version} couldn't be applied automatically
  (step: {step}). It'll retry on the next check, or download manually."), then **delete the
  marker** so the notice shows once. The normal `_check()` still runs and may re-stage `READY`.
- `last_update_error` is **separate from `state`** so a subsequent `READY` does not clobber the
  failure notice.

### 2c. Surface `last_update_error` in BOTH REST and WS
> ⚠️ Known footgun (see `project_update_status_ws_rest_drift.md`): update fields must be added to
> **both** `get_status` (REST) and `broadcast_update_status` (WS), and the UI reads update state
> only from WS. Missing either side silently drops the field.

- `UpdateChecker.get_status()` — add `"last_update_error": self.last_update_error`.
- `EventBroadcaster.broadcast_update_status(...)` — add a `last_update_error: str | None = None`
  parameter and include it in the `data` dict (same conditional pattern as `error`).
- Every caller of `broadcast_update_status` (the `_broadcast()` helper in `updater.py`) passes
  the field.
- `frontend/src/types/index.ts` — add `last_update_error?: string | null` to `UpdateStatus`.

### 2d. Frontend failure variant
`frontend/src/app/components/UpdateBanner.tsx` currently renders only when
`state === "ready"`. Extend it to also render a **failure variant** (magenta accent, `sv.magenta`)
when `updateStatus.last_update_error` is set: shows the message, a dismiss (X), and a "Download
manually" link to `release_url`. Independent of the existing `ready` banner (both can be relevant:
"last attempt failed" + "new build ready to retry"). Update `UpdateBanner.test.tsx` accordingly.

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
When `--updated` is set, schedule a delayed check (~5s) that opens a tab **only if no WebSocket
client has connected** by then. Covers the two cases where the existing tab can't reconnect:
the user closed it, or `_find_free_port` chose a different port than the old instance used.
Implementation reads the live WS connection count (e.g. via `ws_manager` / `app.state`).

Rationale recorded: a backend cannot close a tab it opened via `webbrowser.open`, and browsers
block `window.close()` on non-script-opened tabs — so "close the old tab" is unreliable.
Suppress-new + reconnect reaches one-tab through a supported path; the 5s safeguard covers the
only case it breaks.

---

## 4. Data flow (Windows, failure case)

```
User clicks Restart now → POST /api/updates/restart
  → apply_update() re-verifies staged build → _restart_windows()
    → render bat (bounded wait, IMAGENAME filter, --updated relaunch, marker on failure)
    → _spawn_detached_helper(CREATE_NO_WINDOW | BREAKAWAY)
    → os._exit(0)
  helper: wait for engram.exe(PID) to exit (bounded)
    → robocopy staged → .new ; verify sentinels ; move install→.old ; move .new→install
    → on ANY failure: write update_result.json{failed,step}; restore old; start old exe --updated
    → on success: start new exe --updated ; delete .old ; delete self
relaunched engram (old or new), no new tab:
  UpdateChecker.start() reads update_result.json → last_update_error set → marker deleted
  → broadcast_update_status(... last_update_error) → existing tab reconnects → magenta banner
  → _check() finds update still available → re-stage READY → cyan "ready" banner (retry)
```

## 5. Testing (closes the blind spot)

- **End-to-end Windows swap in the real spawn context** (the key test): spawn the actual helper
  via `_spawn_detached_helper`, pass a throwaway parent PID that exits, against a dummy install
  dir with a dummy `engram.exe` + sentinel tree. Assert: swap completes, new tree in place,
  relaunch invoked. Inject a failure (e.g. unwritable target) and assert: rollback + marker
  written. Windows-only (`@pytest.mark.skipif(sys.platform != "win32")`). This is the automated
  form of the manual 2026-06-06 reproduction.
- **`_render_update_bat` unit assertions:** bounded loop counter present; `IMAGENAME eq engram.exe`
  filter; `--updated` on both relaunch lines; a marker write on each failure label.
- **`_spawn_detached_helper` flags:** asserts `CREATE_NO_WINDOW` is set and `DETACHED_PROCESS` is
  not (via the same getattr-based flag resolution so it runs on non-Windows CI).
- **`run.py` suppression:** with `--updated` + `is_frozen` mocked True, `webbrowser.open` is not
  scheduled; without `--updated`, it is.
- **Field plumbing:** `last_update_error` present in REST `get_status()` and in the WS
  `update_status` payload built by `broadcast_update_status`.
- Existing updater unit tests continue to pass.

## Files touched

- `backend/app/core/updater.py` — `_spawn_detached_helper`, `_render_update_bat`,
  `_restart_linux_macos`, `__init__`, `start`, `get_status`, `_broadcast`.
- `backend/app/services/event_broadcaster.py` — `broadcast_update_status` signature + payload.
- `backend/run.py` — `--updated` suppression + no-client safeguard.
- `frontend/src/types/index.ts` — `UpdateStatus.last_update_error`.
- `frontend/src/app/components/UpdateBanner.tsx` (+ test) — failure variant.
- `backend/tests/unit/test_updater.py` (+ a Windows-gated swap test).

## Risks / open notes

- `CREATE_NO_WINDOW` + breakaway must still outlive the parent — verified in principle; the
  end-to-end test confirms it on CI.
- The no-client safeguard depends on reading live WS connection count from `run.py`; if that's
  awkward to reach at that layer, fall back to always-open-after-delay-unless-flag (slightly
  less precise). Resolve during planning.
- Chicken-and-egg: the fix only runs once the user is on a build containing it. Benign here — the
  user manually installs each latest release, so the first release with the fix is hand-installed
  and auto-update works from there forward.
