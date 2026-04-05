# Linux / macOS Setup

Engram runs fully on Linux and macOS. This guide covers installation, the two supported workflows (optical drive and staging folder), and troubleshooting.

## Installation

### Prerequisites

=== "Debian / Ubuntu / Mint"

    MakeMKV is not in the standard repositories. Install via the official PPA:

    ```bash
    sudo add-apt-repository ppa:heyarje/makemkv-beta
    sudo apt update
    sudo apt install makemkv-bin makemkv-oss ffmpeg
    ```

    Alternatively, download and build from source at [makemkv.com](https://www.makemkv.com/forum/viewtopic.php?f=3&t=224).

=== "Fedora / RHEL"

    MakeMKV is available via RPM Fusion or can be built from source:

    ```bash
    sudo dnf install ffmpeg
    # Install MakeMKV from https://www.makemkv.com/
    ```

=== "macOS"

    Install [MakeMKV](https://www.makemkv.com/) from the official site and [FFmpeg](https://ffmpeg.org/) via Homebrew:

    ```bash
    brew install ffmpeg
    ```

### From Source

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

### Start Engram

Terminal 1 (backend):

```bash
cd backend
uv run uvicorn app.main:app --reload
```

Terminal 2 (frontend):

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The Config Wizard will guide you through initial setup.

## Workflow 1: Optical Drive (Linux Only)

If your machine has an optical drive, Engram detects it automatically on Linux via `/sys/block/sr*`.

1. Insert a disc
2. Engram detects the drive, reads the volume label, and creates a job
3. MakeMKV scans and rips the disc
4. Engram classifies the content (TV/movie), matches episodes, and organizes files

!!! note "How it works under the hood"
    - Drive enumeration: reads `/sys/block/sr*` device entries
    - Volume label: calls `blkid -s LABEL -o value /dev/sr0`
    - Disc presence: reads `/sys/block/sr0/size` (non-zero = disc present)
    - Ejection: runs `eject /dev/sr0`

    If `blkid` or `eject` are not installed, those features degrade gracefully (empty labels, no auto-eject).

!!! warning "macOS"
    macOS does not have automatic drive detection. The `/sys/block` and `blkid` interfaces described above are Linux-specific. On macOS, use the staging folder workflow below.

## Workflow 2: Staging Folder (All Platforms)

The staging folder workflow lets you use MakeMKV externally and have Engram handle the rest (classification, matching, organization). This is the primary workflow on systems without optical drives.

### Automatic (Staging Watcher)

The staging watcher monitors your staging directory for new folders containing MKV files. Enable it in Settings or via the API (see [Configuration](#configuration) below).

1. Rip your disc with MakeMKV to a folder:
   ```
   ~/engram/staging/ARRESTED_DEVELOPMENT_S1D1/
     title_t00.mkv
     title_t01.mkv
     title_t02.mkv
     title_t03.mkv
   ```

2. Engram detects the folder within a few seconds (after confirming files are done copying)

3. A job appears on the dashboard and progresses through identification, matching, and organization

!!! tip "Naming your folder"
    The folder name is used as the volume label for classification. Names like `SHOW_NAME_S01D01` or `MOVIE_NAME_2024` give the best results, matching what a real disc label would look like.

!!! tip "How debouncing works"
    The watcher waits until file sizes stabilize across 2 consecutive polls (~4 seconds) before triggering import. This prevents processing while files are still being copied.

### Manual (API)

You can also trigger import explicitly via the staging import API:

```bash
# TV show
curl -X POST localhost:8000/api/staging/import \
  -H "Content-Type: application/json" \
  -d '{
    "staging_path": "/home/you/engram/staging/SHOW_S1D1",
    "volume_label": "SHOW_S1D1",
    "content_type": "tv",
    "detected_title": "Show Name",
    "detected_season": 1
  }'

# Movie
curl -X POST localhost:8000/api/staging/import \
  -H "Content-Type: application/json" \
  -d '{
    "staging_path": "/home/you/engram/staging/INCEPTION_2010",
    "volume_label": "INCEPTION_2010",
    "content_type": "movie",
    "detected_title": "Inception"
  }'
```

This endpoint is available in all modes (no `DEBUG=true` required).

### Configuration

The staging watcher can be toggled in Settings or via the API:

```bash
# Disable staging watcher
curl -X PUT localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{"staging_watch_enabled": false}'

# Enable staging watcher
curl -X PUT localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{"staging_watch_enabled": true}'
```

The staging directory path is set during initial setup (default: `~/engram/staging/` on Linux/macOS, `~/Engram/Staging/` on Windows).

## Troubleshooting

### "0 optical drives found"

This is normal if your machine doesn't have an optical drive. Use the staging folder workflow instead.

If you do have an optical drive and it's not detected:

1. Check that the device exists: `ls /dev/sr*`
2. Check permissions: `ls -la /dev/sr0` — your user should have read access
3. Add your user to the `cdrom` group if needed: `sudo usermod -aG cdrom $USER` (log out and back in)

### MakeMKV not found

Engram looks for `makemkvcon` on your PATH. Verify it's installed:

```bash
which makemkvcon
makemkvcon --version
```

If installed but not on PATH, set the path in Settings.

### Staging watcher not triggering

- Verify the watcher is enabled: check `staging_watch_enabled` in Settings
- Ensure MKV files are in a **subdirectory** of the staging path, not directly in it
- Folder names starting with `job_` are ignored (reserved for the ripping pipeline)
- Files must be stable (not still being copied) for ~4 seconds before triggering

### FFmpeg not found

Episode matching requires FFmpeg for audio extraction. Install it:

```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# Fedora
sudo dnf install ffmpeg

# macOS
brew install ffmpeg
```
