# Movie Naming Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give movies the same configurable folder and filename naming that TV shows already have, and fix the four defects that made movie naming non-functional.

**Architecture:** Movies currently squeeze all identity (title, year, edition) into one `movie_name: str` that flows through `clean_movie_name()`, a normalizer built for disc volume labels. This plan gives movies the typed-keyword spine TV already has: `organize_movie(..., *, tmdb_id=, edition=, already_clean=)`, a second config field for the filename format, and a movie-aware year resolver. The four defects (dead `{year}`, mangled Plex edition tag, corrupted TMDB titles, no Settings UI) all fall out of that one change.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / pytest (backend), React 18 + TypeScript (frontend), `uv` for all Python commands.

**Spec:** `docs/superpowers/specs/2026-08-04-movie-naming-parity-design.md`
**Issue:** [#576](https://github.com/Jsakkos/engram/issues/576)

---

## Conventions for this plan

- All Python commands run from `backend/` and are prefixed with `uv run`. Never call `python` or `pytest` bare.
- All frontend commands run from `frontend/`.
- Ruff: line length 100, double quotes. Run `uv run ruff format .` before each backend commit.
- **No Alembic migration is needed for the new config column.** `app_config` columns are reconciled by `_add_missing_columns()` in `database.py`, not Alembic. Precedent: `naming_tv_show_format` was added the same way and has no migration file. The `server_default` on the field is what makes existing databases pick up the real default instead of an empty string.
- Commit after every task. Do not batch.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/app/core/organizer.py` | Pure name formatters + the movie organize routine | Modify |
| `backend/app/models/app_config.py` | Persisted config schema | Modify |
| `backend/app/api/routes.py` | Config request/response shapes + format validation | Modify |
| `backend/app/core/tmdb_classifier.py` | TMDB signal carrying the movie release year | Modify |
| `backend/app/services/identification_coordinator.py` | Content-type-aware year resolution | Modify |
| `backend/app/services/finalization_coordinator.py` | Review-path movie organize call | Modify |
| `backend/app/services/job_manager.py` | Auto-path movie organize call | Modify |
| `frontend/src/components/ConfigWizard.tsx` | Movie naming Settings UI | Modify |
| `backend/tests/pipeline/test_organization_paths.py` | Movie path generation tests | Modify |
| `backend/tests/unit/test_movie_naming_formats.py` | Formatter unit tests | Create |
| `docs/getting-started/configuration.md` | User-facing naming reference | Modify |
| `CHANGELOG.md` | Release notes | Modify |

---

## Task 1: Widen the movie folder formatter to accept `{tmdb_id}`

**Files:**
- Modify: `backend/app/core/organizer.py:28` (placeholder set), `backend/app/core/organizer.py:79-87` (`format_movie_folder`)
- Test: `backend/tests/unit/test_movie_naming_formats.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_movie_naming_formats.py`:

```python
"""Unit tests for the movie naming formatters (issue #576).

Pure-function tests: no database, no filesystem. The organize-level
behavior is covered in tests/pipeline/test_organization_paths.py.
"""

import pytest

from app.core.organizer import (
    ALLOWED_MOVIE_PLACEHOLDERS,
    format_movie_folder,
    validate_naming_format,
)


class TestFormatMovieFolder:
    def test_default_format_with_year(self):
        assert format_movie_folder("{title} ({year})", "Blade Runner", 1982) == "Blade Runner (1982)"

    def test_default_format_without_year_strips_empty_parens(self):
        assert format_movie_folder("{title} ({year})", "Blade Runner", None) == "Blade Runner"

    def test_plex_tmdb_tag(self):
        result = format_movie_folder(
            "{title} ({year}) {{tmdb-{tmdb_id}}}", "Blade Runner", 1982, tmdb_id=78
        )
        assert result == "Blade Runner (1982) {tmdb-78}"

    def test_jellyfin_tmdb_tag(self):
        result = format_movie_folder(
            "{title} ({year}) [tmdbid-{tmdb_id}]", "Blade Runner", 1982, tmdb_id=78
        )
        assert result == "Blade Runner (1982) [tmdbid-78]"

    def test_missing_tmdb_id_strips_empty_tag(self):
        result = format_movie_folder(
            "{title} ({year}) {{tmdb-{tmdb_id}}}", "Blade Runner", 1982, tmdb_id=None
        )
        assert result == "Blade Runner (1982)"

    def test_unknown_placeholder_falls_back_safely(self):
        result = format_movie_folder("{title} {nonsense}", "Blade Runner", 1982)
        assert result == "Blade Runner (1982)"


class TestMoviePlaceholderValidation:
    def test_tmdb_id_is_now_allowed(self):
        assert (
            validate_naming_format(
                "{title} ({year}) {{tmdb-{tmdb_id}}}", ALLOWED_MOVIE_PLACEHOLDERS
            )
            is None
        )

    def test_unknown_placeholder_rejected(self):
        error = validate_naming_format("{title} {director}", ALLOWED_MOVIE_PLACEHOLDERS)
        assert error is not None
        assert "director" in error

    def test_path_traversal_rejected(self):
        assert validate_naming_format("../{title}", ALLOWED_MOVIE_PLACEHOLDERS) is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_movie_naming_formats.py -v`
Expected: FAIL. `test_plex_tmdb_tag` fails with `TypeError: format_movie_folder() got an unexpected keyword argument 'tmdb_id'`, and `test_tmdb_id_is_now_allowed` fails because validation still rejects `tmdb_id`.

- [ ] **Step 3: Widen the placeholder set**

In `backend/app/core/organizer.py`, replace line 28:

```python
ALLOWED_MOVIE_PLACEHOLDERS = {"title", "year"}
```

with:

```python
# Movie *folder* format. Mirrors ALLOWED_TV_SHOW_PLACEHOLDERS so movies can carry
# the same Plex "{tmdb-NNNN}" / Jellyfin "[tmdbid-NNNN]" disambiguation tags (#576).
ALLOWED_MOVIE_PLACEHOLDERS = {"title", "year", "tmdb_id"}
```

- [ ] **Step 4: Rewrite `format_movie_folder`**

Replace `backend/app/core/organizer.py:79-87` in full:

```python
def format_movie_folder(
    fmt: str, title: str, year: int | None, tmdb_id: str | int | None = None
) -> str:
    """Format a movie folder name from a config format string.

    Mirrors ``format_tv_show_folder``: ``{tmdb_id}`` lets same-name movies coexist
    (Plex ``{tmdb-NNNN}`` / Jellyfin ``[tmdbid-NNNN]``). Empty groups are stripped
    when year/id are missing, so a stable id tag never degrades to ``Blade Runner
    {tmdb-}``. ``format_map`` (not ``format(**...)``) keeps an unknown placeholder
    raising KeyError into the safe fallback while avoiding CodeQL's
    missing-named-argument false positive (see ``format_season_folder``).
    """
    try:
        result = fmt.format_map(
            {
                "title": title,
                "year": year if year is not None else "",
                "tmdb_id": tmdb_id if tmdb_id is not None else "",
            }
        )
    except (KeyError, ValueError, IndexError):
        result = f"{title} ({year})" if year else title
    return sanitize_filename(_strip_empty_name_groups(result))
```

Note this replaces the old trailing-only regex (`re.sub(r"\s*\(\s*\)\s*$", ...)`) with the shared `_strip_empty_name_groups()` helper, so folder and filename strip identically.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_movie_naming_formats.py -v`
Expected: PASS, 9 passed.

- [ ] **Step 6: Verify no existing test regressed**

Run: `uv run pytest tests/pipeline/test_organization_paths.py -v`
Expected: PASS, all existing movie and TV cases still green.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/core/organizer.py backend/tests/unit/test_movie_naming_formats.py
git commit -m "feat(organizer): allow {tmdb_id} in movie folder format (#576)"
```

---

## Task 2: Add the movie filename formatter with `{edition}`

**Files:**
- Modify: `backend/app/core/organizer.py` (add `ALLOWED_MOVIE_FILE_PLACEHOLDERS` next to line 28, add `format_movie_filename` after `format_movie_folder`)
- Test: `backend/tests/unit/test_movie_naming_formats.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_movie_naming_formats.py`:

```python
class TestFormatMovieFilename:
    DEFAULT = "{title} ({year}) {{edition-{edition}}}"

    def test_default_without_edition_matches_folder_name(self):
        """No edition renders byte-identically to the plain folder format."""
        assert format_movie_filename(self.DEFAULT, "Blade Runner", 1982) == "Blade Runner (1982)"

    def test_default_with_edition_renders_plex_tag(self):
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Final Cut")
        assert result == "Blade Runner (1982) {edition-Final Cut}"

    def test_edition_hyphen_is_preserved(self):
        """Regression: the edition tag used to be flattened by clean_movie_name."""
        result = format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="Director's Cut")
        assert result == "Blade Runner (1982) {edition-Director's Cut}"
        assert "{Edition" not in result

    def test_empty_string_edition_strips_tag(self):
        assert format_movie_filename(self.DEFAULT, "Blade Runner", 1982, edition="") == (
            "Blade Runner (1982)"
        )

    def test_tmdb_id_and_edition_together(self):
        result = format_movie_filename(
            "{title} ({year}) {{tmdb-{tmdb_id}}} {{edition-{edition}}}",
            "Blade Runner",
            1982,
            tmdb_id=78,
            edition="Final Cut",
        )
        assert result == "Blade Runner (1982) {tmdb-78} {edition-Final Cut}"

    def test_unknown_placeholder_falls_back_safely(self):
        assert format_movie_filename("{title} {nonsense}", "Blade Runner", 1982) == (
            "Blade Runner (1982)"
        )


