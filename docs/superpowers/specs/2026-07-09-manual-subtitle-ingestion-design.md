# Manual Subtitle Ingestion — Design

**Date**: 2026-07-09
**Status**: Approved for planning

## Problem

Engram matches ripped disc titles to episodes by comparing an ASR transcript of the
ripped audio against reference subtitle text. Reference subtitles come from three
automated sources — OpenSubtitles.com API, Addic7ed, TVsubtitles (`backend/app/matcher/subtitle_provider.py`,
`provider_scheduler.py`) — fanned out via `CompositeSubtitleProvider`. For some
shows/episodes none of the three have coverage, and the episode's row in the season
roster shows `has_reference: false` with a "no reference subtitle" warning in
`ReviewQueue.tsx` (around line 604). Today there is no way to give Engram a reference
subtitle the user has found manually (e.g. on a fansub site); those episodes can never
be auto-matched.

## Key existing behavior this design relies on

`LocalSubtitleProvider` (`subtitle_provider.py:73-113`) scans
`{cache_dir}/data/{corpus_dir_name(tmdb_id, show_name)}/*.srt` and is checked **first**
by `CompositeSubtitleProvider` (`subtitle_provider.py:458-496`) — if it finds enough
cached files it skips the network providers entirely (`subtitle_provider.py:471-484`).
This means placing a correctly named `.srt` file in that directory makes it available
to matching with **no changes to the matcher/scheduler**. This design is almost
entirely about getting a user-supplied file into that directory safely.

## Scope

In scope:
- Bulk upload of a folder of `.srt` files for a show/season directly from
  `ReviewQueue.tsx`, at the season-roster level (not per single episode — the user
  pointed out one-by-one upload would be painful for a whole season).
- Server-side filename parsing (reusing the existing episode-code parser) with a
  preview/confirmation step for files that can't be auto-parsed.
- Writing accepted files into the same cache location `LocalSubtitleProvider` already
  scans.

Out of scope (explicitly not building):
- Any change to the matching engine / `CompositeSubtitleProvider` — the existing
  cache-priority behavior already does the right thing once a file is present.
- Automatic re-match triggering after import — the user uses the existing advisory
  re-match action (`JobManager.rematch_single_title`) per track, same as today.
- Non-`.srt` formats (`.vtt`, `.ass`, etc.) — `.srt` only, matching the existing
  `is_valid_srt_file` validation and cache format.
- A library-wide/global upload screen decoupled from a specific review session.
- True multipart `UploadFile` upload — see "Upload mechanism" below.

## Upload mechanism: client-side read, JSON body, not multipart

The backend has no existing multipart/`UploadFile` endpoint (the only file-write
endpoint, `fetch_cover` in `routes.py:3505-3583`, downloads from a URL, not a client
upload). Rather than add new multipart-handling plumbing, the browser reads each
selected file's text via `FileReader.readAsText()` and the frontend sends plain JSON
bodies — consistent with every other endpoint in `routes.py`. The `<input type="file"
multiple webkitdirectory>` element still gives the user a folder picker in the UI;
only the wire format differs from a "real" upload.

## API

Two new endpoints, both scoped under the job (so `job.tmdb_id` /
`job.tmdb_name`/`detected_title` resolve the cache directory server-side — the user
never supplies a path):

### `POST /api/jobs/{job_id}/subtitles/preview`

Request:
```json
{"files": [{"filename": "Show.Name.S01E05.srt", "content": "1\n00:00:01,000 --> ..."}]}
```

Response — one verdict per file, resolved via the existing `parse_season_episode`
parser and `is_valid_srt_file`:
```json
{
  "results": [
    {"filename": "...", "season": 1, "episode": 5, "status": "ready"},
    {"filename": "...", "season": 1, "episode": 2, "status": "already_covered"},
    {"filename": "...", "season": null, "episode": null, "status": "unparseable"},
    {"filename": "...", "season": 1, "episode": 9, "status": "invalid_content", "warning": "not a valid SRT"},
    {"filename": "...", "season": 1, "episode": 12, "status": "ready", "warning": "possible encoding issue"},
    {"filename": "...", "season": 1, "episode": 5, "status": "duplicate", "warning": "same episode as an earlier file in this batch"}
  ]
}
```

