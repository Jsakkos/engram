# `{episode_title}` placeholder for the episode filename format (#826)

Issue: Jsakkos/engram#826 (from Discord, reporter BurgerMage27). The reporter wants an optional
episode title in the TV file name (`Show - S01E01 - Pilot.mkv`) so names can be checked against
TinyMediaManager. A follow-up comment asks that a track holding several episodes read
`Title 1 & Title 2`, and that the separator be legal in file names on Windows, Linux and macOS.

## Triage findings

### Where the format is rendered

- `app/core/organizer.py::format_episode_filename` is the only renderer. It runs
  `fmt.format_map({...})` (unknown placeholder raises `KeyError`, caught, falls back to the
  default format), then `_strip_empty_name_groups`, then `_append_extra_episodes`, then
  `sanitize_filename`.
- `ALLOWED_EPISODE_PLACEHOLDERS` (same file) is what `validate_naming_format` and the settings
  export/import preview check against (`app/api/config_models.py`). Adding the name there is the
  whole validation change.
- `organize_tv_episode` is the only caller of the renderer. `TVOrganizer.organize` forwards to it.
- Default format (`{show} - S{season:02d}E{episode:02d}`) never references the new placeholder, so
  its output is byte-identical and no lookup happens (no network call unless the format asks).

### Where "empty placeholder" collapse lives, and why it is not enough

`_strip_empty_name_groups` removes empty `()`, `{x-}` and `[x-]` groups plus the whitespace before
them. It does NOT remove a dangling separator: `"{show} - S01E01 - {episode_title}"` with no title
renders `"Show - S01E01 - "`, and the final `strip(". ")` would only hide that when the title is
last. A title in the middle (`"{show} - {episode_title} - S01E01"`) would leave `"Show -  - S01E01"`.
So the title needs its own collapse rule (below).

### Where the title comes from

`app/core/episode_namespace.py::season_episodes(tmdb_show_id, season, api_key)` already returns
the season roster **in the current namespace**: TMDB under `tmdb`, the TheTVDB roster under
`tvdb` (and `[]`, never a TMDB fallback, when TheTVDB is unreachable). Each entry has
`episode_number` and `name`. The namespace rides in a ContextVar, and the finalization sweeps do
not bind it (they run `organize_tv_episode` via `asyncio.to_thread` with whatever context the sweep
has, default `tmdb`). So the organizer must be told the job's `episode_namespace` explicitly and
bind it with `namespace_context` around the lookup, otherwise a tvdb job would get TMDB titles for
TVDB-numbered codes. `episode_namespace=None` means "keep the ambient namespace".

