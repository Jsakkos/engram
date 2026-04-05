# Architecture Overview

Engram uses a **hub-and-spoke** design. A Python/FastAPI backend acts as the central hub, orchestrating modular "spoke" components through a state machine. A React frontend communicates with the backend via REST API and WebSocket for real-time updates.

## High-Level Diagram

```
                        React Dashboard
          (Dashboard, Review Queue, History, Config Wizard)
                             |
                          WebSocket
                             |
                        FastAPI Backend
                             |
                        Job Manager
                             |
        +----------+---------+-----------+-----------+
        |          |         |           |           |
    Sentinel   Analyst   Extractor   Curator    Organizer
     (drive    (TV vs    (MakeMKV    (episode   (file
     monitor)  movie)    wrapper)    matching)  organization)
```

The **Job Manager** sits at the center, wiring the spokes together and driving each disc through the processing pipeline. Every state transition is persisted in SQLite and broadcast to connected clients over WebSocket.

---

## Backend

The backend lives in `backend/app/` and is built with FastAPI.

- **Entry point** (`main.py`) -- FastAPI app with lifespan management, CORS configured for the Vite dev server, and a WebSocket endpoint at `/ws`.
- **Config** (`config.py`) -- Pydantic Settings for server-level overrides (host, port, debug). No `.env` file is required; all fields have defaults.
- **Database** (`database.py`) -- Async SQLite via SQLModel + aiosqlite. Tables are auto-created on startup. Schema migration uses `ALTER TABLE ADD COLUMN` for additive changes (preserving job history) and drop/recreate only when columns are removed.

### Core Modules (`backend/app/core/`)

Each module maps to a stage in the disc processing pipeline.

| Module | File | Purpose |
|--------|------|---------|
| **Sentinel** | `sentinel.py` | Drive monitor. Polls optical drives on Windows using ctypes/kernel32. Fires async callbacks on disc insert/remove events. |
| **Analyst** | `analyst.py` | Disc classification. Heuristic-based TV vs Movie detection using cluster analysis of title durations. Outputs `DiscAnalysisResult` with content type, confidence score, and whether review is needed. |
| **Extractor** | `extractor.py` | MakeMKV CLI wrapper. Async subprocess management for `makemkvcon` scanning and ripping. Emits `RipProgress` callbacks. Tracks processes per job for multi-drive cancel isolation. |
| **Curator** | `curator.py` | Episode matching via audio fingerprinting. Classifies matches into high-confidence (auto-organize) and needs-review buckets. |
| **Organizer** | `organizer.py` | File organization. Moves files from staging to the media library with naming conventions: `Movies/Name (Year)/Name (Year).mkv` and `TV/Show/Season XX/Show - SXXEXX.mkv`. |
| **DiscDB Classifier** | `discdb_classifier.py` | TheDiscDB integration. Identifies discs via content hash fingerprinting (MD5 of concatenated BDMV/STREAM file sizes). Provides title-to-episode mappings. |
| **TMDB Classifier** | `tmdb_classifier.py` | TMDB-based content type classification. Uses name similarity and popularity ranking to provide strong TV vs Movie signals beyond the heuristic Analyst. |
| **Errors** | `errors.py` | Custom exception hierarchy (`EngramError` base, with `MakeMKVError`, `MatchingError`, `ConfigurationError`, `OrganizationError`, `SubtitleError`, `DatabaseError`). Includes `@handle_errors` decorator. |
| **Logging** | `logging.py` | Centralized logging configuration. |

### Services (`backend/app/services/`)

| Service | File | Purpose |
|---------|------|---------|
| **JobManager** | `job_manager.py` | Singleton orchestrator. Wires the spokes together, manages the `DiscJob` state machine lifecycle, handles `REVIEW_NEEDED` branching and `FAILED` states, broadcasts all transitions via WebSocket, coordinates subtitle download with matching via `asyncio.Event`. Includes simulation methods for E2E testing. |
| **JobStateMachine** | `job_state_machine.py` | Explicit state machine with validated transitions: `IDLE -> IDENTIFYING -> RIPPING -> MATCHING -> ORGANIZING -> COMPLETED`, with `REVIEW_NEEDED` and `FAILED` branching from most states. Fires terminal-state callbacks. |
| **RippingCoordinator** | `ripping_coordinator.py` | Coordinates the ripping process including subtitle download synchronization. |
| **EventBroadcaster** | `event_broadcaster.py` | Abstraction layer for broadcasting events to WebSocket clients. Wraps `ConnectionManager` with typed, domain-specific methods for each event type. |
| **ConfigService** | `config_service.py` | Configuration service with helper functions for loading and updating config from the database. |

### Data Models (`backend/app/models/`)

- **DiscJob** -- Central entity with `JobState` enum (idle, identifying, review_needed, ripping, matching, organizing, completed, failed) and `ContentType` enum (tv, movie, unknown). Key fields include `cleared_at` (soft-delete from dashboard), `completed_at` (auto-set on terminal state), `content_hash` (TheDiscDB fingerprint), and `discdb_mappings_json` (persisted title mappings).
- **DiscTitle** -- Individual title/track on a disc, linked to a job by foreign key. Stores match results (episode code, confidence), `TitleState`, edition info, conflict resolution, and organization tracking.
- **AppConfig** -- Persisted application configuration including paths, API keys, and preferences.

