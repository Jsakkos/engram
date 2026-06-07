# Restart-to-update keeps failing — root cause: `tasklist | find` deadlocks in the console-less helper

**Date:** 2026-06-06
**Status:** Root cause found and reproduced. No fix applied yet (per request — discuss first).

## TL;DR

The Windows "Restart to update" has **never once succeeded** on the user's machine across
≥7 attempts spanning *three different* helper implementations (pre-#322 `xcopy`, #322 atomic
swap, the Jun-5 build). Every prior "fix" (#285 Job Object, #322 atomic swap, #338 cwd handle)
addressed a **later** step in the swap that the helper **never reaches**.

The actual failure: the update helper `.bat` is spawned with `DETACHED_PROCESS` (no console).
Its `:wait` loop polls for the parent's exit with `tasklist /FI "PID eq N" | find /I "N"`.
**A `cmd` pipe deadlocks in a console-less process**, so that first `tasklist | find` hangs
**forever**. The helper never detects the parent's exit → never robocopies → never swaps →
never relaunches. From the user's view the app just disappears, and they re-download manually.

This is download/unpack-independent. Staging works fine; the build is verified complete before
the swap is ever offered.

## Evidence chain

1. **`~/.engram/update_helper.log` is 36 bytes — one line:** `[engram-update] start (pid 11916)`.
   In every helper version the next action after that `echo` is the `:wait` loop. The "process
   exited" line that follows the loop was never written → the loop never completed.

2. **The leftover `%TEMP%\engram_update.bat` is the #322 helper** (no `/D` on relaunch, bare
   `if %ERRORLEVEL% GEQ 8`, no `cd /d %TEMP%`, no `cwd=`/`INSTALL=` echo lines). It only deletes
   itself on success → its presence proves the swap failed. Install dir was
   `C:\Users\jonat\Downloads\engram-windows-x64(1)\engram` (a pre-0.16.1 build → has #322, not #338).

3. **History of every attempt** (grep across all rotated logs):

   | Date | Target | Restart fired | `_restart_windows:` line | Outcome |
   |------|--------|---------------|--------------------------|---------|
   | 06-01 | 0.13.0 | ✅ 14:02, 14:09 | 419 (pre-#322 xcopy) | came back on OLD ver → re-staged |
   | 06-02 | 0.14.0 | ✅ 12:59 | 447 | re-staged later |
   | 06-03 | 0.15.0 | ✅ 12:42, 12:44 | 593 (#322) | re-staged → failed |
   | 06-03 | 0.15.2 | ✅ 20:40 | 593 | failed |
   | 06-05 | 0.16.2 | ✅ 17:22 | 622 | hung at `:wait`, manual recovery |

   Different line numbers = different code. **All implementations fail identically** → the bug is
   in a factor common to all of them, not in any one swap implementation.

## Reproduction (deterministic, on this machine)

Mimicked engram's `_spawn_detached_helper` exactly
(`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, passing the parent
PID) + `os._exit(0)`. The helper wrote only `[mimic] start (pid 16772)` and **hung forever**
(confirmed the `cmd /c` was still running 16s later) — byte-for-byte the user's failure.

Head-to-head probe isolating the cause (`tasklist` for a non-existent PID):

| Spawn mode | plain `tasklist` | `tasklist \| find` (the `:wait` check) |
|---|---|---|
| `DETACHED_PROCESS` (engram) | wrote **nothing** | **HANGS** (never returns) |
| `CREATE_NO_WINDOW` (hidden console) | `INFO: No tasks...` | works → `errorlevel=1` → loop exits ✓ |

The only difference is whether the child has a console. Console-less ⇒ `tasklist` emits nothing
and the pipe deadlocks. Earlier ad-hoc `:wait` tests "passed" only because they ran in a normal
console.

Repro artifacts: `C:\Users\jonat\repro\` (`spawn_parent.py`, `helper_mimic.bat`,
`diag_helper.bat`, `probe.bat`).

## Why the 4 prior fixes all missed it

- **#285 (CREATE_BREAKAWAY_FROM_JOB):** keeps the helper alive past parent exit — necessary, but
  the helper then hangs in `:wait` anyway.
- **#322 (atomic robocopy + rename swap + rollback):** all *after* the `:wait` loop. Never reached.
- **#338 (cwd off the install dir so `move install→.old` works):** also *after* `:wait`. Verified
  in an isolated swap harness that ran the swap steps directly (with a console / no detached wait),
  so the harness never reproduced the console-less hang. Correct fix for a real bug — but not
  *this* bug.

Tellingly, the helper docstring already documents the same console-less hazard one line over:
`timeout` was swapped for `ping` because "`timeout` aborts… in the console-less DETACHED_PROCESS
context." The identical console dependency in `tasklist | find` was never connected.

## Current user state

- Running a manually-installed build (likely 0.16.2 = `engram-windows-x64(3)`, extracted 06-05 17:24).
- Four downloaded copies in `Downloads` (`engram-windows-x64`, `(1)`, `(2)`, `(3)`) — the manual
  re-download trail.
- Chicken-and-egg still applies, but benign here: the user manually installs each latest release
  anyway, so the first release that contains the real fix will be hand-installed, and auto-update
  works from there forward.

## Candidate fix directions (for discussion — NOT yet chosen)

1. **Give the helper a console:** spawn `CREATE_NO_WINDOW` instead of `DETACHED_PROCESS` (keep
   breakaway). Probe shows `tasklist|find` then works. Smallest change; need to confirm the
   helper still outlives the parent.
2. **Drop the pipe / drop `tasklist`:** poll process liveness without a `cmd` pipe — e.g.
   `tasklist ... /FO CSV >tmp` then `findstr` on the file, or use a PowerShell helper with
   `Wait-Process -Id`, which works console-less.
3. **Don't poll by PID at all:** have the (exiting) parent drop a sentinel and the helper wait on
   the file + a bounded delay, sidestepping PID reuse *and* the console issue.
4. **Add a hard timeout/bound** to any wait loop so a future regression degrades to "rolled back
   and relaunched" instead of an invisible infinite hang.

Whatever we pick, the helper needs an **end-to-end test in the real detached spawn context**
(the swap harness's blind spot), and ideally a watchdog so a stuck helper can never again leave
the app simply gone.
