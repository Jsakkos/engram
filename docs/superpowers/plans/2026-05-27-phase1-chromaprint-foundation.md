# Phase 1: Chromaprint Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Engram rip extracts a chromaprint fingerprint and stores it alongside the existing match metadata, with a local-only contribution queue and a bootstrap CLI that lets users seed contributions from existing libraries. No identification path, no server, no UI surfaces beyond a single opt-out toggle.

**Architecture:** Add chromaprint columns to `DiscTitle` and config to `AppConfig`. Wrap `fpcalc` (chromaprint's CLI, bundled with its own FFmpeg) in a `ChromaprintExtractor` mirroring the existing MakeMKV subprocess pattern. Hook extraction into `matching_coordinator._match_single_file_inner()` right after the match result is computed; failures degrade gracefully (no impact on matching). A new `FingerprintContribution` table queues successful (chromaprint, episode tuple) pairs locally — Phase 2 adds the server upload; Phase 1 only writes the queue. The bootstrap CLI script walks a directory and enqueues contributions from labeled MKVs without touching the matching pipeline.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel + aiosqlite, Alembic, asyncio, `fpcalc` (chromaprint 1.5.1), React + TypeScript for the single opt-out toggle, pytest for tests.

---

## File Structure

**Backend new files:**
- `backend/app/matcher/chromaprint_extractor.py` — `ChromaprintExtractor` class; subprocess wrapper + serialization
- `backend/app/services/contribution_queue.py` — Local-only `ContributionQueue` service
- `backend/app/scripts/bootstrap_library.py` — Standalone CLI for fingerprinting existing libraries
- `backend/migrations/versions/<new>_phase1_chromaprint_schema.py` — Alembic migration
- `backend/tests/unit/test_chromaprint_extractor.py`
- `backend/tests/unit/test_contribution_queue.py`
- `backend/tests/unit/test_fpcalc_validation.py`
- `backend/tests/integration/test_chromaprint_pipeline.py`

**Backend modified files:**
- `backend/app/models/disc_job.py` — `DiscTitle`: add `chromaprint_blob`, `chromaprint_extracted_at` fields
- `backend/app/models/app_config.py` — add `fpcalc_path`, `contribution_pseudonym`, `enable_fingerprint_contributions`
- `backend/app/models/__init__.py` — export new `FingerprintContribution` model
- `backend/app/api/validation.py` — add `_validate_fpcalc_binary`, `/api/validate/fpcalc` endpoint, `fpcalc` in `/api/detect-tools`
- `backend/app/services/matching_coordinator.py` — hook extractor after `match_single_file` call
- `backend/app/api/routes.py` — `GET /api/fingerprint/contributions` (local audit log)
- `backend/app/database.py` — no code change; `_add_missing_columns()` automatically picks up new model fields

**Frontend modified files:**
- `frontend/src/components/ConfigWizard.tsx` — `enableFingerprintContributions` toggle
- `frontend/e2e/fingerprint-toggle.spec.ts` — new E2E test

**Test fixtures:**
- The existing `spikes/chromaprint/bin/fpcalc.exe` can be reused as the dev-time binary. Tests should locate it via the `fpcalc_path` config (set in test fixture) and skip cleanly if not available.

---

## Pre-flight

Before starting any task: confirm the worktree, install deps, run baseline tests.

- [ ] **Step 0.1: Confirm working directory**

Run from the worktree: `pwd`
Expected: `C:\Github\engram\.claude\worktrees\romantic-mendeleev-bb5976` (or `/c/Github/engram/.claude/worktrees/romantic-mendeleev-bb5976` in bash).

- [ ] **Step 0.2: Sync backend deps and run baseline tests**

```bash
cd backend
uv sync
uv run pytest tests/unit -x --tb=short 2>&1 | tail -20
```
Expected: all unit tests pass. If failures, note them — don't try to fix them in this plan.

- [ ] **Step 0.3: Confirm fpcalc is available**

```bash
ls -la ../spikes/chromaprint/bin/fpcalc.exe
../spikes/chromaprint/bin/fpcalc.exe -version
```
Expected: `fpcalc version 1.5.1 (...)`. If missing, re-download per the spike instructions.

---

## Cluster A: Schema & Config

### Task A1: Add chromaprint columns to `DiscTitle` model

**Files:**
- Modify: `backend/app/models/disc_job.py` (DiscTitle class)
- Test: `backend/tests/unit/test_chromaprint_extractor.py` (new file — will hold schema test first)

- [ ] **Step A1.1: Write the failing schema test**

Create `backend/tests/unit/test_chromaprint_extractor.py` with:

```python
"""Tests for chromaprint extraction and storage."""

from app.models.disc_job import DiscTitle


def test_disc_title_has_chromaprint_fields():
    """DiscTitle model exposes chromaprint storage fields."""
    fields = DiscTitle.model_fields
    assert "chromaprint_blob" in fields, "DiscTitle is missing chromaprint_blob"
    assert "chromaprint_extracted_at" in fields, "DiscTitle is missing chromaprint_extracted_at"
```

- [ ] **Step A1.2: Run the test (expect FAIL)**

```bash
cd backend
uv run pytest tests/unit/test_chromaprint_extractor.py::test_disc_title_has_chromaprint_fields -v
```
Expected: FAIL with `AssertionError: DiscTitle is missing chromaprint_blob`.

- [ ] **Step A1.3: Add the fields to `DiscTitle`**

In `backend/app/models/disc_job.py`, find the `DiscTitle` class definition. After the existing match-related fields (`matched_episode`, `match_confidence`, `match_details`, `match_source`), add:

```python
    # Chromaprint fingerprint (Phase 1 — extraction + storage only; no identification yet)
    chromaprint_blob: bytes | None = Field(default=None)
    chromaprint_extracted_at: datetime | None = Field(default=None)
```

Ensure `datetime` is imported at the top of the file (it should already be present; if not, add `from datetime import datetime`).

- [ ] **Step A1.4: Run the test (expect PASS)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py::test_disc_title_has_chromaprint_fields -v
```
Expected: PASS.

- [ ] **Step A1.5: Verify the schema reconciler picks it up**

Manually verify `_add_missing_columns()` in `backend/app/database.py` (around line 121) iterates SQLModel metadata — no code change needed, but read the function to confirm new fields will be added on next `init_db()`.

- [ ] **Step A1.6: Commit**

```bash
git add backend/app/models/disc_job.py backend/tests/unit/test_chromaprint_extractor.py
git commit -m "feat(models): add chromaprint storage fields to DiscTitle"
```

### Task A2: Add fingerprint config fields to `AppConfig`

**Files:**
- Modify: `backend/app/models/app_config.py`
- Test: extend `backend/tests/unit/test_chromaprint_extractor.py`

- [ ] **Step A2.1: Write the failing test**

Append to `backend/tests/unit/test_chromaprint_extractor.py`:

```python
from app.models.app_config import AppConfig


def test_app_config_has_fingerprint_fields():
    """AppConfig exposes fingerprint extraction settings."""
    fields = AppConfig.model_fields
    assert "fpcalc_path" in fields
    assert "contribution_pseudonym" in fields
    assert "enable_fingerprint_contributions" in fields


def test_enable_fingerprint_contributions_defaults_true():
    """Opt-out default: contributions enabled unless explicitly disabled."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_contributions is True
```

- [ ] **Step A2.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: both new tests FAIL.

- [ ] **Step A2.3: Add fields to `AppConfig`**

In `backend/app/models/app_config.py`, add near the other tool-path / feature-toggle fields:

```python
    # Chromaprint / fingerprint contributions
    fpcalc_path: str | None = Field(default=None)
    contribution_pseudonym: str | None = Field(default=None)
    enable_fingerprint_contributions: bool = Field(default=True)
```

- [ ] **Step A2.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: all pass.

- [ ] **Step A2.5: Commit**

```bash
git add backend/app/models/app_config.py backend/tests/unit/test_chromaprint_extractor.py
git commit -m "feat(models): add fpcalc_path, pseudonym, and opt-out toggle to AppConfig"
```

### Task A3: Alembic migration for the new columns

**Files:**
- Create: `backend/migrations/versions/<rev>_phase1_chromaprint_schema.py`

- [ ] **Step A3.1: Identify the current head revision**

```bash
cd backend
uv run alembic heads
```
Note the revision ID printed (call it `PREV_REV` below).

- [ ] **Step A3.2: Generate a new migration**

```bash
uv run alembic revision -m "phase1 chromaprint schema"
```
Note the generated file path under `backend/migrations/versions/`. Open it.

- [ ] **Step A3.3: Fill in the migration body**

Replace the generated `upgrade()` and `downgrade()` with:

```python
"""phase1 chromaprint schema

Revision ID: <auto>
Revises: <PREV_REV>
Create Date: <auto>
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Keep auto-generated revision/down_revision fields above this line.


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chromaprint_blob", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("chromaprint_extracted_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fpcalc_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contribution_pseudonym", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "enable_fingerprint_contributions",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("enable_fingerprint_contributions")
        batch_op.drop_column("contribution_pseudonym")
        batch_op.drop_column("fpcalc_path")
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("chromaprint_extracted_at")
        batch_op.drop_column("chromaprint_blob")
```

- [ ] **Step A3.4: Run migration upgrade against a scratch DB**

```bash
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic downgrade -1
DATABASE_URL="sqlite+aiosqlite:///./scratch.db" uv run alembic upgrade head
rm scratch.db
```
Expected: upgrade and downgrade both succeed without errors.

- [ ] **Step A3.5: Commit**

```bash
git add backend/migrations/versions/<new-file>.py
git commit -m "feat(migrations): phase1 chromaprint columns + opt-out config"
```

---

## Cluster B: fpcalc Tooling

### Task B1: `_validate_fpcalc_binary()` helper

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: `backend/tests/unit/test_fpcalc_validation.py` (new)

- [ ] **Step B1.1: Write the failing tests**

Create `backend/tests/unit/test_fpcalc_validation.py`:

```python
"""Tests for fpcalc binary validation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.api.validation import _validate_fpcalc_binary


def test_validate_fpcalc_binary_success():
    """Valid fpcalc binary returns found=True with version."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "fpcalc version 1.5.1 (FFmpeg ...)\n"
        result = _validate_fpcalc_binary("/fake/fpcalc")
        assert result.found is True
        assert "1.5.1" in result.version
        assert result.path == "/fake/fpcalc"


def test_validate_fpcalc_binary_nonzero_exit():
    """Non-zero exit code reports not found with error."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "bad binary"
        result = _validate_fpcalc_binary("/fake/fpcalc")
        assert result.found is False
        assert "exit" in result.error.lower() or "code" in result.error.lower()


def test_validate_fpcalc_binary_missing_file(tmp_path):
    """A nonexistent path reports found=False without raising."""
    bogus = tmp_path / "nope.exe"
    result = _validate_fpcalc_binary(str(bogus))
    assert result.found is False
    assert result.path == str(bogus)
```

- [ ] **Step B1.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_fpcalc_validation.py -v
```
Expected: ImportError or AttributeError — `_validate_fpcalc_binary` doesn't exist yet.

- [ ] **Step B1.3: Implement `_validate_fpcalc_binary`**

In `backend/app/api/validation.py`, immediately after `_validate_ffmpeg_binary` (around line 188-205), add:

```python
def _validate_fpcalc_binary(path_str: str) -> ToolDetectionResult:
    """Validate a chromaprint fpcalc binary and extract version info."""
    if not Path(path_str).is_file():
        return ToolDetectionResult(
            found=False,
            path=path_str,
            error="Path does not exist or is not a file",
        )
    try:
        result = subprocess.run(
            [path_str, "-version"],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode != 0:
            return ToolDetectionResult(
                found=False,
                path=path_str,
                error=f"Non-zero exit code {result.returncode}",
            )
        version_line = (result.stdout or "").split("\n")[0] or "unknown"
        return ToolDetectionResult(found=True, path=path_str, version=version_line)
    except subprocess.TimeoutExpired:
        return ToolDetectionResult(found=False, path=path_str, error="Timed out")
    except OSError as e:
        return ToolDetectionResult(found=False, path=path_str, error=str(e))
```

Ensure `Path` is imported at the top of the file (it should already be).

- [ ] **Step B1.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_fpcalc_validation.py -v
```
Expected: all PASS.

- [ ] **Step B1.5: Commit**

```bash
git add backend/app/api/validation.py backend/tests/unit/test_fpcalc_validation.py
git commit -m "feat(validation): add _validate_fpcalc_binary helper"
```

### Task B2: Auto-detect fpcalc in common locations

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: extend `backend/tests/unit/test_fpcalc_validation.py`

- [ ] **Step B2.1: Write the failing test**

Append to `backend/tests/unit/test_fpcalc_validation.py`:

```python
from unittest.mock import MagicMock


def test_detect_fpcalc_uses_path_search(monkeypatch):
    """detect_fpcalc consults shutil.which before falling back to common paths."""
    from app.api import validation as v

    def fake_which(name):
        return "/usr/local/bin/fpcalc" if name == "fpcalc" else None

    monkeypatch.setattr(v.shutil, "which", fake_which)
    monkeypatch.setattr(
        v,
        "_validate_fpcalc_binary",
        lambda p: v.ToolDetectionResult(found=True, path=p, version="fpcalc version 1.5.1"),
    )
    result = v.detect_fpcalc()
    assert result.found is True
    assert result.path == "/usr/local/bin/fpcalc"
```

- [ ] **Step B2.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_fpcalc_validation.py::test_detect_fpcalc_uses_path_search -v
```
Expected: FAIL — `detect_fpcalc` doesn't exist.

- [ ] **Step B2.3: Implement `detect_fpcalc()`**

In `backend/app/api/validation.py`, after `_validate_fpcalc_binary` add:

```python
FPCALC_COMMON_PATHS = [
    # Windows
    r"C:\Program Files\Chromaprint\fpcalc.exe",
    r"C:\Program Files (x86)\Chromaprint\fpcalc.exe",
    # macOS (homebrew)
    "/opt/homebrew/bin/fpcalc",
    "/usr/local/bin/fpcalc",
    # Linux
    "/usr/bin/fpcalc",
    # Dev convenience: the worktree spike binary
    str(Path(__file__).resolve().parents[3] / "spikes" / "chromaprint" / "bin" / "fpcalc.exe"),
]


def detect_fpcalc() -> ToolDetectionResult:
    """Auto-detect a usable fpcalc binary.

    Order: PATH first, then common platform locations, then the dev spike binary.
    Returns the first result that validates successfully.
    """
    via_path = shutil.which("fpcalc")
    candidates: list[str] = []
    if via_path:
        candidates.append(via_path)
    candidates.extend(FPCALC_COMMON_PATHS)

    for candidate in candidates:
        result = _validate_fpcalc_binary(candidate)
        if result.found:
            return result

    return ToolDetectionResult(
        found=False,
        path=None,
        error="fpcalc not found in PATH or common locations",
    )
```

Ensure `shutil` is imported (it should be).

- [ ] **Step B2.4: Run the test (expect PASS)**

```bash
uv run pytest tests/unit/test_fpcalc_validation.py -v
```
Expected: all PASS.

- [ ] **Step B2.5: Commit**

```bash
git add backend/app/api/validation.py backend/tests/unit/test_fpcalc_validation.py
git commit -m "feat(validation): auto-detect fpcalc in PATH and common locations"
```

### Task B3: Validation endpoint + add to detect-tools response

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: `backend/tests/integration/test_chromaprint_pipeline.py` (new)

- [ ] **Step B3.1: Write the failing integration test**

Create `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
"""Integration tests for chromaprint Phase 1: fpcalc detection + extraction pipeline."""

import pytest


@pytest.mark.asyncio
async def test_detect_tools_includes_fpcalc(integration_client):
    """GET /api/detect-tools surfaces fpcalc alongside makemkv and ffmpeg."""
    response = await integration_client.get("/api/detect-tools")
    assert response.status_code == 200
    data = response.json()
    assert "fpcalc" in data, f"detect-tools should include fpcalc, got keys: {list(data.keys())}"


@pytest.mark.asyncio
async def test_validate_fpcalc_endpoint_rejects_missing(integration_client):
    """POST /api/validate/fpcalc with a bogus path reports found=False."""
    response = await integration_client.post(
        "/api/validate/fpcalc",
        json={"path": "/definitely/not/a/binary"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
```

- [ ] **Step B3.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: FAIL — both endpoints don't include fpcalc support yet.

- [ ] **Step B3.3: Add `/api/validate/fpcalc` endpoint**

In `backend/app/api/validation.py`, after the existing `/api/validate/ffmpeg` endpoint (around line 292), add:

```python
@router.post("/validate/fpcalc", response_model=ValidationResponse)
async def validate_fpcalc(request: ValidationRequest) -> ValidationResponse:
    """Validate a user-supplied fpcalc binary path."""
    result = _validate_fpcalc_binary(request.path)
    return ValidationResponse(
        valid=result.found,
        message=result.version or result.error or "",
        details={"path": result.path, "version": result.version},
    )
```

- [ ] **Step B3.4: Add fpcalc to `/api/detect-tools`**

Find the `detect_tools` endpoint in the same file (search for `@router.get("/detect-tools"`). It currently returns a dict including `makemkv` and `ffmpeg`. Add `fpcalc`:

```python
    return {
        "makemkv": detect_makemkv(),
        "ffmpeg": detect_ffmpeg(),
        "fpcalc": detect_fpcalc(),
    }
```

- [ ] **Step B3.5: Run the tests (expect PASS)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: PASS.

- [ ] **Step B3.6: Commit**

```bash
git add backend/app/api/validation.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(api): /api/validate/fpcalc + fpcalc in detect-tools"
```

---

## Cluster C: ChromaprintExtractor

### Task C1: Module skeleton and types

**Files:**
- Create: `backend/app/matcher/chromaprint_extractor.py`
- Test: extend `backend/tests/unit/test_chromaprint_extractor.py`

- [ ] **Step C1.1: Write the failing test**

Append to `backend/tests/unit/test_chromaprint_extractor.py`:

```python
from app.matcher.chromaprint_extractor import ChromaprintResult, ChromaprintExtractor


def test_chromaprint_result_serializes_to_bytes():
    """ChromaprintResult.to_blob() returns deterministic compressed bytes."""
    r = ChromaprintResult(
        hashes=[1, 2, 3, 4, 5],
        duration_seconds=42.0,
        fpcalc_version="fpcalc version 1.5.1",
    )
    blob = r.to_blob()
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    # Re-serializing the same data must produce identical bytes (deterministic)
    assert r.to_blob() == blob


def test_chromaprint_result_roundtrip():
    """to_blob / from_blob is lossless on the hash stream and duration."""
    r = ChromaprintResult(hashes=[100, 200, 300], duration_seconds=12.5, fpcalc_version="test")
    restored = ChromaprintResult.from_blob(r.to_blob())
    assert restored.hashes == [100, 200, 300]
    assert restored.duration_seconds == 12.5


def test_extractor_construction():
    """ChromaprintExtractor takes an fpcalc_path."""
    ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
    assert ex.fpcalc_path == "/fake/fpcalc"
```

- [ ] **Step C1.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: ImportError — the module doesn't exist.

- [ ] **Step C1.3: Create the module skeleton**

Create `backend/app/matcher/chromaprint_extractor.py`:

```python
"""Chromaprint fingerprint extraction.

Wraps the fpcalc CLI (bundled with libchromaprint) to produce a chromaprint hash
stream for an MKV/MP4/audio file. Phase 1 stores the full fingerprint per title;
windowed querying lives in Phase 3.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass


@dataclass
class ChromaprintResult:
    """The full chromaprint hash stream for one media file."""

    hashes: list[int]
    duration_seconds: float
    fpcalc_version: str

    def to_blob(self) -> bytes:
        """Serialize to gzip-compressed JSON for DB storage."""
        payload = {
            "v": 1,
            "duration": self.duration_seconds,
            "fpcalc": self.fpcalc_version,
            "hashes": self.hashes,
        }
        return gzip.compress(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            mtime=0,
        )

    @classmethod
    def from_blob(cls, blob: bytes) -> "ChromaprintResult":
        payload = json.loads(gzip.decompress(blob).decode("utf-8"))
        if payload.get("v") != 1:
            raise ValueError(f"Unknown chromaprint blob version: {payload.get('v')}")
        return cls(
            hashes=list(payload["hashes"]),
            duration_seconds=float(payload["duration"]),
            fpcalc_version=str(payload.get("fpcalc", "")),
        )


class ChromaprintExtractor:
    """Subprocess-based chromaprint fingerprint extractor."""

    def __init__(self, fpcalc_path: str) -> None:
        self.fpcalc_path = fpcalc_path

    async def extract(self, media_path: str) -> ChromaprintResult:
        raise NotImplementedError("extract() lands in Task C2")
```

- [ ] **Step C1.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: all PASS (including the three new tests).

- [ ] **Step C1.5: Commit**

```bash
git add backend/app/matcher/chromaprint_extractor.py backend/tests/unit/test_chromaprint_extractor.py
git commit -m "feat(matcher): ChromaprintResult + extractor skeleton"
```

### Task C2: Implement `extract()` with subprocess

**Files:**
- Modify: `backend/app/matcher/chromaprint_extractor.py`
- Test: extend `backend/tests/unit/test_chromaprint_extractor.py`

- [ ] **Step C2.1: Write the failing tests**

Append to `backend/tests/unit/test_chromaprint_extractor.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_extract_parses_fpcalc_output():
    """extract() parses DURATION and FINGERPRINT from fpcalc -raw output."""
    fake_output = "DURATION=1304\nFINGERPRINT=112114628,250527685,250521542\n"
    with patch("asyncio.to_thread") as mock_thread:
        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = fake_output
        mock_completed.stderr = ""
        mock_thread.return_value = mock_completed

        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        result = await ex.extract("/fake/movie.mkv")

    assert result.duration_seconds == 1304.0
    assert result.hashes == [112114628, 250527685, 250521542]


@pytest.mark.asyncio
async def test_extract_raises_on_fpcalc_failure():
    """A non-zero fpcalc exit raises a clean exception, not subprocess noise."""
    with patch("asyncio.to_thread") as mock_thread:
        mock_completed = MagicMock()
        mock_completed.returncode = 1
        mock_completed.stdout = ""
        mock_completed.stderr = "fpcalc: ERROR: cannot decode audio"
        mock_thread.return_value = mock_completed

        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        with pytest.raises(RuntimeError, match="fpcalc"):
            await ex.extract("/fake/no-audio.mkv")


@pytest.mark.asyncio
async def test_extract_raises_when_no_fingerprint_line():
    """fpcalc returned 0 but no FINGERPRINT line — should fail loudly."""
    with patch("asyncio.to_thread") as mock_thread:
        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = "DURATION=10\n"
        mock_completed.stderr = ""
        mock_thread.return_value = mock_completed

        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        with pytest.raises(RuntimeError, match="FINGERPRINT"):
            await ex.extract("/fake/silent.mkv")
```

- [ ] **Step C2.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: 3 new tests FAIL (NotImplementedError).

- [ ] **Step C2.3: Implement `extract()`**

Replace the `extract()` body in `backend/app/matcher/chromaprint_extractor.py`:

```python
import asyncio
import subprocess
from loguru import logger


class ChromaprintExtractor:
    def __init__(self, fpcalc_path: str, timeout_seconds: float = 120.0) -> None:
        self.fpcalc_path = fpcalc_path
        self.timeout_seconds = timeout_seconds

    async def extract(self, media_path: str) -> ChromaprintResult:
        """Extract the full chromaprint hash stream from a media file.

        Returns a `ChromaprintResult` on success. Raises `RuntimeError` on any
        fpcalc-side failure — the caller decides whether the matching pipeline
        should continue without a fingerprint.
        """
        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.fpcalc_path, "-raw", "-length", "99999", media_path],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"fpcalc timed out after {self.timeout_seconds}s on {media_path}") from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"fpcalc exited {proc.returncode} on {media_path}: {proc.stderr.strip()}"
            )

        duration: float | None = None
        hashes: list[int] = []
        version_line = ""
        for line in proc.stdout.splitlines():
            if line.startswith("DURATION="):
                duration = float(line.removeprefix("DURATION="))
            elif line.startswith("FINGERPRINT="):
                hashes = [int(x) for x in line.removeprefix("FINGERPRINT=").split(",") if x]

        if not hashes:
            raise RuntimeError(f"fpcalc produced no FINGERPRINT line for {media_path}")
        if duration is None:
            duration = 0.0

        # Best-effort version capture (single call cached at first use is overkill for Phase 1)
        version_line = await self._cached_version()

        logger.info(f"chromaprint extracted: {len(hashes)} hashes, {duration:.1f}s from {media_path}")
        return ChromaprintResult(hashes=hashes, duration_seconds=duration, fpcalc_version=version_line)

    async def _cached_version(self) -> str:
        if hasattr(self, "_version_cache"):
            return self._version_cache  # type: ignore[return-value]
        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.fpcalc_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        try:
            proc = await asyncio.to_thread(_run)
            self._version_cache = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        except Exception:
            self._version_cache = ""
        return self._version_cache
