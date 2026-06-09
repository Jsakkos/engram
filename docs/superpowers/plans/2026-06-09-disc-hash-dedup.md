# Disc ContentHash Tracking + Hash-Based Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute and persist a per-disc ContentHash at disc-insert time and use it to stop a same-labelled next disc (e.g. Breaking Bad S2 Disc 2 while Disc 1 is still MATCHING) from being rejected as a duplicate.

**Architecture:** `JobManager._create_job_for_disc` computes the disc fingerprint (the existing `compute_content_hash`, with a short bounded retry) inside its drive lock, stores it on the new `DiscJob`, and the dedup blocking predicate uses a new `_same_disc` helper that prefers hash identity over the volume label. The label comparison stays only as the conservative fallback when a hash is unavailable. Identification then reuses the insert-time hash instead of recomputing it inside the DiscDB-gated block.

**Tech Stack:** Python 3.11, FastAPI, async SQLModel/SQLite, pytest (`uv run pytest`), `unittest.mock`.

**Design spec:** `docs/superpowers/specs/2026-06-09-disc-hash-dedup-design.md` (real-disc assumptions validated 2026-06-09: hash ready `+0.00s` after mount; two same-label discs → different hashes `E3A6…4D71`/`8FACC…439A`; hash stable across reinserts).

---

## Background — why this is needed

`JobManager._create_job_for_disc` (`backend/app/services/job_manager.py:439`) blocks a new disc-insert event when an in-flight job already occupies the drive. For **post-eject** states (`MATCHING`/`ORGANIZING`) the disc has been ejected, so it blocks only when the new disc's `volume_label` equals the in-flight job's (guarding against a glitchy eject/reinsert that would spawn a duplicate job). Because every disc in a season shares one label (`BREAKINGBADS2`), inserting Disc 2 while Disc 1 is MATCHING collides on the label and Disc 2 is dropped. The disc's ContentHash is a true per-disc fingerprint (already implemented as `compute_content_hash`, `backend/app/core/extractor.py:147`, and the `DiscJob.content_hash` column) but is only computed inside the DiscDB-gated block in identification, so it is null in normal operation.

---

## File Structure

- Modify: `backend/app/services/job_manager.py`
  - Add `compute_content_hash` to the `app.core.extractor` import.
  - Add two module constants (`_DISC_HASH_RETRY_ATTEMPTS`, `_DISC_HASH_RETRY_DELAY`).
  - Add `JobManager._compute_disc_hash` (async retry wrapper) and `JobManager._same_disc` (pure staticmethod).
  - Wire both into `_create_job_for_disc` (compute → store on job → use in the blocking predicate).
- Modify: `backend/app/services/identification_coordinator.py:1093-1100`
  - Reuse `job.content_hash` (set at insert) instead of recomputing; fall back to a fresh compute only if it is None.
- Test: `backend/tests/unit/test_disc_hash_dedup.py` (new).
- Modify: `CHANGELOG.md`.

---

## Task 1: Hash + same-disc helpers on `JobManager`

**Files:**
- Modify: `backend/app/services/job_manager.py` (import line ~22; constants after imports ~line 46; two new methods)
- Test: `backend/tests/unit/test_disc_hash_dedup.py` (create)

- [ ] **Step 1: Add the import and constants**

In `backend/app/services/job_manager.py`, change the extractor import (currently line 22):

```python
from app.core.extractor import STALL_FAILURE_REASON, MakeMKVExtractor
```

to:

```python
from app.core.extractor import (
    STALL_FAILURE_REASON,
    MakeMKVExtractor,
    compute_content_hash,
)
```

Then add these module-level constants immediately after the imports block (after the `from app.services.simulation_service import SimulationService` line, before the first class/definition):

```python
# Per-disc ContentHash is computable the instant a disc mounts (validated on
# real hardware: +0.00s after mount, 4/4 inserts). The retry exists only for a
# cold disc that is mounted-but-not-yet-readable and is essentially never used.
_DISC_HASH_RETRY_ATTEMPTS = 3
_DISC_HASH_RETRY_DELAY = 0.5  # seconds between attempts
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/unit/test_disc_hash_dedup.py`:

