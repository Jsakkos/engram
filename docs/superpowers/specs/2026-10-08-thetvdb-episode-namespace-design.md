# TheTVDB as a secondary episode-numbering source

**Date:** 2026-10-08
**Status:** design approved, not yet implemented
**Origin:** user report, Justice League (2001) Season 1 Blu-ray
**Related:** #200 (TMDB episode-group ordering), #658 (combined multi-episode codes),
`docs/superpowers/specs/2026-09-20-subtitle-cache-numbering-scheme-design.md`

## Problem

TMDB catalogues the Justice League pilot "Secret Origins" as one 72-minute episode, giving
Season 1 24 episodes. The Blu-ray and TheTVDB carry it as three parts, giving 26. Verified
2026-10-08:

| Source | Season 1 opening | Season 1 count |
|---|---|---|
| TMDB (show 1618), aired order | E01 Secret Origins (72 min), E02 In Blackest Night (1) | 24 |
| TheTVDB official order | E01-E03 Secret Origins (1)/(2)/(3), E04 In Blackest Night (1) | 26 |

None of TMDB's episode groups for the show splits the pilot: Aired Order and Production Order
both keep one Secret Origins, the "Season" group has one episode, and the "Bluray Order"
group is empty. The existing #200 ordering feature therefore cannot fix this.

The failure cascades into matching. `download_subtitles` asks TMDB for the season's episode
count (`testing_service.py`, `fetch_season_details`) and fetches E01-E24 from providers whose
own numbering follows the TVDB/IMDb split. References are off by two after the pilot, E25 and
E26 are never fetched, and a manually uploaded subtitle is labelled in TMDB numbering so it
cannot line up either.

### Why this does not contradict the Dexter's Laboratory finding

The 2026-09-20 numbering-scheme spec found TheTVDB is *not* the subtitle corpus's namespace for
Dexter's Laboratory: there TVDB numbers by segment exactly as TMDB does, and the corpus follows
neither. That finding stands. Justice League is the opposite class of show: TMDB merges what
TVDB and the disc split. TVDB is useful as an *opt-in, per-show* numbering, not as a universal
reconciliation anchor, and nothing in this design assumes the subtitle corpus follows TVDB.

## Decisions

1. **TheTVDB is a secondary numbering source, not a replacement.** TMDB remains authoritative
   for show identity, classification, posters and the default numbering.
2. **Output naming follows TVDB numbering** for a show that uses it: the three pilot tracks
   file as `S01E01`, `S01E02`, `S01E03`, and later episodes follow TVDB.
3. **Engram suggests, the user decides.** A roster divergence is detected and surfaced on the
   review page; nothing switches automatically.
4. **Per-job episode namespace (approach 2).** A job carries `episode_namespace`
   (`"tmdb"` | `"tvdb"`). In TVDB mode the roster, references, review selector,
   `matched_episode` and filenames are all TVDB-numbered. Every track keeps a unique code, so
   finalization's dedupe and conflict logic is unchanged.
5. **In-app attribution.** TheTVDB's free licensed tier requires a visible link wherever
   TVDB metadata is shown to end users. The repo/docs exception covers only CLI tools and
   libraries.

### Rejected alternatives

- **TMDB canonical plus a part number** (`matched_part` column, crosswalk TVDB S01E02 to TMDB
  S01E01 part 2). Preserves the "TMDB aired is the sole identity" rule, but three tracks then
  share `matched_episode = S01E01`, and finalization (47 references) dedupes and conflict-checks
  by that code. Every such site would need to learn about parts; missing one mis-files
  silently.
- **TVDB for references only, folded back to TMDB.** The same storage problem as above with an
  extra translation step.
- **A TVDB projection in the #200 seam.** `build_projection` maps one canonical
  `(season, episode)` to one output pair. A 1-to-3 split has no canonical key for the parts.

### Invariant change