```

- [ ] **Step C2.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_chromaprint_extractor.py -v
```
Expected: all PASS.

- [ ] **Step C2.5: Smoke-test with the real fpcalc + a real MKV**

```bash
cd backend
uv run python -c "
import asyncio
from app.matcher.chromaprint_extractor import ChromaprintExtractor

async def main():
    ex = ChromaprintExtractor(fpcalc_path='../spikes/chromaprint/bin/fpcalc.exe')
    r = await ex.extract('C:/Users/jonat/Engram/TV/Arrested Development/Season 1/Arrested Development - S01E07.mkv')
    print(f'OK: {len(r.hashes)} hashes, {r.duration_seconds:.1f}s, {len(r.to_blob())} bytes compressed')

asyncio.run(main())
"
```
Expected output: `OK: ~10500 hashes, ~1300s, ~XX KB compressed`. If your library path differs, edit accordingly.

- [ ] **Step C2.6: Commit**

```bash
git add backend/app/matcher/chromaprint_extractor.py backend/tests/unit/test_chromaprint_extractor.py
git commit -m "feat(matcher): implement ChromaprintExtractor.extract via fpcalc subprocess"
```

### Task C3: Pseudonym generation helper

**Files:**
- Create: `backend/app/services/contribution_pseudonym.py`
- Test: `backend/tests/unit/test_contribution_pseudonym.py` (new)

