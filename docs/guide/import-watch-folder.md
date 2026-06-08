# Import Watch Folder

The Import Watch Folder lets Engram ingest **pre-ripped MKV files** without a physical disc. Point
it at a directory, drop files in (by hand, or automatically from a tool like
[AutomaticRippingMachine](https://github.com/automatic-ripping-machine/automatic-ripping-machine)),
and Engram runs them through the same identify → match → organize pipeline it uses for discs.

It's the right tool for:

- Importing an existing library of pre-ripped files.
- Machines without an optical drive (the primary workflow on macOS).
- Chaining Engram after an external ripper that writes finished MKVs to a folder.

## Enable it

In **Settings**, set:

| Setting | What it does |
|---------|--------------|
| `import_watch_path` | The folder Engram watches for incoming MKVs. |
| `import_destination_mode` | Where matched files end up. `library` (default) files them into your configured Movies/TV libraries, like a disc rip. `in_place` organizes them into a `Movies/` and `TV/` structure **inside the watch folder itself** — useful when the watch folder *is* your library. |

Saving reloads the watcher immediately (no restart needed). Engram then polls the folder about
every couple of seconds.

## Folder layouts

What Engram can tell from your folder structure determines how well it matches. The more it knows
up front (show **and** season), the better and faster the matching.

```text
import_watch_path/
├── The Expanse/              ← 1. Show → Season subfolders  (recommended)
│   ├── Season 01/
│   │   ├── episode.mkv
│   │   └── episode.mkv
│   └── Season 02/
│       └── ...
├── THE_OFFICE_S1D1/          ← 3. Per-disc / loose subfolder (ARM-style)
│   ├── title_t00.mkv
│   └── title_t01.mkv
└── loose_episode.mkv         ← 4. Flat: loose files at the root
```

1. **Show → Season subfolders** — `Show Name/Season 01/*.mkv`. **Recommended.** Both the show and
   the season are read straight from the folder names, which gives the most accurate matching.
2. **Watch folder is the show** — point `import_watch_path` directly at a single show's folder that
   contains `Season NN` subfolders (`import_watch_path/Season 01/*.mkv`). The show name comes from
   the watch folder itself, the season from the subfolder.
3. **Per-disc / loose subfolder** — any subfolder of MKVs that isn't a season folder (e.g. an ARM
   per-disc dump like `THE_OFFICE_S1D1/`). With no season hint, Engram identifies the show and
   episodes from the content and matches **across all seasons** — this works but is slower.
4. **Flat** — loose `*.mkv` files directly in the watch root. Same as above: matched across all
   seasons.

!!! note "Season folder spelling and mixed roots"
    Season folders may be written `Season 1` or `Season 01` (both parse). If the watch **root**
    contains *both* loose MKV files and structured subfolders, the subfolders win and the loose
    top-level files are **left un-imported** — move them into a season (or disc) folder to import
    them.

## How detection works

Engram doesn't grab files the instant they appear — that would catch them mid-copy. Instead it
waits for a folder to go **stable**: the MKV count and total size must be unchanged across two
consecutive polls before an import fires. In practice that's a few seconds after a copy finishes,
which is what lets you drop in large files (or have ARM write them) without Engram starting too
early.

Each stable folder becomes one job. A multi-season show in layout 1 produces **one job per season
folder**.

## The import lifecycle

Because the files already exist, imports **skip the ripping phase** entirely. On the dashboard you'll
see a job move through:

1. **Identifying** — Engram probes each MKV's duration, then classifies the content (TMDB lookup,
   using the show/season hints from the folder layout when available) and creates a title per file.
2. **Matching** *(TV only)* — each title is transcribed and matched to an episode. Titles beyond
   your `max_concurrent_matches` limit wait in **QUEUED** until a slot frees up (see
   [Performance & Hardware](performance.md#concurrency-tuning)).
3. **Organizing** — matched files are moved into your library (or organized in place, per
   `import_destination_mode`), with subtitles placed alongside. Movies skip matching and go
   straight here.
4. **Completed** — the job lands in [history](history.md). Low-confidence matches surface in the
   [review queue](review-queue.md) for your confirmation rather than being filed automatically.

## Your source files are safe

For watch-folder imports, **Engram never deletes your source folder.** Unlike a disc rip — whose
staging directory is a throwaway temp folder — an import's source is your own original files. The
staging-cleanup step explicitly skips import jobs, so even with `staging_cleanup_policy` set to
delete on success, your watch folder is left intact. Imported files leave the folder only by being
**moved into the library** on success (in `library` mode); anything Engram didn't import — skipped
season folders, strays — is never touched.

## Multi-season and bulk imports

Pointing the watch folder at a whole library works well:

- Each season (or disc) folder becomes its own independent job.
- All of those jobs share the global matching limit, so matching proceeds about
  `max_concurrent_matches` titles at a time rather than all at once. Raise that setting (within your
  hardware) before a big import — see [Performance & Hardware](performance.md#bulk-imports-and-large-libraries).

## Import vs physical disc

| Aspect | Physical disc | Import watch folder |
|--------|---------------|---------------------|
| Ripping phase | Yes (MakeMKV extracts from the disc) | No — files already exist |
| Source location | Temporary `job_*` folder under staging | Your watch folder (`import_watch_path`) |
| Cleanup on success | Temp folder deleted | **Never deleted** — it's your source |
| Season detection | From the disc volume label | From the folder layout (best) or content |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Nothing imports | Wrong path, files still copying, or an unsupported layout | Confirm `import_watch_path`; wait for the copy to finish (stability gate); check the layouts above |
| A season folder was skipped | Loose files at the root alongside structured subfolders | Move the loose files into a season/disc folder |
| Everything goes to review with low confidence | No season hint, or a generic label that didn't resolve a show | Use the **Show → Season subfolders** layout so the show and season are explicit |
| Matching is slow | Flat / no-season layout searches every season | Organize into season subfolders; see [Performance & Hardware](performance.md) |
