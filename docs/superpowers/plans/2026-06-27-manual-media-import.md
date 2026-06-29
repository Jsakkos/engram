# Manual Media Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Engram's background import watch folder with an explicit, user-triggered manual import (top-bar button plus a two-pane picker modal with a live preview), and harden file discovery so nested `Disc/` folders and arbitrary layouts import correctly.

**Architecture:** A new recursive `import_scanner` module is the single source of truth for which MKV files belong to which job (grouped by show and season). Three REST endpoints (`browse`, `preview`, `start`) drive a React modal. The existing import-job pipeline is reused; the only pipeline change is that ingestion reads an explicit per-job file manifest instead of a non-recursive glob. The polling `StagingWatcher` is deleted.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/SQLite, Alembic; React 18 + TypeScript + Vite, `motion/react`, Synapse design primitives; pytest, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-06-27-manual-media-import-design.md`

---

## Environment notes (read first)

- Backend commands run from `backend/`. Frontend commands run from `frontend/`.
- This is a git worktree. Per project memory: the worktree `backend/engram.db` may be a 0-byte stub, so backend tests that touch the DB need `init_db()` to have run (the test fixtures below call it). The worktree `frontend/node_modules` may be absent: run `npm install` once before frontend work, and `git checkout package-lock.json` before committing if install rewrites it.
- Never run uvicorn with `--reload`. Terminate any servers you start when done.
- No em dashes in committed text (project style).

---

## Task 1: Recursive import scanner

**Files:**
- Create: `backend/app/core/import_scanner.py`
- Test: `backend/tests/unit/test_import_scanner.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_import_scanner.py`:

```python
"""Unit tests for the recursive manual-import scanner."""

from pathlib import Path

from app.core import import_scanner


def _mkv(p: Path, size: int = 1024) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0" * size)