```python
"""Unit tests for per-disc ContentHash dedup in JobManager.

Covers the pure _same_disc discriminator, the _compute_disc_hash retry wrapper,
and _create_job_for_disc's blocking decision: a same-labelled disc with a
DIFFERENT hash must be allowed through (the Breaking Bad S2 D1-vs-D2 bug),
while the same hash (or an unreadable hash) stays blocked.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

jm_mod = importlib.import_module("app.services.job_manager")


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


# --- _same_disc (pure) -----------------------------------------------------

def _job(content_hash, volume_label):
    return SimpleNamespace(content_hash=content_hash, volume_label=volume_label)


def test_same_disc_true_when_hashes_match():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "AAAA") is True


def test_same_disc_false_when_hashes_differ():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is False


def test_same_disc_falls_back_to_label_when_new_hash_missing():
    job = _job("AAAA", "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", None) is True
    assert job_manager._same_disc(job, "OTHERLABEL", None) is False


def test_same_disc_falls_back_to_label_when_job_hash_missing():
    job = _job(None, "BREAKINGBADS2")
    assert job_manager._same_disc(job, "BREAKINGBADS2", "BBBB") is True
    assert job_manager._same_disc(job, "OTHERLABEL", "BBBB") is False


# --- _compute_disc_hash (retry) -------------------------------------------

@pytest.mark.asyncio
async def test_compute_disc_hash_returns_first_success(monkeypatch):
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "DEADBEEF")
    assert await job_manager._compute_disc_hash("F:") == "DEADBEEF"


@pytest.mark.asyncio
async def test_compute_disc_hash_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    calls = {"n": 0}

    def flaky(drive):
        calls["n"] += 1
        return None if calls["n"] < 2 else "CAFE"

    monkeypatch.setattr(jm_mod, "compute_content_hash", flaky)
    assert await job_manager._compute_disc_hash("F:") == "CAFE"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_compute_disc_hash_returns_none_when_never_ready(monkeypatch):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: None)
    assert await job_manager._compute_disc_hash("F:") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_disc_hash_dedup.py -v`
Expected: FAIL — `AttributeError: 'JobManager' object has no attribute '_same_disc'` / `_compute_disc_hash`.

- [ ] **Step 4: Implement the two methods**

Add both methods to the `JobManager` class in `backend/app/services/job_manager.py`, immediately above `_create_job_for_disc` (line 439):

```python
    async def _compute_disc_hash(self, drive_letter: str) -> str | None:
        """Best-effort per-disc ContentHash at insert time.

        Retries briefly to cover a disc that is mounted but not yet fully
        readable; real-disc testing showed the hash is ready ~instantly after
        mount, so the retry is rarely exercised. Runs off-thread so the disc
        I/O never blocks the event loop.
        """
        for attempt in range(_DISC_HASH_RETRY_ATTEMPTS):
            content_hash = await asyncio.to_thread(compute_content_hash, drive_letter)
            if content_hash:
                return content_hash
            if attempt < _DISC_HASH_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_DISC_HASH_RETRY_DELAY)
        return None

    @staticmethod
    def _same_disc(job: DiscJob, volume_label: str, new_hash: str | None) -> bool:
        """True if `job` is the same physical disc as the one just inserted.

        Prefers the per-disc ContentHash (a different hash means a different
        disc). Falls back to volume-label equality when either fingerprint is
        absent — conservative: a same-labelled disc with no readable hash is
        treated as the same disc, so we never spawn a duplicate job.
        """
        if new_hash and job.content_hash:
            return job.content_hash == new_hash
        return job.volume_label == volume_label
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_disc_hash_dedup.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_disc_hash_dedup.py
git commit -m "feat(dedup): add per-disc ContentHash + _same_disc discriminator to JobManager"
```

---

## Task 2: Use the hash in `_create_job_for_disc`

**Files:**
- Modify: `backend/app/services/job_manager.py:444-514` (compute hash; use `_same_disc`; store on job)
- Test: `backend/tests/unit/test_disc_hash_dedup.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_disc_hash_dedup.py`:

