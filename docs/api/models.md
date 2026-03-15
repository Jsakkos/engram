# Data Models

Engram uses [SQLModel](https://sqlmodel.tiangolo.com/) for database models, combining SQLAlchemy's ORM with Pydantic's validation.

## DiscJob

The central model tracking a disc ripping job through its lifecycle.

::: app.models.disc_job.DiscJob
    options:
      show_source: false
      members: false

### Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Primary key |
| `drive_id` | str | Drive letter or device path (e.g., `E:`, `/dev/sr0`) |
| `volume_label` | str | Disc volume label (e.g., `THE_OFFICE_S1`) |
| `content_type` | ContentType | `tv`, `movie`, or `unknown` |
| `state` | JobState | Current state in the lifecycle |
| `detected_title` | str | Classified title (e.g., "The Office") |
| `detected_season` | int | Season number for TV shows |
| `classification_source` | str | `heuristic`, `tmdb`, or `discdb_*` |
| `classification_confidence` | float | 0.0 to 1.0 |
| `content_hash` | str | TheDiscDB MD5 fingerprint |
| `discdb_mappings_json` | str | Persisted DiscDB title-to-episode mappings |
| `error_message` | str | Error reason if job failed |
| `created_at` | datetime | When the job was created |
| `completed_at` | datetime | When the job reached a terminal state |
| `cleared_at` | datetime | Soft-delete timestamp (hidden from dashboard, still in history) |

## DiscTitle

Individual title (track) on a disc, linked to a job.

::: app.models.disc_job.DiscTitle
    options:
      show_source: false
      members: false

### Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Primary key |
| `job_id` | int | Foreign key to DiscJob |
| `title_index` | int | MakeMKV title index |
| `duration_seconds` | int | Track duration |
| `file_size_bytes` | int | File size |
| `state` | TitleState | Current title state |
| `matched_episode` | str | Matched episode code (e.g., `S01E01`) |
| `match_confidence` | float | Match confidence score |
| `video_resolution` | str | e.g., `4K`, `1080p`, `480p` |
| `edition` | str | e.g., `Extended`, `Director's Cut` |
| `organized_to` | str | Final library path after organization |
| `is_extra` | bool | Whether organized as extra content |

## Enums

### JobState

::: app.models.disc_job.JobState
    options:
      show_source: true
      members: true

### ContentType

::: app.models.disc_job.ContentType
    options:
      show_source: true
      members: true

### TitleState

::: app.models.disc_job.TitleState
    options:
      show_source: true
      members: true

## AppConfig

Persisted application configuration, stored in the `app_config` table.

::: app.models.AppConfig
    options:
      show_source: false
      members: false

Configuration includes paths (staging, library, MakeMKV, FFmpeg), API keys (TMDB, MakeMKV license), matching parameters, ripping coordination settings, staging cleanup policy, extras policy, and naming conventions.
