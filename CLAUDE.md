# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Engram is a disc ripping and media organization tool with a reactive web dashboard. It automates the workflow from optical disc insertion to organized media library, with Human-in-the-Loop intervention for ambiguous content. Windows-native, requires MakeMKV with a valid license.

## Important Rules

- **NEVER delete `backend/engram.db`** unless the user explicitly asks. It contains API keys and credentials that must be re-entered manually.

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
```

## Architecture

**Hub-and-Spoke** design: Python backend hub with modular "spoke" components.

### Backend (`backend/app/`)

- **Entry point**: `main.py` — FastAPI app with lifespan management, CORS for Vite dev server, WebSocket endpoint at `/ws`
- **Config**: `config.py` — Pydantic Settings for server-level overrides (host, port, debug). No `.env` file required — all fields have defaults
- **Database**: `database.py` — Async SQLite via SQLModel + aiosqlite. Tables auto-created on startup

### Core Modules (`backend/app/core/`) — The Four Spokes

Each module maps to a stage in the disc processing pipeline:

1. **Sentinel** (`sentinel.py`) — Drive monitor. Polls optical drives on Windows using ctypes/kernel32. Fires async callbacks on disc insert/remove events.
2. **Analyst** (`analyst.py`) — Disc classification. Heuristic-based TV vs Movie detection (cluster analysis of title durations). Outputs `DiscAnalysisResult` with content type, confidence score, and whether review is needed.
3. **Extractor** (`extractor.py`) — MakeMKV CLI wrapper. Async subprocess management for `makemkvcon` scanning and ripping. Emits `RipProgress` callbacks.
4. **Curator** (`curator.py`) — Episode matching via audio fingerprinting. Classifies matches into high-confidence (auto-organize) and needs-review buckets.
5. **Organizer** (`organizer.py`) — File organization. Moves from staging to library with naming conventions: `Movies/Name (Year)/Name (Year).mkv` and `TV/Show/Season XX/Show - SXXEXX.mkv`.

### Orchestration (`backend/app/services/`)

- **JobManager** (`job_manager.py`) — Singleton that wires the four spokes together. Manages the `DiscJob` state machine lifecycle: `IDLE → IDENTIFYING → RIPPING → MATCHING → ORGANIZING → COMPLETED`. Handles `REVIEW_NEEDED` branching and `FAILED` states. Broadcasts all state transitions via WebSocket. Coordinates subtitle download with matching via `asyncio.Event`. Includes simulation methods for E2E testing.

### Data Models (`backend/app/models/`)

- **DiscJob** — Central state machine with `JobState` enum (idle, identifying, review_needed, ripping, matching, organizing, completed, failed) and `ContentType` enum (tv, movie, unknown)
- **DiscTitle** — Individual title/track on a disc, linked to a job. Stores match results (episode code, confidence) and `TitleState`
- **AppConfig** — Persisted application configuration. Subtitle cache defaults to `~/.engram/cache`

### Matcher (`backend/app/matcher/`)

Integrated from standalone `mkv-episode-matcher` project. Contains its own `core/` with engine, config management, model registry, and providers (`asr.py` for speech recognition, `subtitles.py` for subtitle matching). Uses faster-whisper/onnxruntime for ASR.

### API (`backend/app/api/`)

- `routes.py` — REST endpoints under `/api` prefix (job CRUD, review actions, config, simulation)
- `test_routes.py` — Standalone testing endpoints for subtitle download, transcription, matching
- `websocket.py` — `ConnectionManager` singleton for broadcasting real-time job updates, drive events, subtitle progress, and title discovery to all connected clients

### Frontend (`frontend/src/`)

React 18 + TypeScript + Vite SPA. Vite proxies `/api` and `/ws` to backend at localhost:8000.

- **KanbanBoard** — 5-column Kanban layout (Scanning, Ripping, Processing, Review, Done) with enhanced job cards showing content type badges, progress bars with percentage overlays, speed/ETA, track counts, subtitle download indicators, expandable track lists, and cancel buttons
- **ReviewQueue** — Human-in-the-Loop UI for resolving ambiguous episode matches
- **ConfigWizard** — First-run setup for library paths, MakeMKV license, TMDB Read Access Token, preferences
- **useWebSocket** hook — Manages WebSocket connection and message parsing

### E2E Tests (`frontend/e2e/`)

Playwright-based E2E tests that use simulation endpoints to test the full UI workflow without physical discs. Test scenarios include TV disc flow, movie disc flow, progress display, subtitle indicators, cancel/clear, and review flow.

## Key Patterns

- **Async everywhere**: Backend uses async SQLAlchemy sessions, asyncio tasks for background jobs, and async subprocess for MakeMKV CLI calls
- **Singleton services**: `job_manager`, `ws_manager`, `curator`, `movie_organizer`, `tv_organizer` are module-level singletons
- **State machine driven**: All job lifecycle is tracked through `JobState` transitions persisted in SQLite
- **Subtitle coordination**: Subtitle download runs in background during ripping; matching awaits `asyncio.Event` before proceeding
- **Simulation endpoints**: `POST /api/simulate/insert-disc`, `POST /api/simulate/remove-disc`, `POST /api/simulate/advance-job/{id}` — only available when `DEBUG=true`
- **Ruff config**: Line length 100, target Python 3.11, rules E/F/I/UP/B, double quotes

## TMDB Configuration

The TMDB setting (`tmdb_api_key` in config) accepts a **TMDB Read Access Token** (v4 auth), not the shorter "API Key" (v3 auth). The Read Access Token is a long JWT string starting with `eyJ...`. The env variable name stays `TMDB_API_KEY` for backwards compatibility.

## Error Handling Patterns

### Backend Error Handling

- **Principle**: Use specific exception types, never bare `except` clauses
- **Logging**: Always log exceptions with `exc_info=True` for full stack traces
- **Recovery**: Distinguish between recoverable errors (log warning, continue) and fatal errors (log error, raise)
- **State consistency**: Failed operations should leave jobs in a valid state (e.g., `FAILED` state with error message in `error` field)

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
- **JobManager**: Pre-flight checks before starting jobs

## Testing Guidelines

### Backend Testing

**Unit tests** (`tests/unit/`):
- Test individual modules in isolation (Analyst, Extractor, Curator)
- Mock external dependencies (MakeMKV CLI, TMDB API, filesystem)
- Fast execution (< 1 second per test)

**Integration tests** (`tests/integration/`):
- Test complete workflows from disc insertion through completion
- Use simulation endpoints to avoid physical disc requirements
- Test real database operations with cleanup fixtures
- Validate WebSocket message broadcasting end-to-end
- Execution: ~11 seconds for 8 tests (fast enough for CI/CD)

**Test suite** (`tests/integration/test_workflow.py`):
- `TestTVDiscWorkflow` (3 tests) — Complete TV disc processing, cancellation, review needed
- `TestMovieDiscWorkflow` (1 test) — Movie disc workflow
- `TestDiscRemoval` (1 test) — Disc removal event handling
- `TestStateAdvancement` (1 test) — Manual state progression
- `TestSubtitleCoordination` (1 test) — Subtitle download blocks matching
- `TestConcurrency` (1 test) — Multiple concurrent jobs

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
uv run pytest -k test_name       # Specific test
uv run pytest --cov=app          # With coverage
```

### Frontend Testing

**E2E tests** (`frontend/e2e/`):
- Full UI workflow testing using Playwright
- Requires backend running with `DEBUG=true`
- Uses simulation endpoints to fake disc insertion/ripping
- Tests user interactions (clicking, form submission, WebSocket updates)

**Test structure**:
```typescript
test('TV disc workflow', async ({ page }) => {
  // 1. Start backend simulation
  await fetch('http://localhost:8000/api/simulate/insert-disc', {...})

  // 2. Verify UI updates
  await page.goto('http://localhost:5173')
  await expect(page.locator('[data-testid="job-card"]')).toBeVisible()

  // 3. Interact with UI
  await page.locator('button:has-text("Clear")').click()
})
```

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
