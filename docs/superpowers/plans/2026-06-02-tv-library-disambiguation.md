# TV Library Disambiguation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give TV library organization the same year/tmdb-id folder disambiguation movies already get, so same-name shows (Frasier 1993 #3452 vs the 2023 revival #195241) coexist on disk instead of colliding.

**Architecture:** A new opt-in config format string (`naming_tv_show_format`, default `"{show}"` = current behavior) drives a `format_tv_show_folder` helper that both `organize_tv_episode` and `organize_tv_extras` use to build the show directory. The first-air year is persisted on `DiscJob.tmdb_year` at identification time (no-network fast path via the existing `candidates_json`, cached-TMDB fallback) and threaded to the organizer alongside the already-threaded `tmdb_id`. Three finalization call sites are updated in lockstep.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/SQLite, pytest; React 18 + TypeScript (ConfigWizard). Backend commands use `uv run`.

**Spec:** `docs/superpowers/specs/2026-06-01-tv-library-disambiguation-design.md`

**Working dir note:** All paths are repo-relative. Run backend commands from `backend/`, frontend from `frontend/`. This branch is a worktree; edit only within it.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `backend/app/core/organizer.py` | Naming/format helpers + TV organize functions | Add placeholder sets, `_strip_empty_name_groups`, `format_tv_show_folder`; extend `format_episode_filename`; thread `year`/`tmdb_id` through `organize_tv_episode`, `organize_tv_extras`, `TVOrganizer.organize` |
| `backend/app/models/app_config.py` | Persisted config | Add `naming_tv_show_format` (with `server_default`) |
| `backend/app/api/routes.py` | Config REST + validation | Add field to `ConfigResponse`, GET constructor, `ConfigUpdate`, and the format-validation table |
| `backend/app/models/disc_job.py` | Job state | Add `tmdb_year` column |
| `backend/app/services/identification_coordinator.py` | Identify → persist metadata | Add `_resolve_show_year`; set `job.tmdb_year` at 3 sites |
| `backend/app/services/finalization_coordinator.py` | Organize matched titles | Pass `year`/`tmdb_id` at 3 TV-organize sites |
| `frontend/src/components/ConfigWizard.tsx` | Settings UI | Add Show Folder Format field (interface, defaults, read/write, input) |
| `backend/tests/unit/test_organizer.py` | Organizer unit tests | New helper + disambiguation tests |
| `backend/tests/unit/test_resolve_show_year.py` | Year-resolver unit test | New file |
| `backend/tests/pipeline/test_organization_paths.py` | Pipeline path tests | Coexistence test + default-unchanged guard |

---

## Task 1: Organizer naming helpers + placeholder sets

**Files:**
- Modify: `backend/app/core/organizer.py:15-17` (placeholder sets), `:29-35` (`format_episode_filename`), after `:46` (new helpers)
- Test: `backend/tests/unit/test_organizer.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_organizer.py`:

```python
class TestNamingHelpers:
    """format_tv_show_folder, widened placeholders, episode-filename year."""

    def test_show_folder_plex_full(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", 1993, "3452") == "Frasier (1993) {tmdb-3452}"

    def test_show_folder_jellyfin_full(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) [tmdbid-{tmdb_id}]"
        assert (
            format_tv_show_folder(fmt, "Frasier", 2023, "195241")
            == "Frasier (2023) [tmdbid-195241]"
        )

    def test_show_folder_missing_year_keeps_id(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", None, "3452") == "Frasier {tmdb-3452}"

    def test_show_folder_jellyfin_missing_id_strips_tag(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) [tmdbid-{tmdb_id}]"
        assert format_tv_show_folder(fmt, "Frasier", 1993, None) == "Frasier (1993)"

    def test_show_folder_missing_both_is_bare(self):
        from app.core.organizer import format_tv_show_folder

        fmt = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        assert format_tv_show_folder(fmt, "Frasier", None, None) == "Frasier"

    def test_show_folder_default_is_bare(self):
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("{show}", "Frasier", 1993, "3452") == "Frasier"

    def test_show_folder_empty_format_falls_back_to_bare(self):
        # Existing DBs may have backfilled '' for this column; degrade, don't break.
        from app.core.organizer import format_tv_show_folder

        assert format_tv_show_folder("", "Frasier", 1993, "3452") == "Frasier"

    def test_episode_filename_with_year(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} ({year}) - S{season:02d}E{episode:02d}", "Frasier", 1, 2, year=1993
        )
        assert out == "Frasier (1993) - S01E02"

    def test_episode_filename_year_missing_strips_parens(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename(
            "{show} ({year}) - S{season:02d}E{episode:02d}", "Frasier", 1, 2, year=None
        )
        assert out == "Frasier - S01E02"

    def test_episode_filename_default_unchanged(self):
        from app.core.organizer import format_episode_filename

        out = format_episode_filename("{show} - S{season:02d}E{episode:02d}", "Frasier", 1, 2)
        assert out == "Frasier - S01E02"

    def test_placeholder_sets_validate(self):
        from app.core.organizer import (
            ALLOWED_EPISODE_PLACEHOLDERS,
            ALLOWED_TV_SHOW_PLACEHOLDERS,
            validate_naming_format,
        )

        assert ALLOWED_TV_SHOW_PLACEHOLDERS == {"show", "year", "tmdb_id"}
        assert {"year", "tmdb_id"} <= ALLOWED_EPISODE_PLACEHOLDERS
        assert (
            validate_naming_format(
                "{show} ({year}) {{tmdb-{tmdb_id}}}", ALLOWED_TV_SHOW_PLACEHOLDERS
            )
            is None
        )
        assert (
            validate_naming_format(
                "{show} ({year}) [tmdbid-{tmdb_id}]", ALLOWED_TV_SHOW_PLACEHOLDERS
            )
            is None
        )
        assert (
            validate_naming_format(
                "{show} ({year}) - S{season:02d}E{episode:02d}", ALLOWED_EPISODE_PLACEHOLDERS
            )
            is None
        )
        # Unknown placeholder still rejected.
        assert validate_naming_format("{bogus}", ALLOWED_TV_SHOW_PLACEHOLDERS) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_organizer.py::TestNamingHelpers -q`
