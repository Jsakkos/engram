# Review Queue

The Review Queue is Engram's Human-in-the-Loop interface. It appears when the system cannot confidently resolve disc content on its own and needs your input to proceed.

## When Review Is Needed

A job enters the `REVIEW_NEEDED` state when any of the following occur:

- **Low-confidence matches** -- the audio fingerprinting or subtitle matching engine returned results below the auto-match threshold.
- **Ambiguous content type** -- the disc analysis could not confidently determine whether the disc is TV or movie content.
- **Multiple feature-length titles** -- a movie disc has more than one title that could be the main feature (e.g., Theatrical, Extended, and Director's Cut versions on the same disc).
- **Unreadable disc label** -- the volume label is generic or unreadable, triggering a name prompt modal on the dashboard before the review queue.
- **Catalog-number label** -- the volume label looks like a publisher catalog code (e.g., `BBCDVD1550`, `FHED3456`) and TMDB/DiscDB lookups failed, triggering a name prompt.
- **File conflicts** -- an output file already exists in the library at the expected path.
- **TMDB classification override** -- the TMDB classifier disagrees with the heuristic classifier and the confidence gap is below the override threshold.

When a job needs review, a yellow **REVIEW** button appears on its card in the dashboard. Clicking it navigates to `/review/:jobId`.

## TV Review

The TV review interface is designed for resolving episode assignments on TV disc rips. It organizes titles into three sections:

### Auto-Matched Titles

Titles with match confidence above the auto-match threshold appear in the **AUTO-MATCHED** section with a green indicator. These are pre-filled with the best match but can still be adjusted.

### Needs Review Titles

Titles below the confidence threshold or without any match appear in the **NEEDS REVIEW** section with a yellow indicator. These require manual assignment.

### Processed Titles

Titles that have already been completed or failed appear in a dimmed **PROCESSED** section for reference.

### Episode Assignment

Each title row in the TV review shows:

- **Title index** -- the track number on the disc.
- **Filename** -- the output filename from the rip.
- **Duration and file size** -- for identifying the correct episode.
- **Confidence badge** -- color-coded by confidence level (green for high, yellow for medium, red for low).
- **Review reason tags** -- small badges explaining why review was triggered (e.g., "LOW CONFIDENCE", "FILE EXISTS").
- **Season selector** -- a compact numeric input (S01–S20) that controls which season's episodes appear in the dropdown. Defaults to the auto-detected season but can be changed per title.
- **Episode selector dropdown** -- lists the best match first (with confidence percentage), followed by alternative candidates, then a divider, and finally the full episode list for the selected season (e.g., S02E01 through S02E26).

You can expand each title row to see a **Competing Matches** table that shows:

| Column | Description |
|--------|-------------|
| Rank | 1st, 2nd, 3rd, etc. |
| Episode | Episode code (e.g., S01E03) |
| Score | Match confidence percentage |
| Votes | Number of matching segments |
| Assessment | BEST, POSSIBLE, or UNLIKELY |

Additional match statistics are displayed when available: total vote count, file coverage percentage, and score gap between the top two candidates.

### Title Actions

For each title, you can choose one of four actions:

- **Episode** (default) -- assign a specific episode code from the dropdown.
- **Extra** -- mark the title as extra/bonus content. It will be organized into the extras folder.
- **Discard** -- skip this title entirely; it will not be organized.
- **Skip** -- leave the title unresolved for now.

### Submitting TV Review

The header provides three action buttons:

- **START RIP** -- begins ripping without resolving any matches (useful if the disc has not been ripped yet).
- **SAVE ASSIGNMENTS** -- saves your episode selections without triggering organization. The count reflects how many titles you have assigned.
- **PROCESS MATCHED** -- saves all assignments and immediately triggers the organization pipeline for resolved titles. If any titles remain unresolved, the page refreshes to show the remaining items.

After all titles are resolved and processed, you are redirected back to the dashboard.

## Movie Review

When a movie disc has multiple feature-length titles (an "ambiguous movie"), the review interface switches to a movie-specific layout.

The header displays **SELECT MOVIE VERSION** with the detected title or volume label. A banner explains: "MULTIPLE FEATURE-LENGTH TITLES DETECTED. SELECT THE CORRECT VERSION TO KEEP."

Each title is displayed as a card showing:

- **Title index** -- track number on the disc.
- **Filename** -- the ripped file name.
- **Duration** -- runtime of the title.
- **File size** -- size of the MKV file.
- **Resolution** -- video resolution (e.g., 1080p, 4K).
- **Chapter count** -- number of chapters in the title.
- **Edition input** -- a text field with autocomplete suggestions for common editions:
    - Theatrical
    - Extended
    - Director's Cut
    - Unrated
    - IMAX

For each title, two buttons are available:

- **SELECT** -- keep this title and organize it to the library. The edition tag (if entered) is included in the output path.
- **DISCARD** -- skip this title.

Selecting or discarding a title immediately submits the review and returns you to the dashboard, where the job resumes its pipeline.

## How Review Resumes the Pipeline

When you submit a review (TV or movie), the following happens:

1. Your selections are posted to `POST /api/jobs/:jobId/review` for each title.
2. For TV, the matched episode codes are stored on each `DiscTitle` record. For movies, the selected edition is saved.
3. The job state machine transitions from `REVIEW_NEEDED` back into the active pipeline -- either to `RIPPING` (if the disc has not been ripped yet) or to `ORGANIZING` (if ripping is already complete).
4. The Organizer picks up the resolved titles and moves them to the library using the appropriate naming convention:
    - **Movies**: `Movies/Name (Year)/Name (Year) - Edition.mkv`
    - **TV**: `TV/Show/Season XX/Show - SXXEXX.mkv`
5. The dashboard card updates in real time as organization proceeds, and the job moves to `COMPLETED` when finished.

## Error Handling

If the review submission fails (network error, backend unavailable), an error banner appears at the top of the review page with the specific error message. You can retry without losing your selections -- all state is maintained locally until a successful submission.

If subtitle downloads failed for the job, a warning banner is displayed:
"SUBTITLE DOWNLOAD FAILED. MANUAL FETCH MAY BE REQUIRED."