- [ ] **Step C3.1: Write the failing test**

Create `backend/tests/unit/test_contribution_pseudonym.py`:

```python
"""Per-install pseudonym generation."""

import re

from app.services.contribution_pseudonym import generate_pseudonym, validate_pseudonym


def test_generate_pseudonym_is_uuid_v4():
    p = generate_pseudonym()
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", p), p


def test_generate_pseudonym_unique():
    assert generate_pseudonym() != generate_pseudonym()


def test_validate_pseudonym_accepts_uuid():
    assert validate_pseudonym(generate_pseudonym()) is True


def test_validate_pseudonym_rejects_garbage():
    assert validate_pseudonym("not-a-uuid") is False
    assert validate_pseudonym("") is False
    assert validate_pseudonym(None) is False  # type: ignore[arg-type]
```

- [ ] **Step C3.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_contribution_pseudonym.py -v
```
Expected: ImportError.

- [ ] **Step C3.3: Implement the module**

Create `backend/app/services/contribution_pseudonym.py`:

```python
"""Per-install pseudonym generation for fingerprint contributions.

The pseudonym is a UUIDv4 stored in `app_config.contribution_pseudonym`. It is
intentionally not tied to any user identity; rotating it deletes the contribution
history on the server side (Phase 2). Phase 1 only needs to generate and persist it.
"""