Expected: FAIL with `ImportError: cannot import name 'format_tv_show_folder'` (and `ALLOWED_TV_SHOW_PLACEHOLDERS`).

- [ ] **Step 3: Add the placeholder sets**

In `backend/app/core/organizer.py`, replace lines 15-17:

```python
# Allowed placeholders for naming format strings
ALLOWED_TV_PLACEHOLDERS = {"show", "season", "episode"}
ALLOWED_MOVIE_PLACEHOLDERS = {"title", "year"}
```

with:

```python
# Allowed placeholders for naming format strings
ALLOWED_TV_PLACEHOLDERS = {"show", "season", "episode"}  # season folder format
# Show *folder* format — adds year/tmdb_id for same-name disambiguation
# (Plex "{tmdb-NNNN}" / Jellyfin "[tmdbid-NNNN]").
ALLOWED_TV_SHOW_PLACEHOLDERS = {"show", "year", "tmdb_id"}
# Episode *filename* format — widened so the year can opt into the filename too.
ALLOWED_EPISODE_PLACEHOLDERS = {"show", "season", "episode", "year", "tmdb_id"}
ALLOWED_MOVIE_PLACEHOLDERS = {"title", "year"}
```

- [ ] **Step 4: Extend `format_episode_filename`**

Replace lines 29-35:

```python
def format_episode_filename(fmt: str, show: str, season: int, episode: int) -> str:
    """Format an episode filename from a config format string."""
    try:
        result = fmt.format(show=show, season=season, episode=episode)
    except (KeyError, ValueError, IndexError):
        result = f"{show} - S{season:02d}E{episode:02d}"
    return sanitize_filename(result)
```

with:

```python
def format_episode_filename(
    fmt: str,
    show: str,
    season: int,
    episode: int,
    *,
    year: int | None = None,
    tmdb_id: str | int | None = None,
) -> str:
    """Format an episode filename from a config format string.

    ``year``/``tmdb_id`` are optional placeholders ({year}, {tmdb_id}). When the
    chosen format omits them they are ignored; when year is missing, an empty
    ``()`` left behind is stripped (mirrors ``format_movie_folder``). The default
    format ("{show} - SxxExx") is unaffected.
    """
    try:
        result = fmt.format(
            show=show,
            season=season,
            episode=episode,
            year=year or "",
            tmdb_id=tmdb_id or "",
        )
    except (KeyError, ValueError, IndexError):
        result = f"{show} - S{season:02d}E{episode:02d}"
    result = re.sub(r"\(\s*\)", "", result)
    result = re.sub(r"\s+", " ", result).strip()
    return sanitize_filename(result)
```

- [ ] **Step 5: Add `_strip_empty_name_groups` and `format_tv_show_folder`**

Insert after `format_movie_folder` (after line 46, before `def validate_naming_format`):

```python
def _strip_empty_name_groups(name: str) -> str:
    """Remove empty (), {..-}, [..-] groups left when year/tmdb_id are absent.

    e.g. "Frasier () {tmdb-}" -> "Frasier". A populated tag like "{tmdb-3452}"
    is preserved (the char before '}' is a digit, not '-').
    """
    name = re.sub(r"\(\s*\)", "", name)  # empty parens
    name = re.sub(r"\{[^{}]*-\s*\}", "", name)  # empty Plex tag, e.g. {tmdb-}
    name = re.sub(r"\[[^\[\]]*-\s*\]", "", name)  # empty Jellyfin tag, e.g. [tmdbid-]
    return re.sub(r"\s+", " ", name).strip()


def format_tv_show_folder(
    fmt: str, show: str, year: int | None, tmdb_id: str | int | None
) -> str:
    """Format the show *directory* name from a config format string.

    Mirrors ``format_movie_folder`` but adds a ``{tmdb_id}`` placeholder for
    media-server disambiguation (Plex ``{tmdb-NNNN}`` / Jellyfin ``[tmdbid-NNNN]``).
    Empty groups are stripped when year/id are missing, so the stable id tag never
    degrades to ``Frasier {tmdb-}``. A falsy/empty ``fmt`` (e.g. an existing DB that
    backfilled '') falls back to the bare show name == current behavior.
    """
    if not fmt:
        return sanitize_filename(show)
    try:
        result = fmt.format(show=show, year=year or "", tmdb_id=tmdb_id or "")
    except (KeyError, ValueError, IndexError):
        result = show
    return sanitize_filename(_strip_empty_name_groups(result))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_organizer.py::TestNamingHelpers -q`
