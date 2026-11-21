# Prompt audit: Claude Code configuration (2026-11-21)

## Assumptions

- **Scope:** the Claude Code configuration that loads into sessions in this project. Inventory:
  - `CLAUDE.md` (worktree root, 653 lines): the only project instruction file. No `CLAUDE.local.md`, `AGENTS.md`, nested `CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/`, skills, commands, agents or output styles exist in the project. Its `@pytest.fixture` lines are code inside fences, not imports; it imports nothing.
  - `~/.claude/CLAUDE.md`, `~/.claude/{rules,skills,commands,agents,output-styles}/`: none exist.
  - Managed-policy `CLAUDE.md`: none found.
  - Ancestor directory: the main checkout's own `CLAUDE.md` sits above the worktree. It is outside the project and is reported, not edited (finding F1).
  - Installed plugins (16, 63 skill/agent/command files): scanned for signals and reported only, no edits proposed.
  - Not read, by design: `.claude/settings.local.json`, any `.mcp.json`, `~/.claude.json`. Not in the requested scope: the auto-memory files under `~/.claude/projects/.../memory/`.
- **Target model:** Claude Opus 5.5 (`claude-opus-5-5`), the model running this audit. No audited file pins its own model.
- **Non-Anthropic provider markers:** none in the audited files (the Discord bot's Claude Haiku 5.5 is described in `CLAUDE.md`, but the bot's code is outside this scope).

## Summary

`CLAUDE.md` is in good shape as prompt text: its emphasis is rare and every caps marker sits next to a reason, it has no thinking scaffolds, no step-by-step choreography, and its long Key Patterns section is the kind of author-only context an audit should keep. The real problem is **factual drift**. Three findings matter most:

1. **The WebSocket message table (`CLAUDE.md:421-448`) describes a protocol the backend no longer speaks.** It says messages are `{"type", "data"}`; nearly all are flat. Twelve of its sixteen type names (`job_created`, `job_cancelled`, `rip_progress`, `subtitle_progress`, `title_discovered`, `title_matched`, ...) are emitted nowhere, and eight real types (`title_update`, `titles_discovered`, `subtitle_event`, `drive_armed`, `parked_discs`, `update_status`, ...) are missing. A session writing frontend handling from this table will write dead code.
2. **The frontend component map names components that were deleted** (`CyberpunkProgressBar`, removed in the Synapse v2 migration; `TVTitleCard`, `MovieTitleCard`, `EpisodeSelector`, `EditionInput`, `useReviewState`), at `CLAUDE.md:194-195`.
3. **The error-handling section prescribes a convention the code does not follow** (`@handle_errors` "in service methods": applied nowhere in the app, only mentioned in a docstring) and its sample sets `job.state = JobState.FAILED` directly, bypassing the `JobStateMachine` the rest of the file says owns every transition.

Counts: Group 1 (dated prompt text): 0 edits. Group 2 (brittle configuration): 12 findings (7 high, 4 medium, 1 low). Group 3 (tool descriptions): not applicable. Group 4 (request config): not applicable. Flags outside the project: 2.

## Findings

