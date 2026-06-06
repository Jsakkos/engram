# Windows restart-to-update fix + restart UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows "Restart to update" actually complete the swap, always relaunch the app (rolling back + notifying on failure), stop spawning a duplicate browser tab on restart (all platforms), and add tests in the real spawn context.

**Architecture:** The update helper `.bat` was spawned `DETACHED_PROCESS` (no console); its `tasklist | find` wait loop deadlocks console-less, so the swap never ran. Fix = spawn `CREATE_NO_WINDOW` (hidden console, every command works), bound the wait, write a result marker the relaunched app reads into status fields, suppress the new tab via a `--updated` relaunch flag, and surface success/failure in the UI.

**Tech Stack:** Python 3.11 / FastAPI / loguru (backend), cmd `.bat` helper (Windows), React 18 + TypeScript + Vitest + sonner (frontend).

**Spec:** `docs/superpowers/specs/2026-06-06-restart-to-update-fix-design.md`
**Root cause:** `docs/superpowers/reviews/2026-06-06-restart-to-update-tasklist-hang.md`

---

## File structure

**Modify**
- `backend/app/core/updater.py` — `_spawn_detached_helper` (flags), `_render_update_bat` (+`version`,`result_path` params; bounded wait; markers; `--updated`), `_restart_windows` (pass new params), `_restart_linux_macos` (`--updated`), `UpdateChecker.__init__` (+2 fields), `start` (consume marker), `get_status` (+2 fields), `_broadcast` (pass fields), new `_consume_update_result_marker`.
- `backend/app/services/event_broadcaster.py` — `broadcast_update_status` (+2 params).
- `backend/run.py` — new `_schedule_browser_open` helper + `main()` wiring.
- `frontend/src/types/index.ts` — `UpdateStatusMessage` + `UpdateStatus` (+2 fields).
- `frontend/src/app/hooks/useJobManagement.ts` — `toUpdateStatus` (+2 fields).
- `frontend/src/app/components/UpdateBanner.tsx` — failure variant.
- `frontend/src/app/App.tsx` — remove dead `pendingUpdateVersionRef` toast; call new hook.

**Create**
- `frontend/src/app/hooks/useUpdateSuccessToast.ts` (+ test).

**Test**
- `backend/tests/unit/test_updater.py` (extend), `backend/tests/unit/test_run_browser.py` (new), `frontend/src/app/components/__tests__/UpdateBanner.test.tsx` (extend), `frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts` (new).

**Commands:** backend `cd backend && uv run pytest <path> -v`; frontend `cd frontend && npx vitest run <path>`. (If the worktree lacks `frontend/node_modules`, run `npm install` once; `git checkout package-lock.json` before committing if install rewrote it.)

---

## Task 1: Spawn the helper with CREATE_NO_WINDOW (root-cause fix)

**Files:**
- Modify: `backend/app/core/updater.py` — `_spawn_detached_helper` (~lines 750-777)
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
import subprocess
from app.core.updater import UpdateChecker

def test_spawn_detached_helper_uses_create_no_window(monkeypatch):
    """The helper must run in a hidden console (CREATE_NO_WINDOW), not console-less
    DETACHED_PROCESS — a cmd pipe (tasklist | find) deadlocks without a console."""
    captured = {}

    def fake_popen(args, **kwargs):
        captured["flags"] = kwargs.get("creationflags", 0)

        class _P:  # minimal stand-in
            pass

        return _P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    UpdateChecker._spawn_detached_helper(["cmd", "/c", "x.bat"])

    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    assert captured["flags"] & CREATE_NO_WINDOW, "must use CREATE_NO_WINDOW"
    assert not (captured["flags"] & DETACHED_PROCESS), "must NOT use DETACHED_PROCESS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_spawn_detached_helper_uses_create_no_window -v`
Expected: FAIL — flags have `DETACHED_PROCESS` (0x8) set, `CREATE_NO_WINDOW` not.

- [ ] **Step 3: Implement — swap the flag in `_spawn_detached_helper`**

Replace the flag-resolution block (the `detached = ...` / `base = detached | new_group` lines) with:

```python
        # CREATE_NO_WINDOW gives the helper a HIDDEN console. DETACHED_PROCESS (no console
        # at all) makes `tasklist` emit nothing and a cmd pipe (`tasklist | find`) deadlock,
        # which hung the wait loop forever and was the real "restart bricks my install" bug.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        base = no_window | new_group
```

