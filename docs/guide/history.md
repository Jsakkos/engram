# Job History

The History page provides a complete record of every disc job Engram has processed. Unlike the dashboard, which only shows active jobs and recently completed ones, the history page shows all completed and failed jobs -- including those that have been cleared from the dashboard.

Access it by clicking **History** in the navigation tabs or by navigating to `/history`.

## Stats Dashboard

At the top of the page, six stat cards summarize your archival activity:

| Stat | Description | Color |
|------|-------------|-------|
| **Total Jobs** | Count of all completed and failed jobs | Cyan |
| **Completed** | Successfully archived jobs | Green |
| **Failed** | Jobs that ended in error | Red |
| **TV Shows** | Jobs classified as TV content | Amber |
| **Movies** | Jobs classified as movie content | Magenta |
| **Avg Time** | Average processing time across all jobs | Violet |

These stats are fetched from the `GET /api/jobs/stats` endpoint and reflect the entire job history, not just the current page.

## Common Errors

If any jobs have failed, a **Common Errors** panel appears below the stats. It lists recurring error messages with their frequency, sorted by count. This helps identify systemic issues such as:

- MakeMKV license problems
- Disc read errors
- Subtitle download failures
- File permission issues
- TMDB API failures

Each entry shows the error count (e.g., `x3`) alongside the error message text.

## Filtering

Two dropdown filters are available above the history table:

- **Content type** -- filter by All Types, TV, or Movie.
- **State** -- filter by All States, Completed, or Failed.

Changing either filter resets the page back to 1. Filters are applied server-side for efficiency.

## History Table

The main table lists jobs with the following columns:

| Column | Description | Visibility |
|--------|-------------|------------|
| **Title** | Detected title (with volume label shown below if different) | Always |
| **Type** | Content type badge: TV (amber) or Movie (magenta) | Hidden on small screens |
| **State** | Green checkmark for completed, red X for failed | Always |
| **Titles** | Number of tracks/titles on the disc | Hidden on small screens |
| **Source** | Classification source (e.g., "tmdb", "heuristic", "discdb") | Hidden on narrow screens |
| **Date** | Completion or creation date | Hidden on small screens |

Rows are clickable -- clicking a row opens the detail panel and updates the URL.

## Job Detail Panel

Clicking any row opens a slide-out panel from the right side of the screen. The panel can also be reached directly via deep link at `/history/:jobId`.

### Title and Status

The panel header shows:

- The detected title or volume label
- Content type badge (TV / Movie)
- State badge (Completed / Failed)
- Season number (for TV)
- Disc number (if part of a multi-disc set)
- Volume label and drive ID

### Error Details

For failed jobs, a red error panel displays the full error message in a scrollable monospace block. This includes the complete error text as stored in the database, which may contain stack trace information useful for debugging.

### Processing Timeline

A timeline section shows:

- **Created** -- when the job was first created (disc inserted or simulation triggered)
- **Completed / Failed** -- when the job reached its terminal state
- **Duration** -- total processing time calculated from the two timestamps

### Classification Details

This section reveals how Engram identified the disc content:

- **Source** -- which classifier made the determination (e.g., `tmdb`, `heuristic`, `discdb`, `ai`)
- **Confidence** -- a visual confidence bar with percentage, color-coded green (80%+), amber (50-79%), or red (below 50%)
- **TMDB** -- the matched TMDB title name and ID, if available
- **Ambiguous movie** -- flag shown if multiple main features were detected
- **Review reason** -- the specific reason review was triggered, if applicable

### TheDiscDB Metadata

The TheDiscDB section shows disc fingerprint data:

- **Content Hash** -- the MD5 hash of the disc's BDMV stream files, with a copy-to-clipboard button. The hash is truncated in the display but the full value is copied.
- **Title** -- the TheDiscDB title slug (if the disc was found in the database)
- **Disc** -- the specific disc slug within the title

If the disc was fingerprinted but not found in TheDiscDB, a note indicates this. If fingerprinting was not performed (e.g., the job failed before that stage), a different message explains the absence.

### Subtitle Information

When subtitle data is available, this section shows:

- **Status** -- the subtitle download status (completed, failed, etc.)
- **Downloaded** -- count of successfully downloaded subtitles out of total, with failed count shown in red if any failed

### Per-Track Breakdown

A list of all tracks/titles from the disc, each showing:

- **Track index** -- the title number on the disc
- **Duration** -- formatted as minutes:seconds
- **File size** -- in human-readable units (MB, GB)
- **Resolution** -- video resolution (e.g., 1080p)
- **State badge** -- color-coded by state (OK, FAIL, MATCHED, REVIEW, PENDING, RIPPING, MATCHING)
- **Match info** -- matched episode code, edition tag, extra flag, and match confidence percentage
- **Organized path** -- the final library destination path (for completed tracks)

If no tracks exist (e.g., the job failed before disc scanning), a message explains this.

### Paths

When available, the staging and library paths are displayed:

- **Staging** -- the temporary directory where ripped files were stored
- **Library** -- the final organized location in the media library

### Bug Report

At the bottom of the detail panel, a **Report bug for this job** link generates a diagnostic report specific to that job via the `GET /api/diagnostics/report?job_id=:id` endpoint. Clicking it opens a pre-filled GitHub issue with relevant job data for troubleshooting.

A general **Report Bug** button is also available in the page header for filing reports not tied to a specific job.

## Deep Linking

The history page supports direct URLs to specific jobs:

- `/history` -- shows the history table without any detail panel open
- `/history/:jobId` -- opens the history table with the specified job's detail panel pre-opened

When a detail panel is opened or closed, the URL updates via `history.replaceState` so the browser back button works as expected. Clicking the same row again closes the panel.

The detail panel can be dismissed by:

- Clicking the X button in the panel header
- Clicking outside the panel (on the backdrop)
- Pressing the Escape key

## Pagination

The history table uses server-side pagination with 20 jobs per page. Navigation buttons at the bottom of the table allow moving between pages:

- **Prev** -- go to the previous page (disabled on page 1)
- **Next** -- go to the next page (disabled when fewer than 20 results are returned)

The current page number is displayed between the navigation buttons.
