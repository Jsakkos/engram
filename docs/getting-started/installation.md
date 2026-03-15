# Installation

## Prerequisites

- [MakeMKV](https://www.makemkv.com/) with a valid license key
- A [TMDB Read Access Token](https://www.themoviedb.org/settings/api) (v4 auth) for media metadata and poster art
- If running from source: **Python 3.11+** with [uv](https://docs.astral.sh/uv/), and **Node.js 18+**

## Option A: Standalone Executable (Windows)

The simplest way to get started on Windows -- no Python or Node.js required.

1. Download `engram-windows-x64.zip` from the [Releases](https://github.com/Jsakkos/engram/releases) page.
2. Extract the archive.
3. Run `engram.exe`.

The Config Wizard will open in your browser on first launch to walk you through setup.

## Option B: From Source (All Platforms)

Clone the repository and install dependencies for both the backend and frontend:

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

### GPU-Accelerated Transcription (Optional)

For faster episode matching via GPU-accelerated ASR (requires a CUDA-capable GPU):

```bash
cd backend
uv sync --extra gpu
```

This installs the GPU-optimized build of faster-whisper/onnxruntime instead of the CPU-only default.

## Starting the Dev Servers

You need two terminals -- one for the backend, one for the frontend.

**Backend** (serves the API on port 8000):

```bash
cd backend
uv run uvicorn app.main:app --reload
```

**Frontend** (serves the dashboard on port 5173, proxies API calls to the backend):

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser. The Config Wizard will appear on first launch.

## Verifying the Installation

Once both servers are running:

1. The browser should show the Engram dashboard at `localhost:5173`.
2. The Config Wizard will prompt you to configure MakeMKV path, library paths, and your TMDB token.
3. Engram will auto-detect MakeMKV and FFmpeg if they are on your system PATH.

!!! tip "No physical disc drive?"
    You can test the full workflow without hardware using simulation mode.
    See [Simulation](simulation.md) for details.

## Common Commands

### Backend (from `backend/`)

| Command | Description |
|---------|-------------|
| `uv sync` | Install/sync Python dependencies |
| `uv run uvicorn app.main:app --reload` | Start dev server (port 8000) |
| `uv run pytest` | Run all backend tests |
| `uv run ruff check .` | Lint Python code |
| `uv run ruff format .` | Format Python code |

### Frontend (from `frontend/`)

| Command | Description |
|---------|-------------|
| `npm install` | Install Node dependencies |
| `npm run dev` | Start Vite dev server (port 5173) |
| `npm run build` | TypeScript check + production build |
| `npm run lint` | ESLint |
| `npm run test:e2e` | Run Playwright E2E tests |
