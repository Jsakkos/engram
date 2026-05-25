# Verify Episode Labels — Design

**Date:** 2026-05-23
**Status:** Approved
**Branch:** `feat/verify-episode-labels`

## Problem

A user has TV library folders where episodes are already ripped and named
(`TV/Show/Season 01/Show - S01E03.mkv`), but at least one season got mixed up so
several episodes are out of order (mislabeled). We want a standalone script that
points at an existing library folder and uses Engram's episode matcher to verify
whether each file's *claimed* episode label matches what the audio actually is,
flagging mismatches and optionally renaming them.

## Goals

- Point at a season folder **or** a show folder (auto-detected) and verify labels.
- For each `.mkv`: compare the episode parsed from the filename (the *claim*)
  against the matcher's predicted episode + calibrated confidence.
- Surface mislabeled / out-of-order files clearly.
- Optionally rename mismatched files to the correct code, **safely** (handles the
  cyclic-swap collision inherent in "out of order"), with an undo log.
- Default to a **dry run** that changes nothing.

## Non-Goals

- No changes to the running app, DB, or job pipeline.
- No new web/API surface — this is a CLI utility.
- Not a general media renamer; scoped to verifying against Engram's matcher.

## Approach

Reuse the production matcher path rather than reinventing it:

- **Matching:** drive `EpisodeCurator.match_single_file()`
  (`backend/app/core/curator.py:172`) — the same call the real pipeline uses. It
  resolves the canonical show name via TMDB, initializes `EpisodeMatcher`, runs
  `identify_episode()` in a thread, and returns a `MatchResult` with
  `episode_code`, `confidence`, and `match_details` (including `runner_ups`).
- **Reference setup:** before matching a season, call
  `testing_service.download_subtitles(show, season)`
  (`backend/app/matcher/testing_service.py:242`). This handles the precomputed
  vector cache → OpenSubtitles → Addic7ed fallback exactly like production. The
  matcher itself does **not** download subtitles; it returns `None` if references
  are absent.
- **Config:** the script imports the app package, so `get_config_sync()` supplies
  the TMDB token, cache path, and OpenSubtitles creds straight from `engram.db`.
  No separate API setup.

Rejected alternative: calling `EpisodeMatcher.identify_episode()` raw. It would
force duplicating curator's canonical-name resolution and cache init for no gain.

## Components

The script is `backend/scripts/verify_episode_labels.py`, structured so the pure
logic is unit-testable without the matcher or network. App imports
(`curator`, `testing_service`, `config_service`) are **lazy** (inside functions)
so importing the module for tests is cheap and side-effect-free.

### Pure functions (unit-tested)

- `parse_claim(filename) -> (season, episode) | None` — `SxxEyy`, `NxNN`,
  `Season N Episode M` patterns (mirrors `_parse_episode_from_filename`).
- `detect_scope(path) -> ("season"|"show", seasons)` —
  - `.mkv` files directly in `path` → season folder. Season from `Season NN`
    dir name; fallback to the dominant `SxxEyy` season across filenames.
  - `Season NN` subfolders present → show folder; iterate each season.
- `classify(claim, predicted, confidence, threshold) -> Status` —
  `OK | MISMATCH | LOW_CONF | NO_MATCH | UNPARSEABLE`.
- `build_rename_plan(results, threshold) -> list[RenameStep]` — for `MISMATCH`
  files at confidence ≥ threshold, compute target filenames (swap the episode
  code, preserve the rest + extension), detect cycles/collisions, and emit a
  **two-phase** plan (each source → unique temp name → final name) so cyclic
  swaps (E03↔E05) never clobber. Includes same-stem sidecars (`.srt`, `.nfo`).

### I/O / orchestration

- `argparse` CLI (see flags below).
- Per-season loop: resolve show name (folder name or `--show`) → ensure subtitles
  → match each file → classify → render.
- Console output via `rich` (`Table`, colored status); falls back to plain text
  if `rich` import fails.
- CSV written alongside the target with full per-file detail (path, claimed,
  matched, confidence, status, runner-ups).
- `--apply`: execute the rename plan, writing
  `engram_label_undo_<timestamp>.json` (final → original). `--undo <log>` reverts.

## CLI

```
verify_episode_labels.py PATH [options]

  PATH                 season folder or show folder (auto-detected)
  --show NAME          override inferred show name
  --season N           override inferred season (season-folder mode)
  --apply              perform renames (default: dry run, no changes)
  --min-confidence X   confidence gate for auto-rename (default 0.7)
  --num-points N       denser audio scan for accuracy (matcher default 10)
  --csv PATH           CSV output path (default: alongside target)
  --undo LOG           revert a previous --apply run from its undo log
```

Run from `backend/`:
`uv run python scripts/verify_episode_labels.py "C:\Media\TV\Show\Season 03"`

## Status classification

| Status        | Condition                                              |
|---------------|--------------------------------------------------------|
| `OK`          | predicted == claimed, confidence ≥ threshold           |
| `MISMATCH`    | predicted ≠ claimed, confidence ≥ threshold (rename!)  |
| `LOW_CONF`    | confidence < threshold (can't trust either way)        |
| `NO_MATCH`    | matcher returned `None` (no refs / no transcript hit)  |
| `UNPARSEABLE` | no episode code in filename (still gets a suggestion)  |

## Rename safety

"Out of order" means swaps, so renaming a file into a name that already exists is
expected. The plan:

1. Build the full season target map; only `MISMATCH` ≥ threshold participate.
2. Detect collisions/cycles.
3. Two-phase rename: every participant → unique temp name first, then temp →
   final. Cyclic swaps resolve cleanly.
4. Same-stem sidecars (`.srt`, `.nfo`) move with their `.mkv`.
5. Write an undo log; `--undo` reverses it.
6. Refuse to overwrite a non-participant file (abort with a clear message).

## Error handling

- Subtitle setup failure for a season → mark every file `NO_MATCH`, print a clear
  reason, continue to the next season (don't abort the whole run).
- TMDB unreachable / show not found → caught, reported per season.
- Missing/locked `.mkv` → reported per file, never crashes the run.
- `--apply` aborts before touching anything if the plan has an unsafe collision
  with a non-participant.

## Testing

- **Unit (`backend/tests/unit/test_verify_episode_labels.py`):** `parse_claim`,
  `detect_scope` (via tmp dirs), `classify`, and `build_rename_plan` — especially
  the cyclic-swap two-phase ordering and sidecar inclusion. No matcher/network.
- **Integration:** a real dry-run on the user's known-mixed-up season is the
  acceptance check (ASR + real references; not automated).

## Performance

ASR is CPU/GPU-bound: a few seconds per file, so a ~20-episode season takes a
couple of minutes on first run (subtitle fetch + transcription). Subsequent runs
reuse the cache.
