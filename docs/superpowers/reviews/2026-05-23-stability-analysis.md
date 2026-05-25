# Codebase Stability Analysis — Toward a Stable Release

**Date:** 2026-05-23
**Version analyzed:** 0.6.0
**Branch:** `claude/codebase-stability-analysis-r8pBy`

## Method

Read-only audit of the backend (`backend/app`, ~24.5k LOC Python), frontend
(`frontend/src`, 79 TS/TSX files), tests, and CI. Verified baseline health by
running the suite and linters locally. Key findings were spot-checked against
the actual source rather than taken on faith.

**Baseline health (verified):**

- `uv run pytest tests/unit` → **807 passed in 37s**
- `uv run ruff check .` → **clean (exit 0)**
- `grep TODO|FIXME|HACK|XXX|WORKAROUND` across `backend/app` + `frontend/src` → **0 matches**
- ~1,004 backend tests collected (unit/integration/pipeline; `real_data` + `live` deselected)
- CI gates (all blocking on PR): backend lint, backend unit (ubuntu+windows),
  backend integration, boot smoke test, Alembic migration check, frontend
  lint + build + bundle-size, frontend unit (vitest), E2E (Playwright, 2 retries).
  Plus CodeQL and Dependabot.

The project is architecturally healthy. The gaps that stand between 0.6.0 and a
confident stable release cluster in three areas: **process/async lifecycle
robustness**, **test coverage of the most complex code paths**, and **frontend
failure handling**.

---

## Priority roadmap

### P0 — Release blockers (stability bugs that affect real users)

