# Engram

Engram is a Windows disc ripping and media organization tool. It monitors your optical drive, rips discs with MakeMKV, identifies episodes via audio fingerprinting, and files everything into your media library. A web dashboard shows progress in real time and lets you intervene when matches are ambiguous.

## Prerequisites

- **Windows** (drive monitoring uses kernel32/pywin32)
- [MakeMKV](https://www.makemkv.com/) with a valid license
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 18+

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

**OpenSubtitles** (optional): If you want subtitle-based episode matching, enter your OpenSubtitles username, password, and API key in the Settings page.

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
          (Kanban Board, Review Queue, Config Wizard)
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

## Development

```bash
# Lint and format
cd backend
uv run ruff check .
uv run ruff format .

# Backend tests
uv run pytest

# Frontend E2E tests (requires backend running with DEBUG=true)
cd frontend
npx playwright install   # first time only
npm run test:e2e
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
      api/          # REST + WebSocket endpoints
      core/         # Sentinel, Analyst, Extractor, Curator, Organizer
      matcher/      # Episode identification (ASR + subtitle matching)
      models/       # SQLModel database models
      services/     # Job orchestration
      config.py     # Settings
      database.py   # SQLite setup
      main.py       # FastAPI entry point
    pyproject.toml
    .env.example
  frontend/
    src/
      components/   # React components
      hooks/        # WebSocket hook
      types/        # TypeScript definitions
    e2e/            # Playwright E2E tests
    vite.config.ts
  README.md
```

## License

AGPL-3.0. See [LICENSE](LICENSE).

## Acknowledgments

- [MakeMKV](https://www.makemkv.com/) for disc decryption
- [mkv-episode-matcher](https://github.com/Jsakkos/mkv-episode-matcher) for audio fingerprinting
