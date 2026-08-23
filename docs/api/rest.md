# REST API Endpoints

All endpoints are under the `/api` prefix.

## Jobs

### List Active Jobs

```
GET /api/jobs
```

Returns active disc jobs (excludes cleared/archived jobs). The 10 most recent *finished*
(`completed`/`failed`) jobs are included; every other state is returned in full, so a job
still waiting on you (for example one parked in `review_needed`) never ages out of the
dashboard behind newer discs.

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

Returns completed/failed jobs with pagination and filtering. Jobs appear in history
automatically when they reach a terminal state, with no manual clearing required.

An explicit `state` is honoured for *any* job state, and `include_all_states=true` drops
the state filter entirely. Either makes history a complete record of every job, including
one still in progress or parked in review.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Results per page (max 100) |
| `content_type` | string | — | Filter: `tv` or `movie` |
| `state` | string | — | Filter by any `JobState`, e.g. `completed`, `failed`, `review_needed`. An unknown value is rejected with 422 |
| `include_all_states` | bool | `false` | Return every state instead of the completed/failed default. Ignored when `state` is given |

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

### `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`

Run the LLM episode matcher for a single title and persist the suggestion to `match_details.llm_suggestion`. Requires `ai_episode_matching_enabled` and the job to have a known `detected_title` + `detected_season`.

**Response (200):**

```json
{
  "suggestion": {
    "episode": 7,
    "confidence": 0.93,
    "reasoning": "Mentions of named character and unique plot beat.",
    "runner_up": {"episode": 6, "confidence": 0.12},
    "model": "gemini-2.5-flash-lite"
  },
  "reason": null
}
```

When no suggestion is available (feature disabled, no transcript, no synopses, AI returned zero confidence, or any internal error), `suggestion` is `null` and `reason` describes why (`"no_suggestion"`, `"cached"`, or `"internal_error"`). On a cached hit (the suggestion was already computed for this title), the existing suggestion is returned with `reason: "cached"` to avoid duplicate Whisper transcription.

### `GET /api/jobs/{job_id}/titles/{title_id}/media`

Streams the ripped file for one title, byte for byte, with HTTP range support
(`206`/`416` come from Starlette's `FileResponse`). Served as
`video/x-matroska`.

**The file is never transcoded, and this is deliberate.** MakeMKV remuxes
losslessly, so a rip carries disc-native codecs that no browser decodes, and
the endpoint exists to hand the file to a native player (VLC, MPV, IINA) rather
than to feed a `<video>` element. Engram has no ffmpeg dependency for playback
and should not grow one here. See
`docs/superpowers/specs/2026-08-22-review-external-player-design.md` for why
in-browser playback was ruled out.

The path is read from the database (`DiscTitle.output_filename`, falling back to
`organized_to`) and never accepted from the client. It must resolve inside one
of the configured roots: staging, the TV library, the movie library, the import
watch folder, or the job's own `staging_path` (which is where manual imports
live).

**Status codes:**

| Code | Meaning |
|------|---------|
| `200` / `206` | The file, whole or ranged. |
| `403` | Origin gate refused the peer, or the path resolved outside every configured root. |
| `404` | Title not found, the track has not been ripped, or the recorded file is gone from disk. |

### `GET /api/jobs/{job_id}/titles/{title_id}/playlist.m3u`

Returns a one-entry `.m3u` naming the media URL above, as
`audio/x-mpegurl` with `Content-Disposition: attachment`, so the browser hands
it to whatever owns the extension. Same status codes as the media endpoint; the
path is resolved *before* the body is built, so a broken track fails as HTTP
rather than inside the user's player.

The URL inside the playlist is derived from the request's `Host` header, so it
carries the address the dashboard was actually reached on. Deriving it from
`settings.host` would emit `localhost` for every remote user: correct on the
backend machine, broken everywhere else, and invisible to local testing.

Disc-derived text in the playlist label passes through
`sanitize_playlist_field`, because `.m3u` is line-oriented and a crafted volume
label containing a line break could otherwise forge a second entry.

Both endpoints sit behind `require_localhost_or_lan`: loopback always, LAN peers
only when `allow_lan_access` is set.

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