Expected: PASS (all 11 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/organizer.py backend/tests/unit/test_organizer.py
git commit -m "feat(organizer): add format_tv_show_folder + year/tmdb_id placeholders"
```

---

## Task 2: Add `naming_tv_show_format` config field (model)

**Files:**
- Modify: `backend/app/models/app_config.py:108-111`

- [ ] **Step 1: Add the field with a server_default**

In `backend/app/models/app_config.py`, replace lines 108-111:

```python
    # Naming conventions (Python format strings)
    naming_season_format: str = "Season {season:02d}"
    naming_episode_format: str = "{show} - S{season:02d}E{episode:02d}"
    naming_movie_format: str = "{title} ({year})"
```

with:

```python
    # Naming conventions (Python format strings)
    naming_season_format: str = "Season {season:02d}"
    naming_episode_format: str = "{show} - S{season:02d}E{episode:02d}"
    naming_movie_format: str = "{title} ({year})"
    # Show *folder* format. Default "{show}" == today's bare-name behavior so
    # existing libraries are untouched. server_default ensures EXISTING DBs get
    # "{show}" (not the _add_missing_columns String fallback of '') when the
    # column is added. Opt into disambiguation with e.g.
    # "{show} ({year}) {{tmdb-{tmdb_id}}}" (Plex) or
    # "{show} ({year}) [tmdbid-{tmdb_id}]" (Jellyfin).
    naming_tv_show_format: str = Field(
        default="{show}", sa_column_kwargs={"server_default": text("'{show}'")}
    )
```

> `Field` and `text` are already imported in this file (used by `episode_ordering_preference` at line ~118). Confirm with: `grep -nE "^from sqlmodel|import text|from sqlalchemy" backend/app/models/app_config.py`.

- [ ] **Step 2: Verify the model imports and default cleanly**

Run: `uv run python -c "from app.models.app_config import AppConfig; c=AppConfig(); print(repr(c.naming_tv_show_format))"`
Expected: `'{show}'`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/app_config.py
git commit -m "feat(config): add naming_tv_show_format (default {show}, server_default)"
```

---

## Task 3: Thread year/tmdb_id through the TV organize functions

**Files:**
- Modify: `backend/app/core/organizer.py` — `organize_tv_episode` (~363-447), `organize_tv_extras` (~472-523), `TVOrganizer.organize` (~549-572)
- Test: `backend/tests/unit/test_organizer.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_organizer.py`:

```python
class TestTVDisambiguation:
    """End-to-end folder building with the disambiguating format."""

    @staticmethod
    def _patch_cfg(**over):
        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    def test_same_name_twins_land_in_distinct_folders(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            s1 = tmp_path / "a.mkv"
            s1.write_bytes(b"x")
            r1 = organize_tv_episode(
                s1, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
            s2 = tmp_path / "b.mkv"
            s2.write_bytes(b"x")
            r2 = organize_tv_episode(
                s2, "Frasier", "S01E02", library_path=lib, tmdb_id="195241", year=2023
            )
        assert r1["success"] and r2["success"]
        assert "Frasier (1993) {tmdb-3452}" in str(r1["final_path"])
        assert "Frasier (2023) {tmdb-195241}" in str(r2["final_path"])
        assert r1["final_path"] != r2["final_path"]

    def test_default_format_keeps_bare_folder(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg():  # naming_tv_show_format defaults to "{show}"
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"] == lib / "Frasier" / "Season 01" / "Frasier - S01E02.mkv"

    def test_missing_year_keeps_id_no_empty_parens(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=None
            )
        assert r["success"]
        assert "Frasier {tmdb-3452}" in str(r["final_path"])
        assert "()" not in str(r["final_path"])

    def test_episode_filename_year_opt_in(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg(
            naming_tv_show_format="{show} ({year}) {{tmdb-{tmdb_id}}}",
            naming_episode_format="{show} ({year}) - S{season:02d}E{episode:02d}",
        ):
            s = tmp_path / "a.mkv"
            s.write_bytes(b"x")
            r = organize_tv_episode(
                s, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"].name == "Frasier (1993) - S01E02.mkv"

    def test_extras_share_show_folder_with_episode(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            ep = tmp_path / "e.mkv"
            ep.write_bytes(b"x")
            r_ep = organize_tv_episode(
                ep, "Frasier", "S01E02", library_path=lib, tmdb_id="3452", year=1993
            )
            ex = tmp_path / "x.mkv"
            ex.write_bytes(b"x")
            r_ex = organize_tv_extras(
                ex,
                "Frasier",
                season=1,
                library_path=lib,
                disc_number=1,
                title_index=3,
                tmdb_id="3452",
                year=1993,
            )
        assert r_ep["success"] and r_ex["success"]
        show_dir = str(lib / "Frasier (1993) {tmdb-3452}")
        assert str(r_ep["final_path"]).startswith(show_dir)
        assert str(r_ex["final_path"]).startswith(show_dir)
        assert "Extras" in str(r_ex["final_path"])
```

> `organize_tv_extras` is not yet imported in this test file — add it to the top-of-file import: `from app.core.organizer import (clean_movie_name, organize_movie, organize_tv_episode, organize_tv_extras, sanitize_filename)` (it currently imports `organize_tv_episode` and `sanitize_filename`). `patch` is already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_organizer.py::TestTVDisambiguation -q`
Expected: FAIL — `organize_tv_episode() got an unexpected keyword argument 'year'`.

- [ ] **Step 3: Update `organize_tv_episode`**

In `backend/app/core/organizer.py`, change the signature (lines 369-372). Replace:

```python
    *,
    tmdb_id: str | None = None,
    ordering: str = "aired",
    episode_group_id: str | None = None,
) -> dict:
```

with:

```python
    *,
    tmdb_id: str | None = None,
    ordering: str = "aired",
    episode_group_id: str | None = None,
    year: int | None = None,
) -> dict:
```

Then replace the folder/filename build block (lines 436-447):

```python
    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    season_folder = format_season_folder(cfg.naming_season_format, out_season)
    ep_stem = format_episode_filename(
        cfg.naming_episode_format, clean_show, out_season, out_episode
    )
    filename = f"{ep_stem}.mkv"

    # Build destination path
    library_path = Path(library_path)
    dest_dir = library_path / clean_show / season_folder
    dest_file = dest_dir / filename
```

with:

```python
    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    show_folder = format_tv_show_folder(cfg.naming_tv_show_format, clean_show, year, tmdb_id)
    season_folder = format_season_folder(cfg.naming_season_format, out_season)
    ep_stem = format_episode_filename(
        cfg.naming_episode_format,
        clean_show,
        out_season,
        out_episode,
        year=year,
        tmdb_id=tmdb_id,
    )
    filename = f"{ep_stem}.mkv"

    # Build destination path. The show folder may carry year/tmdb-id so same-name
    # shows (Frasier 1993 vs 2023) coexist; default "{show}" == bare clean_show.
    library_path = Path(library_path)
    dest_dir = library_path / show_folder / season_folder
    dest_file = dest_dir / filename
```

- [ ] **Step 4: Update `organize_tv_extras`**

Replace the signature (lines 472-480):

```python
def organize_tv_extras(
    source_file: Path,
    show_name: str,
    season: int,
    library_path: Path | None = None,
    disc_number: int = 1,
    extra_index: int = 1,
    title_index: int | None = None,
) -> dict:
```

with:

```python
def organize_tv_extras(
    source_file: Path,
    show_name: str,
    season: int,
    library_path: Path | None = None,
    disc_number: int = 1,
    extra_index: int = 1,
    title_index: int | None = None,
    *,
    year: int | None = None,
    tmdb_id: str | int | None = None,
) -> dict:
```

Then replace the folder build (lines 512-523):

```python
    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    season_folder = format_season_folder(cfg.naming_season_format, season)

    if title_index is not None:
        extra_name = f"{clean_show} Disc {disc_number} Extra t{title_index:02d}.mkv"
    else:
        extra_name = f"{clean_show} Disc {disc_number} Extra {extra_index}.mkv"

    # Build destination path
    library_path = Path(library_path)
    dest_dir = library_path / clean_show / season_folder / "Extras"
    dest_file = dest_dir / extra_name
