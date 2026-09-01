# Subtitle cache expansion: harvester repair, English-only curation, scheduled builds

**Date:** 2026-08-31
**Status:** Design approved, pending implementation plan

## Summary

The precomputed subtitle cache stalled: the last published build (2026-07-08) covers
467 shows / 36,742 episodes, and the local coverage tracker shows 27,265 episodes
missing across 665 shows. The apparent cause was believed to be poor subtitle
availability at the providers. It is not. A two-day OpenSubtitles quota failure on
2026-06-11/12 was recorded by the build script as genuine zero coverage, which
skip-listed 664 seasons and made the corpus look permanently unharvestable.

This spec covers three sequenced pieces of work:

1. Repair the harvester so a quota or provider failure can never again be recorded
   as a content fact.
2. Rebuild the show list as English-only, using outcome data drawn only from
   periods when the harvester was known healthy.
3. Move harvesting to the Ubuntu server (`192.168.1.122`) on a daily schedule.

The ordering is load-bearing. Curating or scheduling before the repair would encode
a two-day infrastructure outage into the permanent show list and re-run the failure
on a 30-day cycle.

## Evidence

Coverage rates from `subtitle_coverage`, split at 2026-06-11:

| Window | Season rows | Covered / total | Rate |
|---|---|---|---|
| Before 2026-06-11 | 2,111 | 32,280 / 34,219 | **94.3%** |
| 2026-06-11 onward | 978 | 4,277 / 29,509 | **14.5%** |

The split is by date, not by content. Per-show proof, same providers and account:

- **ER**: S05 to S15 fully covered on 2026-06-08; S01 to S04 recorded zero on 06-12.
- **The Nanny**: S01 to S02 covered 06-04; S03 to S06 zero on 06-12.
- **Superstore**: S01 covered 06-09; S02 to S06 zero on 06-12.
- **CSI: NY**: S08 and S09 covered early in the 06-12 run; S01 to S07 zero later in
  the same run.

A diagnostic re-harvest of ER on 2026-08-31 recovered all 15 seasons (330/331
episodes) in 2m15s for 94 downloads. The four seasons the tracker had written off as
zero downloaded cleanly.

The language gradient that appeared in the aggregate data is also an artifact. Split
by the same window:

| Origin | Healthy window | Suspect window |
|---|---|---|
| US | 94.8% | 24.3% |
| GB | 96.1% | n/a |
| JP | 89.3% | 8.2% |
| KR | 100% | 9.1% |
| MX | 100% (81 eps) | 0.1% |

Only **9 shows** measure below 20% using healthy-window data alone. **243 shows have
never had a fair measurement**: every record they hold comes from the broken window.

## Root cause

Three defects compound:

1. **Quota is checked only at login.** `testing_service._get_os_client` tests
   `remaining <= 0` once and sets `_OS.failed`; the client is then cached for
   `_OS_TOKEN_MAX_AGE`. `_snapshot_os_quota` keeps updating
   `user_downloads_remaining` as downloads proceed, but nothing reads it. A run
   starting at 1,000 sails past zero and turns every later season into a false
   negative.

2. **Coverage is recorded unconditionally.** `build_subtitle_cache._harvest_show`
   calls `coverage_tracker.record()` after every season regardless of why the
   season came back empty. `record()` cannot distinguish "the provider has nothing"
   from "we got nothing", and writes `coverage_ratio = 0.0` either way.
   `should_skip()` then honours that for `skip_window_days` (30).

3. **One window carries two opposite meanings.** `is_done()` and `should_skip()`
   both call `_fresh_record()` with the same `skip_window_days`. For a failure,
   30-day expiry is a sensible retry. For a success, expiry silently disables the
   complete-on-disk fast path, so the next run re-enumerates the whole corpus. That
   oversized run is what hits the quota wall, which poisons records, which expire,
   which produces another oversized run. The failure is self-amplifying.