from __future__ import annotations

import re
import uuid

_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def generate_pseudonym() -> str:
    """Return a fresh UUIDv4 string."""
    return str(uuid.uuid4())


def validate_pseudonym(value: object) -> bool:
    """True if `value` is a syntactically-valid UUIDv4 string."""
    return isinstance(value, str) and bool(_UUID_V4_RE.match(value))
```

- [ ] **Step C3.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_contribution_pseudonym.py -v
```
Expected: all PASS.

- [ ] **Step C3.5: Commit**

```bash
git add backend/app/services/contribution_pseudonym.py backend/tests/unit/test_contribution_pseudonym.py
git commit -m "feat(services): per-install pseudonym generator"
```

### Task C4: Auto-generate pseudonym on first startup

**Files:**
- Modify: `backend/app/main.py` (or wherever startup lifespan hooks live)
- Test: `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step C4.1: Find the startup hook**

```bash
cd backend
uv run grep -n "lifespan\|startup\|on_event" app/main.py | head
```
Note which function runs once at startup (likely the `lifespan` async context manager).

- [ ] **Step C4.2: Write the failing integration test**

Append to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_pseudonym_generated_on_first_startup(integration_client, async_session):
    """After app startup, app_config has a non-empty contribution_pseudonym."""
    from sqlmodel import select
    from app.models.app_config import AppConfig
    from app.services.contribution_pseudonym import validate_pseudonym

    result = await async_session.execute(select(AppConfig))
    cfg = result.scalar_one_or_none()
    assert cfg is not None
    assert validate_pseudonym(cfg.contribution_pseudonym), (
        f"contribution_pseudonym should be a UUIDv4, got {cfg.contribution_pseudonym!r}"
    )
```

- [ ] **Step C4.3: Run the test (expect FAIL)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py::test_pseudonym_generated_on_first_startup -v
```
Expected: FAIL — pseudonym is None.

- [ ] **Step C4.4: Add the startup hook**

In `backend/app/main.py`, inside the `lifespan` async context manager, after the existing `init_db()` call, add:

```python
    # Ensure the contribution pseudonym exists (Phase 1: fingerprint contributions)
    from sqlmodel import select
    from app.models.app_config import AppConfig
    from app.services.contribution_pseudonym import generate_pseudonym, validate_pseudonym

    async with async_session() as session:
        result = await session.execute(select(AppConfig))
        cfg = result.scalar_one_or_none()
        if cfg is not None and not validate_pseudonym(cfg.contribution_pseudonym):
            cfg.contribution_pseudonym = generate_pseudonym()
            session.add(cfg)
            await session.commit()
```

(If `async_session` is not imported in `main.py`, import it from `app.database`.)

- [ ] **Step C4.5: Run the test (expect PASS)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: all PASS.

- [ ] **Step C4.6: Commit**

```bash
git add backend/app/main.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(startup): generate contribution pseudonym on first boot"
```

---

## Cluster D: Pipeline Integration

### Task D1: Hook extractor into matching coordinator

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`
- Test: extend `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step D1.1: Write the failing integration test**

Append to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_chromaprint_extracted_after_match(integration_client, async_session, monkeypatch):
    """When a title finishes matching, chromaprint_blob is populated on the DiscTitle row."""
    from app.matcher.chromaprint_extractor import ChromaprintResult

    fake_result = ChromaprintResult(hashes=[1, 2, 3], duration_seconds=10.0, fpcalc_version="test")

    async def fake_extract(self, media_path: str):
        return fake_result

    monkeypatch.setattr(
        "app.matcher.chromaprint_extractor.ChromaprintExtractor.extract",
        fake_extract,
    )

    # Drive a simulated TV match end-to-end via /api/simulate
    response = await integration_client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        },
    )
    assert response.status_code == 200

    # Poll for completion (matching+chromaprint should complete < 30s with all-mocked extraction)
    import asyncio
    from sqlmodel import select
    from app.models.disc_job import DiscTitle

    for _ in range(60):
        await asyncio.sleep(0.5)
        result = await async_session.execute(select(DiscTitle))
        titles = result.scalars().all()
        if titles and any(t.chromaprint_blob is not None for t in titles):
            break
    else:
        pytest.fail("No title got chromaprint_blob within 30s")

    matched = [t for t in titles if t.chromaprint_blob is not None]
    assert matched, "Expected at least one title with chromaprint_blob set"
    assert matched[0].chromaprint_extracted_at is not None
```