```python
# --- _create_job_for_disc dedup -------------------------------------------

async def _seed_active_job(*, drive, label, state, content_hash):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id=drive,
            volume_label=label,
            state=state,
            content_hash=content_hash,
            staging_path="/tmp/seed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _jobs_on_drive(drive):
    async with _unit_session_factory() as session:
        res = await session.execute(select(DiscJob).where(DiscJob.drive_id == drive))
        return res.scalars().all()


@pytest.fixture
def _isolate_create(monkeypatch):
    """Stub the identification spawn + config so _create_job_for_disc is unit-isolated."""
    monkeypatch.setattr(
        job_manager._identification, "identify_disc", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(job_manager, "_on_task_done", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(staging_path="/tmp/staging")),
    )
    job_manager._drive_locks.clear()
    job_manager._last_job_created_at.clear()
    job_manager._active_jobs.clear()


@pytest.mark.asyncio
async def test_same_label_different_hash_creates_new_job(monkeypatch, _isolate_create):
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "BBBB")
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    jobs = await _jobs_on_drive("F:")
    assert len(jobs) == 2  # the genuinely-new Disc 2 was allowed through
    new = [j for j in jobs if j.state == JobState.IDENTIFYING]
    assert len(new) == 1 and new[0].content_hash == "BBBB"


@pytest.mark.asyncio
async def test_same_label_same_hash_blocks(monkeypatch, _isolate_create):
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: "AAAA")
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    assert len(await _jobs_on_drive("F:")) == 1  # same disc lingering → blocked


@pytest.mark.asyncio
async def test_null_hash_same_label_blocks(monkeypatch, _isolate_create):
    monkeypatch.setattr(jm_mod, "_DISC_HASH_RETRY_DELAY", 0.0)
    await _seed_active_job(
        drive="F:", label="BREAKINGBADS2", state=JobState.MATCHING, content_hash="AAAA"
    )
    monkeypatch.setattr(jm_mod, "compute_content_hash", lambda drive: None)
    await job_manager._create_job_for_disc("F:", "BREAKINGBADS2")
    assert len(await _jobs_on_drive("F:")) == 1  # conservative fallback to label
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend; uv run pytest tests/unit/test_disc_hash_dedup.py -k create -v`
Expected: FAIL — `test_same_label_different_hash_creates_new_job` fails (only 1 job; the current label-only predicate blocks Disc 2). The hash is also not yet stored, so the `content_hash == "BBBB"` assertion fails.

- [ ] **Step 3: Wire the hash into `_create_job_for_disc`**

In `backend/app/services/job_manager.py`, inside `_create_job_for_disc`, compute the hash right after acquiring the drive lock and before opening the session. Change the start of the locked block (currently line 444-445):

```python
        async with self._drive_locks[drive_letter]:
            async with async_session() as session:
```

to:

```python
        async with self._drive_locks[drive_letter]:
            # Fingerprint the inserted disc so dedup can tell two same-labelled
            # discs apart (e.g. season Disc 1 vs Disc 2 both 'BREAKINGBADS2').
            new_hash = await self._compute_disc_hash(drive_letter)
            async with async_session() as session:
```

Then change the blocking predicate (currently lines 472-480):

```python
                blocking_job = next(
                    (
                        j
                        for j in active_jobs
                        if j.state in disc_required_states
                        or (j.state in post_eject_states and j.volume_label == volume_label)
                    ),
                    None,
                )
```

to:

```python
                blocking_job = next(
                    (
                        j
                        for j in active_jobs
                        if j.state in disc_required_states
                        or (
                            j.state in post_eject_states
                            and self._same_disc(j, volume_label, new_hash)
                        )
                    ),
                    None,
                )
```

Finally, store the hash on the new job. Change the `DiscJob(...)` construction (currently lines 505-510):

```python
                job = DiscJob(
                    drive_id=drive_letter,
                    volume_label=volume_label,
                    staging_path=str(staging_dir),
                    state=JobState.IDENTIFYING,
                )
```

to:

```python
                job = DiscJob(
                    drive_id=drive_letter,
                    volume_label=volume_label,
                    staging_path=str(staging_dir),
                    state=JobState.IDENTIFYING,
                    content_hash=new_hash,
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/unit/test_disc_hash_dedup.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Run the job-manager + identification suites for regressions**

Run: `cd backend; uv run pytest tests/unit/test_job_manager.py tests/unit -k "job_manager or dedup or identification" -q`
Expected: no new failures.

- [ ] **Step 6: Lint**

Run: `cd backend; uv run ruff check app/services/job_manager.py tests/unit/test_disc_hash_dedup.py; uv run ruff format app/services/job_manager.py tests/unit/test_disc_hash_dedup.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_disc_hash_dedup.py
git commit -m "fix(dedup): discriminate same-label discs by ContentHash in _create_job_for_disc"
```

---

## Task 3: Reuse the insert-time hash in identification (decouple from DiscDB)

**Files:**
- Modify: `backend/app/services/identification_coordinator.py:1093-1100`

**Why:** with Task 2, every disc job already carries `content_hash` from insert, so the per-disc fingerprint is populated independently of DiscDB. This task removes the now-redundant recompute inside the DiscDB-gated block, having it reuse the insert-time value (and only recompute as a backfill if it is somehow missing).

- [ ] **Step 1: Replace the gated recompute with reuse**

In `backend/app/services/identification_coordinator.py`, replace the current block (lines 1093-1100):

```python
                if is_staging:
                    content_hash = None
                else:
                    from app.core.extractor import compute_content_hash

                    content_hash = await asyncio.to_thread(compute_content_hash, job.drive_id)
                    if content_hash:
                        job.content_hash = content_hash