class TestMovieFilePlaceholderValidation:
    def test_edition_allowed_on_file_format_only(self):
        assert (
            validate_naming_format(
                "{title} {{edition-{edition}}}", ALLOWED_MOVIE_FILE_PLACEHOLDERS
            )
            is None
        )
        # edition is deliberately NOT valid on the folder format
        assert validate_naming_format("{title} {edition}", ALLOWED_MOVIE_PLACEHOLDERS) is not None
```

Update the import at the top of the file to:

```python
from app.core.organizer import (
    ALLOWED_MOVIE_FILE_PLACEHOLDERS,
    ALLOWED_MOVIE_PLACEHOLDERS,
    format_movie_filename,
    format_movie_folder,
    validate_naming_format,
)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_movie_naming_formats.py -v`
Expected: FAIL at collection with `ImportError: cannot import name 'ALLOWED_MOVIE_FILE_PLACEHOLDERS'`.

- [ ] **Step 3: Add the placeholder set**

In `backend/app/core/organizer.py`, immediately after the `ALLOWED_MOVIE_PLACEHOLDERS` line from Task 1, add:

```python
# Movie *filename* format. Adds {edition} because Plex reads the edition tag off
# the FILE, not the folder (#576).
ALLOWED_MOVIE_FILE_PLACEHOLDERS = {"title", "year", "tmdb_id", "edition"}
```

- [ ] **Step 4: Add the formatter**

In `backend/app/core/organizer.py`, immediately after `format_movie_folder`, add:

```python
def format_movie_filename(
    fmt: str,
    title: str,
    year: int | None,
    tmdb_id: str | int | None = None,
    edition: str | None = None,
) -> str:
    """Format a movie filename (no extension) from a config format string.

    Mirrors ``format_movie_folder`` but adds ``{edition}`` for Plex's
    ``{edition-Director's Cut}`` convention, which belongs on the file rather than
    the folder. Empty groups are stripped, so a movie with no edition renders
    byte-identically to the bare folder format and pre-#576 libraries are
    untouched.
    """
    try:
        result = fmt.format_map(
            {
                "title": title,
                "year": year if year is not None else "",
                "tmdb_id": tmdb_id if tmdb_id is not None else "",
                "edition": edition if edition is not None else "",
            }
        )
    except (KeyError, ValueError, IndexError):
        result = f"{title} ({year})" if year else title
    return sanitize_filename(_strip_empty_name_groups(result))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_movie_naming_formats.py -v`
Expected: PASS, 17 passed.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/core/organizer.py backend/tests/unit/test_movie_naming_formats.py
git commit -m "feat(organizer): add format_movie_filename with {edition} (#576)"
```

