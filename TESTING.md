# Testing Guide

## Quick Reference

```bash
# Backend unit tests (fast, CI-safe)
cd backend && uv run pytest tests/unit/ -v

# Backend pipeline tests (CI-safe, uses disc snapshots)
cd backend && uv run pytest tests/pipeline/ -v

# Backend integration tests (CI-safe, ~80s)
cd backend && uv run pytest tests/integration/ -v

# All backend CI-safe tests
cd backend && uv run pytest tests/unit/ tests/pipeline/ tests/integration/ -v -m "not real_data"

# Frontend unit tests (fast, CI-safe)
cd frontend && npm run test:unit

# Frontend E2E tests (requires backend + frontend running)
cd frontend && npm run test:e2e

# Frontend E2E with interactive UI
cd frontend && npm run test:e2e:ui

# Real-data tests (local-only, requires MKV files on disk)
cd backend && uv run pytest tests/real_data/ -v -m real_data
```

---

## Test Categories

### Backend Unit Tests (`backend/tests/unit/`)

Fast, isolated tests using an in-memory SQLite database. No external services needed. The `conftest.py` autouse fixture monkey-patches `async_session` everywhere so **no unit test touches `engram.db`**.

Run: `cd backend && uv run pytest tests/unit/ -v` (~1s)

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_api_routes.py` | 15 | REST API endpoints: job CRUD, config get/update with redaction, validation errors, 404s |
| `test_analyst.py` | 14 | Disc classification heuristics: TV detection (uniform durations, clusters), movie detection (single long title, extras), ambiguous cases, volume label parsing |
| `test_config_service.py` | 8 | Config CRUD: default creation, field persistence, sensitive field protection (empty strings don't overwrite API keys), path directory creation |
| `test_event_broadcaster.py` | 21 | WebSocket event abstraction layer: drive events, job lifecycle broadcasts, title state changes, subtitle progress, parameter contract validation |
| `test_job_completion.py` | 6 | Job completion state machine: active titles block completion, all-completed triggers transition, mixed review states, all-failed detection, broadcast failure doesn't undo DB commit |
| `test_organizer.py` | 12 | File organization: movie name cleanup (underscores, disc identifiers), filename sanitization (colons, question marks), naming conventions (`Movies/Name (Year)/Name (Year).mkv`, `TV/Show/Season XX/Show - SXXEXX.mkv`), conflict skip behavior |
| `test_speed_calculator.py` | 6 | Ripping speed calculation: initial zero state, speed-after-updates, ETA math, debounce of rapid updates (<0.5s apart) |
| `test_state_machine.py` | 16 | `JobStateMachine`: valid/invalid transitions, happy-path workflow sequences, convenience methods (fail/review/complete), concurrent broadcast control |
| `test_validation.py` | 14 | Input validation: path traversal prevention, API key formats, config value ranges, SQL injection resistance (ORM parameterization), default values |
| `test_websocket.py` | 14 | `ConnectionManager`: connect/disconnect lifecycle, message broadcasting to multiple clients, partial failure handling (bad client removed, others receive), concurrency, message shape verification |
| `test_testing_service.py` | 10 | Subtitle download service: TMDB lookup, Addic7ed scraping, cache hit/miss behavior, error handling, filename format |
| `test_addic7ed_client.py` | 11 | Addic7ed subtitle client: search, best-subtitle selection by download count, rate limiting, show name aliases |
| `test_local_provider.py` | 11 | Local subtitle provider: cache directory scanning, season filtering, file extension handling, episode info parsing |
| `test_tmdb_client.py` | 13 | TMDB API client: show name variations (prefix removal, punctuation, ampersands), exact match fast-path, error handling, season detail fetching |

### Backend Integration Tests (`backend/tests/integration/`)

Test complete workflows with a real (test-isolated) database. Use simulation endpoints. No physical discs needed.

Run: `cd backend && uv run pytest tests/integration/ -v` (~80s)

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_workflow.py` | 10 | Full disc processing workflows: TV disc start-to-finish, movie workflow, disc removal, state advancement, subtitle coordination blocking matching, concurrent jobs, job completion from matching state, review submit resumption |
| `test_simulation.py` | 8 | Simulation endpoint validation: job/title creation in DB, state advancement, disc removal, production-mode lockout (DEBUG=false returns 403), `_on_title_ripped` callback behavior |
| `test_error_recovery.py` | 4 | Error paths: cancel during ripping produces FAILED state, cancelled jobs remain queryable via API, error messages preserved, single job deletion |
| `test_websocket_e2e.py` | 3 | WebSocket message shape contracts: `job_update`, `titles_discovered`, and `subtitle_event` message structure validated end-to-end |
| `test_subtitle_workflow.py` | 5 | Subtitle download pipeline: TMDB lookup + Addic7ed download + file creation, name variation fallback, cache hit/miss/partial behavior |
| `test_movie_edition_workflow.py` | 4 | Movie edition handling: edition review workflow, skip workflow, pre-rip selection, ambiguous rip resolution (winner/loser file management) |

