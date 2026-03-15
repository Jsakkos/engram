# REST API Endpoints

All endpoints are under the `/api` prefix.

## Jobs

### List Active Jobs

```
GET /api/jobs
```

Returns active disc jobs (excludes cleared/archived jobs). Limited to 10 most recent.

::: app.api.routes.list_jobs
    options:
      show_source: false

### Get Job by ID

```
GET /api/jobs/{job_id}
```

::: app.api.routes.get_job
    options:
      show_source: false

### Get Job Detail

```
GET /api/jobs/{job_id}/detail
```

Returns full job detail with all titles for history drill-down. Includes classification metadata, TheDiscDB mappings, subtitle info, and per-track breakdown.

::: app.api.routes.get_job_detail
    options:
      show_source: false

### Get Job Titles

```
GET /api/jobs/{job_id}/titles
```

Returns all titles (tracks) with match results for a job.

::: app.api.routes.get_job_titles
    options:
      show_source: false

### Start Job

```
POST /api/jobs/{job_id}/start
```

Start ripping a disc. Job must be in `idle` or `review_needed` state.

### Cancel Job

```
POST /api/jobs/{job_id}/cancel
```

Cancel a running job.

### Clear Job

```
DELETE /api/jobs/{job_id}
```

Soft-delete a job from the dashboard (sets `cleared_at` timestamp). Job remains visible in history.

---

## History & Analytics

### Job History

```
GET /api/jobs/history?page=1&per_page=20&content_type=tv&state=completed
```

Returns all completed/failed jobs with pagination and filtering. Jobs appear in history automatically when they reach a terminal state — no manual clearing required.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Results per page (max 100) |
| `content_type` | string | — | Filter: `tv` or `movie` |
| `state` | string | — | Filter: `completed` or `failed` |

### Job Statistics

```
GET /api/jobs/stats
```

Returns aggregated analytics: total jobs, completed/failed counts, TV/movie counts, average processing time, top 5 common errors, and 10 most recent jobs.

---

## Review

### Submit Review

```
POST /api/jobs/{job_id}/review
```

Submit a review decision for a title that needs human intervention.

**Request Body:**

```json
{
  "title_id": 1,
  "episode_code": "S01E01",
  "edition": null
}
```

### Approve All

```
POST /api/jobs/{job_id}/approve-all
```

Approve all pending review items and continue processing.

---

## Configuration

### Get Configuration

```
GET /api/config
```

Returns current application configuration. API keys are **redacted** (masked as `"***"`).

### Update Configuration

```
PUT /api/config
```

Update configuration fields. Accepts partial updates.

---

## Tool Validation

### Validate MakeMKV

```
POST /api/validate/makemkv
```

Validate MakeMKV installation and license.

### Validate FFmpeg

```
POST /api/validate/ffmpeg
```

Validate FFmpeg installation.

### Detect Tools

```
GET /api/detect-tools
```

Auto-detect MakeMKV and FFmpeg installations on the system. Searches platform-specific paths.

---

## Diagnostics

### Bug Report

```
GET /api/diagnostics/report?job_id=1
```

Generate a sanitized bug report with system info, recent errors, and optional job context. Returns a pre-filled GitHub issue URL.

---

## Simulation

!!! warning "Debug Mode Only"
    Simulation endpoints are only available when `DEBUG=true`.

### Insert Disc

```
POST /api/simulate/insert-disc
```

```json
{
  "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
  "content_type": "tv",
  "simulate_ripping": true,
  "rip_speed_multiplier": 1
}
```

### Remove Disc

```
POST /api/simulate/remove-disc?drive_id=E%3A
```

### Advance Job

```
POST /api/simulate/advance-job/{job_id}
```

Manually advance a job to its next state.

### Reset All Jobs

```
DELETE /api/simulate/reset-all-jobs
```

Delete all jobs and titles. Useful for test cleanup.

### Insert from Staging

```
POST /api/simulate/insert-disc-from-staging
```

Create a job from pre-existing files in the staging directory.