---

## Task 3: Add the `naming_movie_file_format` config field

**Files:**
- Modify: `backend/app/models/app_config.py:137-149`
- Modify: `backend/app/api/routes.py:302` (ConfigResponse), `:391` (ConfigUpdate), `:1571` (response construction), `:1703-1716` (validation)

A field missed in any one of these four places is silently dropped by Pydantic. All four must land in this task.

- [ ] **Step 1: Add the model field**

In `backend/app/models/app_config.py`, immediately after the `naming_movie_format` line (currently line 140), add:

```python
    # Movie *filename* format (#576). Default carries the Plex edition tag; with
    # empty-group stripping a movie with no edition renders exactly like the
    # folder format, so existing libraries are untouched. server_default ensures
    # EXISTING DBs get this literal (not the _add_missing_columns String fallback
    # of ''). A blank value means "reuse naming_movie_format".
    naming_movie_file_format: str = Field(
        default="{title} ({year}) {{edition-{edition}}}",
        sa_column_kwargs={
            "server_default": text("'{title} ({year}) {{edition-{edition}}}'")
        },
    )
```

`Field` and `text` are already imported in this module (used by `naming_tv_show_format` directly above).

- [ ] **Step 2: Verify the default loads**

Run:
```bash
uv run python -c "from app.models.app_config import AppConfig; print(repr(AppConfig().naming_movie_file_format))"
```
Expected output exactly: `'{title} ({year}) {{edition-{edition}}}'`

- [ ] **Step 3: Add to `ConfigResponse`**

In `backend/app/api/routes.py`, after line 302 (`naming_movie_format: str`), add:

```python
    naming_movie_file_format: str
```

- [ ] **Step 4: Add to `ConfigUpdate`**

After line 391 (`naming_movie_format: str | None = None`), add:

```python
    naming_movie_file_format: str | None = None
```

- [ ] **Step 5: Add to the response construction**

After line 1571 (`naming_movie_format=config.naming_movie_format,`), add:

```python
        naming_movie_file_format=config.naming_movie_file_format,
```

- [ ] **Step 6: Add to format validation**

In `backend/app/api/routes.py`, update the import block at lines 1703-1709 to include the new set:

```python
    from app.core.organizer import (
        ALLOWED_EPISODE_PLACEHOLDERS,
        ALLOWED_MOVIE_FILE_PLACEHOLDERS,
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        ALLOWED_TV_SHOW_PLACEHOLDERS,
        validate_naming_format,
    )
```

and add one entry to the `format_checks` list (line 1711-1716), after the `naming_movie_format` line:

```python
        ("naming_movie_file_format", ALLOWED_MOVIE_FILE_PLACEHOLDERS),
```

- [ ] **Step 7: Verify the round-trip**

Run:
```bash
uv run pytest tests/unit -k "config" -v
```
Expected: PASS. No test should reference the new field yet; this confirms nothing broke.