### Matcher (`backend/app/matcher/`)

Integrated from the standalone [mkv-episode-matcher](https://github.com/Jsakkos/mkv-episode-matcher) project.

- **ASR** (`asr_provider.py`) -- Speech recognition using faster-whisper/onnxruntime.
- **Subtitle matching** (`subtitle_provider.py`) -- Matches transcribed audio against reference subtitles.
- **Core engine** (`core/engine.py`, `core/matcher.py`) -- Matching engine logic.
- **Subtitle sources** -- `addic7ed_client.py`, `opensubtitles_scraper.py`, `subtitle_utils.py`.

### API (`backend/app/api/`)

- `routes.py` -- REST endpoints under `/api` prefix (job CRUD, review actions, config, simulation, staging management, job history, stats, diagnostics).
- `validation.py` -- Tool validation endpoints (`POST /api/validate/makemkv`, `POST /api/validate/ffmpeg`, `GET /api/detect-tools`).
- `test_routes.py` -- Standalone testing endpoints for subtitle download, transcription, and matching.
- `websocket.py` -- `ConnectionManager` singleton for broadcasting real-time updates to all connected clients.

---

## Frontend

The frontend is a React 18 + TypeScript + Vite single-page application located in `frontend/src/`. Vite proxies `/api` and `/ws` requests to the backend at `localhost:8000` during development.

**Key libraries**: React Router v7, Framer Motion, Recharts, React Hook Form, Tailwind CSS v4, shadcn/ui, Lucide React, Sonner.

| Component | Location | Purpose |
|-----------|----------|---------|
| **Dashboard** | `app/App.tsx` | Filterable job card list (Active, Done, All) with real-time progress, speed/ETA, cover art, and browser notifications. Cyberpunk dual-tone cyan/magenta theme. |
| **DiscCard** | `app/components/DiscCard.tsx` | Main job display component with subcomponents for media type badge, disc metadata, action buttons, and poster image hook. |
| **ReviewQueue** | `components/ReviewQueue.tsx` | Human-in-the-Loop UI for resolving ambiguous episode matches and movie edition selection. |
| **HistoryPage** | `components/HistoryPage.tsx` | All completed/failed jobs with stats dashboard, filterable table, slide-out detail panel, and deep-linking via `/history/:jobId`. |
| **ConfigWizard** | `components/ConfigWizard.tsx` | First-run setup and settings modal for library paths, MakeMKV license, TMDB token, and preferences. |
| **Supporting** | Various | `StateIndicator`, `CyberpunkProgressBar`, `TrackGrid`, `MatchingVisualizer`, `NamePromptModal`. |

**Hooks**: `useJobManagement` (job lifecycle + WebSocket), `useDiscFilters` (job filtering/transformation), `useWebSocket` (connection management with auto-reconnect).

---

## Database

Engram uses async SQLite via SQLModel + aiosqlite. The database file is stored at `backend/engram.db` in development mode.

### Schema Migration

The `_migrate_schema()` function in `database.py` handles schema evolution:

- **Additive changes**: Uses `ALTER TABLE ADD COLUMN` to add new columns to `disc_jobs` and `disc_titles`, preserving all existing job history across upgrades.
- **Column removal**: Only drops and recreates tables when columns are removed (rare).
- **AppConfig**: Always preserves data via backup/restore during migration.

There is no Alembic or other migration framework -- Engram uses direct schema comparison.

---

## Key Patterns

### Async Everywhere

The backend uses async throughout: async SQLAlchemy sessions, asyncio tasks for background jobs, and async subprocess calls for MakeMKV CLI operations.

### Singleton Services

Core services are module-level singletons: `job_manager`, `ws_manager`, `curator`, `movie_organizer`, `tv_organizer`. They are initialized once and shared across the application.

### State Machine Driven

All job lifecycle is tracked through `JobState` transitions persisted in SQLite. The `JobStateMachine` validates every transition against a map of allowed state changes, ensuring jobs never enter invalid states.

### Subtitle Coordination

Subtitle downloads run in the background during ripping. An `asyncio.Event` synchronizes the two processes -- matching awaits the event before proceeding, ensuring subtitles are available when needed.

### DiscDB Mapping Persistence

TheDiscDB title mappings are serialized as JSON in the `discdb_mappings_json` column on `DiscJob`. They are persisted during identification and restored from the database on server startup via `_restore_discdb_mappings()`.

### Custom Error Hierarchy

All domain errors extend `EngramError` with typed subclasses (`MakeMKVError`, `MatchingError`, `ConfigurationError`, etc.). The `@handle_errors` decorator provides standardized error handling in service methods.

### Simulation Endpoints

When `DEBUG=true`, simulation endpoints allow testing the full workflow without a physical disc. These are used extensively by E2E tests and manual development.
