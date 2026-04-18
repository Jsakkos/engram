# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Engram is a disc ripping and media organization tool with a reactive web dashboard. It automates the workflow from optical disc insertion to organized media library, with Human-in-the-Loop intervention for ambiguous content. Cross-platform backend (Python/FastAPI), with automatic drive detection on Windows. Linux/macOS can run the backend and dashboard but require manual job submission. Requires MakeMKV with a valid license.

## Important Rules

- **NEVER delete `backend/engram.db`** unless the user explicitly asks. It contains API keys and credentials that must be re-entered manually.
- **Always terminate backend processes after testing.** Kill all `uvicorn`, `python` (uvicorn workers), and `makemkvcon` processes when done. Orphaned processes cause duplicate jobs and MakeMKV drive conflicts. Never use `--reload` — it spawns a child process with its own drive sentinel, creating duplicate disc events.

## Commands

### Backend (from `backend/`)

```bash
uv sync                              # Install/sync dependencies
uv run uvicorn app.main:app --reload  # Start dev server (port 8000)
uv run pytest                         # Run all tests
uv run pytest test_file.py::test_name # Run a single test
uv run ruff check .                   # Lint
uv run ruff format .                  # Format
```

### Frontend (from `frontend/`)

```bash
npm install          # Install dependencies
npm run dev          # Start Vite dev server (port 5173)
npm run build        # TypeScript check + production build
npm run lint         # ESLint
npm run test:e2e     # Run Playwright E2E tests
npm run test:e2e:ui  # Run E2E tests with interactive UI
```

### Simulation (requires backend running with DEBUG=true)

```bash
# Simulate TV disc insertion with auto-ripping
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D1","content_type":"tv","simulate_ripping":true}'

# Simulate movie disc
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"INCEPTION_2010","content_type":"movie","simulate_ripping":true}'

# Simulate disc removal
curl -X POST "localhost:8000/api/simulate/remove-disc?drive_id=E%3A"

# Manually advance a job to its next state
curl -X POST localhost:8000/api/simulate/advance-job/1

# Reset all jobs (useful for test cleanup)
curl -X DELETE localhost:8000/api/simulate/reset-all-jobs

# Insert disc from staging directory
curl -X POST localhost:8000/api/simulate/insert-disc-from-staging
```

## Architecture

**Hub-and-Spoke** design: Python backend hub with modular "spoke" components.

### Backend (`backend/app/`)

- **Entry point**: `main.py` — FastAPI app with lifespan management, CORS for Vite dev server, WebSocket endpoint at `/ws`
- **Config**: `config.py` — Pydantic Settings for server-level overrides (host, port, debug). No `.env` file required — all fields have defaults
- **Database**: `database.py` — Async SQLite via SQLModel + aiosqlite. Tables auto-created on startup. Alembic for versioned migrations; `app_config` data preserved via backup/restore

### Core Modules (`backend/app/core/`)

Each module maps to a stage in the disc processing pipeline:

1. **Sentinel** (`sentinel.py`) — Drive monitor (`DriveMonitor` class). Polls optical drives on Windows using ctypes/kernel32. Fires async callbacks on disc insert/remove events.
2. **Analyst** (`analyst.py`) — Disc classification. Heuristic-based TV vs Movie detection (cluster analysis of title durations). Outputs `DiscAnalysisResult` with content type, confidence score, and whether review is needed.
3. **Extractor** (`extractor.py`) — MakeMKV CLI wrapper. Async subprocess management for `makemkvcon` scanning and ripping. Emits `RipProgress` callbacks.
4. **Curator** (`curator.py`) — Episode matching via audio fingerprinting. Classifies matches into high-confidence (auto-organize) and needs-review buckets.
5. **Organizer** (`organizer.py`) — File organization. Moves from staging to library with naming conventions: `Movies/Name (Year)/Name (Year).mkv` and `TV/Show/Season XX/Show - SXXEXX.mkv`.
6. **TMDB Classifier** (`tmdb_classifier.py`) — TMDB-based content type classification. Provides strong signals for TV vs Movie detection beyond the heuristic-based Analyst.
7. **Errors** (`errors.py`) — Custom exception hierarchy (`EngramError` base, with `MakeMKVError`, `MatchingError`, `ConfigurationError`, `OrganizationError`, `SubtitleError`, `DatabaseError`). Includes `@handle_errors` decorator for standardized error handling.
8. **Logging** (`logging.py`) — Centralized logging configuration.