```

with:

```python
    # Clean and sanitize names
    clean_show = sanitize_filename(show_name.strip())
    # Use the SAME disambiguated show folder as organize_tv_episode so an extra
    # and its episodes land under one show folder (TV-organize-paths-sync hazard).
    show_folder = format_tv_show_folder(cfg.naming_tv_show_format, clean_show, year, tmdb_id)
    season_folder = format_season_folder(cfg.naming_season_format, season)

    if title_index is not None:
        extra_name = f"{clean_show} Disc {disc_number} Extra t{title_index:02d}.mkv"
    else:
        extra_name = f"{clean_show} Disc {disc_number} Extra {extra_index}.mkv"

    # Build destination path
    library_path = Path(library_path)
    dest_dir = library_path / show_folder / season_folder / "Extras"
    dest_file = dest_dir / extra_name
```

- [ ] **Step 5: Update `TVOrganizer.organize` to forward `year`**

Replace the method signature and body (lines 549-572). Replace:

```python
    def organize(
        self,
        source_file: Path,
        show_name: str,
        episode_code: str,
        *,
        tmdb_id: str | None = None,
        ordering: str = "aired",
        episode_group_id: str | None = None,
    ) -> dict:
        """Organize a TV episode from staging to library.

        Forwards the output-ordering controls to organize_tv_episode so the
        library-mode path (no explicit library_path) also honors the chosen
        ordering. episode_code stays canonical; only the filename is projected.
        """
        return organize_tv_episode(
            source_file,
            show_name,
            episode_code,
            tmdb_id=tmdb_id,
            ordering=ordering,
            episode_group_id=episode_group_id,
        )
```

with:

```python
    def organize(
        self,
        source_file: Path,
        show_name: str,
        episode_code: str,
        *,
        tmdb_id: str | None = None,
        ordering: str = "aired",
        episode_group_id: str | None = None,
        year: int | None = None,
    ) -> dict:
        """Organize a TV episode from staging to library.

        Forwards the output-ordering controls AND show disambiguation (year/tmdb_id)
        to organize_tv_episode so the library-mode path (no explicit library_path)
        also honors them. episode_code stays canonical; only the filename is projected.
        """
        return organize_tv_episode(
            source_file,
            show_name,
            episode_code,
            tmdb_id=tmdb_id,
            ordering=ordering,
            episode_group_id=episode_group_id,
            year=year,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_organizer.py -q`
Expected: PASS (TestTVDisambiguation + all pre-existing organizer tests still green).

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/organizer.py backend/tests/unit/test_organizer.py
git commit -m "feat(organizer): build disambiguated TV show folder from year/tmdb_id"
```

---

## Task 4: Config three-way sync + validation (routes.py)

**Files:**
- Modify: `backend/app/api/routes.py:291-293` (`ConfigResponse`), `:369-371` (`ConfigUpdate`), `:1141-1143` (GET constructor), `:1248-1258` (validation imports + table)

- [ ] **Step 1: Add to `ConfigResponse`**

In `backend/app/api/routes.py`, replace lines 291-293:

```python
    naming_season_format: str
    naming_episode_format: str
    naming_movie_format: str
```

with:

```python
    naming_season_format: str
    naming_episode_format: str
    naming_movie_format: str
    naming_tv_show_format: str
```

- [ ] **Step 2: Add to `ConfigUpdate`**

Replace lines 369-371:

```python
    naming_season_format: str | None = None
    naming_episode_format: str | None = None
    naming_movie_format: str | None = None
```

with:

```python
    naming_season_format: str | None = None
    naming_episode_format: str | None = None
    naming_movie_format: str | None = None
    naming_tv_show_format: str | None = None
```

- [ ] **Step 3: Add to the GET-config constructor**

Replace lines 1141-1143:

```python
        naming_season_format=config.naming_season_format,
        naming_episode_format=config.naming_episode_format,
        naming_movie_format=config.naming_movie_format,
```

with:

```python
        naming_season_format=config.naming_season_format,
        naming_episode_format=config.naming_episode_format,
        naming_movie_format=config.naming_movie_format,
        naming_tv_show_format=config.naming_tv_show_format,
```

- [ ] **Step 4: Update the validation imports + table**

Replace lines 1248-1258:

```python
    from app.core.organizer import (
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        validate_naming_format,
    )

    format_checks = [
        ("naming_season_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_episode_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_movie_format", ALLOWED_MOVIE_PLACEHOLDERS),
    ]
```

with:

```python
    from app.core.organizer import (
        ALLOWED_EPISODE_PLACEHOLDERS,
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        ALLOWED_TV_SHOW_PLACEHOLDERS,
        validate_naming_format,
    )

    format_checks = [
        ("naming_season_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_episode_format", ALLOWED_EPISODE_PLACEHOLDERS),
        ("naming_movie_format", ALLOWED_MOVIE_PLACEHOLDERS),
        ("naming_tv_show_format", ALLOWED_TV_SHOW_PLACEHOLDERS),
    ]
```

- [ ] **Step 5: Verify the config round-trips through PUT → GET**

Run:

```bash
uv run python -c "
import asyncio
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.database import init_db

async def main():
    await init_db()
    t = ASGITransport(app=app)
    async with AsyncClient(transport=t, base_url='http://test') as ac:
        plex = '{show} ({year}) {{tmdb-{tmdb_id}}}'
        r = await ac.put('/api/config', json={'naming_tv_show_format': plex})
        print('PUT', r.status_code)
        g = await ac.get('/api/config')
        print('GET value:', g.json().get('naming_tv_show_format'))
        bad = await ac.put('/api/config', json={'naming_tv_show_format': '{bogus}'})
        print('BAD status:', bad.status_code)

asyncio.run(main())
"
```

Expected: `PUT 200`, `GET value: {show} ({year}) {{tmdb-{tmdb_id}}}` (the stored value keeps
its doubled braces — it is the raw format string, not the rendered folder name), `BAD status: 400`.
(This proves the three-way sync — model ↔ ConfigUpdate ↔ ConfigResponse — and validation.)

> **Note:** this writes to the worktree's `backend/engram.db`. That's fine — it only changes a naming format. If the DB lacks the `app_config` table, `init_db()` creates it (worktree-empty-DB hazard).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes.py
git commit -m "feat(config): wire naming_tv_show_format through REST + validation"
```

---

## Task 5: Persist `tmdb_year` + resolver helper

**Files:**
- Modify: `backend/app/models/disc_job.py:64-65` (add column)
- Modify: `backend/app/services/identification_coordinator.py` — add `_resolve_show_year` (near `_candidates_json_from_signal`, ~line 71); set `job.tmdb_year` at `:200-202`, `:555-559`, `:828-848`
- Test: `backend/tests/unit/test_resolve_show_year.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_resolve_show_year.py`:

```python
"""Unit tests for identification year resolution."""

from types import SimpleNamespace
from unittest.mock import patch

from app.services.identification_coordinator import _resolve_show_year


def test_none_tmdb_id_returns_none():
    assert _resolve_show_year(None) is None


def test_fast_path_reads_year_from_candidates():
    sig = SimpleNamespace(
        all_candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 50.0},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 30.0},
        ]
    )
    assert _resolve_show_year(3452, sig) == 1993
    assert _resolve_show_year(195241, sig) == 2023


