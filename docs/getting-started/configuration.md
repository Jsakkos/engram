# Configuration

## Config Wizard

On first launch, Engram presents a Config Wizard that walks you through essential setup:

- **MakeMKV path** -- auto-detected if on your system PATH
- **FFmpeg path** -- auto-detected if on your system PATH
- **Staging path** -- where ripped files are stored temporarily
- **Library paths** -- separate directories for Movies and TV shows
- **TMDB Read Access Token** -- for media metadata and poster art
- **MakeMKV license key** -- your MakeMKV registration key

Settings are stored in the SQLite database and can be edited at any time from the Settings page (gear icon in the dashboard header).

## TMDB API Token

The TMDB setting requires a **Read Access Token** (v4 auth), **not** the shorter v3 API Key.

To obtain your token:

1. Create an account at [TMDB](https://www.themoviedb.org/).
2. Go to [API Settings](https://www.themoviedb.org/settings/api).
3. Copy the **Read Access Token** -- this is a long JWT string starting with `eyJ...`.

!!! warning "Common mistake"
    The v3 "API Key" is a short alphanumeric string. Engram needs the v4 "Read Access Token" (the long JWT). Using the wrong one will cause TMDB lookups to fail silently.

The configuration field is named `tmdb_api_key` for backwards compatibility, but it expects the v4 Read Access Token.

## Environment Variables

An optional `backend/.env` file can override server-level defaults. These settings are **not** managed through the Config Wizard -- they control the server itself.

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///./engram.db` |
| `HOST` | Server bind address | `127.0.0.1` |
| `PORT` | Server port | `8000` |
| `DEBUG` | Enable simulation endpoints | `false` |

Example `.env` file:

```ini
DEBUG=true
HOST=0.0.0.0
PORT=8000
```

!!! note
    The `.env` file is optional. All fields have sensible defaults. The file is included in `.gitignore` and should never be committed.

## LAN Access (Dashboard on Other Devices)

By default Engram binds to `127.0.0.1` (localhost only), so the dashboard is only reachable on the machine running Engram. To monitor from a phone, tablet, or another computer on the same network:

### Via Settings (Windows desktop)

1. Open the Settings page (gear icon) → **Preferences** step.
2. Enable **"Allow access from other devices on my network (LAN)"**.
3. The panel below the toggle shows your LAN address and a QR code once the change is applied.
4. Click **Save** and **restart Engram** — the bind address is fixed at startup.

!!! warning "No authentication"
    Engram has no login. Anyone on your network can view job status and control the application.
    Only enable this on a trusted home network.

After restart, access the dashboard from any device on your LAN at `http://<host-ip>:8000`.
The QR code makes it easy to open on a phone or tablet.

### Via environment variable (power users / Docker)

Set `HOST=0.0.0.0` in your `backend/.env` file (or pass it as an environment variable in Docker).
The env var takes precedence over the UI toggle:

```ini
HOST=0.0.0.0
PORT=8000
```

For Docker containers this is typically the right approach — the container has its own network
namespace, so binding to `127.0.0.1` inside the container would make it unreachable even via
published ports.

## Configuration Sources

Configuration is resolved in this priority order:

1. **Database** (`app_config` table) -- runtime configuration, editable via API and Config Wizard
2. **Environment variables** (or `.env` file) -- server-level settings only
3. **Defaults** -- hardcoded in the `AppConfig` model

## Configuration Fields

### Paths

| Field | Description | Default |
|-------|-------------|---------|
| `staging_path` | Temporary directory for ripped files | *(set during wizard)* |
| `library_movies_path` | Movie library root directory | *(set during wizard)* |
| `library_tv_path` | TV show library root directory | *(set during wizard)* |
| `makemkv_path` | Path to `makemkvcon` executable | *(auto-detected)* |
| `ffmpeg_path` | Path to `ffmpeg` executable | *(auto-detected)* |

### API Keys

| Field | Description | Notes |
|-------|-------------|-------|
| `makemkv_key` | MakeMKV registration key | Redacted in API responses |
| `tmdb_api_key` | TMDB Read Access Token (v4) | Redacted in API responses |

### Matching & Processing

| Field | Description | Default |
|-------|-------------|---------|
| `max_concurrent_matches` | Parallel episode-matching (ASR) tasks | `2` |
| `matcher_min_confidence` | Minimum confidence to auto-accept an episode match | `0.6` |
| `enable_gpu_acceleration` | Run transcription on an NVIDIA GPU (opt-in; downloads the CUDA runtime) | `false` |
| `conflict_resolution_default` | File conflict handling | `"ask"` |

`max_concurrent_matches` is the main matching-throughput knob. It's clamped to your hardware and
takes effect on restart — see [Performance & Hardware](../guide/performance.md#concurrency-tuning)
for how to tune it for CPU vs GPU and bulk imports.

`enable_gpu_acceleration` is best toggled from **Settings → Matching → GPU Acceleration** rather than
edited directly, since enabling it triggers the one-time CUDA-runtime download. See
[GPU acceleration](../guide/performance.md#gpu-acceleration-faster-whisper-asr).

The `conflict_resolution_default` field accepts one of four values:

- `"ask"` -- prompt via the review queue (default)
- `"skip"` -- skip files that already exist in the library
- `"overwrite"` -- replace existing files
- `"rename"` -- keep both by writing a numbered `(vN)` variant

### AI-Powered Title Resolution

`ai_identification_enabled` (default: `false`) — when enabled, Engram sends the disc volume label and any collected metadata to your configured AI provider to help resolve ambiguous or unrecognised disc titles.

| Field | Description | Notes |
|-------|-------------|-------|
| `ai_identification_enabled` | Enable AI-assisted disc title resolution | Requires `ai_provider` and `ai_api_key` |
| `ai_provider` | AI provider to use | `anthropic`, `openai`, `openrouter`, `gemini` |
| `ai_api_key` | API key for the selected provider | Redacted in API responses |

### AI-Powered Episode Matching

`ai_episode_matching_enabled` (default: `false`) — when enabled, low-confidence TV episode matches are sent to your configured AI provider with the season's TMDB synopses for a suggested episode. Always surfaces through the [review queue](../guide/review-queue.md); never auto-organizes. Shares `ai_provider`/`ai_api_key` with [AI-Powered Title Resolution](#ai-powered-title-resolution).

See the [LLM Episode Matcher guide](../guide/llm-episode-matcher.md) for accuracy expectations and provider recommendations (Gemini Flash-Lite is best on this task).

### Extras Policy

Controls how bonus content (behind-the-scenes, deleted scenes, etc.) is handled during organization:

| Field | Description | Default |
|-------|-------------|---------|
| `extras_policy` | How to handle extras (`"keep"`, `"skip"`, `"ask"`) | `"keep"` |

### Import Watch Folder

Engram can auto-ingest pre-ripped MKV files dropped into a watched folder. See the
[Import Watch Folder guide](../guide/import-watch-folder.md) for folder layouts and the full
workflow.

| Field | Description | Default |
|-------|-------------|---------|
| `import_watch_path` | Folder to watch for incoming MKV files (unset = disabled) | *(none)* |
| `import_destination_mode` | `"library"` files imports into your Movies/TV libraries; `"in_place"` organizes them inside the watch folder | `"library"` |
| `staging_watch_enabled` | Also auto-import folders dropped into the staging directory | `false` |

### Staging Cleanup

Controls when the staging directory is reclaimed after a job finishes. Watch-folder imports are
never deleted regardless of this setting (the source folder is yours). See
[sizing the staging directory](../guide/performance.md#sizing-the-staging-directory).

| Field | Description | Default |
|-------|-------------|---------|
| `staging_cleanup_policy` | `"on_success"`, `"on_completion"`, `"manual"`, or `"after_days"` | `"on_success"` |
| `staging_cleanup_days` | Retention period when policy is `"after_days"` | `7` |

### Naming Conventions

Organized files follow these naming patterns:

- **Movies**: `Movies/Name (Year)/Name (Year).mkv`
- **TV Shows**: `TV/Show/Season XX/Show - SXXEXX.mkv`

## Configuration Flow

```
User edits config in Config Wizard / Settings page
  |
  v
PUT /api/config
  |
  v
Update AppConfig in database
  |
  v
JobManager reloads config on next operation
  |
  v
Components use updated settings
```

## Validation

Configuration is validated at multiple levels:

- **Pydantic models** -- type checking and required fields
- **API routes** -- path existence checks, MakeMKV license validation
- **Validation endpoints** -- `POST /api/validate/makemkv`, `POST /api/validate/ffmpeg`, `GET /api/detect-tools`
- **JobManager** -- pre-flight checks before starting any job

API keys are **redacted** (masked as `"***"`) in `GET /api/config` responses. The `PUT /api/config` endpoint accepts new values but never returns sensitive fields in the response.