The rule recorded for #200, "canonical TMDB aired order is the sole internal identity", becomes
"TMDB aired order is the internal identity unless the job's `episode_namespace` says
otherwise". The sites that genuinely need TMDB identity are enumerated in
[TMDB-identity boundaries](#tmdb-identity-boundaries) and guarded by a test.

## Licensing

TheTVDB issued Engram a free licensed project key (tier: under $50k annual revenue). There is
no per-user subscriber PIN. Conditions: "attribution with a direct link to TheTVDB.com must be
displayed to end users viewing metadata from our API". Source:
https://thetvdb.com/api-information.

## Architecture

### `app/matcher/tvdb_client.py` (new)

TheTVDB v4 client. Responsibilities:

- `POST https://api4.thetvdb.com/v4/login` with `{"apikey": key}`; cache the bearer token in
  memory until a 401, then re-login once.
- `fetch_season_roster(tvdb_id, season) -> list[dict]` via
  `GET /series/{id}/episodes/official?season=N`, paging until exhausted. Each dict carries
  `season`, `episode`, `name`, `runtime`, `aired`.
- Results persisted in `tmdb_cache.sqlite` in a separate `tvdb_season_roster` table with a TTL
  matching the TMDB season cache.
- Never raises into the pipeline: any failure returns `None` ("no TVDB data"). Uses the existing
  `retry_network_operation` decorator. All calls from async code go through
  `asyncio.to_thread`.
- Key resolution: `AppConfig.tvdb_api_key` if non-empty, else the `TVDB_API_KEY` environment
  variable (injected into frozen builds, set by hand in dev). Neither present: feature disabled.

### `app/core/episode_namespace.py` (new)

Single source of truth for a job's numbering.

- `roster_for(job, season) -> list[RosterEpisode]`: TMDB roster for `"tmdb"`, TVDB roster for
  `"tvdb"`. `RosterEpisode` is `(season, episode, name, runtime)`.
- `detect_divergence(tmdb_roster, tvdb_roster) -> Divergence | None`: divergent when the
  episode counts differ or the normalized title sequences differ (normalization strips part
  suffixes such as `(1)`, `Part I`, punctuation and case).
- `build_crosswalk(tmdb_roster, tvdb_roster)`: pairs episodes by normalized title, then air
  date, in order. A TVDB episode maps to a TMDB episode only when the pairing is 1:1.
- `to_tmdb(job, season, episode) -> (season, episode) | None` and
  `from_tmdb(job, season, episode) -> (season, episode) | None`: identity for `"tmdb"` jobs;
  crosswalk lookups for `"tvdb"` jobs; `None` when the mapping is not 1:1 (all three Secret
  Origins parts). Callers treat `None` as "skip".

### Show-to-TVDB link

TMDB `GET /tv/{id}/external_ids` returns `tvdb_id` (verified for show 1618). Resolved once per
show and cached on `ShowOrderingPreference.tvdb_id`. No name search.

### Data model

| Change | Notes |
|---|---|
| `DiscJob.episode_namespace: str` | default `"tmdb"`, `server_default 'tmdb'` so existing rows read correctly |
| `DiscJob.tvdb_divergence_json: str \| None` | e.g. `{"season": 1, "tmdb": 24, "tvdb": 26}` |
| `DiscJob.episode_namespace_note: str \| None` | fallback reason, e.g. "TheTVDB unavailable; matched with TMDB numbering" |
| `ShowOrderingPreference.ordering` | gains the value `"tvdb"` |
| `ShowOrderingPreference.tvdb_id: int \| None` | cached link |
| `ShowOrderingPreference.tvdb_suggestion_dismissed: bool` | default false, `server_default 0` |
| `AppConfig.tvdb_api_key: str` | default `""`; also in `ConfigUpdate`, `ConfigResponse` and ConfigWizard; redacted as `***` |

Columns go through `_add_missing_columns()` as well as an Alembic revision, because frozen builds
skip Alembic.

## Data flow

### Deciding the namespace (identification)

Once `tmdb_id` and the season are known:

1. If the show's preference is `"tvdb"` and a TVDB roster can be fetched, set
   `episode_namespace = "tvdb"`. Subsequent discs of the show take this path.
2. If the preference is `"tvdb"` but TVDB is unavailable, keep `"tmdb"` and set
   `episode_namespace_note`.
3. Otherwise, if a TVDB key is configured and the show has a `tvdb_id`, fetch both rosters and
   store `detect_divergence` in `tvdb_divergence_json`. The job proceeds in TMDB numbering.

The divergence check is best-effort decoration: it never delays or fails identification.

### Accepting the suggestion (review page)

`POST /api/jobs/{job_id}/episode-namespace` with `{"namespace": "tvdb"}`:

1. Upsert the show preference to `"tvdb"`.
2. Set the job's `episode_namespace = "tvdb"`.
3. Re-download references in TVDB numbering for the job's season(s).
4. Run the existing post-rip `rerun_matching`.

No re-rip. `{"namespace": "tmdb"}` reverses the job and preference. Dismissing is a separate
`POST /api/shows/{tmdb_id}/tvdb-suggestion/dismiss`, which sets `tvdb_suggestion_dismissed`
(it is a show-level fact, not a job action).

Switching a show away from TVDB affects future jobs only; organized files are never renamed.

### Stage behavior for a `"tvdb"` job

| Stage | Behavior |
|---|---|
| Subtitle download | Episode count from `roster_for(job)`. References stored in `data/<tmdb_id>@tvdb/`; the precomputed (published) pack fast path is skipped. |
| Runtime checks (`matching_coordinator`, `identification_coordinator`) | `fetch_season_episode_runtimes` call sites read `roster_for`. |
| LLM matcher (`llm_episode_matcher`) | `fetch_season_episodes` reads `roster_for`. |
| Review roster (`GET` season roster in `routes.py`) | Returns TVDB episodes and `source: "tvdb"`. Manual subtitle uploads are labelled in TVDB numbering. |
| Organize | Filename built from `matched_episode` directly; the #200 TMDB projection is skipped. Episode titles from the TVDB roster. |

## TMDB-identity boundaries

Rule: translate through the crosswalk when 1:1, otherwise omit.

**Outbound**

| Site | Behavior for `"tvdb"` jobs |
|---|---|
| Fingerprint contribution (`disc_contribution_queue._derive_assignment`) | Title translated via `to_tmdb`; untranslatable titles omitted from the contribution. |
| TheDiscDB export (`discdb_exporter`, `discdb_episode_fields`) | Same. |
| Published subtitle cache, `coverage_tracker` | Never written for TVDB-numbered harvests. |

**Inbound**

| Site | Behavior for `"tvdb"` jobs |
|---|---|
| DiscDB lookup mappings (`discdb_mappings_json`) | Translated via `from_tmdb`; untranslatable hints dropped, the matcher decides. |
| Disc-hash fingerprint pack assignments | Same. |

**Display only (no translation)**

Discord notifications, history and the dashboard show the stored code, which matches the file
on disk. The history detail panel shows "Episode numbering: TheTVDB". The diagnostic bundle
includes `episode_namespace`, `tvdb_divergence_json` and `episode_namespace_note`.

**Guard rail.** A unit test enumerates every module in `app/` that reads `matched_episode`
through `parse_episode_code` / `episode_parts` and requires each to appear in an explicit
classification (namespace-aware or display-safe). An unclassified new consumer fails the test.

## Error handling

Every TVDB problem degrades to today's TMDB behavior; enabling TVDB must never make a disc less
likely to finish.

| Failure | Result |
|---|---|
| No key, login rejected, TVDB down at identification | No divergence check; TMDB as today; one warning logged. |
| TMDB has no `tvdb_id` | No suggestion offered. |
| TVDB down when preference is `"tvdb"` | Job runs in TMDB numbering; `episode_namespace_note` set and shown on the review page. |
| 401 mid-session | One re-login, then the fallback above. |
| TVDB down when the user clicks Switch | Endpoint returns an error; nothing changes; review page shows the message. |

## Configuration

- `tvdb_api_key` in `AppConfig`/`ConfigUpdate`/`ConfigResponse`/ConfigWizard, redacted in
  `GET /api/config`. Empty means "use the built-in key".
- The built-in key is a `TVDB_API_KEY` GitHub Actions secret that `release.yml` exposes to the
  PyInstaller build; `engram.spec` writes it into a generated, gitignored module read at
  runtime. It is never committed. (Any key shipped in a binary is extractable; this is
  accepted for project keys.)
- `"tvdb"` is a **per-show** ordering only. It is excluded from the global default ordering
  dropdown, because TVDB numbering is wrong for most shows. This intentionally breaks the
  lock-step note on `ALLOWED_ORDERINGS` in `app/core/episode_ordering.py`; update that comment.

## UI

- **Suggestion notice** on the review page when `tvdb_divergence_json` is set, the job is
  `"tmdb"`, and the show has not dismissed it: "TheTVDB numbers Season 1 differently (26
  episodes vs TMDB's 24). [Switch to TheTVDB numbering] [Dismiss]". Switching shows the usual
  matching progress.
- **Ordering selector** (per show) offers aired, DVD and TheTVDB; TheTVDB only when the show
  has a `tvdb_id`.
- **Attribution:** "Episode data: TheTVDB" linking to `https://thetvdb.com`, shown under the
  episode selector whenever the roster `source` is `"tvdb"`, and beside the TheTVDB option in
  the ordering selector.
- **Fallback note:** `episode_namespace_note`, when set, shown on the review page and history
  detail.

## Testing

**Unit (`tests/unit/`)**

- `tvdb_client`: token caching and single re-login on 401; parsing of a *recorded live*
  Justice League S1 response (no synthetic fixture); paging; persistent cache hit; never
  raises.
- `episode_namespace`: `roster_for` source selection; crosswalk from recorded Justice League
  rosters (TVDB S01E04 to TMDB S01E02 translates; TVDB S01E01-E03 all return `None`); recorded
  Dexter's Laboratory rosters report no divergence.
- `detect_divergence`: count mismatch, title-sequence mismatch, agreement.
- Boundaries: contribution and DiscDB export omit untranslatable titles; inbound hints
  translate or drop; coverage never written for `"tvdb"` jobs.
- The `matched_episode` consumer guard-rail test.
- Config: `tvdb_api_key` three-way sync; redaction.

**Pipeline (`tests/pipeline/test_tvdb_namespace_flow.py`)**

Justice League S1, 26 tracks, stubbed TMDB and TVDB clients: TMDB job records divergence and
parks in review; accepting flips namespace and re-matches; files organize as `S01E01`-`S01E26`
with TVDB titles; a second disc of the show starts in `"tvdb"`. Failure paths: TVDB down after
the preference is set falls back with a note; no key means no behavior change.

**Frontend**

- Vitest: notice renders and dismisses; attribution only when `source === "tvdb"`; TheTVDB
  selector option only with a `tvdb_id`.
- Playwright: one simulated suggestion flow. Simulation bypasses identification, so the test
  seeds `tvdb_divergence_json` directly.

**Manual**

Real Justice League Blu-ray, or the reporting user on a beta build with a diagnostic bundle.

## Out of scope (v1)

- TVDB DVD / absolute season types (official order only).
- Renaming existing library files when a show's numbering changes.
- TVDB artwork, overviews or show identification.
- Publishing a TVDB-numbered subtitle cache.
- Using TVDB as the namespace for the subtitle corpus generally (see the Dexter's Laboratory
  finding).