### Orchestration (`backend/app/services/`)

- **JobManager** (`job_manager.py`) — Thin orchestrator (~1,166 lines). Wires coordinators together, manages job lifecycle, handles drive events, and coordinates ripping.
- **IdentificationCoordinator** (`identification_coordinator.py`) — Disc scanning, DiscDB/TMDB/AI lookup, classification pipeline.
- **MatchingCoordinator** (`matching_coordinator.py`) — Episode matching, subtitle download, file readiness, DiscDB assignment, extras handling. Owns per-job caches.
- **FinalizationCoordinator** (`finalization_coordinator.py`) — Conflict resolution, file organization, review workflow, job completion.
- **CleanupService** (`cleanup_service.py`) — Staging directory cleanup, timed cleanup, TheDiscDB auto-export.
- **SimulationService** (`simulation_service.py`) — All simulation methods for E2E testing (DEBUG only).
- **JobStateMachine** (`job_state_machine.py`) — Explicit state machine implementation: `IDLE → IDENTIFYING → RIPPING → MATCHING → ORGANIZING → COMPLETED`, with `REVIEW_NEEDED` and `FAILED` branching.
- **EventBroadcaster** (`event_broadcaster.py`) — Abstraction layer for broadcasting events to WebSocket clients. Wraps `ConnectionManager` with typed methods for each event type.
- **ConfigService** (`config_service.py`) — Configuration service with helper functions for loading and updating config. Caches sync engine.

### Data Models (`backend/app/models/`)

- **DiscJob** — Central state machine with `JobState` enum (idle, identifying, review_needed, ripping, matching, organizing, completed, failed) and `ContentType` enum (tv, movie, unknown). Key fields: `cleared_at` (soft-delete from dashboard, does NOT affect history visibility), `completed_at` (auto-set on terminal state), `content_hash` (TheDiscDB fingerprint), `discdb_mappings_json` (persisted title mappings)
- **DiscTitle** — Individual title/track on a disc, linked to a job. Stores match results (episode code, confidence) and `TitleState`
- **AppConfig** — Persisted application configuration. Subtitle cache defaults to `~/.engram/cache`

### Matcher (`backend/app/matcher/`)

Integrated from standalone `mkv-episode-matcher` project. Flattened directory structure (as of v0.2.0):

- **Top-level modules**: `asr_provider.py` (speech recognition), `subtitle_provider.py` (subtitle matching), `models.py`, `config_manager.py`, `model_registry.py`, `srt_utils.py`, `tmdb_client.py`, `episode_identification.py`
- **Core**: `core/engine.py` and `core/matcher.py` — matching engine logic
- **Subtitle sources**: `addic7ed_client.py`, `opensubtitles_scraper.py`, `subtitle_utils.py`
- Uses faster-whisper/onnxruntime for ASR

### API (`backend/app/api/`)

- `routes.py` — REST endpoints under `/api` prefix (job CRUD, review actions, config, simulation, staging management, job history with `GET /api/jobs/history`, job detail with `GET /api/jobs/{job_id}/detail`, stats with `GET /api/jobs/stats`, diagnostics with `GET /api/diagnostics/report`)
- `validation.py` — Tool validation endpoints (`POST /api/validate/makemkv`, `POST /api/validate/ffmpeg`, `GET /api/detect-tools`)
- `test_routes.py` — Standalone testing endpoints for subtitle download, transcription, matching
- `websocket.py` — `ConnectionManager` singleton for broadcasting real-time updates to all connected clients

### Frontend (`frontend/src/`)

React 18 + TypeScript + Vite SPA. Vite proxies `/api` and `/ws` to backend at localhost:8000.

**Key libraries**: React Router v7, Framer Motion, Recharts, React Hook Form, Tailwind CSS v4, shadcn/ui components.

