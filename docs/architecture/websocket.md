# WebSocket Protocol

Engram uses WebSocket for real-time communication between the backend and the frontend dashboard. All connected clients receive live updates about job progress, state transitions, drive events, and more.

## Connection

**Endpoint**: `ws://localhost:8000/ws`

The frontend connects on page load and automatically reconnects with exponential backoff on disconnection. On reconnect, the frontend reloads the full job list via REST to resync state.

The `ConnectionManager` singleton manages all active connections. It maintains a list of connected WebSocket clients protected by an `asyncio.Lock` and handles graceful cleanup of disconnected clients during broadcast.

---

## Message Format

All WebSocket messages are JSON objects. Server-to-client messages follow this general structure:

```json
{
  "type": "<message_type>",
  "<field>": "<value>",
  ...
}
```

!!! note "Flat structure"
    Messages use a flat key-value structure (not nested under a `data` key). The `type` field identifies the message kind, and remaining fields carry the payload.

---

## Server to Client Messages

### `job_update`

Sent on any job state change, progress update, or metadata change. Only includes optional fields when their values are not `null`, so the frontend can safely merge without overwriting existing values.

| Field | Type | Always Present | Description |
|-------|------|:-:|-------------|
| `type` | `string` | Yes | `"job_update"` |
| `job_id` | `int` | Yes | Job identifier |
| `state` | `string` | Yes | Current `JobState` value (e.g., `"ripping"`, `"completed"`) |
| `progress_percent` | `float` | No | Overall progress (0-100) |
| `current_speed` | `string` | No | Ripping speed (e.g., `"2.3x"`) |
| `eta_seconds` | `int` | No | Estimated time remaining in seconds |
| `current_title` | `int` | No | Index of title currently being processed |
| `total_titles` | `int` | No | Total number of titles on disc |
| `error_message` | `string` | No | Error description (when state is `"failed"`) |
| `content_type` | `string` | No | `"tv"`, `"movie"`, or `"unknown"` |
| `detected_title` | `string` | No | Detected media name (e.g., `"The Office"`) |
| `detected_season` | `int` | No | Detected season number |
| `review_reason` | `string` | No | Human-readable reason for review |

### `drive_event`

Sent when a physical disc is inserted or removed.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | `"drive_event"` |
| `drive_id` | `string` | Drive path (e.g., `"E:"` or `"/dev/sr0"`) |
| `event` | `string` | `"inserted"` or `"removed"` |
| `volume_label` | `string` | Disc volume label (may be empty) |

### `titles_discovered`

Sent after disc scanning completes, carrying all discovered titles.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | `"titles_discovered"` |
| `job_id` | `int` | Job identifier |
| `titles` | `array` | List of `DiscTitle` objects |
| `content_type` | `string` | `"tv"`, `"movie"`, or `"unknown"` |
| `detected_title` | `string?` | Detected media name |
| `detected_season` | `int?` | Detected season number |

### `title_update`

Sent on any title (track) state change, progress update, or match result. Only includes optional fields when their values are meaningful.

| Field | Type | Always Present | Description |
|-------|------|:-:|-------------|
| `type` | `string` | Yes | `"title_update"` |
| `job_id` | `int` | Yes | Parent job identifier |
| `title_id` | `int` | Yes | Title identifier |
| `state` | `string` | Yes | Current `TitleState` value |
| `matched_episode` | `string` | No | Episode code (e.g., `"S01E03"`) |
| `match_confidence` | `float` | No | Match confidence score (0.0-1.0) |
| `match_stage` | `string` | No | Current matching stage description |
| `match_progress` | `float` | No | Matching or ripping progress (0-100) |
| `duration_seconds` | `int` | No | Title duration |
| `file_size_bytes` | `int` | No | File size |
| `expected_size_bytes` | `int` | No | Expected final size during ripping |
| `actual_size_bytes` | `int` | No | Current size during ripping |
| `matches_found` | `int` | No | Number of candidate matches found |
| `matches_rejected` | `int` | No | Number of candidates rejected |
| `match_details` | `string` | No | JSON string with score breakdown |
| `organized_from` | `string` | No | Source filename |
| `organized_to` | `string` | No | Destination path in library |
| `output_filename` | `string` | No | Output filename |
| `is_extra` | `bool` | No | Whether title was organized as extra content |
| `error` | `string` | No | Error message on failure |

### `subtitle_event`

Sent during subtitle download progress.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | `"subtitle_event"` |
| `job_id` | `int` | Job identifier |
| `status` | `string` | `"downloading"`, `"completed"`, or `"failed"` |
| `downloaded` | `int` | Number of subtitles downloaded so far |
| `total` | `int` | Total number of subtitles to download |
| `failed_count` | `int` | Number of subtitle downloads that failed |

---

## Client to Server Messages

The WebSocket connection is **server-push only**. There are no client-to-server messages. All user actions (review decisions, job cancellation, configuration changes) go through the REST API.

---

## Architecture Layers

WebSocket broadcasting flows through two layers:

```
EventBroadcaster (domain-specific methods)
        |
        v
ConnectionManager (transport-level broadcasting)
        |
        v
WebSocket clients (frontend)
```

The **EventBroadcaster** provides semantic methods like `broadcast_title_matched()` and `broadcast_subtitle_download_progress()`. These translate domain concepts into the correct `ConnectionManager` calls with properly named parameters.

The **ConnectionManager** handles the actual WebSocket transport: connection tracking, JSON serialization, broadcasting to all clients, and cleanup of disconnected clients.

---

## Contract Validation

Parameter names must match exactly across all layers. Mismatches between `EventBroadcaster` method calls and `ConnectionManager` method signatures cause `TypeError` exceptions at runtime.

### Known Validated Contracts

These contracts have been verified by integration tests:

| EventBroadcaster Method | ConnectionManager Method | Key Parameters |
|------------------------|-------------------------|----------------|
| `broadcast_job_failed(job_id, error_message)` | `broadcast_job_update(job_id, state, error=...)` | Uses `error=`, not `error_message=` |
| `broadcast_subtitle_download_progress(...)` | `broadcast_subtitle_event(job_id, status, downloaded, total, failed_count)` | No `error_msg` parameter |
| `broadcast_title_failed(title, error)` | `broadcast_title_update(job_id, title_id, state, error=...)` | Uses `error=` in transport layer |

!!! warning "Parameter name mismatches"
    Integration tests have caught two production bugs caused by parameter name mismatches between the `EventBroadcaster` and `ConnectionManager` layers. Always verify that parameter names used in keyword arguments match the receiving method's signature exactly.

### Testing

Integration tests validate WebSocket parameter contracts end-to-end. The test suite sends messages through the full `EventBroadcaster -> ConnectionManager -> WebSocket` pipeline and verifies the JSON output matches expected schemas.

All state values in WebSocket messages must use the string values from the `JobState` or `TitleState` enums (e.g., `"ripping"`, `"matched"`), never the enum member names.