def test_fallback_to_tmdb_details_when_no_candidates():
    with patch(
        "app.matcher.tmdb_client.fetch_show_details",
        return_value={"first_air_date": "1993-09-16"},
    ):
        assert _resolve_show_year(3452, None) == 1993


def test_returns_none_when_details_missing():
    with patch("app.matcher.tmdb_client.fetch_show_details", return_value=None):
        assert _resolve_show_year(3452, None) is None


def test_candidate_year_blank_falls_through_to_details():
    sig = SimpleNamespace(all_candidates=[{"tmdb_id": 3452, "year": ""}])
    with patch(
        "app.matcher.tmdb_client.fetch_show_details",
        return_value={"first_air_date": "1993-09-16"},
    ):
        assert _resolve_show_year(3452, sig) == 1993
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_resolve_show_year.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_show_year'`.

- [ ] **Step 3: Add the `tmdb_year` column**

In `backend/app/models/disc_job.py`, replace lines 64-65:

```python
    tmdb_id: int | None = Field(default=None)
    tmdb_name: str | None = Field(default=None)
```

with:

```python
    tmdb_id: int | None = Field(default=None)
    tmdb_name: str | None = Field(default=None)
    # First-air year for the resolved show; persisted at identify time so the
    # organizer can build a disambiguated library folder (Frasier 1993 vs 2023)
    # deterministically and offline. Nullable — degrades to id-only/bare folder.
    tmdb_year: int | None = Field(default=None)
```

- [ ] **Step 4: Add the `_resolve_show_year` helper**

In `backend/app/services/identification_coordinator.py`, add after `_candidates_json_from_signal` (after line 71, before `class IdentificationCoordinator`):

```python
def _resolve_show_year(tmdb_id: int | None, signal=None) -> int | None:
    """First-air year for a show, for library-folder disambiguation.

    No-network fast path: same-name candidates already carry a 'year' string
    (Frasier 1993 vs 2023). Universal fallback: cached TMDB details. Returns
    None when unknown — the organizer then degrades to an id-only/bare folder.
    Sync (blocking on the fallback) — call via ``asyncio.to_thread``.
    """
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

> Confirm `import asyncio` is present at the top of this file: `grep -n "^import asyncio" backend/app/services/identification_coordinator.py`. It is used elsewhere in the file; if absent, add it.

- [ ] **Step 5: Set `job.tmdb_year` at the main identify site**

Replace lines 200-202:

```python
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                job.candidates_json = _candidates_json_from_signal(tmdb_signal)
```

with:

```python
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                job.candidates_json = _candidates_json_from_signal(tmdb_signal)
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, tmdb_signal
                )
```

- [ ] **Step 6: Set `job.tmdb_year` at the staging-import site**

Replace lines 555-559:

```python
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                job.candidates_json = _candidates_json_from_signal(
                    getattr(analysis, "_tmdb_signal", None)
                )
```

with:

```python
                job.tmdb_id = analysis.tmdb_id
                job.tmdb_name = analysis.tmdb_name
                _signal = getattr(analysis, "_tmdb_signal", None)
                job.candidates_json = _candidates_json_from_signal(_signal)
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, _signal
                )
```

- [ ] **Step 7: Set `job.tmdb_year` at the re-identify site**

Replace lines 828-848:

```python
            # Optionally re-run TMDB lookup with corrected title
            if tmdb_id is not None:
                job.tmdb_id = tmdb_id
            else:
                # Try TMDB search with the corrected title
                try:
                    from app.core.tmdb_classifier import classify_from_tmdb
                    from app.services.config_service import get_config

                    config = await get_config()
                    if config.tmdb_api_key:
                        signal = classify_from_tmdb(title, config.tmdb_api_key)
                        if signal and signal.tmdb_id:
                            job.tmdb_id = signal.tmdb_id
                            if signal.tmdb_name:
                                job.detected_title = signal.tmdb_name
                except Exception:
                    logger.warning(
                        f"Job {job_id}: TMDB re-lookup failed for '{title}', "
                        f"continuing with user-provided title"
                    )