- **Dashboard** (`app/App.tsx`) — Filterable job card list (Active, Done, All) with `DiscCard` components showing content type badges, progress bars, speed/ETA, track counts, subtitle indicators, expandable track lists, and cancel buttons. Cyberpunk dual-tone cyan/magenta theme.
- **DiscCard** (`app/components/DiscCard.tsx`) — Main job display component with subcomponents: `DiscCard/MediaTypeBadge`, `DiscCard/DiscMetadata`, `DiscCard/ActionButtons`, `DiscCard/hooks/usePosterImage`
- **Supporting components**: `StateIndicator`, `CyberpunkProgressBar`, `TrackGrid`, `MatchingVisualizer`
- **ReviewQueue** (`components/ReviewQueue.tsx`) — Human-in-the-Loop UI with subcomponents: `TVTitleCard`, `MovieTitleCard`, `EpisodeSelector`, `EditionInput`, `hooks/useReviewState`
- **ConfigWizard** (`components/ConfigWizard.tsx`) — First-run setup and settings modal for library paths, MakeMKV license, TMDB Read Access Token, preferences
- **HistoryPage** (`components/HistoryPage.tsx`) — All completed/failed jobs with stats dashboard, filterable table, and slide-out detail panel showing error messages, processing timeline, classification details, TheDiscDB metadata, and per-track breakdown. Deep-linkable via `/history/:jobId`
- **NamePromptModal** (`components/NamePromptModal.tsx`) — Modal for unreadable disc labels
- **Hooks**: `useDiscFilters` (job filtering/transformation), `useJobManagement` (job lifecycle + WebSocket), `useWebSocket` (connection management)

### E2E Tests (`frontend/e2e/`)

Playwright-based E2E tests (10 spec files) that use simulation endpoints to test the full UI workflow without physical discs. Test scenarios include disc flow, progress display, review flow, error recovery, visual verification, realistic disc scenarios, and screenshot capture.

## Key Patterns

- **Async everywhere**: Backend uses async SQLAlchemy sessions, asyncio tasks for background jobs, and async subprocess for MakeMKV CLI calls
- **Singleton services**: `job_manager`, `ws_manager`, `curator`, `movie_organizer`, `tv_organizer` are module-level singletons
- **State machine driven**: All job lifecycle is tracked through `JobState` transitions persisted in SQLite
- **Subtitle coordination**: Subtitle download runs in background during ripping; matching awaits `asyncio.Event` before proceeding
- **Simulation endpoints**: `POST /api/simulate/insert-disc`, `POST /api/simulate/remove-disc`, `POST /api/simulate/advance-job/{id}`, `DELETE /api/simulate/reset-all-jobs` — only available when `DEBUG=true`
- **Custom error hierarchy**: All domain errors extend `EngramError` with typed subclasses. Use `@handle_errors` decorator for standardized error handling in services.
- **Ruff config**: Line length 100, target Python 3.11, rules E/F/I/UP/B, double quotes
- **Tailwind v4**: Uses `@theme inline` blocks in CSS for custom colors (including custom `magenta` palette), not `tailwind.config.js`. No PostCSS config — uses `@tailwindcss/vite` plugin directly.
- **Database migration**: Alembic with async SQLModel metadata. `render_as_batch=True` for SQLite. Existing databases auto-stamped at head on first startup. `app_config` always preserves data via backup/restore (independent of Alembic).
- **DiscDB mapping persistence**: `discdb_mappings_json` column on `DiscJob` stores serialized `DiscDbTitleMapping` list. Persisted during identification, restored from DB on server startup via `_restore_discdb_mappings()`.
- **CI caching**: Playwright browsers cached by version, uv packages cached by lockfile hash, apt packages cached via `cache-apt-pkgs-action`.

## TMDB Configuration

The TMDB setting (`tmdb_api_key` in config) accepts a **TMDB Read Access Token** (v4 auth), not the shorter "API Key" (v3 auth). The Read Access Token is a long JWT string starting with `eyJ...`. The env variable name stays `TMDB_API_KEY` for backwards compatibility.

## Error Handling Patterns

### Backend Error Handling

- **Principle**: Use specific exception types from `app/core/errors.py`, never bare `except` clauses
- **Logging**: Always log exceptions with `exc_info=True` for full stack traces
- **Recovery**: Distinguish between recoverable errors (log warning, continue) and fatal errors (log error, raise)
- **State consistency**: Failed operations should leave jobs in a valid state (e.g., `FAILED` state with error message in `error` field)
- **Decorator**: Use `@handle_errors` for standardized error handling in service methods

**Common patterns**:
```python
# Subprocess errors (MakeMKV)
try:
    result = await makemkv_operation()
except subprocess.SubprocessError as e:
    logger.error(f"MakeMKV operation failed: {e}", exc_info=True)
    job.state = JobState.FAILED
    job.error = str(e)

# Database errors
try:
    await session.commit()
except SQLAlchemyError as e:
    await session.rollback()
    logger.error(f"Database commit failed: {e}", exc_info=True)
    raise

# External API errors (TMDB)
try:
    response = await tmdb_client.fetch()
except (HTTPError, RequestException) as e:
    logger.warning(f"TMDB API failed, using fallback: {e}")
    # Continue with degraded functionality
```