def test_show_season_disc_layout_groups_per_season(tmp_path: Path):
    # The King of Queens case: Show / Season N / Disc N / *.mkv
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "t00.mkv")
    _mkv(show / "Season 1" / "Disc 2" / "t01.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "t02.mkv")

    scan = import_scanner.scan(show)

    assert scan.total_files == 3
    by_season = {u.season: u for u in scan.units}
    assert set(by_season) == {1, 2}
    assert len(by_season[1].files) == 2  # both disc folders rolled into season 1
    assert len(by_season[2].files) == 1
    assert all(u.show_name == "The King of Queens (1998)" for u in scan.units)


def test_disc_only_layout_no_season_is_flat(tmp_path: Path):
    show = tmp_path / "Show Title"
    _mkv(show / "Disc 1" / "a.mkv")
    _mkv(show / "Disc 2" / "b.mkv")

    scan = import_scanner.scan(show)

    assert scan.total_files == 2
    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.units[0].show_name == "Show Title"
    assert len(scan.units[0].files) == 2


def test_flat_loose_files(tmp_path: Path):
    show = tmp_path / "Seinfeld"
    _mkv(show / "e1.mkv")
    _mkv(show / "e2.mkv")

    scan = import_scanner.scan(show)

    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.total_files == 2


def test_loose_files_beside_season_folders_are_reported_not_merged(tmp_path: Path):
    show = tmp_path / "Mixed"
    _mkv(show / "Season 1" / "ep.mkv")
    _mkv(show / "stray.mkv")

    scan = import_scanner.scan(show)

    seasons = [u.season for u in scan.units]
    assert seasons == [1]
    assert [p.name for p in scan.loose_files] == ["stray.mkv"]
    assert scan.total_files == 2  # totals still count the loose file


def test_multiple_shows_under_picked_root(tmp_path: Path):
    _mkv(tmp_path / "King of Queens" / "Season 1" / "a.mkv")
    _mkv(tmp_path / "Seinfeld" / "Season 1" / "b.mkv")

    scan = import_scanner.scan(tmp_path)

    shows = {u.show_name for u in scan.units}
    assert shows == {"King of Queens", "Seinfeld"}


def test_single_file_target(tmp_path: Path):
    f = tmp_path / "Some Folder" / "movie.mkv"
    _mkv(f)

    scan = import_scanner.scan(f)

    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.units[0].files == [f]
    assert scan.units[0].show_name == "Some Folder"


def test_season_inferred_from_nearest_ancestor(tmp_path: Path):
    f = tmp_path / "Show" / "Season 03" / "Disc 2" / "x.mkv"
    _mkv(f)

    scan = import_scanner.scan(tmp_path / "Show")

    assert scan.units[0].season == 3


def test_underscore_show_name_is_cleaned(tmp_path: Path):
    show = tmp_path / "KING_OF_QUEENS"
    _mkv(show / "Season 1" / "a.mkv")

    scan = import_scanner.scan(show)

    assert scan.units[0].show_name == "KING OF QUEENS"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_import_scanner.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.import_scanner'`.

- [ ] **Step 3: Implement the scanner**

Create `backend/app/core/import_scanner.py`:

```python
"""Recursive scanner for manual media import.

Given a folder (or a single .mkv file) chosen by the user, walk the tree, find
every .mkv at any depth, and group the files into import units keyed by
(show, season). Intermediate folders that are not "Season NN" (for example
"Disc 1") are transparent: we recurse through them and roll their files up into
the inferred season.

This is the single source of truth for which files belong to which import job,
so the preview and the actual import are always consistent. Pure and
synchronous; callers run it via asyncio.to_thread. No network calls.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Matches "Season 1", "season 01", "Season 12", etc. (mirrors the old watcher).
_SEASON_RE = re.compile(r"^[Ss]eason\s*0*(\d+)$")

# Matches "Disc 1", "disc 02", etc. Disc folders are transparent grouping levels,
# not shows, so a "Show / Disc N / *.mkv" layout resolves to one show, not many.
_DISC_RE = re.compile(r"^[Dd]isc\s*0*\d+$")

# Bound the walk so a user pointing at a huge tree (or a symlink loop) can't
# hang the request. Surfaced as ImportScan.truncated when hit.
_MAX_FILES = 5000
_MAX_DEPTH = 12


@dataclass
class ImportUnit:
    show_name: str | None
    season: int | None
    files: list[Path]
    total_bytes: int


@dataclass
class ImportScan:
    root: Path
    units: list[ImportUnit]
    loose_files: list[Path]
    total_files: int
    total_bytes: int
    truncated: bool = False


def _clean_show(name: str) -> str:
    """Light cleanup of a folder name for use as a show title (keeps any year)."""
    cleaned = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    return cleaned or name


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _safe_dirs(p: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_dir(follow_symlinks=False):
                    out.append(Path(entry.path))
            except OSError:
                continue
    except OSError:
        return []
    return out


def _season_from_path(file: Path, root: Path) -> int | None:
    """Season from the nearest 'Season NN' ancestor of file under root, else None."""
    try:
        rel_parts = file.relative_to(root).parts
    except ValueError:
        rel_parts = file.parts
    for part in reversed(rel_parts[:-1]):  # exclude the filename itself
        m = _SEASON_RE.match(part)
        if m:
            return int(m.group(1))
    return None


def _iter_mkvs(root: Path) -> tuple[list[Path], bool]:
    """Recursively collect .mkv files under root, bounded by count and depth.

    Skips symlinked directories and any file whose resolved path escapes root,
    so a crafted symlink cannot surface outside files or cause a loop.
    """
    found: list[Path] = []
    truncated = False
    root_resolved = root.resolve()

    def walk(d: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if depth > _MAX_DEPTH:
            truncated = True
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for entry in entries:
            if len(found) >= _MAX_FILES:
                truncated = True
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(
                    ".mkv"
                ):
                    p = Path(entry.path)
                    try:
                        if not p.resolve().is_relative_to(root_resolved):
                            continue
                    except (OSError, ValueError):
                        continue
                    found.append(p)
            except OSError:
                continue

    walk(root, 0)
    return found, truncated


def scan(path: Path) -> ImportScan:
    """Scan a folder or single .mkv file into import units."""
    path = Path(path).expanduser()

    # Single-file target: one flat unit; show derived from the parent folder.
    if path.is_file():
        if path.suffix.lower() != ".mkv":
            return ImportScan(path.parent, [], [], 0, 0, False)
        size = _safe_size(path)
        unit = ImportUnit(_clean_show(path.parent.name), None, [path], size)
        return ImportScan(path.parent, [unit], [], 1, size, False)

    root = path
    files, truncated = _iter_mkvs(root)

    immediate_dirs = _safe_dirs(root)
    has_loose_top = any(f.parent == root for f in files)
    has_season_top = any(_SEASON_RE.match(d.name) for d in immediate_dirs)
    has_disc_top = any(_DISC_RE.match(d.name) for d in immediate_dirs)

    # The picked folder IS a single show when it directly holds media, season
    # folders, or disc folders. Only when its immediate children are none of
    # those do we treat each child as a separate show (a parent-of-shows folder).
    picked_is_show = has_loose_top or has_season_top or has_disc_top

    # Loose top-level files beside structured season folders are ambiguous; report
    # them rather than silently merging (preserves the old data-loss safeguard).
    loose_files: list[Path] = []
    structured = files
    if has_season_top and has_loose_top:
        loose_files = sorted(f for f in files if f.parent == root)
        structured = [f for f in files if f.parent != root]

    def show_for(file: Path) -> str | None:
        if picked_is_show:
            return _clean_show(root.name)
        try:
            rel = file.relative_to(root)
        except ValueError:
            return _clean_show(root.name)
        return _clean_show(rel.parts[0]) if len(rel.parts) > 1 else _clean_show(root.name)

    groups: dict[tuple[str | None, int | None], list[Path]] = defaultdict(list)
    for f in structured:
        groups[(show_for(f), _season_from_path(f, root))].append(f)

    units: list[ImportUnit] = []
    for (show, season), unit_files in sorted(
        groups.items(),
        key=lambda kv: (str(kv[0][0]), kv[0][1] if kv[0][1] is not None else -1),
    ):
        ordered = sorted(unit_files)
        units.append(ImportUnit(show, season, ordered, sum(_safe_size(f) for f in ordered)))

    total_files = len(structured) + len(loose_files)
    total_bytes = sum(u.total_bytes for u in units) + sum(_safe_size(f) for f in loose_files)
    return ImportScan(root, units, loose_files, total_files, total_bytes, truncated)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_import_scanner.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/import_scanner.py tests/unit/test_import_scanner.py && uv run ruff format app/core/import_scanner.py tests/unit/test_import_scanner.py
git add backend/app/core/import_scanner.py backend/tests/unit/test_import_scanner.py
git commit -m "feat(import): recursive import scanner grouped by show and season"
```

---

## Task 2: Add the `import_manifest_json` column

**Files:**
- Modify: `backend/app/models/disc_job.py` (DiscJob, after `staging_path`/`final_path` block near line 81)
- Create: a new Alembic migration under `backend/migrations/versions/`

- [ ] **Step 1: Add the model field**

In `backend/app/models/disc_job.py`, inside `class DiscJob`, directly after the `final_path` line (currently line 81), add:

```python
    # Explicit file manifest for manual imports (drive_id == "import").
    # JSON: {"root": "<picked folder abs path>", "files": ["<abs mkv>", ...]}.
    # When present, identify_from_staging ingests exactly these files instead of
    # a non-recursive glob, so files nested in Disc/ subfolders import correctly;
    # "root" is the in-place organize base for destination_mode == "in_place".
    import_manifest_json: str | None = Field(default=None)
```

- [ ] **Step 2: Generate the Alembic migration skeleton**

Run (this auto-sets `down_revision` to the current head):

```bash
cd backend && uv run alembic revision -m "add disc_jobs import_manifest_json"
```

Expected: prints `Generating .../migrations/versions/<hash>_add_disc_jobs_import_manifest_json.py ... done`.

- [ ] **Step 3: Fill in the migration body**

Open the generated file and replace the `upgrade`/`downgrade` bodies (leave the auto-generated `revision`/`down_revision` lines untouched):

```python
def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("import_manifest_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("import_manifest_json")
```

Ensure the imports at the top match the sibling migration `c4d8e1f0a2b3_add_disc_jobs_candidates_json.py`:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
```

- [ ] **Step 4: Verify the migration applies and the model reconciler covers frozen builds**

Run: `cd backend && uv run alembic upgrade head`
Expected: no error (applies the new revision).

Run: `cd backend && uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"`
Expected: no error. (`init_db` runs `_add_missing_columns`, which derives the column from model metadata for frozen builds.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/disc_job.py backend/migrations/versions/
git commit -m "feat(import): add disc_jobs.import_manifest_json column + migration"
```

---

## Task 3: Manifest-driven ingestion

**Files:**
- Modify: `backend/app/services/job_manager.py` (`create_job_from_staging`, signature near line 755, insert block near line 800)
- Modify: `backend/app/services/identification_coordinator.py` (`identify_from_staging`, glob at line 848)
- Test: `backend/tests/unit/test_import_manifest_ingestion.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_import_manifest_ingestion.py`:

```python
"""The import manifest, when present, selects the exact files to ingest."""

import json
from pathlib import Path

from app.models.disc_job import DiscJob


def _resolve_mkv_files(job: DiscJob) -> list[Path]:
    """Mirror of the selection logic in identify_from_staging (kept in sync)."""
    staging_dir = Path(job.staging_path)
    if job.import_manifest_json:
        manifest = json.loads(job.import_manifest_json)
        files = sorted(Path(f) for f in manifest.get("files", []))
        return [f for f in files if f.exists()]
    return sorted(staging_dir.glob("*.mkv"))


def test_manifest_files_win_over_glob(tmp_path: Path):
    season = tmp_path / "Season 1"
    (season / "Disc 1").mkdir(parents=True)
    nested = season / "Disc 1" / "ep.mkv"
    nested.write_bytes(b"0")
    (season / "stray.mkv").write_bytes(b"0")  # present in dir but NOT in manifest

    job = DiscJob(
        drive_id="import",
        staging_path=str(season),
        import_manifest_json=json.dumps({"root": str(tmp_path), "files": [str(nested)]}),
    )

    result = _resolve_mkv_files(job)
    assert result == [nested]  # nested disc file ingested; stray excluded


def test_no_manifest_falls_back_to_glob(tmp_path: Path):
    (tmp_path / "a.mkv").write_bytes(b"0")
    job = DiscJob(drive_id="staging", staging_path=str(tmp_path))
    result = _resolve_mkv_files(job)
    assert [p.name for p in result] == ["a.mkv"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_import_manifest_ingestion.py -q`
Expected: FAIL (the DiscJob has no `import_manifest_json` until Task 2 is merged; if Task 2 is already done this test still drives the production change below). If `import_manifest_json` is unknown, recheck Task 2.

- [ ] **Step 3a: Thread the manifest through `create_job_from_staging`**

In `backend/app/services/job_manager.py`, change the `create_job_from_staging` signature (near line 755) to add the parameter:

```python
    async def create_job_from_staging(
        self,
        staging_path: str,
        volume_label: str = "",
        content_type: str = "unknown",
        detected_title: str | None = None,
        detected_season: int | None = None,
        destination_mode: str = "library",
        drive_id: str = "staging",
        import_manifest: dict | None = None,
    ) -> int:
```

Then, where the `DiscJob(...)` is constructed (near line 800), add the manifest assignment right after the existing `if detected_season is not None:` block (before `session.add(job)`):

```python
                if import_manifest is not None:
                    import json

                    job.import_manifest_json = json.dumps(import_manifest)
```

- [ ] **Step 3b: Consume the manifest in `identify_from_staging`**

In `backend/app/services/identification_coordinator.py`, replace the single line at 848:

```python
                mkv_files = sorted(staging_dir.glob("*.mkv"))
```

with:

```python
                if job.import_manifest_json:
                    import json

                    manifest = json.loads(job.import_manifest_json)
                    mkv_files = sorted(Path(f) for f in manifest.get("files", []))
                    mkv_files = [f for f in mkv_files if f.exists()]
                else:
                    mkv_files = sorted(staging_dir.glob("*.mkv"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_import_manifest_ingestion.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/services/job_manager.py app/services/identification_coordinator.py tests/unit/test_import_manifest_ingestion.py && uv run ruff format app/services/job_manager.py app/services/identification_coordinator.py tests/unit/test_import_manifest_ingestion.py
git add backend/app/services/job_manager.py backend/app/services/identification_coordinator.py backend/tests/unit/test_import_manifest_ingestion.py
git commit -m "feat(import): ingest explicit file manifest in identify_from_staging"
```

---

## Task 4: `GET /api/import/browse` endpoint

**Files:**
- Modify: `backend/app/api/routes.py` (add `import os` and `import string` to the stdlib import block near lines 3-17; add the endpoint)
- Test: `backend/tests/integration/test_import_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_import_endpoints.py`:

```python
"""Endpoint tests for manual import: browse, preview, start."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mkv(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0" * 1024)


async def test_browse_lists_dirs_and_mkvs(client, tmp_path: Path):
    _mkv(tmp_path / "Season 1" / "a.mkv")
    _mkv(tmp_path / "loose.mkv")

    res = await client.get("/api/import/browse", params={"path": str(tmp_path)})
    assert res.status_code == 200
    data = res.json()
    names = {e["name"]: e for e in data["entries"]}
    assert names["Season 1"]["type"] == "dir"
    assert names["loose.mkv"]["type"] == "mkv"
    assert data["cwd"] == str(tmp_path.resolve())


async def test_browse_empty_path_returns_roots(client):
    res = await client.get("/api/import/browse", params={"path": ""})
    assert res.status_code == 200
    assert isinstance(res.json()["roots"], list)


async def test_browse_bad_path_400(client, tmp_path: Path):
    res = await client.get(
        "/api/import/browse", params={"path": str(tmp_path / "nope")}
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -q`
Expected: FAIL (404 on `/api/import/browse`, so the 200 assertions fail).

- [ ] **Step 3: Add imports and the endpoint**

In `backend/app/api/routes.py`, add to the stdlib import block (near the top, alongside `import json`):

```python
import os
import string
```

Then add this endpoint (place it after the existing config endpoints, anywhere inside the `router`):

```python
@router.get("/import/browse")
async def import_browse(path: str = "") -> dict:
    """Read-only directory listing for the manual-import picker.

    Localhost-only (CORS-restricted). Returns directory names with a shallow
    direct-child MKV count, plus selectable .mkv files. Never returns file
    contents. Empty path returns the drive roots (Windows) or / and home (POSIX).
    """
    if not path:
        if os.name == "nt":
            roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/", str(Path.home())]
        return {"cwd": None, "parent": None, "roots": roots, "entries": []}

    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries: list[dict] = []
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_dir(follow_symlinks=False):
                    count = 0
                    try:
                        for f in os.scandir(entry.path):
                            if f.is_file(follow_symlinks=False) and f.name.lower().endswith(
                                ".mkv"
                            ):
                                count += 1
                    except OSError:
                        count = 0
                    entries.append(
                        {"name": entry.name, "path": entry.path, "type": "dir", "mkv_count": count}
                    )
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".mkv"):
                    entries.append({"name": entry.name, "path": entry.path, "type": "mkv"})
            except OSError:
                continue
    except OSError:
        raise HTTPException(status_code=400, detail="Cannot read directory")

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    parent = str(p.parent) if p.parent != p else None
    logger.info("Import browse: %s (%d entries)", sanitize_log_value(str(p)), len(entries))
    return {"cwd": str(p), "parent": parent, "roots": [], "entries": entries}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -q`
Expected: PASS for the three browse tests (preview/start tests come in later tasks; they will appear once added).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py && uv run ruff format app/api/routes.py
git add backend/app/api/routes.py backend/tests/integration/test_import_endpoints.py
git commit -m "feat(import): GET /api/import/browse server directory listing"
```

---

## Task 5: `POST /api/import/preview` endpoint

**Files:**
- Modify: `backend/app/api/routes.py` (request model + endpoint)
- Modify: `backend/tests/integration/test_import_endpoints.py` (add tests)

- [ ] **Step 1: Add the failing tests**

Append to `backend/tests/integration/test_import_endpoints.py`:

```python
async def test_preview_groups_per_season(client, tmp_path: Path):
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "a.mkv")
    _mkv(show / "Season 1" / "Disc 2" / "b.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "c.mkv")

    res = await client.post("/api/import/preview", json={"path": str(show)})
    assert res.status_code == 200
    data = res.json()
    assert data["total_jobs"] == 2
    assert data["total_files"] == 3
    seasons = sorted(u["season"] for u in data["units"])
    assert seasons == [1, 2]


async def test_preview_bad_path_400(client, tmp_path: Path):
    res = await client.post("/api/import/preview", json={"path": str(tmp_path / "nope")})
    assert res.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -k preview -q`
Expected: FAIL (404 -> assertions fail).

- [ ] **Step 3: Add the request model and endpoint**

In `backend/app/api/routes.py`, add a request model near the other `BaseModel` request models (for example just above the `import_browse` endpoint):

```python
class ImportPathRequest(BaseModel):
    path: str


class ImportStartRequest(BaseModel):
    path: str
    destination_mode: str = "library"
```

Then add the preview endpoint:

```python
@router.post("/import/preview")
async def import_preview(req: ImportPathRequest) -> dict:
    """Scan a path and return the import units, loose files, and totals.

    Filesystem-only (no network); safe to call on each folder selection.
    """
    from app.core import import_scanner

    p = Path(req.path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {req.path}")

    scan = await asyncio.to_thread(import_scanner.scan, p)
    units = [
        {
            "show_name": u.show_name,
            "season": u.season,
            "file_count": len(u.files),
            "total_bytes": u.total_bytes,
        }
        for u in scan.units
    ]
    return {
        "root": str(scan.root),
        "units": units,
        "loose_files": [str(f) for f in scan.loose_files],
        "total_jobs": len(scan.units),
        "total_files": scan.total_files,
        "total_bytes": scan.total_bytes,
        "truncated": scan.truncated,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -k preview -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py && uv run ruff format app/api/routes.py
git add backend/app/api/routes.py backend/tests/integration/test_import_endpoints.py
git commit -m "feat(import): POST /api/import/preview scan summary"
```

---

## Task 6: `POST /api/import/start` endpoint

**Files:**
- Modify: `backend/app/api/routes.py` (endpoint)
- Modify: `backend/tests/integration/test_import_endpoints.py` (add test + DB cleanup fixture)

- [ ] **Step 1: Add the failing test and cleanup fixture**

First, add these imports to the TOP of `backend/tests/integration/test_import_endpoints.py` (with the existing imports, so ruff does not flag E402):

```python
from sqlalchemy import text

from app.database import async_session, init_db
```

Then append the fixture and tests to the end of the file:

```python
@pytest.fixture(autouse=True)
async def _clean_import_jobs():
    # start creates real jobs; clean import rows around each test in this module.
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs WHERE drive_id = 'import'"))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs WHERE drive_id = 'import'"))
        await session.commit()


async def test_start_creates_one_job_per_season_with_manifest(client, tmp_path: Path):
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "a.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "b.mkv")

    res = await client.post(
        "/api/import/start", json={"path": str(show), "destination_mode": "library"}
    )
    assert res.status_code == 200
    job_ids = res.json()["job_ids"]
    assert len(job_ids) == 2

    async with async_session() as session:
        from app.models.disc_job import DiscJob

        for jid in job_ids:
            job = await session.get(DiscJob, jid)
            assert job is not None
            assert job.drive_id == "import"
            assert job.import_manifest_json is not None


async def test_start_no_mkvs_400(client, tmp_path: Path):
    (tmp_path / "empty").mkdir()
    res = await client.post(
        "/api/import/start", json={"path": str(tmp_path / "empty")}
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -k start -q`
Expected: FAIL (404).

- [ ] **Step 3: Add the start endpoint**

In `backend/app/api/routes.py`, add:

```python
@router.post("/import/start")
async def import_start(req: ImportStartRequest) -> dict:
    """Create one import job per (show, season) unit from a chosen path.

    Each job gets an explicit file manifest, so nested Disc/ files import
    correctly. Remembers the path and destination as the next defaults.
    """
    from app.core import import_scanner
    from app.services import config_service
    from app.services.job_manager import job_manager

    if req.destination_mode not in ("library", "in_place"):
        raise HTTPException(status_code=400, detail="Invalid destination_mode")

    p = Path(req.path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {req.path}")

    scan = await asyncio.to_thread(import_scanner.scan, p)
    if not scan.units:
        raise HTTPException(status_code=400, detail="No MKV files found to import")

    root_str = str(scan.root)
    job_ids: list[int] = []
    for unit in scan.units:
        files = [str(f) for f in unit.files]
        staging = str(Path(files[0]).parent) if len(files) == 1 else os.path.commonpath(files)
        manifest = {"root": root_str, "files": files}
        jid = await job_manager.create_job_from_staging(
            staging_path=staging,
            content_type="tv" if unit.season is not None else "unknown",
            detected_title=unit.show_name,
            detected_season=unit.season,
            destination_mode=req.destination_mode,
            drive_id="import",
            import_manifest=manifest,
        )
        if jid != -1:
            job_ids.append(jid)

    await config_service.update_config(
        import_watch_path=req.path, import_destination_mode=req.destination_mode
    )
    logger.info("Import start: %s -> %d job(s)", sanitize_log_value(req.path), len(job_ids))
    return {"job_ids": job_ids}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_import_endpoints.py -q`
Expected: PASS (all import endpoint tests).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py && uv run ruff format app/api/routes.py
git add backend/app/api/routes.py backend/tests/integration/test_import_endpoints.py
git commit -m "feat(import): POST /api/import/start creating per-season jobs"
```

---

## Task 7: Per-job in-place destination

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py` (`_library_path_for_job`, lines 113-122)
- Test: `backend/tests/unit/test_import_in_place_root.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_import_in_place_root.py`:

```python
"""In-place imports organize under the per-job manifest root."""

import json
from pathlib import Path

from app.services.finalization_coordinator import _library_path_for_job


class _Job:
    def __init__(self, destination_mode, import_manifest_json=None, jid=1):
        self.destination_mode = destination_mode
        self.import_manifest_json = import_manifest_json
        self.id = jid


def test_library_mode_returns_none():
    job = _Job("library", json.dumps({"root": "/x", "files": []}))
    assert _library_path_for_job(job, "tv") is None


def test_in_place_uses_manifest_root_tv():
    job = _Job("in_place", json.dumps({"root": "/media/rips", "files": []}))
    assert _library_path_for_job(job, "tv") == Path("/media/rips") / "TV"


def test_in_place_uses_manifest_root_movie():
    job = _Job("in_place", json.dumps({"root": "/media/rips", "files": []}))
    assert _library_path_for_job(job, "movie") == Path("/media/rips") / "Movies"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_import_in_place_root.py -q`
Expected: FAIL (current code reads `cfg.import_watch_path`, so the manifest-root assertions fail).

- [ ] **Step 3: Rewrite `_library_path_for_job`**

In `backend/app/services/finalization_coordinator.py`, replace the function (lines 113-122):

```python
def _library_path_for_job(job, content_type: str) -> "Path | None":
    """Return a library_path override for in_place jobs, or None for library mode."""
    if job.destination_mode != "in_place":
        return None

    root = None
    manifest_json = getattr(job, "import_manifest_json", None)
    if manifest_json:
        try:
            root = json.loads(manifest_json).get("root")
        except (ValueError, TypeError):
            root = None

    if not root:
        # Backward-compat for any pre-existing in_place job created before manual
        # import: fall back to the legacy global watch path.
        from app.services.config_service import get_config_sync

        cfg = get_config_sync()
        root = cfg.import_watch_path

    if not root:
        logger.warning(
            "In-place import job %s has no manifest root; using library mode", job.id
        )
        return None

    return Path(root) / ("Movies" if content_type == "movie" else "TV")
```

Verify `json` and `logger` are imported at the top of `finalization_coordinator.py` (both are used elsewhere in the file; if `json` is missing, add `import json`).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_import_in_place_root.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/services/finalization_coordinator.py tests/unit/test_import_in_place_root.py && uv run ruff format app/services/finalization_coordinator.py tests/unit/test_import_in_place_root.py
git add backend/app/services/finalization_coordinator.py backend/tests/unit/test_import_in_place_root.py
git commit -m "feat(import): in-place organize under per-job manifest root"
```

---

## Task 8: Remove the StagingWatcher

**Files:**
- Delete: `backend/app/core/staging_watcher.py`
- Delete: `backend/tests/unit/test_staging_watcher.py`
- Modify: `backend/app/services/job_manager.py` (remove import, field, init block, reload method, shutdown stop, `_on_staging_event`)
- Modify: `backend/app/api/routes.py` (remove the watcher-reload trigger in PUT /api/config)
- Modify: `backend/app/services/cleanup_service.py` (comment wording only)

- [ ] **Step 1: Delete the watcher and its test**

```bash
git rm backend/app/core/staging_watcher.py backend/tests/unit/test_staging_watcher.py
```

- [ ] **Step 2: Remove the watcher wiring from `job_manager.py`**

- Delete the import (line 35): `from app.core.staging_watcher import StagingWatcher`
- Delete the field (line 146): `self._staging_watcher: StagingWatcher | None = None`
- Delete the init block in `start()` (lines ~282-292), the whole:

```python
        need_watcher = (
            config.staging_watch_enabled and config.staging_path
        ) or config.import_watch_path
        if need_watcher:
            self._staging_watcher = StagingWatcher(
                config.staging_path if config.staging_watch_enabled else "",
                import_watch_path=config.import_watch_path or None,
                import_destination_mode=config.import_destination_mode,
                config=config,
            )
            self._staging_watcher.set_async_callback(self._on_staging_event, self._loop)
            self._staging_watcher.start()
```

- Delete the entire `reload_staging_watcher` method (lines ~397-420).
- Delete the shutdown stop (lines ~425-426):

```python
        if self._staging_watcher:
            self._staging_watcher.stop()
```

- Delete the entire `_on_staging_event` method (lines ~489-513).

- [ ] **Step 3: Remove the reload trigger from `routes.py`**

In `backend/app/api/routes.py`, delete the watcher-reload block in the PUT /api/config handler (lines ~1456-1465):

```python
    # Reload the staging watcher if watch-related settings changed
    _watch_fields = {
        "staging_watch_enabled",
        "staging_path",
        "import_watch_path",
        "import_destination_mode",
    }
    if update_data.keys() & _watch_fields:
        from app.services.job_manager import job_manager

        await job_manager.reload_staging_watcher()
```

- [ ] **Step 4: Update stale comments in `cleanup_service.py`**

In `backend/app/services/cleanup_service.py`, the comment near lines 49-51 referring to "watch-folder import" / "import_watch_path or a subfolder of it" should read that import sources are the user-chosen import folder. Change the docstring fragment:

```python
        staging root, so deleting it just reclaims space. For a manual import
        (drive_id == "import"), staging_path is the user's *original* source
        folder on disk (the folder they picked, or a subfolder of it).
```

(The `drive_id == "import"` guard logic itself at line 63 is unchanged and still correct.)

- [ ] **Step 5: Verify nothing else references the watcher**

Run: `cd backend && uv run python -c "import app.main"`
Expected: imports cleanly (no `ImportError`/`AttributeError`).

Run: `grep -rn "staging_watcher\|StagingWatcher\|reload_staging_watcher\|_on_staging_event" backend/app`
Expected: no matches.

- [ ] **Step 6: Run the backend suite for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS. If any test imported `staging_watcher` indirectly, fix or remove that reference. (The simulation `insert-disc-from-staging` path uses `create_job_from_staging` directly, not the watcher, so it is unaffected.)

- [ ] **Step 7: Commit**

```bash
cd backend && uv run ruff check app && uv run ruff format app
git add -A backend/
git commit -m "refactor(import): remove StagingWatcher; manual import replaces polling"
```

---

## Task 9: Frontend API client helpers

**Files:**
- Modify: `frontend/src/api/client.ts` (append domain helpers + types)
- Test: `frontend/src/api/importClient.test.ts`

- [ ] **Step 0: One-time setup (if not already done)**

Run: `cd frontend && npm install`
(Per project memory, if this rewrites `package-lock.json`, run `git checkout package-lock.json` before any commit.)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/importClient.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { browseDir, previewImport, startImport } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockJson(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response);
}

describe("import client", () => {
  it("browseDir encodes the path query", async () => {
    const fetchMock = mockJson({ cwd: "/x", parent: null, roots: [], entries: [] });
    vi.stubGlobal("fetch", fetchMock);
    await browseDir("/media/My Rips");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/import/browse?path=");
    expect(url).toContain(encodeURIComponent("/media/My Rips"));
  });

  it("previewImport posts the path", async () => {
    const fetchMock = mockJson({ root: "/x", units: [], loose_files: [], total_jobs: 0, total_files: 0, total_bytes: 0, truncated: false });
    vi.stubGlobal("fetch", fetchMock);
    const res = await previewImport("/x");
    expect(res.total_jobs).toBe(0);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });

  it("startImport posts path and destination", async () => {
    const fetchMock = mockJson({ job_ids: [1, 2] });
    vi.stubGlobal("fetch", fetchMock);
    const res = await startImport("/x", "library");
    expect(res.job_ids).toEqual([1, 2]);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toEqual({ path: "/x", destination_mode: "library" });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm run test:unit -- importClient`
Expected: FAIL (`browseDir`/`previewImport`/`startImport` are not exported).

- [ ] **Step 3: Append the helpers and types to `client.ts`**

At the end of `frontend/src/api/client.ts`:

```typescript
// ---------------------------------------------------------------------------
// Manual import
// ---------------------------------------------------------------------------

export interface BrowseEntry {
  name: string;
  path: string;
  type: "dir" | "mkv";
  mkv_count?: number;
}

export interface BrowseResult {
  cwd: string | null;
  parent: string | null;
  roots: string[];
  entries: BrowseEntry[];
}

export interface PreviewUnit {
  show_name: string | null;
  season: number | null;
  file_count: number;
  total_bytes: number;
}

export interface PreviewResult {
  root: string;
  units: PreviewUnit[];
  loose_files: string[];
  total_jobs: number;
  total_files: number;
  total_bytes: number;
  truncated: boolean;
}

/** List a server directory for the import picker. Empty path returns roots. */
export async function browseDir(path: string): Promise<BrowseResult> {
  return apiFetch<BrowseResult>(`/api/import/browse?path=${encodeURIComponent(path)}`);
}

/** Scan a path and return the import units + totals (no job is created). */
export async function previewImport(path: string): Promise<PreviewResult> {
  return apiFetch<PreviewResult>("/api/import/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

/** Create one import job per (show, season) unit under path. */
export async function startImport(
  path: string,
  destinationMode: "library" | "in_place",
): Promise<{ job_ids: number[] }> {
  return apiFetch<{ job_ids: number[] }>("/api/import/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, destination_mode: destinationMode }),
  });
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm run test:unit -- importClient`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src/api/client.ts frontend/src/api/importClient.test.ts
git checkout package-lock.json 2>/dev/null || true
git commit -m "feat(import): frontend api helpers browseDir/previewImport/startImport"
```

---

## Task 10: ImportModal component

**Files:**
- Create: `frontend/src/components/ImportModal.tsx`
- Test: `frontend/src/components/ImportModal.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ImportModal.test.tsx`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ImportModal from "./ImportModal";
import * as client from "../api/client";

beforeEach(() => {
  vi.spyOn(client, "browseDir").mockResolvedValue({
    cwd: "/media",
    parent: "/",
    roots: [],
    entries: [
      { name: "King of Queens", path: "/media/King of Queens", type: "dir", mkv_count: 0 },
    ],
  });
  vi.spyOn(client, "previewImport").mockResolvedValue({
    root: "/media/King of Queens",
    units: [{ show_name: "King of Queens", season: 1, file_count: 25, total_bytes: 100 }],
    loose_files: [],
    total_jobs: 1,
    total_files: 25,
    total_bytes: 100,
    truncated: false,
  });
  vi.spyOn(client, "startImport").mockResolvedValue({ job_ids: [1] });
});

describe("ImportModal", () => {
  it("lists entries from the starting directory", async () => {
    render(<ImportModal onClose={() => {}} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => expect(screen.getByText("King of Queens")).toBeInTheDocument());
  });

  it("previews a folder and starts the import", async () => {
    const onClose = vi.fn();
    render(<ImportModal onClose={onClose} defaultPath="/media" defaultDestinationMode="library" />);
    await waitFor(() => screen.getByText("King of Queens"));
    fireEvent.click(screen.getByText("King of Queens"));
    await waitFor(() => expect(client.previewImport).toHaveBeenCalledWith("/media/King of Queens"));
    const startBtn = await screen.findByTestId("import-start-btn");
    fireEvent.click(startBtn);
    await waitFor(() => expect(client.startImport).toHaveBeenCalledWith("/media/King of Queens", "library"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm run test:unit -- ImportModal`
Expected: FAIL (`./ImportModal` does not exist).

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/ImportModal.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
import { IcoLibrary, IcoFilter, IcoError } from "../app/components/icons";
import { SvPanel, sv } from "../app/components/synapse";
import {
  browseDir,
  previewImport,
  startImport,
  type BrowseEntry,
  type PreviewResult,
} from "../api/client";

interface Props {
  onClose: () => void;
  defaultPath: string;
  defaultDestinationMode: "library" | "in_place";
}

function fmtBytes(n: number): string {
  if (n <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

export default function ImportModal({ onClose, defaultPath, defaultDestinationMode }: Props) {
  const [cwd, setCwd] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<BrowseEntry[]>([]);
  const [roots, setRoots] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [destMode, setDestMode] = useState<"library" | "in_place">(defaultDestinationMode);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const navigate = useCallback(async (path: string) => {
    setError(null);
    try {
      const res = await browseDir(path);
      setCwd(res.cwd);
      setParent(res.parent);
      setEntries(res.entries);
      setRoots(res.roots);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read directory");
    }
  }, []);

  useEffect(() => {
    navigate(defaultPath || "");
  }, [navigate, defaultPath]);

  const choose = useCallback(async (path: string) => {
    setSelected(path);
    setPreview(null);
    setError(null);
    try {
      setPreview(await previewImport(path));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not scan folder");
    }
  }, []);

  const onStart = useCallback(async () => {
    if (!selected || !preview || preview.total_jobs === 0) return;
    setStarting(true);
    setError(null);
    try {
      await startImport(selected, destMode);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed to start");
      setStarting(false);
    }
  }, [selected, preview, destMode, onClose]);

  const seasonsByShow = (p: PreviewResult) => {
    const map = new Map<string, typeof p.units>();
    for (const u of p.units) {
      const key = u.show_name ?? "Unknown";
      map.set(key, [...(map.get(key) ?? []), u]);
    }
    return [...map.entries()];
  };

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Import media"
    >
      <motion.div
        className="absolute inset-0"
        style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
        data-testid="import-backdrop"
      />
      <motion.div
        className="relative w-full"
        style={{ maxWidth: 820 }}
        initial={{ opacity: 0, scale: 0.96, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 16 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
      >
        <SvPanel glow pad={0} style={{ background: sv.bg1 }}>
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "14px 18px",
              borderBottom: `1px solid ${sv.line}`,
            }}
          >
            <IcoLibrary size={18} color={sv.cyan} />
            <span
              style={{
                fontFamily: sv.mono,
                fontWeight: 700,
                letterSpacing: "0.2em",
                fontSize: 13,
                color: sv.cyanHi,
              }}
            >
              IMPORT MEDIA
            </span>
            <button
              onClick={onClose}
              aria-label="Close"
              data-testid="import-close-btn"
              style={{
                marginLeft: "auto",
                background: "transparent",
                border: "none",
                color: sv.inkDim,
                cursor: "pointer",
                fontSize: 16,
              }}
            >
              ✕
            </button>
          </div>

          <div style={{ display: "flex", minHeight: 340 }}>
            {/* Left: navigator */}
            <div
              style={{
                width: "46%",
                borderRight: `1px solid ${sv.line}`,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <div
                style={{
                  padding: "8px 12px",
                  fontFamily: sv.mono,
                  fontSize: 10,
                  color: sv.inkFaint,
                  borderBottom: `1px solid ${sv.line}`,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {cwd ?? "Select a drive"}
              </div>
              <div style={{ flex: 1, overflow: "auto" }}>
                {parent !== null && (
                  <Row label=".." onClick={() => navigate(parent)} kind="dir" />
                )}
                {roots.map((r) => (
                  <Row key={r} label={r} onClick={() => navigate(r)} kind="dir" />
                ))}
                {entries.map((e) => (
                  <Row
                    key={e.path}
                    label={e.name}
                    count={e.type === "dir" ? e.mkv_count : undefined}
                    kind={e.type}
                    active={selected === e.path}
                    onClick={() =>
                      e.type === "dir"
                        ? (navigate(e.path), choose(e.path))
                        : choose(e.path)
                    }
                  />
                ))}
              </div>
            </div>

            {/* Right: preview */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
              <div
                style={{
                  padding: "8px 14px",
                  fontFamily: sv.mono,
                  fontSize: 9,
                  letterSpacing: "0.2em",
                  color: sv.inkFaint,
                  borderBottom: `1px solid ${sv.line}`,
                }}
              >
                PREVIEW
              </div>
              <div style={{ flex: 1, overflow: "auto", padding: 14 }}>
                {!preview && (
                  <p style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint }}>
                    Select a folder or file to preview.
                  </p>
                )}
                {preview && preview.total_jobs === 0 && (
                  <p style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>
                    No MKV files found here.
                  </p>
                )}
                {preview &&
                  seasonsByShow(preview).map(([show, units]) => (
                    <div key={show} style={{ marginBottom: 12 }}>
                      <div
                        style={{
                          fontFamily: sv.mono,
                          fontSize: 13,
                          color: sv.cyanHi,
                          marginBottom: 4,
                        }}
                      >
                        {show}
                      </div>
                      {units.map((u, i) => (
                        <div
                          key={i}
                          style={{
                            display: "flex",
                            gap: 8,
                            fontFamily: sv.mono,
                            fontSize: 11,
                            color: sv.inkDim,
                            padding: "3px 0",
                          }}
                        >
                          <span style={{ width: 90 }}>
                            {u.season != null ? `SEASON ${u.season}` : "ALL SEASONS"}
                          </span>
                          <span style={{ flex: 1 }}>{u.file_count} files</span>
                          <span style={{ color: sv.cyan }}>1 job</span>
                        </div>
                      ))}
                    </div>
                  ))}

                {preview && preview.loose_files.length > 0 && (
                  <Notice
                    text={`${preview.loose_files.length} loose file(s) have no Season folder; they will match across all seasons.`}
                  />
                )}
                {preview?.truncated && (
                  <Notice text="This folder is very large; only part of it was scanned." />
                )}
                {error && <Notice text={error} tone="error" />}
              </div>

              {/* Destination */}
              <div style={{ padding: "10px 14px", borderTop: `1px solid ${sv.line}` }}>
                <div
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 9,
                    letterSpacing: "0.15em",
                    color: sv.inkFaint,
                    marginBottom: 6,
                  }}
                >
                  DESTINATION
                </div>
                <div style={{ display: "flex" }}>
                  {(["library", "in_place"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setDestMode(m)}
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        padding: "5px 11px",
                        cursor: "pointer",
                        border: `1px solid ${sv.lineMid}`,
                        background: destMode === m ? sv.cyan : "transparent",
                        color: destMode === m ? sv.bg0 : sv.inkDim,
                        fontWeight: destMode === m ? 700 : 400,
                        marginRight: m === "library" ? -1 : 0,
                      }}
                    >
                      {m === "library" ? "Organize into library" : "Organize in place"}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "12px 16px",
              borderTop: `1px solid ${sv.line}`,
            }}
          >
            <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
              {preview
                ? `${preview.total_jobs} jobs · ${preview.total_files} files · ${fmtBytes(preview.total_bytes)}`
                : ""}
            </span>
            <button
              onClick={onClose}
              style={{
                marginLeft: "auto",
                fontFamily: sv.mono,
                fontSize: 10,
                padding: "7px 14px",
                border: `1px solid ${sv.lineMid}`,
                background: "transparent",
                color: sv.inkDim,
                cursor: "pointer",
              }}
            >
              CANCEL
            </button>
            <button
              onClick={onStart}
              disabled={!preview || preview.total_jobs === 0 || starting}
              data-testid="import-start-btn"
              style={{
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.1em",
                padding: "7px 16px",
                border: `1px solid ${sv.cyan}`,
                background:
                  !preview || preview.total_jobs === 0 || starting ? "transparent" : sv.cyan,
                color: !preview || preview.total_jobs === 0 || starting ? sv.inkFaint : sv.bg0,
                cursor:
                  !preview || preview.total_jobs === 0 || starting ? "not-allowed" : "pointer",
              }}
            >
              {starting
                ? "STARTING…"
                : `START IMPORT${preview && preview.total_jobs ? ` · ${preview.total_jobs} JOBS` : ""}`}
            </button>
          </div>
        </SvPanel>
      </motion.div>
    </motion.div>
  );
}

function Row({
  label,
  count,
  kind,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  kind: "dir" | "mkv";
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        textAlign: "left",
        padding: "8px 12px",
        fontFamily: sv.mono,
        fontSize: 11,
        color: active ? sv.cyanHi : sv.inkDim,
        background: active ? `${sv.cyan}14` : "transparent",
        border: "none",
        borderBottom: `1px solid ${sv.line}`,
        boxShadow: active ? `inset 2px 0 0 ${sv.cyan}` : "none",
        cursor: "pointer",
      }}
    >
      <IcoFilter size={12} color={kind === "mkv" ? sv.inkFaint : sv.cyan} />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      {count != null && count > 0 && (
        <span style={{ fontSize: 9, color: sv.cyan }}>{count} mkv</span>
      )}
    </button>
  );
}

function Notice({ text, tone = "warn" }: { text: string; tone?: "warn" | "error" }) {
  const color = tone === "error" ? sv.red : sv.yellow;
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        alignItems: "flex-start",
        marginTop: 10,
        padding: "8px 10px",
        border: `1px solid ${color}4d`,
        background: `${color}14`,
      }}
    >
      <IcoError size={14} color={color} style={{ flexShrink: 0, marginTop: 1 }} />
      <span style={{ fontFamily: sv.mono, fontSize: 10, color, lineHeight: 1.5 }}>{text}</span>
    </div>
  );
}
```

Note: if `sv.red` is not a token (check `frontend/src/app/components/synapse/tokens.ts`), use `sv.yellow` for the error tone too, or the nearest red/danger token present.

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm run test:unit -- ImportModal`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src/components/ImportModal.tsx frontend/src/components/ImportModal.test.tsx
git checkout package-lock.json 2>/dev/null || true
git commit -m "feat(import): two-pane ImportModal with live preview"
```

---

## Task 11: Top-bar button + App wiring

**Files:**
- Modify: `frontend/src/app/components/synapse/SvTopBar.tsx` (add `onImportClick` prop + button)
- Modify: `frontend/src/app/App.tsx` (state, prop, modal render, defaults from config)

- [ ] **Step 1: Add the prop and button to `SvTopBar.tsx`**

Add `IcoLibrary` to the icons import (line 4):

```tsx
import { IcoSettings, IcoLibrary } from "../icons";
```

Add to the `Props` interface (after `onSettingsClick`):

```tsx
  onImportClick: () => void;
```

Add `onImportClick` to the destructured params in `SvTopBar({ ... })`. Then, in the right cluster (inside the `div` at line 95, before the `<SvBadge ...>`), add the button:

```tsx
        <button
          onClick={onImportClick}
          aria-label="Import media"
          title="Import media"
          data-testid="sv-import-btn"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            border: `1px solid ${sv.cyan}`,
            color: sv.cyan,
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.16em",
            padding: "7px 12px",
            cursor: "pointer",
            transition: "all 0.18s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${sv.cyan}1f`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IcoLibrary size={14} />
          IMPORT
        </button>
```

- [ ] **Step 2: Wire it in `App.tsx`**

Add state near the other modal state (after line 73 `const [namePromptJob, ...]`):

```tsx
  const [showImport, setShowImport] = useState(false);
```

Add the import of the modal near the other component imports (after line 14):

```tsx
import ImportModal from "../components/ImportModal";
```

Pass the prop to `SvTopBar` (the element at line 275, alongside `onSettingsClick`):

```tsx
        onImportClick={() => setShowImport(true)}
```

Render the modal near the other `AnimatePresence` modal blocks (for example just after the NamePromptModal `AnimatePresence` that ends at line 766):

```tsx
      <AnimatePresence>
        {showImport && (
          <ImportModal
            onClose={() => setShowImport(false)}
            defaultPath={importDefaultPath}
            defaultDestinationMode={importDefaultMode}
          />
        )}
      </AnimatePresence>
```

Provide the defaults. App already fetches config elsewhere; add lightweight state and a fetch in an existing effect or a new one. Add this state near the others:

```tsx
  const [importDefaultPath, setImportDefaultPath] = useState("");
  const [importDefaultMode, setImportDefaultMode] = useState<"library" | "in_place">("library");
```

And add an effect (place near the other `useEffect`s):

```tsx
  useEffect(() => {
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => {
        if (!cfg) return;
        setImportDefaultPath(cfg.import_watch_path || "");
        setImportDefaultMode(cfg.import_destination_mode === "in_place" ? "in_place" : "library");
      })
      .catch(() => {});
  }, [showImport]);
```

- [ ] **Step 3: Verify the build typechecks**

Run: `cd frontend && npm run build`
Expected: TypeScript compiles and Vite build succeeds. Fix any prop/type mismatch surfaced (for example a stray `onImportClick` required-prop error means another `SvTopBar` usage needs the prop; there is only one usage, in `App.tsx`).

- [ ] **Step 4: Commit**

```bash
cd frontend && npm run lint
git add frontend/src/app/components/synapse/SvTopBar.tsx frontend/src/app/App.tsx
git checkout package-lock.json 2>/dev/null || true
git commit -m "feat(import): top-bar IMPORT button opens ImportModal"
```

---

## Task 12: Remove the watch-folder section from ConfigWizard

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step 1: Remove the config-state field, initial value, load mapping, and save payload**

In `frontend/src/components/ConfigWizard.tsx`:
- Delete the type fields (lines 148-149):

```tsx
    importWatchPath: string;
    importDestinationMode: string;
```

- Delete the initial-state values (lines 227-228):

```tsx
        importWatchPath: '',
        importDestinationMode: 'library',
```

- Delete the load mapping (lines 339-340):

```tsx
                    importWatchPath: data.import_watch_path || '',
                    importDestinationMode: data.import_destination_mode || 'library',
```

- Delete the save payload (lines 496-497):

```tsx
                    import_watch_path: config.importWatchPath || null,
                    import_destination_mode: config.importDestinationMode,
```

- [ ] **Step 2: Remove the JSX section**

Delete the entire `<div className="form-group" ...>` block for "Import Watch Folder" (lines 731-795, from the opening `<div className="form-group" style={{ marginTop: '1.5rem' }}>` whose child is `<label htmlFor="importWatchPath">` through its matching closing `</div>`).

- [ ] **Step 3: Verify the build typechecks**

Run: `cd frontend && npm run build`
Expected: compiles. If a leftover reference to `config.importWatchPath` / `config.importDestinationMode` remains, the TS error will name the line; remove that reference too.

Run: `grep -rn "importWatchPath\|importDestinationMode" frontend/src`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
cd frontend && npm run lint
git add frontend/src/components/ConfigWizard.tsx
git checkout package-lock.json 2>/dev/null || true
git commit -m "feat(import): remove watch-folder settings (replaced by manual import)"
```

---

## Task 13: E2E test for manual import

**Files:**
- Create: `frontend/e2e/import-flow.spec.ts`

- [ ] **Step 1: Write the E2E spec**

Create `frontend/e2e/import-flow.spec.ts`. This drives the real UI; the backend must be running with `DEBUG=true`. It seeds files in a temp dir the backend can read, opens the modal, navigates, previews, and starts.

```typescript
import { test, expect } from "@playwright/test";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("manual import: browse, preview, start", async ({ page }) => {
  // Seed a Show/Season/Disc tree on the machine running the backend (same host
  // in dev/CI). The backend's browse endpoint reads it directly.
  const root = mkdtempSync(join(tmpdir(), "engram-import-"));
  const disc = join(root, "Demo Show", "Season 1", "Disc 1");
  mkdirSync(disc, { recursive: true });
  writeFileSync(join(disc, "t00.mkv"), Buffer.alloc(1024));
  writeFileSync(join(disc, "t01.mkv"), Buffer.alloc(1024));

  await page.goto("/");
  await page.getByTestId("sv-import-btn").click();
  await expect(page.getByText("IMPORT MEDIA")).toBeVisible();

  // Navigate into the seeded root by typing nothing; instead drive via the API
  // shortcut: the modal opens at the configured default path. For determinism,
  // navigate using the on-screen rows from the temp root's parent.
  // Simplest deterministic path: set the import default via config, then reopen.
  await page.evaluate(async (p) => {
    await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ import_watch_path: p }),
    });
  }, root);

  // Reopen so the modal starts at the seeded root.
  await page.getByTestId("import-close-btn").click();
  await page.getByTestId("sv-import-btn").click();

  await page.getByText("Demo Show").click();
  await expect(page.getByText("Demo Show")).toBeVisible();
  await expect(page.getByText(/SEASON 1/)).toBeVisible();

  await page.getByTestId("import-start-btn").click();
  // A job card should appear on the dashboard for the import.
  await expect(page.getByText(/Demo Show/i).first()).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 2: Run the E2E test**

Start the backend with DEBUG (separate terminal, from `backend/`): `DEBUG=true uv run uvicorn app.main:app` (bash) or set `$env:DEBUG="true"` first (PowerShell). Then:

Run: `cd frontend && npm run test:e2e -- import-flow`
Expected: PASS. If the dashboard text assertion is flaky, assert on a stable job-card testid instead. Stop the backend when done.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/import-flow.spec.ts
git commit -m "test(import): e2e manual import flow"
```

---

## Task 14: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Backend lint, format, and full test suite**

Run: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: ruff clean; all tests pass. (If a DB-dependent test errors with `no such table`, ensure `init_db()` has run against the worktree DB once.)

- [ ] **Step 2: Frontend lint, unit, build**

Run: `cd frontend && npm run lint && npm run test:unit && npm run build`
Expected: all green.

- [ ] **Step 3: Manual smoke (optional but recommended)**

Start one backend with `DEBUG=true` and the Vite dev server, click IMPORT, browse to a `Show/Season/Disc` tree, confirm the preview shows per-season units with disc files rolled in, start, and confirm per-season job cards appear. Stop all servers afterward (kill the `uvicorn`/`python` and any `makemkvcon` you started).

- [ ] **Step 4: Final commit / branch ready for PR**

```bash
git status
# Ensure package-lock.json is unchanged (git checkout it if install rewrote it).
```

The branch is ready for the finishing-a-development-branch flow (PR creation, `@claude please review this PR` comment, etc.).

---

## Spec coverage check

- Bug 1 (depth-rigid discovery): Task 1 (recursive scanner) + Task 3 (manifest ingestion). Covered.
- Bug 2 (partial-import lock-in): Task 8 (watcher removed). Covered by construction.
- Server-side browser: Task 4. Preview-then-confirm: Tasks 5 + 10. One job per (show, season): Tasks 1 + 6. Folder + single file: Task 1. Top-bar button: Task 11. Two-pane modal: Task 10.
- In-place per-job root: Task 7. Config repurposing: Task 6 (persist) + Task 12 (UI removal). Schema + frozen-build reconciler + Alembic: Task 2.
- Security (read-only browse, symlink-escape, sanitized logs): Tasks 1 + 4.
- Testing (scanner units, endpoints, in-place, modal, E2E): Tasks 1, 4, 5, 6, 7, 9, 10, 13, 14.

## Notes for the implementer

- Leave the matching season-pin guard (`matching_coordinator.py:978`) as-is; season-scoped imports still match correctly. Enabling pinning for them is future work.
- `staging_watch_enabled` becomes vestigial after Task 8. Leave the DB column for compatibility; it no longer drives behavior.
- Do not run uvicorn with `--reload`. Always terminate servers you start.