A fourth, non-causal defect obscured the problem: the OpenSubtitles bulk-season path
writes SRTs to disk and the per-episode loop reads them back as `cached`, so
`tally.by_source` never credits `opensubtitles_api`. The ER diagnostic reported
`new downloads: 1` while consuming 94 quota. Run summaries have been under-reporting
OpenSubtitles usage by roughly 99%, so the June quota wall was invisible in the logs.

## Goals

- A build run cannot record a coverage fact it did not fairly measure.
- A daily unattended run stays inside the 1,000/day VIP quota and makes monotonic
  progress.
- The shipped cache contains only English-language shows, matching Engram's actual
  capability (the matcher pairs English subtitles with English audio).
- Harvesting runs on the Ubuntu server on a schedule, publishing to the rolling
  `subtitle-cache-latest` release.

## Non-goals

- Capping tarball size. Growth is deliberately uncapped for now. A shard-by-range
  split is noted as a future escape hatch, not built.
- Changing the cache format or the runtime consumer path.
- Multi-language matching support.

## Design

### 1. Harvester repair

**1a. Mid-run quota guard.** Add a quota floor check after each season in
`_harvest_show`. When `user_downloads_remaining` falls to the floor, set
`_OS.failed` and raise a `QuotaExhausted` exception that `main()` catches to end the
run cleanly, package what was harvested, and exit non-zero with a clear message.

**1b. Do not record coverage on infrastructure failure.** `download_subtitles`
returns a `degraded: bool` in its result when OpenSubtitles was unavailable or
exhausted **and** the scraper cascade did not serve the season. `_harvest_show`
skips `coverage_tracker.record()` entirely for degraded seasons. No record is
strictly better than a false zero: absence means "measure this later", while `0.0`
means "never retry for 30 days".

**1c. Split the two windows.** `should_skip()` keeps the 30-day window. `is_done()`
stops expiring on a timer: a season whose SRTs are present on disk is done
regardless of record age. Verification moves to the disk check that the
complete-on-disk path already performs via `discover_season_srts`. This removes the
periodic full re-sweep.

**1d. Download budget.** New `--max-downloads` (default 900) counted across the run.
On reaching it the run stops cleanly, exactly as 1a. This is the single change that
makes every unattended run safe, and it lands first.

**1e. Purge the poisoned window.** One-time maintenance command deleting
`subtitle_coverage` rows with `attempted_at >= 2026-06-11` and
`coverage_ratio < min_ratio`. These rows are already inert (expired by age), but
they corrupt any outcome-based curation. Deleting converts known-bad data into
no-data, which the repaired harvester refills honestly. Legitimately-zero seasons in
that window are re-measured, which is acceptable.

**1f. Honest reporting.** Credit `opensubtitles_api` in `tally.by_source` for
episodes served by the bulk path, and surface downloads-consumed alongside
`OS quota left` in the final summary.

### 2. Curation

Produce a new `curated_shows.csv` with:

**Hard filter:** `original_language == "en"`. This is the matcher's real constraint.
Note that `origin_country` is the wrong field: Telemundo shows such as
*El Señor de los Cielos* and *Pasión de Gavilanes* are `origin_country: US` and
Spanish-language, and are among the largest gaps in the current list.

**Outcome-based exclusion, windowed.** A show is excluded on measured coverage only
when the measurement comes from a healthy window (currently: before 2026-06-11, plus
anything measured after 1a to 1f land) and the sample is at least 20 episodes.
Threshold: below 20%. Shows with no trustworthy measurement stay in the list and are
measured by the repaired harvester.

**Genre as ordering prior, not a filter.** Kids animation and reality rank last in
harvest order rather than being excluded, so a wrong prior costs scheduling position
rather than permanent omission. This keeps *Archer*, *Rick and Morty*, *South Park*
and *Futurama*, which are commonly ripped from disc.

