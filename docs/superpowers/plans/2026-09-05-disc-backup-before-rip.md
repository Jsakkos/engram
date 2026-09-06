# Disc Backup Before Rip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optionally write a full decrypted MakeMKV backup of a disc to a separate root after identification, extract MKVs from that backup instead of from the drive, and let the user import an existing backup folder or ISO through the manual import modal.

**Architecture:** A job's MakeMKV source becomes a value (`DiscSource`: drive, backup folder, or ISO) rather than an assumed drive letter, stored as a `source_spec` string on `DiscJob`. A new `JobState.BACKING_UP` sits between `IDENTIFYING` and `RIPPING`; on success it rewrites `source_spec` to `file:<backup>` and releases the drive early. Every backup problem degrades to today's direct rip, so enabling the setting can never make a disc less likely to finish.

**Tech Stack:** Python 3.11, FastAPI, SQLModel + aiosqlite, Alembic, asyncio subprocess wrapping `makemkvcon`, pytest. Frontend: React 18 + TypeScript, Vite, vitest + React Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-05-disc-backup-before-rip-design.md`

---

## Working agreements

- All backend commands run from `backend/`. Always `uv run ...`, never bare `python`/`pytest`.
- All frontend commands run from `frontend/`.
- Never delete `backend/engram.db`.
- Never start a server with `--reload`.
- Do not write em dashes in code comments, docstrings, or docs.
- Commit after every task. Commit messages reference the feature, not the task number.
- Do not run the full backend unit suite in one go inside an agent (it exceeds 300 s). Run the specific test files named in each task.

## File structure

**New backend files**

| File | Responsibility |
| --- | --- |
| `backend/app/core/disc_source.py` | The `DiscSource` value object and `resolve_disc_index()`. Pure except the one subprocess call. |
| `backend/app/core/backup_paths.py` | Backup destination naming and the free-space preflight. Pure. |
| `backend/migrations/versions/<rev>_add_backup_columns.py` | Alembic migration for the three `disc_jobs` columns and two `app_config` columns. |
| `backend/tests/unit/test_disc_source.py` | Unit tests for `disc_source`. |
| `backend/tests/unit/test_backup_paths.py` | Unit tests for `backup_paths`. |
| `backend/tests/unit/test_backup_disc.py` | Unit tests for `Extractor.backup_disc` and its argv builder. |
| `backend/tests/unit/test_backup_reconciliation.py` | Unit tests for post-backup title reconciliation. |
| `backend/tests/unit/test_import_scanner_disc_images.py` | Unit tests for disc-image detection. |

**Modified backend files**

| File | Change |
| --- | --- |
| `backend/app/models/disc_job.py` | `JobState.BACKING_UP`; `source_spec`, `backup_path`, `backup_status`, `backup_status_reason` columns. |
| `backend/app/models/app_config.py` | `backup_before_rip`, `backup_path`, `timeout_backing_up_seconds`. |
| `backend/app/core/extractor.py` | `_to_source_spec`, `DiscSource`-aware locking, `backup_disc()`, `BackupResult`. |
| `backend/app/core/import_scanner.py` | `DiscImageUnit` detection and short-circuit. |
| `backend/app/services/job_state_machine.py` | New transitions. |
| `backend/app/services/job_manager.py` | `_run_backup`, reconciliation, `_release_drive` outcome, `_phase_timeout`. |
| `backend/app/services/identification_coordinator.py` | Route to `BACKING_UP` instead of `RIPPING` when enabled. |
| `backend/app/services/event_broadcaster.py`, `backend/app/api/websocket.py` | `backup_progress` message. |
| `backend/app/services/simulation_service.py` | `simulate_backup`. |
| `backend/app/api/routes.py` | Config schemas, import browse/preview/start, job detail. |

**Modified frontend files**

| File | Change |
| --- | --- |
| `frontend/src/app/components/DiscCard.tsx` | Rename the orphaned `archiving_iso` state to `backing_up`; `backupProgress`; backup-status note. |
| `frontend/src/app/components/discState.ts` | Rename the state key and relabel. |
| `frontend/src/types/adapters.ts` | Map `backing_up`. |
| `frontend/src/components/ConfigWizard.tsx` | Toggle and path picker. |
| `frontend/src/components/ImportModal.tsx` | Disc-image and ISO entries. |
| `frontend/src/components/HistoryPage/` | Backup path and status in the detail panel. |

**Important pre-existing condition:** `archiving_iso` and `isoProgress` already exist in the frontend (`DiscCard.tsx:18`, `DiscCard.tsx:72`, `DiscCard.tsx:676`, `discState.ts:31`) but no backend code emits them and only `mockData.ts` feeds them. They are orphaned design-era scaffolding. Task 18 repurposes them rather than adding an eleventh state, because a dead `ARCHIVING` state sitting next to a live `BACKING UP` state is exactly the drift the comments in `discState.ts` warn about.

---

## Task 1: `DiscSource` value object

**Files:**
- Create: `backend/app/core/disc_source.py`
- Test: `backend/tests/unit/test_disc_source.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_disc_source.py`:

```python
"""Unit tests for the DiscSource value object."""

import pytest

from app.core.disc_source import DiscSource, SourceKind


class TestParse:
    def test_bare_drive_letter_is_a_drive(self):
        s = DiscSource.parse("E:")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_dev_prefixed_drive_passes_through(self):
        s = DiscSource.parse("dev:E:")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_linux_device_is_a_drive(self):
        s = DiscSource.parse("/dev/sr0")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:/dev/sr0"

    def test_disc_index_is_a_drive(self):
        s = DiscSource.parse("disc:0")
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "disc:0"

    def test_file_spec_is_a_backup(self, tmp_path):
        s = DiscSource.parse(f"file:{tmp_path}")
        assert s.kind is SourceKind.BACKUP
        assert s.spec == f"file:{tmp_path}"

    def test_iso_spec_is_an_iso(self, tmp_path):
        iso = tmp_path / "disc.iso"
        s = DiscSource.parse(f"iso:{iso}")
        assert s.kind is SourceKind.ISO
        assert s.spec == f"iso:{iso}"

    def test_windows_path_in_file_spec_survives_the_colon(self):
        # "file:D:\\backups\\x" must not be split on the drive-letter colon.
        s = DiscSource.parse("file:D:\\backups\\Inception (2010)")
        assert s.kind is SourceKind.BACKUP
        assert s.value == "D:\\backups\\Inception (2010)"

    def test_empty_spec_is_rejected(self):
        with pytest.raises(ValueError):
            DiscSource.parse("")


class TestPhysicality:
    def test_drive_is_physical(self):
        assert DiscSource.parse("E:").is_physical is True

    def test_backup_is_not_physical(self):
        assert DiscSource.parse("file:/backups/x").is_physical is False

    def test_iso_is_not_physical(self):
        assert DiscSource.parse("iso:/backups/x.iso").is_physical is False


class TestLockKey:
    def test_drive_forms_share_one_lock_key(self):
        assert DiscSource.parse("E:").lock_key == DiscSource.parse("dev:E:").lock_key

    def test_trailing_separator_does_not_split_the_lock(self):
        assert DiscSource.parse("dev:E:\\").lock_key == DiscSource.parse("E:").lock_key

    def test_a_backup_does_not_share_a_drive_lock(self):
        drive = DiscSource.parse("E:")
        backup = DiscSource.parse("file:E:\\backups\\x")
        assert backup.lock_key != drive.lock_key

    def test_two_backups_at_the_same_path_share_a_lock(self):
        a = DiscSource.parse("file:/backups/x")
        b = DiscSource.parse("file:/backups/x")
        assert a.lock_key == b.lock_key


class TestFromJob:
    def test_source_spec_wins_when_present(self):
        job = _FakeJob(drive_id="E:", source_spec="file:/backups/x")
        s = DiscSource.from_job(job)
        assert s.kind is SourceKind.BACKUP
        assert s.value == "/backups/x"

    def test_legacy_row_falls_back_to_drive_id(self):
        job = _FakeJob(drive_id="E:", source_spec=None)
        s = DiscSource.from_job(job)
        assert s.kind is SourceKind.DRIVE
        assert s.spec == "dev:E:"

    def test_import_drive_id_without_source_spec_is_rejected(self):
        # A manual-import job has no MakeMKV source at all; asking for one is a bug.
        job = _FakeJob(drive_id="import", source_spec=None)
        with pytest.raises(ValueError):
            DiscSource.from_job(job)


