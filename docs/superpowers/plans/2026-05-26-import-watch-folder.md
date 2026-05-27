# Import Watch Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `StagingWatcher` to watch a user-configurable import path for ARM-ripped MKV files, auto-detecting three folder structure patterns and creating Engram jobs that flow through the normal pipeline.

**Architecture:** Two new AppConfig fields (`import_watch_path`, `import_destination_mode`) and one new DiscJob field (`destination_mode`) carry the configuration. `StagingWatcher` gains a second scan mode — `_scan_import_path()` — that runs on the same poll loop as the existing staging scan and fires the same `staging_ready` callback with an extra `metadata` dict. `JobManager` reads that metadata to pass `destination_mode` into `create_job_from_staging()`. `FinalizationCoordinator` calls the bare `organize_tv_episode`/`organize_movie` functions with an overridden `library_path` when `destination_mode == "in_place"`.

**Tech Stack:** Python/SQLModel (backend), React/TypeScript (frontend), SQLite via `_add_missing_columns` for schema migration.

---

## File Map

| Action | File |
|--------|------|
| Modify | `backend/app/models/app_config.py` |
| Modify | `backend/app/models/disc_job.py` |
| Modify | `backend/app/core/staging_watcher.py` |
| Modify | `backend/tests/unit/test_staging_watcher.py` |
| Modify | `backend/app/services/job_manager.py` |
| Modify | `backend/app/services/finalization_coordinator.py` |
| Modify | `frontend/src/types/index.ts` |
| Modify | `frontend/src/types/adapters.ts` |
| Modify | `frontend/src/app/components/DiscCard.tsx` |
| Modify | `frontend/src/components/ConfigWizard.tsx` |

---

## Task 1: Data model additions

Add fields to `AppConfig` and `DiscJob`. `_add_missing_columns()` in `database.py` handles live-schema convergence automatically — no migration file needed.

**Files:**
- Modify: `backend/app/models/app_config.py`
- Modify: `backend/app/models/disc_job.py`

- [ ] **Step 1: Add two fields to AppConfig**

In `backend/app/models/app_config.py`, after the `staging_watch_enabled` line (≈ line 110), add:

```python
# Import watch folder (for ARM / external ripper ingestion)
import_watch_path: str | None = Field(default=None)
import_destination_mode: str = Field(
    default="library", sa_column_kwargs={"server_default": text("'library'")}
)
```

The `server_default` ensures existing database rows get `"library"` (not `""`) when `_add_missing_columns` adds the column.

- [ ] **Step 2: Add one field to DiscJob**

In `backend/app/models/disc_job.py`, after the `error_message` field (≈ line 95), add:

```python
destination_mode: str = Field(
    default="library", sa_column_kwargs={"server_default": text("'library'")}
)
```

Add the missing import at the top of the file if not already present:

```python
from sqlalchemy import text
from sqlmodel import Field, SQLModel
```

