# Installation

## Prerequisites

- [MakeMKV](https://www.makemkv.com/) with a valid license key
- [FFmpeg](https://ffmpeg.org/download.html) for episode matching (audio fingerprinting) — see [Installing FFmpeg](#installing-ffmpeg)
- A [TMDB Read Access Token](https://www.themoviedb.org/settings/api) (v4 auth) for media metadata and poster art
- If running from source: **Python 3.11+** with [uv](https://docs.astral.sh/uv/), and **Node.js 24**

### Linux-Specific Prerequisites

On Debian/Ubuntu-based distributions (including Linux Mint):

```bash
# MakeMKV (requires PPA — not in standard repos)
sudo add-apt-repository ppa:heyarje/makemkv-beta
sudo apt update
sudo apt install makemkv-bin makemkv-oss

# FFmpeg (for episode matching)
sudo apt install ffmpeg

# Optional: blkid and eject for optical drive detection (usually pre-installed)
sudo apt install util-linux eject
```

Alternatively, build MakeMKV from source: [makemkv.com](https://www.makemkv.com/forum/viewtopic.php?f=3&t=224).

On Fedora/RHEL:

```bash
sudo dnf install ffmpeg eject
# Install MakeMKV from https://www.makemkv.com/
```

## Installing FFmpeg

Engram uses FFmpeg for episode matching — audio fingerprinting and the speech-recognition fallback both decode audio with it. On first launch Engram auto-detects FFmpeg on your `PATH` and in common install locations; if it can't find it, install it as below and restart Engram.

!!! note "The version doesn't matter"
    Engram only needs to be able to run FFmpeg — any recent build works. A "not detected" result almost always means FFmpeg isn't on your `PATH`, not that the version is wrong.

### Windows

The simplest option is [winget](https://learn.microsoft.com/windows/package-manager/winget/):

```powershell
winget install Gyan.FFmpeg
```

Then **fully close and reopen Engram** so it picks up the updated `PATH` — a running process keeps the `PATH` it started with.

Prefer a manual install? Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases), extract it, and do **one** of:

- Add the extracted `...\bin` folder to your **system PATH** (Settings → *Edit the system environment variables* → *Environment Variables*), then restart Engram.
- Drop `ffmpeg.exe` at `C:\ffmpeg\bin\ffmpeg.exe` — one of the locations Engram scans automatically.
- Point Engram at it directly: in the Config Wizard (or **Settings → Tools**), use **Override path manually** and enter the full path to `ffmpeg.exe`. The path is validated immediately and shows the detected version.

Still not detected? See [Troubleshooting → FFmpeg not detected (Windows)](../troubleshooting.md#ffmpeg-not-detected-windows).

### Linux / macOS

```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# Fedora
sudo dnf install ffmpeg

# macOS (Homebrew)
brew install ffmpeg
```

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
uv run uvicorn app.main:app
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
3. Engram will auto-detect MakeMKV and FFmpeg if they are on your system PATH. If FFmpeg isn't detected, see [Installing FFmpeg](#installing-ffmpeg) or [Troubleshooting](../troubleshooting.md#ffmpeg-not-detected-windows).

!!! tip "No physical disc drive?"
    You can test the full workflow without hardware using simulation mode.
    See [Simulation](simulation.md) for details.

## Common Commands

### Backend (from `backend/`)

| Command | Description |
|---------|-------------|
| `uv sync` | Install/sync Python dependencies |
| `uv run uvicorn app.main:app` | Start dev server (port 8000) |
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