### Backend Pipeline Tests (`backend/tests/pipeline/`)

Snapshot-based tests that feed real disc metadata (frozen as JSON) through the actual Analyst, Organizer, and state machine logic. CI-safe, fast, no external dependencies. The JSON snapshots live in `backend/tests/fixtures/disc_snapshots/` and were captured from real MakeMKV rips via ffprobe.

Run: `cd backend && uv run pytest tests/pipeline/ -v` (~0.4s)

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_classification.py` | 18 | Content type detection for all 4 real discs: Arrested Development (TV, 95% confidence, season parsed from label), Picard S1D3 (TV, Play All resolved, label+TMDB confirms), Terminator (movie, ambiguous 2 features), LOGICAL_VOLUME_ID (movie, generic label, name=None) |
| `test_play_all_detection.py` | 8 | Play All track flagging: Picard t03 (9416s = sum of 3 episodes) correctly detected, episodes and extras NOT flagged, Arrested Dev has no Play All, LOGICAL_VOLUME_ID feature is not a false positive, tolerance boundary edge case |
| `test_generic_label_flow.py` | 16 | Generic label handling: all 11 generic placeholders (LOGICAL_VOLUME_ID, VIDEO_TS, BDMV, DVD, etc.) return None, non-generic labels work, full name-prompt flow (analyst returns no name, user provides "The Italian Job", Organizer generates correct path) |
| `test_ambiguous_movie_flow.py` | 8 | Multi-feature movie detection: Terminator 2 identical features triggers review, single feature + extras does NOT trigger review, synthetic 3-feature compilation triggers review, "Rip First Review Later" preconditions verified |
| `test_tv_episode_pipeline.py` | 10 | TV track selection and organization: Picard 3 episodes in TV range, extra below range, Play All excluded from selection, episode paths (S01E07-E09), extras to `Extras/` subfolder. Arrested Dev 8-episode cluster, 3 extras filtered, multi-prefix filenames (B1_, C1_, D1_), mixed durations (pilot 28min vs regular 22min) still cluster |
| `test_concurrent_jobs.py` | 5 | Concurrency model: REVIEW_NEEDED is not terminal, can transition to RIPPING, not in drive-blocking set, different drives are independent, same drive review blocks new insert |
| `test_organization_paths.py` | 11 | Dry-run file organization with `tmp_path`: movie paths (Italian Job, Terminator, no-year variant), movie name cleaning (uppercase, disc suffix, bluray suffix, title case), TV episode paths (Picard S01E07), full 8-episode Arrested Dev run, extras paths for both shows |

**Disc snapshot fixtures** (`backend/tests/fixtures/disc_snapshots/`):

| Fixture | Source Disc | Tracks | Key Trait |
|---------|------------|-------:|-----------|
| `arrested_development_s1d1.json` | ARRESTED_Development_S1D1 | 11 | 8 episodes (pilots 28min, regular 22min), 3 extras, multi-prefix filenames |
| `star_trek_picard_s1d3.json` | STAR TREK PICARD S1D3 | 5 | 3 episodes, 1 Play All (9416s = exact sum), 1 extra |
| `the_terminator.json` | THE TERMINATOR | 7 | 2 identical features at 6423s (1080p), 5 extras (480p) |
| `logical_volume_id.json` | LOGICAL_VOLUME_ID | 10 | Generic label, 1 feature (110min), 9 extras, name=None |

### Backend Real-Data Tests (`backend/tests/real_data/`)

Requires actual ripped MKV files on disk. Skipped automatically if files don't exist. Never run in CI.

Run: `cd backend && uv run pytest tests/real_data/ -v -m real_data`

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_real_disc_classification.py` | 5 | Feed real MKV files through the Analyst: Arrested Dev (TV), Picard (TV with Play All), Terminator (movie, ambiguous), LOGICAL_VOLUME_ID (movie, generic label) |
| `test_real_episode_matching.py` | 2 | Episode matching against expected results: verify file-to-episode mapping matches golden JSON fixtures, verify subtitle cache availability |
| `test_snapshot_capture.py` | 5 | Dual-mode snapshot capture utility: Mode A captures disc metadata from ripped MKV folders via ffprobe, Mode B captures from physical disc via `makemkvcon info` scan (no ripping) |

