# Episode Ordering via TMDB Episode Groups (GitHub #200)

## Context

**Problem.** Engram's matcher is content-based: it transcribes disc audio and votes the transcript against reference-subtitle text. That correctly answers *"which episode is this, by its dialogue?"* — but the season/episode **number** it emits is taken verbatim off the matched reference subtitle's label (`episode_identification.py` parses `S##E##` straight from the filename / precomputed `.index.json`; there is **zero** TMDB reconciliation in the match path). For the minority of shows whose orderings diverge — aired-out-of-order (*Firefly*, *TOS*), anime absolute numbering, segmented kids' shows, streaming re-cuts — the number that's "correct" depends on what the user's media server expects (Plex/Jellyfin let you pick aired / DVD / absolute per show). Engram has no concept of ordering, so it can match the right content and still write the wrong number.

**Why it's urgent now.** The upcoming acoustic fingerprint network keys every contribution on `(tmdb_id, season, episode)` parsed from `matched_episode` (`contribution_uploader.py:145-161`, `matching_coordinator.py:748-802`). If two users rip the *same* episode from differently-numbered sources and we let an output ordering bleed into that key, the network fractures into conflicting records — exactly the "noise and disagreement" we must avoid.

**What exploration established (de-risks the work):**
- The reference corpus is **already canonical TMDB aired-order by construction** — `backend/scripts/build_subtitle_cache.py` iterates `/tv/{id}/season/{n}` and builds codes from the loop index; OpenSubtitles `episode_number` is TMDB-reconciled. So `matched_episode` is canonical aired-order **today**, and the fingerprint network already gets clean keys.
- Therefore the danger is purely *forward-looking*: a new output-ordering projection must never leak into `matched_episode` or the fingerprint payload.
- TMDB Episode Group detail (`GET /3/tv/episode_group/{id}`) returns `groups[]` (each with `order`, `name`, `episodes[]`), and **every episode retains its canonical `season_number`/`episode_number`** plus an `order` (its position in that group) — the perfect shape for an *outward-only* canonical→output projection. (Verified against [TMDB docs](https://developer.themoviedb.org/reference/tv-episode-group-details) + [golang-tmdb structs](https://github.com/cyruzin/golang-tmdb).)

**Outcome.** A user can pick an output ordering (global default + per-show override); divergent shows file under the expected `SxxExx`; the common case sees no change and no prompts; and **canonical aired-order remains the single internal identity** for storage, history, and the fingerprint network.

## Governing invariant (the whole design hangs on this)

> **Canonical = TMDB aired order `(season, episode)`.** It is the only value written to `DiscTitle.matched_episode`, the fingerprint payload, and job history. **Output ordering is a presentation-only projection applied at the filename-formatting seam and nowhere else.** The projection is strictly one-directional (canonical → output), never inverted, never persisted to `matched_episode`.

Enforced structurally: the projection happens *inside* `organize_tv_episode`, so the projected ints never leave that function — no callsite can write them back. The fingerprint enqueue runs upstream at match time and reads `matched_episode` directly, so a projected number physically cannot reach it.

## Locked product decisions

| Decision | Choice |
|---|---|
| Global default ordering | **Aired (canonical)** — zero regression, matches Plex/Jellyfin, == fingerprint key |
| v1 ordering scope | aired / DVD / digital / production / story-arc / TV groups. **Absolute/anime deferred** to a follow-up issue |
| Backfill | **New rips only.** No renaming of already-organized files in v1 |
| Detection | **Auto-surface** the selector in review **only when a show's groups actually diverge** for the matched episodes; silent otherwise |
| Granularity | Global default + **per-show override** (divergence is a show property) |

## Implementation

### 1. New projection module — `backend/app/core/episode_ordering.py`

Pure, deterministic, network-light (reads only via cached TMDB fetchers); no DB writes; no coordinator imports.

- Ordering string enum mirroring TMDB `type` ints: `aired`(1), `dvd`(3), `digital`(4), `story_arc`(5), `production`(6), `tv`(7). `absolute`(2) reserved but excluded from `ALLOWED_ORDERINGS` in v1.
- `resolve_episode_group_id(show_id, ordering, api_key) -> str | None` — `aired` → `None`; else `fetch_episode_groups` filtered by `type`, with a **deterministic tiebreak** among same-type groups (greatest `episode_count`, then `group_count`, then lexicographically smallest `id`) so re-rips project identically.
- `build_projection(group_detail) -> dict[(s,e) -> (s',e')]` — keyed on each episode's **canonical** `season_number`/`episode_number`.
  - `episode' = episode.order + 1` (TMDB `order` is 0-based) — unambiguous.
  - `season' = group.order + 1` as primary rule; fall back to digit-parse of `group.name` only if `order` is absent across the whole payload. **Do NOT** use sorted-group-index (breaks when a Specials group sits at `order 0`).
- `project_episode(show_id, ordering, season, episode, api_key) -> (s,e)` — identity fast path for `aired`; **never raises**; returns the input unchanged for every failure mode (no groups, type absent, key missing, canonical pair not found).
- `compute_divergence(show_id, ordering, canonical_pairs, api_key) -> bool` — reuses cached group detail; True iff any pair remaps.

### 2. TMDB client + cache — `tmdb_client.py`, `tmdb_persistent_cache.py`

Follow the existing `fetch_movie_runtime` pattern exactly (caller passes `api_key`; check `tmdb_persistent_cache.get()` → `_tmdb_get_json(url, api_key)` (SSRF allowlist already covers `api.themoviedb.org`) → `.put()` on success; don't cache failures).

- `fetch_episode_groups(show_id, api_key) -> list[dict]` → `GET /3/tv/{id}/episode_groups`, key `episode_groups:{show_id}`.
- `fetch_episode_group(group_id, api_key) -> dict | None` → `GET /3/tv/episode_group/{id}`, key `episode_group:{group_id}`.
- New TTLs `TTL_EPISODE_GROUPS = TTL_EPISODE_GROUP = 30 * 86400`. `clear_caches()` already wipes the whole `tmdb_cache` table on key rotation — automatically covers the new keys.

### 3. Organize seam — `backend/app/core/organizer.py`

Extend `organize_tv_episode(...)` with keyword-only `tmdb_id: str | None = None`, `ordering: str = "aired"`, `episode_group_id: str | None = None`. After parsing the canonical `season_num, episode_num` (the existing `S(\d+)E(\d+)` regex), and *before* `format_season_folder`/`format_episode_filename`:

```python
if ordering != "aired" and tmdb_id:
    out_season, out_episode = project_episode(tmdb_id, ordering, season_num, episode_num,
                                              get_config_sync().tmdb_api_key)
else:
    out_season, out_episode = season_num, episode_num
```

`episode_code` arg stays canonical; only `out_season/out_episode` feed the filename. The `"extra"` branch (→ `organize_tv_extras`) is untouched and bypasses projection.

### 4. Wire the three finalization callsites — `finalization_coordinator.py`

All three already hold `job` (`.tmdb_id`) and the title, and pass `matched_episode` as `episode_code`: `finalize_disc_job` deferred-organize loop, `apply_review` → `_finalize_tv_if_resolved`, and `process_matched_titles` → `_finalize_tv_if_resolved`. Resolve `(ordering, episode_group_id)` **once per finalize sweep** via the resolver (step 6), then pass `tmdb_id=str(job.tmdb_id), ordering=..., episode_group_id=...` into each `organize_tv_episode` call. Set `title.episode_ordering` / `title.episode_group_id` to what was applied (audit only). `matched_episode` is **not** touched. Applies uniformly regardless of `match_source` (engram / discdb / ai_llm / user all store canonical aired today — confirm none pre-project).

### 5. Models + migrations

- **Global default** — `AppConfig` (`app_config.py`): `episode_ordering_preference: str = Field(default="aired", sa_column_kwargs={"server_default": text("'aired'")})`.
- **Per-show override — dedicated table** `backend/app/models/show_ordering.py` → `ShowOrderingPreference` (`tmdb_id` PK, `ordering` default `"aired"`, `episode_group_id` nullable, `created_at`/`updated_at`). Chosen over a JSON-on-AppConfig column because `database.py:_migrate_app_config()` drops+recreates the `app_config` row on schema change and would clobber a blob; a separate table is immune and gives PK lookups.
- **DiscTitle audit fields** (`disc_job.py`, after `match_source`): `episode_ordering: str | None`, `episode_group_id: str | None` (both `default=None`).
- **Registration + migration:** import `ShowOrderingPreference` in `models/__init__.py` **and** `database.py` (so `create_all`/`_add_missing_columns` register the table). New table auto-creates on fresh/frozen builds; new columns land via `_add_missing_columns` (server_default / `DEFAULT NULL`) for frozen users. Add an Alembic revision for dev parity (create table + add the AppConfig column + add the two DiscTitle columns) — the reconciler and Alembic must agree.
- **Resolver** `episode_ordering_service.py`: `resolve_show_ordering(tmdb_id, session) -> (ordering, group_id)` with order **per-show row → global default → `"aired"`**; lazily resolve+persist `episode_group_id` for a non-aired result that lacks one.

### 6. Divergence detection — reuse `get_season_roster` (`routes.py:~600`)

No new read endpoint. The roster endpoint already runs at review time with `job.tmdb_id`, the season, the matched titles, a TMDB key, and off-thread TMDB calls; the Inspector already consumes it via `useSeasonRoster`. Extend its response with: `ordering_available` (any non-aired group exists), `ordering_diverges` (some option remaps a matched episode on this disc), `current_ordering` (resolved per-show pref), and `ordering_options[]` (`{ordering, label, tmdb_type, diverges, projection: {canonical_code -> projected_code}}`). When `ordering_diverges` is false the UI hides the selector.

### 7. Config + per-show API — `routes.py`, `config_service.py`

- `ConfigResponse.episode_ordering_preference: str`; `ConfigUpdate.episode_ordering_preference: str | None`; validate against `ALLOWED_ORDERINGS` (422 on `"absolute"`/unknown) before `update_config` (its setattr loop handles the rest).
- New per-show endpoints near `reassign_episode` (`~2750`): `GET /api/shows/{tmdb_id}/ordering` (reports resolved value + source) and `PUT /api/shows/{tmdb_id}/ordering` (validate, upsert row, may auto-resolve group id).

### 8. Review UI — `frontend/src/components/ReviewQueue/`

- `Inspector.tsx`: new props `orderingOptions`, `currentOrdering`, `orderingDiverges`, `onSelectOrdering`. Render an ordering `<select>` above "Ranked candidates", gated on `orderingDiverges`. In each candidate row, when `currentOrdering !== 'aired'`, show a secondary line (e.g. "DVD: S01E03") from `option.projection[candidateCode]` while the primary code stays canonical — making canonical-vs-output explicit.
- `ReviewQueue.tsx`: changing ordering calls `setShowOrdering(tmdbId, ordering)` then re-fetches the roster (refreshes projections/divergence). Ordering is **not** threaded into the review-batch decision — decisions stay canonical; ordering is resolved server-side at organize time from the persisted per-show pref.
- `api/client.ts`: `setShowOrdering` / `getShowOrdering`; extend the `SeasonRoster` type (`ReviewQueue/types.ts`) with the new fields.

## Files

| File | Change |
|---|---|
| `backend/app/core/episode_ordering.py` | **NEW** — projection engine + divergence |
| `backend/app/services/episode_ordering_service.py` | **NEW** — per-show→global→aired resolver |
| `backend/app/models/show_ordering.py` | **NEW** — `ShowOrderingPreference` table |
| `backend/app/matcher/tmdb_client.py`, `tmdb_persistent_cache.py` | new fetchers + TTLs |
| `backend/app/core/organizer.py` | projection seam in `organize_tv_episode` |
| `backend/app/services/finalization_coordinator.py` | wire 3 organize callsites + audit fields |
| `backend/app/models/app_config.py`, `disc_job.py`, `models/__init__.py`, `database.py` | new field/table/columns + registration |
| `backend/migrations/versions/*` | **NEW** Alembic revision (dev parity) |
| `backend/app/api/routes.py`, `services/config_service.py` | config surface + roster divergence + per-show endpoints |
| `frontend/src/components/ReviewQueue/{Inspector,types}.tsx`, `ReviewQueue.tsx`, `api/client.ts` | ordering selector + state + client |

## Build sequence (each step independently testable)

1. Projection module + unit tests against a **captured real Firefly DVD-group fixture** (identity, DVD remap, missing group, pair-not-found fallback, tiebreak, divergence).
2. TMDB fetchers + cache (mock `requests.get`; assert key/TTL + `clear_caches()` flush).
3. Models + migrations; **legacy-DB migration test** (snapshot lacking all three → `init_db()` → columns/table + defaults present).
4. Resolver tests (per-show > global > aired).
5. Organizer seam (aired byte-identical to today; DVD projected path; `tmdb_id=None`/group-absent → canonical).
6. **INVARIANT TRIPWIRE TEST** — finalize a matched TV title under a non-aired per-show pref; assert (a) file at projected path, (b) `matched_episode` still canonical, (c) enqueued `FingerprintContribution.season/episode` canonical, (d) wire payload canonical.
7. Finalization wiring (all 3 callsites) + resolver call.
8. Roster divergence + response extension (`ordering_diverges` flips only on real remap).
9. Config API (global + validation; per-show GET/PUT; reject `"absolute"`).
10. Frontend (types, client, Inspector selector + per-candidate projected line, ReviewQueue state + roster refetch).
11. E2E: diverging-DVD show → selector auto-surfaces → pick DVD → per-show pref persisted → finalize → DVD-named file → `matched_episode` + fingerprint still aired.

**Highest risk:** the **season-derivation rule** in step 1 — wrong `season'` silently misfiles a whole show while leaving canonical identity (and the fingerprint network) healthy, so it's invisible. Pin it to a captured real Firefly payload (plus ideally one show with a `order 0` Specials group), never a synthetic guess.

## Verification

- `cd backend && uv run pytest tests/unit/test_episode_ordering.py tests/integration/` — projection, resolver, organizer, migration, and the invariant tripwire green.
- Manual divergent-show check (backend `DEBUG=true`, worktree uvicorn against the real DB per the real-disc-testing setup — **no `--reload`**, one backend only): simulate/insert a *Firefly* TV disc, open the review queue, confirm the ordering selector **appears** (groups diverge), pick **DVD order**, confirm candidate rows show the projected `SxxExx`, finalize, and verify on disk the file is DVD-numbered **while** the job-history/`matched_episode` and any fingerprint contribution remain aired-order. Insert a non-divergent show (e.g. a normal sitcom) and confirm **no** selector and unchanged behavior.
- `cd frontend && npm run build && npm run lint`; `npm run test:e2e` for the review-flow spec.
- Terminate all `uvicorn`/`python`/`makemkvcon` processes after testing.

## Notes for execution

- Work on a feature branch (e.g. `feat/200-episode-ordering`), PR into `main` (squash-only, rebase to integrate).
- Per repo convention, drop a dated copy of this plan at `docs/superpowers/plans/2026-05-28-episode-ordering-tmdb-groups.md`.
- Follow-up issues to file: **absolute/anime numbering**, and **backfill / rename already-organized files on ordering change**.