- [ ] **Step 8: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/models/app_config.py backend/app/api/routes.py
git commit -m "feat(config): add naming_movie_file_format (#576)"
```

---

## Task 4: Rewire `organize_movie` to use both formats and skip cleaning canonical titles

**Files:**
- Modify: `backend/app/core/organizer.py:311-320` (signature + docstring), `:379-391` (name derivation), `:467-479` (`MovieOrganizer.organize`)
- Test: `backend/tests/pipeline/test_organization_paths.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/pipeline/test_organization_paths.py`:

```python
@pytest.mark.pipeline
class TestMovieNamingParity:
    """Movie folder/filename formats, edition tags, and canonical-title handling (#576)."""

    @staticmethod
    def _patch_cfg(**over):
        from unittest.mock import patch

        from app.models.app_config import AppConfig

        return patch(
            "app.services.config_service.get_config_sync",
            return_value=AppConfig(**over),
        )

    @staticmethod
    def _staged(tmp_path, name="t00.mkv"):
        staging = tmp_path / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / name).write_bytes(b"x" * 1024)
        return staging

    def test_default_no_edition_matches_pre_feature_layout(self, tmp_path):
        """Guard: the common case must not move relative to the old behavior."""
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging, "Blade Runner", year=1982, library_path=tmp_path / "movies"
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982)"
        assert r["main_file"].name == "Blade Runner (1982).mkv"

    def test_edition_lands_on_the_file_unmangled(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                edition="Final Cut",
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982)"
        assert r["main_file"].name == "Blade Runner (1982) {edition-Final Cut}.mkv"

    def test_tmdb_id_tag_in_folder(self, tmp_path):
        staging = self._staged(tmp_path)
        plex = "{title} ({year}) {{tmdb-{tmdb_id}}}"
        with self._patch_cfg(naming_movie_format=plex):
            r = organize_movie(
                staging,
                "Blade Runner",
                year=1982,
                library_path=tmp_path / "movies",
                tmdb_id=78,
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "Blade Runner (1982) {tmdb-78}"

    def test_blank_file_format_inherits_folder_format(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg(naming_movie_file_format=""):
            r = organize_movie(
                staging, "Blade Runner", year=1982, library_path=tmp_path / "movies"
            )
        assert r["success"], r.get("error")
        assert r["main_file"].name == "Blade Runner (1982).mkv"

    def test_canonical_tmdb_title_is_not_normalized(self, tmp_path):
        """Regression: clean_movie_name used to turn Spider-Man into Spider Man."""
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging,
                "Spider-Man: No Way Home",
                year=2021,
                library_path=tmp_path / "movies",
                already_clean=True,
            )
        assert r["success"], r.get("error")
        # The colon is stripped by sanitize_filename (invalid on Windows); the
        # hyphen and the capitalization must survive.
        assert r["main_file"].parent.name.startswith("Spider-Man")
        assert "Spider Man" not in r["main_file"].parent.name

    def test_volume_label_still_normalized_by_default(self, tmp_path):
        staging = self._staged(tmp_path)
        with self._patch_cfg():
            r = organize_movie(
                staging, "THE_SOCIAL_NETWORK", year=2010, library_path=tmp_path / "movies"
            )
        assert r["success"], r.get("error")
        assert r["main_file"].parent.name == "The Social Network (2010)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_organization_paths.py::TestMovieNamingParity -v`
Expected: FAIL. `test_edition_lands_on_the_file_unmangled` fails with `TypeError: organize_movie() got an unexpected keyword argument 'edition'`.

- [ ] **Step 3: Update the `organize_movie` signature**

In `backend/app/core/organizer.py`, replace lines 311-316 (the signature) with:

```python
def organize_movie(
    staging_dir: Path,
    movie_name: str,
    year: int | None = None,
    library_path: Path | None = None,
    conflict_resolution: str = "ask",
    move_extras: bool = True,
    *,
    tmdb_id: str | int | None = None,
    edition: str | None = None,
    already_clean: bool = False,
) -> dict:
```

> Read the existing lines 311-320 before editing: keep whatever `conflict_resolution` and `move_extras` defaults are already there rather than assuming the values above, and preserve the existing docstring body, appending the new argument descriptions below.

Add to the docstring's Args section:

```
        tmdb_id: TMDB movie id, used only when the configured folder/filename
            format includes ``{tmdb_id}``.
        edition: Edition label (e.g. "Final Cut"). Rendered via the ``{edition}``
            placeholder in the FILENAME format; Plex reads the tag off the file.
            Passed as a real argument rather than concatenated into ``movie_name``
            so ``clean_movie_name`` cannot flatten it (#576).
        already_clean: True when ``movie_name`` is a canonical TMDB title that must
            not be run through ``clean_movie_name`` (which is built for volume
            labels and would turn "Spider-Man" into "Spider Man").
```

- [ ] **Step 4: Rewire the name derivation**

Replace `backend/app/core/organizer.py:379-385` (from the `# Clean and sanitize the movie name` comment through `file_name = f"{folder_name}.mkv"`) with:

```python
    # Clean the movie name unless the caller says it is already canonical.
    # clean_movie_name is built for volume labels (THE_SOCIAL_NETWORK) and would
    # corrupt a TMDB title (Spider-Man -> Spider Man, Se7en -> Se7En).
    clean_name = movie_name if already_clean else clean_movie_name(movie_name)

    # Load naming formats from config
    cfg = get_config_sync()
    folder_name = format_movie_folder(cfg.naming_movie_format, clean_name, year, tmdb_id)
    # A blank filename format means "reuse the folder format", which is exactly
    # the pre-#576 behavior (the folder name was used verbatim as the filename).
    file_fmt = (getattr(cfg, "naming_movie_file_format", "") or "").strip()
    file_stem = format_movie_filename(
        file_fmt or cfg.naming_movie_format, clean_name, year, tmdb_id, edition
    )
    file_name = f"{file_stem}.mkv"
```

- [ ] **Step 5: Forward the new arguments through `MovieOrganizer`**

Replace `backend/app/core/organizer.py:467-479` (the `MovieOrganizer.organize` method) with:

```python
    def organize(
        self,
        staging_dir: Path,
        volume_label: str,
        detected_name: str | None = None,
        year: int | None = None,
        *,
        tmdb_id: str | int | None = None,
        edition: str | None = None,
        already_clean: bool = False,
    ) -> dict:
        """Organize a movie from staging to library.

        Uses detected_name if provided, otherwise falls back to volume_label.
        """
        movie_name = detected_name or volume_label
        return organize_movie(
            staging_dir,
            movie_name,
            year,
            tmdb_id=tmdb_id,
            edition=edition,
            already_clean=already_clean,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_organization_paths.py -v`
Expected: PASS, including all six new cases and every pre-existing movie case.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/core/organizer.py backend/tests/pipeline/test_organization_paths.py
git commit -m "feat(organizer): thread tmdb_id/edition/already_clean into organize_movie (#576)"
```

---

## Task 5: Teach the TMDB signal and resolver about movie release years

**Files:**
- Modify: `backend/app/core/tmdb_classifier.py:107-149` (`TmdbSignal`), `:421-432` (`_make_movie_signal`)
- Modify: `backend/app/services/identification_coordinator.py:162-187` (add `_resolve_movie_year` and `_resolve_year` after `_resolve_show_year`)
- Test: `backend/tests/unit/test_movie_year_resolution.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_movie_year_resolution.py`:

```python
"""Movie release-year resolution (issue #576).

Before this change, movie jobs called _resolve_show_year, which queries the TV
endpoint with a movie id. That either returned nothing or an unrelated show's
first-air year.
"""

from unittest.mock import patch

from app.core.tmdb_classifier import TmdbSignal, _make_movie_signal
from app.models import ContentType
from app.services.identification_coordinator import _resolve_movie_year, _resolve_year


class TestMovieSignalYear:
    def test_release_date_captured_from_search_result(self):
        signal = _make_movie_signal(
            {"id": 78, "title": "Blade Runner", "release_date": "1982-06-25", "popularity": 30.0}
        )
        assert signal.year == 1982

    def test_missing_release_date_is_none(self):
        signal = _make_movie_signal({"id": 78, "title": "Blade Runner", "popularity": 30.0})
        assert signal.year is None

    def test_malformed_release_date_is_none(self):
        signal = _make_movie_signal(
            {"id": 78, "title": "Blade Runner", "release_date": "", "popularity": 30.0}
        )
        assert signal.year is None


class TestResolveMovieYear:
    def test_signal_fast_path_avoids_network(self):
        signal = TmdbSignal(ContentType.MOVIE, 0.9, tmdb_id=78, tmdb_name="Blade Runner", year=1982)
        with patch("app.matcher.tmdb_client.fetch_movie_details") as fetch:
            assert _resolve_movie_year(78, signal) == 1982
            fetch.assert_not_called()

    def test_falls_back_to_movie_details(self):
        with patch(
            "app.matcher.tmdb_client.fetch_movie_details",
            return_value={"release_date": "1982-06-25"},
        ):
            assert _resolve_movie_year(78, None) == 1982

    def test_falsy_id_short_circuits(self):
        with patch("app.matcher.tmdb_client.fetch_movie_details") as fetch:
            assert _resolve_movie_year(None, None) is None
            fetch.assert_not_called()

    def test_unknown_year_is_none(self):
        with patch("app.matcher.tmdb_client.fetch_movie_details", return_value=None):
            assert _resolve_movie_year(78, None) is None


class TestResolveYearDispatch:
    def test_movie_never_hits_the_tv_endpoint(self):
        """Regression guard: a movie id must not be sent to /tv/{id}."""
        with (
            patch("app.matcher.tmdb_client.fetch_show_details") as show,
            patch(
                "app.matcher.tmdb_client.fetch_movie_details",
                return_value={"release_date": "1982-06-25"},
            ),
        ):
            assert _resolve_year(ContentType.MOVIE, 78, None) == 1982
            show.assert_not_called()

    def test_tv_still_uses_the_show_resolver(self):
        with patch(
            "app.matcher.tmdb_client.fetch_show_details",
            return_value={"first_air_date": "1993-09-16"},
        ):
            assert _resolve_year(ContentType.TV, 3452, None) == 1993
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_movie_year_resolution.py -v`
Expected: FAIL at collection with `ImportError: cannot import name '_resolve_movie_year'`.

- [ ] **Step 3: Add `year` to `TmdbSignal`**

In `backend/app/core/tmdb_classifier.py`, make three edits to the class at lines 107-149.

Add `"year"` to `__slots__` (after `"tmdb_name"`):

```python
    __slots__ = (
        "content_type",
        "confidence",
        "tmdb_id",
        "tmdb_name",
        "year",
        "ambiguous_identity",
        "candidates",
        "all_candidates",
    )
```

Add the parameter to `__init__` (after `tmdb_name`) and assign it (after `self.tmdb_name = tmdb_name`):

```python
        tmdb_name: str | None = None,
        year: int | None = None,
```

```python
        self.tmdb_name = tmdb_name
        # Release year for movies (first-air year for shows is resolved separately
        # from same-name candidates). Captured here so the organizer's {year}
        # placeholder needs no extra network call (#576).
        self.year = year
```

Add it to `__repr__`, changing the `tmdb_name` line to:

```python
            f"tmdb_name={self.tmdb_name!r}, year={self.year}, "
```

- [ ] **Step 4: Capture `release_date` in `_make_movie_signal`**

Replace `backend/app/core/tmdb_classifier.py:421-432` in full:

```python
def _make_movie_signal(result: dict, ambiguous: bool = False) -> TmdbSignal:
    """Build a MOVIE TmdbSignal from a TMDB search result."""
    popularity = result.get("popularity", 0)
    confidence = _confidence_from_popularity(popularity, ambiguous)
    name = result.get("title", result.get("original_title", ""))
    # The search payload already carries release_date, so capturing the year here
    # costs nothing and spares the organizer a /movie/{id} round trip (#576).
    raw_year = (result.get("release_date") or "")[:4]
    year = int(raw_year) if raw_year.isdigit() else None
    logger.info(f"TMDB: Movie match '{name}' (id={result['id']}, popularity={popularity:.1f})")
    return TmdbSignal(
        content_type=ContentType.MOVIE,
        confidence=confidence,
        tmdb_id=result["id"],
        tmdb_name=name,
        year=year,
    )
```

- [ ] **Step 5: Add the movie resolver and the dispatcher**

In `backend/app/services/identification_coordinator.py`, immediately after `_resolve_show_year` (which ends at line 187), add:

```python
def _resolve_movie_year(tmdb_id: int | None, signal=None) -> int | None:
    """Release year for a movie, for library-folder disambiguation.

    Mirrors ``_resolve_show_year`` but reads the MOVIE endpoint. No-network fast
    path: the search signal already carries the year. Universal fallback: cached
    TMDB movie details. Returns None when unknown, and the organizer then degrades
    to a year-less folder. Sync (blocking on the fallback), so call it via
    ``asyncio.to_thread``.
    """
    # Falsy guard (not ``is None``) so a 0/empty id short-circuits instead of
    # making a pointless fetch_movie_details(0) call.
    if not tmdb_id:
        return None
    signal_year = getattr(signal, "year", None) if signal else None
    if signal_year:
        return signal_year
    from app.matcher.tmdb_client import fetch_movie_details

    details = fetch_movie_details(tmdb_id)
    if details:
        rd = (details.get("release_date") or "")[:4]
        if rd.isdigit():
            return int(rd)
    return None


def _resolve_year(content_type, tmdb_id: int | None, signal=None) -> int | None:
    """Content-type-aware year resolution.

    Movies previously fell through to ``_resolve_show_year``, which queries
    ``/tv/{id}`` with a MOVIE id: that silently returns either nothing or an
    unrelated show's first-air year. Every ``job.tmdb_year`` assignment goes
    through this dispatcher so the two can never diverge again (#576).
    """
    if content_type == ContentType.MOVIE:
        return _resolve_movie_year(tmdb_id, signal)
    return _resolve_show_year(tmdb_id, signal)
```

`ContentType` is already imported in this module (used at line 882).

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_movie_year_resolution.py -v`
Expected: PASS, 10 passed.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/core/tmdb_classifier.py backend/app/services/identification_coordinator.py backend/tests/unit/test_movie_year_resolution.py
git commit -m "feat(tmdb): resolve movie release year instead of querying /tv (#576)"
```

---

## Task 6: Route all three `job.tmdb_year` assignments through the dispatcher

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:378-380`, `:890`, `:972-974`

- [ ] **Step 1: Update the main identify path (line 378)**

Replace:

```python
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, tmdb_signal
                )
```

with:

```python
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_year, analysis.content_type, analysis.tmdb_id, tmdb_signal
                )
```

- [ ] **Step 2: Update the missing-id backfill path (line 890)**

Replace:

```python
                job.tmdb_year = await asyncio.to_thread(_resolve_show_year, signal.tmdb_id, signal)
```

with:

```python
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_year, job.content_type, signal.tmdb_id, signal
                )
```

- [ ] **Step 3: Update the staging-import path (line 972)**

Replace:

```python
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_show_year, analysis.tmdb_id, _signal
                )
```

with:

```python
                job.tmdb_year = await asyncio.to_thread(
                    _resolve_year, analysis.content_type, analysis.tmdb_id, _signal
                )
```

- [ ] **Step 4: Confirm no call site was missed**

Run: `uv run python -c "import re,io; s=io.open('app/services/identification_coordinator.py',encoding='utf-8').read(); print(re.findall(r'_resolve_show_year', s))"`

Expected output: `['_resolve_show_year', '_resolve_show_year']`

That is exactly two: the function definition, and the single reference inside `_resolve_year`. Any third occurrence means a call site was missed.

- [ ] **Step 5: Run the affected test suites**

Run: `uv run pytest tests/unit/test_movie_year_resolution.py tests/pipeline/ -v`
Expected: PASS.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/services/identification_coordinator.py
git commit -m "fix(identify): resolve tmdb_year per content type (#576)"
```

---

## Task 7: Thread identity through the review-path organize call

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py:1444-1463`

This is where the `{edition-...}` string concat bug lives.

- [ ] **Step 1: Replace the edition concat and both organize calls**

Replace `backend/app/services/finalization_coordinator.py:1444-1463` in full:

```python
                        # Edition is passed as a real argument now: concatenating
                        # "{edition-X}" into the title let clean_movie_name flatten
                        # it to "{Edition X}", which Plex cannot parse (#576).
                        final_title = job.detected_title or job.volume_label
                        # Read job fields before the blocking organize so no
                        # attribute access outlives the session.
                        _tmdb_year = job.tmdb_year
                        _tmdb_id = job.tmdb_id
                        _already_clean = bool(job.tmdb_name)
                        _edition = title.edition or edition

                        _lib_path = _library_path_for_job(job, "movie")
                        if _lib_path:
                            org_result = await asyncio.to_thread(
                                partial(
                                    organize_movie,
                                    source_file,
                                    final_title,
                                    _tmdb_year,
                                    _lib_path,
                                    tmdb_id=_tmdb_id,
                                    edition=_edition,
                                    already_clean=_already_clean,
                                )
                            )
                        else:
                            org_result = await asyncio.to_thread(
                                partial(
                                    movie_organizer.organize,
                                    source_file,
                                    job.volume_label,
                                    final_title,
                                    _tmdb_year,
                                    tmdb_id=_tmdb_id,
                                    edition=_edition,
                                    already_clean=_already_clean,
                                )
                            )
```

`asyncio.to_thread` forwards `**kwargs` to the callable, but wrapping in `functools.partial` keeps the call readable and avoids any ambiguity between `to_thread`'s own arguments and the target's. Add the import at the top of the file if it is not already present:

```python
from functools import partial
```

- [ ] **Step 2: Verify the import**

Run: `uv run python -c "import io; s=io.open('app/services/finalization_coordinator.py',encoding='utf-8').read(); print('from functools import partial' in s)"`
Expected: `True`

- [ ] **Step 3: Confirm the mangling bug is gone**

Run:
```bash
uv run python -c "import io; s=io.open('app/services/finalization_coordinator.py',encoding='utf-8').read(); print('edition-' in s.replace('{edition-{edition}}',''))"
```
Expected: `False`. A `True` means the old `f\"{final_title} {{edition-{edition}}}\"` concat is still present.

- [ ] **Step 4: Run the integration and pipeline suites**

Run: `uv run pytest tests/pipeline/ tests/integration/test_movie_edition_workflow.py -v`
Expected: PASS. If `test_movie_edition_workflow.py` asserts the old mangled folder name, update the assertion to the new filename-based tag (`Blade Runner (1982) {edition-Final Cut}.mkv`) and note the change in the commit message.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/services/finalization_coordinator.py
git commit -m "fix(organize): pass movie edition as an argument, not a title suffix (#576)"
```

---

## Task 8: Thread identity through the auto-path organize call

**Files:**
- Modify: `backend/app/services/job_manager.py:2981-2994`

- [ ] **Step 1: Capture the job fields before the session closes and pass them**

Replace `backend/app/services/job_manager.py:2981-2994`:

```python
            # Single title flow (Standard Movie). Commit ORGANIZING and
            # release the session before the blocking organize so no DB
            # connection is held across it (mirrors the rip itself).
            job.state = JobState.ORGANIZING
            # Read identity off the job while the session is still open; the
            # organize below runs after it closes.
            _tmdb_year = job.tmdb_year
            _tmdb_id = job.tmdb_id
            _already_clean = bool(job.tmdb_name)
            await session.commit()

        await ws_manager.broadcast_job_update(job_id, JobState.ORGANIZING.value)

        organize_result = await asyncio.to_thread(
            partial(
                movie_organizer.organize,
                output_dir,
                volume_label,
                detected_title,
                _tmdb_year,
                tmdb_id=_tmdb_id,
                already_clean=_already_clean,
            )
        )
```

There is no `edition` on this path: it is the unattended single-title flow with no user review, so no edition has been chosen.

Add the import at the top of the file if not already present:

```python
from functools import partial
```

- [ ] **Step 2: Verify the import**

Run: `uv run python -c "import io; s=io.open('app/services/job_manager.py',encoding='utf-8').read(); print('from functools import partial' in s)"`
Expected: `True`

- [ ] **Step 3: Run the backend test tiers**

Run: `uv run pytest tests/unit/ tests/pipeline/ -q`
Expected: PASS.

> Per the repo's testing notes, the full unit tier can exceed 300 seconds. If you are a subagent with a timeout, run `tests/pipeline/` first and the unit tier in a second invocation.

- [ ] **Step 4: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add backend/app/services/job_manager.py
git commit -m "feat(organize): pass tmdb identity into the auto movie organize path (#576)"
```

---

## Task 9: Add the movie naming Settings UI

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx:130` (state type), `:210` (defaults), `:326` (load), `:511` (save), and insert a new block after line 1683

`namingMovieFormat` already round-trips through state but is never rendered. This task renders it and adds its filename sibling.

- [ ] **Step 1: Add the state field to the type**

In `frontend/src/components/ConfigWizard.tsx`, after line 130 (`namingMovieFormat: string;`), add:

```typescript
    namingMovieFileFormat: string;
```

- [ ] **Step 2: Add the default**

After line 210 (`namingMovieFormat: '{title} ({year})',`), add:

```typescript
        namingMovieFileFormat: '{title} ({year}) {{edition-{edition}}}',
```

- [ ] **Step 3: Add the load mapping**

After line 326, add:

```typescript
                    namingMovieFileFormat: data.naming_movie_file_format || '{title} ({year}) {{edition-{edition}}}',
```

- [ ] **Step 4: Add the save mapping**

After line 511 (`naming_movie_format: config.namingMovieFormat,`), add:

```typescript
                    naming_movie_file_format: config.namingMovieFileFormat,
```

- [ ] **Step 5: Render the Movies subsection**

In `frontend/src/components/ConfigWizard.tsx`, insert this block after line 1683 (the `)}` that closes the custom-TV-format conditional) and before line 1685 (`</div>`):

```tsx
                        <div className="form-group">
                            <label htmlFor="namingMovieFormat">Movie Folder Format</label>
                            <input
                                id="namingMovieFormat"
                                type="text"
                                value={config.namingMovieFormat}
                                onChange={(e) => handleInputChange('namingMovieFormat', e.target.value)}
                                placeholder="{title} ({year})"
                            />
                            <span className="form-hint">
                                Placeholders: {'{title}'}, {'{year}'}, {'{tmdb_id}'}. To let
                                same-title movies coexist, use Plex{' '}
                                &quot;{'{title} ({year}) {{tmdb-{tmdb_id}}}'}&quot; or Jellyfin{' '}
                                &quot;{'{title} ({year}) [tmdbid-{tmdb_id}]'}&quot;.
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="namingMovieFileFormat">Movie Filename Format</label>
                            <input
                                id="namingMovieFileFormat"
                                type="text"
                                value={config.namingMovieFileFormat}
                                onChange={(e) => handleInputChange('namingMovieFileFormat', e.target.value)}
                                placeholder="{title} ({year}) {{edition-{edition}}}"
                            />
                            <span className="form-hint">
                                Placeholders: {'{title}'}, {'{year}'}, {'{tmdb_id}'}, {'{edition}'}.
                                Plex reads the edition tag off the filename. Leave blank to reuse the
                                folder format. Empty tags are removed, so a movie with no edition is
                                named exactly like its folder.
                            </span>
                            <span className="form-hint">
                                Preview: Movies/{config.namingMovieFormat.replace('{title}', 'Blade Runner').replace('{year}', '1982').replace('{tmdb_id}', '78')}/{(config.namingMovieFileFormat || config.namingMovieFormat).replace('{title}', 'Blade Runner').replace('{year}', '1982').replace('{tmdb_id}', '78').replace('{edition}', 'Final Cut')}.mkv
                            </span>
                        </div>