class _FakeJob:
    def __init__(self, drive_id: str, source_spec: str | None):
        self.drive_id = drive_id
        self.source_spec = source_spec
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_disc_source.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'app.core.disc_source'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/core/disc_source.py`:

```python
"""What a job points MakeMKV at.

MakeMKV accepts three source forms: ``dev:<drive or device>``, ``disc:<index>``
and ``file:<path>`` (a backup folder or an ISO). Engram historically assumed the
first, so "is this source a physical drive?" was answered ad hoc with string
tests at every call site that needed eject, sentinel re-arm, drive locking or
progress labelling. This module is the single answer.

Pure, apart from :func:`resolve_disc_index`, which shells out to makemkvcon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# "iso:" is Engram's own spec kind, not a MakeMKV one: MakeMKV reads an ISO
# through the same file: source as a folder. Keeping them distinct lets the UI
# and the import scanner say which one the user picked without re-stat-ing disk.
_SCHEMES = ("dev:", "disc:", "file:", "iso:")

# A bare Windows drive letter ("E:", "E:\") or a POSIX device path.
_BARE_DRIVE_RE = re.compile(r"^(?:[A-Za-z]:\\?|/dev/[A-Za-z0-9/]+)$")


class SourceKind(StrEnum):
    """What kind of thing a job reads from."""

    DRIVE = "drive"
    BACKUP = "backup"
    ISO = "iso"


@dataclass(frozen=True)
class DiscSource:
    """A MakeMKV source, and the behaviour that depends on which kind it is."""

    kind: SourceKind
    value: str
    # Which MakeMKV scheme to emit for a DRIVE. Preserved rather than recomputed
    # so a caller that resolved "E:" to "disc:0" keeps that resolution.
    _scheme: str = "dev:"

    @classmethod
    def parse(cls, spec: str) -> DiscSource:
        """Build a source from a stored spec string or a bare drive identifier."""
        if not spec:
            raise ValueError("Empty disc source spec")

        for scheme in _SCHEMES:
            if spec.startswith(scheme):
                # split on the FIRST colon only: "file:D:\backups\x" must keep
                # its Windows drive-letter colon.
                value = spec[len(scheme) :]
                if not value:
                    raise ValueError(f"Disc source spec has no value: {spec!r}")
                if scheme == "file:":
                    return cls(SourceKind.BACKUP, value)
                if scheme == "iso:":
                    return cls(SourceKind.ISO, value)
                return cls(SourceKind.DRIVE, value, scheme)

        if _BARE_DRIVE_RE.match(spec):
            return cls(SourceKind.DRIVE, spec, "dev:")

        raise ValueError(f"Unrecognized disc source spec: {spec!r}")

    @classmethod
    def from_job(cls, job) -> DiscSource:
        """Build a source from a DiscJob, honouring legacy rows.

        ``source_spec`` is None on every row written before this feature, where
        the source was always the drive. A manual-import job (``drive_id ==
        "import"``) has no MakeMKV source at all, so asking for one is a bug in
        the caller rather than something to paper over.
        """
        spec = getattr(job, "source_spec", None)
        if spec:
            return cls.parse(spec)
        drive_id = job.drive_id
        if not drive_id or drive_id == "import":
            raise ValueError(
                f"Job has no MakeMKV source: drive_id={drive_id!r}, source_spec=None"
            )
        return cls.parse(drive_id)

    @classmethod
    def for_backup(cls, path: Path | str) -> DiscSource:
        """A source reading from a completed backup folder."""
        return cls(SourceKind.BACKUP, str(path))

    @property
    def spec(self) -> str:
        """The argument to hand makemkvcon."""
        if self.kind is SourceKind.DRIVE:
            return f"{self._scheme}{self.value}"
        if self.kind is SourceKind.ISO:
            return f"iso:{self.value}"
        return f"file:{self.value}"

    @property
    def makemkv_arg(self) -> str:
        """The argument makemkvcon actually accepts.

        An ISO is read through the same ``file:`` source as a folder; the ISO
        kind exists for Engram's own bookkeeping, not for MakeMKV's.
        """
        if self.kind is SourceKind.ISO:
            return f"file:{self.value}"
        return self.spec

    @property
    def is_physical(self) -> bool:
        """Whether this source is a real optical drive.

        Gates eject, sentinel re-arm, disc-hash computation and every other
        behaviour that only makes sense for hardware.
        """
        return self.kind is SourceKind.DRIVE

    @property
    def lock_key(self) -> str:
        """Key for the per-source MakeMKV serialization lock.

        Physical drives normalize so "E:", "dev:E:" and "dev:E:\\" contend for
        one lock. A file source keys on its own path instead: two makemkvcon
        processes reading different backups do not contend, and a backup on
        drive E: must not block the optical drive at E:.
        """
        if self.is_physical:
            return "drive:" + self.value.replace("dev:", "").replace("disc:", "").rstrip("\\")
        return "path:" + str(Path(self.value))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_disc_source.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/disc_source.py tests/unit/test_disc_source.py && uv run ruff format app/core/disc_source.py tests/unit/test_disc_source.py
git add backend/app/core/disc_source.py backend/tests/unit/test_disc_source.py
git commit -m "feat(backup): add DiscSource value object for drive/backup/ISO sources"
```

---

## Task 2: Resolve a drive to a MakeMKV disc index

`makemkvcon backup` accepts only `disc:N`, never `dev:E:`. Engram has never needed this mapping.

**Files:**
- Modify: `backend/app/core/disc_source.py`
- Modify: `backend/tests/unit/test_disc_source.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_disc_source.py`:

```python
from unittest.mock import patch

from app.core.disc_source import parse_drive_listing, resolve_disc_index

# Real makemkvcon -r info disc:9999 output shape. DRV lines are:
# DRV:index,visible,enabled,flags,"drive name","disc name","device"
_LISTING = (
    'DRV:0,2,999,1,"BD-RE HL-DT-ST BH16NS40 1.05","THE_SWEETEST_THING","E:"\n'
    'DRV:1,0,999,0,"","",""\n'
    'DRV:2,2,999,1,"HL-DT-ST DVDRAM GH24","INCEPTION","F:"\n'
    "TCOUNT:0\n"
)


class TestParseDriveListing:
    def test_maps_device_to_index(self):
        assert parse_drive_listing(_LISTING) == {"E:": 0, "F:": 2}

    def test_ignores_empty_drive_slots(self):
        assert "" not in parse_drive_listing(_LISTING)

    def test_empty_output_maps_nothing(self):
        assert parse_drive_listing("") == {}

    def test_malformed_line_is_skipped_not_raised(self):
        assert parse_drive_listing('DRV:garbage\nDRV:0,2,999,1,"n","d","E:"\n') == {"E:": 0}


class TestResolveDiscIndex:
    @pytest.mark.asyncio
    async def test_resolves_a_known_drive(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("E:", makemkv_path="mmk") == "disc:0"

    @pytest.mark.asyncio
    async def test_case_insensitive_on_windows_letters(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("e:", makemkv_path="mmk") == "disc:0"

    @pytest.mark.asyncio
    async def test_unknown_drive_returns_none(self):
        with patch("app.core.disc_source._run_drive_listing", return_value=_LISTING):
            assert await resolve_disc_index("Z:", makemkv_path="mmk") is None

    @pytest.mark.asyncio
    async def test_subprocess_failure_returns_none(self):
        with patch("app.core.disc_source._run_drive_listing", side_effect=OSError("boom")):
            assert await resolve_disc_index("E:", makemkv_path="mmk") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_disc_source.py -q -k "DriveListing or DiscIndex"
```

Expected: `ImportError: cannot import name 'parse_drive_listing'`.

- [ ] **Step 3: Write the implementation**

Append to `backend/app/core/disc_source.py`:

```python
import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

# makemkvcon -r info disc:9999 lists drives without touching a disc. 9999 is
# MakeMKV's documented "no such drive" index: the scan fails, but the DRV lines
# describing every drive are printed first, which is all we want.
_DRIVE_LISTING_INDEX = "disc:9999"

# DRV:index,visible,enabled,flags,"drive name","disc name","device"
_DRV_RE = re.compile(r'^DRV:(\d+),\d+,\d+,\d+,"[^"]*","[^"]*","([^"]*)"')

_DRIVE_LISTING_TIMEOUT = 30.0


def parse_drive_listing(output: str) -> dict[str, int]:
    """Map each device identifier in a makemkvcon DRV listing to its disc index.

    Slots with an empty device string are drives MakeMKV enumerated but cannot
    use, and are omitted. A malformed line is skipped rather than raised on: a
    single unparseable row must not cost us the whole mapping.
    """
    mapping: dict[str, int] = {}
    for line in output.splitlines():
        m = _DRV_RE.match(line.strip())
        if not m:
            continue
        device = m.group(2).strip()
        if device:
            mapping[device] = int(m.group(1))
    return mapping


def _run_drive_listing(makemkv_path: str) -> str:
    """Run the drive enumeration synchronously (called via asyncio.to_thread)."""
    result = subprocess.run(
        [makemkv_path, "-r", "info", _DRIVE_LISTING_INDEX],
        capture_output=True,
        text=True,
        timeout=_DRIVE_LISTING_TIMEOUT,
        check=False,
    )
    # Non-zero is expected: index 9999 does not exist. The DRV lines we want are
    # printed before the failure, so stdout is used regardless of return code.
    return result.stdout


async def resolve_disc_index(drive: str, makemkv_path: str) -> str | None:
    """Return the ``disc:N`` spec for a drive, or None if it cannot be resolved.

    ``makemkvcon backup`` accepts only ``disc:N``, so a backup cannot start
    without this. None is a normal outcome, not an error: the caller degrades to
    a direct rip.
    """
    normalized = drive.replace("dev:", "").rstrip("\\")
    try:
        output = await asyncio.to_thread(_run_drive_listing, makemkv_path)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not enumerate MakeMKV drives: {e}", exc_info=True)
        return None

    mapping = parse_drive_listing(output)
    for device, index in mapping.items():
        if device.rstrip("\\").lower() == normalized.lower():
            return f"disc:{index}"

    logger.warning(
        f"Drive {normalized} not found in MakeMKV drive listing "
        f"(saw: {sorted(mapping)}); cannot back up"
    )
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_disc_source.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/disc_source.py tests/unit/test_disc_source.py && uv run ruff format app/core/disc_source.py tests/unit/test_disc_source.py
git add backend/app/core/disc_source.py backend/tests/unit/test_disc_source.py
git commit -m "feat(backup): resolve a drive letter to a MakeMKV disc index"
```

---

## Task 3: Thread `DiscSource` through the Extractor

**Files:**
- Modify: `backend/app/core/extractor.py:65-72` (`_to_drive_spec`), `:670-676` (`_get_drive_lock`), `:678-700` (`scan_disc`), `:772-826` (`rip_titles`)
- Test: `backend/tests/unit/test_extractor_source.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_extractor_source.py`:

```python
"""The Extractor accepts a DiscSource, not just a drive letter."""

from app.core.disc_source import DiscSource
from app.core.extractor import MakeMKVExtractor, _to_source_spec


class TestToSourceSpec:
    def test_bare_drive_gets_a_dev_prefix(self):
        assert _to_source_spec("E:") == "dev:E:"

    def test_disc_index_passes_through(self):
        assert _to_source_spec("disc:0") == "disc:0"

    def test_file_spec_passes_through(self):
        assert _to_source_spec("file:/backups/x") == "file:/backups/x"

    def test_disc_source_object_is_accepted(self):
        assert _to_source_spec(DiscSource.for_backup("/backups/x")) == "file:/backups/x"

    def test_iso_is_handed_to_makemkv_as_a_file_source(self):
        assert _to_source_spec(DiscSource.parse("iso:/b/x.iso")) == "file:/b/x.iso"


class TestSourceLocking:
    def test_drive_forms_share_one_lock(self):
        ex = MakeMKVExtractor(makemkv_path="mmk")
        assert ex._get_source_lock("E:") is ex._get_source_lock("dev:E:")

    def test_a_backup_does_not_take_the_drive_lock(self):
        # Regression guard: extracting from a backup must not block the optical
        # drive at the same letter from scanning the next disc.
        ex = MakeMKVExtractor(makemkv_path="mmk")
        assert ex._get_source_lock("E:") is not ex._get_source_lock("file:E:\\backups\\x")

    def test_the_same_backup_path_shares_one_lock(self):
        ex = MakeMKVExtractor(makemkv_path="mmk")
        assert ex._get_source_lock("file:/b/x") is ex._get_source_lock("file:/b/x")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_extractor_source.py -q
```

Expected: `ImportError: cannot import name '_to_source_spec'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/extractor.py`, replace `_to_drive_spec` (line 65) with:

```python
def _to_source_spec(source: "DiscSource | str") -> str:
    """Normalize a source into the argument makemkvcon accepts.

    Accepts a DiscSource, a bare drive identifier, or an already-schemed spec,
    so existing call sites that pass ``job.drive_id`` keep working unchanged.
    """
    from app.core.disc_source import DiscSource

    if isinstance(source, DiscSource):
        return source.makemkv_arg
    return DiscSource.parse(source).makemkv_arg
```

Replace `_get_drive_lock` (line 670) with:

```python
    def _get_source_lock(self, source: "DiscSource | str") -> asyncio.Lock:
        """Get or create the per-source lock serializing MakeMKV operations.

        Two makemkvcon processes fighting over one optical drive stall both, so
        every physical drive form keys to one lock. A backup or ISO keys on its
        own path instead: reading a backup does not touch the drive, and making
        it wait for the drive lock would silently serialize work that is now
        genuinely independent.
        """
        from app.core.disc_source import DiscSource

        parsed = source if isinstance(source, DiscSource) else DiscSource.parse(source)
        key = parsed.lock_key
        if key not in self._source_locks:
            self._source_locks[key] = asyncio.Lock()
        return self._source_locks[key]
```

In `__init__` (line 659), rename the attribute:

```python
        # Per-source locks prevent concurrent MakeMKV operations on one source.
        # Two makemkvcon processes fighting over one drive causes both to stall.
        self._source_locks: dict[str, asyncio.Lock] = {}
```

Then update every internal reference: `scan_disc`, `rip_titles` and their `_unlocked` twins call `self._get_source_lock(...)` and `_to_source_spec(...)`. Widen the parameter type on `scan_disc`, `_scan_disc_unlocked`, `rip_titles` and `_rip_titles_unlocked` from `drive: str` to `source: "DiscSource | str"`, keeping the positional order so no call site needs to change. Update the docstrings to say "source" rather than "drive".

Add at the top of the module, under the existing imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.disc_source import DiscSource
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_extractor_source.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the existing extractor tests to verify nothing regressed**

```bash
cd backend && uv run pytest tests/unit -q -k "extractor or rip"
```

Expected: all pass. If any test patches or asserts on `_to_drive_spec` or `_get_drive_lock` by name, update the test to the new name; those are the only two renames.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app/core/extractor.py tests/unit/test_extractor_source.py && uv run ruff format app/core/extractor.py tests/unit/test_extractor_source.py
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_source.py backend/tests/unit
git commit -m "refactor(backup): make the Extractor source-shaped rather than drive-shaped"
```

---

## Task 4: Database columns and migration

**Files:**
- Modify: `backend/app/models/disc_job.py`
- Modify: `backend/app/models/app_config.py`
- Create: `backend/migrations/versions/<rev>_add_backup_columns.py`
- Test: `backend/tests/unit/test_backup_columns.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_backup_columns.py`:

```python
"""The backup columns exist, default correctly, and read safely on legacy rows."""

from app.models import AppConfig, DiscJob, JobState


class TestDiscJobBackupColumns:
    def test_new_job_has_no_source_spec(self):
        # None means "legacy or drive-sourced"; DiscSource.from_job falls back.
        assert DiscJob(drive_id="E:").source_spec is None

    def test_new_job_has_no_backup_path_or_status(self):
        job = DiscJob(drive_id="E:")
        assert job.backup_path is None
        assert job.backup_status is None


class TestAppConfigBackupFields:
    def test_backup_is_off_by_default(self):
        # Opt-in: an upgraded row with NULL must read as disabled, which is why
        # the column carries server_default 0 (see discord_notify_ripped).
        assert AppConfig().backup_before_rip is False

    def test_backup_path_defaults_empty(self):
        assert AppConfig().backup_path == ""

    def test_backing_up_has_its_own_timeout(self):
        # A 40 GB sequential copy cannot share the ripping phase ceiling.
        assert AppConfig().timeout_backing_up_seconds > 0


class TestJobState:
    def test_backing_up_exists_and_is_not_terminal(self):
        from app.models import TERMINAL_JOB_STATES

        assert JobState.BACKING_UP.value == "backing_up"
        assert JobState.BACKING_UP not in TERMINAL_JOB_STATES
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_backup_columns.py -q
```

Expected: `AttributeError: type object 'DiscJob' has no attribute 'source_spec'` (or the `JobState.BACKING_UP` equivalent).

- [ ] **Step 3: Add the model fields**

In `backend/app/models/disc_job.py`, add to the `JobState` enum after `IDENTIFYING`:

```python
    BACKING_UP = "backing_up"  # Writing a full decrypted disc copy before extraction
```

Add to `DiscJob`, in the Paths block after `import_manifest_json`:

```python
    # What MakeMKV is pointed at for this job: "dev:E:", "disc:0",
    # "file:<backup folder>" or "iso:<file>". None on every row written before
    # backup support, meaning "legacy: derive from drive_id" (DiscSource.from_job).
    # Diverges from drive_id exactly once: after a successful backup, drive_id is
    # still the drive the disc came from while source_spec has become the copy.
    source_spec: str | None = Field(default=None)
    # Where this job's backup landed. Kept for history and for re-import; Engram
    # never deletes it.
    backup_path: str | None = Field(default=None)
    # "pending" | "completed" | "failed" | "skipped". None means no backup was
    # attempted (the feature is off). Mirrors subtitle_status: a small closed set
    # of literals here, with the free-text explanation in its own column, so no
    # consumer has to parse a delimiter out of a status.
    backup_status: str | None = Field(default=None)
    # Why the backup failed or was skipped, e.g. "insufficient_space",
    # "not_configured", "no_disc_index", "unsupported_disc", or a MakeMKV error
    # string. None when backup_status is pending or completed.
    backup_status_reason: str | None = Field(default=None)
```

In `backend/app/models/app_config.py`, add next to the other path fields (near line 43):

```python
    # Root for full decrypted disc backups. Empty means the feature cannot run,
    # which the backup phase reports as a "not_configured" skip rather than failing.
    backup_path: str = ""
```

Add next to `always_review` (near line 141):

```python
    # Write a full decrypted disc copy under backup_path after identification and
    # extract from that copy instead of the drive. server_default 0, like
    # discord_notify_ripped: an opt-in feature must read a NULL as disabled.
    backup_before_rip: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})
```

Add next to the other phase timeouts (near line 121), matching their `Field(...)` shape:

```python
    # A 40 GB sequential copy is slower than any other phase and, on the scratched
    # discs this feature exists for, deliberately slow. Two hours of no output
    # growth, not of wall clock.
    timeout_backing_up_seconds: int = Field(
        default=7200, sa_column_kwargs={"server_default": text("7200")}
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_backup_columns.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Generate the migration**

Find the current head, then write the migration by hand (do not autogenerate; autogenerate against a live dev DB picks up unrelated drift):

```bash
cd backend && uv run alembic heads
```

Create `backend/migrations/versions/<newrev>_add_backup_columns.py`, replacing `<HEAD>` with the revision printed above and `<newrev>` with a fresh 12-hex-character id:

```python
"""Add disc backup columns.

