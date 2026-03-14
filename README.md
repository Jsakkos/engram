# Engram

Engram is a Windows disc ripping and media organization tool. It monitors your optical drive, rips discs with MakeMKV, identifies episodes via audio fingerprinting, and files everything into your media library. A web dashboard shows progress in real time and lets you intervene when matches are ambiguous.

## Features

- **Automatic disc detection** — monitors optical drives and starts processing on insertion
- **Smart classification** — distinguishes TV shows from movies using duration analysis, TMDB lookup, and TheDiscDB
- **Audio fingerprint matching** — identifies TV episodes via ASR transcription matched against subtitles
- **Real-time dashboard** — cyberpunk-themed web UI with WebSocket live updates, progress tracking, and notifications
- **Human-in-the-loop** — review queue for low-confidence matches with competing candidate display
- **Job history** — searchable archive of completed jobs with analytics
- **Responsive design** — works on desktop and mobile with compact/expanded view modes

## Prerequisites

- **Windows** (drive monitoring uses kernel32/pywin32)
- [MakeMKV](https://www.makemkv.com/) with a valid license
- TMDB API Read Access Token (v4) [TMDB](https://www.themoviedb.org/settings/api)
- If running from source, Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- If running from source, Node.js 18+

## Install

### Option A: Standalone executable

Download `engram-windows-x64.zip` from the [Releases](https://github.com/Jsakkos/engram/releases) page, extract it, and run `engram.exe`. No Python or Node.js required.

### Option B: From source

```bash
git clone https://github.com/Jsakkos/engram.git
cd engram

# Backend
cd backend
uv sync
cd ..

# Frontend
cd frontend
npm install
cd ..
```

For GPU-accelerated transcription (optional):

```bash
cd backend
uv sync --extra gpu
```

### Start the dev servers

Backend:

```bash
cd backend
uv run uvicorn app.main:app --reload
```

Frontend (separate terminal):

```bash
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

## Configuration

On first launch the Config Wizard walks you through setup: MakeMKV path, library paths, TMDB token, and more. Settings are stored in the database and editable from the Settings page.

**TMDB**: The wizard asks for a **Read Access Token** (v4 auth) from your [TMDB API Settings](https://www.themoviedb.org/settings/api). This is the long JWT string starting with `eyJ...`, not the shorter v3 API Key.

An optional `backend/.env` file can override server-level defaults:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///./engram.db` |
| `HOST` | Server bind address | `127.0.0.1` |
| `PORT` | Server port | `8000` |
| `DEBUG` | Enable simulation endpoints | `false` |

## Architecture

Hub-and-spoke design. The Job Manager orchestrates five modules through a state machine (`IDLE -> IDENTIFYING -> RIPPING -> MATCHING -> ORGANIZING -> COMPLETED`).

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

### Frontend

React 18 + TypeScript + Vite SPA with a cyberpunk dual-tone (cyan/magenta) theme on a deep navy background with circuit board traces.

- **Dashboard** — filterable job cards (Active/Done/All) with expanded and compact view modes, real-time progress, speed/ETA, cover art with holographic effects, and browser notifications
- **Review Queue** — human-in-the-loop UI for resolving ambiguous episode matches and movie edition selection
- **History** — searchable archive of completed/failed jobs with duration and size analytics
- **Config Wizard** — first-run setup and settings modal for library paths, API keys, and preferences

Key libraries: React Router v7, Framer Motion, Tailwind CSS v4 (with `@theme inline`), shadcn/ui, Lucide React, Sonner.

## Development

```bash
# Lint and format
cd backend
uv run ruff check .
uv run ruff format .

# Backend tests
uv run pytest

# Frontend unit tests
cd frontend
npm run test:unit

# Frontend E2E tests (requires backend running with DEBUG=true)
npx playwright install   # first time only
npm run test:e2e
npm run test:e2e:headed  # with visible browser
```

### Simulation

With `DEBUG=true`, you can test the full workflow without a physical disc:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D1","content_type":"tv","simulate_ripping":true}'
```

## Project Structure

```
engram/
  backend/
    app/
      api/            # REST + WebSocket endpoints
      core/           # Sentinel, Analyst, Extractor, Curator, Organizer,
                      #   DiscDB Classifier, TMDB Classifier, Snapshot
      matcher/        # Episode identification (ASR + subtitle matching)
      models/         # SQLModel database models (DiscJob, DiscTitle, AppConfig)
      services/       # Job Manager, State Machine, Ripping Coordinator,
                      #   Event Broadcaster, Config Service
      config.py       # Server-level settings (host, port, debug)
      database.py     # Async SQLite setup + schema migration
      main.py         # FastAPI entry point with lifespan management
    pyproject.toml
    .env.example
  frontend/
    src/
      app/
        components/   # DiscCard, TrackGrid, StateIndicator, ProgressBar,
                      #   MatchingVisualizer, shadcn/ui primitives
        hooks/        # useJobManagement, useDiscFilters, useElapsedTime,
                      #   useNotifications
      components/     # HistoryPage, ReviewQueue, ConfigWizard, NamePromptModal
      config/         # UI constants and thresholds
      hooks/          # useWebSocket
      styles/         # Tailwind theme (navy palette, glow tokens, circuit board)
      types/          # TypeScript definitions + adapters
    e2e/              # Playwright E2E tests (10 spec files)
    vite.config.ts
  README.md
```

## License

AGPL-3.0. See [LICENSE](LICENSE).

## Acknowledgments

- [MakeMKV](https://www.makemkv.com/) for disc decryption
- [mkv-episode-matcher](https://github.com/Jsakkos/mkv-episode-matcher) for audio fingerprinting
- [TheDiscDB](https://thediscdb.com/) for disc content hash lookups
- [TMDB](https://www.themoviedb.org/) for media metadata and poster art