- [ ] **Step D1.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py::test_chromaprint_extracted_after_match -v
```
Expected: FAIL — extraction not yet hooked into the pipeline.

- [ ] **Step D1.3: Add the hook in `_match_single_file_inner`**

In `backend/app/services/matching_coordinator.py`, find `_match_single_file_inner()` (around line 544). After the `await episode_curator.match_single_file(...)` call returns (around line 635) and before the existing `title.state = TitleState.MATCHED` write, insert:

```python
            # Phase 1: extract chromaprint fingerprint (best-effort; failure does not block match)
            try:
                from app.matcher.chromaprint_extractor import ChromaprintExtractor
                from app.services.config_service import get_config
                from datetime import datetime, timezone

                cfg = await get_config(session)
                fpcalc_path = cfg.fpcalc_path
                if not fpcalc_path:
                    from app.api.validation import detect_fpcalc
                    detected = detect_fpcalc()
                    fpcalc_path = detected.path if detected.found else None

                if fpcalc_path:
                    extractor = ChromaprintExtractor(fpcalc_path=fpcalc_path)
                    fp_result = await extractor.extract(str(file_path))
                    title.chromaprint_blob = fp_result.to_blob()
                    title.chromaprint_extracted_at = datetime.now(timezone.utc)
                else:
                    logger.debug(f"fpcalc not configured; skipping chromaprint extraction for title {title.id}")
            except Exception as e:
                # Graceful degradation: matching already succeeded; log and continue
                logger.warning(f"Chromaprint extraction failed for title {title.id}: {e}", exc_info=True)
```

Make sure `logger` is in scope (it should be from existing imports at top of file).

- [ ] **Step D1.4: Run the test (expect PASS)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: PASS.

- [ ] **Step D1.5: Verify graceful degradation when fpcalc is missing**

Add another test to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_matching_succeeds_when_fpcalc_missing(integration_client, async_session, monkeypatch):
    """If fpcalc isn't configured and auto-detect fails, matching still completes — just without a fingerprint."""
    from app.api import validation as v
    from app.api.validation import ToolDetectionResult
    monkeypatch.setattr(v, "detect_fpcalc", lambda: ToolDetectionResult(found=False, path=None, error="absent"))

    response = await integration_client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "INCEPTION_2010",
            "content_type": "movie",
            "simulate_ripping": True,
        },
    )
    assert response.status_code == 200
    # Confirm at least one title was created and reached a terminal state without an exception bubbling up.
    import asyncio
    from sqlmodel import select
    from app.models.disc_job import DiscJob
    for _ in range(60):
        await asyncio.sleep(0.5)
        result = await async_session.execute(select(DiscJob))
        jobs = result.scalars().all()
        if jobs and any(j.state in ("completed", "review_needed", "matching", "organizing") for j in jobs):
            break
    else:
        pytest.fail("Job never advanced past initial state with fpcalc absent")
```

Run: `uv run pytest tests/integration/test_chromaprint_pipeline.py -v`. Expected: PASS.

- [ ] **Step D1.6: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(matching): extract chromaprint after match completes; graceful fallback"
```

---

## Cluster E: Local Contribution Queue

### Task E1: `FingerprintContribution` model

**Files:**
- Modify: `backend/app/models/disc_job.py` (add new model alongside DiscJob/DiscTitle) OR create `backend/app/models/fingerprint.py` (preferred)
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/unit/test_contribution_queue.py` (new)

- [ ] **Step E1.1: Write the failing test**

Create `backend/tests/unit/test_contribution_queue.py`:

```python
"""Tests for the local FingerprintContribution queue."""

from datetime import datetime

from app.models.fingerprint import FingerprintContribution


def test_fingerprint_contribution_has_required_fields():
    fields = FingerprintContribution.model_fields
    for required in (
        "id",
        "queued_at",
        "title_id",
        "chromaprint_blob",
        "tmdb_id",
        "season",
        "episode",
        "match_confidence",
        "match_source",
        "disc_content_hash",
        "pseudonym",
        "uploaded_at",
        "upload_attempts",
    ):
        assert required in fields, f"FingerprintContribution missing field: {required}"


def test_fingerprint_contribution_construction():
    c = FingerprintContribution(
        title_id=1,
        chromaprint_blob=b"\x00\x01",
        tmdb_id=12345,
        season=1,
        episode=7,
        match_confidence=0.92,
        match_source="engram_asr",
        disc_content_hash=b"\xab\xcd",
        pseudonym="00000000-0000-4000-8000-000000000000",
    )
    assert c.uploaded_at is None
    assert c.upload_attempts == 0
```

- [ ] **Step E1.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_contribution_queue.py -v
```
Expected: ImportError.

- [ ] **Step E1.3: Create the model**

Create `backend/app/models/fingerprint.py`:

```python
"""Models for the chromaprint fingerprint contribution queue (Phase 1: local-only)."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class FingerprintContribution(SQLModel, table=True):
    """Local-only queue row.

    Phase 1: rows are appended on successful match. They never leave the local
    machine. Phase 2 adds a `ContributionUploader` service that drains this table
    over HTTPS to the fingerprint network server.
    """

    __tablename__ = "fingerprint_contributions"

    id: int | None = Field(default=None, primary_key=True)
    queued_at: datetime = Field(default_factory=datetime.utcnow)

    # Nullable so bootstrap rows (no DiscTitle row) can also be queued.
    title_id: int | None = Field(default=None, foreign_key="disc_titles.id", index=True)
    chromaprint_blob: bytes

    # Episode identity (the payload-bearing fields per the Phase 2 design)
    tmdb_id: int
    season: int | None = None
    episode: int | None = None

    # Provenance for trust-tier promotion
    match_confidence: float
    match_source: str  # 'engram_asr' | 'engram_discdb' | 'bootstrap' | 'user_review'

    # Identifies a *disc release*, not the user's file (m2ts size MD5 from TheDiscDB)
    disc_content_hash: bytes | None = None

    pseudonym: str

    # Phase 1 fields, set when Phase 2 lands
    uploaded_at: datetime | None = None
    upload_attempts: int = Field(default=0)
```

- [ ] **Step E1.4: Re-export from models package**

In `backend/app/models/__init__.py`, add:

```python
from app.models.fingerprint import FingerprintContribution

__all__ = [..., "FingerprintContribution"]  # merge with existing __all__
```

- [ ] **Step E1.5: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_contribution_queue.py -v
```
Expected: PASS.

- [ ] **Step E1.6: Add Alembic migration for the new table**

```bash
uv run alembic revision -m "phase1 fingerprint_contributions table"
```
Edit the new file's `upgrade()` / `downgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "fingerprint_contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("title_id", sa.Integer(), sa.ForeignKey("disc_titles.id"), nullable=True, index=True),
        sa.Column("chromaprint_blob", sa.LargeBinary(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("match_source", sa.String(), nullable=False),
        sa.Column("disc_content_hash", sa.LargeBinary(), nullable=True),
        sa.Column("pseudonym", sa.String(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("upload_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("fingerprint_contributions")
```

Run upgrade/downgrade smoke as in Task A3.

- [ ] **Step E1.7: Commit**

```bash
git add backend/app/models/fingerprint.py backend/app/models/__init__.py backend/migrations/versions/<file>.py backend/tests/unit/test_contribution_queue.py
git commit -m "feat(models): FingerprintContribution local queue table"
```

### Task E2: `ContributionQueue.enqueue()` service

**Files:**
- Create: `backend/app/services/contribution_queue.py`
- Test: extend `backend/tests/unit/test_contribution_queue.py`

- [ ] **Step E2.1: Write the failing test**

Append to `backend/tests/unit/test_contribution_queue.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.contribution_queue import ContributionQueue


@pytest.mark.asyncio
async def test_enqueue_persists_row():
    """enqueue() inserts a FingerprintContribution row with the supplied fields."""
    session = AsyncMock()
    session.add = MagicMock()
    queue = ContributionQueue()
    await queue.enqueue(
        session=session,
        title_id=42,
        chromaprint_blob=b"\xde\xad",
        tmdb_id=1399,
        season=1,
        episode=1,
        match_confidence=0.91,
        match_source="engram_asr",
        disc_content_hash=b"\x12\x34",
        pseudonym="11111111-1111-4111-8111-111111111111",
    )
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.title_id == 42
    assert added.match_source == "engram_asr"


@pytest.mark.asyncio
async def test_enqueue_respects_opt_out():
    """If enable_fingerprint_contributions=False, enqueue is a no-op."""
    session = AsyncMock()
    session.add = MagicMock()
    queue = ContributionQueue()
    await queue.enqueue(
        session=session,
        title_id=1,
        chromaprint_blob=b"x",
        tmdb_id=1,
        season=1,
        episode=1,
        match_confidence=0.9,
        match_source="engram_asr",
        disc_content_hash=None,
        pseudonym="11111111-1111-4111-8111-111111111111",
        contributions_enabled=False,
    )
    session.add.assert_not_called()
```

- [ ] **Step E2.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_contribution_queue.py -v
```
Expected: ImportError.

- [ ] **Step E2.3: Create the service**

Create `backend/app/services/contribution_queue.py`:

```python
"""Local-only fingerprint contribution queue (Phase 1).

