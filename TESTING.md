# Testing Guide

## Quick Reference

```bash
# Backend unit tests (fast, CI-safe)
cd backend && uv run pytest tests/unit/ -v

# Backend integration tests (CI-safe, ~11s)
cd backend && uv run pytest tests/integration/ -v

# All backend tests except real-data
cd backend && uv run pytest tests/unit/ tests/integration/ -v -m "not real_data"

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

Run: `cd backend && uv run pytest tests/integration/ -v` (~11s)

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_workflow.py` | 10 | Full disc processing workflows: TV disc start-to-finish, movie workflow, disc removal, state advancement, subtitle coordination blocking matching, concurrent jobs, job completion from matching state, review submit resumption |
| `test_simulation.py` | 8 | Simulation endpoint validation: job/title creation in DB, state advancement, disc removal, production-mode lockout (DEBUG=false returns 403), `_on_title_ripped` callback behavior |
| `test_error_recovery.py` | 4 | Error paths: cancel during ripping produces FAILED state, cancelled jobs remain queryable via API, error messages preserved, single job deletion |
| `test_websocket_e2e.py` | 3 | WebSocket message shape contracts: `job_update`, `titles_discovered`, and `subtitle_event` message structure validated end-to-end |
| `test_subtitle_workflow.py` | 5 | Subtitle download pipeline: TMDB lookup + Addic7ed download + file creation, name variation fallback, cache hit/miss/partial behavior |
| `test_movie_edition_workflow.py` | 4 | Movie edition handling: edition review workflow, skip workflow, pre-rip selection, ambiguous rip resolution (winner/loser file management) |

### Backend Real-Data Tests (`backend/tests/real_data/`)

Requires actual ripped MKV files on disk. Skipped automatically if files don't exist. Never run in CI.

Run: `cd backend && uv run pytest tests/real_data/ -v -m real_data`

| File | Tests | What It Covers |
|------|------:|----------------|
| `test_real_disc_classification.py` | 2 | Feed real MKV files through the Analyst: verify TV/movie classification and confidence scores against known discs |
| `test_real_episode_matching.py` | 2 | Episode matching against expected results: verify file-to-episode mapping matches golden JSON fixtures, verify subtitle cache availability |

Expected data fixtures live in `backend/tests/real_data/expected/*.json`.

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
| `real-data-simulation.spec.ts` | 1 | Full workflow with real MKV files from disk (auto-skipped if files don't exist) |

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
  real_data/
    conftest.py            # Skip-if-missing fixtures for staging paths and expected JSONs
```

### Key Backend Fixtures

| Fixture | Scope | Location | Purpose |
|---------|-------|----------|---------|
| `isolate_database` | function, autouse | `unit/conftest.py` | Patches `async_session` in database, config_service, job_manager, ripping_coordinator to prevent touching `engram.db` |
| `integration_client` | function | `integration/conftest.py` | `AsyncClient` with `ASGITransport` and session override |
| `integration_config` | function | `integration/conftest.py` | Seeds `AppConfig` with fast poll intervals |
| `real_staging_path` | function, indirect | `real_data/conftest.py` | Parametrized path, skips test if directory doesn't exist |
| `expected_matches` | function, indirect | `real_data/conftest.py` | Loads golden JSON from `expected/` directory |

### Frontend Test Fixtures

| File | Purpose |
|------|---------|
| `e2e/fixtures/api-helpers.ts` | `simulateInsertDisc()`, `resetAllJobs()`, `simulateInsertDiscFromStaging()` |
| `e2e/fixtures/disc-scenarios.ts` | Disc configs: `TV_DISC_ARRESTED_DEVELOPMENT`, `MOVIE_DISC`, `AMBIGUOUS_DISC` |
| `e2e/fixtures/selectors.ts` | CSS/text selectors for all UI elements |

### Pytest Markers

| Marker | Description | CI? |
|--------|-------------|-----|
| `unit` | Fast isolated tests | Yes |
| `integration` | Multi-component workflow tests | Yes |
| `slow` | Tests taking >30 seconds | Skip in CI |
| `real_data` | Requires real MKV files on disk | No |
| `asyncio` | Async tests (auto-applied via `asyncio_mode = auto`) | Yes |

---

## CI/CD Configuration

Recommended CI test commands:

```yaml
# Backend (all CI-safe tests)
- name: Backend tests
  run: cd backend && uv run pytest tests/unit/ tests/integration/ -v -m "not real_data and not slow"

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
| Backend unit | 14 | ~170 | ~1s |
| Backend integration | 6 | ~34 | ~11s |
| Backend real-data | 2 | 4 | local-only |
| Frontend unit (Vitest) | 2 | 32 | ~0.5s |
| Frontend E2E (Playwright) | 8 | ~52 | ~2-3 min |