```

- [ ] **Step 6: Verify the build and lint**

Run from `frontend/`: `npm run build && npm run lint`
Expected: both succeed with no TypeScript errors.

> If `node_modules` is absent in this worktree, run `npm install` first, then `git checkout package-lock.json` before committing: a fresh install rewrites the lockfile with a large spurious diff.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(ui): add movie folder and filename format settings (#576)"
```

---

## Task 10: Documentation and changelog

**Files:**
- Modify: `docs/getting-started/configuration.md` (the "Naming Conventions" section, currently at line 191)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Extend the naming documentation**

Read `docs/getting-started/configuration.md` around line 191 first and match its existing formatting. Add a movie subsection covering both format strings:

```markdown
#### Movie naming

| Setting | Default | Placeholders |
|---------|---------|--------------|
| Movie Folder Format | `{title} ({year})` | `{title}`, `{year}`, `{tmdb_id}` |
| Movie Filename Format | `{title} ({year}) {{edition-{edition}}}` | `{title}`, `{year}`, `{tmdb_id}`, `{edition}` |

Empty groups are removed automatically, so a movie with no edition or no known
year does not leave a stray `{edition-}` or `()` behind.

To let same-title movies coexist, add the id tag your media server expects:

- Plex: `{title} ({year}) {{tmdb-{tmdb_id}}}`
- Jellyfin: `{title} ({year}) [tmdbid-{tmdb_id}]`

Plex reads the edition tag from the *filename*, not the folder, which is why
`{edition}` is available on the filename format only.
```