| # | Area | File / line | Risk |
|---|------|-------------|------|
| P0-1 | Process lifecycle | `core/extractor.py:787` `cancel()` | `terminate()` with no `wait()`/`kill()` escalation; a hung `makemkvcon` survives cancel. No child-process cleanup on backend shutdown (lifespan teardown). Orphans cause duplicate jobs + drive conflicts (per CLAUDE.md, the #1 documented hazard). |
| P0-2 | Database | `services/job_manager.py:~703` | A single `async_session` is held across the entire (multi-hour) rip → `SQLITE_BUSY` risk under concurrent jobs. |
| P0-3 | Cross-thread errors | `services/job_manager.py:~863`, `services/matching_coordinator.py:~562` | `asyncio.run_coroutine_threadsafe(...)` futures from the MakeMKV callback thread aren't tracked; a failed broadcast (title-complete / progress) vanishes with no log. |
| P0-4 | Frontend errors | `app/hooks/useJobManagement.ts:43-50` | `.json()` called without checking `res.ok`; a backend 5xx flows into `setJobs` as corrupt state. No user-facing error. |

**Recommended fixes:**

- P0-1: escalate `terminate()` → `wait(timeout)` → `kill()`; track all child PIDs
  (`psutil` is already a dependency) and drain them in the FastAPI lifespan
  shutdown. Add a regression test that simulates a non-exiting process.
- P0-2: use short, per-operation sessions for progress writes instead of one
  long-lived session per rip.
- P0-3: store the returned `Future` and log exceptions in its done-callback.
- P0-4: `if (!res.ok) throw ...`; surface via the already-bundled `sonner` toast.

### P1 — Should-fix before declaring stable

**Test coverage of the riskiest code (biggest gap):**

- `matcher/episode_identification.py` — **1,553 lines, the single most complex
  module** — is exercised only by `tests/real_data/` tests that **auto-skip in
  CI**. No mocked unit coverage of the core matching logic.
- `core/curator.py` (296 LOC) and `core/sentinel.py` (359 LOC, the
  drive-detection entry point) have **no direct unit tests**.
- `matcher/core/engine.py` (619 LOC) — no direct unit tests.
- **No coverage threshold is enforced** in CI; coverage is uploaded but never
  gates a merge (`.github/workflows/ci.yml`). `app/matcher/*` is omitted from
  coverage entirely.

**Async coordination:**

- `services/matching_coordinator.py:337` — the `_subtitle_ready` event is created
  in `start_subtitle_download` but checked-by-key in matching; depending on task
  ordering matching may skip the wait. The 300s `wait_for` timeout prevents a
  permanent hang, but the coordination is order-dependent and fragile. Create the
  event eagerly when the job starts, before any match task can reference it.

**Frontend resilience:**

- `hooks/useWebSocket.ts:36` — fixed 3s reconnect, **no exponential backoff** and
  **no state resync on reconnect** → stale UI after a network blip and
  reconnect-storm load on flapping connections.
- `components/HistoryPage.tsx` — `.catch(() => {})` silently swallows failures,
  leaving lists empty with no explanation.
- `app/hooks/useJobManagement.ts:48-50` — sequential **N+1** title fetch (one
  `await` per job); slow with many jobs. Also un-debounced refetch on an unknown
  `job_update` can fan out into a burst of fetches.

### P2 — Polish

- Replace remaining `console.error` / silent catches with consistent
  user-visible errors. (Note: the verbose debug `console.log`s in
  `useJobManagement.ts` are already correctly `import.meta.env.DEV`-gated — good.)
- Normalize frontend dependency ranges (mix of exact and caret); add a bundle
  budget given Framer Motion + Recharts.
- Minor backend cleanups: `extractor.py` watchdog `thread.join(timeout=2.0)`
  can leave the thread alive; `episode_identification.py:~590` whisper temp
  chunks under `tempfile.gettempdir()/whisper_chunks` are never cleaned up.

---

## Detailed findings

### Backend — process / subprocess management

- **HIGH** `core/extractor.py:787` — `cancel()` terminates without waiting or
  escalating to `kill()`.
- **HIGH** No subprocess drain on backend shutdown (lifespan) — crash/Ctrl-C
  leaves `makemkvcon` running.
- **MED** `core/extractor.py:~638` — stall watchdog `thread.join(timeout=2.0)`
  may leave the thread alive.
- **MED** `core/extractor.py:~684` — process cleanup inside the exception path
  isn't validated; a failed `terminate()` leaks silently.

### Backend — concurrency / race conditions

- **HIGH** `services/matching_coordinator.py:337` — `_subtitle_ready` created in
  one path, awaited-by-key in another; order-dependent (mitigated by 300s timeout).
- **MED** module-level singletons (`event_broadcaster`, `state_machine`,
  `job_manager`) broadcast from concurrent jobs with no serialization guard.
- **MED** `services/job_manager.py:~777` `_title_file_cache` dict mutated from
  both the callback thread and the FS-monitor task without a lock.
- **MED** per-job caches (`_discdb_mappings`, `_episode_runtimes`) can be cleared
  on terminal state while a match task still reads them.

### Backend — error handling

- **HIGH** `services/job_manager.py:~863` / `matching_coordinator.py:~562` —
  `run_coroutine_threadsafe` futures not tracked; errors dropped.
- **MED** `core/extractor.py:~54` `_safe_callback` swallows all exceptions from
  user callbacks; a crashing `title_complete` callback only logs.
- **MED** several `await session.commit()` sites lack an explicit rollback on
  failure (relies on implicit SQLModel behavior).

### Backend — database / transactions

- **MED** long-held session across rip (P0-2).
- **MED** `session.expunge()` / `refresh()` after ripping without error handling
  (`job_manager.py:~770`).
- **LOW** FS-monitor loop opens a new session every ~2s.

### Backend — resource leaks

- **MED** whisper temp chunks never deleted (`episode_identification.py:~590`).
- **LOW** subtitle tasks / stale WS connections slowly accumulate; eventual GC /
  heartbeat reclaims them.

### Backend — state machine

- **MED** a `CancelledError` mid-MATCHING can briefly expose a half-complete
  state to clients before restart cleanup marks it FAILED.
- **LOW** `can_transition()` permits redundant same-state transitions.

### Frontend

- **HIGH** `useJobManagement.ts:43-50` — no `res.ok` check (P0-4).
- **HIGH** `HistoryPage.tsx` — `.catch(() => {})` hides failures.
- **MED** `useWebSocket.ts:36` — fixed reconnect delay, no backoff, no resync.
- **MED** `useJobManagement.ts` — N+1 sequential fetch; un-debounced refetch.
- **MED** `ConfigWizard.tsx:204,207` — non-null assertions on optional API paths.
- **LOW** `AppIcon.tsx:31` `globalThis as any`; `main.tsx:41` root `!` assertion.
- **GOOD** `tsconfig.json` strict mode on, `noUnusedLocals/Parameters`; debug
  logs DEV-gated; an error boundary exists at `main.tsx`.

### Test / CI gaps

- `matcher/episode_identification.py` (1,553 LOC) covered only by skipping
  `real_data` tests.
- `curator.py`, `sentinel.py`, `matcher/core/engine.py` — no direct unit tests.
- No coverage threshold enforced; `app/matcher/*` omitted from coverage.
- Frontend is almost entirely E2E-validated (12 specs, ~73 cases); only ~5
  vitest files. One E2E `test.fixme` (cancel button) is unfinished.

---

## Suggested 1.0 acceptance criteria

1. Zero orphaned `makemkvcon` processes across N consecutive real rips
   (including a cancel and a mid-rip backend restart).
2. All P0 closed; P1 closed or explicitly deferred with rationale.
3. CI enforces a coverage floor; `episode_identification.py`, `curator.py`,
   `sentinel.py` have mocked unit coverage of their primary paths.
4. Frontend surfaces every API/WS failure to the user (no silent catches).
5. A documented "concurrent jobs" soak test passes without `SQLITE_BUSY`.