The code passed to `organize_tv_episode` is the CANONICAL code in the job's namespace, so the
lookup uses it as-is. The `ordering` projection (#200 episode groups) only renumbers the filename;
the title belongs to the canonical episode, so it is looked up from the canonical season/episodes,
not the projected ones.

`fetch_season_episodes` is not cached (only season episode COUNTS are). One HTTP call per title,
and with TMDB down each call can burn the 30 s timeout, so a 24-episode sweep could stall for
minutes. A small in-process roster cache (10 min for a roster, 1 min for a miss) lives next to the
lookup helper. A miss is cached briefly on purpose: it turns "TMDB is down" into one timeout per
season per minute instead of one per file.

### Multi-episode tracks

`parse_episode_code` (never a local regex) yields the canonical episode numbers. Titles are looked
up per episode and joined with `" & "`. `&` is legal on Windows, Linux and macOS (it needs no
escaping in the library layer; only shell quoting, which Engram never does). Rule, kept simple:

1. Take the roster title of every episode in the code, in order.
2. Drop an episode with no title (partial coverage is still informative; all missing = empty).
3. Drop exact duplicates, compared case-insensitively with whitespace collapsed. Two-part
   episodes whose parts share a base name read once; titles that merely look alike
   (`"Secret Origins (1)"` / `"(2)"`) are different strings and are both kept.
4. Join with `" & "`.

### Sanitizing and length

- Reuse `organizer.sanitize_filename` per title (removes `<>:"/\|?*`, strips leading/trailing dots
  and spaces). That is the same treatment show names and movie titles get, so
  `"Who: What?"` becomes `"Who What"`. Additionally: control characters are dropped (one of them is
  the template sentinel, below) and runs of whitespace left by removed characters are collapsed.
- Cap: 100 characters of the joined title, then trailing separators/spaces are trimmed. A flat cap is
  enough to keep show + season + file inside Windows' 260-character path budget for ordinary
  library roots, and the cap is applied to the joined text so `A & B & C` cannot dodge it.

### The `_append_extra_episodes` trap

`_append_extra_episodes` splices `-E03` after the LAST regex match of `E0*<n>` in the rendered
stem. A title such as `"E1 Rising"` or `"Fate E01"` would be matched and the range would be spliced
into the title. So the title is inserted last: the format renders with a one-character sentinel
(`\x00`) standing in for `{episode_title}`, the strip/collapse/splice steps run on the sentinel
form, and the sanitized title replaces the sentinel at the end. A sentinel is also what lets the
empty-title rule know exactly where the placeholder sat.

### Empty-title collapse rule

When the title is empty the sentinel is removed with its surroundings:

- a group holding only the placeholder (`( )`, `[ ]`, Plex `{ep-...}`, Jellyfin `[ep-...]`) goes
  entirely, with the whitespace before it;
- otherwise, if real text precedes the placeholder, the separator run before it
  (`space - _ . ,`) goes with it, so `"Show - S01E01 - "` becomes `"Show - S01E01"`;
- if nothing precedes it (title first), the separator run after it goes instead.

This runs BEFORE the `-E03` splice, so the range still lands after the episode token.

### Rename paths that must stay in sync

All TV episode renames go through `organize_tv_episode`, so the format change is in ONE place.
The only per-site work is passing the job's namespace. Sites (grep `organize_tv_episode|tv_organizer.organize`):

| Site | Notes |
|------|-------|
| `finalization_coordinator.py` main organize sweep (~L1577/L1590) | has `_namespace = job.episode_namespace` already |
| `finalization_coordinator.py::_finalize_tv_if_resolved` (~L2300/L2313) | `job.episode_namespace` |
| `finalization_coordinator.py::process_matched_titles` (~L2535/L2548) | `job.episode_namespace` |
| `job_manager.py::amend_title_assignment` (~L2710) | amends a COMPLETED title, `job.episode_namespace` |
| `TVOrganizer.organize` / `organize_batch` | forward the new kwarg |

Not affected: `organize_tv_extras` (fixed `... Extra tNN` naming, no format), movies
(`format_movie_*`, `already_clean` compares the TMDB title and has nothing to do with episodes),
the show and season folder formats (their placeholder sets do not include the title).

Known side effect: `app/scripts/bootstrap_library.py::EP_REGEX` requires `SnnEnn` to run straight
into the extension, so library files with a title are reported unparseable and skipped. That is the
script's existing behavior for any custom format (it already skips `S01E01-E03` and `{year}`
suffixes) and loosening it would risk feeding fingerprint contributions bad data. Documented, not
changed.

Another: changing the format does not rename already-filed episodes, and conflict detection is by
exact stem, so re-organizing an episode after enabling the placeholder files a second copy beside
the old name rather than a conflict. Same as any other format change.

### Config three-way sync

No new `AppConfig` field. `naming_episode_format` already exists in `AppConfig`, `ConfigUpdate`,
`ConfigResponse` and the ConfigWizard. Only the allowed-placeholder set, the wizard help text and
the wizard preview change.

## Implementation plan

1. **Tests first** (`tests/unit/test_naming_conventions.py`, new `tests/unit/test_episode_titles.py`):
   rendering with a title, empty title (end / middle / start / bracketed), sanitization,
   length cap, title that looks like an episode token, multi-episode splice, validation accepts
   the placeholder, default format unchanged; title lookup for tmdb, tvdb (namespace bound, TVDB
   roster used), unknown namespace default, missing roster, partial coverage, duplicate collapse,
   cache behavior; `organize_tv_episode` end to end incl. `E01-E02` combined track.
2. `app/core/episode_titles.py`: roster cache + `episode_title_for_code`.
3. `organizer.py`: `episode_title` placeholder, sentinel render, collapse, title sanitize/cap,
   `episode_namespace` kwarg on `organize_tv_episode` and `TVOrganizer.organize`; lookup only when the
   format string references the placeholder.
4. Pass `episode_namespace` at the four call sites; tests that each site forwards it.
5. ConfigWizard: help text lists `{episode_title}`, preview renders every placeholder (vitest).
6. `docs/getting-started/configuration.md` TV naming table + example; CHANGELOG `[Unreleased]`.