Revision ID: <newrev>
Revises: <HEAD>
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "<newrev>"
down_revision: str | Sequence[str] | None = "<HEAD>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("disc_jobs", sa.Column("source_spec", sa.String(), nullable=True))
    op.add_column("disc_jobs", sa.Column("backup_path", sa.String(), nullable=True))
    op.add_column("disc_jobs", sa.Column("backup_status", sa.String(), nullable=True))
    op.add_column(
        "disc_jobs", sa.Column("backup_status_reason", sa.String(), nullable=True)
    )
    op.add_column(
        "app_config",
        sa.Column("backup_path", sa.String(), nullable=False, server_default=sa.text("''")),
    )
    # server_default 0: this is opt-in, so an upgraded row must come back OFF.
    op.add_column(
        "app_config",
        sa.Column(
            "backup_before_rip", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "timeout_backing_up_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("7200"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("timeout_backing_up_seconds")
        batch_op.drop_column("backup_before_rip")
        batch_op.drop_column("backup_path")
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("backup_status_reason")
        batch_op.drop_column("backup_status")
        batch_op.drop_column("backup_path")
        batch_op.drop_column("source_spec")
```

This revision contains only `add_column` calls, which is required: `_run_alembic_upgrade`'s duplicate-column self-heal stamps a whole revision as applied when any `ADD COLUMN` in it collides with `_add_missing_columns`, so mixing other DDL into this revision would let that DDL be silently skipped.

- [ ] **Step 6: Verify the migration applies to a scratch database**

```bash
cd backend && DATABASE_URL="sqlite+aiosqlite:///./scratch-backup-migration.db" uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())" && rm -f scratch-backup-migration.db
```

Expected: exits 0, logs `Database initialized successfully`. Never point this at `engram.db`.

- [ ] **Step 7: Lint and commit**

```bash
cd backend && uv run ruff check app/models tests/unit/test_backup_columns.py migrations/versions && uv run ruff format app/models tests/unit/test_backup_columns.py migrations/versions
git add backend/app/models backend/migrations/versions backend/tests/unit/test_backup_columns.py
git commit -m "feat(backup): add BACKING_UP state and backup columns"
```

---

## Task 5: Backup destination naming

**Files:**
- Create: `backend/app/core/backup_paths.py`
- Test: `backend/tests/unit/test_backup_paths.py`

> **Superseded during review.** The code listed below hardcodes `Name (Year)` and
> `Season NN`, which was wrong: library naming is user-configurable through
> `naming_movie_format`, `naming_tv_show_format` and `naming_season_format`, and
> the Organizer builds its folders with `format_movie_folder`,
> `format_tv_show_folder` and `format_season_folder`. Hardcoding the default
> shape gave a user with a custom format a backup tree that no longer mirrored
> their library, which is the one thing this module exists to prevent. The
> shipped implementation takes `backup_destination(job, config)` (an `AppConfig`,
> not a root string) and delegates to those three helpers. Note the consequence:
> the default `naming_tv_show_format` is `"{show}"`, so a TV backup folder
> carries no year by default, exactly as the library folder does not. See
> commit `af801b57`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_backup_paths.py`:

```python
"""Backup destination naming and the free-space preflight."""

from pathlib import Path
from unittest.mock import patch

from app.core.backup_paths import backup_destination, has_room_for_backup
from app.models import ContentType, DiscJob


def _job(**kw) -> DiscJob:
    base = {"drive_id": "E:", "volume_label": "DISC_LABEL"}
    base.update(kw)
    return DiscJob(**base)


class TestBackupDestination:
    def test_movie_uses_name_and_year(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception", tmdb_year=2010)
        assert backup_destination(job, "/b") == Path("/b/Movies/Inception (2010)")

    def test_movie_without_year_omits_the_parenthetical(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="Inception")
        assert backup_destination(job, "/b") == Path("/b/Movies/Inception")

    def test_tv_uses_show_season_and_disc(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            disc_number=2,
        )
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Season 01/Disc 2")

    def test_tv_prefers_the_discdb_disc_slug(self):
        job = _job(
            content_type=ContentType.TV,
            tmdb_name="Frasier",
            tmdb_year=1993,
            detected_season=1,
            discdb_disc_slug="S01D02",
        )
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Season 01/S01D02")

    def test_tv_without_a_season_still_files_under_the_show(self):
        job = _job(content_type=ContentType.TV, tmdb_name="Frasier", tmdb_year=1993)
        assert backup_destination(job, "/b") == Path("/b/TV/Frasier (1993)/Disc 1")

    def test_unidentified_falls_back_to_the_volume_label(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="THE_SWEETEST_THING")
        assert backup_destination(job, "/b") == Path("/b/Unidentified/THE_SWEETEST_THING")

    def test_unidentified_without_a_label_uses_the_job_id(self):
        job = _job(content_type=ContentType.UNKNOWN, volume_label="")
        job.id = 42
        assert backup_destination(job, "/b") == Path("/b/Unidentified/job-42")

    def test_path_separators_in_a_title_cannot_escape_the_root(self):
        job = _job(content_type=ContentType.MOVIE, tmdb_name="../../etc/passwd")
        dest = backup_destination(job, "/b")
        assert Path("/b") in dest.parents

    def test_empty_root_is_rejected(self):
        assert backup_destination(_job(), "") is None


class TestHasRoomForBackup:
    def test_enough_space_passes(self, tmp_path):
        with patch("shutil.disk_usage", return_value=(0, 0, 100 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=40 * 1024**3) is True

    def test_margin_is_applied(self, tmp_path):
        # 40 GB needed x 1.15 = 46 GB; 42 GB free is not enough.
        with patch("shutil.disk_usage", return_value=(0, 0, 42 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=40 * 1024**3) is False

    def test_unreadable_destination_fails_closed(self, tmp_path):
        with patch("shutil.disk_usage", side_effect=OSError("gone")):
            assert has_room_for_backup(tmp_path, needed_bytes=1) is False

    def test_unknown_size_falls_back_to_a_full_bluray(self, tmp_path):
        # needed_bytes 0 means the scan gave no sizes. Assume a 50 GB BD-DL
        # rather than waving the check through.
        with patch("shutil.disk_usage", return_value=(0, 0, 10 * 1024**3)):
            assert has_room_for_backup(tmp_path, needed_bytes=0) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_backup_paths.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.core.backup_paths'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/core/backup_paths.py`:

```python
"""Where a disc backup goes, and whether there is room for it.

Naming mirrors the library layout so a preservation shelf browses the same way
the library does. Sanitization reuses the Organizer's helper rather than
reimplementing it, so the two cannot drift.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.models import ContentType, DiscJob

logger = logging.getLogger(__name__)

# Free space must exceed the estimate by this factor. A backup carries the
# container and filesystem overhead the per-title sizes do not.
_SPACE_MARGIN = 1.15

# Assumed disc size when the scan produced no usable sizes. A dual-layer
# Blu-ray, so an unknown disc is treated as the largest realistic case rather
# than waved through.
_ASSUMED_DISC_BYTES = 50 * 1024**3


def _safe_name(raw: str) -> str:
    """Sanitize one path component, reusing the Organizer's rules."""
    from app.core.organizer import sanitize_filename

    cleaned = sanitize_filename(raw or "").strip().strip(".")
    # sanitize_filename handles the illegal-character set; separators are
    # stripped here as well so a crafted title cannot introduce a new path
    # component and climb out of the backup root.
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    return cleaned or "Unknown"


def backup_destination(job: DiscJob, backup_root: str) -> Path | None:
    """Compute where this job's backup should be written.

    Returns None when no root is configured, which the caller reports as
    a "not_configured" skip.
    """
    if not backup_root:
        return None

    root = Path(backup_root).expanduser()
    name = job.tmdb_name or job.detected_title

    if job.content_type == ContentType.MOVIE and name:
        folder = f"{_safe_name(name)} ({job.tmdb_year})" if job.tmdb_year else _safe_name(name)
        return root / "Movies" / folder

    if job.content_type == ContentType.TV and name:
        show = f"{_safe_name(name)} ({job.tmdb_year})" if job.tmdb_year else _safe_name(name)
        disc = _safe_name(job.discdb_disc_slug or f"Disc {job.disc_number or 1}")
        base = root / "TV" / show
        if job.detected_season is not None:
            return base / f"Season {job.detected_season:02d}" / disc
        return base / disc

    label = _safe_name(job.volume_label) if job.volume_label else ""
    if not label or label == "Unknown":
        label = f"job-{job.id}" if job.id else "job-unknown"
    return root / "Unidentified" / label


def has_room_for_backup(dest: Path, needed_bytes: int) -> bool:
    """Whether ``dest``'s filesystem has room for the backup, with margin.

    Fails closed: an unreadable destination returns False and the caller falls
    back to a direct rip. A false negative costs one direct rip; a false
    positive costs a half-written 40 GB folder and a failed job.
    """
    estimate = needed_bytes if needed_bytes > 0 else _ASSUMED_DISC_BYTES
    required = int(estimate * _SPACE_MARGIN)
    # The destination itself may not exist yet, so measure the nearest existing
    # ancestor: that is the filesystem the write will land on.
    probe = dest
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read free space at {probe}: {e}")
        return False
    if free < required:
        logger.warning(
            f"Not enough room for backup at {probe}: "
            f"{free / 1024**3:.1f} GB free, {required / 1024**3:.1f} GB required"
        )
        return False
    return True
```

If `sanitize_filename` does not exist in `app/core/organizer.py` under that name, use whatever the Organizer's existing name-sanitization helper is called and adjust the import. Do not write a second sanitizer.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_backup_paths.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/backup_paths.py tests/unit/test_backup_paths.py && uv run ruff format app/core/backup_paths.py tests/unit/test_backup_paths.py
git add backend/app/core/backup_paths.py backend/tests/unit/test_backup_paths.py
git commit -m "feat(backup): compute backup destinations and preflight free space"
```

---

## Task 6: `Extractor.backup_disc()`

**Files:**
- Modify: `backend/app/core/extractor.py`
- Test: `backend/tests/unit/test_backup_disc.py`

> **Corrected during implementation and review.** Four things below are wrong as
> written. (1) The `_parse_backup_progress` fixtures contradict the reference
> implementation directly beneath them: in `PRGV:current,total,max`, `current` is
> the per-title bar and `total` is the overall bar, pinned by a verbatim real rip
> log in `tests/unit/test_rip_progress.py`, so `total/maximum` is right and the
> fixtures had the fields swapped. Reading `current` would run the backup bar
> backwards once per sub-operation. (2) `_run_backup_process` is an instance
> method, not a staticmethod, so it can register its `Popen` in `self._processes`
> and be cancelled; a staticmethod leaves a multi-hour backup uncancellable.
> (3) The log file is `backup.log`, matching `scan.log` and `rip.log`, because
> callers already pass a per-job log dir. (4) `_last_msg_text` parses the first
> quoted field by regex rather than `split(",", 4)[4]`, because MakeMKV's MSG
> format puts the format string at index 4 and a comma inside the message breaks
> the naive split. Review then added a guarded `mkdir`, a clean-working-directory
> rule for a retried backup (at most one stale `.partial` preserved), and a
> `BackupResult.already_existed` flag. See commits `38598c54` and `8d4b1ce0`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_backup_disc.py`:

```python
"""makemkvcon backup wrapper."""

import pytest

from app.core.extractor import (
    BackupResult,
    MakeMKVExtractor,
    _build_backup_command,
    _parse_backup_progress,
)


class TestBuildBackupCommand:
    def test_uses_the_disc_index_and_decrypt_flag(self):
        cmd = _build_backup_command("mmk", "disc:0", "/b/out.partial")
        assert cmd == ["mmk", "-r", "--progress=-same", "--decrypt", "backup", "disc:0", "/b/out.partial"]

    def test_refuses_a_dev_source(self):
        # makemkvcon backup accepts only disc:N. Catching it here beats a
        # confusing MakeMKV error 40 minutes into a job.
        with pytest.raises(ValueError):
            _build_backup_command("mmk", "dev:E:", "/b/out.partial")


class TestParseBackupProgress:
    def test_reads_a_prgv_line(self):
        # PRGV:current,total,max
        assert _parse_backup_progress("PRGV:32768,65536,65536") == pytest.approx(50.0)

    def test_zero_max_is_ignored(self):
        assert _parse_backup_progress("PRGV:0,0,0") is None

    def test_non_progress_line_is_ignored(self):
        assert _parse_backup_progress('MSG:1005,0,1,"Backup done"') is None


class TestBackupDisc:
    @pytest.mark.asyncio
    async def test_success_renames_partial_to_final(self, tmp_path, monkeypatch):
        dest = tmp_path / "Inception (2010)"

        def fake_run(cmd, on_line, job_id):
            partial = tmp_path / "Inception (2010).partial"
            (partial / "BDMV").mkdir(parents=True)
            on_line("PRGV:65536,65536,65536")
            return 0, "PRGV:65536,65536,65536\n"

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = MakeMKVExtractor(makemkv_path="mmk")
        result = await ex.backup_disc("disc:0", dest, job_id=1)

        assert result.success is True
        assert dest.exists()
        assert not (tmp_path / "Inception (2010).partial").exists()

    @pytest.mark.asyncio
    async def test_failure_keeps_the_partial(self, tmp_path, monkeypatch):
        # A partial backup of a dying disc has salvage value; the user decides
        # whether to discard it, not Engram.
        dest = tmp_path / "Scratched"

        def fake_run(cmd, on_line, job_id):
            partial = tmp_path / "Scratched.partial"
            (partial / "BDMV").mkdir(parents=True)
            return 1, "MSG:5010,0,0,\"Failed to read disc\"\n"

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = MakeMKVExtractor(makemkv_path="mmk")
        result = await ex.backup_disc("disc:0", dest, job_id=1)

        assert result.success is False
        assert result.error_message
        assert (tmp_path / "Scratched.partial").exists()
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_progress_callback_receives_percentages(self, tmp_path, monkeypatch):
        seen = []

        def fake_run(cmd, on_line, job_id):
            (tmp_path / "X.partial").mkdir(parents=True)
            on_line("PRGV:16384,65536,65536")
            on_line("PRGV:65536,65536,65536")
            return 0, ""

        monkeypatch.setattr(MakeMKVExtractor, "_run_backup_process", staticmethod(fake_run))
        ex = MakeMKVExtractor(makemkv_path="mmk")
        await ex.backup_disc("disc:0", tmp_path / "X", progress_callback=seen.append, job_id=1)

        assert seen == [pytest.approx(25.0), pytest.approx(100.0)]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_backup_disc.py -q
```

Expected: `ImportError: cannot import name 'BackupResult'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/extractor.py`, add next to `RipResult` (line 289):

```python
@dataclass
class BackupResult:
    """Result of a full-disc backup operation."""

    success: bool
    dest: Path | None = None
    error_message: str | None = None
```

Add near `_build_rip_commands` (line 180):

```python
# PRGV:current,total,max is MakeMKV's global progress line.
_PRGV_RE = re.compile(r"^PRGV:(\d+),(\d+),(\d+)")


def _build_backup_command(makemkv_path: str, source_spec: str, dest: str) -> list[str]:
    """Build the argv for a full decrypted disc backup.

    ``makemkvcon backup`` accepts only a ``disc:N`` source, never ``dev:``.
    Rejecting a dev spec here turns a 40-minute mystery into an immediate,
    named fallback.
    """
    if not source_spec.startswith("disc:"):
        raise ValueError(
            f"makemkvcon backup requires a disc:N source, got {source_spec!r}. "
            f"Resolve it with disc_source.resolve_disc_index() first."
        )
    return [
        makemkv_path,
        "-r",
        "--progress=-same",
        "--decrypt",
        "backup",
        source_spec,
        dest,
    ]


def _parse_backup_progress(line: str) -> float | None:
    """Percentage from a PRGV line, or None if the line carries no progress."""
    m = _PRGV_RE.match(line.strip())
    if not m:
        return None
    _current, total, maximum = (int(g) for g in m.groups())
    if maximum <= 0:
        return None
    return min(100.0, total / maximum * 100.0)
```

Add the method to `MakeMKVExtractor`:

```python
    @staticmethod
    def _run_backup_process(cmd: list[str], on_line, job_id: int) -> tuple[int, str]:
        """Run makemkvcon backup, streaming stdout to ``on_line``.

        A separate staticmethod so tests can substitute it without a real
        subprocess. Runs in a thread (Windows asyncio subprocess workaround),
        matching how scan_disc and rip_titles already invoke MakeMKV.
        """
        collected: list[str] = []
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            for line in proc.stdout or []:
                collected.append(line)
                on_line(line)
        finally:
            proc.wait()
        return proc.returncode, "".join(collected)

    async def backup_disc(
        self,
        source: "DiscSource | str",
        dest: Path,
        progress_callback=None,
        log_dir: Path | None = None,
        *,
        job_id: int = 0,
    ) -> BackupResult:
        """Write a full decrypted copy of the disc to ``dest``.

        Writes to ``<dest>.partial`` and renames on success, so a killed or
        crashed backup never leaves something that looks complete. On failure
        the ``.partial`` is left in place: a partial backup of a dying disc has
        salvage value, and discarding it is the user's call.
        """
        spec = _to_source_spec(source)
        partial = dest.with_name(dest.name + ".partial")

        lock = self._get_source_lock(source)
        async with lock:
            try:
                cmd = _build_backup_command(str(self.makemkv_path), spec, str(partial))
            except ValueError as e:
                return BackupResult(success=False, error_message=str(e))

            dest.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Job {job_id}: backing up disc: {' '.join(cmd)}")

            def on_line(line: str) -> None:
                pct = _parse_backup_progress(line)
                if pct is not None and progress_callback is not None:
                    _safe_callback(progress_callback, pct, label="backup progress")

            returncode, output = await asyncio.to_thread(
                self._run_backup_process, cmd, on_line, job_id
            )

            if log_dir is not None:
                _save_makemkv_log(Path(log_dir) / f"backup_job{job_id}.log", output)

            if returncode != 0 or not partial.exists():
                reason = _last_msg_text(output) or f"makemkvcon backup exited {returncode}"
                logger.error(f"Job {job_id}: backup failed: {reason}")
                return BackupResult(success=False, error_message=reason)

            try:
                if dest.exists():
                    # Destination appeared while we were copying. Keep the
                    # existing one and drop ours rather than clobbering a
                    # complete backup with a fresh one.
                    logger.warning(f"Job {job_id}: {dest} already exists; keeping it")
                else:
                    partial.rename(dest)
            except OSError as e:
                logger.error(f"Job {job_id}: could not finalize backup: {e}", exc_info=True)
                return BackupResult(success=False, error_message=str(e))

            logger.info(f"Job {job_id}: backup complete at {dest}")
            return BackupResult(success=True, dest=dest)
```

Add the helper next to `_extract_created_mkv`:

```python
def _last_msg_text(output: str) -> str | None:
    """The text of the last MSG line in MakeMKV output, for error reporting."""
    last = None
    for line in output.splitlines():
        if line.startswith("MSG:"):
            parts = line.split(",", 4)
            if len(parts) >= 5:
                last = parts[4].strip().strip('"')
    return last
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_backup_disc.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/extractor.py tests/unit/test_backup_disc.py && uv run ruff format app/core/extractor.py tests/unit/test_backup_disc.py
git add backend/app/core/extractor.py backend/tests/unit/test_backup_disc.py
git commit -m "feat(backup): add makemkvcon backup wrapper with partial-rename safety"
```

---

## Task 7: State machine transitions and phase timeout

**Files:**
- Modify: `backend/app/services/job_state_machine.py:22-56`
- Modify: `backend/app/services/job_manager.py:1998-2005`
- Test: `backend/tests/unit/test_job_state_machine.py` (existing file, add cases)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_job_state_machine.py`:

```python
class TestBackingUpTransitions:
    def test_identifying_can_enter_backing_up(self):
        assert JobState.BACKING_UP in JobStateMachine.VALID_TRANSITIONS[JobState.IDENTIFYING]

    def test_backing_up_can_start_ripping(self):
        assert JobState.RIPPING in JobStateMachine.VALID_TRANSITIONS[JobState.BACKING_UP]

    def test_backing_up_can_park_for_review(self):
        # Reconciliation failure parks here; it must not fall back to the drive,
        # which may already be ejected.
        assert JobState.REVIEW_NEEDED in JobStateMachine.VALID_TRANSITIONS[JobState.BACKING_UP]

    def test_backing_up_can_fail(self):
        assert JobState.FAILED in JobStateMachine.VALID_TRANSITIONS[JobState.BACKING_UP]

    def test_backing_up_cannot_skip_straight_to_matching(self):
        # There are no MKVs yet. Skipping RIPPING would strand the job.
        assert JobState.MATCHING not in JobStateMachine.VALID_TRANSITIONS[JobState.BACKING_UP]

    def test_review_can_return_to_backing_up(self):
        # A disc parked for a name prompt still needs its backup once answered.
        assert JobState.BACKING_UP in JobStateMachine.VALID_TRANSITIONS[JobState.REVIEW_NEEDED]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_job_state_machine.py -q -k BackingUp
```

Expected: `KeyError: <JobState.BACKING_UP>` or an assertion failure.

- [ ] **Step 3: Write the implementation**

In `backend/app/services/job_state_machine.py`, edit `VALID_TRANSITIONS`:

```python
        JobState.IDENTIFYING: {
            JobState.BACKING_UP,  # backup_before_rip: copy the disc, then extract from it
            JobState.RIPPING,
            JobState.MATCHING,  # import/staging path skips RIPPING (files already exist)
            JobState.ORGANIZING,  # import/staging movie path skips RIPPING + matching
            JobState.REVIEW_NEEDED,
            JobState.FAILED,
        },
        # No edge to MATCHING or ORGANIZING: a backup produces no MKVs, so a job
        # that skipped RIPPING from here would have nothing to match or organize.
        JobState.BACKING_UP: {
            JobState.RIPPING,
            JobState.REVIEW_NEEDED,
            JobState.FAILED,
        },
        JobState.REVIEW_NEEDED: {
            JobState.IDENTIFYING,  # Re-identify with corrected title
            JobState.BACKING_UP,  # Answered a pre-rip prompt with backup enabled
            JobState.RIPPING,
            JobState.MATCHING,  # Re-match with corrected metadata (post-rip)
            JobState.COMPLETED,
            JobState.FAILED,
        },
```

In `backend/app/services/job_manager.py`, extend `_phase_timeout`:

```python
    def _phase_timeout(config, state: JobState) -> int | None:
        """Per-phase no-activity ceiling (seconds), or None for resting/untimed states."""
        return {
            JobState.IDENTIFYING: config.timeout_identifying_seconds,
            JobState.BACKING_UP: config.timeout_backing_up_seconds,
            JobState.RIPPING: config.timeout_ripping_seconds,
            JobState.MATCHING: config.timeout_matching_seconds,
            JobState.ORGANIZING: config.timeout_organizing_seconds,
        }.get(state)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_job_state_machine.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/services/job_state_machine.py app/services/job_manager.py && uv run ruff format app/services/job_state_machine.py app/services/job_manager.py
git add backend/app/services/job_state_machine.py backend/app/services/job_manager.py backend/tests/unit/test_job_state_machine.py
git commit -m "feat(backup): wire BACKING_UP into the state machine and watchdog"
```

---

## Task 8: `backup_progress` WebSocket message

**Files:**
- Modify: `backend/app/api/websocket.py`
- Modify: `backend/app/services/event_broadcaster.py`
- Test: `backend/tests/integration/test_websocket_e2e.py` (existing file, add cases)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_websocket_e2e.py`:

```python
class TestBackupProgressContract:
    """The parameter names must match across broadcaster, manager, and wire.

    This chain has produced two production bugs already (error= vs
    error_message=), so it is asserted end to end rather than per layer.
    """

    @pytest.mark.asyncio
    async def test_broadcaster_reaches_the_wire_with_the_documented_fields(self):
        from unittest.mock import AsyncMock

        from app.services.event_broadcaster import EventBroadcaster

        ws = AsyncMock()
        broadcaster = EventBroadcaster(ws)
        await broadcaster.broadcast_backup_progress(
            job_id=7, current_bytes=100, total_bytes=400, speed="2.5x", eta_seconds=90
        )

        ws.broadcast_backup_progress.assert_awaited_once_with(
            7, current_bytes=100, total_bytes=400, speed="2.5x", eta=90
        )

    @pytest.mark.asyncio
    async def test_manager_emits_the_documented_message_shape(self):
        from unittest.mock import AsyncMock, patch

        from app.api.websocket import ConnectionManager

        mgr = ConnectionManager()
        with patch.object(mgr, "broadcast", new=AsyncMock()) as bcast:
            await mgr.broadcast_backup_progress(
                7, current_bytes=100, total_bytes=400, speed="2.5x", eta=90
            )

        msg = bcast.await_args.args[0]
        assert msg["type"] == "backup_progress"
        assert msg["data"] == {
            "job_id": 7,
            "current_bytes": 100,
            "total_bytes": 400,
            "speed": "2.5x",
            "eta": 90,
        }
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/integration/test_websocket_e2e.py -q -k BackupProgress
```

Expected: `AttributeError: 'ConnectionManager' object has no attribute 'broadcast_backup_progress'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/api/websocket.py`, add to `ConnectionManager`:

```python
    async def broadcast_backup_progress(
        self,
        job_id: int,
        *,
        current_bytes: int,
        total_bytes: int,
        speed: str | None = None,
        eta: int | None = None,
    ) -> None:
        """Broadcast disc-backup copy progress.

        A distinct message type rather than rip_progress: a client that renders
        "ripping" from rip_progress would be reporting a phase that produces no
        MKV at all.
        """
        await self.broadcast(
            {
                "type": "backup_progress",
                "data": {
                    "job_id": job_id,
                    "current_bytes": current_bytes,
                    "total_bytes": total_bytes,
                    "speed": speed,
                    "eta": eta,
                },
            }
        )
```

In `backend/app/services/event_broadcaster.py`, add:

```python
    async def broadcast_backup_progress(
        self,
        job_id: int,
        current_bytes: int,
        total_bytes: int,
        speed: str | None = None,
        eta_seconds: int | None = None,
    ) -> None:
        """Broadcast disc-backup copy progress."""
        await self._ws.broadcast_backup_progress(
            job_id,
            current_bytes=current_bytes,
            total_bytes=total_bytes,
            speed=speed,
            eta=eta_seconds,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/integration/test_websocket_e2e.py -q -k BackupProgress
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/websocket.py app/services/event_broadcaster.py && uv run ruff format app/api/websocket.py app/services/event_broadcaster.py
git add backend/app/api/websocket.py backend/app/services/event_broadcaster.py backend/tests/integration/test_websocket_e2e.py
git commit -m "feat(backup): add backup_progress websocket message"
```

---

## Task 9: Post-backup title reconciliation

**Files:**
- Create: `backend/app/services/backup_reconcile.py`
- Test: `backend/tests/unit/test_backup_reconciliation.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_backup_reconciliation.py`:

```python
"""Re-map DiscTitle.title_index onto a re-scan of the backup."""

from app.core.extractor import TitleInfo
from app.models import DiscTitle
from app.services.backup_reconcile import ReconcileOutcome, reconcile_titles


def _title(index: int, duration: int, source: str = "", segments: str = "") -> DiscTitle:
    return DiscTitle(
        job_id=1,
        title_index=index,
        duration_seconds=duration,
        source_filename=source,
        segment_map=segments,
    )


def _scanned(index: int, duration: int, source: str = "", segments: str = "") -> TitleInfo:
    return TitleInfo(
        index=index,
        duration_seconds=duration,
        source_filename=source,
        segment_map=segments,
        file_size_bytes=0,
        chapter_count=0,
    )


class TestIdenticalEnumeration:
    def test_same_order_and_durations_keeps_every_index(self):
        db = [_title(0, 2600), _title(1, 2610)]
        scan = [_scanned(0, 2600), _scanned(1, 2610)]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.IDENTICAL
        assert result.remap == {}

    def test_small_duration_drift_still_counts_as_identical(self):
        # MakeMKV rounds; a one-second difference is not a different disc.
        db = [_title(0, 2600)]
        scan = [_scanned(0, 2601)]
        assert reconcile_titles(db, scan).outcome is ReconcileOutcome.IDENTICAL


class TestRemapped:
    def test_shifted_indices_remap_by_source_filename(self):
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 2610, "00002.m2ts")]
        scan = [_scanned(5, 2600, "00001.m2ts"), _scanned(6, 2610, "00002.m2ts")]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 5, 1: 6}

    def test_segment_map_breaks_a_source_filename_tie(self):
        db = [_title(0, 2600, "00001.m2ts", "1,2"), _title(1, 2600, "00001.m2ts", "3,4")]
        scan = [
            _scanned(9, 2600, "00001.m2ts", "3,4"),
            _scanned(8, 2600, "00001.m2ts", "1,2"),
        ]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 8, 1: 9}


class TestAmbiguous:
    def test_missing_title_is_ambiguous(self):
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 2610, "00002.m2ts")]
        scan = [_scanned(0, 2600, "00001.m2ts")]
        assert reconcile_titles(db, scan).outcome is ReconcileOutcome.AMBIGUOUS

    def test_indistinguishable_titles_are_ambiguous(self):
        db = [_title(0, 2600), _title(1, 2600)]
        scan = [_scanned(7, 2600), _scanned(8, 2600)]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.AMBIGUOUS
        assert result.reason

    def test_empty_scan_is_ambiguous(self):
        assert reconcile_titles([_title(0, 2600)], []).outcome is ReconcileOutcome.AMBIGUOUS
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_backup_reconciliation.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.services.backup_reconcile'`.

If `TitleInfo` does not carry `source_filename`, `segment_map`, `file_size_bytes` or `chapter_count` under those names, read its definition in `app/core/extractor.py` and adjust both the test helper and the implementation to the real field names before continuing.

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/backup_reconcile.py`:

```python
"""Re-validate title indices after extraction moves from the disc to its backup.

DiscTitle rows are created from the *disc* scan and carry ``title_index``, which
is what the rip command passes to MakeMKV. Extraction now runs against
``file:<backup>``, so a re-scan may enumerate differently. A backup is a byte
copy and almost always enumerates identically, but "almost always" is not a
contract, and ripping the wrong index files an episode under another episode's
name with no error anywhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

# MakeMKV rounds durations, so titles within this many seconds are the same title.
_DURATION_TOLERANCE_S = 2


class ReconcileOutcome(StrEnum):
    """How a backup re-scan lined up with the disc scan."""

    IDENTICAL = "identical"
    REMAPPED = "remapped"
    AMBIGUOUS = "ambiguous"


@dataclass
class ReconcileResult:
    outcome: ReconcileOutcome
    # {old title_index: new title_index}. Empty for IDENTICAL.
    remap: dict[int, int] = field(default_factory=dict)
    reason: str | None = None


def _fingerprint(source_filename: str | None, segment_map: str | None, duration: int) -> tuple:
    """Identity of a title independent of its enumeration position."""
    return (
        (source_filename or "").strip().lower(),
        (segment_map or "").strip(),
        duration // (_DURATION_TOLERANCE_S + 1),
    )


def reconcile_titles(db_titles, scanned_titles) -> ReconcileResult:
    """Line up stored DiscTitle rows against a fresh scan of the backup."""
    if not scanned_titles:
        return ReconcileResult(
            ReconcileOutcome.AMBIGUOUS, reason="the backup scan returned no titles"
        )

    if len(db_titles) == len(scanned_titles) and all(
        abs(db.duration_seconds - sc.duration_seconds) <= _DURATION_TOLERANCE_S
        and db.title_index == sc.index
        for db, sc in zip(db_titles, scanned_titles, strict=True)
    ):
        return ReconcileResult(ReconcileOutcome.IDENTICAL)

    buckets: dict[tuple, list] = {}
    for sc in scanned_titles:
        buckets.setdefault(
            _fingerprint(
                getattr(sc, "source_filename", None),
                getattr(sc, "segment_map", None),
                sc.duration_seconds,
            ),
            [],
        ).append(sc)

    remap: dict[int, int] = {}
    for db in db_titles:
        key = _fingerprint(db.source_filename, db.segment_map, db.duration_seconds)
        candidates = buckets.get(key, [])
        if len(candidates) != 1:
            return ReconcileResult(
                ReconcileOutcome.AMBIGUOUS,
                reason=(
                    f"title {db.title_index} matched {len(candidates)} titles in the "
                    f"backup scan; cannot tell which one to extract"
                ),
            )
        remap[db.title_index] = candidates[0].index

    logger.info(f"Backup re-scan remapped {len(remap)} title indices")
    return ReconcileResult(ReconcileOutcome.REMAPPED, remap=remap)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_backup_reconciliation.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/services/backup_reconcile.py tests/unit/test_backup_reconciliation.py && uv run ruff format app/services/backup_reconcile.py tests/unit/test_backup_reconciliation.py
git add backend/app/services/backup_reconcile.py backend/tests/unit/test_backup_reconciliation.py
git commit -m "feat(backup): reconcile title indices against a backup re-scan"
```

---

## Task 10: `JobManager._run_backup`

**Files:**
- Modify: `backend/app/services/job_manager.py`
- Test: `backend/tests/unit/test_run_backup.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_run_backup.py`:

```python
"""The backup phase, and its fallback matrix."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.core.extractor import BackupResult
from app.database import async_session
from app.models import ContentType, DiscJob, JobState
from app.services.job_manager import job_manager


async def _make_job(**kw) -> int:
    async with async_session() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="INCEPTION",
            content_type=ContentType.MOVIE,
            tmdb_name="Inception",
            tmdb_year=2010,
            state=JobState.BACKING_UP,
            **kw,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        return job.id


async def _reload(job_id: int) -> DiscJob:
    async with async_session() as s:
        return await s.get(DiscJob, job_id)


class TestFallbacks:
    @pytest.mark.asyncio
    async def test_unconfigured_root_skips_to_ripping(self, tmp_path):
        job_id = await _make_job()
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path="", backup_before_rip=True)
        )), patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)
        job = await _reload(job_id)
        assert job.backup_status == "skipped"
        assert job.backup_status_reason == "not_configured"
        assert job.state == JobState.RIPPING

    @pytest.mark.asyncio
    async def test_insufficient_space_skips_to_ripping(self, tmp_path):
        job_id = await _make_job()
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch("app.services.job_manager.has_room_for_backup", return_value=False), \
             patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)
        job = await _reload(job_id)
        assert job.backup_status == "skipped"
        assert job.backup_status_reason == "insufficient_space"
        assert job.state == JobState.RIPPING

    @pytest.mark.asyncio
    async def test_unresolvable_disc_index_skips_to_ripping(self, tmp_path):
        job_id = await _make_job()
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch("app.services.job_manager.has_room_for_backup", return_value=True), \
             patch("app.services.job_manager.resolve_disc_index", new=AsyncMock(return_value=None)), \
             patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)
        job = await _reload(job_id)
        assert job.backup_status == "skipped"
        assert job.backup_status_reason == "no_disc_index"
        assert job.state == JobState.RIPPING

    @pytest.mark.asyncio
    async def test_backup_failure_falls_back_and_records_the_reason(self, tmp_path):
        job_id = await _make_job()
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch("app.services.job_manager.has_room_for_backup", return_value=True), \
             patch("app.services.job_manager.resolve_disc_index", new=AsyncMock(return_value="disc:0")), \
             patch.object(job_manager.extractor, "backup_disc", new=AsyncMock(
                 return_value=BackupResult(success=False, error_message="read error")
             )), patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)
        job = await _reload(job_id)
        assert job.backup_status == "failed"
        assert "read error" in job.backup_status_reason
        assert job.state == JobState.RIPPING
        # The drive was never released: extraction still needs the disc.
        assert job.source_spec is None


