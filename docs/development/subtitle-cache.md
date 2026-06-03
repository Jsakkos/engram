# Building the subtitle-vector cache

Engram ships with a precomputed cache of hashed TF-IDF vectors covering the most-voted-on TV shows from TMDB. When the backend starts for the first time, it downloads this cache from a GitHub Release and uses it for matching — no per-disc subtitle scraping required for cached shows.

This page documents the workflow that **builds** that cache, who can run it, and what to expect when you do.

## When to run it

Rarely. The workflow is manual-only (`workflow_dispatch`) because:

- Harvesting subtitles for ~300 shows hits rate-limited third-party APIs and scrapers; a full build takes hours.
- The output (`engram-subtitle-cache.tar.gz`) is largely static — show vocabulary doesn't change once an episode airs.

Run it when:

- A new release of Engram bumps `CACHE_FORMAT_VERSION` in [`backend/app/matcher/vectorizer_config.py`](https://github.com/Jsakkos/engram/blob/main/backend/app/matcher/vectorizer_config.py).
- You want to refresh the cache with newer episodes from currently-listed shows.
- You want to add shows beyond the default top 300.

## Required secrets

Set all four at **repository scope** in `Settings → Secrets and variables → Actions → New repository secret`. Repository-scope (not environment-scope) is required so a `workflow_dispatch` from any branch can read them.

| Secret | Required? | Source |
|---|---|---|
| `TMDB_API_KEY` | **Yes** — workflow aborts immediately if unset | [TMDB v4 Read Access Token](https://www.themoviedb.org/settings/api) (a long JWT starting with `eyJ…`, **not** the shorter "API Key") |
| `OPENSUBTITLES_API_KEY` | Yes for fast/reliable builds | Create a consumer key at [opensubtitles.com/en/consumers](https://www.opensubtitles.com/en/consumers) |
| `OPENSUBTITLES_USERNAME` | Yes if API key set | Your OpenSubtitles account username |
| `OPENSUBTITLES_PASSWORD` | Yes if API key set | Your OpenSubtitles account password (a VIP tier is strongly recommended — daily download quota is the main bottleneck) |

Without the OS credentials the workflow falls back to legacy HTML scrapers (Addic7ed, opensubtitles.org). Expect a 5-10× longer run, more failures, and a lower coverage ratio in the final tarball.

The workflow emits one log line at startup announcing which mode it's in:

```
INFO  OpenSubtitles API: ACTIVE — bulk season downloads enabled
INFO  OpenSubtitles API login OK — 950 downloads remaining today
```

or

```
WARN  OpenSubtitles API: INACTIVE — credentials missing; falling back to rate-limited scrapers (slow, flaky)…
```

## How to dispatch a run

1. GitHub → **Actions** → **Build Subtitle Cache**
2. **Run workflow** → branch `main`
3. Inputs:
   - `limit` (default `300`): number of top-vote shows to include. Use `5` for a smoke test.
   - `cache_tag` (default `subtitle-cache-latest`): the GitHub Release tag to publish to. Leave as-is unless you specifically want a sandboxed run.
4. Click **Run workflow**.

The workflow has `concurrency.group: build-subtitle-cache` with `cancel-in-progress: false`, so only one build can run at a time; queued dispatches wait. The job's `timeout-minutes: 720` (12 hours) is the upper bound — typical full runs with VIP creds finish in 3–6 hours.

## How resume works

Two layers of resumability:

1. **`actions/cache@v4`** preserves harvested SRTs at `~/.engram/cache/data` between runs. The cache key is `subtitle-srt-${{ github.run_id }}` (unique per run, immutable), with `restore-keys: subtitle-srt-` pulling in the most recent prior cache.
2. **Per-season "fully cached" pre-check** in [`testing_service.download_subtitles`](https://github.com/Jsakkos/engram/blob/main/backend/app/matcher/testing_service.py): before calling the OpenSubtitles API for a season, count how many episodes already exist on disk under any naming variant. If all of them do, skip the API search entirely and log:

   ```
   INFO  Breaking Bad S01: all 7 episodes cached; skipping API
   ```

Together this means a re-dispatched run that finds 100% cache coverage completes in under 10 minutes with zero new API downloads.

### Sanity check

After any full build, dispatch the workflow once more with the same `limit`. The second run's log should:

- Emit one "skipping API" line per fully-cached season.
- End with `new downloads: 0` in the Final summary block.
- Total elapsed measured in minutes, not hours.

If those don't hold, something has broken the resume path — file an issue with the run URL.

## Migrating an older name-keyed cache

The harvested SRT cache under `~/.engram/cache/data/` used to be keyed by show
**name** (`data/Breaking Bad/`). Since [#288](https://github.com/Jsakkos/engram/pull/288)
it is keyed by **tmdb_id** (`data/1396/`) so two same-named shows can't collide. The
per-season coverage records (`subtitle_coverage`) were already tmdb-keyed, so after the
switch the resume fast path sees a season marked "done" but looks for its SRTs under the
new `data/<tmdb_id>/` path, finds nothing, and **re-harvests the whole show from scratch**.

If you have a cache built before that change, relocate the legacy dirs once with:

```bash
cd backend
# Preview the plan (dry-run is the default — nothing is moved):
uv run python scripts/migrate_subtitle_cache_keys.py --cache-dir ~/.engram/cache
# Apply it:
uv run python scripts/migrate_subtitle_cache_keys.py --cache-dir ~/.engram/cache --apply
```

How it resolves each `data/<name>/` dir to a tmdb_id:

- **Offline first.** Names are matched against `scripts/curated_shows.csv` (the list the
  cache is built from), tolerant of case and of Windows silently stripping trailing dots
  from directory names (`S.W.A.T.` → on-disk `S.W.A.T`). This is the default and needs no
  network.
- **`--tmdb-fallback`** (opt-in) resolves names absent from the CSV via a TMDB search.
  It's off by default so an unrecognized dir is reported rather than risk a fuzzy
  misfile. It needs a TMDB key, so run it with `DATABASE_URL` pointed at a config DB.
- A purely-numeric dir (`1396/`) is treated as already-migrated and skipped — unless it's
  also a show name in the CSV (`24` is the *show*, not tmdb_id 24), in which case it's
  reported as ambiguous; pass `--treat-as-name 24` to force it.
- Dirs ending in a backup suffix (`-bak`, `.bak`, `~`, `.tmp`, `.old`) are always left in
  place, so deliberate manual backups are never clobbered.

When a target id dir already exists (e.g. a show that was re-harvested from scratch after
the switch), the two are **merged**: episodes are unioned and, on a filename collision,
the larger SRT is kept. The migration is idempotent — a second run finds nothing to move.

## Cache format versioning

The published tarball includes a [`manifest.json`](https://github.com/Jsakkos/engram/blob/main/backend/scripts/build_subtitle_cache.py) with `cache_format_version` (a string defined in `backend/app/matcher/vectorizer_config.py` — currently `"2"`, which stores uint16 hashed counts and applies TF-IDF at load time; `"1"` shipped pre-computed float64 TF-IDF rows). The backend reads this on download and rejects incompatible caches, falling back to scraping. The check happens in two places:

- [`precomputed_cache_service._ensure_precomputed_cache_inner`](https://github.com/Jsakkos/engram/blob/main/backend/app/services/precomputed_cache_service.py) — on download.
- [`EpisodeMatcher._load_precomputed_season`](https://github.com/Jsakkos/engram/blob/main/backend/app/matcher/episode_identification.py) — on load.

This is what makes the rolling `subtitle-cache-latest` release scheme safe: old backends never load a tarball they can't interpret.

When you intentionally bump the version:

1. Update `CACHE_FORMAT_VERSION` in `vectorizer_config.py` (this also changes the hash → forces re-builds).
2. Dispatch the workflow to publish a new tarball at the same `subtitle-cache-latest` tag.
3. Older backends will see the format-version mismatch and fall back to scraping until they are upgraded.

There's no need to delete or rename old releases — the manifest check makes them inert.

## Expected log output

A healthy run with the recent [#150](https://github.com/Jsakkos/engram/pull/150) progress improvements logs:

```
INFO  OpenSubtitles API: ACTIVE — bulk season downloads enabled
INFO  OpenSubtitles API login OK — 950 downloads remaining today
INFO  Selected 300 shows for the cache
Building cache  [▓▓▓░░░░░] 42/300  0:01:23  ETA 0:08:12
  Breaking Bad  [▓▓▓░] 3/5
✓ Breaking Bad — 62 episodes (54 cached, 8 new, 0 missing) in 248s
INFO  OS API quota: 942 downloads remaining today
…
INFO  Built cache: 295 shows, 12,847 episodes, 84.3 MB -> engram-subtitle-cache.tar.gz
Final summary: 295 shows, 12,847 episodes packaged (84.3 MB)
  episodes seen:    13,012
  cache hits:       11,892
  new downloads:    955
  not found:        165
  cache hit rate:   93%
  seasons OK:       1,440
  seasons skipped:  12 (below coverage threshold)
  seasons failed:   3
  elapsed:          4:21:07
  OS quota left:    287 downloads today
```

Under GitHub Actions there's no TTY, so `rich` auto-degrades the live bar to plain `console.log` lines — every `✓` / `OS API quota` / `Final summary` line still appears, just not the animated bar.

## Manually invalidating the cache

If you need to force a clean rebuild from zero (e.g., suspecting a poisoned cache entry):

1. **Caches** → in the Actions sidebar, delete all entries whose key starts with `subtitle-srt-`.
2. Dispatch the workflow.

This makes every show start from a cold cache and re-scrape everything. Costs real API quota; avoid unless necessary.

## Local equivalent

You can run the script outside CI for smoke tests:

```bash
cd backend
export TMDB_API_KEY="eyJ..."
export OPENSUBTITLES_API_KEY="..."
export OPENSUBTITLES_USERNAME="..."
export OPENSUBTITLES_PASSWORD="..."

uv run python scripts/build_subtitle_cache.py --shows "The Wire" --limit 1
```

Useful args:

- `--shows "Show1,Show2"` — pin to specific show names (overrides `--limit`/`--pages`).
- `--limit 5` — top 5 shows only.
- `--clean-srt` — delete harvested SRTs at the end of the run (default keeps them so re-runs resume).
- `--min-episodes-ratio 0.6` — drop a season if fewer than 60% of its episodes were harvested.

Run twice in a row with the same args to verify the resume path locally before pushing changes to the workflow:

```bash
uv run python scripts/build_subtitle_cache.py --shows "The Wire" --limit 1
uv run python scripts/build_subtitle_cache.py --shows "The Wire" --limit 1
```

The second run should finish in seconds and print `new downloads: 0` in the Final summary.
