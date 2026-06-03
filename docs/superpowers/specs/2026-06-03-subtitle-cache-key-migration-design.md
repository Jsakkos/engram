# Subtitle cache key migration (name → tmdb_id)

_Date: 2026-06-03_

## Problem

PR #288 (`e11ad8f`, "key precomputed subtitle corpus by tmdb_id (cache v3)") changed
`corpus_dir_name()` so the on-disk SRT harvest cache moved from `data/<sanitized show
name>/` to `data/<tmdb_id>/`. The `subtitle_coverage` table was already keyed by
`tmdb_id`, so the build script's complete-on-disk resume fast path
(`build_subtitle_cache._harvest_show`) still finds a fresh "done" coverage record for a
season — but then calls `discover_season_srts(data/<tmdb_id>/, season)`, which finds
nothing because the SRTs are still under the legacy `data/<name>/`. It logs *"coverage
recorded but SRTs missing on disk; re-harvesting from scratch"* and re-downloads
everything.

Observed on the real cache (`~/.engram/cache/data/`): 308 dirs total — **291 legacy
name-keyed**, **17 fresh id-keyed**. The 17 are shows that were re-downloaded from
scratch after #288 because their legacy dirs were invisible (e.g. `Breaking Bad/` from
May 23 sitting next to `1396/` from Jun 3).

Nothing migrates the legacy dirs: `normalize_subtitle_cache.py` only canonicalizes
filenames *inside* a dir, never the dir name itself.

## Goal

A one-shot, idempotent migration that relocates legacy `data/<name>/` dirs to
`data/<tmdb_id>/` so the resume path finds them. Scoped to `data/` only — `precomputed/`
is `shutil.rmtree`'d and rebuilt every run, and the runtime re-downloads it from the
release.

## Design

New script `backend/scripts/migrate_subtitle_cache_keys.py`, mirroring the conventions of
its siblings (`build_`/`normalize_`/`audit_subtitle_cache.py`): argparse, loguru,
idempotent `sys.path` bootstrap, `--cache-dir` override defaulting to
`AppConfig.subtitles_cache_path`.

### Name → tmdb_id resolution (offline-first)

1. Build `{sanitize_filename(name): tmdb_id}` from `scripts/curated_shows.csv` (the list
   the cache was built from) — deterministic, no network, covers nearly all 291 dirs.
2. Misses fall back to `tmdb_client.fetch_show_id(name)` (persistent-cache first; only
   true misses hit the network; returns `None` with no API key → treated as unresolved).
3. Purely-numeric dirs (`1396/`) are already migrated → skipped silently. **Exception:** a
   numeric dir that is *also* a show name in the CSV (`24`) is ambiguous — left in place,
   reported, with a `--treat-as-name 24` escape hatch to force it through name resolution.

### Merge: union, keep larger (chosen)

For a legacy dir resolving to tmdb_id `T`, target `data/T/`:

- Target absent → `Path.rename` (atomic on same volume).
- Target present (the 17 collisions) → move each `*.srt`; on exact-name collision keep the
  larger file (`st_size`), drop the smaller; remove the emptied legacy dir afterward. Same
  episode shares the canonical `{name} - SxxExx.srt` filename in both dirs, so exact-name
  collision catches it. Cross-naming duplicates are out of scope — `normalize_subtitle_cache.py`
  handles those and is run as a follow-up pass.

### Safety

- Defaults to **dry-run**; `--apply` required to mutate (higher-stakes + one-shot vs. its
  siblings, which default to apply).
- Idempotent: a second run finds no legacy dirs → no-op.
- Final summary tallies migrated / merged / skipped / **ambiguous** / **unresolved**, with
  the ambiguous and unresolved lists printed explicitly so stragglers are actionable.
- Coverage records need no migration — already tmdb-keyed.

## Testing (TDD)

`tests/unit/test_migrate_subtitle_cache_keys.py`, against a tmp cache dir + a fake CSV
(never the real cache, per the real-cache verification hazard):

- name dir → renamed to id dir; CSV hit does **not** call `fetch_show_id`.
- collision → union, larger SRT kept, legacy dir removed.
- already-id dir → skipped.
- ambiguous numeric-name (`24` present in fake CSV as a show) → reported, left in place;
  `--treat-as-name 24` migrates it.
- unresolved name (not in CSV, `fetch_show_id` → None) → left + reported.
- `--dry-run` (default) → no filesystem changes.
- second run → no-op (idempotent).

## Rollout

1. Implement test-first; `uv run pytest tests/unit/test_migrate_subtitle_cache_keys.py`.
2. Dry-run against the real cache; review the plan (renames / merges / skips / ambiguous /
   unresolved).
3. `--apply`.
4. `normalize_subtitle_cache.py` follow-up pass.
5. Verify a migrated show ships "from disk" on the next build run.
