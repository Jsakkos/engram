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
| `max_concurrent_matches` | Parallel episode matching jobs | `3` |
| `transcoding_enabled` | Enable post-rip transcoding | `false` |
| `conflict_resolution_default` | File conflict handling | `"skip"` |

The `conflict_resolution_default` field accepts one of three values:

- `"skip"` -- skip files that already exist in the library
- `"overwrite"` -- replace existing files
- `"ask"` -- prompt via the review queue

### Extras Policy

Controls how bonus content (behind-the-scenes, deleted scenes, etc.) is handled during organization:

| Field | Description | Default |
|-------|-------------|---------|
| `extras_policy` | How to handle extras | `"skip"` |

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