```

with:

```python
                if is_staging:
                    content_hash = None
                elif job.content_hash:
                    # Set at insert (_create_job_for_disc); reuse it.
                    content_hash = job.content_hash
                else:
                    # Backfill: a job created before this change, or a cold-disc
                    # insert that missed the hash. Cheap (glob + stat).
                    from app.core.extractor import compute_content_hash

                    content_hash = await asyncio.to_thread(compute_content_hash, job.drive_id)
                    if content_hash:
                        job.content_hash = content_hash
```

- [ ] **Step 2: Lint and run the identification suite**

Run: `cd backend; uv run ruff check app/services/identification_coordinator.py; uv run pytest tests/unit -k identification -q`
Expected: clean; no new failures.

> **No new unit test for this task:** the edited block lives inside `if DISCDB_ENABLED and config.discdb_enabled:` (`identification_coordinator.py:1089`), which is gated off and not exercised by the suite. The behavior that matters for this feature — `job.content_hash` being populated for every disc job — is delivered and tested at insert in Task 2. This change only avoids a redundant recompute on the DiscDB-on path and is verified by lint + the existing identification tests still passing.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/identification_coordinator.py
git commit -m "refactor(identification): reuse insert-time ContentHash instead of recomputing"
```

---

## Task 4: Changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an Unreleased entry**

Under `## [Unreleased]` → `### Fixed`, add:

```markdown
- Inserting the next disc of a season (e.g. Disc 2) while the previous disc is still matching no longer rejects it as a duplicate. Engram now fingerprints each disc by content hash, so discs that share a volume label (common across a season's discs) are told apart. (#NNN)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note same-label disc dedup fix"
```

---

## Self-Review

**Spec coverage:**
- §3.1 Hash-at-insert → Task 1 (`_compute_disc_hash` + retry constants) + Task 2 (call it in `_create_job_for_disc`, store on `DiscJob`). ✓
- §3.2 Dedup discrimination → Task 1 (`_same_disc`) + Task 2 (predicate uses it on the post-eject branch only; `disc_required_states` branch unchanged). ✓
- §3.3 Decouple from DiscDB → satisfied primarily by insert becoming the unconditional hash source (Tasks 1-2); Task 3 removes the redundant recompute and makes identification reuse the insert-time value. ✓
- §3 Error handling: null hash → label fallback (`test_null_hash_same_label_blocks`); import/staging jobs keep `content_hash=None` (untouched path — `create_job_from_staging` is not modified); pre-existing null-hash jobs → label fallback (`_same_disc` covers it). ✓
- §3 Testing: `_same_disc` truth table + `_create_job_for_disc` dedup with mocked hashes; the note about `/api/simulate/insert-disc` bypassing `_create_job_for_disc` is honored (tests drive `_create_job_for_disc` directly, not the sim endpoint). ✓

**Placeholder scan:** none — every code step shows full code; the Task 3 "no new test" is an explicit, justified decision, not a TODO.

**Type consistency:** `_compute_disc_hash(drive_letter) -> str | None` and `_same_disc(job, volume_label, new_hash) -> bool` are defined in Task 1 and called in Task 2 with matching signatures. `compute_content_hash` is imported in Task 1 Step 1 and patched as `jm_mod.compute_content_hash` in tests. Constants `_DISC_HASH_RETRY_ATTEMPTS` / `_DISC_HASH_RETRY_DELAY` are defined in Task 1 and monkeypatched in tests. `DiscJob(content_hash=...)` matches the existing `DiscJob.content_hash` column (`models/disc_job.py:119`).

**Risk notes:**
- The Task 2 tests use the `job_manager` singleton; the `_isolate_create` fixture clears `_drive_locks`/`_last_job_created_at`/`_active_jobs` and stubs the identification spawn + `_on_task_done` so no real identification runs and there's no cross-test state bleed. The conftest `isolate_database` autouse fixture already points `job_manager.async_session` at the in-memory engine.
- This is unit-tested only (the sim endpoint bypasses `_create_job_for_disc`); real-disc behavior for the hash itself was validated separately (see spec §2). End-to-end dedup on real hardware can be spot-checked after merge by inserting Disc 2 during a Disc 1 match, but is not required to land the change.