`disc_job.py` already imports `Field` and `SQLModel` but not `text`. Check before adding.

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/app_config.py backend/app/models/disc_job.py
git commit -m "feat(models): add import_watch_path, import_destination_mode, destination_mode fields"
```

---

## Task 2: StagingWatcher — structure detection (TDD)

Write and pass tests for the three ARM output structure patterns before touching `staging_watcher.py`.

**Files:**
- Modify: `backend/tests/unit/test_staging_watcher.py`
- Modify: `backend/app/core/staging_watcher.py`

### Step group A — Pattern A (per-disc subfolders)

- [ ] **Step 1: Write failing test for Pattern A detection**

Append to `backend/tests/unit/test_staging_watcher.py`:

```python
class TestImportWatcherStructureDetection:
    """Tests for ARM output structure detection in _scan_import_path."""

    async def test_pattern_a_per_disc_subfolders(self, tmp_path):
        """Direct subdirectory with MKVs → one import unit per subdir."""
        watch_root = tmp_path / "arm_output"
        watch_root.mkdir()
        disc1 = watch_root / "THE_OFFICE_S1D1"
        disc1.mkdir()
        (disc1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        (disc1 / "title_t02.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 1
        path, mkv_count, total_size, meta = units[0]
        assert path == disc1
        assert mkv_count == 2
        assert total_size == 3072
        assert meta["structure"] == "disc_folder"
        assert meta["show_name"] is None
        assert meta["season"] is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend
uv run pytest tests/unit/test_staging_watcher.py::TestImportWatcherStructureDetection::test_pattern_a_per_disc_subfolders -v
```

Expected: `FAILED` — `StagingWatcher.__init__` does not accept `import_watch_path`, `_scan_import_dir` does not exist.

- [ ] **Step 3: Add constructor params and stub `_scan_import_dir`**

In `backend/app/core/staging_watcher.py`, update `__init__`:

```python
def __init__(
    self,
    staging_path: str,
    import_watch_path: str | None = None,
    import_destination_mode: str = "library",
    config=None,
) -> None:
    self._staging_path = Path(staging_path).expanduser() if staging_path else None
    self._import_watch_path = Path(import_watch_path).expanduser() if import_watch_path else None
    self._import_destination_mode = import_destination_mode
    self._running = False
    self._task: asyncio.Task | None = None
    self._loop: asyncio.AbstractEventLoop | None = None
    self._async_callback: Callable[[str, str, str, dict | None], Any] | None = None
    self._config = config
    self._poll_interval: float = 2.0
    self._known_dirs: dict[str, dict] = {}
    self._processed_dirs: set[str] = set()
```

Note: `staging_path` can now be an empty string (handled by the `if staging_path else None` guard). The callback signature gains an optional fourth argument `metadata: dict | None = None`.

Add the `_scan_import_dir` method (Pattern A only for now):

```python
_SEASON_RE = re.compile(r"^[Ss]eason\s*0*(\d+)$")

def _scan_import_dir(self, root: Path) -> list[tuple[Path, int, int, dict]]:
    """Detect ARM output structure under root and return import units.

    Returns list of (dir_path, mkv_count, total_size, metadata) tuples.
    Runs synchronously; call via asyncio.to_thread() in production.
    """
    units = []
    try:
<<<<<<< HEAD
        for entry in os.scandir(root):
            if entry.is_file() and entry.name.lower().endswith(".mkv"):
                # Pattern C: MKVs directly in root — treat whole root as one unit
                mkv_count, total_size = self._count_mkvs(root)
                units.append((root, mkv_count, total_size, {
                    "structure": "flat",
                    "show_name": None,
                    "season": None,
                    "destination_mode": self._import_destination_mode,
                    "source": "import",
                }))
                return units  # Whole root is one unit; stop scanning

            if not entry.is_dir():
                continue

            subdir = Path(entry.path)
            # Check for Pattern B: subdir contains Season subdirs with MKVs
            season_units = self._try_pattern_b(subdir)
            if season_units:
                units.extend(season_units)
                continue

            # Pattern A: subdir directly contains MKVs
            mkv_count, total_size = self._count_mkvs(subdir)
            if mkv_count > 0:
                units.append((subdir, mkv_count, total_size, {
                    "structure": "disc_folder",
                    "show_name": None,
                    "season": None,
                    "destination_mode": self._import_destination_mode,
                    "source": "import",
                }))
    except OSError as e:
        logger.debug(f"Could not scan import directory {root}: {e}")
=======
        entries = list(os.scandir(root))
    except OSError as e:
        logger.debug(f"Could not scan import directory {root}: {e}")
        return units

    # Pattern C check first: any MKV directly in root → whole root is one unit.
    # Must be a pre-pass because os.scandir order is not guaranteed; without it a
    # directory appearing before a root-level MKV would add spurious A/B units.
    if any(e.is_file() and e.name.lower().endswith(".mkv") for e in entries):
        mkv_count, total_size = self._count_mkvs(root)
        units.append((root, mkv_count, total_size, {
            "structure": "flat",
            "show_name": None,
            "season": None,
            "destination_mode": self._import_destination_mode,
            "source": "import",
        }))
        return units

    # Pattern B / Pattern A: scan subdirectories only
    for entry in entries:
        if not entry.is_dir():
            continue
        subdir = Path(entry.path)
        season_units = self._try_pattern_b(subdir)
        if season_units:
            units.extend(season_units)
            continue
        mkv_count, total_size = self._count_mkvs(subdir)
        if mkv_count > 0:
            units.append((subdir, mkv_count, total_size, {
                "structure": "disc_folder",
                "show_name": None,
                "season": None,
                "destination_mode": self._import_destination_mode,
                "source": "import",
            }))
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
    return units

def _try_pattern_b(self, show_dir: Path) -> list[tuple[Path, int, int, dict]]:
    """Return season units if show_dir looks like a show-organised ARM folder."""
    units = []
    try:
        for entry in os.scandir(show_dir):
            if not entry.is_dir():
                continue
            m = _SEASON_RE.match(entry.name)
            if not m:
                continue
            season_num = int(m.group(1))
            season_dir = Path(entry.path)
            mkv_count, total_size = self._count_mkvs(season_dir)
            if mkv_count > 0:
                units.append((season_dir, mkv_count, total_size, {
                    "structure": "show_organised",
                    "show_name": show_dir.name,
                    "season": season_num,
                    "destination_mode": self._import_destination_mode,
                    "source": "import",
                }))
<<<<<<< HEAD
    except OSError:
        pass
=======
    except OSError as e:
        logger.debug(f"Could not scan show directory {show_dir}: {e}", exc_info=True)
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
    return units

def _count_mkvs(self, directory: Path) -> tuple[int, int]:
    """Return (mkv_count, total_size_bytes) for MKVs directly inside directory."""
    count, size = 0, 0
    try:
        for f in directory.iterdir():
            if f.is_file() and f.suffix.lower() == ".mkv":
                count += 1
                try:
                    size += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return count, size
```

Add `import re` to the top of the file.

- [ ] **Step 4: Run Pattern A test to confirm it passes**

```bash
uv run pytest tests/unit/test_staging_watcher.py::TestImportWatcherStructureDetection::test_pattern_a_per_disc_subfolders -v
```

Expected: `PASSED`

### Step group B — Pattern B and C

- [ ] **Step 5: Write failing tests for Pattern B and C**

Append to the `TestImportWatcherStructureDetection` class:

```python
    async def test_pattern_b_show_organised(self, tmp_path):
        """Show dir with Season subdirs → one job unit per season."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        show = watch_root / "The Office"
        show.mkdir()
        s1 = show / "Season 1"
        s1.mkdir()
        (s1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        s2 = show / "Season 2"
        s2.mkdir()
        (s2 / "title_t01.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 2
        seasons = {meta["season"]: (path, meta) for path, _, _, meta in units}
        assert 1 in seasons and 2 in seasons
        assert seasons[1][1]["show_name"] == "The Office"
        assert seasons[1][1]["structure"] == "show_organised"
        assert seasons[1][0] == s1
        assert seasons[2][0] == s2

    async def test_pattern_c_flat(self, tmp_path):
        """MKVs directly in root → single job unit for whole directory."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        (watch_root / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        (watch_root / "title_t02.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 1
        path, mkv_count, total_size, meta = units[0]
        assert path == watch_root
        assert mkv_count == 2
        assert total_size == 3072
        assert meta["structure"] == "flat"

    async def test_mixed_patterns_in_root(self, tmp_path):
        """Root can contain both per-disc subfolders and show-organised subdirs."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        # Pattern A
        disc = watch_root / "BOB_S1D1"
        disc.mkdir()
        (disc / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        # Pattern B
        show = watch_root / "The Wire"
        show.mkdir()
        s1 = show / "Season 1"
        s1.mkdir()
        (s1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 2
        structures = {meta["structure"] for _, _, _, meta in units}
        assert structures == {"disc_folder", "show_organised"}

    async def test_empty_subdir_ignored(self, tmp_path):
        """Subdirectories with no MKV files are not returned."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        empty = watch_root / "EMPTY_DIR"
        empty.mkdir()

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert units == []

    async def test_destination_mode_in_metadata(self, tmp_path):
        """destination_mode from constructor appears in metadata."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "DISC1"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(
            "/tmp/staging",
            import_watch_path=str(watch_root),
            import_destination_mode="in_place",
        )
        units = watcher._scan_import_dir(watch_root)

        assert units[0][3]["destination_mode"] == "in_place"
```

- [ ] **Step 6: Run new tests**

```bash
uv run pytest tests/unit/test_staging_watcher.py::TestImportWatcherStructureDetection -v
```

Expected: all 6 tests `PASSED` (implementation from Step 3 already handles these).

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/staging_watcher.py backend/tests/unit/test_staging_watcher.py
git commit -m "feat(staging_watcher): add import-path structure detection for ARM output patterns"
```

---

## Task 3: StagingWatcher — integrate import scan into poll loop

Wire `_scan_import_dir` into `_check_staging`, use the same `_known_dirs`/`_processed_dirs` tracking, and extend the callback.

**Files:**
- Modify: `backend/app/core/staging_watcher.py`
- Modify: `backend/tests/unit/test_staging_watcher.py`

- [ ] **Step 1: Write failing test for import path triggering**

Append a new test class to `test_staging_watcher.py`:

```python
class TestImportWatcherPolling:
    """Tests for the full poll loop with import paths."""

    async def test_import_dir_fires_after_stability(self, tmp_path):
        """Import unit fires callback after STABILITY_THRESHOLD stable polls."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "THE_OFFICE_S1D1"
        disc.mkdir()
        (disc / "title_t01.mkv").write_bytes(b"\x00" * 1024)

        watcher = StagingWatcher(str(tmp_path / "staging"), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        await watcher._check_staging()
        callback.assert_not_called()

        await watcher._check_staging()
        callback.assert_not_called()

        await watcher._check_staging()
        callback.assert_called_once()

        args = callback.call_args[0]
        kwargs = callback.call_args[1] if callback.call_args[1] else {}
        # Fourth positional arg or keyword 'metadata'
        metadata = args[3] if len(args) > 3 else kwargs.get("metadata")
        assert metadata is not None
        assert metadata["source"] == "import"
        assert metadata["structure"] == "disc_folder"

    async def test_import_not_retriggered_after_fire(self, tmp_path):
        """Import unit is not re-triggered in subsequent polls."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "DISC1"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(str(tmp_path / "staging"), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(3):
            await watcher._check_staging()
        assert callback.call_count == 1

        for _ in range(5):
            await watcher._check_staging()
        assert callback.call_count == 1

    async def test_staging_and_import_fire_independently(self, tmp_path):
        """Existing staging scan and import scan are both active simultaneously."""
        staging = tmp_path / "staging"
        staging.mkdir()
        watch_root = tmp_path / "arm"
        watch_root.mkdir()

        # One disc in import path
        disc = watch_root / "IMPORT_DISC"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        # One disc in staging path
        staging_sub = staging / "STAGING_DISC"
        staging_sub.mkdir()
        (staging_sub / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(str(staging), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(3):
            await watcher._check_staging()

        assert callback.call_count == 2
        sources = set()
        for call in callback.call_args_list:
            args = call[0]
            meta = args[3] if len(args) > 3 else None
            sources.add(meta["source"] if meta else "staging")
        assert "import" in sources
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_staging_watcher.py::TestImportWatcherPolling -v
```

Expected: `FAILED` — `_check_staging` doesn't call `_scan_import_dir` yet and callback still takes 3 args.

- [ ] **Step 3: Update `_check_staging` to also scan the import path**

Replace the `_check_staging` method in `staging_watcher.py`:

```python
async def _check_staging(self) -> None:
    """Scan both staging and import paths for new MKV directories."""
    # --- Existing staging scan ---
    if self._staging_path and self._staging_path.exists():
        try:
            entries = await asyncio.to_thread(self._scan_staging_dir)
        except OSError as e:
            logger.debug(f"Could not scan staging directory: {e}")
            entries = []

        seen_dirs: set[str] = set()
        for dir_path, mkv_count, total_size in entries:
            dir_str = str(dir_path)
            seen_dirs.add(dir_str)
            if dir_str in self._processed_dirs:
                continue
            if mkv_count == 0:
                continue
            await self._update_stability(dir_str, dir_path, mkv_count, total_size, metadata=None)

<<<<<<< HEAD
        stale = [k for k in self._known_dirs if k not in seen_dirs
                 and not str(k).startswith(str(self._import_watch_path or ""))]
=======
        stale = [
            k for k in self._known_dirs
            if k not in seen_dirs
            and (
                not self._import_watch_path
                or not str(k).startswith(str(self._import_watch_path))
            )
        ]
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
        for key in stale:
            del self._known_dirs[key]

    # --- Import path scan ---
    if self._import_watch_path and self._import_watch_path.exists():
        try:
            import_entries = await asyncio.to_thread(
                self._scan_import_dir, self._import_watch_path
            )
        except OSError as e:
            logger.debug(f"Could not scan import directory: {e}")
            import_entries = []

        seen_import: set[str] = set()
        for dir_path, mkv_count, total_size, meta in import_entries:
            dir_str = str(dir_path)
            seen_import.add(dir_str)
            if dir_str in self._processed_dirs:
                continue
            if mkv_count == 0:
                continue
            await self._update_stability(dir_str, dir_path, mkv_count, total_size, metadata=meta)

        stale_import = [
            k for k in self._known_dirs
            if k not in seen_import
            and self._import_watch_path
            and str(k).startswith(str(self._import_watch_path))
        ]
        for key in stale_import:
            del self._known_dirs[key]
```

Add the `_update_stability` helper to handle both cases uniformly:

```python
async def _update_stability(
    self,
    dir_str: str,
    dir_path: Path,
    mkv_count: int,
    total_size: int,
    metadata: dict | None,
) -> None:
    """Shared stability tracking for staging and import entries."""
    prev = self._known_dirs.get(dir_str)
    if prev is None:
        self._known_dirs[dir_str] = {
            "mkv_count": mkv_count,
            "total_size": total_size,
            "stable_polls": 0,
            "metadata": metadata,
        }
        logger.debug(
            f"Watcher: new directory {dir_path.name} "
            f"({mkv_count} MKV files, {total_size} bytes)"
        )
    elif prev["mkv_count"] != mkv_count or prev["total_size"] != total_size:
        self._known_dirs[dir_str] = {
            "mkv_count": mkv_count,
            "total_size": total_size,
            "stable_polls": 0,
            "metadata": metadata,
        }
        logger.debug(f"Watcher: {dir_path.name} changed — stability reset")
    else:
        prev["stable_polls"] += 1
        if prev["stable_polls"] >= STABILITY_THRESHOLD:
            label = dir_path.name.upper().replace(" ", "_")
            logger.info(
                f"Watcher: {dir_path.name} is stable ({mkv_count} MKV files) — "
                f"triggering import (source={metadata['source'] if metadata else 'staging'})"
            )
            self._processed_dirs.add(dir_str)
            del self._known_dirs[dir_str]
            await self._notify("staging_ready", dir_str, label, metadata)
```

Update `_notify` to forward metadata:

```python
async def _notify(
    self, event: str, staging_dir: str, label: str, metadata: dict | None = None
) -> None:
    """Fire the async callback."""
    if self._async_callback:
        try:
            await self._async_callback(event, staging_dir, label, metadata)
        except Exception as e:
            logger.error(f"Watcher callback error: {e}", exc_info=True)
```

Remove the old stability-tracking block from `_check_staging` (it is now in `_update_stability`). The old `_check_staging` body should be fully replaced by the new version above.

- [ ] **Step 4: Run all staging watcher tests**

```bash
uv run pytest tests/unit/test_staging_watcher.py -v
```

Expected: all tests `PASSED`. If existing tests fail because `_notify` now receives a fourth arg, update them: the existing call to `_notify` in the old `_check_staging` passed 3 args; the new `_update_stability` passes 4. Existing tests that check `callback.call_args[0]` still work since `args[2]` is still the label.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/staging_watcher.py backend/tests/unit/test_staging_watcher.py
git commit -m "feat(staging_watcher): integrate import scan into poll loop with extended callback"
```

---

## Task 4: JobManager wiring

Update `_on_staging_event` to read import metadata, add `destination_mode`/`drive_id` params to `create_job_from_staging`, and update the StagingWatcher initialisation at startup.

**Files:**
- Modify: `backend/app/services/job_manager.py`

- [ ] **Step 1: Update `_on_staging_event` signature and body**

Find `_on_staging_event` (≈ line 274) and replace it:

```python
async def _on_staging_event(
    self, event: str, staging_dir: str, label: str, metadata: dict | None = None
) -> None:
    """Handle new staging directory detection from StagingWatcher."""
    logger.info(f"Staging event: {event} dir={staging_dir} label={label} source={metadata and metadata.get('source')}")
    if event == "staging_ready":
        try:
            is_import = metadata and metadata.get("source") == "import"
            await self.create_job_from_staging(
                staging_path=staging_dir,
                volume_label=label,
                content_type="unknown",
                detected_title=metadata.get("show_name") if is_import else None,
                detected_season=metadata.get("season") if is_import else None,
                destination_mode=metadata.get("destination_mode", "library") if is_import else "library",
                drive_id="import" if is_import else "staging",
            )
        except Exception as e:
            logger.error(
                f"Failed to create job from staging directory {staging_dir}: {e}",
                exc_info=True,
            )
```

- [ ] **Step 2: Update `create_job_from_staging` to accept new params**

Find `create_job_from_staging` (≈ line 353) and update its signature and body:

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
) -> int:
    """Create a job from pre-ripped MKV files in a staging directory."""
    staging_dir = Path(staging_path)

    if not volume_label:
        volume_label = staging_dir.name.upper().replace(" ", "_")

    async with async_session() as session:
        # Guard: don't create a duplicate job for the same staging directory
        from sqlmodel import select as sa_select
        existing = await session.execute(
            sa_select(DiscJob).where(DiscJob.staging_path == str(staging_dir))
        )
        if existing.scalar_one_or_none():
            logger.info(f"Job already exists for staging path {staging_dir}, skipping")
            return -1

        job = DiscJob(
            drive_id=drive_id,
            volume_label=volume_label,
            staging_path=str(staging_dir),
            state=JobState.IDENTIFYING,
            destination_mode=destination_mode,
        )

        if content_type in ("tv", "movie"):
            job.content_type = ContentType(content_type)
        if detected_title:
            job.detected_title = detected_title
        if detected_season is not None:
            job.detected_season = detected_season

        session.add(job)
        await session.commit()
        await session.refresh(job)

        job_id = job.id
        logger.info(
            f"Created {'import' if drive_id == 'import' else 'staging'} job {job_id} "
            f"from {staging_path} (label: {volume_label}, destination: {destination_mode})"
        )

    await event_broadcaster.broadcast_drive_inserted(drive_id, volume_label)

    task = asyncio.create_task(
        with_job_log_context(job_id, self._identification.identify_from_staging(job_id))
    )
    task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
    self._active_jobs[job_id] = task

    return job_id
```

- [ ] **Step 3: Update StagingWatcher startup to pass import path**

Find the StagingWatcher startup block (≈ line 158):

```python
# Start staging watcher if enabled
if config.staging_watch_enabled and config.staging_path:
    self._staging_watcher = StagingWatcher(config.staging_path, config=config)
    self._staging_watcher.set_async_callback(self._on_staging_event, self._loop)
    self._staging_watcher.start()
```

Replace with:

```python
# Start staging/import watcher if either feature is enabled
need_watcher = (config.staging_watch_enabled and config.staging_path) or config.import_watch_path
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

- [ ] **Step 4: Run backend unit tests**

```bash
cd backend
uv run pytest tests/unit/ -v
```

Expected: all passing. If any test creates a `StagingWatcher` without the new params, the defaults handle it.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py
git commit -m "feat(job_manager): wire import metadata into staging event handler and job creation"
```

---

## Task 5: FinalizationCoordinator — `in_place` destination mode

When `job.destination_mode == "in_place"`, call the bare organiser functions with a `library_path` override pointing to the import watch root.

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py`

The finalization coordinator currently calls these wrappers which do not forward `library_path`:
- `tv_organizer.organize(source_file, show_name, episode_code)` — lines ≈ 694, 957, 1068
- `movie_organizer.organize(source_file, volume_label, final_title)` — line ≈ 864
- `organize_tv_extras(source_file, ...)` — lines ≈ 946, 1057

For `in_place` mode, replace each with a direct call to the bare function with the override.

- [ ] **Step 1: Add a helper to compute `library_path` override**

Near the top of `finalization_coordinator.py` (after the existing imports), add a module-level helper:

```python
from pathlib import Path as _Path

<<<<<<< HEAD
def _library_path_for_job(job, content_type: str) -> "_Path | None":
    """Return a library_path override for in_place jobs, or None for library mode."""
    if job.destination_mode != "in_place":
        return None
    from app.services.config_service import get_config_sync
    cfg = get_config_sync()
    if not cfg.import_watch_path:
        return None
    root = _Path(cfg.import_watch_path)
    return root / ("Movies" if content_type == "movie" else "TV")
```

=======
def _library_path_for_job(job, cfg, content_type: str) -> "_Path | None":
    """Return a library_path override for in_place jobs, or None for library mode."""
    if job.destination_mode != "in_place" or not cfg.import_watch_path:
        return None
    return _Path(cfg.import_watch_path) / ("Movies" if content_type == "movie" else "TV")
```

`cfg` is the `AppConfig` already fetched at the start of the finalization step — thread it in rather than issuing a blocking `get_config_sync()` call from inside the async coroutine body.

>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
- [ ] **Step 2: Update TV episode organiser calls**

There are three call sites for `tv_organizer.organize(...)`. At each one, replace:

```python
org_result = await asyncio.to_thread(
    tv_organizer.organize,
    source_file,
    job.detected_title or job.volume_label,
    disc_title.matched_episode,
)
```

with:

```python
<<<<<<< HEAD
_lib_path = _library_path_for_job(job, "tv")
=======
_lib_path = _library_path_for_job(job, cfg, "tv")
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
if _lib_path:
    from app.core.organizer import organize_tv_episode
    org_result = await asyncio.to_thread(
        organize_tv_episode,
        source_file,
        job.detected_title or job.volume_label,
        disc_title.matched_episode,
        _lib_path,
    )
else:
    org_result = await asyncio.to_thread(
        tv_organizer.organize,
        source_file,
        job.detected_title or job.volume_label,
        disc_title.matched_episode,
    )
```

Apply the same pattern to the three TV episode call sites (search for `tv_organizer.organize` to find all of them).

For `organize_tv_extras` calls, replace:

```python
org_result = await asyncio.to_thread(
    organize_tv_extras,
    source_file,
    job.detected_title or job.volume_label,
    job.detected_season or 1,
    ...
)
```

with:

```python
<<<<<<< HEAD
_lib_path = _library_path_for_job(job, "tv")
=======
_lib_path = _library_path_for_job(job, cfg, "tv")
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
org_result = await asyncio.to_thread(
    organize_tv_extras,
    source_file,
    job.detected_title or job.volume_label,
    job.detected_season or 1,
    ...,
    library_path=_lib_path,
)
```

- [ ] **Step 3: Update movie organiser call**

Find the `movie_organizer.organize(...)` call (≈ line 864) and replace:

```python
org_result = await asyncio.to_thread(
    movie_organizer.organize,
    source_file,
    job.volume_label,
    final_title,
)
```

with:

```python
<<<<<<< HEAD
_lib_path = _library_path_for_job(job, "movie")
=======
_lib_path = _library_path_for_job(job, cfg, "movie")
>>>>>>> 669fab7daf2348a5086b62d3a9abdf283538a461
if _lib_path:
    from app.core.organizer import organize_movie
    org_result = await asyncio.to_thread(
        organize_movie,
        source_file,
        final_title,
        None,  # year
        _lib_path,
    )
else:
    org_result = await asyncio.to_thread(
        movie_organizer.organize,
        source_file,
        job.volume_label,
        final_title,
    )
```

Note: `organize_movie` signature is `(staging_dir, movie_name, year=None, library_path=None, ...)`.

- [ ] **Step 4: Run backend tests**

```bash
cd backend
uv run pytest tests/ -v --ignore=tests/real_data
```

Expected: all existing tests pass (no in_place jobs in the test suite, so the new branches are not executed by existing tests — but nothing should break).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/finalization_coordinator.py
git commit -m "feat(finalization): add in_place destination mode using bare organiser functions"
```

---

## Task 6: ConfigWizard UI — Import Watch Folder section

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step 1: Add `importWatchPath` and `importDestinationMode` to the config state**

In `ConfigWizard.tsx`, find the config state initialisation (the object with `stagingPath`, `libraryMoviesPath`, etc., ≈ line 190). Add:

```typescript
importWatchPath: data.import_watch_path || '',
importDestinationMode: data.import_destination_mode || 'library',
```

Find the `setConfig` call in the fetch handler and update accordingly (search for `stagingPath: data.staging_path`). Then add to the `PUT` body (search for `staging_path: config.stagingPath`):

```typescript
import_watch_path: config.importWatchPath || null,
import_destination_mode: config.importDestinationMode,
```

Update the TypeScript config type (the `useState` type or local interface) to include:

```typescript
importWatchPath: string;
importDestinationMode: string;
```

- [ ] **Step 2: Add the Import Watch Folder section to the JSX**

Find the library path fields section (the block containing `libraryTvPath` or `libraryMoviesPath`). After it, add a new section:

```tsx
{/* Import Watch Folder */}
<div style={{ marginTop: 24 }}>
  <div style={{
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: sv.inkFaint,
    textTransform: 'uppercase',
    marginBottom: 8,
  }}>
    Import Watch Folder
  </div>
  <div style={{ fontSize: 12, color: sv.inkFaint, marginBottom: 12 }}>
    Automatically import MKV files ripped by AutomaticRippingMachine or similar tools.
    Engram detects per-disc subfolders, show-organised trees, and flat layouts.
  </div>

  {/* Path input */}
  <div style={{ marginBottom: 12 }}>
    <label style={{ display: 'block', fontSize: 11, color: sv.inkFaint, marginBottom: 4 }}>
      Watch Folder Path
    </label>
    <div style={{ display: 'flex', gap: 8 }}>
      <input
        type="text"
        value={config.importWatchPath}
        onChange={e => setConfig(c => ({ ...c, importWatchPath: e.target.value }))}
        placeholder="Not configured"
        style={{
          flex: 1,
          background: sv.panelBg,
          border: `1px solid ${sv.borderFaint}`,
          color: sv.inkHi,
          padding: '6px 10px',
          fontSize: 13,
          fontFamily: sv.mono,
        }}
      />
      {config.importWatchPath && (
        <button
          type="button"
          onClick={() => setConfig(c => ({ ...c, importWatchPath: '' }))}
          style={{
            background: 'transparent',
            border: `1px solid ${sv.borderFaint}`,
            color: sv.inkFaint,
            padding: '6px 10px',
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          Clear
        </button>
      )}
    </div>
  </div>

  {/* Destination toggle */}
  {config.importWatchPath && (
    <div>
      <label style={{ display: 'block', fontSize: 11, color: sv.inkFaint, marginBottom: 6 }}>
        Destination
      </label>
      <div style={{ display: 'flex', gap: 0 }}>
        {(['library', 'in_place'] as const).map(mode => (
          <button
            key={mode}
            type="button"
            onClick={() => setConfig(c => ({ ...c, importDestinationMode: mode }))}
            style={{
              padding: '6px 14px',
              fontSize: 12,
              cursor: 'pointer',
              background: config.importDestinationMode === mode ? sv.cyanHi : sv.panelBg,
              color: config.importDestinationMode === mode ? sv.bgBase : sv.inkFaint,
              border: `1px solid ${sv.borderFaint}`,
              marginRight: mode === 'library' ? -1 : 0,
              fontWeight: config.importDestinationMode === mode ? 700 : 400,
            }}
          >
            {mode === 'library' ? 'Organize into library' : 'Organize in place'}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 11, color: sv.inkFaint, marginTop: 6 }}>
        {config.importDestinationMode === 'library'
          ? 'Files are moved into your configured TV and movie library paths.'
          : 'Files are organized within the watch folder itself.'}
      </div>
    </div>
  )}
</div>
```

- [ ] **Step 3: Run frontend type check**

```bash
cd frontend
npm run build
```

Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(config-wizard): add Import Watch Folder section with path and destination mode"
```

---

## Task 7: DiscCard UI — import source badge

Show a folder icon for imported jobs instead of the disc icon where `drive_id == "import"`.

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/types/adapters.ts`
- Modify: `frontend/src/app/components/DiscCard.tsx`

- [ ] **Step 1: Add `destination_mode` to the `Job` interface**

In `frontend/src/types/index.ts`, update the `Job` interface to add:

```typescript
destination_mode?: string;
```

- [ ] **Step 2: Add `sourceType` to `DiscData`**

In `frontend/src/app/components/DiscCard.tsx`, update the `DiscData` interface:

```typescript
export interface DiscData {
  id: string;
  title: string;
  subtitle?: string;
  discLabel?: string;
  sourceType?: 'disc' | 'import' | 'staging';  // add this line
  coverUrl: string;
  // ... rest unchanged
}
```

- [ ] **Step 3: Set `sourceType` in the adapter**

In `frontend/src/types/adapters.ts`, update `transformJobToDiscData` to set `sourceType`:

```typescript
return {
  id: job.id.toString(),
  title: job.detected_title || job.volume_label,
  subtitle: `${displayType} • ${job.volume_label}`,
  discLabel: job.volume_label,
  sourceType: job.drive_id === 'import' ? 'import'
    : job.drive_id === 'staging' ? 'staging'
    : 'disc',
  // ... rest unchanged
};
```

- [ ] **Step 4: Render a folder badge for import jobs**

In `frontend/src/app/components/DiscCard.tsx`, find where `IcoDisc` is rendered (search for `<IcoDisc`). Import `IcoFolder` from the icons index — check `frontend/src/app/components/icons/` for the correct icon name. If `IcoFolder` does not exist, use `Folder` from `lucide-react` (already available).

Locate the disc icon usage and conditionally render:

```tsx
{disc.sourceType === 'import' ? (
  <Folder size={14} color={sv.cyanHi} />
) : (
  <IcoDisc size={14} />
)}
```

If the icon is inside a tooltip or badge component, keep the wrapper and only swap the inner icon.

- [ ] **Step 5: Run frontend type check and start dev server to verify visually**

```bash
cd frontend
npm run build
```

Then start backend and frontend to confirm imported jobs (created via the simulate or staging import endpoint) show the folder badge.

```bash
# Terminal 1 — backend with DEBUG=true
cd backend
uv run uvicorn app.main:app

# Terminal 2 — frontend
cd frontend
npm run dev
```

Create a test import job:
```bash
curl -X POST localhost:8000/api/staging/import \
  -H "Content-Type: application/json" \
  -d '{"staging_path": "/tmp/test_import", "volume_label": "TEST", "content_type": "tv"}'
```

Verify: the DiscCard shows a folder icon rather than a disc icon.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/types/adapters.ts frontend/src/app/components/DiscCard.tsx
git commit -m "feat(disc-card): show folder badge for watch-folder-imported jobs"
```

---

## Self-Review Checklist

- [x] **Pattern A** (per-disc subfolders) → Task 2 tests + implementation
- [x] **Pattern B** (show-organised) → Task 2 tests + implementation
- [x] **Pattern C** (flat) → Task 2 tests + implementation
- [x] **Mixed patterns** → Task 2 mixed-pattern test
- [x] **Stability debounce** → Task 3 polling tests
- [x] **No re-trigger after fire** → Task 3 + existing DB guard in Task 4
- [x] **Metadata forwarded** → Task 3 (callback) + Task 4 (job_manager)
- [x] **`destination_mode` persisted** → Task 1 (DiscJob field) + Task 4 (create_job_from_staging)
- [x] **`in_place` organises to watch root** → Task 5 (_library_path_for_job)
- [x] **`library` mode unchanged** → Task 5 (falls through to existing wrappers)
- [x] **ConfigWizard path + toggle** → Task 6
- [x] **ConfigWizard clear button** → Task 6
- [x] **Watcher starts when only import_watch_path set** → Task 4 (need_watcher logic)
- [x] **Import badge on DiscCard** → Task 7
- [x] **Existing staging watcher behaviour unchanged** — `_check_staging` only calls `_scan_staging_dir` when `self._staging_path` exists; old tests still pass