### Frontend Error Handling

- **API calls**: Use try-catch with user-friendly error alerts
- **WebSocket**: Auto-reconnect on disconnect with exponential backoff
- **State recovery**: Reload job list on reconnect to sync state

## WebSocket Message Types

All WebSocket messages follow the format: `{"type": "...", "data": {...}}`

### Server → Client Messages

| Type | Data Fields | Description |
|------|-------------|-------------|
| `job_update` | `DiscJob` object | Full job state update (sent on any job change) |
| `job_created` | `DiscJob` object | New job created from disc insertion |
| `job_cancelled` | `{"job_id": int}` | Job was cancelled by user |
| `job_cleared` | `{"job_id": int}` | Completed job was cleared from UI |
| `drive_event` | `{"drive_id": str, "event": "inserted"\|"removed"}` | Physical disc inserted/removed |
| `subtitle_progress` | `{"job_id": int, "downloaded": int, "total": int, "failed": int}` | Subtitle download progress |
| `title_discovered` | `{"job_id": int, "title": DiscTitle}` | New title found during ripping |
| `rip_progress` | `{"job_id": int, "current_bytes": int, "total_bytes": int, "speed": str, "eta": int}` | Ripping progress update |
| `title_ripping_started` | `{"job_id": int, "title_id": int}` | Title ripping started |
| `title_ripping_progress` | `{"job_id": int, "title_id": int, ...}` | Per-track ripping progress |
| `title_matching_started` | `{"job_id": int, "title_id": int}` | Title matching started |
| `title_matched` | `{"job_id": int, "title_id": int, ...}` | Successful title match |
| `title_state_changed` | `{"job_id": int, "title_id": int, "state": str}` | Generic title state change |
| `title_failed` | `{"job_id": int, "title_id": int, "error": str}` | Title processing failed |

### Client → Server Messages

No client messages currently supported (WebSocket is server-push only).

### WebSocket Contract Validation

**CRITICAL**: Parameter names must match exactly between layers:
- `EventBroadcaster` methods → `ConnectionManager` methods → WebSocket messages
- Example bug: Using `error_message=` when parameter is `error=` causes TypeError

**Validated contracts** (from integration tests):
- `broadcast_job_update(..., error=str)` — NOT `error_message`
- `broadcast_subtitle_event(job_id, status, downloaded, total, failed_count)` — NO `error_msg` parameter
- All state changes must use `JobState` or `TitleState` enum values

**Testing**: Integration tests validate WebSocket parameter contracts end-to-end

## Security Considerations

### API Endpoint Security

- **Sensitive data**: API keys (MakeMKV, TMDB) are **redacted** in `GET /api/config` responses (masked as `"***"`)
- **Configuration updates**: `PUT /api/config` accepts new values but never returns them in response
- **Debug endpoints**: Simulation endpoints (`/api/simulate/*`) only available when `DEBUG=true` (env var or `.env`)
- **Path traversal**: All file paths validated to prevent directory traversal attacks
- **CORS**: Configured for `localhost:5173` (Vite dev server) only

### Configuration Storage

- **Sensitive values**: MakeMKV keys, TMDB tokens stored in `backend/engram.db` (SQLite)
- **File permissions**: Database file should have restrictive permissions in production
- **Environment variables**: `.env` file (if used) should never be committed (included in `.gitignore`)

## Configuration Management

### Configuration Sources (Priority Order)

1. **Database** (`app_config` table) — Runtime configuration, editable via API
2. **Environment variables** (or optional `.env` file) — Server-level settings (DEBUG, HOST, PORT, DATABASE_URL)
3. **Defaults** — Hardcoded in `AppConfig` model

### Configuration Flow

```
User edits config in ConfigWizard
  ↓
PUT /api/config
  ↓
Update AppConfig in database
  ↓
JobManager reloads config on next operation
  ↓
Components use updated settings
```

### Key Configuration Fields