**Extension.** Grow well beyond the current 448 rows via
`fetch_shows_by_vote_count`, filtered as above.

### 3. Server deployment

Target: `jsakkos@192.168.1.122` (Ubuntu VM `pve-docker`, 357 GB free, Python 3.12.3,
`gh` 2.45.0 authenticated as `Jsakkos` with `repo` scope, `crontab` and user systemd
both available, TMDB/OpenSubtitles/GitHub all reachable).

- **Checkout.** Upgrade the existing `~/engram` checkout from v0.8.1 to current
  `main` (approved; the deployment there is not in active use). Install `uv`, which
  is currently absent.
- **Scheduler.** systemd user timer rather than cron: real logs via `journalctl`,
  health via `systemctl status`, no `MAILTO` noise, and `Persistent=true` to catch
  runs missed while the box was down.
- **Secrets.** A `0600` `EnvironmentFile` supplying `TMDB_API_KEY`,
  `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD`, which
  the build script already bootstraps from env. **The user writes this file; the
  credentials are not to be copied or handled on their behalf.**
- **Publish step.** After a successful build, `gh release upload
  subtitle-cache-latest ... --clobber`.

### 4. Cutover

Quota is per-account, so exactly one machine may harvest. Two harvesters would race
for the same 1,000/day bucket, and each would record coverage the other cannot see:
the loser writes zeros and skip-lists seasons the winner already holds, which is
defect 2 firing across machines.

1. `rsync` `~/.engram/cache/data` (1.7 GB, 36,809 SRTs) to the server.
2. `rsync` `~/.engram/cache/tmdb_cache.sqlite`, which carries `subtitle_coverage`.
   Without it the server re-harvests 36,000 episodes it already has on disk.
3. Verify a dry run ships from disk at zero quota cost.
4. Retire the laptop build. The laptop keeps its corpus as a backup but must not run
   `build_subtitle_cache.py` again.

## Testing

- **Unit:** `coverage_tracker` window semantics (`is_done` no longer expires;
  `should_skip` still does); degraded-season suppression of `record()`; budget
  counter arithmetic.
- **Integration:** a simulated quota exhaustion mid-run asserts that (a) the run
  stops, (b) no coverage rows are written for unmeasured seasons, (c) the exit code
  is non-zero.
- **Regression guard:** a test asserting that a season returning zero episodes with
  `degraded=True` writes no coverage row. This is the specific defect that cost
  664 seasons.
- **Live validation:** re-harvest a known-poisoned show (Full House, 1/192) and
  confirm recovery, as ER already demonstrated.

## Risks

- **Re-measuring the purged window costs quota.** Roughly 978 season rows return to
  the unmeasured pool. At 900/day this is several days of harvesting. Acceptable:
  it is the price of replacing bad data with real data, and it is one-time.
- **Healthy-window samples are uneven.** JP (24 shows), MX (3) and CO (1) have small
  trustworthy samples skewed toward higher-ranked titles, so their true long-tail
  rates are unknown. This does not affect the English-only scope, but it means the
  20% exclusion threshold should not be applied to thin samples, hence the
  20-episode minimum.
- **Server upgrade from v0.8.1 to main** crosses roughly 60 commits of schema
  migrations. The deployment is not in active use, and the cache builder does not
  depend on the deployment's database, but the upgrade should be verified rather
  than assumed.
- **Schema reconciler faults observed on the dev DB** (`no such column:
  app_config.always_review`, then `table fingerprint_contributions already exists`
  during `init_db()`). These did not block the ER diagnostic but indicate drift in
  the migration path that the server build will also traverse.

## Open items

- Exact size of the recoverable English backlog cannot be stated until the purge and
  a first repaired sweep complete. The pre-purge estimate of "~4,000 to 5,000
  recoverable episodes" is not reliable and is deliberately not committed to here.
- Whether to shard the tarball if it grows past a few hundred MB more. Noted, not
  designed.