- [ ] **Step 2: Add the changelog entries**

In `CHANGELOG.md`, under `## [Unreleased]`, add (creating the subsection headers if they do not exist):

```markdown
### Added

- **Movie naming is now configurable, matching TV.** Settings gains a Movie Folder Format and a Movie Filename Format, with `{title}`, `{year}`, `{tmdb_id}`, and (on the filename) `{edition}`. Same-title movies can now coexist using the Plex `{tmdb-NNNN}` or Jellyfin `[tmdbid-NNNN]` tag, exactly as TV shows already could. (#576, thanks @DrAquafreshhh!)

### Fixed

- **Movie folders were missing their year.** The default movie format has always read `{title} ({year})`, but no year was ever passed to the organizer, so every movie landed in a bare `Blade Runner/` folder. Movies now resolve their release year from TMDB. **Note:** newly ripped movies will land in `Blade Runner (1982)/` rather than `Blade Runner/`. Existing folders are not touched or moved. To keep the old layout, set Movie Folder Format to `{title}` in Settings. (#576)
- **Plex edition tags were mangled.** An edition chosen in the review queue was written as `{Edition Final Cut}` instead of `{edition-Final Cut}`, which Plex cannot parse. The tag is now emitted correctly and placed on the filename, where Plex expects it. (#576)
- **Movie titles from TMDB were being corrupted.** The volume-label normalizer was applied to titles TMDB had already returned clean, turning `Spider-Man: No Way Home` into `Spider Man: No Way Home`, `WALL-E` into `Wall E`, and `Se7en` into `Se7En`. Canonical TMDB titles are now used verbatim. (#576)
- **Movie jobs queried the TV endpoint.** Year resolution sent movie TMDB ids to `/tv/{id}`, which could silently return an unrelated show's first-air year. Year resolution is now content-type aware. (#576)
```

