# TV library disambiguation for same-name shows

**Date:** 2026-06-01
**Status:** Approved (design) — pending implementation plan
**Branch:** `claude/vibrant-sinoussi-f510bc`
**Related:** PR #287 / #288 (matcher-side same-name fixes), `project_show_identity_collision`, `project_tv_organize_paths_sync`

## Problem

TV library organization keys the destination on the **bare show name only**, so two shows
that share a title collide on disk. The canonical case is *Frasier* 1993 (`tmdb_id=3452`) vs.
the 2023 revival (`tmdb_id=195241`): both land in `<library>/Frasier/Season 1/` with identical
filenames (`Frasier - S01E02.mkv`). The second rip then hits `resolve_conflict` and is
skipped / overwritten / prompted per `conflict_resolution_default`. The two shows cannot
coexist, and nothing in the path records which is which.

Movies already solve this: `format_movie_folder` (organizer.py:38) emits `Title (Year)` and
strips a trailing empty `()` when the year is unknown. `organize_tv_episode` already *receives*
`tmdb_id` but uses it only for episode-ordering projection (organizer.py:429), never for naming.

Surfaced during live verification of PR #287/#288. This work is **library-side coexistence /
naming only** — it is independent of episode *matching*.

## Goal

Give TV the same disambiguation movies get, targeting the convention Plex/Jellyfin parse:

```
<library>/Frasier (1993) {tmdb-3452}/Season 1/Frasier (1993) - S01E02.mkv      (Plex)
<library>/Frasier (1993) [tmdbid-3452]/Season 1/Frasier (1993) - S01E02.mkv    (Jellyfin)
```

The format is configurable via the existing `naming_*` config fields, defaulting to **preserve
today's behavior** so no existing library is silently relocated.

## Media-server compatibility (researched)

- **Series identity lives on the FOLDER, not the filename.** Both Plex and Jellyfin match the
  series from the folder's provider-id tag and the episode from the `SxxExx` token. A year in
  the filename is ignored by both — Jellyfin's own docs use `Alias (2001) - S01E01`, Plex's use
  `Alias - S01E01`. Both render fine in both servers.
- **The id-tag syntax differs and there is no single string that satisfies both:**
  - Plex: `{tmdb-3452}` (curly braces, `tmdb-`)
  - Jellyfin: `[tmdbid-3452]` (square brackets, `tmdbid-`)
  This is precisely why the format is **configurable + opt-in**: the user picks the syntax for
  their server. The id tag belongs **only on the folder**, never the filename.
- Sources: Plex "Naming and Organizing Your TV Show Files"; Jellyfin "Metadata Provider Identifiers".

## Decisions (confirmed with user)

1. **Opt-in default.** New `naming_tv_show_format` defaults to `"{show}"` — byte-for-byte
   identical to today. Disambiguation turns on only when the user changes the format. No
   migration relocates existing libraries.
2. **Persist the year.** Add `DiscJob.tmdb_year`, set at identification time (and on
   re-identify). Deterministic and offline-safe; the folder name stays stable across rips even
   if TMDB is unreachable on a later disc.
3. **Year in the filename too**, opt-in via `naming_episode_format` (default unchanged). Matches
   the GOAL and is server-neutral.

## Design

### 1. Config: new show-folder format + widened episode placeholders

`AppConfig` (`models/app_config.py`):

```python
naming_tv_show_format: str = "{show}"   # default == current behavior (bare show folder)
# existing:
naming_season_format:   str = "Season {season:02d}"
naming_episode_format:  str = "{show} - S{season:02d}E{episode:02d}"
naming_movie_format:    str = "{title} ({year})"
```

Placeholder sets (`organizer.py`):

```python
ALLOWED_TV_PLACEHOLDERS      = {"show", "season", "episode"}     # season format (unchanged)
ALLOWED_TV_SHOW_PLACEHOLDERS = {"show", "year", "tmdb_id"}        # NEW: show folder
ALLOWED_EPISODE_PLACEHOLDERS = {"show", "season", "episode", "year", "tmdb_id"}  # NEW: widened
ALLOWED_MOVIE_PLACEHOLDERS   = {"title", "year"}                 # unchanged
```

