# Engram

**Glass-Box Automation for Disc Ripping** — Transparent, Visual, and User-Centric.

![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-green.svg)
![React](https://img.shields.io/badge/react-18+-61dafb.svg)

## Overview

Engram is a modern disc ripping and media organization tool with a reactive web dashboard. Unlike headless automation tools, Engram provides full visibility into the ripping process with intelligent Human-in-the-Loop intervention for ambiguous content.

### Key Features

- 🔍 **Automatic Disc Detection** — Windows drive monitoring with instant notification
- 🎬 **Smart Content Classification** — Heuristic-based TV/Movie detection
- 💿 **Lossless Extraction** — MakeMKV integration for raw MKV passthrough
- 📺 **Episode Matching** — Audio fingerprint-based episode identification
- 🖥️ **Glass-Box Dashboard** — Real-time progress with WebSocket updates
- 👤 **Human-in-the-Loop** — Review queue for ambiguous matches

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- [MakeMKV](https://www.makemkv.com/) with a valid license
- [uv](https://docs.astral.sh/uv/) for Python dependency management

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/engram.git
   cd engram
   ```

2. **Set up the backend:**
   ```bash
   cd backend
   uv sync
   ```

3. **Set up the frontend:**
   ```bash
   cd frontend
   npm install
   ```

4. **Start the development servers:**

   Backend (Terminal 1):
   ```bash
   cd backend
   uv run uvicorn app.main:app --reload
   ```

   Frontend (Terminal 2):
   ```bash
   cd frontend
   npm run dev
   ```

5. **Open your browser** to http://localhost:5173

## Configuration

On first launch, the **Config Wizard** in the web UI will guide you through setup (MakeMKV path, library paths, TMDB token, etc.). All user-configurable settings are stored in the database and managed via the Settings page.

An optional `backend/.env` file can override server-level defaults:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///./engram.db` |
| `HOST` | Server bind address | `127.0.0.1` |
| `PORT` | Server port | `8000` |
| `DEBUG` | Enable debug mode and simulation endpoints | `false` |

> **TMDB Note:** The TMDB setting in the Config Wizard expects a **Read Access Token** (v4 auth) from your [TMDB API Settings](https://www.themoviedb.org/settings/api) page (the long JWT string starting with `eyJ...`), not the shorter "API Key" (v3 auth).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     React Dashboard                          │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │Dashboard │  │ Review Queue │  │ Config Wizard      │    │
│  └────┬─────┘  └──────┬───────┘  └────────────────────┘    │
│       │               │                                      │
│       └───────┬───────┘                                      │
│               │ WebSocket                                    │
└───────────────┼──────────────────────────────────────────────┘
                │
┌───────────────┼──────────────────────────────────────────────┐
│               │            FastAPI Backend                   │
│  ┌────────────▼────────────┐                                 │
│  │     Job Manager         │                                 │
│  └────────────┬────────────┘                                 │
│               │                                              │
│  ┌────────────┼────────────────────────────────────┐        │
│  │            │                                     │        │
│  ▼            ▼            ▼            ▼          │        │
│ Sentinel   Analyst    Extractor    Curator         │        │
│ (Drive     (Content   (MakeMKV     (Episode        │        │
│  Monitor)   Analysis)  Wrapper)     Matcher)       │        │
│  └────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────┘
```

## Development

### Linting and Formatting

```bash
cd backend
uv run ruff check .      # Lint
uv run ruff format .     # Format
```

### Running Tests

```bash
# Backend unit/integration tests
cd backend
uv run pytest

# Frontend E2E tests (requires both servers running, or use Playwright's webServer config)
cd frontend
npx playwright install   # First time only
npm run test:e2e          # Headless
npm run test:e2e:headed   # With browser visible
npm run test:e2e:ui       # Interactive UI mode
```

### Simulation (for UI development without a physical disc)

With the backend running in debug mode (`DEBUG=true`):

```bash
# Simulate a TV disc with auto-ripping
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D1","content_type":"tv","simulate_ripping":true}'

# Simulate a movie disc
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"INCEPTION_2010","content_type":"movie","simulate_ripping":true}'
```

## Project Structure

```
engram/
├── backend/
│   ├── app/
│   │   ├── api/           # REST + WebSocket endpoints
│   │   ├── core/          # Sentinel, Analyst, Extractor, Curator
│   │   ├── models/        # SQLModel database models
│   │   ├── services/      # Job orchestration
│   │   ├── config.py      # Settings management
│   │   ├── database.py    # SQLite setup
│   │   └── main.py        # FastAPI entry point
│   ├── pyproject.toml
│   └── .env.example       # Optional server overrides
├── frontend/
│   ├── src/
│   │   ├── components/    # React components
│   │   ├── hooks/         # Custom hooks (WebSocket)
│   │   ├── types/         # TypeScript definitions
│   │   └── App.tsx
│   ├── e2e/               # Playwright E2E tests
│   │   └── fixtures/      # Test scenarios & API helpers
│   ├── package.json
│   ├── playwright.config.ts
│   └── vite.config.ts
└── README.md
```

## License

AGPL-3.0 — See [LICENSE](LICENSE) for details.

## Acknowledgments

- [MakeMKV](https://www.makemkv.com/) for disc decryption
- [mkv-episode-matcher](https://github.com/yourusername/mkv-episode-matcher) for audio fingerprinting