- [ ] **Step 3: Verify the docs build**

Run from the repository root:
```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```
Expected: build succeeds. Roughly 18 warnings are pre-existing and expected; do not add `--strict`.

- [ ] **Step 4: Commit**

```bash
git add docs/getting-started/configuration.md CHANGELOG.md
git commit -m "docs: document movie naming formats (#576)"
```

---

## Final verification

- [ ] **Run the full backend suite**

```bash
uv run pytest tests/unit/ tests/pipeline/ tests/integration/ -q
```
Expected: PASS. Report the actual summary line; do not claim success without it.

- [ ] **Run the frontend build and lint**

```bash
npm run build && npm run lint
```

- [ ] **Manual smoke test with simulation**

Start the backend with `DEBUG=true` on a port that does not collide with another session, then:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc -H "Content-Type: application/json" -d '{"volume_label":"BLADE_RUNNER_1982","content_type":"movie","simulate_ripping":true}'
```

Confirm the organized path is `Blade Runner (1982)/Blade Runner (1982).mkv`, not a bare `Blade Runner/`.

- [ ] **Stop this session's servers**

Per the repository rules, kill the `uvicorn` and `makemkvcon` processes this session started, scoped by port, before opening the PR.

- [ ] **Open the PR and request review**

After `gh pr create`, post a comment on the PR: `@claude please review this PR`. The review workflow only fires on the `opened` event, so a comment is required to trigger it.

---

## Self-review notes

**Spec coverage:** every section of the design spec maps to a task. Layer 1 (renderer) is Tasks 1, 2, 4. Layer 2 (year resolution) is Tasks 5 and 6. Layer 3 (threading) is Tasks 7 and 8. Layer 4 (config and UI) is Tasks 3 and 9. Defaults and the migration note are in Task 3 and Task 10. Testing is embedded per task plus the final verification. The deferred library-rename appendix is intentionally not planned.

**Naming consistency across tasks:** `format_movie_folder(fmt, title, year, tmdb_id)` and `format_movie_filename(fmt, title, year, tmdb_id, edition)` are defined in Tasks 1 and 2 and used with those exact signatures in Task 4. `_resolve_movie_year(tmdb_id, signal)` and `_resolve_year(content_type, tmdb_id, signal)` are defined in Task 5 and called with those signatures in Task 6. The config field is `naming_movie_file_format` in Python and `namingMovieFileFormat` in TypeScript throughout.

**Known line-number drift:** every line reference is against the state of the repository at the time of writing. Tasks 1 through 4 all edit `organizer.py`, so line numbers cited in later tasks will have shifted. Locate the anchors by their quoted content rather than by number.