Limits: reject request if more than ~60 files or any file exceeds ~2MB (generous
for a season / for a single subtitle track; bounds payload abuse since this path
doesn't go through the existing 10MB cover-image guard). Season/episode bounds:
season `0-50`, episode `1-999` (season 0 covers specials) — values outside this range
are treated as `unparseable` rather than trusted. If two files in the same batch parse
to the same season/episode, the first is kept as `ready`/`already_covered`/etc. and
every subsequent one is marked `duplicate` and excluded from the default selection.

### `POST /api/jobs/{job_id}/subtitles/commit`

Request — only the subset of files the user confirmed in the UI, with any manual
season/episode corrections for previously-`unparseable` files:
```json
{"files": [{"filename": "...", "season": 1, "episode": 5, "content": "..."}]}
```

The commit handler **does not trust the preview result** — it independently
re-validates content (`is_valid_srt_file`), re-checks season/episode are small
positive ints, and re-checks whether a reference already exists at the destination
path (protects against a race where an automated download landed between preview and
commit, or a tampered client payload). Already-covered episodes are skipped, not
overwritten.

Response:
```json
{"imported": [{"season": 1, "episode": 5}], "skipped": [{"season": 1, "episode": 2, "reason": "already_covered"}], "errors": []}
```

On success, the destination file is written to exactly
`{cache_dir}/data/{corpus_dir_name(tmdb_id, show_name)}/{show_name} - S{season:02d}E{episode:02d}.srt`
— the same path and naming convention `LocalSubtitleProvider` already scans.

## UI

In `ReviewQueue.tsx`, at the season-roster level (where `has_reference: false`
episodes are already listed), add an "Upload Subtitles" action:

1. User picks a folder (or multiple files) of `.srt` files.
2. Frontend reads each file's text client-side and POSTs to `preview`.
3. A confirmation table renders: filename → detected episode → status. Rows flagged
   `unparseable` get an inline season/episode input before they can be included.
   Rows flagged `already_covered` are shown but excluded from the default selection
   (user can still opt to override — no destructive default).
4. "Import All" POSTs the confirmed set to `commit`; on response, the season roster
   refetches so `has_reference` flips to `true` for imported episodes and the warning
   clears.
5. No automatic re-match is triggered — the user uses the existing per-track advisory
   re-match action once ready.

## Error handling & security

- **No path traversal surface**: the uploaded filename is used only as a parsing hint
  and for display; the destination path is always synthesized server-side from
  `job.tmdb_id`/show name plus validated integer season/episode (season `0-50`,
  episode `1-999`; out-of-range values are rejected, not clamped).
- **Commit re-validates independently of preview** (see above) rather than trusting
  client-echoed verdicts.
- **Encoding**: `FileReader.readAsText()` defaults to UTF-8. Many real-world `.srt`
  files are Windows-1252/Latin-1. Rather than add an encoding-detection dependency,
  content containing replacement characters (U+FFFD) after decode gets a soft
  `"possible encoding issue"` warning in the preview but is still importable —
  best-effort, not a hard block.
- **Size/batch limits** as above, applied identically at preview and commit.

## Testing

- Unit tests for `preview`: correct season/episode detection via
  `parse_season_episode`, correct `already_covered`/`unparseable`/`invalid_content`
  classification.
- Unit tests for `commit`: writes land at the exact expected path/filename; rejects
  out-of-range or tampered season/episode; skip-on-conflict; encoding-warning
  detection.
- One integration test proving the full loop needs no matcher changes: commit a
  manual `.srt` for an episode with `has_reference: false`, then assert
  `LocalSubtitleProvider.get_subtitles()` returns it.
- Extend existing Playwright `ReviewQueue` specs with a case that imports via the new
  UI and asserts the "no reference subtitle" warning clears for that episode.