- Season format keeps validating against `ALLOWED_TV_PLACEHOLDERS` (no year/id — meaningless there).
- Episode format validates against the widened `ALLOWED_EPISODE_PLACEHOLDERS`. Default string
  unchanged → year in filename is strictly opt-in.

**Config three-way-sync hazard** (`project_config_field_three_way_sync`): the new field MUST be
added to all of:
- `AppConfig` model (above)
- `ConfigResponse` + the GET-config constructor that builds it (`routes.py` ~285, ~1132)
- `ConfigUpdate` (`routes.py` ~362) + the naming-format validation table (`routes.py` ~1245,
  pairing `naming_tv_show_format` → `ALLOWED_TV_SHOW_PLACEHOLDERS`, and updating the
  `naming_episode_format` pairing to `ALLOWED_EPISODE_PLACEHOLDERS`)
- `ConfigWizard.tsx` read (~234) + write (~374) mappings

**Recommended formats (documented for users):**

| Server | `naming_tv_show_format` | `naming_episode_format` |
|---|---|---|
| Plex | `{show} ({year}) {{tmdb-{tmdb_id}}}` | `{show} ({year}) - S{season:02d}E{episode:02d}` |
| Jellyfin | `{show} ({year}) [tmdbid-{tmdb_id}]` | `{show} ({year}) - S{season:02d}E{episode:02d}` |

> **Brace escaping gotcha:** to emit a literal `{tmdb-3452}` via `str.format`, the format string
> needs **doubled** braces: `{{tmdb-{tmdb_id}}}`. Jellyfin's `[tmdbid-{tmdb_id}]` needs no escaping.

### 2. Persist the first-air year

`DiscJob` (`models/disc_job.py`), next to `tmdb_id`/`tmdb_name`:

```python
tmdb_year: int | None = Field(default=None)
```

Auto-migrated for existing/end-user DBs by `database.py` `_add_missing_columns` (frozen builds
skip Alembic — the reconciler is what reaches users; see memory note on frozen-build migrations).

Population — a single helper in `identification_coordinator.py`, no analyst/classifier changes:

```python
def _resolve_show_year(tmdb_id: int | None, signal=None) -> int | None:
    """First-air year for a show. No-network fast path via same-name candidates
    (each carries a 'year' string), else cached TMDB details."""
    if not tmdb_id:
        return None
    cands = getattr(signal, "all_candidates", None) if signal else None
    for c in cands or []:
        if c.get("tmdb_id") == tmdb_id:
            y = (c.get("year") or "").strip()
            if y.isdigit():
                return int(y)
    from app.matcher.tmdb_client import fetch_show_details
    details = fetch_show_details(tmdb_id)
    if details:
        fa = (details.get("first_air_date") or "")[:4]
        if fa.isdigit():
            return int(fa)
    return None
```

Call it (wrapped in `asyncio.to_thread`, since `fetch_show_details` blocks) right after each
`job.tmdb_id = …` assignment:
- main identify flow — `identification_coordinator.py:200` (signal = local `tmdb_signal`)
- staging-import flow — `:555` (signal = `getattr(analysis, "_tmdb_signal", None)`)
- re-identify / name-correction — `:830` (manual id, no signal → details fallback) and `:841`
  (re-search `signal`). This is the path `POST /jobs/{id}/re-identify` →
  `job_manager.re_identify_job` reaches, so re-identify is covered here — **no routes.py change**.

> **Correction (vs. earlier draft):** `candidates_json` *does* exist — it was added by the
> #287/#288 same-name-collision work and persists `{tmdb_id, name, year, popularity}` for every
> same-name twin (classifier.py builds the dict at ~199-203; `identification_coordinator` sets the
> column at `:202` / `:557`). So the Frasier case resolves its year with **zero extra network** via
> the fast path. No `DiscAnalysisResult`/Analyst/classifier change is needed. (The earlier draft's
> claim that `candidates_json` didn't exist and that re-identify lived in `routes.py:1854` was based
> on a stale checkout; `routes.py:1851` is the unrelated bootstrap-library response builder.)

Reading directly off the persisted `job.tmdb_year` at organize time means **no per-sweep TMDB
fetch** and a stable folder name.

### 3. organizer.py — disambiguated folder + filename