```

with:

```python
            # Optionally re-run TMDB lookup with corrected title
            _signal = None
            if tmdb_id is not None:
                job.tmdb_id = tmdb_id
            else:
                # Try TMDB search with the corrected title
                try:
                    from app.core.tmdb_classifier import classify_from_tmdb
                    from app.services.config_service import get_config

                    config = await get_config()
                    if config.tmdb_api_key:
                        _signal = classify_from_tmdb(title, config.tmdb_api_key)
                        if _signal and _signal.tmdb_id:
                            job.tmdb_id = _signal.tmdb_id
                            if _signal.tmdb_name:
                                job.detected_title = _signal.tmdb_name
                except Exception:
                    logger.warning(
                        f"Job {job_id}: TMDB re-lookup failed for '{title}', "
                        f"continuing with user-provided title"
                    )

            # Re-derive the year for the (possibly changed) show so the library
            # folder stays correct after re-identification.
            job.tmdb_year = await asyncio.to_thread(_resolve_show_year, job.tmdb_id, _signal)
```

- [ ] **Step 8: Run the resolver test to verify it passes**

Run: `uv run pytest tests/unit/test_resolve_show_year.py -q`
Expected: PASS (5 tests).

- [ ] **Step 9: Verify the column is added cleanly on an existing DB**

Run:

```bash
uv run python -c "
import asyncio
from app.database import init_db, async_session
from sqlalchemy import text

async def main():
    await init_db()
    async with async_session() as s:
        rows = (await s.execute(text('PRAGMA table_info(disc_jobs)'))).fetchall()
        cols = [r[1] for r in rows]
        print('tmdb_year present:', 'tmdb_year' in cols)

asyncio.run(main())
"
```

Expected: `tmdb_year present: True`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/disc_job.py backend/app/services/identification_coordinator.py backend/tests/unit/test_resolve_show_year.py
git commit -m "feat(identify): persist tmdb_year (candidates fast-path + TMDB fallback)"
```

---

## Task 6: Thread year/tmdb_id at the three finalization call sites

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py` — `finalize_disc_job` (preamble :815, extras :839, episode :850, tv_organizer :861); `_finalize_tv_if_resolved` (preamble :1174, extras :1192, episode :1205, tv_organizer :1216); `process_matched_titles` (preamble :1360, extras :1378, episode :1391, tv_organizer :1402)

> All three sites share an identical shape. For each: add `_tmdb_year = job.tmdb_year` beside the existing `_tmdb_id_str = …` line, then add `year=_tmdb_year` to the episode + tv_organizer calls and `tmdb_id=_tmdb_id_str, year=_tmdb_year` to the extras call.

- [ ] **Step 1: `finalize_disc_job` — preamble (line 815)**

> The bare `_tmdb_id_str = …` line is identical at sites 1 and 3 (same 12-space indent), so each preamble edit below includes its surrounding comment/`resolve_show_ordering` line to be a UNIQUE match. Site 1 is distinguished by its two-line comment; sites 2 and 3 differ by indentation (8 vs 12 spaces).

Replace (note the site-1 comment wording, "Resolve the show's output ordering…"):

```python
            # Resolve the show's output ordering once for this sweep (#200).
            # Canonical (aired) for the common case; projected to the filename only.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
```

with:

```python
            # Resolve the show's output ordering once for this sweep (#200).
            # Canonical (aired) for the common case; projected to the filename only.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
            _tmdb_year = job.tmdb_year
```

- [ ] **Step 2: `finalize_disc_job` — extras call (line 838-847)**

Replace:

```python
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=t.title_index,
                    )
```

with:

```python
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=t.title_index,
                        tmdb_id=_tmdb_id_str,
                        year=_tmdb_year,
                    )
```

- [ ] **Step 3: `finalize_disc_job` — episode + tv_organizer calls (lines 849-868)**

Replace:

```python
                elif _lib_path:
                    org_result = await asyncio.to_thread(
                        organize_tv_episode,
                        source_file,
                        job.detected_title or job.volume_label,
                        t.matched_episode,
                        _lib_path,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                    )
                else:
                    org_result = await asyncio.to_thread(
                        tv_organizer.organize,
                        source_file,
                        job.detected_title,
                        t.matched_episode,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                    )
```

with:

```python
                elif _lib_path:
                    org_result = await asyncio.to_thread(
                        organize_tv_episode,
                        source_file,
                        job.detected_title or job.volume_label,
                        t.matched_episode,
                        _lib_path,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                        year=_tmdb_year,
                    )
                else:
                    org_result = await asyncio.to_thread(
                        tv_organizer.organize,
                        source_file,
                        job.detected_title,
                        t.matched_episode,
                        tmdb_id=_tmdb_id_str,
                        ordering=ordering,
                        episode_group_id=ordering_group_id,
                        year=_tmdb_year,
                    )
```

- [ ] **Step 4: `_finalize_tv_if_resolved` — preamble (line 1174)**

Replace (8-space indent — this is the only site at this indentation):

```python
        # Resolve output ordering once for this sweep (#200); filename-only projection.
        ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
        _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
```

with:

```python
        # Resolve output ordering once for this sweep (#200); filename-only projection.
        ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
        _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
        _tmdb_year = job.tmdb_year
```

- [ ] **Step 5: `_finalize_tv_if_resolved` — extras call (lines 1191-1200)**

Replace:

```python
                        org_result = await asyncio.to_thread(
                            organize_tv_extras,
                            source_file,
                            job.detected_title or job.volume_label,
                            job.detected_season or 1,
                            library_path=_lib_path,
                            disc_number=job.disc_number or 1,
                            extra_index=extra_index,
                            title_index=disc_title.title_index,
                        )
```

with:

```python
                        org_result = await asyncio.to_thread(
                            organize_tv_extras,
                            source_file,
                            job.detected_title or job.volume_label,
                            job.detected_season or 1,
                            library_path=_lib_path,
                            disc_number=job.disc_number or 1,
                            extra_index=extra_index,
                            title_index=disc_title.title_index,
                            tmdb_id=_tmdb_id_str,
                            year=_tmdb_year,
                        )
```

- [ ] **Step 6: `_finalize_tv_if_resolved` — episode + tv_organizer calls (lines 1203-1223)**

Replace:

```python
                        if _lib_path:
                            org_result = await asyncio.to_thread(
                                organize_tv_episode,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                _lib_path,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                            )
                        else:
                            org_result = await asyncio.to_thread(
                                tv_organizer.organize,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                            )