Phase 2 adds an uploader that drains this queue over HTTPS. For Phase 1 the queue
is append-only and never uploads anything — it exists so that contributions are
captured from day one, ready to flow when the server lands.
"""

from __future__ import annotations

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.fingerprint import FingerprintContribution


class ContributionQueue:
    """Append rows to the local FingerprintContribution table."""

    async def enqueue(
        self,
        *,
        session: AsyncSession,
        title_id: int,
        chromaprint_blob: bytes,
        tmdb_id: int,
        season: int | None,
        episode: int | None,
        match_confidence: float,
        match_source: str,
        disc_content_hash: bytes | None,
        pseudonym: str,
        contributions_enabled: bool = True,
    ) -> None:
        """Append a contribution if the user has opt-in (default True)."""
        if not contributions_enabled:
            logger.debug(f"Skipping contribution for title {title_id}: contributions disabled")
            return
        row = FingerprintContribution(
            title_id=title_id,
            chromaprint_blob=chromaprint_blob,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            match_confidence=match_confidence,
            match_source=match_source,
            disc_content_hash=disc_content_hash,
            pseudonym=pseudonym,
        )
        session.add(row)
        logger.info(
            f"Queued contribution for title {title_id} (tmdb={tmdb_id} s{season}e{episode}, "
            f"source={match_source}, conf={match_confidence:.2f})"
        )
```

- [ ] **Step E2.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_contribution_queue.py -v
```
Expected: PASS.

- [ ] **Step E2.5: Commit**

```bash
git add backend/app/services/contribution_queue.py backend/tests/unit/test_contribution_queue.py
git commit -m "feat(services): ContributionQueue local-only enqueue"
```

### Task E3: Enqueue on successful match

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`
- Test: extend `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step E3.1: Write the failing test**

Append to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_contribution_enqueued_on_match(integration_client, async_session, monkeypatch):
    """A successful match enqueues a FingerprintContribution row."""
    from app.matcher.chromaprint_extractor import ChromaprintResult

    fake_result = ChromaprintResult(hashes=[1], duration_seconds=10.0, fpcalc_version="test")
    monkeypatch.setattr(
        "app.matcher.chromaprint_extractor.ChromaprintExtractor.extract",
        lambda self, p: fake_result,
    )

    response = await integration_client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        },
    )
    assert response.status_code == 200

    import asyncio
    from sqlmodel import select
    from app.models.fingerprint import FingerprintContribution
    for _ in range(60):
        await asyncio.sleep(0.5)
        result = await async_session.execute(select(FingerprintContribution))
        rows = result.scalars().all()
        if rows:
            break
    else:
        pytest.fail("No FingerprintContribution row was queued within 30s")

    assert rows[0].chromaprint_blob is not None
    assert rows[0].pseudonym  # non-empty
```

- [ ] **Step E3.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py::test_contribution_enqueued_on_match -v
```
Expected: FAIL — queue isn't wired up yet.

- [ ] **Step E3.3: Wire `ContributionQueue` into the matching coordinator**

In `backend/app/services/matching_coordinator.py`, immediately after the chromaprint extraction block (added in Task D1), add:

```python
            # Phase 1: enqueue contribution if the title is identified
            if title.chromaprint_blob and title.matched_episode and cfg.contribution_pseudonym:
                try:
                    from app.services.contribution_queue import ContributionQueue
                    # Parse "S01E07" → (season, episode)
                    import re
                    m = re.match(r"S(\d{1,2})E(\d{1,3})", title.matched_episode or "")
                    season = int(m.group(1)) if m else None
                    episode_num = int(m.group(2)) if m else None
                    await ContributionQueue().enqueue(
                        session=session,
                        title_id=title.id,
                        chromaprint_blob=title.chromaprint_blob,
                        tmdb_id=int(job.tmdb_id) if getattr(job, "tmdb_id", None) else 0,
                        season=season,
                        episode=episode_num,
                        match_confidence=float(title.match_confidence or 0.0),
                        match_source=title.match_source or "engram_asr",
                        disc_content_hash=bytes.fromhex(job.content_hash) if getattr(job, "content_hash", None) else None,
                        pseudonym=cfg.contribution_pseudonym,
                        contributions_enabled=cfg.enable_fingerprint_contributions,
                    )
                except Exception as e:
                    logger.warning(f"Failed to enqueue contribution for title {title.id}: {e}", exc_info=True)
```

- [ ] **Step E3.4: Run the test (expect PASS)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: all PASS.

- [ ] **Step E3.5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(matching): enqueue local contribution after successful match"
```

---

## Cluster F: Bootstrap CLI

### Task F1: Filename parser + scanner

**Files:**
- Create: `backend/app/scripts/__init__.py` (empty if not present)
- Create: `backend/app/scripts/bootstrap_library.py`
- Test: `backend/tests/unit/test_bootstrap_library.py`

- [ ] **Step F1.1: Write the failing test**

Create `backend/tests/unit/test_bootstrap_library.py`:

```python
"""Tests for the bootstrap-library CLI utility."""

from pathlib import Path

from app.scripts.bootstrap_library import parse_episode_filename, walk_library


def test_parse_episode_filename_standard():
    assert parse_episode_filename("Arrested Development - S01E07.mkv") == ("Arrested Development", 1, 7)
    assert parse_episode_filename("The Gilded Age - S03E08.mkv") == ("The Gilded Age", 3, 8)
    assert parse_episode_filename("Star Trek The Next Generation - S07E09.mkv") == (
        "Star Trek The Next Generation",
        7,
        9,
    )


def test_parse_episode_filename_rejects_garbage():
    assert parse_episode_filename("movie.mkv") is None
    assert parse_episode_filename("Show.mkv") is None
    assert parse_episode_filename("Show - 1x07.mkv") is None  # only the canonical SxxExx form


def test_walk_library_skips_extras(tmp_path):
    show = tmp_path / "Foo"
    season = show / "Season 1"
    season.mkdir(parents=True)
    extras = season / "Extras"
    extras.mkdir()
    (season / "Foo - S01E01.mkv").touch()
    (season / "Foo - S01E02.mkv").touch()
    (extras / "Foo Extra t00.mkv").touch()  # should be ignored

    found = list(walk_library(tmp_path))
    names = sorted(p.name for p, _ in found)
    assert names == ["Foo - S01E01.mkv", "Foo - S01E02.mkv"]
```

- [ ] **Step F1.2: Run the tests (expect FAIL)**

```bash
uv run pytest tests/unit/test_bootstrap_library.py -v
```
Expected: ImportError.

- [ ] **Step F1.3: Implement the module**

Create `backend/app/scripts/bootstrap_library.py`:

```python
"""Bootstrap-library CLI.

Walks a directory of MKVs labeled `Show - SnnEnn.mkv`, fingerprints each one,
and enqueues a FingerprintContribution row tagged `match_source="bootstrap"`.