Expected data fixtures live in `backend/tests/real_data/expected/*.json`.

**Capturing new disc snapshots:**

```bash
# Mode A: from ripped folders at C:\Video
cd backend && uv run pytest tests/real_data/test_snapshot_capture.py -v -m real_data -k capture_from_folder -s

# Mode B: from physical disc in drive E: (no ripping, ~30 second scan)
cd backend && uv run pytest tests/real_data/test_snapshot_capture.py -v -m real_data -k capture_from_disc -s
```

Both modes output skeleton JSON to `tests/fixtures/disc_snapshots/` with `expected_*` fields left blank for manual annotation.

### Frontend Unit Tests (`frontend/src/**/__tests__/`)

Pure logic tests using Vitest + jsdom. No browser or server needed.

Run: `cd frontend && npm run test:unit` (~0.5s)

| File | Tests | What It Covers |
|------|------:|----------------|
| `src/types/__tests__/adapters.test.ts` | 24 | Data transformation layer: `JobState` to UI state mapping (8 values), `TitleState` mapping (7 values), full job-to-disc-data transformation (TV/movie/fallback/unknown), duration formatting edge cases, match candidate extraction from JSON |
| `src/hooks/__tests__/useJobManagement.test.ts` | 8 | WebSocket data merging logic: partial job update merging, title update targeting correct job/title, all-terminal-state detection, `titles_discovered` replacement, `subtitle_event` field updates |

### Frontend E2E Tests (`frontend/e2e/`)

Playwright tests against the real UI. Requires both backend (DEBUG=true) and frontend servers running. Playwright config auto-starts them if needed.

Run: `cd frontend && npm run test:e2e`