| # | Location | Evidence | Pattern | Why obsolete | Confidence | Action |
|---|---|---|---|---|---|---|
| 1 | `CLAUDE.md:421-448` | `All WebSocket messages follow the format: {"type": "...", "data": {...}}` and the 16-row type table | G2 Volatile specifics | `backend/app/api/websocket.py` and `event_broadcaster.py` emit flat messages (`gpu_status` is the only `data`-wrapped one). No code emits `job_created`, `job_cancelled`, `job_cleared`, `rip_progress`, `subtitle_progress`, `title_discovered`, `title_ripping_started`, `title_ripping_progress`, `title_matching_started`, `title_matched`, `title_state_changed`, `title_failed`. Real types are `job_update`, `title_update`, `titles_discovered`, `subtitle_event`, `drive_event`, `drive_armed`, `backup_progress`, `parked_discs`, `fingerprint_disclosure_required`, `update_status`, `gpu_status`, `ping`. | High | rewrite |
| 2 | `CLAUDE.md:194-195` | `CyberpunkProgressBar` ... `TVTitleCard`, `MovieTitleCard`, `EpisodeSelector`, `EditionInput`, `hooks/useReviewState` | G2 Volatile specifics | None of these exist under `frontend/src`. `CyberpunkProgressBar` was removed by the Synapse v2 migration (`54226b88`, #116; `SvProgressBar` replaced it); `components/ReviewQueue/` now holds `TitleList`, `Inspector`, `IdentityGate`, `SeasonRosterStrip`, `OrderingSelector`, `PlayerControls` and others. | High | rewrite |
| 3 | `CLAUDE.md:152` | `Thin orchestrator (~1,166 lines).` | G2 Volatile specifics | `job_manager.py` is 5,285 lines. A size claim invites the next session to treat it as small. | High | rewrite |
| 4 | `CLAUDE.md:203`, `CLAUDE.md:604` | `(10 spec files)` | G2 Volatile specifics | `frontend/e2e/` has 25 `*.spec.ts` files. | High | rewrite |
| 5 | `CLAUDE.md:141` | `Polls optical drives on Windows using ctypes/kernel32.` | G2 Volatile specifics | `sentinel.py` also implements Linux (`/sys/block/sr*` + `ioctl(CDROM_DRIVE_STATUS)`), and `CLAUDE.md:7` itself says detection works on Windows and Linux. | High | rewrite |
| 6 | `CLAUDE.md:471` | `CORS: Configured for localhost:5173 (Vite dev server) only` | G2 Volatile specifics | `main.py:179-184`: that is the default; the `cors_origins` setting (`config.py:81`) replaces it. | High | rewrite |
| 7 | `CLAUDE.md:601-607` | `### Frontend Testing` lists only Playwright E2E | G2 Volatile specifics | `frontend/package.json` has a `test:unit` script (vitest + Testing Library, colocated `*.test.tsx`), absent from both the Commands block (`:39-47`) and this section. | High | add |
| 8 | `CLAUDE.md:386`, `:389-413` | `Use @handle_errors for standardized error handling in service methods` and the "Common patterns" sample with `job.state = JobState.FAILED` | G1d Unenforced instructions; G1c Padding | `@handle_errors` is defined in `errors.py` but applied nowhere in `backend/app`; its only other mention is a `tmdb_classifier.py` docstring. The three samples are generic try/except idioms, and the first one teaches a direct state assignment while job lifecycle runs through `JobStateMachine.transition` (`:157`, `:217`). | Medium | rewrite (decorator line); remove (samples) |
| 9 | `CLAUDE.md:589` | `Integration tests have caught 2 production bugs (WebSocket parameter mismatches)` | G2 History narratives | Archaeology, not a rule; the rule it supports is already stated at `:452-459`. | Medium | remove |
| 10 | `CLAUDE.md:148` | `only jobs that ran *after* this change have tagged lines` | G1d Migration-relative phrasing | "this change" is a diff against a version of the file the reader never saw. | Medium | rewrite |
| 11 | `CLAUDE.md:347` | `(the badge no longer claims CUDA while silently on CPU)` | G1d Migration-relative phrasing | Describes the old bug, not the current behavior; the preceding clause already states the guarantee. | Medium | remove |
| 12 | `CLAUDE.md:145` | `Episode matching via audio fingerprinting.` | G2 Volatile specifics | Matching is ASR transcription against subtitle references with a chromaprint prepass (`:283-345`). The module docstring says the same thing, so the repository does not flatly contradict it. | Low | flag |
| 13 | `CLAUDE.md:554` (and the pipeline/real-data lists at `:560`, `:565`) | `Files: test_workflow.py, ...` | G2 Volatile specifics | Partial lists (6 of 38 integration files). Not contradicted, since every listed file exists, but they will keep drifting. | Low | flag |
| 14 | Key Patterns, e.g. `:220`, `:227`, `:243`, `:283` | `(#912)`, `(#813)`, `(#760)`, `(#241)` | G2 History narratives (idiom only) | Each bullet states its current rule, and the issue numbers point at rationale. Idiom-dating alone. | Low | flag |

Not a finding: `:11` (`NEVER delete backend/engram.db`), `:452` (`CRITICAL`) and `:646` (`MUST`) each carry their reason, and they encode real constraints (keep-list items 1 and 5). The long Key Patterns section is author-only context (keep-list items 1 and 2).

### Flags outside the project (no edits proposed)

- **F1: the main checkout's `CLAUDE.md`** (ancestor directory of the worktree, checked out on an older branch). It is a 517-line older copy of this file and contradicts it: `React 18` against `React 19` (`:188`), `Recharts, React Hook Form, ... shadcn/ui components` against `Radix UI primitives` (`:190`), plus the missing Discord-bot section and half of Key Patterns. This session's loaded context showed only the worktree copy, so it does not appear to load here, but any session started from the main checkout reads the stale version. The fix is to bring that checkout up to date with `main`, not to edit the file.
- **F2: Installed plugins (report only).** `superpowers` `using-superpowers` (injected at SessionStart) is G1a pressure language at its densest: `EXTREMELY_IMPORTANT`, `YOU DO NOT HAVE A CHOICE`, `ABSOLUTELY MUST`, `even a 1% chance`. Part of it is routing text, which may carry calibrated urgency, but it is injected as behavioral text in every session. On Opus 5.5 that register drives skill over-invocation (for example, process skills loaded for one-line lookups). `superpowers/7.0.0/skills/writing-skills/SKILL.md` has 7 caps markers. The plugin cache also holds a stale `superpowers/7.0.0` next to the installed `7.0.2`; only 7.0.2 loads. No retired model IDs appear in any plugin skill, agent or command. These files belong to their plugin authors; disabling the plugin is the only local lever.

## Proposed diff

One hunk per finding; take them selectively. Findings 12-14 and F1-F2 are report-only.

**Finding 1: WebSocket message table**

```diff
@@ CLAUDE.md:421 @@
 ## WebSocket Message Types

-All WebSocket messages follow the format: `{"type": "...", "data": {...}}`
+Messages are flat JSON objects: `type` plus the fields below at the top level. `gpu_status` is the
+one exception (its payload sits under `data`). Optional fields are omitted when unset, so the
+frontend merges updates (`{...job, ...message}`) without blanking existing values.

 ### Server → Client Messages

-| Type | Data Fields | Description |
-|------|-------------|-------------|
-| `job_update` | `DiscJob` object | Full job state update (sent on any job change) |
-| `job_created` | `DiscJob` object | New job created from disc insertion |
-| `job_cancelled` | `{"job_id": int}` | Job was cancelled by user |
-| `job_cleared` | `{"job_id": int}` | Completed job was cleared from UI |
-| `drive_event` | `{"drive_id": str, "event": "inserted"\|"removed"}` | Physical disc inserted/removed |
-| `subtitle_progress` | `{"job_id": int, "downloaded": int, "total": int, "failed": int}` | Subtitle download progress |
-| `title_discovered` | `{"job_id": int, "title": DiscTitle}` | New title found during ripping |
-| `rip_progress` | `{"job_id": int, "current_bytes": int, "total_bytes": int, "speed": str, "eta": int}` | Ripping progress update |
-| `backup_progress` | `job_id`, `current_bytes`, `total_bytes`, `speed`, `eta` (flat, not nested under `data`) | Full-disc backup copy progress |
-| `title_ripping_started` | `{"job_id": int, "title_id": int}` | Title ripping started |
-| `title_ripping_progress` | `{"job_id": int, "title_id": int, ...}` | Per-track ripping progress |
-| `title_matching_started` | `{"job_id": int, "title_id": int}` | Title matching started |
-| `title_matched` | `{"job_id": int, "title_id": int, ...}` | Successful title match |
-| `title_state_changed` | `{"job_id": int, "title_id": int, "state": str}` | Generic title state change |
-| `title_failed` | `{"job_id": int, "title_id": int, "error": str}` | Title processing failed |
-| `gpu_status` | `{"state": "idle"\|"downloading"\|"installing"\|"error", "downloaded": int, "total": int, "error": str\|null}` | CUDA-runtime download progress for GPU ASR (see `broadcast_gpu_status`) |
+| Type | Fields | Description |
+|------|--------|-------------|
+| `job_update` | `job_id`, plus any of `state`, `progress`, `speed`, `eta`, `current_title`, `total_titles`, `error`, `content_type`, `detected_title`, `detected_season`, `review_reason`, ... | Job change; `state` omitted means unchanged (progress-only update) |
+| `title_update` | `job_id`, `title_id`, `state`, plus any of `matched_episode`, `match_confidence`, `match_stage`, `match_progress`, size fields, `match_details`, `organized_to`, `is_extra`, ... | Any per-track change (rip, match, organize, failure) |
+| `titles_discovered` | `job_id`, `titles`, `content_type`, `detected_title`, `detected_season` | Scan results |
+| `subtitle_event` | `job_id`, `status`, `downloaded`, `total`, `failed_count` | Subtitle download progress |
+| `backup_progress` | `job_id`, `current_bytes`, `total_bytes`, `speed`, `eta` | Full-disc backup copy progress |
+| `drive_event` | `drive_id`, `event` (`"inserted"`\|`"removed"`), `volume_label` | Physical disc inserted/removed |
+| `drive_armed` | `drive_id`, `identity` (`null` = disarmed) | Manual identity armed on a drive |
+| `parked_discs` | `discs` | Discs parked awaiting action |
+| `fingerprint_disclosure_required` | `pending_count`, `pseudonym`, `server_url` | Fingerprint-contribution disclosure prompt |
+| `update_status` | `state`, `current_version`, `is_frozen`, ... | App self-update status |
+| `gpu_status` | `data`: `{"state": "idle"\|"downloading"\|"installing"\|"error", "downloaded", "total", "error"}` | CUDA-runtime download progress (`broadcast_gpu_status`) |
+| `ping` | none | Heartbeat |
```

**Finding 2: component map**

```diff
@@ CLAUDE.md:194 @@
-- **Supporting components**: `StateIndicator`, `CyberpunkProgressBar`, `TrackGrid`, `MatchingVisualizer`
-- **ReviewQueue** (`components/ReviewQueue.tsx`) — Human-in-the-Loop UI with subcomponents: `TVTitleCard`, `MovieTitleCard`, `EpisodeSelector`, `EditionInput`, `hooks/useReviewState`
+- **Supporting components**: `StateIndicator`, `TrackGrid`, `MatchingVisualizer`; Synapse primitives such as `SvProgressBar` live in `app/components/synapse/`
+- **ReviewQueue** (`components/ReviewQueue.tsx`) — Human-in-the-Loop UI; its parts live in `components/ReviewQueue/` (track list, inspector, identity gate, season roster, ordering selector, external-player controls)
```

**Finding 3: job_manager size**

```diff
@@ CLAUDE.md:152 @@
-- **JobManager** (`job_manager.py`) — Thin orchestrator (~1,166 lines). Wires coordinators together, manages job lifecycle, handles drive events, and coordinates ripping.
+- **JobManager** (`job_manager.py`) — Orchestrator. Wires coordinators together, manages job lifecycle, handles drive events, and coordinates ripping.
```

**Finding 4: E2E spec count**

```diff
@@ CLAUDE.md:203 @@
-Playwright-based E2E tests (10 spec files) that use simulation endpoints to test the full UI workflow without physical discs.
+Playwright-based E2E tests that use simulation endpoints to test the full UI workflow without physical discs.
@@ CLAUDE.md:604 @@
-- Full UI workflow testing using Playwright (10 spec files)
+- Full UI workflow testing using Playwright
```

**Finding 5: Sentinel platforms**

```diff
@@ CLAUDE.md:141 @@
-1. **Sentinel** (`sentinel.py`) — Drive monitor (`DriveMonitor` class). Polls optical drives on Windows using ctypes/kernel32. Fires async callbacks on disc insert/remove events.
+1. **Sentinel** (`sentinel.py`) — Drive monitor (`DriveMonitor` class). Polls optical drives (Windows via ctypes/kernel32; Linux via `/sys/block/sr*` + `ioctl(CDROM_DRIVE_STATUS)`). Fires async callbacks on disc insert/remove events.
```

**Finding 6: CORS**

```diff
@@ CLAUDE.md:471 @@
-- **CORS**: Configured for `localhost:5173` (Vite dev server) only
+- **CORS**: Defaults to `localhost:5173` / `127.0.0.1:5173` (Vite dev server); the `cors_origins` setting (comma-separated) replaces the default list
```

**Finding 7: frontend unit tier**

```diff
@@ CLAUDE.md:43 @@
 npm run lint         # ESLint
+npm run test:unit    # Vitest + Testing Library unit tests (colocated *.test.tsx)
 npm run test:e2e     # Run Playwright E2E tests
@@ CLAUDE.md:601 @@
 ### Frontend Testing

+**Unit tests**: Vitest + React Testing Library, colocated as `*.test.ts(x)` beside the component; run with `npm run test:unit`.
+
 **E2E tests** (`frontend/e2e/`):
```

**Finding 8: error-handling convention and samples**

```diff
@@ CLAUDE.md:386 @@
-- **Decorator**: Use `@handle_errors` for standardized error handling in service methods
-
-**Common patterns**:
-```python
-# Subprocess errors (MakeMKV)
-try:
-    result = await makemkv_operation()
-except subprocess.SubprocessError as e:
-    logger.error(f"MakeMKV operation failed: {e}", exc_info=True)
-    job.state = JobState.FAILED
-    job.error = str(e)
-
-# Database errors
-try:
-    await session.commit()
-except SQLAlchemyError as e:
-    await session.rollback()
-    logger.error(f"Database commit failed: {e}", exc_info=True)
-    raise
-
-# External API errors (TMDB)
-try:
-    response = await tmdb_client.fetch()
-except (HTTPError, RequestException) as e:
-    logger.warning(f"TMDB API failed, using fallback: {e}")
-    # Continue with degraded functionality
-```
+- **Failing a job**: move it to `FAILED` through `JobStateMachine.transition` (which fires terminal callbacks such as Discord notifications), not by assigning `job.state`
+- **Decorator**: `@handle_errors` (`app/core/errors.py`) is available; services currently handle errors inline
```

If `@handle_errors` in services is the intended direction rather than current practice, keep the original decorator line and take only the sample removal.

**Finding 9: history narrative**

```diff
@@ CLAUDE.md:588 @@
 - Accept simulation auto-start behavior in test expectations
-- Integration tests have caught 2 production bugs (WebSocket parameter mismatches)
```

**Finding 10: logging caveat**

```diff
@@ CLAUDE.md:148 @@
-... The diagnostics bundle greps the `job=<id>` token. **Caveat:** only jobs that ran *after* this change have tagged lines — older jobs fall back to the global ERROR/CRITICAL tail. ...
+... The diagnostics bundle greps the `job=<id>` token. **Caveat:** jobs logged before job tagging existed have no tagged lines, so their bundle falls back to the global ERROR/CRITICAL tail. ...
```

**Finding 11: GPU badge parenthetical**

```diff
@@ CLAUDE.md:347 @@
-... so the `/api/asr-status` badge, the match semaphore, and the model loader can't disagree (the badge no longer claims CUDA while silently on CPU). ...
+... so the `/api/asr-status` badge, the match semaphore, and the model loader can't disagree. ...
```

## Verification

Every finding above was checked against the repository rather than a model: file existence with the file tools, `git ls-files`, `git grep` for every message type and identifier, `frontend/package.json` scripts, and `git blame` on the stale lines (the WebSocket table and contract section date from `d3163119`, 2026-02-16; the component map from `024d188d`, 2026-03-07). Nothing was applied to `CLAUDE.md`.