class TestSuccess:
    @pytest.mark.asyncio
    async def test_success_repoints_the_source_and_releases_the_drive(self, tmp_path):
        job_id = await _make_job()
        dest = tmp_path / "Movies" / "Inception (2010)"
        release = AsyncMock(return_value=True)
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch("app.services.job_manager.has_room_for_backup", return_value=True), \
             patch("app.services.job_manager.resolve_disc_index", new=AsyncMock(return_value="disc:0")), \
             patch.object(job_manager.extractor, "backup_disc", new=AsyncMock(
                 return_value=BackupResult(success=True, dest=dest)
             )), patch.object(job_manager, "_release_drive", new=release), \
             patch.object(job_manager, "_reconcile_backup_titles", new=AsyncMock(return_value=True)), \
             patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)

        job = await _reload(job_id)
        assert job.backup_status == "completed"
        assert job.backup_path == str(dest)
        assert job.source_spec == f"file:{dest}"
        assert job.state == JobState.RIPPING
        release.assert_awaited_once()
        assert release.await_args.kwargs.get("outcome") == "Backed up" or \
               release.await_args.args[2] == "Backed up"

    @pytest.mark.asyncio
    async def test_existing_destination_is_reused_without_recopying(self, tmp_path):
        # Re-inserting a disc backed up last month must not cost another 40 GB.
        dest = tmp_path / "Movies" / "Inception (2010)"
        (dest / "BDMV").mkdir(parents=True)
        job_id = await _make_job()
        backup = AsyncMock()
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch.object(job_manager.extractor, "backup_disc", new=backup), \
             patch.object(job_manager, "_release_drive", new=AsyncMock()), \
             patch.object(job_manager, "_reconcile_backup_titles", new=AsyncMock(return_value=True)), \
             patch.object(job_manager, "_run_ripping", new=AsyncMock()):
            await job_manager._run_backup(job_id)

        backup.assert_not_awaited()
        job = await _reload(job_id)
        assert job.backup_status == "completed"
        assert job.source_spec == f"file:{dest}"

    @pytest.mark.asyncio
    async def test_unreconcilable_backup_parks_for_review(self, tmp_path):
        job_id = await _make_job()
        dest = tmp_path / "Movies" / "Inception (2010)"
        with patch("app.services.config_service.get_config", new=AsyncMock(
            return_value=_config(backup_path=str(tmp_path), backup_before_rip=True)
        )), patch("app.services.job_manager.has_room_for_backup", return_value=True), \
             patch("app.services.job_manager.resolve_disc_index", new=AsyncMock(return_value="disc:0")), \
             patch.object(job_manager.extractor, "backup_disc", new=AsyncMock(
                 return_value=BackupResult(success=True, dest=dest)
             )), patch.object(job_manager, "_release_drive", new=AsyncMock()), \
             patch.object(job_manager, "_reconcile_backup_titles", new=AsyncMock(return_value=False)), \
             patch.object(job_manager, "_run_ripping", new=AsyncMock()) as rip:
            await job_manager._run_backup(job_id)

        job = await _reload(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        rip.assert_not_awaited()


def _config(**kw):
    from app.models import AppConfig

    return AppConfig(**kw)
```

Add whatever `setup_db` / clean-database fixture the neighbouring unit tests use (copy the autouse fixture from `tests/unit/test_api_routes.py`) so these rows do not leak between tests. Keep every job non-terminal, so no terminal Discord task leaks a pooled connection.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_run_backup.py -q
```

Expected: `AttributeError: 'JobManager' object has no attribute '_run_backup'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/services/job_manager.py`, add these module-level imports near the other `app.core` imports:

```python
from app.core.backup_paths import backup_destination, has_room_for_backup
from app.core.disc_source import DiscSource, resolve_disc_index
```

Add the method next to `_run_ripping`:

```python
    async def _run_backup(self, job_id: int) -> None:
        """Copy the whole disc to backup_path, then hand off to extraction.

        Every problem here degrades to today's behaviour (a direct rip from the
        drive) rather than failing the job: turning the setting on must never
        make a disc less likely to finish. The single exception is a backup that
        succeeded but whose re-scan cannot be reconciled, which parks for review
        instead of falling back, because by then the disc may already be ejected.
        """
        from app.services.config_service import get_config

        safe_job = sanitize_log_value(job_id)

        async def _record(
            status: str,
            *,
            reason: str | None = None,
            path: str | None = None,
            spec: str | None = None,
        ):
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job:
                    return
                job.backup_status = status
                job.backup_status_reason = reason
                if path is not None:
                    job.backup_path = path
                if spec is not None:
                    job.source_spec = spec
                job.updated_at = datetime.now(UTC)
                await session.commit()

        async def _fall_back(status: str, reason: str) -> None:
            logger.warning(
                f"Job {safe_job}: backup {status} ({reason}); ripping directly from the drive"
            )
            await _record(status, reason=reason)
            await self._enter_ripping(job_id)

        try:
            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                if not job:
                    return
                drive_id = job.drive_id
                total_bytes = sum(
                    t.file_size_bytes
                    for t in (
                        await session.execute(
                            select(DiscTitle).where(DiscTitle.job_id == job_id)
                        )
                    ).scalars()
                )

            config = await get_config()
            dest = backup_destination(job, config)
            if dest is None:
                await _fall_back("skipped", "not_configured")
                return

            # Already backed up (a re-inserted disc, or a retried job). Reuse it
            # rather than spending another 40 GB and 25 minutes.
            if dest.exists() and any(dest.iterdir()):
                logger.info(f"Job {safe_job}: reusing existing backup at {dest}")
                await _record("completed", path=str(dest), spec=f"file:{dest}")
                await self._release_drive(job_id, drive_id, "Backed up")
                if await self._reconcile_backup_titles(job_id, dest):
                    await self._enter_ripping(job_id)
                return

            if not has_room_for_backup(dest, total_bytes):
                await _fall_back("skipped", "insufficient_space")
                return

            disc_spec = await resolve_disc_index(drive_id, str(self.extractor.makemkv_path))
            if disc_spec is None:
                await _fall_back("skipped", "no_disc_index")
                return

            await _record("pending", path=str(dest))

            last_pct = -1.0

            def on_progress(pct: float) -> None:
                nonlocal last_pct
                # One broadcast per whole percent: a 40 GB copy emits PRGV
                # constantly and every message fans out to every client.
                if pct - last_pct < 1.0:
                    return
                last_pct = pct
                asyncio.create_task(
                    self._broadcaster.broadcast_backup_progress(
                        job_id,
                        current_bytes=int(total_bytes * pct / 100),
                        total_bytes=total_bytes,
                    )
                )

            result = await self.extractor.backup_disc(
                DiscSource.parse(disc_spec),
                dest,
                progress_callback=on_progress,
                log_dir=Path(job.staging_path) if job.staging_path else None,
                job_id=job_id,
            )

            if not result.success:
                await _fall_back("failed", result.error_message or "unknown error")
                return

            await _record("completed", path=str(result.dest), spec=f"file:{result.dest}")
            # The disc is copied and out of our way. This is the RIPPED_EVENT
            # milestone: hardware done, not media produced.
            await self._release_drive(job_id, drive_id, "Backed up")

            if not await self._reconcile_backup_titles(job_id, result.dest):
                return

            await self._enter_ripping(job_id)

        except Exception as e:
            logger.error(f"Job {safe_job}: backup phase crashed: {e}", exc_info=True)
            await _fall_back("failed", str(e))

    async def _enter_ripping(self, job_id: int) -> None:
        """Transition into RIPPING and start the rip task."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                return
            job.state = JobState.RIPPING
            job.updated_at = datetime.now(UTC)
            await session.commit()
            await ws_manager.broadcast_job_update(job_id, job.state.value)
        task = asyncio.create_task(with_job_log_context(job_id, self._run_ripping(job_id)))
        task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
        self._active_jobs[job_id] = task

    async def _reconcile_backup_titles(self, job_id: int, dest: Path) -> bool:
        """Re-scan the backup and re-map title indices. False means parked.

        Extraction from the backup uses stored title indices, so they must be
        re-validated against the copy rather than assumed. A backup that cannot
        be reconciled parks for review: falling back to the drive is not an
        option, because the disc has already been released.
        """
        from app.services.backup_reconcile import ReconcileOutcome, reconcile_titles

        source = DiscSource.for_backup(dest)
        try:
            scanned, _name = await self.extractor.scan_disc(source, job_id=job_id)
        except Exception as e:
            logger.error(f"Job {job_id}: could not scan the backup: {e}", exc_info=True)
            scanned = []

        async with async_session() as session:
            db_titles = list(
                (
                    await session.execute(
                        select(DiscTitle)
                        .where(DiscTitle.job_id == job_id)
                        .order_by(DiscTitle.title_index)
                    )
                )
                .scalars()
                .all()
            )
            result = reconcile_titles(db_titles, scanned)

            if result.outcome is ReconcileOutcome.AMBIGUOUS:
                job = await session.get(DiscJob, job_id)
                reason = (
                    f"The disc backup does not line up with the disc scan "
                    f"({result.reason}). The backup is safe at {dest}; "
                    f"re-run matching or re-import it to continue."
                )
                await self._state_machine.transition_to_review(job, session, reason=reason)
                return False

            for title in db_titles:
                new_index = result.remap.get(title.title_index)
                if new_index is not None and new_index != title.title_index:
                    title.title_index = new_index
                    session.add(title)
            await session.commit()
        return True
```

If `transition_to_review` has a different signature on `JobStateMachine`, match the call to the one `identification_coordinator.py` already uses.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_run_backup.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/services/job_manager.py tests/unit/test_run_backup.py && uv run ruff format app/services/job_manager.py tests/unit/test_run_backup.py
git add backend/app/services/job_manager.py backend/tests/unit/test_run_backup.py
git commit -m "feat(backup): add the BACKING_UP phase with a full fallback matrix"
```

---

## Task 11: Route identification into `BACKING_UP`

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:652-654`, `:697-699`, `:781-783`
- Modify: `backend/app/services/job_manager.py:1152-1168` (`start_ripping`)
- Test: `backend/tests/unit/test_backup_routing.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_backup_routing.py`:

```python
"""Identification hands a disc to BACKING_UP only when it should."""

import pytest

from app.models import AppConfig, JobState
from app.services.identification_coordinator import next_state_after_identify


class TestNextStateAfterIdentify:
    def test_backup_disabled_goes_straight_to_ripping(self):
        cfg = AppConfig(backup_before_rip=False, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.RIPPING

    def test_backup_enabled_goes_to_backing_up(self):
        cfg = AppConfig(backup_before_rip=True, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.BACKING_UP

    def test_backup_enabled_without_a_root_still_rips(self):
        # No root means nothing to write to; do not enter a phase that can only
        # immediately fall back.
        cfg = AppConfig(backup_before_rip=True, backup_path="")
        assert next_state_after_identify(cfg, drive_id="E:") is JobState.RIPPING

    def test_an_import_job_never_backs_up(self):
        # It already is a backup.
        cfg = AppConfig(backup_before_rip=True, backup_path="/b")
        assert next_state_after_identify(cfg, drive_id="import") is JobState.RIPPING

    def test_a_missing_config_rips(self):
        assert next_state_after_identify(None, drive_id="E:") is JobState.RIPPING
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_backup_routing.py -q
```

Expected: `ImportError: cannot import name 'next_state_after_identify'`.

- [ ] **Step 3: Write the implementation**

Add to `backend/app/services/identification_coordinator.py`, at module level:

```python
def next_state_after_identify(config, drive_id: str) -> JobState:
    """The state a freshly identified disc should enter.

    One function rather than a condition repeated at each of the three places
    identification currently assigns JobState.RIPPING, so the three cannot
    drift. An import job is excluded because it already is a backup, and an
    enabled-but-unconfigured backup is excluded because entering a phase whose
    only possible action is to fall back would put a misleading BACKING UP on
    the card for no reason.
    """
    if not config or not config.backup_before_rip or not config.backup_path:
        return JobState.RIPPING
    if drive_id == "import":
        return JobState.RIPPING
    return JobState.BACKING_UP
```

Then at each of the three sites that currently set `job.state = JobState.RIPPING` (lines 653, 698 and 781), replace with:

```python
                        job.state = next_state_after_identify(config, job.drive_id)
```

using whatever config object is already in scope at that point (each of those blocks already loads config; if one does not, call `await get_config()` there). After the commit and broadcast at each site, the coordinator currently spawns `_run_ripping`. Change that spawn to branch:

```python
                        runner = (
                            self._job_manager._run_backup
                            if job.state == JobState.BACKING_UP
                            else self._job_manager._run_ripping
                        )
                        task = asyncio.create_task(with_job_log_context(job_id, runner(job_id)))
```

Match the existing task-registration lines around each site rather than inventing new ones.

In `job_manager.start_ripping`, honour the same routing so a review resume backs up too:

```python
    async def start_ripping(self, job_id: int) -> None:
        """Start ripping, backing the disc up first when configured."""
        from app.services.config_service import get_config

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.state not in (JobState.IDLE, JobState.REVIEW_NEEDED):
                raise ValueError(f"Cannot start job in state: {job.state}")

            from app.services.identification_coordinator import next_state_after_identify

            config = await get_config()
            job.state = next_state_after_identify(config, job.drive_id)
            job.updated_at = datetime.now(UTC)
            await session.commit()
            runner = self._run_backup if job.state == JobState.BACKING_UP else self._run_ripping

            task = asyncio.create_task(with_job_log_context(job_id, runner(job_id)))
            task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
            self._active_jobs[job_id] = task
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_backup_routing.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the pipeline tests to verify the default path is unchanged**

```bash
cd backend && uv run pytest tests/pipeline -q
```

Expected: all pass, since `backup_before_rip` defaults to False. If a pipeline test fails with `no such table`, run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first: a fresh worktree DB is often a zero-byte stub.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check app/services && uv run ruff format app/services
git add backend/app/services backend/tests/unit/test_backup_routing.py
git commit -m "feat(backup): route identified discs into BACKING_UP when enabled"
```

---

## Task 12: Config API surface

**Files:**
- Modify: `backend/app/api/routes.py` (`ConfigUpdate`, `ConfigResponse`)
- Test: `backend/tests/unit/test_api_routes.py` (existing file, add cases)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_api_routes.py`:

```python
class TestBackupConfigRoundTrip:
    """A new AppConfig field must also exist in ConfigUpdate and ConfigResponse.

    Pydantic drops unknown keys silently, so a field missing from either schema
    is accepted by the API, never stored, and never reported: the setting simply
    does nothing.
    """

    @pytest.mark.asyncio
    async def test_backup_settings_round_trip(self, client, tmp_path):
        resp = await client.put(
            "/api/config",
            json={"backup_before_rip": True, "backup_path": str(tmp_path)},
        )
        assert resp.status_code == 200

        got = await client.get("/api/config")
        assert got.status_code == 200
        body = got.json()
        assert body["backup_before_rip"] is True
        assert body["backup_path"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_backup_defaults_are_reported(self, client):
        body = (await client.get("/api/config")).json()
        assert "backup_before_rip" in body
        assert "backup_path" in body
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_api_routes.py -q -k BackupConfig
```

Expected: `KeyError: 'backup_before_rip'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/api/routes.py`, add to `ConfigUpdate`:

```python
    backup_before_rip: bool | None = None
    backup_path: str | None = None
    timeout_backing_up_seconds: int | None = None
```

Add to `ConfigResponse`, matching the style of the neighbouring fields:

```python
    backup_before_rip: bool = False
    backup_path: str = ""
    timeout_backing_up_seconds: int = 7200
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_api_routes.py -q -k BackupConfig
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py && uv run ruff format app/api/routes.py
git add backend/app/api/routes.py backend/tests/unit/test_api_routes.py
git commit -m "feat(backup): expose backup settings through the config API"
```

---

## Task 13: Disc-image detection in the import scanner

**Files:**
- Modify: `backend/app/core/import_scanner.py`
- Test: `backend/tests/unit/test_import_scanner_disc_images.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_import_scanner_disc_images.py`:

```python
"""A backup folder or ISO is an import unit, not a folder to walk for MKVs."""

from app.core import import_scanner


def _bdmv(root, name):
    d = root / name
    (d / "BDMV" / "STREAM").mkdir(parents=True)
    (d / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"x" * 10)
    return d


def _video_ts(root, name):
    d = root / name
    (d / "VIDEO_TS").mkdir(parents=True)
    (d / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"x" * 10)
    return d


class TestDetection:
    def test_bdmv_folder_is_a_disc_image(self, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["Inception (2010)"]
        assert scan.disc_images[0].kind == "backup"

    def test_video_ts_folder_is_a_disc_image(self, tmp_path):
        _video_ts(tmp_path, "The Sweetest Thing")
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["The Sweetest Thing"]

    def test_iso_file_is_a_disc_image(self, tmp_path):
        (tmp_path / "Inception.iso").write_bytes(b"x" * 10)
        scan = import_scanner.scan(tmp_path)
        assert [d.name for d in scan.disc_images] == ["Inception.iso"]
        assert scan.disc_images[0].kind == "iso"

    def test_picking_the_backup_folder_itself_yields_one_image(self, tmp_path):
        d = _bdmv(tmp_path, "Inception (2010)")
        scan = import_scanner.scan(d)
        assert len(scan.disc_images) == 1

    def test_nested_backups_are_all_found(self, tmp_path):
        _bdmv(tmp_path / "TV" / "Frasier (1993)" / "Season 01", "S01D01")
        _bdmv(tmp_path / "TV" / "Frasier (1993)" / "Season 01", "S01D02")
        scan = import_scanner.scan(tmp_path)
        assert sorted(d.name for d in scan.disc_images) == ["S01D01", "S01D02"]

    def test_a_tree_can_hold_both_kinds(self, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        loose = tmp_path / "Frasier" / "Season 01"
        loose.mkdir(parents=True)
        (loose / "Frasier - S01E01.mkv").write_bytes(b"x" * 10)
        scan = import_scanner.scan(tmp_path)
        assert len(scan.disc_images) == 1
        assert len(scan.units) == 1

    def test_m2ts_files_do_not_count_toward_the_file_budget(self, tmp_path):
        # The walk stops at the disc image, so a backup's thousands of stream
        # files cannot truncate a scan of the folder above it.
        d = _bdmv(tmp_path, "Big")
        for i in range(50):
            (d / "BDMV" / "STREAM" / f"{i:05d}.m2ts").write_bytes(b"x")
        scan = import_scanner.scan(tmp_path)
        assert scan.truncated is False
        assert scan.total_files == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_import_scanner_disc_images.py -q
```

Expected: `AttributeError: 'ImportScan' object has no attribute 'disc_images'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/import_scanner.py`, add:

```python
# A directory holding one of these is a disc backup, not a folder of media.
_DISC_IMAGE_MARKERS = ("BDMV", "VIDEO_TS")


@dataclass
class DiscImageUnit:
    """A disc backup folder or ISO file, to be scanned and extracted by MakeMKV."""

    path: Path
    name: str
    kind: str  # "backup" | "iso"
    total_bytes: int


def is_disc_image_dir(path: Path) -> bool:
    """Whether this directory is a MakeMKV disc backup."""
    try:
        return any((path / marker).is_dir() for marker in _DISC_IMAGE_MARKERS)
    except OSError:
        return False


def _dir_size(path: Path) -> int:
    """Total bytes under a directory, best effort."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total
```

Add `disc_images: list[DiscImageUnit] = field(default_factory=list)` to `ImportScan` (import `field` from `dataclasses`).

In the walk, before recursing into a directory, check `is_disc_image_dir(d)`. If true, append a `DiscImageUnit` and do **not** descend: that short-circuit is what keeps a backup's stream files out of the `_MAX_FILES` budget. Treat any file whose suffix is `.iso` (case-insensitive) as a `DiscImageUnit` with `kind="iso"`. If the picked root is itself a disc image, return a scan holding exactly that one unit and no MKV units.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_import_scanner_disc_images.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the existing scanner tests to verify nothing regressed**

```bash
cd backend && uv run pytest tests/unit -q -k import_scanner
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check app/core/import_scanner.py tests/unit/test_import_scanner_disc_images.py && uv run ruff format app/core/import_scanner.py tests/unit/test_import_scanner_disc_images.py
git add backend/app/core/import_scanner.py backend/tests/unit/test_import_scanner_disc_images.py
git commit -m "feat(backup): detect disc backups and ISOs in the import scanner"
```

---

## Task 14: Import a backup through the API

**Files:**
- Modify: `backend/app/api/routes.py:3000-3054` (`import_browse`), `:3057-3088` (`import_preview`), `:3092-3182` (`import_start`)
- Modify: `backend/app/services/job_manager.py` (`create_job_from_staging`)
- Test: `backend/tests/integration/test_import_backup.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_import_backup.py`:

```python
"""Importing an existing disc backup runs the full pipeline, not a file move."""

import pytest

from app.database import async_session
from app.models import DiscJob, JobState


def _bdmv(root, name):
    d = root / name
    (d / "BDMV" / "STREAM").mkdir(parents=True)
    (d / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"x" * 10)
    return d


class TestBrowse:
    @pytest.mark.asyncio
    async def test_a_backup_folder_is_labelled_a_disc_image(self, client, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        resp = await client.get("/api/import/browse", params={"path": str(tmp_path)})
        assert resp.status_code == 200
        kinds = {e["name"]: e["type"] for e in resp.json()["entries"]}
        assert kinds["Inception (2010)"] == "disc_image"

    @pytest.mark.asyncio
    async def test_an_iso_is_listed(self, client, tmp_path):
        (tmp_path / "Inception.iso").write_bytes(b"x")
        resp = await client.get("/api/import/browse", params={"path": str(tmp_path)})
        kinds = {e["name"]: e["type"] for e in resp.json()["entries"]}
        assert kinds["Inception.iso"] == "iso"


class TestPreview:
    @pytest.mark.asyncio
    async def test_preview_reports_disc_images(self, client, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        resp = await client.post("/api/import/preview", json={"path": str(tmp_path)})
        body = resp.json()
        assert body["disc_images"] == [
            {"name": "Inception (2010)", "path": str(tmp_path / "Inception (2010)"),
             "kind": "backup", "total_bytes": 10}
        ]
        assert body["total_jobs"] == 1


class TestStart:
    @pytest.mark.asyncio
    async def test_start_creates_an_identifying_job_pointed_at_the_backup(self, client, tmp_path):
        d = _bdmv(tmp_path, "Inception (2010)")
        resp = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert resp.status_code == 200
        job_ids = resp.json()["job_ids"]
        assert len(job_ids) == 1

        async with async_session() as s:
            job = await s.get(DiscJob, job_ids[0])
        assert job.source_spec == f"file:{d}"
        assert job.drive_id == "import"
        # It already is a backup: it must never enter BACKING_UP.
        assert job.state != JobState.BACKING_UP

    @pytest.mark.asyncio
    async def test_an_iso_job_carries_an_iso_spec(self, client, tmp_path):
        iso = tmp_path / "Inception.iso"
        iso.write_bytes(b"x")
        resp = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        async with async_session() as s:
            job = await s.get(DiscJob, resp.json()["job_ids"][0])
        assert job.source_spec == f"iso:{iso}"

    @pytest.mark.asyncio
    async def test_a_second_import_of_a_live_backup_is_blocked(self, client, tmp_path):
        _bdmv(tmp_path, "Inception (2010)")
        first = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert first.json()["job_ids"]
        second = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert second.json()["job_ids"] == []
        assert second.json()["blocked"][0]["reason"] == "in_flight"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/integration/test_import_backup.py -q
```

Expected: `KeyError: 'disc_image'` or a `disc_images` KeyError.

- [ ] **Step 3: Write the implementation**

In `import_browse`, inside the `entry.is_dir(...)` branch, check the disc-image marker before counting MKVs:

```python
                if entry.is_dir(follow_symlinks=False):
                    if import_scanner.is_disc_image_dir(Path(entry.path)):
                        entries.append(
                            {"name": entry.name, "path": entry.path, "type": "disc_image"}
                        )
                        continue
```

and add an ISO arm next to the `.mkv` file arm:

```python
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".iso"):
                    entries.append({"name": entry.name, "path": entry.path, "type": "iso"})
```

Import `import_scanner` at the top of `import_browse` the same way `import_preview` does.

In `import_preview`, add to the returned dict:

```python
        "disc_images": [
            {"name": d.name, "path": str(d.path), "kind": d.kind, "total_bytes": d.total_bytes}
            for d in scan.disc_images
        ],
```

and change `"total_jobs": len(scan.units)` to `"total_jobs": len(scan.units) + len(scan.disc_images)`.

In `import_start`, relax the empty guard and add a disc-image loop before the existing unit loop:

```python
    if not scan.units and not scan.disc_images:
        raise HTTPException(status_code=400, detail="No MKV files or disc backups found to import")

    for image in scan.disc_images:
        staging = str(image.path)
        key = unit_key_for(staging)
        seen_keys.add(key)
        result = await job_manager.create_job_from_staging(
            staging_path=staging,
            content_type="unknown",
            detected_title=image.name,
            destination_mode=req.destination_mode,
            drive_id="import",
            # The job reads from the image with MakeMKV rather than ingesting
            # existing MKVs, so it carries a source spec and no file manifest.
            source_spec=f"iso:{image.path}" if image.kind == "iso" else f"file:{image.path}",
            force=key in force_keys,
        )
        if result.job_id is not None:
            job_ids.append(result.job_id)
        else:
            blocked.append(
                BlockedImportUnit(
                    unit_key=key,
                    show_name=image.name,
                    season=None,
                    display_path=staging,
                    reason=result.block,
                    job_ids=list(result.blocking_job_ids),
                )
            )
```

In `job_manager.create_job_from_staging`, add a `source_spec: str | None = None` keyword and persist it on the new `DiscJob`. When `source_spec` is set, the job must enter `IDENTIFYING` and run the normal scan-and-rip pipeline rather than the existing "files already exist" shortcut that jumps to `MATCHING`. Follow the branch that `drive_id == "import"` already takes and add the source-spec case beside it.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/integration/test_import_backup.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the existing import tests to verify nothing regressed**

```bash
cd backend && uv run pytest tests -q -k "import"
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py app/services/job_manager.py && uv run ruff format app/api/routes.py app/services/job_manager.py
git add backend/app/api/routes.py backend/app/services/job_manager.py backend/tests/integration/test_import_backup.py
git commit -m "feat(backup): import an existing disc backup or ISO through the import modal"
```

---

## Task 15: Diagnostics and job detail

**Files:**
- Modify: `backend/app/api/routes.py` (`build_job_detail`)
- Test: `backend/tests/unit/test_diagnostics.py` (existing file, add case)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_diagnostics.py`:

```python
class TestBackupFieldsInJobDetail:
    @pytest.mark.asyncio
    async def test_backup_fields_are_reported(self, client, tmp_path):
        from app.database import async_session
        from app.models import DiscJob, JobState

        async with async_session() as s:
            job = DiscJob(
                drive_id="E:",
                state=JobState.COMPLETED,
                backup_path=str(tmp_path / "Inception (2010)"),
                backup_status="completed",
                source_spec=f"file:{tmp_path / 'Inception (2010)'}",
            )
            s.add(job)
            await s.commit()
            await s.refresh(job)
            job_id = job.id

        body = (await client.get(f"/api/jobs/{job_id}/detail")).json()
        assert body["backup_status"] == "completed"
        assert body["backup_path"].endswith("Inception (2010)")
        assert body["source_spec"].startswith("file:")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_diagnostics.py -q -k BackupFields
```

Expected: `KeyError: 'backup_status'`.

- [ ] **Step 3: Write the implementation**

In `build_job_detail`, add `backup_status`, `backup_status_reason`, `backup_path` and `source_spec` to the job dict alongside the other `DiscJob` scalars. The bundle's markdown summary and log collection need no change: `_run_backup` logs inside `with_job_log_context`, so its lines already carry the `job=<id>` tag the bundle greps, and `backup_disc` writes `backup_job<id>.log` into the same log dir the scan and rip logs use.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_diagnostics.py -q -k BackupFields
```

Expected: passes.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/api/routes.py && uv run ruff format app/api/routes.py
git add backend/app/api/routes.py backend/tests/unit/test_diagnostics.py
git commit -m "feat(backup): report backup fields in job detail and diagnostics"
```

---

## Task 16: Simulation support

**Files:**
- Modify: `backend/app/services/simulation_service.py`
- Modify: `backend/app/api/routes.py` (the insert-disc request model)
- Test: `backend/tests/integration/test_simulation.py` (existing file, add case)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_simulation.py`:

```python
class TestSimulatedBackup:
    @pytest.mark.asyncio
    async def test_simulated_disc_passes_through_backing_up(self, client):
        resp = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "INCEPTION_2010",
                "content_type": "movie",
                "simulate_backup": True,
                "simulate_ripping": False,
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # The simulated backup does not auto-advance, so the job rests here.
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        assert job["state"] == "backing_up"
        assert job["backup_status"] == "pending"

    @pytest.mark.asyncio
    async def test_advancing_a_simulated_backup_reaches_ripping(self, client):
        resp = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "INCEPTION_2010",
                "content_type": "movie",
                "simulate_backup": True,
                "simulate_ripping": False,
            },
        )
        job_id = resp.json()["job_id"]
        await client.post(f"/api/simulate/advance-job/{job_id}")
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        assert job["state"] == "ripping"
        assert job["backup_status"] == "completed"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/integration/test_simulation.py -q -k SimulatedBackup
```

Expected: the job reports `ripping` or `identifying`, not `backing_up`.

- [ ] **Step 3: Write the implementation**

Add `simulate_backup: bool = False` to the simulate-insert-disc request model in `routes.py`. In `SimulationService`, when `simulate_backup` is set, park the created job in `JobState.BACKING_UP` with `backup_status="pending"` and a synthetic `backup_path`, broadcast a few `backup_progress` messages, and leave it there. In the service's advance handler, add a `BACKING_UP` arm that sets `backup_status="completed"`, sets `source_spec` to the synthetic backup path, and transitions to `RIPPING`. Keep every one of these code paths inside `SimulationService`, as the other simulation methods are, and behind the existing DEBUG gate.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/integration/test_simulation.py -q -k SimulatedBackup
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check app/services/simulation_service.py app/api/routes.py && uv run ruff format app/services/simulation_service.py app/api/routes.py
git add backend/app/services/simulation_service.py backend/app/api/routes.py backend/tests/integration/test_simulation.py
git commit -m "feat(backup): simulate the BACKING_UP phase for E2E without 40GB"
```

---

## Task 17: Frontend state, replacing the orphaned `archiving_iso`

`archiving_iso` and `isoProgress` exist only in the frontend and only mock data feeds them. Repurpose rather than adding an eleventh state.

**Files:**
- Modify: `frontend/src/app/components/DiscCard.tsx:18`, `:72`, `:676-695`
- Modify: `frontend/src/app/components/discState.ts:31`, `:56`, `:52`
- Modify: `frontend/src/types/adapters.ts:11-23`
- Modify: `frontend/src/app/utils/mockData.ts`
- Modify: `frontend/src/app/components/DiscCard.test.tsx:369-376`, `frontend/src/app/components/discState.test.ts:8`
- Test: `frontend/src/types/__tests__/adapters.test.ts` (add cases; create if absent)

- [ ] **Step 1: Write the failing test**

Add to the adapters test file:

```tsx
import { mapJobStateToDiscState } from '../adapters';

describe('mapJobStateToDiscState', () => {
  it('maps backing_up to the backing_up disc state', () => {
    expect(mapJobStateToDiscState('backing_up')).toBe('backing_up');
  });

  it('still maps ripping', () => {
    expect(mapJobStateToDiscState('ripping')).toBe('ripping');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm run test:unit -- adapters
```

Expected: TypeScript error, `'backing_up' is not assignable to Job['state']`, or the assertion returns `'idle'`.

If `node_modules` is missing, run `npm install` first, then `git checkout package-lock.json` before committing: the install rewrites the lock with a large unrelated diff.

- [ ] **Step 3: Write the implementation**

In `frontend/src/app/components/DiscCard.tsx:18`, rename the state and the progress field:

```tsx
export type DiscState = "idle" | "scanning" | "review_needed" | "backing_up" | "ripping" | "matching" | "organizing" | "processing" | "completed" | "error";
```

```tsx
  /** Percentage of the full-disc backup copied, when state is "backing_up". */
  backupProgress?: number;
```

Replace the ISO block at `:676`:

```tsx
              {/* Full-disc backup */}
              {disc.state === "backing_up" && disc.backupProgress !== undefined && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      fontFamily: sv.mono,
                      fontSize: 12,
                      color: sv.magenta,
                      letterSpacing: "0.2em",
                      textTransform: "uppercase",
                    }}
                  >
                    <Database size={14} />
                    <span>› BACKING UP DISC…</span>
                  </div>
                  <SvProgressBar progress={disc.backupProgress} color="magenta" label="DISC BACKUP" />
                </div>
              )}
```

In `discState.ts:31`, rename the key and label:

```tsx
  backing_up:     { label: "BACKING UP",    badgeState: "matching", color: sv.purple,   glow: sv.purple,   icon: IcoLibrary },
```

Rename it in `ACTIVE_PIPELINE_STATES` too, and update the two comments at `:52` and the `discState.test.ts:8` reference from `archiving_iso` to `backing_up`.

In `adapters.ts`, add the mapping:

```tsx
    'identifying': 'scanning',
    'backing_up': 'backing_up',
```

and add `'backing_up'` to the `Job['state']` union in `frontend/src/types/` wherever the backend states are declared.

In `mockData.ts`, rename `isoProgress` to `backupProgress` at all four call sites, and any `state: 'archiving_iso'` to `'backing_up'`. Do the same in `DiscCard.test.tsx:369-376`, keeping the comment's meaning (this state is what distinguishes `ACTIVE_PIPELINE_STATES` from the settled ones).

- [ ] **Step 4: Run the tests and the type check**

```bash
cd frontend && npm run test:unit && npm run build
```

Expected: tests pass and the build type-checks clean. `npm run build` runs `tsc`, which is what catches a missed rename.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src
git commit -m "feat(backup): render the BACKING UP phase, replacing the orphaned ISO state"
```

---

## Task 18: Wire `backup_progress` into the dashboard

**Files:**
- Modify: `frontend/src/app/hooks/useJobManagement.ts`
- Test: `frontend/src/app/hooks/__tests__/useJobManagement.test.ts` (add case; create if absent)

- [ ] **Step 1: Write the failing test**

```tsx
it('applies backup_progress to the matching job', () => {
  // The message carries bytes, not a percentage: the card renders the bar,
  // so the conversion belongs here rather than on the wire.
  const jobs = [{ id: 7, state: 'backing_up', progress_percent: 0 }] as never;
  const next = applyBackupProgress(jobs, {
    job_id: 7, current_bytes: 25, total_bytes: 100, speed: '2.5x', eta: 90,
  });
  expect(next[0].progress_percent).toBe(25);
  expect(next[0].current_speed).toBe('2.5x');
  expect(next[0].eta_seconds).toBe(90);
});

it('ignores backup_progress for an unknown job', () => {
  const jobs = [{ id: 7, state: 'backing_up', progress_percent: 0 }] as never;
  expect(applyBackupProgress(jobs, {
    job_id: 99, current_bytes: 1, total_bytes: 2, speed: null, eta: null,
  })).toBe(jobs);
});

it('treats a zero total as no progress rather than dividing by zero', () => {
  const jobs = [{ id: 7, state: 'backing_up', progress_percent: 0 }] as never;
  const next = applyBackupProgress(jobs, {
    job_id: 7, current_bytes: 0, total_bytes: 0, speed: null, eta: null,
  });
  expect(next[0].progress_percent).toBe(0);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm run test:unit -- useJobManagement
```

Expected: `applyBackupProgress is not defined`.

- [ ] **Step 3: Write the implementation**

Export the pure reducer from `useJobManagement.ts`:

```tsx
/** Fold a backup_progress message into the job list. Returns the same array
 *  reference when nothing matched, so React skips the re-render. */
export function applyBackupProgress(jobs: Job[], data: BackupProgressMessage): Job[] {
  const idx = jobs.findIndex(j => j.id === data.job_id);
  if (idx === -1) return jobs;
  const pct = data.total_bytes > 0
    ? Math.min(100, Math.round((data.current_bytes / data.total_bytes) * 100))
    : 0;
  const next = [...jobs];
  next[idx] = {
    ...next[idx],
    progress_percent: pct,
    current_speed: data.speed ?? next[idx].current_speed,
    eta_seconds: data.eta ?? next[idx].eta_seconds,
  };
  return next;
}
```

Then add a `case 'backup_progress':` arm to the WebSocket message switch that calls it, and pass `backupProgress: job.progress_percent` through `transformJobToDiscData` when the job state is `backing_up`.

- [ ] **Step 4: Run the tests and build**

```bash
cd frontend && npm run test:unit && npm run build
```

Expected: pass and clean.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src
git commit -m "feat(backup): drive the backup progress bar from backup_progress events"
```

---

## Task 19: Config wizard controls

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`
- Test: `frontend/src/components/ConfigWizard.test.tsx` (add cases)

- [ ] **Step 1: Write the failing test**

```tsx
it('shows the backup path field when backup before rip is enabled', async () => {
  render(<ConfigWizard {...defaultProps} />);
  const toggle = await screen.findByLabelText(/back up disc before ripping/i);
  expect(screen.queryByLabelText(/backup folder/i)).not.toBeInTheDocument();
  await userEvent.click(toggle);
  expect(await screen.findByLabelText(/backup folder/i)).toBeInTheDocument();
});

it('sends both backup fields on save', async () => {
  const onSave = vi.fn();
  render(<ConfigWizard {...defaultProps} onSave={onSave} />);
  await userEvent.click(await screen.findByLabelText(/back up disc before ripping/i));
  await userEvent.type(await screen.findByLabelText(/backup folder/i), 'D:\\backups');
  await userEvent.click(screen.getByRole('button', { name: /save/i }));
  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({ backup_before_rip: true, backup_path: 'D:\\backups' }),
  );
});
```

Adapt `defaultProps` and the save-button query to whatever the existing tests in this file already use.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm run test:unit -- ConfigWizard
```

Expected: `Unable to find a label with the text of: /back up disc before ripping/i`.

- [ ] **Step 3: Write the implementation**

Add the toggle to the paths/preferences section, following the shape of the existing `always_review` toggle, with help text: "Write a full copy of each disc to a separate folder, then extract from that copy. Slower per disc and uses tens of gigabytes, but the disc is read once and the copy is kept." Reveal a `backup_path` picker when the toggle is on, using the same `PathStatusHint` treatment as the library roots. Include both keys in the payload the wizard PUTs to `/api/config`.

- [ ] **Step 4: Run the tests and build**

```bash
cd frontend && npm run test:unit -- ConfigWizard && npm run build
```

Expected: pass and clean.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src/components/ConfigWizard.tsx frontend/src/components/ConfigWizard.test.tsx
git commit -m "feat(backup): add backup settings to the config wizard"
```

---

## Task 20: Import modal and history surfaces

**Files:**
- Modify: `frontend/src/components/ImportModal.tsx`
- Modify: `frontend/src/components/HistoryPage/` (the detail panel component)
- Modify: `frontend/src/app/components/DiscCard/DiscMetadata.tsx`
- Test: `frontend/src/components/ImportModal.test.tsx` (add cases)

- [ ] **Step 1: Write the failing tests**

```tsx
it('renders a disc backup entry distinctly from a folder', async () => {
  mockBrowse({ entries: [
    { name: 'Inception (2010)', path: '/b/Inception (2010)', type: 'disc_image' },
    { name: 'Frasier', path: '/b/Frasier', type: 'dir', mkv_count: 3 },
  ]});
  render(<ImportModal {...defaultProps} />);
  expect(await screen.findByText(/disc backup/i)).toBeInTheDocument();
});

it('summarises disc images in the preview', async () => {
  mockPreview({ units: [], disc_images: [
    { name: 'Inception (2010)', path: '/b/Inception (2010)', kind: 'backup', total_bytes: 40e9 },
  ], total_jobs: 1, total_files: 0, total_bytes: 40e9, loose_files: [], root: '/b' });
  render(<ImportModal {...defaultProps} />);
  // The user must know this is scanned and extracted, not filed.
  expect(await screen.findByText(/will be scanned and extracted/i)).toBeInTheDocument();
});
```

Adapt `mockBrowse` / `mockPreview` to the fetch-mocking helpers this file already uses.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npm run test:unit -- ImportModal
```

Expected: `Unable to find an element with the text: /disc backup/i`.

- [ ] **Step 3: Write the implementation**

In `ImportModal.tsx`, render `disc_image` and `iso` entries with a distinct icon and a "DISC BACKUP" / "ISO" tag, and add a preview line reading "N disc backup(s) will be scanned and extracted" so the user knows this path runs MakeMKV rather than filing existing MKVs. Include disc images in the selection and confirmation counts.

In the History detail panel, render the backup path and status next to the other job paths. Render a warning row when `backup_status` is `skipped` or `failed`, mapping `backup_status_reason` to plain text ("No backup folder configured", "Not enough free space", "MakeMKV could not enumerate the drive", "This disc type cannot be backed up", "Backup failed: <reason>").

In `DiscMetadata.tsx`, show the same one-line note on the card for a live job.

- [ ] **Step 4: Run the tests and build**

```bash
cd frontend && npm run test:unit && npm run build
```

Expected: pass and clean.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run lint
git add frontend/src
git commit -m "feat(backup): surface disc backups in the import modal and history"
```

---

## Task 21: End-to-end tests

**Files:**
- Create: `frontend/e2e/disc-backup.spec.ts`

- [ ] **Step 1: Write the E2E spec**

```ts
import { test, expect } from '@playwright/test';
import { resetAllJobs } from './helpers';

test.describe('disc backup before rip', () => {
  test.beforeEach(async ({ request }) => {
    await resetAllJobs(request);
  });

  test('a simulated disc shows BACKING UP, then RIPPING', async ({ page, request }) => {
    await page.goto('/');
    await request.post('/api/simulate/insert-disc', {
      data: {
        volume_label: 'INCEPTION_2010',
        content_type: 'movie',
        simulate_backup: true,
        simulate_ripping: false,
      },
    });

    const card = page.getByTestId('disc-card').first();
    await expect(card.getByText('BACKING UP')).toBeVisible();
    await expect(card.getByText(/BACKING UP DISC/i)).toBeVisible();

    const jobId = await card.getAttribute('data-job-id');
    await request.post(`/api/simulate/advance-job/${jobId}`);
    await expect(card.getByText('RIPPING')).toBeVisible();
  });

  test('a backup folder appears in the import modal', async ({ page }) => {
    // e2e/fixtures/backups/Inception (2010)/BDMV/STREAM/ holds a single tiny
    // .m2ts, which is all the detector needs: no media is required.
    await page.goto('/');
    await page.getByRole('button', { name: /import/i }).click();
    await page.getByLabel(/path/i).fill('e2e/fixtures/backups');
    await expect(page.getByText(/disc backup/i)).toBeVisible();
  });
});
```

Match the existing specs' selectors and `resetAllJobs` helper import; if `disc-card` has no `data-testid`, use the selector the neighbouring specs use.

- [ ] **Step 2: Create the fixture**

```bash
cd frontend && mkdir -p "e2e/fixtures/backups/Inception (2010)/BDMV/STREAM" && printf 'x' > "e2e/fixtures/backups/Inception (2010)/BDMV/STREAM/00001.m2ts"
```

- [ ] **Step 3: Run the E2E spec**

Start the backend with `DEBUG=true` on a port of your choosing, point the frontend proxy at it, then:

```bash
cd frontend && npm run test:e2e -- disc-backup.spec.ts
```

Expected: both tests pass. Screenshots and modal interaction can leave a stuck `opacity-0` overlay under automation; that is a known pre-existing condition, not a regression from this work.

- [ ] **Step 4: Stop the servers you started**

```powershell
Get-NetTCPConnection -LocalPort <your backend port>,<your vite port> -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e
git commit -m "test(backup): E2E coverage for the backup phase and backup import"
```

---

## Task 22: Documentation and changelog

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/` (the configuration page)

- [ ] **Step 1: Update CLAUDE.md**

Add to the WebSocket message table:

| `backup_progress` | `{"job_id": int, "current_bytes": int, "total_bytes": int, "speed": str, "eta": int}` | Full-disc backup copy progress |

Add to Key Configuration Fields:

- **Disc backup**: `backup_before_rip` (bool, default false, `server_default 0`) plus `backup_path`. When on, a disc is copied whole to `<backup_path>` after identification and extracted from the copy, and the drive is released at the end of `BACKING_UP` rather than at the end of `RIPPING`. Every failure degrades to a direct rip (see `backup_status`); the one exception is a backup whose re-scan cannot be reconciled onto the stored title indices, which parks in `REVIEW_NEEDED` because the disc has already been ejected. Backups are never deleted by Engram.

Add to Key Patterns:

- **A job's source is a value, not a drive.** `DiscSource` (`app/core/disc_source.py`) is the single answer to "is this a physical drive?", which gates eject, sentinel re-arm and, critically, the MakeMKV lock key: a `file:` source must not contend for the optical drive's lock, or extracting from a backup would block the next disc from being scanned. `source_spec` on `DiscJob` is the stored form, `None` meaning "legacy: derive from `drive_id`".

- [ ] **Step 2: Update CHANGELOG.md**

Add under `## [Unreleased]`:

```markdown
### Added

- Optional full-disc backup before ripping. Engram can write a complete decrypted MakeMKV copy of each disc to a separate folder, then extract from that copy instead of the drive: the disc is read once and released as soon as the copy finishes, extraction is faster and survives marginal reads, and the backup is kept for preservation. Off by default; enable it under Settings with a backup folder. (#NNN)
- Existing disc backups and ISO files can be imported from the manual import modal, which scans and extracts them through the normal pipeline. Pointing at a folder of backups queues every disc under it. (#NNN)
```

Replace `#NNN` with the PR number once the PR exists.

- [ ] **Step 3: Update the docs configuration page**

Document both settings and the backup folder layout (`Movies/Name (Year)/`, `TV/Show (Year)/Season NN/Disc slug/`, `Unidentified/<label>/`), and state plainly that Engram never deletes a backup.

- [ ] **Step 4: Verify the docs build**

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Run from the repository root, not `backend/`. Expected: builds with the usual 18 warnings and no new ones. Do not add `--strict`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md CHANGELOG.md docs
git commit -m "docs(backup): document disc backup before rip and backup import"
```

---

## Task 23: Full verification before the PR

- [ ] **Step 1: Run the backend unit tier**

```bash
cd backend && uv run pytest tests/unit -q
```

Expected: all pass. This takes several minutes; run it in the main session rather than inside a subagent.

- [ ] **Step 2: Run the integration and pipeline tiers**

```bash
cd backend && uv run pytest tests/integration tests/pipeline -q
```

Expected: all pass. Note that `tests/integration/test_workflow.py` runs against the real app database and can move files into the real library, so run it only when that is acceptable.

- [ ] **Step 3: Lint the backend**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 4: Build and lint the frontend**

```bash
cd frontend && npm run build && npm run lint && npm run test:unit
```

Expected: clean.

- [ ] **Step 5: Confirm the default path is untouched**

With `backup_before_rip` off (the default), simulate a disc and confirm it goes `identifying -> ripping` with no `BACKING_UP` and `backup_status` still `None`:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc -H "Content-Type: application/json" -d '{"volume_label":"INCEPTION_2010","content_type":"movie","simulate_ripping":true}'
```

- [ ] **Step 6: Real-disc verification, exactly one backend running**

Verify, and record the answers in the spec's Open Questions section:

1. A Blu-ray with the setting on: the backup lands at the expected path, the tray opens when the backup finishes rather than when extraction finishes, and extraction reads from the backup.
2. A DVD with the setting on: does `makemkvcon backup` accept it? If not, confirm the job records `backup_status="skipped"` with `backup_status_reason="unsupported_disc"` and rips directly.
3. Whether `makemkvcon backup` emits `PRGV` lines. If it does not, the progress bar will sit at zero, and `_run_backup` needs the filesystem-polling fallback that `_run_ripping` already demonstrates.
4. An existing backup folder and an ISO imported through the modal.

- [ ] **Step 7: Stop this session's servers**

```powershell
Get-NetTCPConnection -LocalPort <your ports> -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

Also kill any orphaned `makemkvcon` processes this session started.

- [ ] **Step 8: Open the PR**

```bash
git push -u origin claude/disc-backup-before-rip-4967ae
```

Then open the PR with `gh pr create`, and immediately post `@claude please review this PR` as a comment: the code-review workflow only fires on the `opened` event.

---

## Self-review notes

Spec coverage checked section by section. Every spec section maps to a task: data model (4), components (1, 2, 3, 6), destination naming (5), reconciliation (9), failure handling (10), drive release and notifications (10), watchdog (7), WebSocket contract (8), import from backup (13, 14), frontend (17, 18, 19, 20), diagnostics (15), testing (every task, plus 16 and 21), open questions (23 step 6).

Two deliberate deviations from the spec, both improvements found while planning:

1. The spec's frontend section assumed a new UI state. Task 17 instead repurposes the orphaned `archiving_iso` / `isoProgress` scaffolding, which no backend code has ever emitted. Adding an eleventh state beside a dead tenth one would create exactly the drift `discState.ts` warns about.
2. `next_state_after_identify` returns `RIPPING` when `backup_before_rip` is on but `backup_path` is empty, rather than entering `BACKING_UP` and immediately falling back. The `skipped` / `not_configured` path in `_run_backup` remains as the backstop for a root that is emptied mid-job, so both are covered.