| File | Tests | What It Covers |
|------|------:|----------------|
| `kanban-flow.spec.ts` | 6 | Core Kanban UI: TV disc state progression with track detail, movie disc flow, filter buttons (ACTIVE/DONE/ALL), empty state, multiple simultaneous discs, progress percentage |
| `progress-display.spec.ts` | 9 | Progress visualization: ripping percentage updates, speed/ETA display, cyberpunk progress bar styling, track grid for TV, per-track byte counts, LISTENING state during transcription, match candidates with confidence, completed green styling, WebSocket status indicator |
| `review-flow.spec.ts` | 5 | Review workflow: ambiguous disc shows ANALYZING badge, card displays basic info (title, subtitle), review page navigation (skipped), review candidates UI, review submission resumes processing |
| `error-recovery.spec.ts` | 4 | Error handling UI: failed job shows ERROR badge, error message text displayed, WebSocket reconnection, cancel button triggers job cancellation |
| `visual-verification.spec.ts` | 14 | Visual correctness: header branding, cyberpunk card styling, progress bar with percentage, track grid with per-track progress, filter button state switching, connection status, empty state, state indicator colors, movie display, speed/ETA, completed state, footer operation counts |
| `basic-ui-verification.spec.ts` | 11 | Static UI elements (no simulation): header, subtitle, filter buttons, WebSocket indicator, empty state, color scheme, footer, settings button, full-page screenshot, existing card styling, filter switching with data |
| `screenshot-workflow.spec.ts` | 2 | Screenshot capture of every major UI state: TV disc 9-stage progression, movie disc 3-stage progression (used for visual regression review) |
| `realistic-disc-flow.spec.ts` | 5 | Realistic disc scenarios using actual disc metadata: generic label triggers NamePromptModal and resumes after name entry, movie disc flows through without review, review-blocked job on E: doesn't block new job on F:, TV disc shows track grid with per-track ripping and episode matching, TV Picard processes multiple episodes to completion |
| `real-data-simulation.spec.ts` | 1 | Full workflow with real MKV files from disk (auto-skipped if files don't exist) |

---

## Running E2E Tests with Visible UI

### Option A: Playwright UI Mode (recommended)

Opens an interactive test runner dashboard where you can click individual tests, watch them execute in a live browser panel, step through actions, inspect the DOM at each step, and view timeline traces.

```bash
cd frontend
npm run test:e2e:ui
```

This auto-starts both servers if they aren't already running.

### Option B: Headed Browser

Runs tests in a visible Chrome window so you can watch them in real time.

```bash
# All E2E tests
cd frontend && npm run test:e2e:headed

# Just the realistic disc flow tests
cd frontend && npx playwright test e2e/realistic-disc-flow.spec.ts --headed

# Single test by name
cd frontend && npx playwright test -g "generic label" --headed
cd frontend && npx playwright test -g "review-blocked" --headed
cd frontend && npx playwright test -g "TV disc shows track grid" --headed
```

### Option C: Trace Viewer (replay after the fact)

Captures a full trace (screenshots, DOM snapshots, network requests, console logs) for every test, then opens an HTML report where you can click any test to replay it step-by-step.

```bash
cd frontend
npx playwright test e2e/realistic-disc-flow.spec.ts --trace on
npx playwright show-report
```

### Slowing Down Tests

Watch tests in slow motion by adding `--slow-mo` (milliseconds between each action):

```bash
cd frontend && npx playwright test e2e/realistic-disc-flow.spec.ts --headed --slow-mo=500
```

### Step-Through Debugging

Opens the Playwright Inspector where you click "Resume" to advance one action at a time:

```bash
cd frontend
set PWDEBUG=1
npx playwright test -g "generic label"
```

(On bash/PowerShell use `PWDEBUG=1 npx playwright test -g "generic label"` or `$env:PWDEBUG=1` respectively.)

### What Each Realistic Test Looks Like

| Test | Duration | What You'll See |
|------|----------|----------------|
| **Generic label → name prompt** | ~2s | Card appears briefly, "Identify Disc" modal slides in with input field and Movie/TV toggle, auto-fills "The Italian Job", clicks "Start Ripping", modal closes, card updates with new name |
| **Movie disc flows through** | ~3-5s | "The Terminator" card appears with MOVIE badge, PROCESSING state indicator, progress bar fills, switches to COMPLETE |
| **Concurrent jobs (two drives)** | ~2-3s | "Identify Disc" modal for E: drive, second card (Star Trek Picard, TV badge) appears on F: drive behind the modal, modal submits, both cards visible simultaneously |
| **TV track grid (Arrested Dev)** | ~20s | TV badge, 11-track grid appears, per-track RIPPING labels animate through, MATCHING labels appear, episode codes (S01E01, S01E02...) populate one by one, COMPLETE |
| **TV Picard episodes** | ~10s | "Star Trek Picard" card with TV badge, 5-track grid, ripping progress, COMPLETE |

### Starting Servers Manually

Playwright auto-starts servers via `playwright.config.ts`, but if you prefer to manage them yourself:

**Terminal 1 — Backend** (must have DEBUG=true for simulation endpoints):
```bash
cd backend
set DEBUG=true
uv run uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd frontend
npm run dev
```

Verify both are healthy:
- Backend: http://localhost:8000/health
- Frontend: http://localhost:5173

---

## Test Infrastructure

### Backend Conftest Hierarchy

```
backend/tests/
  conftest.py              # Shared fixtures: temp dirs, mock configs, TMDB responses
  unit/
    conftest.py            # Autouse: patches async_session → in-memory SQLite
  integration/
    conftest.py            # Session-scoped engine, per-test session, config seeding
  pipeline/
    conftest.py            # load_snapshot(), snapshot_to_titles(), analyst fixture
  real_data/
    conftest.py            # Skip-if-missing fixtures for staging paths and expected JSONs
```

### Key Backend Fixtures

| Fixture | Scope | Location | Purpose |
|---------|-------|----------|---------|
| `isolate_database` | function, autouse | `unit/conftest.py` | Patches `async_session` in database, config_service, job_manager, ripping_coordinator to prevent touching `engram.db` |
| `integration_client` | function | `integration/conftest.py` | `AsyncClient` with `ASGITransport` and session override |
| `integration_config` | function | `integration/conftest.py` | Seeds `AppConfig` with fast poll intervals |
| `analyst` | function | `pipeline/conftest.py` | `DiscAnalyst` instance with production-default thresholds |
| `load_snapshot()` | helper | `pipeline/conftest.py` | Loads disc JSON from `fixtures/disc_snapshots/`, skips if missing |
| `snapshot_to_titles()` | helper | `pipeline/conftest.py` | Converts snapshot JSON tracks to `list[TitleInfo]` |
| `real_staging_path` | function, indirect | `real_data/conftest.py` | Parametrized path, skips test if directory doesn't exist |
| `expected_matches` | function, indirect | `real_data/conftest.py` | Loads golden JSON from `expected/` directory |

### Frontend Test Fixtures

| File | Purpose |
|------|---------|
| `e2e/fixtures/api-helpers.ts` | `simulateInsertDisc()`, `resetAllJobs()`, `advanceJob()`, `simulateInsertDiscFromStaging()` |
| `e2e/fixtures/disc-scenarios.ts` | Disc configs: `TV_DISC_ARRESTED_DEVELOPMENT`, `MOVIE_DISC`, `AMBIGUOUS_DISC`, `GENERIC_LABEL_DISC`, `MULTI_FEATURE_MOVIE_DISC`, `TV_DISC_PICARD_S1D3`, `TV_DISC_ARRESTED_DEV_REALISTIC` |
| `e2e/fixtures/selectors.ts` | CSS/text selectors for all UI elements, plus `getDiscCardByTitle()` helper |

### Pytest Markers

| Marker | Description | CI? |
|--------|-------------|-----|
| `unit` | Fast isolated tests | Yes |
| `integration` | Multi-component workflow tests | Yes |
| `pipeline` | Snapshot-based pipeline tests using disc metadata fixtures | Yes |
| `slow` | Tests taking >30 seconds | Skip in CI |
| `real_data` | Requires real MKV files on disk | No |
| `asyncio` | Async tests (auto-applied via `asyncio_mode = auto`) | Yes |

---

## CI/CD Configuration

Recommended CI test commands:

```yaml
# Backend (all CI-safe tests)
- name: Backend tests
  run: cd backend && uv run pytest tests/unit/ tests/pipeline/ tests/integration/ -v -m "not real_data and not slow"

# Frontend unit tests
- name: Frontend unit tests
  run: cd frontend && npm run test:unit

# Frontend E2E (requires server startup)
- name: Frontend E2E tests
  run: cd frontend && npm run test:e2e
```

---

## Running Specific Tests

```bash
# Single test by name
cd backend && uv run pytest -k test_classify_tv_uniform_durations

# Single file
cd backend && uv run pytest tests/unit/test_analyst.py -v

# All pipeline tests for a specific disc
cd backend && uv run pytest tests/pipeline/ -k "Picard" -v

# With coverage
cd backend && uv run pytest tests/unit/ --cov=app --cov-report=term-missing

# Frontend: single test file
cd frontend && npx vitest run src/types/__tests__/adapters.test.ts

# E2E: single spec
cd frontend && npx playwright test kanban-flow.spec.ts

# E2E: headed mode (see the browser)
cd frontend && npm run test:e2e:headed
```

---

## Test Counts

| Layer | Files | Tests | Runtime |
|-------|------:|------:|---------|
| Backend unit | 14 | ~242 | ~8s |
| Backend pipeline | 7 | 77 | ~0.4s |
| Backend integration | 6 | ~36 | ~80s |
| Backend real-data | 3 | ~12 | local-only |
| Frontend unit (Vitest) | 2 | 32 | ~0.5s |
| Frontend E2E (Playwright) | 9 | ~57 | ~2-3 min |