```

with:

```python
                        if _lib_path:
                            org_result = await asyncio.to_thread(
                                organize_tv_episode,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                _lib_path,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                                year=_tmdb_year,
                            )
                        else:
                            org_result = await asyncio.to_thread(
                                tv_organizer.organize,
                                source_file,
                                job.detected_title or job.volume_label,
                                disc_title.matched_episode,
                                tmdb_id=_tmdb_id_str,
                                ordering=ordering,
                                episode_group_id=ordering_group_id,
                                year=_tmdb_year,
                            )
```

- [ ] **Step 7: `process_matched_titles` — preamble (line 1360)**

Replace (12-space indent + the "filename-only projection" comment — distinguishes it from site 1, whose comment differs):

```python
            # Resolve output ordering once for this sweep (#200); filename-only projection.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
```

with:

```python
            # Resolve output ordering once for this sweep (#200); filename-only projection.
            ordering, ordering_group_id = await resolve_show_ordering(job.tmdb_id, session)
            _tmdb_id_str = str(job.tmdb_id) if job.tmdb_id else None
            _tmdb_year = job.tmdb_year
```

- [ ] **Step 8: `process_matched_titles` — extras call (lines 1377-1386)**

Replace:

```python
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=disc_title.title_index,
                    )
```

with:

```python
                    org_result = await asyncio.to_thread(
                        organize_tv_extras,
                        source_file,
                        job.detected_title or job.volume_label,
                        job.detected_season or 1,
                        library_path=_lib_path,
                        disc_number=job.disc_number or 1,
                        extra_index=extra_index,
                        title_index=disc_title.title_index,
                        tmdb_id=_tmdb_id_str,
                        year=_tmdb_year,
                    )
```

- [ ] **Step 9: `process_matched_titles` — episode + tv_organizer calls (lines 1389-1409)**

Replace:

```python
                    if _lib_path:
                        org_result = await asyncio.to_thread(
                            organize_tv_episode,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            _lib_path,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                        )
                    else:
                        org_result = await asyncio.to_thread(
                            tv_organizer.organize,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                        )
```

with:

```python
                    if _lib_path:
                        org_result = await asyncio.to_thread(
                            organize_tv_episode,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            _lib_path,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                            year=_tmdb_year,
                        )
                    else:
                        org_result = await asyncio.to_thread(
                            tv_organizer.organize,
                            source_file,
                            job.detected_title or job.volume_label,
                            disc_title.matched_episode,
                            tmdb_id=_tmdb_id_str,
                            ordering=ordering,
                            episode_group_id=ordering_group_id,
                            year=_tmdb_year,
                        )
```

- [ ] **Step 10: Verify it imports and existing finalization tests pass**

Run: `uv run python -c "import app.services.finalization_coordinator"`
Expected: no error.

Run: `uv run pytest tests/pipeline/ tests/integration/ -q -k "tv or organiz or finaliz or extra"`
Expected: PASS (no regressions). If a pre-existing unrelated failure appears (`test_movie_ambiguous_rip_first_workflow` is a known flaky staging-cleanup race per project memory), note it and continue.

- [ ] **Step 11: Commit**

```bash
git add backend/app/services/finalization_coordinator.py
git commit -m "feat(finalization): pass year+tmdb_id to all 3 TV-organize sites"
```

---

## Task 7: ConfigWizard — Show Folder Format field

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx:70-72` (interface), `:139-141` (defaults), `:234-236` (read), `:374-376` (write), after `:1368` (input)

- [ ] **Step 1: Add to the config interface**

In `frontend/src/components/ConfigWizard.tsx`, replace lines 70-72:

```tsx
    namingSeasonFormat: string;
    namingEpisodeFormat: string;
    namingMovieFormat: string;
```

with:

```tsx
    namingSeasonFormat: string;
    namingEpisodeFormat: string;
    namingMovieFormat: string;
    namingTvShowFormat: string;
```

- [ ] **Step 2: Add to the defaults object**

Replace lines 139-141:

```tsx
        namingSeasonFormat: 'Season {season:02d}',
        namingEpisodeFormat: '{show} - S{season:02d}E{episode:02d}',
        namingMovieFormat: '{title} ({year})',
```

with:

```tsx
        namingSeasonFormat: 'Season {season:02d}',
        namingEpisodeFormat: '{show} - S{season:02d}E{episode:02d}',
        namingMovieFormat: '{title} ({year})',
        namingTvShowFormat: '{show}',
```

- [ ] **Step 3: Add to the read mapping (GET → state)**

Replace lines 234-236:

```tsx
                    namingSeasonFormat: data.naming_season_format || 'Season {season:02d}',
                    namingEpisodeFormat: data.naming_episode_format || '{show} - S{season:02d}E{episode:02d}',
                    namingMovieFormat: data.naming_movie_format || '{title} ({year})',
```

with:

```tsx
                    namingSeasonFormat: data.naming_season_format || 'Season {season:02d}',
                    namingEpisodeFormat: data.naming_episode_format || '{show} - S{season:02d}E{episode:02d}',
                    namingMovieFormat: data.naming_movie_format || '{title} ({year})',
                    namingTvShowFormat: data.naming_tv_show_format || '{show}',
```

- [ ] **Step 4: Add to the write mapping (state → PUT)**

Replace lines 374-376:

```tsx
                    naming_season_format: config.namingSeasonFormat,
                    naming_episode_format: config.namingEpisodeFormat,
                    naming_movie_format: config.namingMovieFormat,
```

with:

```tsx
                    naming_season_format: config.namingSeasonFormat,
                    naming_episode_format: config.namingEpisodeFormat,
                    naming_movie_format: config.namingMovieFormat,
                    naming_tv_show_format: config.namingTvShowFormat,
```

- [ ] **Step 5: Add the input field (always visible, opt-in)**

Replace the closing of the Naming Convention form-group + hint (lines 1365-1368):

