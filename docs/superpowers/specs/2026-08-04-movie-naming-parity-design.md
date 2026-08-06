# Movie Naming Parity (issue #576)

**Date:** 2026-08-04
**Issue:** [#576](https://github.com/Jsakkos/engram/issues/576) (Feature Request: Movie Folder Formatting like Series Formatting)
**Status:** Design approved, ready for implementation planning

## Problem

The reporter asks for movie folder naming customization matching what TV already has, and separately for a tool that reviews and renames an existing media library via TMDB lookup.

Investigation found the TV/movie asymmetry is deeper than a missing placeholder. Movies have four distinct defects, all sharing one root cause.

### Root cause

TV threads identity through **typed keyword arguments**: `organize_tv_episode(..., year=, tmdb_id=, ordering=)` feeds `format_tv_show_folder()` directly. Movies squeeze every piece of identity into a single `movie_name: str`, which then flows through `clean_movie_name()`, a normalizer built for disc volume labels (`THE_SOCIAL_NETWORK` to `The Social Network`) that strips hyphens and force-title-cases.

Any structured token hidden inside that string gets flattened, and anything with no channel at all (year) is simply never passed.

### The four defects

**1. No movie naming UI exists.** The naming step of `frontend/src/components/ConfigWizard.tsx` (lines 1606 to 1690) renders only TV controls: Naming Convention preset, Show Folder Format, Season Folder Format, Episode Filename Format. `namingMovieFormat` is read from the API at line 326 and written back at line 511 but never rendered. It is a dead round-trip. This is precisely what the reporter observed.

**2. `{year}` is dead in production.** Every production call site passes `year=None`:

- `backend/app/services/finalization_coordinator.py:1454` has a literal `None,  # year`
- `backend/app/services/job_manager.py:2990` calls `movie_organizer.organize(...)` without it

Only pipeline tests pass a year. The shipped default `{title} ({year})` therefore strips its own empty parens and every movie lands as a bare `Blade Runner/`.

The upstream cause: `job.tmdb_year` is populated by `_resolve_show_year()` (`identification_coordinator.py:162`), which calls `fetch_show_details`, the **TV** endpoint. Called unconditionally at lines 378, 890, and 972 regardless of content type, so a movie job queries `/tv/{movie_id}` with a movie id. That either returns nothing or silently returns an unrelated TV show's first-air year. Harmless today because the value is unused for movies; it becomes a correctness bug the moment `{year}` goes live.

Meanwhile `_make_movie_signal()` (`tmdb_classifier.py:421`) receives a TMDB search result that already carries `release_date` and discards it.

**3. The Plex edition tag is mangled.** `finalization_coordinator.py:1446` builds the tag by string concatenation into the title:

```python
final_title = f"{final_title} {{edition-{edition}}}"
```

which then flows through `clean_movie_name()`. Verified:

```
input:  Blade Runner {edition-Final Cut}
actual: Blade Runner {Edition Final Cut}
```

Plex cannot parse the result. The tag also lands on the folder, where Plex does not look for it.

**4. `clean_movie_name()` corrupts canonical TMDB titles.** Applied unconditionally at `organizer.py:380`, including to titles TMDB returned already clean. Verified:

```
'Se7en'                    -> 'Se7En'
'WALL-E'                   -> 'Wall E'
'Spider-Man: No Way Home'  -> 'Spider Man: No Way Home'
"Ocean's Eleven"           -> "Ocean'S Eleven"
```

### Current state table

| Layer | TV | Movies |
|---|---|---|
| Folder format | `naming_tv_show_format`: `{show}`, `{year}`, `{tmdb_id}` | `naming_movie_format`: `{title}`, `{year}` (year non-functional) |
| Filename format | `naming_episode_format`: 5 placeholders | none; folder name reused verbatim (`organizer.py:385`) |
| Settings UI | 4 controls with live preview | none |

## Scope

**In scope:** movie naming parity, plus all four defects above.

**Out of scope, sketched only:** the library review and rename tool. There is no library-scan capability anywhere in the codebase (verified by grep). Building it means a filesystem walker, a reverse-parser for arbitrary existing names, TMDB disambiguation UI, a dry-run diff, and destructive batch renames against a user's real media with no undo. That is its own multi-phase feature. A non-binding outline is appended.

## Design

Four layers, bottom up. Layer 1 is pure and independently testable; layers 2 and 3 need the DB.

### Layer 1: renderer (`backend/app/core/organizer.py`)

```python
ALLOWED_MOVIE_PLACEHOLDERS      = {"title", "year", "tmdb_id"}
ALLOWED_MOVIE_FILE_PLACEHOLDERS = {"title", "year", "tmdb_id", "edition"}

def format_movie_folder(fmt, title, year, tmdb_id) -> str: ...
def format_movie_filename(fmt, title, year, tmdb_id, edition) -> str: ...
```

Both use `format_map` (not `format(**...)`) so an unknown placeholder raises `KeyError` into the safe fallback, matching the existing rationale documented at `format_season_folder`, and keeping CodeQL's missing-named-argument check quiet on the user-controlled format string.

Both route through the existing `_strip_empty_name_groups()`, which already collapses empty `()`, `{tmdb-}`, and `[tmdbid-]`, so an absent edition collapses `{edition-}` for free. `format_movie_folder` currently carries its own trailing-only paren regex (`organizer.py:86`); that is replaced by the shared helper so folder and filename behave identically.

`organize_movie()` signature:

```python
def organize_movie(
    staging_dir, movie_name, year=None, library_path=None,
    conflict_resolution="ask", move_extras=True,
    *, tmdb_id=None, edition=None, already_clean=False,
) -> dict: ...
```

Behavior changes inside:

- `clean_movie_name()` is skipped when `already_clean=True`
- the folder comes from `naming_movie_format`
- the filename comes from `naming_movie_file_format`, falling back to the folder format when that setting is blank or whitespace-only (mirroring the falsy-format fallback in `format_tv_show_folder`)

`MovieOrganizer.organize()` forwards the new keyword arguments.

### Layer 2: year resolution

- `_make_movie_signal()` captures `release_date` from the search result it already holds, onto `TmdbSignal`. No extra network call.
- `_resolve_movie_year(tmdb_id, signal)` mirrors `_resolve_show_year`: signal fast path first, `fetch_movie_details()` fallback, `None` when unknown.
- The three `job.tmdb_year = ...` sites in `identification_coordinator.py` (378, 890, 972) branch on content type. This also closes the `/tv/{movie_id}` mislookup.

`job.tmdb_year` is reused for movie release year rather than adding a column. Its docstring is updated to say "first-air year for shows, release year for movies."

### Layer 3: threading (two call sites)

`finalization_coordinator.apply_review` (around line 1444):

- delete the `{edition-...}` string concat
- pass `year=job.tmdb_year, tmdb_id=job.tmdb_id, edition=title.edition, already_clean=bool(job.tmdb_name)`

`job_manager` (around line 2990): same values through `MovieOrganizer.organize`.

`already_clean` is derived from `bool(job.tmdb_name)`: a title that came back from TMDB is canonical and must not be re-normalized; a title derived from a volume label still needs cleaning.

### Layer 4: config and UI

New field `naming_movie_file_format`, through the full four-way sync (a field missed in any one of these is silently dropped by Pydantic):

| File | Change |
|---|---|
| `backend/app/models/app_config.py` | new field with `server_default` so existing DBs get the real default, not the `_add_missing_columns` empty-string fallback |
| `backend/app/api/routes.py` (~300) | `ConfigResponse` |
| `backend/app/api/routes.py` (~389) | `ConfigUpdate` |
| `backend/app/api/routes.py` (~1569) | response construction |
| `backend/app/api/routes.py` (~1712) | validation tuple: `("naming_movie_file_format", ALLOWED_MOVIE_FILE_PLACEHOLDERS)`, and widen the existing `naming_movie_format` entry |
| `frontend/src/components/ConfigWizard.tsx` | state shape, defaults, load, save |

UI: a Movies subsection in the naming step mirroring the TV block, with two text inputs (Movie Folder Format, Movie Filename Format), a placeholder hint naming the Plex and Jellyfin conventions, and a live preview line in the style of the TV preview at line 1632.

### Defaults

Chosen so a movie with no edition renders byte-identically to today's inherit behavior.

| Setting | Default |
|---|---|
| `naming_movie_format` (folder) | `{title} ({year})` (unchanged) |
| `naming_movie_file_format` | `{title} ({year}) {{edition-{edition}}}` |

Rendered results:

```
no edition:   Blade Runner (1982)/Blade Runner (1982).mkv
with edition: Blade Runner (1982)/Blade Runner (1982) {edition-Final Cut}.mkv
```

The edition moves from the folder (where it was mangled and where Plex does not look) to the filename (Plex's documented location).

### Migration and behavior change

Fixing `{year}` changes movie folder layout on upgrade: existing rips remain in `Blade Runner/`, new rips land in `Blade Runner (1982)/`.

Decision: ship it, with a prominent CHANGELOG note. The shipped default already reads `{title} ({year})`; users configured and expected that behavior, and making it work is the bug fix. Users who prefer bare folders can set `naming_movie_format` to `{title}`, which is now reachable because the UI field exists.

No automatic reconciliation of existing folders. That is squarely the deferred (B) feature.

## Testing

**Pipeline** (`backend/tests/pipeline/test_organization_paths.py::TestMovieOrganizationPaths`):

- folder with `{tmdb_id}` renders the Plex and Jellyfin tag forms
- edition present renders `{edition-Final Cut}` verbatim, unmangled
- edition absent leaves no empty `{edition-}` and no double space
- a TMDB title survives intact with `already_clean=True` (`Spider-Man: No Way Home` keeps its hyphen)
- a volume label still normalizes with `already_clean=False` (`THE_SOCIAL_NETWORK`)
- blank `naming_movie_file_format` inherits the folder format
- default config on a no-edition movie produces the same path as before this change

**Unit:** `format_movie_folder` and `format_movie_filename` in isolation, including unknown-placeholder fallback and path-traversal rejection via `validate_naming_format`.

**Regression guard:** a movie job must not call `fetch_show_details`.

Placement note: `tests/integration/test_workflow.py` runs against the real app DB, and tests there that organize files will move them into the real configured library. Keep new movie-organize tests in the pipeline tier using `tmp_path`, as the existing `TestMovieOrganizationPaths` cases already do.

## Documentation

- `docs/getting-started/configuration.md` line 191 has a "Naming Conventions" section describing patterns but not the format strings. Add the movie placeholder table alongside the TV one.
- `CHANGELOG.md` `[Unreleased]`: one `### Added` entry for the movie naming controls, and `### Fixed` entries for the year, edition, and TMDB-title defects. The folder-layout change gets explicit callout text since the curated changelog becomes the release notes.

## Appendix: sketch of the library review and rename tool (not scoped)

Non-binding outline, recorded so the issue has a visible path and so the ordering rationale is not lost.

- **Phase 1, read only.** Walk the configured library paths, parse existing folder and file names, query TMDB, and produce a match report. No writes at all. This alone answers "what in my library is misnamed," which is most of the reporter's stated value.
- **Phase 2, review UI.** A table of current name, proposed name, confidence, and an override control. The proposed-name column is rendered by the Layer 1 formatters from this spec, which is why (A) is a genuine prerequisite rather than arbitrary sequencing.
- **Phase 3, apply.** Opt-in batch rename behind a reversible journal (record every source and destination pair so a single action can undo the batch). This phase touches a user's real media with no filesystem-level undo, so it needs its own design pass covering dry-run defaults, partial-failure recovery, and what happens when a media server has the files open.