(Leave the `safe_cwd` line and the try/except Popen calls below unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_spawn_detached_helper_uses_create_no_window -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "fix(updater): spawn the Windows helper with CREATE_NO_WINDOW so tasklist|find doesn't deadlock

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Rewrite `_render_update_bat` — bounded wait, image filter, result marker, --updated relaunch

**Files:**
- Modify: `backend/app/core/updater.py` — `_render_update_bat` signature + body, and `_restart_windows` (pass `version`, `result_path`)
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
from app.core.updater import UpdateChecker

def _bat():
    return UpdateChecker._render_update_bat(
        src=r"C:\stage\engram", install=r"C:\app\engram",
        new_dir=r"C:\app\engram.new", old_dir=r"C:\app\engram.old",
        log_path=r"C:\u\.engram\update_helper.log", exe="engram.exe", pid=4321,
        version="9.9.9", result_path=r"C:\u\.engram\update_result.txt",
    )

def test_render_bat_wait_is_bounded_and_image_filtered():
    bat = _bat()
    assert 'IMAGENAME eq engram.exe' in bat          # don't wedge on a reused PID
    assert "set /a WAITED" in bat                     # bounded counter exists
    assert "if %WAITED% GEQ 10" in bat                # ~10s cap

def test_render_bat_relaunch_passes_updated_flag():
    bat = _bat()
    # both the success relaunch and the rollback relaunch suppress the new tab
    assert bat.count('"%EXE%" --updated') == 2

def test_render_bat_writes_result_marker():
    bat = _bat()
    assert 'echo result=success>"%RESULT%"' in bat
    assert 'echo result=failed>"%RESULT%"' in bat
    assert 'echo version=%VER%>>"%RESULT%"' in bat
    assert 'echo step=%STEP%>>"%RESULT%"' in bat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -k render_bat -v`
Expected: FAIL — `_render_update_bat` has no `version`/`result_path` params (TypeError) and lacks the new lines.

- [ ] **Step 3: Implement — replace `_render_update_bat` entirely**

Replace the whole `_render_update_bat` static method with:

```python
    @staticmethod
    def _render_update_bat(
        *,
        src: str,
        install: str,
        new_dir: str,
        old_dir: str,
        log_path: str,
        exe: str,
        pid: int,
        version: str,
        result_path: str,
    ) -> str:
        """Render the Windows update helper batch script (pure — testable off-Windows).

        Spawned with CREATE_NO_WINDOW (hidden console) so every command — including the
        `tasklist | find` wait pipe — works; DETACHED_PROCESS (no console) deadlocked it.

        After the parent PID exits (bounded ~10s wait, filtered on PID *and* image name so a
        reused PID can't wedge it): robocopy the staged build to a sibling `.new`, verify
        sentinels, swap via two same-volume renames, relaunch with `--updated` (suppresses a
        duplicate browser tab; the existing tab reconnects), and roll back on any failure. Every
        terminal path writes a `result=...` marker to %RESULT% that the relaunched app reads to
        toast success / show a failure notice. Logs every step to %LOG%; only deletes itself on
        success.
        """
        lines = [
            "@echo off",
            "setlocal enableextensions",
            f'set "SRC={src}"',
            f'set "INSTALL={install}"',
            f'set "NEWDIR={new_dir}"',
            f'set "OLDDIR={old_dir}"',
            f'set "LOG={log_path}"',
            f'set "RESULT={result_path}"',
            f'set "VER={version}"',
            f'set "EXE=%INSTALL%\\{exe}"',
            "set \"STEP=start\"",
            f'echo [engram-update] start (pid {pid}) > "%LOG%"',
            'echo [engram-update] cwd=%CD% >> "%LOG%"',
            'echo [engram-update] INSTALL=%INSTALL% NEWDIR=%NEWDIR% OLDDIR=%OLDDIR% >> "%LOG%"',
            'echo [engram-update] SRC=%SRC% EXE=%EXE% >> "%LOG%"',
            # --- wait for parent to exit; bounded (~10s), filtered on PID + image name ---
            "set /a WAITED=0",
            ":wait",
            f'tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {exe}" 2>NUL | find /I "{pid}" >NUL',
            "if errorlevel 1 goto exited",
            "set /a WAITED+=1",
            "if %WAITED% GEQ 10 (",
            '    echo [engram-update] timed out waiting for exit >> "%LOG%"',
            '    set "STEP=wait_timeout"',
            "    goto fail",
            ")",
            "ping -n 2 127.0.0.1 >nul",
            "goto wait",
            ":exited",
            'echo [engram-update] process exited; waiting for handles >> "%LOG%"',
            "ping -n 3 127.0.0.1 >nul",
            "ping -n 3 127.0.0.1 >nul",
            # Move our cwd off the install dir before the swap (a process's cwd can't be renamed).
            'cd /d "%TEMP%"',
            'echo [engram-update] cwd(after cd)=%CD% >> "%LOG%"',
            # --- copy staged build to a sibling of the install (never in place) ---
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            'echo [engram-update] robocopy "%SRC%" to "%NEWDIR%" >> "%LOG%"',
            'robocopy "%SRC%" "%NEWDIR%" /MIR /R:3 /W:2 /NP /NFL /NDL >> "%LOG%" 2>&1',
            "set RC=%ERRORLEVEL%",
            'echo [engram-update] robocopy exit=%RC% >> "%LOG%"',
            'set "STEP=robocopy"',
            "if %RC% GEQ 8 goto fail",
            # --- verify the copied tree before touching the live install ---
            'set "STEP=verify"',
            f'if not exist "%NEWDIR%\\{exe}" goto fail',
            'if not exist "%NEWDIR%\\_internal\\base_library.zip" goto fail',
            'if not exist "%NEWDIR%\\_internal\\certifi\\cacert.pem" goto fail',
            'if not exist "%NEWDIR%\\_internal\\app\\static\\index.html" goto fail',
            # --- atomic swap: install -> .old, .new -> install ---
            'rmdir /S /Q "%OLDDIR%" >nul 2>&1',
            'echo [engram-update] swapping install >> "%LOG%"',
            'set "STEP=move_install"',
            'move "%INSTALL%" "%OLDDIR%" >> "%LOG%" 2>&1',
            "if errorlevel 1 goto fail_no_swap",
            'set "STEP=move_new"',
            'move "%NEWDIR%" "%INSTALL%" >> "%LOG%" 2>&1',
            "if errorlevel 1 goto restore_old",
            # --- success ---
            'echo [engram-update] success; relaunching >> "%LOG%"',
            'echo result=success>"%RESULT%"',
            'echo version=%VER%>>"%RESULT%"',
            'start "" /D "%INSTALL%" "%EXE%" --updated',
            'echo [engram-update] done (success) >> "%LOG%"',
            'rmdir /S /Q "%OLDDIR%" >nul 2>&1',
            '(goto) 2>nul & del "%~f0"',
            # --- rollback paths (bat NOT deleted — left with the log for diagnosis) ---
            ":restore_old",
            'echo [engram-update] swap failed; restoring previous install >> "%LOG%"',
            'set "STEP=swap_restore"',
            'rmdir /S /Q "%INSTALL%" >nul 2>&1',
            'move "%OLDDIR%" "%INSTALL%" >> "%LOG%" 2>&1',
            "goto relaunch_old",
            ":fail_no_swap",
            'echo [engram-update] could not move install aside; left untouched >> "%LOG%"',
            "goto relaunch_old",
            ":fail",
            'echo [engram-update] failed at step %STEP%; install untouched >> "%LOG%"',
            'rmdir /S /Q "%NEWDIR%" >nul 2>&1',
            "goto relaunch_old",
            ":relaunch_old",
            'echo [engram-update] rolled back to previous install >> "%LOG%"',
            'echo result=failed>"%RESULT%"',
            'echo version=%VER%>>"%RESULT%"',
            'echo step=%STEP%>>"%RESULT%"',
            'start "" /D "%INSTALL%" "%EXE%" --updated',
            'echo [engram-update] done (rolled back) >> "%LOG%"',
            "endlocal",
        ]
        return "\n".join(lines) + "\n"
```

Then update the caller `_restart_windows` to compute the marker path and pass the two new
args. Add near the `log_path = ...` line:

```python
        result_path = Path.home() / ".engram" / "update_result.txt"
```

and change the `bat_content = self._render_update_bat(...)` call to include:

```python
            version=version,
            result_path=str(result_path),
```

(`version` is already computed above as `version = self.latest_version or "new"`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -k render_bat -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "fix(updater): bound the restart wait, filter by image name, write a result marker, relaunch with --updated

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: POSIX restart passes `--updated`

**Files:**
- Modify: `backend/app/core/updater.py` — `_restart_linux_macos`
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
import os, shutil, sys
from pathlib import Path
from app.core.updater import UpdateChecker

def test_restart_posix_appends_updated_flag(monkeypatch, tmp_path):
    """POSIX exec-in-place re-runs run.py, which would open a 2nd browser tab; the
    --updated flag suppresses it."""
    staging = tmp_path / "stage"
    (staging / "engram").mkdir(parents=True)
    (staging / "engram" / "engram").write_text("binary")

    checker = UpdateChecker()
    checker.staging_path = staging

    monkeypatch.setattr(shutil, "copy2", lambda *a, **k: None)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["/app/engram"])
    captured = {}
    monkeypatch.setattr(os, "execv", lambda path, argv: captured.update(path=path, argv=argv))

    checker._restart_linux_macos()
    assert "--updated" in captured["argv"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_restart_posix_appends_updated_flag -v`
Expected: FAIL — `--updated` not in argv (current code execs raw `sys.argv`).

- [ ] **Step 3: Implement**

In `_restart_linux_macos`, replace the final `os.execv(sys.executable, sys.argv)` line with:

```python
        # Re-exec re-runs run.py, which would open a second browser tab; --updated suppresses
        # it so the already-open tab just reconnects.
        argv = sys.argv if "--updated" in sys.argv else [*sys.argv, "--updated"]
        os.execv(sys.executable, argv)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_restart_posix_appends_updated_flag -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "fix(updater): pass --updated on POSIX re-exec to suppress the duplicate tab

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Consume the result marker into status fields

**Files:**
- Modify: `backend/app/core/updater.py` — `UpdateChecker.__init__`, new `_consume_update_result_marker`, `start`
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
from app.core.updater import UpdateChecker

def test_consume_marker_success(tmp_path):
    marker = tmp_path / "update_result.txt"
    marker.write_text("result=success\nversion=9.9.9\n", encoding="utf-8")
    c = UpdateChecker()
    c._consume_update_result_marker(marker)
    assert c.last_update_success_version == "9.9.9"
    assert c.last_update_error is None
    assert not marker.exists()  # consumed once

def test_consume_marker_failed(tmp_path):
    marker = tmp_path / "update_result.txt"
    marker.write_text("result=failed\nversion=9.9.9\nstep=verify\n", encoding="utf-8")
    c = UpdateChecker()
    c._consume_update_result_marker(marker)
    assert "9.9.9" in c.last_update_error and "verify" in c.last_update_error
    assert c.last_update_success_version is None
    assert not marker.exists()

def test_consume_marker_absent_is_noop(tmp_path):
    c = UpdateChecker()
    c._consume_update_result_marker(tmp_path / "nope.txt")
    assert c.last_update_error is None and c.last_update_success_version is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -k consume_marker -v`
Expected: FAIL — `_consume_update_result_marker` undefined / fields missing.

- [ ] **Step 3: Implement**

In `__init__`, after `self.error: str | None = None`, add:

```python
        self.last_update_error: str | None = None
        self.last_update_success_version: str | None = None
```

Add the method (e.g. just after `_load_skipped_version`):

```python
    def _consume_update_result_marker(self, path: Path | None = None) -> None:
        """Read + delete the Windows helper's result marker (~/.engram/update_result.txt)
        and translate it into last_update_error / last_update_success_version. Best-effort."""
        marker = path or (Path.home() / ".engram" / "update_result.txt")
        if not marker.exists():
            return
        data: dict[str, str] = {}
        try:
            for line in marker.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    data[key.strip()] = value.strip()
        except OSError as exc:
            logger.debug(f"Could not read update result marker: {exc}")
            return
        finally:
            marker.unlink(missing_ok=True)

        version = data.get("version") or "?"
        if data.get("result") == "success":
            self.last_update_success_version = version
            logger.info(f"Update applied successfully to {version}")
        elif data.get("result") == "failed":
            step = data.get("step") or "unknown"
            self.last_update_error = (
                f"Update to {version} couldn't be applied automatically (step: {step}). "
                "It'll retry on the next check, or download manually."
            )
            logger.warning(f"Update to {version} failed at step {step}")
```

In `start()`, add as the first line of the method body (before `self._prune_staging()`):

```python
        self._consume_update_result_marker()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -k consume_marker -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "feat(updater): read the helper result marker into success/error status fields

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `get_status()` exposes the new fields

**Files:**
- Modify: `backend/app/core/updater.py` — `get_status`
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
from app.core.updater import UpdateChecker

def test_get_status_includes_marker_fields():
    c = UpdateChecker()
    c.last_update_error = "boom"
    c.last_update_success_version = "9.9.9"
    s = c.get_status()
    assert s["last_update_error"] == "boom"
    assert s["last_update_success_version"] == "9.9.9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_get_status_includes_marker_fields -v`
Expected: FAIL — KeyError.

- [ ] **Step 3: Implement**

In `get_status()`, add two entries to the returned dict (next to `"error"`):

```python
            "last_update_error": self.last_update_error,
            "last_update_success_version": self.last_update_success_version,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_get_status_includes_marker_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/updater.py backend/tests/unit/test_updater.py
git commit -m "feat(updater): expose last_update_error/last_update_success_version in REST status

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Broadcast the new fields over WebSocket

**Files:**
- Modify: `backend/app/services/event_broadcaster.py` — `broadcast_update_status`; `backend/app/core/updater.py` — `_broadcast`
- Test: `backend/tests/unit/test_updater.py`

> ⚠️ The UI reads update state ONLY from the WS message; a field present in REST but missing
> from the WS payload is silently dropped. Both sides must carry it.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_updater.py
import pytest
from app.services.event_broadcaster import EventBroadcaster

class _FakeWS:
    def __init__(self): self.sent = []
    async def broadcast(self, data): self.sent.append(data)

@pytest.mark.asyncio
async def test_broadcast_update_status_carries_marker_fields():
    ws = _FakeWS()
    eb = EventBroadcaster(ws)
    await eb.broadcast_update_status(
        state="ready", last_update_error="boom", last_update_success_version="9.9.9"
    )
    payload = ws.sent[-1]
    assert payload["last_update_error"] == "boom"
    assert payload["last_update_success_version"] == "9.9.9"
```

(`EventBroadcaster.__init__` takes the connection manager as its first arg — confirm the param
name in the file and pass positionally as above.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_broadcast_update_status_carries_marker_fields -v`
Expected: FAIL — `broadcast_update_status` has no such kwargs (TypeError).

- [ ] **Step 3: Implement**

In `event_broadcaster.py`, extend the `broadcast_update_status` signature (after `error`):

```python
        error: str | None = None,
        last_update_error: str | None = None,
        last_update_success_version: str | None = None,
```

and before `await self._ws.broadcast(data)` add:

```python
        if last_update_error is not None:
            data["last_update_error"] = last_update_error
        if last_update_success_version is not None:
            data["last_update_success_version"] = last_update_success_version
```

In `updater.py` `_broadcast`, pass them through:

```python
            await self._broadcaster.broadcast_update_status(
                state=self.state,
                latest_version=self.latest_version,
                release_notes=self.release_notes,
                release_url=self.release_url,
                error=self.error,
                last_update_error=self.last_update_error,
                last_update_success_version=self.last_update_success_version,
            )
```

Also, so currently-connected clients get the notice even on the `UP_TO_DATE` path (which does
not broadcast), add to `start()` right after `await self._check(skipped_version)`:

```python
        if self.last_update_error or self.last_update_success_version:
            await self._broadcast()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_updater.py::test_broadcast_update_status_carries_marker_fields -v`
Expected: PASS

- [ ] **Step 5: Run the full updater suite + commit**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -v` (expect all PASS)

```bash
git add backend/app/core/updater.py backend/app/services/event_broadcaster.py backend/tests/unit/test_updater.py
git commit -m "feat(updater): broadcast last_update_error/success over WS (REST+WS parity)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Suppress the duplicate browser tab on restart (`run.py`)

**Files:**
- Modify: `backend/run.py` — new `_schedule_browser_open` + `main()` wiring
- Test: `backend/tests/unit/test_run_browser.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_run_browser.py
import threading
import webbrowser
import run  # backend/run.py (pytest runs from backend/)

class _FakeTimer:
    """Captures (interval, fn) and runs fn immediately on .start()."""
    last = None
    def __init__(self, interval, fn, args=None, kwargs=None):
        self.interval, self.fn = interval, fn
        self.args, self.kwargs = args or [], kwargs or {}
        _FakeTimer.last = self
    def start(self):
        self.fn(*self.args, **self.kwargs)

def test_normal_launch_opens_tab(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    run._schedule_browser_open("http://localhost:8000", updated=False)
    assert opened == ["http://localhost:8000"]

def test_updated_relaunch_suppresses_tab_when_client_connects(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    from app.api.websocket import manager
    monkeypatch.setattr(manager, "active_connections", ["client"])  # tab reconnected
    run._schedule_browser_open("http://localhost:8000", updated=True)
    assert opened == []  # existing tab reconnected → no new tab

def test_updated_relaunch_opens_tab_when_no_client(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    from app.api.websocket import manager
    monkeypatch.setattr(manager, "active_connections", [])  # old tab gone / port changed
    run._schedule_browser_open("http://localhost:8000", updated=True)
    assert opened == ["http://localhost:8000"]  # safeguard opened one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_run_browser.py -v`
Expected: FAIL — `run._schedule_browser_open` does not exist.

- [ ] **Step 3: Implement**

Add a module-level helper to `backend/run.py` (above `main()`):

```python
def _schedule_browser_open(url: str, *, updated: bool) -> None:
    """Open the dashboard tab after startup — unless this is an update relaunch.

    Normal launch: open after 1.5s. Update relaunch (--updated): the existing tab reconnects on
    its own, so suppress the new tab; as a safeguard open one after 5s only if no WebSocket
    client has connected (the old tab was closed, or _find_free_port picked a different port)."""
    import threading
    import webbrowser

    if not updated:
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
        return

    def _open_if_no_client() -> None:
        from app.api.websocket import manager

        if not manager.active_connections:
            webbrowser.open(url)

    threading.Timer(5.0, _open_if_no_client).start()
```

In `main()`, replace the `threading.Timer(1.5, webbrowser.open, args=[url]).start()` line with:

```python
            _schedule_browser_open(url, updated="--updated" in sys.argv[1:])
```

(The local `import webbrowser` / `import threading` inside `main()` may remain; they're harmless.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_run_browser.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/run.py backend/tests/unit/test_run_browser.py
git commit -m "fix(restart): suppress duplicate browser tab on update relaunch (--updated) with a no-client safeguard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: End-to-end Windows swap test (closes the blind spot)

This is the test the prior fixes lacked: it spawns the **real** helper via the **real**
`_spawn_detached_helper` (CREATE_NO_WINDOW), against a dummy install/staging tree and a dummy
parent process whose image name matches the wait filter, and asserts the swap completes (it never
hangs) and the marker is written. Windows-only.

**Files:**
- Test: `backend/tests/unit/test_updater.py`

- [ ] **Step 1: Write the test**

```python
# backend/tests/unit/test_updater.py
import os, shutil, subprocess, sys, time
from pathlib import Path
import pytest
from app.core.updater import UpdateChecker

pytestmark_win = pytest.mark.skipif(sys.platform != "win32", reason="Windows helper only")

def _make_build(root: Path, exe_name: str, marker_text: str):
    """Create a minimal onedir-shaped build tree with the verify sentinels."""
    root.mkdir(parents=True, exist_ok=True)
    # dummy launcher = a renamed copy of cmd.exe so its IMAGENAME matches the wait filter
    shutil.copy2(os.environ["COMSPEC"], root / exe_name)
    (root / "_internal" / "certifi").mkdir(parents=True, exist_ok=True)
    (root / "_internal" / "app" / "static").mkdir(parents=True, exist_ok=True)
    (root / "_internal" / "base_library.zip").write_text("z")
    (root / "_internal" / "certifi" / "cacert.pem").write_text("ca")
    (root / "_internal" / "app" / "static" / "index.html").write_text("<html>")
    (root / "VERSION.txt").write_text(marker_text)

def _spawn_dummy_parent(exe_path: Path) -> subprocess.Popen:
    # long-lived process named like the launcher, so `tasklist /FI IMAGENAME eq <exe>` matches
    return subprocess.Popen([str(exe_path), "/c", "ping -n 60 127.0.0.1 >nul"])

def _run_helper(checker, *, src, install, new_dir, old_dir, log, result, exe, pid, version):
    bat = checker._render_update_bat(
        src=str(src), install=str(install), new_dir=str(new_dir), old_dir=str(old_dir),
        log_path=str(log), exe=exe, pid=pid, version=version, result_path=str(result),
    )
    bat_path = Path(os.environ["TEMP"]) / f"engram_update_test_{pid}.bat"
    with open(bat_path, "w", newline="\r\n") as f:
        f.write(bat.replace('del "%~f0"', f'del "{bat_path}"'))  # self-delete the test bat
    checker._spawn_detached_helper(["cmd", "/c", str(bat_path)])
    return bat_path

def _wait_for(predicate, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return False

@pytestmark_win
def test_windows_swap_succeeds_end_to_end(tmp_path):
    """The real helper, spawned the real way, completes the swap and writes a success marker —
    i.e. it does NOT hang in :wait (the bug)."""
    exe = "engram.exe"
    src = tmp_path / "stage" / "engram"
    install = tmp_path / "app" / "engram"
    _make_build(src, exe, "NEW")
    _make_build(install, exe, "OLD")
    log = tmp_path / "helper.log"
    result = tmp_path / "update_result.txt"

    parent = _spawn_dummy_parent(install / exe)
    try:
        checker = UpdateChecker()
        bat_path = _run_helper(
            checker, src=src, install=install,
            new_dir=tmp_path / "app" / "engram.new", old_dir=tmp_path / "app" / "engram.old",
            log=log, result=result, exe=exe, pid=parent.pid, version="9.9.9",
        )
        time.sleep(2)  # let it enter the wait loop
        parent.terminate()  # now the wait loop should observe the exit and proceed
        assert _wait_for(lambda: result.exists()), f"no marker; helper log:\n{log.read_text() if log.exists() else '<none>'}"
        text = result.read_text()
        assert "result=success" in text, text
        assert (install / "VERSION.txt").read_text() == "NEW"  # swap happened
    finally:
        if parent.poll() is None:
            parent.terminate()
        # kill any relaunched dummy (start "" ... engram.exe --updated)
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)

@pytestmark_win
def test_windows_swap_failure_writes_marker_and_keeps_install(tmp_path):
    """A missing sentinel makes verify fail → failure marker + install left intact."""
    exe = "engram.exe"
    src = tmp_path / "stage" / "engram"
    install = tmp_path / "app" / "engram"
    _make_build(src, exe, "NEW")
    (src / "_internal" / "app" / "static" / "index.html").unlink()  # break verify
    _make_build(install, exe, "OLD")
    log = tmp_path / "helper.log"
    result = tmp_path / "update_result.txt"

    parent = _spawn_dummy_parent(install / exe)
    try:
        checker = UpdateChecker()
        _run_helper(
            checker, src=src, install=install,
            new_dir=tmp_path / "app" / "engram.new", old_dir=tmp_path / "app" / "engram.old",
            log=log, result=result, exe=exe, pid=parent.pid, version="9.9.9",
        )
        time.sleep(2)
        parent.terminate()
        assert _wait_for(lambda: result.exists())
        assert "result=failed" in result.read_text()
        assert (install / "VERSION.txt").read_text() == "OLD"  # install untouched
    finally:
        if parent.poll() is None:
            parent.terminate()
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
```

- [ ] **Step 2: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_updater.py -k windows_swap -v`
Expected (on Windows): PASS — 2 tests. (On non-Windows: SKIPPED.)
If a test fails, the assertion prints the helper log; inspect it to see which step hung/failed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_updater.py
git commit -m "test(updater): end-to-end Windows swap in the real detached spawn context

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Frontend types — add the two fields

**Files:**
- Modify: `frontend/src/types/index.ts` (`UpdateStatusMessage`, `UpdateStatus`)
- Modify: `frontend/src/app/hooks/useJobManagement.ts` (`toUpdateStatus`)

- [ ] **Step 1: Add fields to both interfaces**

In `frontend/src/types/index.ts`, add to `UpdateStatusMessage` (after `error?:`):

```typescript
    last_update_error?: string | null;
    last_update_success_version?: string | null;
```

and to `UpdateStatus` (after `error: string | null;`):

```typescript
    last_update_error: string | null;
    last_update_success_version: string | null;
```

- [ ] **Step 2: Map them in `toUpdateStatus`**

In `frontend/src/app/hooks/useJobManagement.ts`, add to the returned object in `toUpdateStatus`
(after `error: raw.error ?? null,`):

```typescript
        last_update_error: raw.last_update_error ?? null,
        last_update_success_version: raw.last_update_success_version ?? null,
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/app/hooks/useJobManagement.ts
git commit -m "feat(ui): add last_update_error/last_update_success_version to UpdateStatus types

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: UpdateBanner failure variant

**Files:**
- Modify: `frontend/src/app/components/UpdateBanner.tsx`
- Test: `frontend/src/app/components/__tests__/UpdateBanner.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/app/components/__tests__/UpdateBanner.test.tsx  (add these cases)
import { render, screen } from "@testing-library/react";
import { UpdateBanner } from "../UpdateBanner";
import type { UpdateStatus } from "../../../types";

const base: UpdateStatus = {
    state: "idle", current_version: "9.9.9", latest_version: null, release_notes: null,
    release_url: "https://example/r", download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: null,
};

it("renders a failure notice when last_update_error is set", () => {
    render(
        <UpdateBanner
            updateStatus={{ ...base, last_update_error: "Update to 9.9.9 couldn't be applied (step: verify)." }}
            onShowNotes={() => {}} onDismiss={() => {}}
        />,
    );
    expect(screen.getByText(/couldn't be applied/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /download manually/i })).toHaveAttribute("href", "https://example/r");
});

it("renders nothing when neither ready nor a failure", () => {
    const { container } = render(
        <UpdateBanner updateStatus={base} onShowNotes={() => {}} onDismiss={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/components/__tests__/UpdateBanner.test.tsx`
Expected: FAIL — failure notice not rendered (component returns null unless `state === "ready"`).

- [ ] **Step 3: Implement**

In `UpdateBanner.tsx`, replace the early-return guard

```tsx
    if (!updateStatus || updateStatus.state !== "ready") return null;
```

with logic that also handles the failure notice. Add a local dismissed state and a failure block.
Just after the existing `const [restarting, setRestarting] = useState(false);` add:

```tsx
    const [failureDismissed, setFailureDismissed] = useState(false);
```

Replace the guard with:

```tsx
    const showReady = !!updateStatus && updateStatus.state === "ready";
    const showFailure = !!updateStatus?.last_update_error && !failureDismissed;
    if (!showReady && !showFailure) return null;
```

Then, immediately inside the returned markup, render the failure banner before/above the ready
banner. Wrap the return in a fragment and add this block (uses the magenta accent `sv.magenta`):

```tsx
    if (showFailure && !showReady) {
        return (
            <div
                data-testid="update-failure-banner"
                style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 28px",
                    background: `${sv.magenta}10`, borderBottom: `1px solid ${sv.magenta}55`,
                    fontFamily: sv.mono, fontSize: 12, letterSpacing: "0.06em", color: sv.magenta,
                }}
            >
                <X size={14} color={sv.magenta} style={{ flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{updateStatus!.last_update_error}</span>
                {updateStatus!.release_url && (
                    <a
                        href={updateStatus!.release_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: sv.magenta, textTransform: "uppercase", fontSize: 10, letterSpacing: "0.14em" }}
                    >
                        Download manually
                    </a>
                )}
                <button
                    type="button"
                    onClick={() => setFailureDismissed(true)}
                    style={{ color: sv.inkDim, background: "transparent", border: "none", cursor: "pointer" }}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }
```

(Confirm `sv.magenta` exists in `./synapse`; if the export differs, use the project's magenta
token. Keep the existing ready-banner markup for the `showReady` path unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/components/__tests__/UpdateBanner.test.tsx`
Expected: PASS (existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/UpdateBanner.tsx frontend/src/app/components/__tests__/UpdateBanner.test.tsx
git commit -m "feat(ui): show a failure notice when an update couldn't be applied

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Success toast (localStorage-deduped) + remove the dead ref toast

The page reloads after a successful update (bundle-version guard in `syncUpdateStatus`), which
wipes any in-memory ref — so the toast must be driven by the server field and deduped via
`localStorage`.

**Files:**
- Create: `frontend/src/app/hooks/useUpdateSuccessToast.ts`
- Test: `frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts` (new)
- Modify: `frontend/src/app/App.tsx`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts
import { renderHook } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { useUpdateSuccessToast } from "../useUpdateSuccessToast";
import type { UpdateStatus } from "../../../types";

const success = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => success(...a) } }));

const status = (v: string | null): UpdateStatus => ({
    state: "up_to_date", current_version: "9.9.9", latest_version: null, release_notes: null,
    release_url: null, download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: v,
});

describe("useUpdateSuccessToast", () => {
    beforeEach(() => { success.mockClear(); localStorage.clear(); });

    it("toasts once when a new success version arrives", () => {
        const { rerender } = renderHook(({ s }) => useUpdateSuccessToast(s), { initialProps: { s: status("9.9.9") } });
        expect(success).toHaveBeenCalledTimes(1);
        rerender({ s: status("9.9.9") }); // same version again (e.g. another status push)
        expect(success).toHaveBeenCalledTimes(1); // deduped
    });

    it("does not toast when there is no success version", () => {
        renderHook(() => useUpdateSuccessToast(status(null)));
        expect(success).not.toHaveBeenCalled();
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useUpdateSuccessToast.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the hook**

```ts
// frontend/src/app/hooks/useUpdateSuccessToast.ts
import { useEffect } from "react";
import { toast } from "sonner";
import type { UpdateStatus } from "../../types";

const KEY = "engram:lastSuccessToastVersion";

/**
 * Toast "you're now on vX" exactly once after a successful self-update.
 * Driven by the server field (survives the post-update page reload) and deduped via
 * localStorage (an in-memory ref would be wiped by that reload).
 */
export function useUpdateSuccessToast(updateStatus: UpdateStatus | null): void {
    const version = updateStatus?.last_update_success_version ?? null;
    useEffect(() => {
        if (!version) return;
        try {
            if (localStorage.getItem(KEY) === version) return;
            localStorage.setItem(KEY, version);
        } catch {
            // localStorage unavailable — degrade to once-per-mount
        }
        toast.success(`You're now on engram v${version}`);
    }, [version]);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/app/hooks/__tests__/useUpdateSuccessToast.test.ts`
Expected: PASS

- [ ] **Step 5: Wire into App.tsx + remove the dead ref toast**

Read `frontend/src/app/App.tsx` around the `pendingUpdateVersionRef` usages first. Then:

1. Add the import near the other hook imports:
   ```tsx
   import { useUpdateSuccessToast } from "./hooks/useUpdateSuccessToast";
   ```
2. Call it next to the `useJobManagement(...)` line:
   ```tsx
   useUpdateSuccessToast(updateStatus);
   ```
3. Delete the dead success-toast effect (the `useEffect` containing
   `toast.success(\`Updated to ${pendingUpdateVersionRef.current} ✓\`)` and its
   `state === "up_to_date"` check — around L147–156).
4. Delete the `pendingUpdateVersionRef` declaration and remove the two
   `onRestart={() => { pendingUpdateVersionRef.current = ... }}` props passed to `<UpdateBanner>`
   and `<UpdateModal>` (drop the `onRestart` prop entirely at both call sites).
5. If `toast` is no longer referenced elsewhere in App.tsx, remove its now-unused import.

- [ ] **Step 6: Type-check, run frontend tests, commit**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/app`
Expected: no type errors; tests PASS.
If `npm install` rewrote `package-lock.json`, run `git checkout package-lock.json` before adding.

```bash
git add frontend/src/app/hooks/useUpdateSuccessToast.ts frontend/src/app/hooks/__tests__/useUpdateSuccessToast.test.ts frontend/src/app/App.tsx
git commit -m "feat(ui): toast on successful self-update (server-driven, localStorage-deduped); drop dead ref toast

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Backend:** `cd backend && uv run pytest tests/unit/test_updater.py tests/unit/test_run_browser.py -v` — all PASS (Windows swap tests run; on non-Windows they SKIP).
- [ ] **Backend lint:** `cd backend && uv run ruff check app/core/updater.py app/services/event_broadcaster.py run.py && uv run ruff format --check app/core/updater.py`
- [ ] **Frontend:** `cd frontend && npx tsc --noEmit && npx vitest run src/app` — all PASS.
- [ ] **Manual smoke (optional, Windows, requires a frozen build that contains this code):** build, install an older version, trigger an update, click Restart — confirm: app relaunches on the new version in the SAME tab (no second tab), and a "you're now on vX" toast appears. Then simulate a failed swap (e.g. lock a file) and confirm the magenta failure banner + rollback to the old version.

## Notes on test runnability

- All backend unit tests (Tasks 1, 3–7) and the bat-render tests (Task 2) run on any platform.
- Task 8's swap tests are Windows-gated and runnable on the dev box; they are the automated form
  of the 2026-06-06 manual reproduction. They spawn real processes — the `finally`/`taskkill`
  teardown prevents orphaned dummies.
- Frontend tests use Vitest + React Testing Library (already in the repo).