- **Paths**: `staging_path`, `library_movies_path`, `library_tv_path`, `makemkv_path`, `ffmpeg_path`
- **API Keys**: `makemkv_key`, `tmdb_api_key` (redacted in responses)
- **Matching**: `max_concurrent_matches` (default: 3), threshold constants in Analyst
- **Transcoding**: `transcoding_enabled` (default: false)
- **Conflict resolution**: `conflict_resolution_default` ("skip" | "overwrite" | "ask")

### Configuration Validation

Validation occurs in:
- **Pydantic models**: Type checking, required fields
- **API routes**: Path existence checks, MakeMKV license validation
- **Validation endpoints**: `POST /api/validate/makemkv`, `POST /api/validate/ffmpeg`, `GET /api/detect-tools`
- **JobManager**: Pre-flight checks before starting jobs

## Testing Guidelines

### Backend Testing

**Unit tests** (`tests/unit/`):
- Test individual modules in isolation (Analyst, Extractor, Curator, StateMachine, EventBroadcaster, ConfigService, TMDB, validation, etc.)
- Mock external dependencies (MakeMKV CLI, TMDB API, filesystem)
- Fast execution (< 1 second per test)

**Integration tests** (`tests/integration/`):
- Test complete workflows from disc insertion through completion
- Use simulation endpoints to avoid physical disc requirements
- Test real database operations with cleanup fixtures
- Validate WebSocket message broadcasting end-to-end
- Files: `test_workflow.py`, `test_simulation.py`, `test_websocket_e2e.py`, `test_error_recovery.py`, `test_movie_edition_workflow.py`, `test_subtitle_workflow.py`

**Pipeline tests** (`tests/pipeline/`):
- Snapshot-based pipeline tests for classification, organization, and flow scenarios
- Files: `test_classification.py`, `test_play_all_detection.py`, `test_generic_label_flow.py`, `test_concurrent_jobs.py`, `test_ambiguous_movie_flow.py`, `test_organization_paths.py`, `test_tv_episode_pipeline.py`

**Real data tests** (`tests/real_data/`):
- Tests requiring actual MKV files (auto-skipped if files don't exist)
- Files: `test_real_episode_matching.py`, `test_real_disc_classification.py`, `test_snapshot_capture.py`

**Setup pattern**:
```python
@pytest.fixture
async def client():
    """AsyncClient with ASGITransport for FastAPI testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture(autouse=True)
async def setup_db():
    """Clean database between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()
```

**Key patterns**:
- Use real `async_session` from app (not mocked)
- Clean data between tests with autouse fixture
- Use simulation endpoints (`POST /api/simulate/insert-disc`)
- Poll job state with asyncio.sleep() for async workflows
- Accept simulation auto-start behavior in test expectations
- Integration tests have caught 2 production bugs (WebSocket parameter mismatches)

**Running tests**:
```bash
cd backend
uv run pytest                    # All tests
uv run pytest tests/unit/        # Unit tests only
uv run pytest tests/pipeline/    # Pipeline tests only
uv run pytest -k test_name       # Specific test
uv run pytest --cov=app          # With coverage
```

### Frontend Testing

**E2E tests** (`frontend/e2e/`):
- Full UI workflow testing using Playwright (10 spec files)
- Requires backend running with `DEBUG=true`
- Uses simulation endpoints to fake disc insertion/ripping
- Tests user interactions (clicking, form submission, WebSocket updates)

**Running E2E tests**:
```bash
cd frontend
npm run test:e2e           # Headless mode
npm run test:e2e:ui        # Interactive mode with browser UI
```

### Manual Testing with Simulation

For development without physical discs:

1. Start backend with `DEBUG=true` (set env var or add to `.env`)
2. Use simulation endpoints to trigger workflows:
   ```bash
   # Insert TV disc
   curl -X POST localhost:8000/api/simulate/insert-disc \
     -H "Content-Type: application/json" \
     -d '{"volume_label":"SHOW_S1D1","content_type":"tv","simulate_ripping":true}'

   # Advance job through states
   curl -X POST localhost:8000/api/simulate/advance-job/1
   ```
3. Observe UI updates in real-time via WebSocket

## External Dependencies

- **MakeMKV** (`makemkvcon64.exe`) must be installed with a valid license key
- **uv** for Python dependency management (not pip)
- **Playwright** for E2E tests (`npx playwright install` to set up browsers)
- SQLite database stored at `backend/engram.db`
- Subtitle cache stored at `~/.engram/cache/`
- Logs written to `~/.engram/engram.log`