```tsx
                            <span className="form-hint">
                                Preview: TV/{config.namingSeasonFormat.replace('{season:02d}', '01').replace('{season:d}', '1')}/{config.namingEpisodeFormat.replace('{show}', 'Breaking Bad').replace('{season:02d}', '01').replace('{season:d}', '1').replace('{episode:02d}', '05').replace('{episode:d}', '5')}.mkv
                            </span>
                        </div>
```

with:

```tsx
                            <span className="form-hint">
                                Preview: TV/{config.namingSeasonFormat.replace('{season:02d}', '01').replace('{season:d}', '1')}/{config.namingEpisodeFormat.replace('{show}', 'Breaking Bad').replace('{season:02d}', '01').replace('{season:d}', '1').replace('{episode:02d}', '05').replace('{episode:d}', '5')}.mkv
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="namingTvShowFormat">Show Folder Format</label>
                            <input
                                id="namingTvShowFormat"
                                type="text"
                                value={config.namingTvShowFormat}
                                onChange={(e) => handleInputChange('namingTvShowFormat', e.target.value)}
                                placeholder="{show}"
                            />
                            <span className="form-hint">
                                Placeholders: {'{show}'}, {'{year}'}, {'{tmdb_id}'}. Default{' '}
                                {'{show}'} keeps your current folders. To let same-name shows
                                coexist (e.g. Frasier 1993 vs 2023), use Plex{' '}
                                &quot;{'{show} ({year}) {{tmdb-{tmdb_id}}}'}&quot; or Jellyfin{' '}
                                &quot;{'{show} ({year}) [tmdbid-{tmdb_id}]'}&quot;.
                            </span>
                        </div>
```

- [ ] **Step 6: Verify the frontend builds and lints**

Run (from `frontend/`): `npm run build && npm run lint`
Expected: build succeeds (TypeScript happy with the new `namingTvShowFormat` key), lint clean.

> Worktree note (project memory): `frontend/node_modules` may be absent — run `npm install` first if build fails on missing deps. If `package-lock.json` changes, `git checkout` it before committing (it rewrites a stale lock).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(ui): add Show Folder Format setting to ConfigWizard"
```

---

## Task 8: Pipeline org-path tests — coexistence + default guard

**Files:**
- Modify: `backend/tests/pipeline/test_organization_paths.py`

- [ ] **Step 1: Write the failing/guard tests**

Append to `backend/tests/pipeline/test_organization_paths.py`:

```python
@pytest.mark.pipeline
class TestTVSameNameCoexistence:
    """Same-name shows coexist when disambiguation is enabled; default unchanged."""

    @staticmethod
    def _patch_cfg(**over):
        from unittest.mock import patch

        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    def test_frasier_twins_coexist(self, tmp_path):
        lib = tmp_path / "tv"
        plex = "{show} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_tv_show_format=plex):
            a = tmp_path / "staging" / "a.mkv"
            a.parent.mkdir(parents=True, exist_ok=True)
            a.write_bytes(b"x" * 1024)
            r1 = organize_tv_episode(
                a, "Frasier", "S01E01", library_path=lib, tmdb_id="3452", year=1993
            )
            b = tmp_path / "staging" / "b.mkv"
            b.write_bytes(b"x" * 1024)
            r2 = organize_tv_episode(
                b, "Frasier", "S01E01", library_path=lib, tmdb_id="195241", year=2023
            )
        assert r1["success"] and r2["success"]
        assert r1["final_path"].parent.parent.name == "Frasier (1993) {tmdb-3452}"
        assert r2["final_path"].parent.parent.name == "Frasier (2023) {tmdb-195241}"
        assert r1["final_path"] != r2["final_path"]

    def test_default_format_unchanged_bare_folder(self, tmp_path):
        lib = tmp_path / "tv"
        with self._patch_cfg():  # default naming_tv_show_format == "{show}"
            s = tmp_path / "staging" / "c.mkv"
            s.parent.mkdir(parents=True, exist_ok=True)
            s.write_bytes(b"x" * 1024)
            r = organize_tv_episode(
                s, "Frasier", "S01E01", library_path=lib, tmdb_id="3452", year=1993
            )
        assert r["success"]
        assert r["final_path"].parent.parent.name == "Frasier"
        assert r["final_path"].name == "Frasier - S01E01.mkv"
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/pipeline/test_organization_paths.py::TestTVSameNameCoexistence -q`
Expected: PASS (both tests).

- [ ] **Step 3: Run the full pipeline org-path file (regression)**

Run: `uv run pytest tests/pipeline/test_organization_paths.py -q`
Expected: PASS (existing Picard/Arrested-Dev tests still green — they rely on `get_config_sync` defaults, so the worktree DB must have an `app_config` row; if they error with "no such table"/missing column, run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first per the worktree-DB hazard).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/pipeline/test_organization_paths.py
git commit -m "test(pipeline): same-name TV coexistence + default-folder guard"
```

---

## Task 9: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Lint the backend**

Run (from `backend/`): `uv run ruff check .`
Expected: `All checks passed!` (fix any new warnings in the touched files).

- [ ] **Step 2: Run the full touched-area backend suites**

Run:

```bash
uv run pytest tests/unit/test_organizer.py tests/unit/test_resolve_show_year.py tests/pipeline/test_organization_paths.py -q
uv run pytest tests/integration/ -q -k "tv or organiz or finaliz or extra or workflow"
```

Expected: PASS. The only acceptable failure is the documented pre-existing flaky `test_movie_ambiguous_rip_first_workflow` (staging-cleanup race) — confirm any failure is exactly that and unrelated to this change.

- [ ] **Step 3: Frontend build + lint**

Run (from `frontend/`): `npm run build && npm run lint`
Expected: both succeed.

- [ ] **Step 4: Final commit (if any verification fixes were needed)**

```bash
git add -A
git commit -m "chore: lint/test fixes for TV disambiguation"
```

(Skip if nothing changed.)

---

## Out of scope (do NOT implement here)

- Runtime SRT scrape cache `~/.engram/cache/data/<show_name>/` is still name-keyed — separately tracked follow-up.
- Migrating/relocating users' existing bare `Frasier/` libraries — intentionally not done (opt-in default avoids touching them).
- Exposing `tmdb_year` in WebSocket/REST job payloads — it is internal organize-time metadata only.