Usage:
  uv run python -m app.scripts.bootstrap_library /path/to/library [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

EP_REGEX = re.compile(
    r"(?P<show>.+?)\s*-\s*S(?P<season>\d{1,2})E(?P<ep>\d{1,3})\s*\.\w+$",
    re.IGNORECASE,
)


def parse_episode_filename(name: str) -> tuple[str, int, int] | None:
    """Return (show, season, episode) for canonical 'Show - SnnEnn.ext' names, else None."""
    m = EP_REGEX.match(name)
    if not m:
        return None
    return m["show"].strip(), int(m["season"]), int(m["ep"])


def walk_library(root: Path) -> Iterable[tuple[Path, tuple[str, int, int]]]:
    """Yield (file_path, (show, season, episode)) for every labeled MKV under root, skipping Extras."""
    for mkv in sorted(root.rglob("*.mkv")):
        if "Extras" in mkv.parts:
            continue
        label = parse_episode_filename(mkv.name)
        if label is None:
            continue
        yield mkv, label
```

- [ ] **Step F1.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_bootstrap_library.py -v
```
Expected: PASS.

- [ ] **Step F1.5: Commit**

```bash
git add backend/app/scripts/__init__.py backend/app/scripts/bootstrap_library.py backend/tests/unit/test_bootstrap_library.py
git commit -m "feat(scripts): bootstrap-library filename parser + walker"
```

### Task F2: TMDB resolution + extraction loop

**Files:**
- Modify: `backend/app/scripts/bootstrap_library.py`
- Test: extend `backend/tests/unit/test_bootstrap_library.py`

- [ ] **Step F2.1: Write the failing test**

Append to `backend/tests/unit/test_bootstrap_library.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.scripts.bootstrap_library import resolve_tmdb_id


@pytest.mark.asyncio
async def test_resolve_tmdb_id_caches_per_show():
    """resolve_tmdb_id calls the TMDB client at most once per (show, content_type)."""
    calls = []

    async def fake_search(name, content_type):
        calls.append(name)
        return 12345

    cache: dict[tuple[str, str], int] = {}
    a = await resolve_tmdb_id("Foo", "tv", search_fn=fake_search, cache=cache)
    b = await resolve_tmdb_id("Foo", "tv", search_fn=fake_search, cache=cache)
    c = await resolve_tmdb_id("Foo", "tv", search_fn=fake_search, cache=cache)
    assert a == b == c == 12345
    assert calls == ["Foo"]  # only one upstream call
```

- [ ] **Step F2.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/unit/test_bootstrap_library.py::test_resolve_tmdb_id_caches_per_show -v
```
Expected: ImportError.

- [ ] **Step F2.3: Implement TMDB resolution + main loop**

Extend `backend/app/scripts/bootstrap_library.py`:

```python
from typing import Awaitable, Callable


SearchFn = Callable[[str, str], Awaitable[int | None]]


async def resolve_tmdb_id(
    show: str,
    content_type: str,
    *,
    search_fn: SearchFn,
    cache: dict[tuple[str, str], int],
) -> int | None:
    """Resolve a show name to a TMDB ID with an in-memory cache."""
    key = (show, content_type)
    if key in cache:
        return cache[key]
    result = await search_fn(show, content_type)
    if result is not None:
        cache[key] = result
    return result


async def _default_search(show: str, content_type: str) -> int | None:
    """Use the existing TMDB classifier to resolve a name → ID."""
    from app.core.tmdb_classifier import TmdbClassifier
    classifier = TmdbClassifier()
    res = await classifier.search_show(show) if content_type == "tv" else await classifier.search_movie(show)
    return res.tmdb_id if res else None


async def bootstrap_directory(
    root: Path,
    *,
    dry_run: bool = False,
    fpcalc_path: str | None = None,
) -> dict[str, int]:
    """Walk `root`, extract+enqueue contributions, return summary counts."""
    from app.database import async_session
    from app.matcher.chromaprint_extractor import ChromaprintExtractor
    from app.services.contribution_queue import ContributionQueue
    from app.services.config_service import get_config

    counters = {"scanned": 0, "skipped": 0, "extracted": 0, "queued": 0, "errors": 0}
    cache: dict[tuple[str, str], int] = {}

    if fpcalc_path is None:
        from app.api.validation import detect_fpcalc
        detected = detect_fpcalc()
        fpcalc_path = detected.path if detected.found else None
    if not fpcalc_path:
        logger.error("fpcalc not configured; cannot bootstrap")
        return counters

    extractor = ChromaprintExtractor(fpcalc_path=fpcalc_path)

    async with async_session() as session:
        cfg = await get_config(session)
        pseudonym = cfg.contribution_pseudonym
        if not pseudonym:
            logger.error("contribution_pseudonym not set; start the app once before bootstrapping")
            return counters

        for path, (show, season, episode) in walk_library(root):
            counters["scanned"] += 1
            tmdb_id = await resolve_tmdb_id(show, "tv", search_fn=_default_search, cache=cache)
            if tmdb_id is None:
                logger.warning(f"Could not resolve TMDB ID for {show!r}; skipping")
                counters["skipped"] += 1
                continue

            try:
                fp = await extractor.extract(str(path))
                counters["extracted"] += 1
            except Exception as e:
                logger.error(f"fpcalc failed on {path}: {e}")
                counters["errors"] += 1
                continue

            if dry_run:
                logger.info(f"[dry-run] would queue {show} s{season}e{episode} ({len(fp.hashes)} hashes)")
                continue

            await ContributionQueue().enqueue(
                session=session,
                title_id=None,  # bootstrap contributions are unmoored from any DiscTitle row
                chromaprint_blob=fp.to_blob(),
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                match_confidence=1.0,  # filename was the source of truth
                match_source="bootstrap",
                disc_content_hash=None,
                pseudonym=pseudonym,
                contributions_enabled=cfg.enable_fingerprint_contributions,
            )
            counters["queued"] += 1
        await session.commit()

    return counters


def _main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap chromaprint contributions from an existing library")
    parser.add_argument("library_root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fpcalc", type=str, default=None, help="Override fpcalc binary path")
    args = parser.parse_args()

    counters = asyncio.run(bootstrap_directory(args.library_root, dry_run=args.dry_run, fpcalc_path=args.fpcalc))
    logger.info(f"Bootstrap done: {counters}")
    return 0 if counters["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
```

Note: bootstrap rows pass `title_id=None` because they have no corresponding `DiscTitle` row. Task E1 already declared `title_id` as nullable for exactly this case.

- [ ] **Step F2.4: Run the tests (expect PASS)**

```bash
uv run pytest tests/unit/test_bootstrap_library.py -v
```
Expected: all PASS.

- [ ] **Step F2.5: Smoke-test the CLI**

```bash
cd backend
uv run python -m app.scripts.bootstrap_library "C:/Users/jonat/Engram/TV" --dry-run
```
Expected: log lines for each labeled MKV under that path; no DB writes; final summary shows `queued=0, extracted>=16, errors=0`.

- [ ] **Step F2.6: Commit**

```bash
git add backend/app/scripts/bootstrap_library.py backend/tests/unit/test_bootstrap_library.py
git commit -m "feat(scripts): bootstrap-library CLI with TMDB resolution + dry-run"
```

---

## Cluster G: Opt-out UI

### Task G1: Frontend toggle in ConfigWizard

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step G1.1: Add to `ConfigData` interface**

In `frontend/src/components/ConfigWizard.tsx`, find the `ConfigData` interface (around line 49). Add:

```typescript
    enableFingerprintContributions: boolean;
```

- [ ] **Step G1.2: Initialize in default state**

Find the `useState` initializer (around line 112). Add:

```typescript
    enableFingerprintContributions: true,
```

- [ ] **Step G1.3: Load from API**

Find the GET `/api/config` response handler (around line 217). Add:

```typescript
    enableFingerprintContributions: data.enable_fingerprint_contributions ?? true,
```

- [ ] **Step G1.4: Save to API**

Find the PUT `/api/config` payload (around line 328). Add:

```typescript
    enable_fingerprint_contributions: config.enableFingerprintContributions,
```

- [ ] **Step G1.5: Render the toggle**

In the Preferences step (around line 820, near existing toggles), add:

```tsx
<div className="form-group checkbox-group">
    <label className="checkbox-label">
        <input
            type="checkbox"
            checked={config.enableFingerprintContributions}
            onChange={(e) => handleInputChange('enableFingerprintContributions', e.target.checked)}
        />
        <span className="checkbox-text">
            <strong>Contribute audio fingerprints</strong>
            <span className="checkbox-hint">
                Engram extracts a perceptual audio fingerprint from each ripped title and queues it
                locally. Future versions will share these fingerprints with a community catalog so
                everyone's rips identify faster. No filenames, paths, or personally identifying
                information are sent. Disable to skip extraction entirely.
            </span>
        </span>
    </label>
</div>
```

- [ ] **Step G1.6: Run lint + typecheck**

```bash
cd frontend
npm run lint
npm run build
```
Expected: clean.

- [ ] **Step G1.7: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(ui): fingerprint contributions opt-out toggle in ConfigWizard"
```

### Task G2: E2E test for the toggle

**Files:**
- Create: `frontend/e2e/fingerprint-toggle.spec.ts`

- [ ] **Step G2.1: Write the test**

Create `frontend/e2e/fingerprint-toggle.spec.ts`:

```typescript
import { test, expect } from '@playwright/test';

test('fingerprint contributions toggle round-trips through ConfigWizard', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /settings|configure/i }).click();

    // Navigate to the Preferences step
    while (!(await page.getByText('Contribute audio fingerprints').isVisible().catch(() => false))) {
        const next = page.getByRole('button', { name: /next/i });
        if (!(await next.isVisible())) break;
        await next.click();
    }

    const toggle = page.locator('input[type=checkbox]').filter({ hasText: /audio fingerprint/i }).first();
    // Default = on
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    // Save
    await page.getByRole('button', { name: /save|done/i }).click();

    // Reload + verify persistence
    await page.reload();
    await page.getByRole('button', { name: /settings|configure/i }).click();
    while (!(await page.getByText('Contribute audio fingerprints').isVisible().catch(() => false))) {
        const next = page.getByRole('button', { name: /next/i });
        if (!(await next.isVisible())) break;
        await next.click();
    }
    const reloaded = page.locator('input[type=checkbox]').filter({ hasText: /audio fingerprint/i }).first();
    await expect(reloaded).not.toBeChecked();
});
```

- [ ] **Step G2.2: Run the test (expect PASS — backend must be running with DEBUG=true)**

```bash
cd frontend
npm run test:e2e -- fingerprint-toggle
```
Expected: PASS. If failures, inspect with `npm run test:e2e:ui`.

- [ ] **Step G2.3: Commit**

```bash
git add frontend/e2e/fingerprint-toggle.spec.ts
git commit -m "test(e2e): fingerprint contributions toggle persists"
```

---

## Cluster H: Local Audit Log API

### Task H1: `GET /api/fingerprint/contributions` endpoint

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: extend `backend/tests/integration/test_chromaprint_pipeline.py`

- [ ] **Step H1.1: Write the failing test**

Append to `backend/tests/integration/test_chromaprint_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_get_fingerprint_contributions(integration_client, async_session):
    """GET /api/fingerprint/contributions returns the local queue, redacted of blobs by default."""
    from app.models.fingerprint import FingerprintContribution
    from datetime import datetime

    row = FingerprintContribution(
        title_id=None,
        chromaprint_blob=b"\x00" * 1000,
        tmdb_id=1399,
        season=1,
        episode=1,
        match_confidence=0.95,
        match_source="bootstrap",
        pseudonym="22222222-2222-4222-8222-222222222222",
    )
    async_session.add(row)
    await async_session.commit()

    response = await integration_client.get("/api/fingerprint/contributions")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    item = next(i for i in data["items"] if i["tmdb_id"] == 1399)
    assert item["match_source"] == "bootstrap"
    # Blob should be summarized (size) not returned wholesale
    assert "chromaprint_blob" not in item
    assert item["blob_size_bytes"] == 1000
```

- [ ] **Step H1.2: Run the test (expect FAIL)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py::test_get_fingerprint_contributions -v
```
Expected: 404 — endpoint doesn't exist.

- [ ] **Step H1.3: Add the endpoint**

In `backend/app/api/routes.py`, add (next to existing job endpoints):

```python
@router.get("/fingerprint/contributions")
async def list_fingerprint_contributions(
    session: AsyncSession = Depends(get_session),
    limit: int = 200,
) -> dict:
    """Return locally-queued fingerprint contributions (Phase 1 audit log).

    Excludes the chromaprint blob body — only summarizes size — so the response stays
    manageable. Phase 2 will add filtering by upload status.
    """
    from app.models.fingerprint import FingerprintContribution
    from sqlmodel import select

    result = await session.execute(
        select(FingerprintContribution)
        .order_by(FingerprintContribution.queued_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    items = [
        {
            "id": r.id,
            "queued_at": r.queued_at.isoformat(),
            "title_id": r.title_id,
            "tmdb_id": r.tmdb_id,
            "season": r.season,
            "episode": r.episode,
            "match_confidence": r.match_confidence,
            "match_source": r.match_source,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "upload_attempts": r.upload_attempts,
            "blob_size_bytes": len(r.chromaprint_blob) if r.chromaprint_blob else 0,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}
```

- [ ] **Step H1.4: Run the test (expect PASS)**

```bash
uv run pytest tests/integration/test_chromaprint_pipeline.py -v
```
Expected: all PASS.

- [ ] **Step H1.5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_chromaprint_pipeline.py
git commit -m "feat(api): GET /api/fingerprint/contributions audit-log endpoint"
```

---

## Wrap-up

### Final verification

- [ ] **Step W1: Run the full backend test suite**

```bash
cd backend
uv run pytest -x --tb=short 2>&1 | tail -20
```
Expected: all green (or same baseline failures noted in Step 0.2).

- [ ] **Step W2: Run lint + format**

```bash
uv run ruff check .
uv run ruff format --check .
```
Fix anything ruff flags, then re-commit if needed.

- [ ] **Step W3: Run the frontend build + lint**

```bash
cd ../frontend
npm run lint
npm run build
```
Expected: clean.

- [ ] **Step W4: Manual end-to-end smoke test (against the real fpcalc + a real MKV)**

Stop any running Engram backend. Start a fresh dev backend pointed at a scratch DB:

```bash
cd backend
DATABASE_URL="sqlite+aiosqlite:///./scratch_phase1.db" DEBUG=true uv run uvicorn app.main:app --port 8001
```

In a separate terminal:
```bash
# Insert a simulated TV disc
curl -X POST localhost:8001/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D1","content_type":"tv","simulate_ripping":true}'

# After ~10 seconds, list contributions
curl -s localhost:8001/api/fingerprint/contributions | python -m json.tool | head -30
```
Expected: at least one contribution row with `match_source` set, non-zero `blob_size_bytes`, and a valid pseudonym in the DB.

Clean up:
```bash
rm scratch_phase1.db scratch_phase1.db-*
```

- [ ] **Step W5: Commit any final fixups**

```bash
git status
# If any small lint/test fixes were needed:
git add -p
git commit -m "chore: phase1 wrap-up fixups"
```

### Phase 1 Done — what's not done

Documented here so Phase 2/3 planners have explicit context:

- **No server-side anything.** Contributions sit in the local `fingerprint_contributions` table indefinitely. Phase 2 adds `POST /v1/contribute` upload.
- **No identification path.** Chromaprints are stored but never queried. Phase 3 adds the matcher integration.
- **`title_id=0` sentinel for bootstrap contributions.** A real foreign key relationship would force Phase 1 to invent a `DiscTitle` for each bootstrap file, which is wasted work. Phase 2's upload schema doesn't care about local `title_id`. If lint/static-checking complains, make `title_id` nullable in the model + a follow-up migration.
- **No `/v1/forget` equivalent locally.** Privacy promise is "nothing leaves the machine"; Phase 1 needs no forget endpoint. Phase 2 adds it.
- **No JSONL audit log at `~/.engram/cache/contribution_log.jsonl`.** The Phase 2 design wants users to see "exactly what was sent." Since Phase 1 sends nothing, the DB-backed `GET /api/fingerprint/contributions` covers the "exactly what was queued" need. The JSONL file lands alongside the Phase 2 uploader.
- **No first-run privacy disclosure.** The opt-out toggle exists in ConfigWizard but isn't a forced first-run screen. Phase 2 design adds the disclosure UX.
- **No real-data integration test against a duplicate rip.** The spike (`spikes/chromaprint/spike.py`) proved the algorithm on the same MKV; we still don't have evidence that a re-rip of the same disc matches itself. Capture two rips and replay the spike before Phase 3 ships.