New helper, mirroring `format_movie_folder`:

```python
def format_tv_show_folder(fmt: str, show: str, year: int | None, tmdb_id: str | int | None) -> str:
    """Format the show *directory* name. Strips empty (), {..-}, [..-] groups
    when year/tmdb_id are missing, then collapses whitespace. Sanitized."""
```

Graceful-strip helper removes, after formatting with empty strings for missing values:
- empty parens `\(\s*\)`
- empty Plex tag `\{[^{}]*-\s*\}`  (e.g. `{tmdb-}`)
- empty Jellyfin tag `\[[^\[\]]*-\s*\]`  (e.g. `[tmdbid-]`)

then `re.sub(r"\s+", " ", x).strip()`. Degradation ladder:
- year + id → `Frasier (1993) {tmdb-3452}`
- id only (no year) → `Frasier {tmdb-3452}`  ← still disambiguated; id is the stable token
- neither → `Frasier`  ← bare name == current behavior

`format_episode_filename` gains `year`/`tmdb_id` params and the same empty-`()` strip, so an
opt-in episode format yields `Frasier (1993) - S01E02` (year missing → `Frasier - S01E02`).

`organize_tv_episode` and `organize_tv_extras` BOTH compute the show directory via
`format_tv_show_folder(cfg.naming_tv_show_format, show, year, tmdb_id)` — a single source of
truth so episodes and their `Extras/` always share one folder (the
`project_tv_organize_paths_sync` hazard). Both gain a `year` param; `organize_tv_extras` also
gains `tmdb_id`. `TVOrganizer.organize` forwards `year` (and `tmdb_id`, already present) to
`organize_tv_episode`.

- Extras **folder** is disambiguated (required, so extras sit under the same show folder).
- Extras **filename** stays `"{clean_show} Disc N Extra ..."` — non-colliding inside the now-unique
  folder; left as-is to keep the change focused.

### 4. finalization_coordinator — three call sites in sync

All three TV-organize sites pass `year=_tmdb_year`; the two `organize_tv_extras` calls also gain
`tmdb_id=_tmdb_id_str` so the Extras folder matches the episode folder. Worktree line numbers:
- auto-flow `finalize_disc_job` — preamble `:814-815`; extras `:839`, episode `:850`, `tv_organizer.organize` `:861`
- `_finalize_tv_if_resolved` review path — preamble `:1173-1174`; extras `:1192`, episode `:1205`, `tv_organizer.organize` `:1216`
- `process_matched_titles` — preamble `:1359-1360`; extras `:1378`, episode `:1391`, `tv_organizer.organize` `:1402`

(`_tmdb_id_str` is already computed at each preamble; add `_tmdb_year = job.tmdb_year` beside it.)

## Testing

**organizer unit tests** (`tests/unit/`):
- Same-name twins (3452 vs 195241) with a disambiguating format → distinct folders, no conflict.
- Missing year → `Frasier {tmdb-3452}` (no empty parens); missing year **and** id → bare `Frasier`.
- Filename year strip: opt-in episode format with missing year → `Frasier - S01E02` (no `()`).
- Plex `{{tmdb-{tmdb_id}}}` and Jellyfin `[tmdbid-{tmdb_id}]` formats both render correctly.
- Default `"{show}"` reproduces today's exact path (regression guard).
- `organize_tv_extras` lands under the SAME disambiguated folder as `organize_tv_episode`.

**pipeline org-path tests** (`tests/pipeline/test_organization_paths.py`): update expectations;
add a same-name-twins coexistence case.

**Validation/commands:** `uv run ruff check .`; organizer + finalization + pipeline suites.
(Worktree note from memory: pipeline org-path tests need `init_db()` against the worktree DB.)

## Out of scope (noted only)

- Runtime SRT scrape cache `~/.engram/cache/data/<show_name>/` is still **name-keyed** (only the
  precomputed corpus was re-keyed by `tmdb_id` in #288), so same-name shows still collide there.
  Separately tracked follow-up — not fixed here.
- Episode **matching** of same-name shows (PR #287/#288 territory) — unchanged.
- Migrating/relocating users' existing bare `Frasier/` libraries — intentionally not done
  (opt-in default avoids touching them).
