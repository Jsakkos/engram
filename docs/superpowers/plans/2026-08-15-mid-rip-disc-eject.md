# Mid-Rip Disc Eject Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Eject action that stops the current rip and releases the disc without cancelling the job, so cleanly-ripped tracks still match and organize while unfinished tracks park in review as re-rippable.

**Architecture:** The extractor gains an eject-abort flag that terminates MakeMKV and returns `success=True` (modelled directly on the existing `aborted_for_skip` path). `JobManager._run_ripping` forks on that flag: it skips the per-title fallback and the auto-eject block, routes every unfinished title to REVIEW with a new `rip_ejected` error code, then falls through to the normal post-rip flow. A `POST /api/jobs/{id}/eject` endpoint drives it, and an amber Eject button in the DiscCard action row calls it behind a confirmation modal.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / asyncio (backend, `uv`), React 18 + TypeScript + Vite + Motion (frontend), pytest (backend tests), vitest + React Testing Library (frontend unit), Playwright (E2E).

**Design spec:** `docs/superpowers/specs/2026-08-15-mid-rip-disc-eject-design.md`

---

## Critical constraints (read before starting)

These three facts are the ones most likely to be violated by someone unfamiliar with this codebase.

1. **Ordering:** the REVIEW routing in Task 5 MUST run before `reconcile_stuck_titles`. That function marks any selected `PENDING`/`RIPPING` title with no file as `FAILED` (`job_manager.py:1335`). Because the abort deletes the truncated in-flight file, a half-ripped track and a never-started track are both fileless and indistinguishable to it. Routing after reconcile silently produces `FAILED`, non-re-rippable tracks: the exact opposite of the feature.
2. **Error-code registration:** `"rip_ejected"` MUST be added to `RIP_FAILURE_ERROR_CODES`. An unregistered REVIEW error code gets auto-escalated and has its `match_details` overwritten by the re-match pass. Registration also grants `rerip_eligible` and `_NON_REMATCHABLE_REVIEW_ERRORS` membership for free.
3. **Sentinel:** call `_drive_monitor.notify_ejected(drive_id)` ONLY when `eject_disc()` returned `True`. After a failed eject the sentinel must stay armed so it correctly observes the disc when the user pops it by hand.

**Repo conventions:** ruff line length 100, double quotes, Python 3.11 target. Run backend commands from `backend/` with `uv run`. Never use `--reload` on uvicorn. **No em dashes in any prose you write** (code comments, docstrings, changelog, commit messages): use colons, commas, semicolons, or parentheses.

---

## File Structure

**Backend, modify:**
- `backend/app/core/extractor.py`: `RipResult.aborted_for_eject`, `_ejected_jobs`, `eject_abort()`, shared partial-file cleanup helper, eject return branch.
- `backend/app/services/matching_coordinator.py`: register `"rip_ejected"`, add `EJECTED_RIP_MESSAGE`.
- `backend/app/services/job_manager.py`: `eject_disc_for_job()`, the `_run_ripping` eject fork.
- `backend/app/api/routes.py`: `POST /jobs/{job_id}/eject`.
- `backend/app/services/simulation_service.py`: honor the eject flag in the simulated rip.

**Backend, create:**
- `backend/tests/unit/test_disc_eject.py`: extractor + job_manager + error-code unit tests.
- `backend/tests/integration/test_eject_workflow.py`: end-to-end salvage test.

**Frontend, modify:**
- `frontend/src/app/hooks/useJobManagement.ts`: `ejectJob()`.
- `frontend/src/app/components/DiscCard/ActionButtons.tsx`: `AMBER` tone, `"amber"` confirm tone, Eject button.
- `frontend/src/app/components/DiscCard.tsx`: thread `onEject` through.
- `frontend/src/app/App.tsx`: wire `ejectJob` to the card.

**Frontend, create:**
- `frontend/e2e/disc-eject.spec.ts`: E2E.

**Docs, modify:**
- `CHANGELOG.md`: `[Unreleased]` entry.
- `docs/architecture/state-machine.md`: document the eject path.

No new files are needed for the icon: `IcoEject` already exists at `frontend/src/app/components/icons/action.tsx:41` and is exported from `icons/index.ts`.

---

## Task 1: Extractor eject flag and abort

**Files:**
- Modify: `backend/app/core/extractor.py:287-304` (RipResult), `:574` (`_cancelled_jobs`), `:1376` (`cancel`)
- Test: `backend/tests/unit/test_disc_eject.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_disc_eject.py`:

```python
"""Unit tests for the mid-rip disc eject feature."""

from app.core.extractor import Extractor, RipResult


def test_rip_result_defaults_aborted_for_eject_false():
    """A normal RipResult is not an eject abort."""
    result = RipResult(success=True, output_files=[])
    assert result.aborted_for_eject is False


def test_eject_abort_marks_job_ejected_and_cancelled():
    """eject_abort flags the job in both sets so the existing rip loop breaks out.

    _cancelled_jobs is what the reader loop already checks to terminate the
    subprocess; _ejected_jobs is what the return path checks (first) to report
    an eject rather than a cancel.
    """
    extractor = Extractor()
    extractor.eject_abort(42)
    assert 42 in extractor._ejected_jobs
    assert 42 in extractor._cancelled_jobs


def test_eject_abort_is_isolated_per_job():
    """Ejecting one job must not disturb another drive's rip."""
    extractor = Extractor()
    extractor.eject_abort(1)
    assert 2 not in extractor._ejected_jobs
    assert 2 not in extractor._cancelled_jobs
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: FAIL. `AttributeError: 'RipResult' object has no attribute 'aborted_for_eject'` and `AttributeError: 'Extractor' object has no attribute 'eject_abort'`.

- [ ] **Step 3: Add the RipResult field**

In `backend/app/core/extractor.py`, in the `RipResult` dataclass, directly after the existing `aborted_for_skip` field (around line 304):

```python
    # True when a rip was terminated because the user ejected the disc from the
    # dashboard. Like aborted_for_skip this is NOT a failure: titles that
    # finished before the abort are kept and continue to matching, and the
    # caller routes every unfinished title to REVIEW as re-rippable. Unlike
    # aborted_for_skip there is no per-title fallback pass afterward, because
    # the disc is no longer in the drive.
    aborted_for_eject: bool = False
```

- [ ] **Step 4: Add the eject set and method**

In `Extractor.__init__`, directly after the `self._cancelled_jobs: set[int] = set()` line (around line 574):

```python
        # Jobs the user ejected mid-rip. Distinct from _cancelled_jobs so the
        # return path can report a salvageable eject rather than a cancel;
        # eject_abort populates BOTH so the existing reader-loop terminate
        # check needs no change.
        self._ejected_jobs: set[int] = set()
```

Then add this method directly after `cancel()` (around line 1384):

```python
    def eject_abort(self, job_id: int) -> None:
        """Stop ripping because the user ejected the disc, keeping finished titles.

        Adds the job to _ejected_jobs (read by the return path) and to
        _cancelled_jobs (read by the reader loop, which already terminates the
        subprocess and breaks out of the command loop). The caller is
        responsible for the physical eject and for routing unfinished titles
        to REVIEW.
        """
        self._ejected_jobs.add(job_id)
        self.cancel(job_id)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: PASS, 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(extractor): add eject_abort and aborted_for_eject flag"
```

---

## Task 2: Extractor eject return branch and partial cleanup

The rip loop must delete the truncated in-flight file (so matching never sees a partial `.mkv`) and return `success=True`. Both behaviors already exist for `aborted_for_skip`; this task extracts the cleanup so the two branches share it.

**Files:**
- Modify: `backend/app/core/extractor.py:1149-1180` (cleanup block), `:766` and `:1025` (state reset), `:1302-1323` (return branches)
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_disc_eject.py`:

```python
from pathlib import Path

from app.core.extractor import _delete_partials_at_abort


class _FakeCompletion:
    """Stands in for the rip's completion tracker.

    files_incomplete_at_abort normally reports files that grew since the last
    poll (still being written). The fake just returns a fixed list.
    """

    def __init__(self, incomplete: list[str]) -> None:
        self._incomplete = incomplete

    def files_incomplete_at_abort(self, sizes: dict[str, int]) -> list[str]:
        return list(self._incomplete)


def test_delete_partials_removes_growing_file_and_stub(tmp_path: Path):
    """The in-flight (growing) file and the named stub are deleted."""
    (tmp_path / "title_t00.mkv").write_bytes(b"complete")
    (tmp_path / "title_t01.mkv").write_bytes(b"partial")
    (tmp_path / "title_t02.mkv").write_bytes(b"stub")

    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["title_t01.mkv"]),
        extra_stub="title_t02.mkv",
    )

    assert (tmp_path / "title_t00.mkv").exists(), "finished title must be kept"
    assert not (tmp_path / "title_t01.mkv").exists()
    assert not (tmp_path / "title_t02.mkv").exists()


def test_delete_partials_tolerates_missing_files(tmp_path: Path):
    """A file already gone must not raise (MakeMKV may have cleaned it up)."""
    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["gone.mkv"]),
        extra_stub=None,
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: FAIL, `ImportError: cannot import name '_delete_partials_at_abort'`.

- [ ] **Step 3: Extract the cleanup helper**

In `backend/app/core/extractor.py`, add this module-level function near the other module-level helpers (directly above `class Extractor`, around line 560):

```python
def _delete_partials_at_abort(
    output_dir: Path,
    completion,  # RipCompletionTracker; untyped to avoid a forward reference
    extra_stub: str | None,
) -> None:
    """Delete the files MakeMKV was mid-write on when a pass was terminated.

    Titles that already finished are kept so the caller need not re-rip them.
    Two sources of doom: files that grew since the last completion poll (still
    being written), plus a named stub. The stub is named explicitly because
    growth detection cannot see a file that stopped growing inside the
    terminate -> wait window; it would read as finished and survive.

    Shared by the skip-abort and eject-abort paths.
    """
    with _fs_lock:
        abort_sizes: dict[str, int] = {}
        for mkv in output_dir.glob("*.mkv"):
            try:
                abort_sizes[mkv.name] = mkv.stat().st_size
            except OSError as e:
                logger.debug(f"Could not stat {mkv.name} on abort: {e}")
        doomed = completion.files_incomplete_at_abort(abort_sizes)
        if extra_stub and extra_stub not in doomed:
            doomed.append(extra_stub)
    for fname in doomed:
        try:
            (output_dir / fname).unlink()
            logger.info(f"Deleted partial file from aborted pass: {fname}")
        except FileNotFoundError:
            logger.debug(f"Partial file already gone: {fname}")
        except OSError as e:
            logger.warning(f"Failed to delete partial file {fname}: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: PASS, 5 passed.

- [ ] **Step 5: Use the helper in the existing skip branch**

Replace the body of the `if aborted_for_skip[0]:` block at `extractor.py:1149-1180`. The whole block (from `if aborted_for_skip[0]:` through the `break`) becomes:

```python
                    if aborted_for_skip[0]:
                        # The 'all' pass was terminated to honor a mid-rip skip.
                        # Delete only the title MakeMKV was actively writing plus
                        # the stub the abort stopped on, so neither is handed to
                        # matching; finished titles are kept so the caller doesn't
                        # needlessly re-rip them. The post-process force poll below
                        # then finalizes those kept titles.
                        _delete_partials_at_abort(
                            output_dir, completion, aborted_stub[0]
                        )
                        break
```

- [ ] **Step 6: Add the eject cleanup branch**

Directly after the `if aborted_for_skip[0]:` block you just replaced, add:

```python
                    if job_id in self._ejected_jobs:
                        # The user ejected the disc. Same salvage as a skip abort:
                        # drop the truncated in-flight file, keep everything that
                        # finished. No stub applies (nothing was skipped), and the
                        # caller does NOT run a per-title fallback because the disc
                        # is gone.
                        _delete_partials_at_abort(output_dir, completion, None)
                        break
```

- [ ] **Step 7: Add the eject return branch**

In `_rip_titles_impl`, the return path currently starts with the cancel check at line 1302. Insert the eject branch **immediately before** it, so an eject is never misreported as a cancel (`eject_abort` populates both sets):

```python
            if job_id in self._ejected_jobs:
                # Not a failure: the user chose this. Titles that finished are
                # returned for matching; the caller routes the rest to REVIEW as
                # re-rippable and skips the per-title fallback.
                if not output_files:
                    output_files = list(output_dir.glob("*.mkv"))
                logger.info(
                    f"Job {job_id}: rip stopped for disc eject; "
                    f"{len(output_files)} title(s) already ripped"
                )
                return RipResult(
                    success=True,
                    output_files=output_files,
                    aborted_for_eject=True,
                )

            if job_id in self._cancelled_jobs:
```

- [ ] **Step 8: Reset the flag at rip start and clear it on shutdown**

Alongside each existing `self._cancelled_jobs.discard(job_id)` at lines 766 and 1025, add:

```python
        self._ejected_jobs.discard(job_id)
```

And in `shutdown()`, alongside `self._cancelled_jobs.clear()`:

```python
        self._ejected_jobs.clear()
```

- [ ] **Step 9: Run the extractor test suite**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py tests/unit/test_rip_disc_title_map.py tests/unit/test_skip_rip_title.py tests/unit/test_rip_skip_boundary_chain.py -v
```

Expected: PASS. The skip tests must still pass: they exercise the cleanup path you just refactored.

- [ ] **Step 10: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/core/extractor.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(extractor): return salvageable result on eject abort"
```

---

## Task 3: Register the rip_ejected error code

**Files:**
- Modify: `backend/app/services/matching_coordinator.py:193-197`
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_disc_eject.py`:

```python
from app.services.matching_coordinator import (
    EJECTED_RIP_MESSAGE,
    RIP_FAILURE_ERROR_CODES,
)


def test_rip_ejected_is_a_registered_rip_failure_code():
    """Registration grants rerip_eligible and non-rematchable status.

    An unregistered REVIEW error code gets auto-escalated and has its
    match_details overwritten by the re-match pass, which would destroy the
    re-rip bookkeeping this feature depends on.
    """
    assert "rip_ejected" in RIP_FAILURE_ERROR_CODES


def test_rip_ejected_is_excluded_from_auto_rematch():
    """_NON_REMATCHABLE_REVIEW_ERRORS is derived from RIP_FAILURE_ERROR_CODES."""
    from app.services.finalization_coordinator import _NON_REMATCHABLE_REVIEW_ERRORS

    assert "rip_ejected" in _NON_REMATCHABLE_REVIEW_ERRORS


def test_ejected_rip_message_is_user_facing():
    """The review queue shows this verbatim, so it must explain the recovery."""
    assert "eject" in EJECTED_RIP_MESSAGE.lower()
    assert EJECTED_RIP_MESSAGE.strip() == EJECTED_RIP_MESSAGE
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k ejected
```

Expected: FAIL, `ImportError: cannot import name 'EJECTED_RIP_MESSAGE'`.

- [ ] **Step 3: Register the code and message**

In `backend/app/services/matching_coordinator.py`, replace the `RIP_FAILURE_ERROR_CODES` definition at line 197:

```python
# match_details["error"] codes that mean "the rip itself failed" — these titles
# are eligible for single-track re-rip after a clean & reinsert.
RIP_FAILURE_ERROR_CODES = frozenset({"incomplete_rip", "rip_stalled", "rip_ejected"})

# Shown verbatim in the review queue for a track the user's mid-rip eject cut
# short (or never reached). Deliberately states that nothing was lost, because
# the alarming case is a user who thinks ejecting discarded the whole disc.
EJECTED_RIP_MESSAGE = (
    "You ejected the disc before this track finished ripping. "
    "Nothing else was lost: reinsert the disc and use Re-rip to recover it."
)
```

Note: the existing comment on that constant uses an em dash. Leave the existing comment text as-is (it is pre-existing code, not new prose) but do not introduce new em dashes.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k ejected
```

Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(matching): register rip_ejected as a re-rippable failure code"
```

---

## Task 3b: Register rip_ejected in the frontend re-rip gate

**Discovered during Task 3 review.** `frontend/src/components/ReviewQueue/rerip.ts:1`
hardcodes its OWN copy of the backend's rip-failure code set:

```typescript
const RIP_FAILURE_CODES = new Set(['incomplete_rip', 'rip_stalled']);
```

`getRerippableState` returns `EMPTY` for any code not in that set, so without this
task `isRerippable` is `false` for every ejected track and the Re-rip button never
renders. The recovery path the feature promises would be dead, and
`EJECTED_RIP_MESSAGE` (which tells the user to "use Re-rip to recover it") would be
a false promise, with every backend test still green.

**Files:**
- Modify: `frontend/src/components/ReviewQueue/rerip.ts:1`
- Test: `frontend/src/components/ReviewQueue/__tests__/rerip.test.ts` (check the real
  path; if no test file exists for this module, create one next to it following the
  colocated `*.test.ts` convention used elsewhere in `frontend/src`)

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from 'vitest';
import { getRerippableState } from '../rerip';

describe('getRerippableState for ejected tracks', () => {
  it('treats rip_ejected as re-rippable', () => {
    const details = JSON.stringify({
      error: 'rip_ejected',
      message: 'You ejected the disc before this track finished ripping.',
      rerip_eligible: true,
      rerip_attempts: 0,
    });

    const state = getRerippableState(details);

    expect(state.isRerippable).toBe(true);
    expect(state.errorCode).toBe('rip_ejected');
    expect(state.autoEligible).toBe(true);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd frontend && npm run test:unit -- rerip
```

Expected: FAIL, `expected false to be true` on `isRerippable`.

- [ ] **Step 3: Add the code**

```typescript
// Mirrors RIP_FAILURE_ERROR_CODES in backend/app/services/matching_coordinator.py.
// Both must be updated together: this set gates whether the Re-rip button renders,
// so a code registered only on the backend parks tracks as re-rippable that the UI
// then offers no way to recover.
const RIP_FAILURE_CODES = new Set(['incomplete_rip', 'rip_stalled', 'rip_ejected']);
```

- [ ] **Step 4: Verify**

```bash
cd frontend && npm run test:unit -- rerip
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue/rerip.ts frontend/src/components/ReviewQueue/__tests__/rerip.test.ts
git commit -m "fix(ui): treat rip_ejected as re-rippable in the review queue"
```

---

## Task 4: JobManager.eject_disc_for_job

**Files:**
- Modify: `backend/app/services/job_manager.py` (add after `cancel_job`, around line 1170)
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_disc_eject.py`:

```python
import pytest
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models.disc_job import ContentType, DiscJob, JobState


@pytest.fixture
async def session_factory():
    """In-memory DB with StaticPool.

    StaticPool is REQUIRED: without it each connection gets a separate
    in-memory database and tests fail intermittently with "no such table".
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_eject_while_ripping_stops_rip_and_does_not_cancel(monkeypatch):
    """RIPPING: abort the rip, eject, leave the job alive for the salvage flow."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    aborted: list[int] = []
    monkeypatch.setattr(
        job_manager._extractor, "eject_abort", lambda jid: aborted.append(jid)
    )
    monkeypatch.setattr(jm_module, "eject_disc", lambda drive: True, raising=False)

    notified: list[str] = []
    monkeypatch.setattr(
        job_manager._drive_monitor, "notify_ejected", lambda d: notified.append(d)
    )

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    result = await job_manager.eject_disc_for_job(job.id)

    assert result == {"ejected": True, "action": "rip_stopped"}
    assert aborted == [job.id]
    assert notified == ["E:"]

    async with jm_module.async_session() as session:
        refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.RIPPING, "eject must not cancel the job"


@pytest.mark.asyncio
async def test_failed_eject_still_stops_rip_and_skips_notify(monkeypatch):
    """A tray that will not open must not suppress the salvage, and must not
    reset sentinel state (the user will pop the disc by hand)."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    aborted: list[int] = []
    monkeypatch.setattr(
        job_manager._extractor, "eject_abort", lambda jid: aborted.append(jid)
    )
    monkeypatch.setattr(jm_module, "eject_disc", lambda drive: False, raising=False)

    notified: list[str] = []
    monkeypatch.setattr(
        job_manager._drive_monitor, "notify_ejected", lambda d: notified.append(d)
    )

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    result = await job_manager.eject_disc_for_job(job.id)

    assert result == {"ejected": False, "action": "rip_stopped"}
    assert aborted == [job.id], "the rip must stop even if the tray will not open"
    assert notified == [], "sentinel must stay armed after a failed eject"


@pytest.mark.asyncio
async def test_eject_while_identifying_cancels_the_job(monkeypatch):
    """IDENTIFYING has produced nothing to salvage, so the job is cancelled."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    monkeypatch.setattr(jm_module, "eject_disc", lambda drive: True, raising=False)
    monkeypatch.setattr(job_manager._drive_monitor, "notify_ejected", lambda d: None)

    cancelled: list[int] = []

    async def fake_cancel(jid):
        cancelled.append(jid)

    monkeypatch.setattr(job_manager, "cancel_job", fake_cancel)

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.IDENTIFYING,
        content_type=ContentType.UNKNOWN,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    result = await job_manager.eject_disc_for_job(job.id)

    assert result == {"ejected": True, "action": "job_cancelled"}
    assert cancelled == [job.id]


@pytest.mark.asyncio
async def test_eject_in_matching_raises(monkeypatch):
    """The drive is already free post-rip, so there is nothing to eject."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.MATCHING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    with pytest.raises(ValueError, match="Cannot eject"):
        await job_manager.eject_disc_for_job(job.id)
```

Note the `sys.modules[...]` idiom: `import app.services.job_manager as mod` yields the module-level **singleton**, not the module, so monkeypatching module-level names through it silently no-ops.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k eject_
```

Expected: FAIL, `AttributeError: 'JobManager' object has no attribute 'eject_disc_for_job'`.

- [ ] **Step 3: Add the module-level eject import**

`eject_disc` is currently imported inside functions. The tests monkeypatch it at module scope, so hoist it. At the top of `backend/app/services/job_manager.py`, with the other `app.core` imports:

```python
from app.core.sentinel import eject_disc
```

Then remove the two function-local `from app.core.sentinel import eject_disc` lines (around lines 2335 and 2877) so both call sites resolve through the module global.

- [ ] **Step 4: Implement the method**

Add directly after `cancel_job` in `backend/app/services/job_manager.py` (after line 1169):

```python
    async def eject_disc_for_job(self, job_id: int) -> dict:
        """Release the disc without cancelling the job.

        RIPPING: terminate MakeMKV and eject. The in-flight ``_run_ripping``
        task sees ``aborted_for_eject`` and performs the salvage itself (finished
        titles continue to matching, unfinished ones route to REVIEW as
        re-rippable), so this method does NOT await it.

        IDENTIFYING: eject and cancel. Nothing was produced to salvage.

        Any other state: the drive is not held, so raise.

        Returns ``{"ejected": bool, "action": str}``. ``ejected`` is False when
        the tray refused to open (MakeMKV may still hold a handle, or the
        platform has no eject support); the rip is stopped either way, and the
        caller tells the user to eject manually.
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            state = job.state
            drive_id = job.drive_id

        if state not in (JobState.IDENTIFYING, JobState.RIPPING):
            raise ValueError(f"Cannot eject a job in state: {state.value}")

        safe_job = sanitize_log_value(job_id)

        # Stop MakeMKV FIRST so it releases its handle on the drive; a rip in
        # progress is exactly why the tray would otherwise refuse to open.
        #
        # to_thread is required, not cosmetic: eject_abort runs the rip's
        # eject preparer, which globs and stats the staging directory under
        # the rip's filesystem lock (held by the rip thread during its own
        # polls). Calling it inline would block the event loop on contended
        # disc I/O. See eject_abort's docstring.
        if state == JobState.RIPPING:
            await asyncio.to_thread(self._extractor.eject_abort, job_id)

        ejected = False
        try:
            ejected = await asyncio.to_thread(eject_disc, drive_id)
        except (OSError, RuntimeError) as e:
            logger.warning(f"Job {safe_job}: eject of {drive_id} raised: {e}")

        if ejected:
            # Only on success: after a failed eject the sentinel must stay armed
            # so it correctly observes the disc when the user pops it by hand.
            self._drive_monitor.notify_ejected(drive_id)
        else:
            logger.warning(
                f"Job {safe_job}: drive {drive_id} did not open; "
                f"the rip was still stopped and the disc can be removed by hand"
            )

        if state == JobState.IDENTIFYING:
            await self.cancel_job(job_id)
            logger.info(f"Job {safe_job}: ejected during identify, job cancelled")
            return {"ejected": ejected, "action": "job_cancelled"}

        logger.info(
            f"Job {safe_job}: disc ejected mid-rip; finished titles continue, "
            f"unfinished titles route to review"
        )
        return {"ejected": ejected, "action": "rip_stopped"}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/services/job_manager.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(jobs): add eject_disc_for_job with state-dependent behavior"
```

---

## Task 5: The _run_ripping eject fork (ordering-critical)

**Files:**
- Modify: `backend/app/services/job_manager.py:2736-2882`
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing test**

This is the regression test for constraint #1. Append to `backend/tests/unit/test_disc_eject.py`:

```python
from app.models.disc_title import DiscTitle, TitleState


@pytest.mark.asyncio
async def test_ejected_rip_routes_unfinished_titles_to_review_not_failed():
    """The ordering guard.

    reconcile_stuck_titles marks fileless PENDING/RIPPING titles FAILED. Since
    the abort deletes the truncated in-flight file, an unfinished track is
    fileless and indistinguishable from a never-started one. If the eject fork
    routes to REVIEW AFTER reconcile runs, every unfinished track silently
    lands FAILED and non-re-rippable, which inverts the whole feature.
    """
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

        never_started = DiscTitle(
            job_id=job.id, title_index=1, duration_seconds=1400,
            state=TitleState.PENDING, is_selected=True,
        )
        in_flight = DiscTitle(
            job_id=job.id, title_index=2, duration_seconds=1400,
            state=TitleState.RIPPING, is_selected=True,
        )
        session.add(never_started)
        session.add(in_flight)
        await session.commit()
        await session.refresh(never_started)
        await session.refresh(in_flight)

    await job_manager._route_unfinished_titles_after_eject(job.id)

    async with jm_module.async_session() as session:
        for title_id in (never_started.id, in_flight.id):
            title = await session.get(DiscTitle, title_id)
            assert title.state == TitleState.REVIEW, (
                f"title {title.title_index} must park in REVIEW, got {title.state}"
            )
            details = json.loads(title.match_details)
            assert details["error"] == "rip_ejected"
            assert details["rerip_eligible"] is True


@pytest.mark.asyncio
async def test_ejected_rip_leaves_finished_titles_alone():
    """A title that finished ripping must continue to matching untouched."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

        done = DiscTitle(
            job_id=job.id, title_index=1, duration_seconds=1400,
            state=TitleState.QUEUED, is_selected=True,
        )
        session.add(done)
        await session.commit()
        await session.refresh(done)

    await job_manager._route_unfinished_titles_after_eject(job.id)

    async with jm_module.async_session() as session:
        title = await session.get(DiscTitle, done.id)
        assert title.state == TitleState.QUEUED
```

Add `import json` to the test file imports if not already present.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k unfinished
```

Expected: FAIL, `AttributeError: 'JobManager' object has no attribute '_route_unfinished_titles_after_eject'`.

- [ ] **Step 3: Implement the routing helper**

Add to `backend/app/services/job_manager.py`, directly after `eject_disc_for_job`:

```python
    async def _route_unfinished_titles_after_eject(self, job_id: int) -> None:
        """Park every still-unfinished selected title in REVIEW as re-rippable.

        MUST be called BEFORE reconcile_stuck_titles. That function marks a
        fileless PENDING/RIPPING title FAILED, and because the eject abort
        deletes the truncated in-flight file, an interrupted track and a
        never-started track are both fileless and indistinguishable to it.
        Routing after reconcile would silently produce FAILED, non-re-rippable
        titles instead of the re-rippable REVIEW titles this feature promises.

        route_rip_failure_to_review is itself a no-op for titles outside
        PENDING/RIPPING/QUEUED/MATCHING, so titles that already finished and
        moved on are untouched. Only PENDING/RIPPING are passed here anyway.
        """
        async with async_session() as session:
            result = await session.execute(
                select(DiscTitle).where(
                    DiscTitle.job_id == job_id,
                    DiscTitle.is_selected == True,  # noqa: E712 (SQL expression)
                    DiscTitle.state.in_([TitleState.PENDING, TitleState.RIPPING]),
                )
            )
            unfinished = list(result.scalars().all())

        for title in unfinished:
            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title.title_index)} unfinished at eject "
                f"-> REVIEW (re-rippable)"
            )
            await self._matching.route_rip_failure_to_review(
                job_id, title.id, "rip_ejected", EJECTED_RIP_MESSAGE
            )
```

Add the import at the top of `job_manager.py`, alongside the other `matching_coordinator` imports:

```python
from app.services.matching_coordinator import EJECTED_RIP_MESSAGE
```

If `matching_coordinator` is only imported lazily in this module (to avoid circular imports), instead import it inside the method body and note that in a comment.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k unfinished
```

Expected: PASS, 2 passed.

- [ ] **Step 5: Wire the fork into _run_ripping**

In `backend/app/services/job_manager.py`, immediately after the `rip_titles` call's `finally` block that cancels `monitor_task` (after line 2727) and **before** the `stall_title_list = sorted_titles` line at 2736, insert:

```python
            # User ejected the disc mid-rip. Salvage and skip both the per-title
            # fallback (the disc is gone; each retry would cost a full stall
            # timeout) and the auto-eject block below (already ejected, and
            # re-issuing would spuriously reset sentinel state). Routing runs
            # HERE, before the post-rip backfill/reconcile, because
            # reconcile_stuck_titles marks fileless titles FAILED.
            if result.aborted_for_eject:
                logger.info(
                    f"Job {safe_job}: rip stopped for disc eject; "
                    f"routing unfinished titles to review"
                )
                await self._route_unfinished_titles_after_eject(job_id)
                ejected_mid_rip = True
            else:
                ejected_mid_rip = False
```

Then guard the per-title fallback: change the `if rip_all:` at line 2737 to:

```python
            if rip_all and not ejected_mid_rip:
```

Then guard the auto-eject block at line 2875. Change:

```python
            if rip_config and rip_config.auto_eject_enabled:
```

to:

```python
            if rip_config and rip_config.auto_eject_enabled and not ejected_mid_rip:
```

Everything after that point (backfill, reconcile, matching dispatch, organize) runs unchanged: that is the "standard workflow after eject" the feature promises.

- [ ] **Step 6: Catch an eject that lands during the fallback pass**

The check in Step 5 runs before the per-title fallback, so an eject clicked *while
the fallback is running* would be missed: its `rip_titles` call returns a second
`result` that never gets inspected. Immediately after the fallback block closes
(after `stall_title_list = missing` at what was line 2850), add:

```python
                    # An eject during the fallback pass sets the flag on the
                    # fallback's own result, which the Step 5 check (which ran
                    # before this pass) never saw. Route here, still ahead of
                    # the backfill/reconcile below.
                    if result.aborted_for_eject and not ejected_mid_rip:
                        logger.info(
                            f"Job {safe_job}: disc ejected during the per-title "
                            f"fallback; routing unfinished titles to review"
                        )
                        await self._route_unfinished_titles_after_eject(job_id)
                        ejected_mid_rip = True
```

Because `ejected_mid_rip` is set here too, the auto-eject guard added in Step 5
covers this path as well.

- [ ] **Step 7: Add the fallback-eject test**

Append to `backend/tests/unit/test_disc_eject.py`:

```python
@pytest.mark.asyncio
async def test_route_unfinished_after_eject_is_idempotent():
    """Routing may run twice (main pass, then fallback pass) without double-counting.

    The second call finds no PENDING/RIPPING titles left, so it is a no-op and
    must not bump rerip_attempts or overwrite the first call's match_details.
    """
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title = DiscTitle(
            job_id=job.id, title_index=1, duration_seconds=1400,
            state=TitleState.PENDING, is_selected=True,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)

    await job_manager._route_unfinished_titles_after_eject(job.id)
    async with jm_module.async_session() as session:
        first = json.loads((await session.get(DiscTitle, title.id)).match_details)

    await job_manager._route_unfinished_titles_after_eject(job.id)
    async with jm_module.async_session() as session:
        second = json.loads((await session.get(DiscTitle, title.id)).match_details)

    assert first == second
```

Run it:

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k idempotent
```

Expected: PASS.

- [ ] **Step 8: Verify the whole unit file plus the rip regression suites**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py tests/unit/test_job_manager.py tests/unit/test_rip_fallback_heartbeat.py tests/unit/test_stuck_job_recovery.py -v
```

Expected: PASS. The fallback-heartbeat and stuck-recovery suites guard the code paths you just added guards to.

- [ ] **Step 9: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/services/job_manager.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(jobs): salvage finished tracks when a disc is ejected mid-rip"
```

---

## Task 6: API endpoint

**Files:**
- Modify: `backend/app/api/routes.py:1064-1070`
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_disc_eject.py`:

```python
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_eject_endpoint_returns_result(client, monkeypatch):
    """The endpoint surfaces whether the tray actually opened."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    async def fake_eject(job_id):
        return {"ejected": True, "action": "rip_stopped"}

    monkeypatch.setattr(job_manager, "eject_disc_for_job", fake_eject)

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.RIPPING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    response = await client.post(f"/api/jobs/{job.id}/eject")

    assert response.status_code == 200
    assert response.json() == {
        "ejected": True,
        "action": "rip_stopped",
        "job_id": job.id,
    }


@pytest.mark.asyncio
async def test_eject_endpoint_409s_on_wrong_state(client, monkeypatch):
    """A job that does not hold the drive returns 409, not 500."""
    import sys

    from app.services.job_manager import job_manager

    jm_module = sys.modules["app.services.job_manager"]

    async def fake_eject(job_id):
        raise ValueError("Cannot eject a job in state: matching")

    monkeypatch.setattr(job_manager, "eject_disc_for_job", fake_eject)

    job = DiscJob(
        drive_id="E:",
        volume_label="TEST",
        state=JobState.MATCHING,
        content_type=ContentType.TV,
    )
    async with jm_module.async_session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    response = await client.post(f"/api/jobs/{job.id}/eject")

    assert response.status_code == 409
    assert "Cannot eject" in response.json()["detail"]


@pytest.mark.asyncio
async def test_eject_endpoint_404s_on_missing_job(client):
    response = await client.post("/api/jobs/999999/eject")
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k endpoint
```

Expected: FAIL with 404 on the first two tests (route does not exist).

- [ ] **Step 3: Add the route**

In `backend/app/api/routes.py`, directly after the `cancel_job` endpoint (after line 1070):

```python
@router.post("/jobs/{job_id}/eject")
async def eject_job_disc(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Release the disc without cancelling the job.

    While RIPPING: stops MakeMKV, ejects, and lets the job continue. Tracks
    that finished ripping still match and organize; tracks that did not are
    parked in review as re-rippable. While IDENTIFYING: ejects and cancels,
    because nothing has been produced to salvage.

    ``ejected`` reports whether the tray actually opened. False means the rip
    was still stopped but the user must remove the disc by hand.
    """
    from app.services.job_manager import job_manager

    try:
        result = await job_manager.eject_disc_for_job(job.id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {**result, "job_id": job.id}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v -k endpoint
```

Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/api/routes.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(api): add POST /api/jobs/{id}/eject"
```

---

## Task 7: Simulation support

E2E needs to drive an eject without a physical drive.

**Files:**
- Modify: `backend/app/services/simulation_service.py:600-620`
- Test: `backend/tests/integration/test_eject_workflow.py` (create, used in Task 8)

- [ ] **Step 1: Find the simulated rip loop**

Read `backend/app/services/simulation_service.py` around lines 595-625. The loop already re-reads each title from the DB per iteration and special-cases `TitleState.SKIPPED`. The eject check goes in the same place.

- [ ] **Step 2: Add the eject break**

Inside the simulated per-title rip loop, directly after the existing `SKIPPED` check block (around line 618), add:

```python
                # Mid-rip eject: stop producing files, exactly like the real
                # extractor's eject abort. Titles not yet produced stay
                # PENDING for _route_unfinished_titles_after_eject to park in
                # REVIEW.
                if job_id in job_manager._extractor._ejected_jobs:
                    logger.info(
                        f"Job {job_id}: simulated rip stopped for disc eject"
                    )
                    break
```

- [ ] **Step 3: Call the salvage after the simulated rip**

After the simulated rip loop completes, before the simulated flow advances to matching, add:

```python
            if job_id in job_manager._extractor._ejected_jobs:
                job_manager._extractor._ejected_jobs.discard(job_id)
                job_manager._extractor._cancelled_jobs.discard(job_id)
                await job_manager._route_unfinished_titles_after_eject(job_id)
```

- [ ] **Step 4: Verify nothing regressed**

```bash
cd backend && uv run pytest tests/integration/test_simulation.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
git add backend/app/services/simulation_service.py
git commit -m "feat(simulation): honor mid-rip eject in the simulated rip loop"
```

---

## Task 8: Integration test

**Files:**
- Create: `backend/tests/integration/test_eject_workflow.py`

- [ ] **Step 1: Write the test**

```python
"""Integration test: eject mid-rip salvages finished tracks.

NOTE: like the other integration tests in this directory, this runs against
the app's real database session. It creates and cleans up its own job rows.
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscJob, JobState
from app.models.disc_title import DiscTitle, TitleState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.mark.asyncio
async def test_eject_midrip_parks_job_in_review_with_rerippable_tracks(client):
    """End to end: insert, start ripping, eject, assert the salvage."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        },
    )
    assert response.status_code == 200

    # Wait for the job to reach RIPPING.
    job_id = None
    for _ in range(50):
        await asyncio.sleep(0.2)
        jobs = (await client.get("/api/jobs")).json()
        ripping = [j for j in jobs if j["state"] == "ripping"]
        if ripping:
            job_id = ripping[0]["id"]
            break
    assert job_id is not None, "job never reached RIPPING"

    eject = await client.post(f"/api/jobs/{job_id}/eject")
    assert eject.status_code == 200
    assert eject.json()["action"] == "rip_stopped"

    # The job must NOT be failed or cancelled.
    for _ in range(50):
        await asyncio.sleep(0.2)
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job.state in (JobState.REVIEW_NEEDED, JobState.COMPLETED):
                break
    async with async_session() as session:
        job = await session.get(DiscJob, job_id)
        assert job.state != JobState.FAILED, "eject must never fail the job"

        result = await session.execute(
            text("SELECT id, state, match_details FROM disc_titles WHERE job_id = :j"),
            {"j": job_id},
        )
        rows = result.fetchall()

    # Every track parked by the eject carries the re-rippable marker.
    ejected_rows = [
        r for r in rows
        if r.match_details and json.loads(r.match_details).get("error") == "rip_ejected"
    ]
    for row in ejected_rows:
        details = json.loads(row.match_details)
        assert details["rerip_eligible"] is True
        # State enum is stored by NAME in raw SQL reads.
        assert row.state == TitleState.REVIEW.name
```

- [ ] **Step 2: Run the test**

```bash
cd backend && uv run pytest tests/integration/test_eject_workflow.py -v
```

Expected: PASS. If the job never reaches `ripping`, confirm `DEBUG=true` is set so the simulation endpoints are mounted.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_eject_workflow.py
git commit -m "test(integration): cover eject-mid-rip salvage end to end"
```

---

## Task 9: Frontend API wiring

**Files:**
- Modify: `frontend/src/app/hooks/useJobManagement.ts:231-250`, `:465`
- Test: `frontend/src/app/hooks/__tests__/useJobManagement.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/app/hooks/__tests__/useJobManagement.test.ts`, following the file's existing mock/render pattern:

```typescript
describe('ejectJob', () => {
    it('POSTs to the eject endpoint and reports a successful eject', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ ejected: true, action: 'rip_stopped', job_id: 1 }),
        });
        vi.stubGlobal('fetch', fetchMock);

        const { result } = renderHook(() => useJobManagement(false));
        await act(async () => { await result.current.ejectJob('1'); });

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/jobs/1/eject',
            expect.objectContaining({ method: 'POST' }),
        );
    });

    it('warns the user when the tray did not open', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ ejected: false, action: 'rip_stopped', job_id: 1 }),
        });
        vi.stubGlobal('fetch', fetchMock);

        const { result } = renderHook(() => useJobManagement(false));
        await act(async () => { await result.current.ejectJob('1'); });

        expect(toast.warning).toHaveBeenCalledWith(
            expect.stringContaining('manually'),
        );
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npm run test:unit -- useJobManagement
```

Expected: FAIL, `result.current.ejectJob is not a function`.

- [ ] **Step 3: Implement ejectJob**

In `frontend/src/app/hooks/useJobManagement.ts`, directly after `cancelJob` (line 239):

```typescript
    async function ejectJob(jobId: string) {
        try {
            const result = await apiFetch<{ ejected: boolean; action: string }>(
                `/api/jobs/${jobId}/eject`,
                { method: 'POST' },
            );
            if (!result.ejected) {
                // The rip was still stopped, so this is a warning, not an error.
                toast.warning('Ripping stopped, but the drive would not open. Eject the disc manually.');
            } else if (result.action === 'job_cancelled') {
                toast.success('Disc ejected. The job was cancelled because nothing had been ripped yet.');
            } else {
                toast.success('Disc ejected. Finished tracks keep processing; the rest are in review.');
            }
            // Job and track states arrive via WebSocket.
        } catch (error) {
            console.error('Failed to eject disc:', error);
            toast.error('Failed to eject the disc. Please try again.');
        }
    }
```

Ensure `apiFetch` is imported alongside `apiFetchVoid` at the top of the file.

Then add `ejectJob` to the hook's return object (around line 465), next to `cancelJob`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npm run test:unit -- useJobManagement
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/hooks/useJobManagement.ts frontend/src/app/hooks/__tests__/useJobManagement.test.ts
git commit -m "feat(ui): add ejectJob to useJobManagement"
```

---

## Task 10: The Eject button

Design rationale (from the frontend-design pass, recorded so it is not re-litigated):

- **Amber**, not red: red is Cancel's register (destroy the job, delete staging), and eject destroys nothing. Sharing the hue would mislead in exactly the moment the user is anxious about losing work.
- **Amber**, not magenta: `#ff3d7f` against Cancel's `#ff5555` at 14px side by side is genuinely confusable, and collapses entirely for red-blind viewers. Two stop-actions must not be distinguished by hue alone.
- **Amber despite Review owning yellow:** that collision cannot occur. `showReview` requires `state === "review_needed"`; `showEject` requires `scanning`/`ripping`. They are never on screen together.
- **Labeled, not icon-only:** deliberate redundancy so shape and text carry the distinction from the icon-only Cancel square when color fails.

**Files:**
- Modify: `frontend/src/app/components/DiscCard/ActionButtons.tsx`
- Test: `frontend/src/app/components/DiscCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/app/components/DiscCard.test.tsx`, following its existing render helpers:

```typescript
import { ActionButtons } from './DiscCard/ActionButtons';

describe('ActionButtons eject', () => {
    it('shows Eject while ripping', () => {
        render(<ActionButtons state="ripping" isHovered={false} onEject={vi.fn()} />);
        expect(screen.getByTestId('eject-button')).toBeInTheDocument();
    });

    it('shows Eject while scanning', () => {
        render(<ActionButtons state="scanning" isHovered={false} onEject={vi.fn()} />);
        expect(screen.getByTestId('eject-button')).toBeInTheDocument();
    });

    it('hides Eject once the drive is free', () => {
        render(<ActionButtons state="matching" isHovered={false} onEject={vi.fn()} />);
        expect(screen.queryByTestId('eject-button')).not.toBeInTheDocument();
    });

    it('requires confirmation before calling onEject', async () => {
        const onEject = vi.fn();
        render(<ActionButtons state="ripping" isHovered={false} onEject={onEject} />);

        await userEvent.click(screen.getByTestId('eject-button'));
        expect(onEject).not.toHaveBeenCalled();

        await userEvent.click(screen.getByRole('button', { name: /eject disc/i }));
        expect(onEject).toHaveBeenCalledTimes(1);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npm run test:unit -- DiscCard
```

Expected: FAIL, no element with testid `eject-button`.

- [ ] **Step 3: Add the AMBER tone**

In `frontend/src/app/components/DiscCard/ActionButtons.tsx`, after the `YELLOW` tone definition (line 167):

```typescript
// Eject: the caution/interrupt register. Distinct from RED (Cancel destroys the
// job) and CYAN (routine). It shares a hue family with YELLOW, but the Review
// button only renders in review_needed and Eject only in scanning/ripping, so
// the two are never on screen together.
const AMBER: Tone = {
    fg: sv.amber,
    fgHi: "#fde9a8",
    border: `${sv.amber}66`,
    borderHi: sv.amber,
    glow: `${sv.amber}33`,
    glowHi: `${sv.amber}77`,
    bgHi: "rgba(252, 211, 77, 0.10)",
};
```

- [ ] **Step 4: Extend the confirm tone**

Change the `ConfirmModalProps` interface (line 33):

```typescript
    confirmTone?: "red" | "cyan" | "amber";
```

And replace the two colour lines in `ConfirmModal` (lines 53-54):

```typescript
    const confirmColor =
        confirmTone === "red" ? sv.red : confirmTone === "amber" ? sv.amber : sv.cyan;
    const confirmBg =
        confirmTone === "red"
            ? "rgba(255,85,85,0.12)"
            : confirmTone === "amber"
              ? "rgba(252,211,77,0.12)"
              : "rgba(94,234,212,0.12)";
```

- [ ] **Step 5: Add the visibility constant, prop, and state**

Add after `CANCELABLE_STATES` (line 127):

```typescript
// Eject only makes sense while the drive physically holds the disc. Post-rip
// states (matching, organizing) run after auto-eject has already fired.
const EJECTABLE_STATES = ["scanning", "ripping"];
```

Add to `ActionButtonsProps`:

```typescript
    onEject?: () => void;
```

Add to the destructured params and add the modal state:

```typescript
    const [showEjectModal, setShowEjectModal] = useState(false);
    const showEject = !!onEject && EJECTABLE_STATES.includes(state);

    const handleEject = (e: MouseEvent) => {
        e.stopPropagation();
        setShowEjectModal(true);
    };
```

- [ ] **Step 6: Add the modal**

Inside the existing `<AnimatePresence>`, after the advance modal:

```tsx
            {showEjectModal && (
                <ConfirmModal
                    titleId="eject-modal-title"
                    title="Eject disc?"
                    body="Ripping stops and the disc is released. Finished tracks keep processing; the rest go to review so you can re-rip them after cleaning the disc."
                    confirmLabel="Eject disc"
                    dismissLabel="Keep ripping"
                    confirmTone="amber"
                    onConfirm={() => { setShowEjectModal(false); onEject!(); }}
                    onDismiss={() => setShowEjectModal(false)}
                />
            )}
```

- [ ] **Step 7: Add the button**

Directly after the `showCancel` block, so the two stop-actions group together:

```tsx
            {showEject && (
                <ToneButton
                    tone={AMBER}
                    onClick={handleEject}
                    title="Eject the disc and keep the finished tracks"
                    ariaLabel="Eject disc"
                    paddingX={10}
                    testId="eject-button"
                >
                    <IcoEject size={12} />
                    <span style={{ fontSize: 10 }}>Eject</span>
                </ToneButton>
            )}
```

Add `IcoEject` to the icon import on line 23:

```typescript
import { IcoCancel, IcoError, IcoRetry, IcoPlay, IcoEject } from "../icons";
```

- [ ] **Step 8: Run test to verify it passes**

```bash
cd frontend && npm run test:unit -- DiscCard
```

Expected: PASS.

- [ ] **Step 9: Thread the prop through DiscCard and App**

In `frontend/src/app/components/DiscCard.tsx`, add `onEject?: () => void;` to the card's props and pass it to `<ActionButtons onEject={onEject} />`.

In `frontend/src/app/App.tsx`, destructure `ejectJob` from `useJobManagement` (line 171) and pass it to the card alongside `onCancel` at line 773:

```tsx
              onEject={(id) => ejectJob(id)}
```

Match the existing call signature at that site: if `onCancel` there is `() => cancelJob(disc.id)`, mirror that form.

- [ ] **Step 10: Verify the build and lint**

```bash
cd frontend && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/app/components/DiscCard/ActionButtons.tsx frontend/src/app/components/DiscCard.tsx frontend/src/app/App.tsx frontend/src/app/components/DiscCard.test.tsx
git commit -m "feat(ui): add amber Eject button to the disc card action row"
```

---

## Task 11: E2E test

**Files:**
- Create: `frontend/e2e/disc-eject.spec.ts`

- [ ] **Step 1: Write the test**

```typescript
import { test, expect } from '@playwright/test';
import { resetAllJobs, insertDisc } from './helpers';

test.describe('mid-rip disc eject', () => {
    test.beforeEach(async ({ request }) => {
        await resetAllJobs(request);
    });

    test('ejecting mid-rip parks the job in review without failing it', async ({ page, request }) => {
        await page.goto('/');
        await insertDisc(request, {
            volume_label: 'ARRESTED_DEVELOPMENT_S1D1',
            content_type: 'tv',
            simulate_ripping: true,
        });

        const ejectButton = page.getByTestId('eject-button');
        await expect(ejectButton).toBeVisible({ timeout: 30_000 });

        await ejectButton.click();
        await expect(page.getByText('Eject disc?')).toBeVisible();
        await page.getByRole('button', { name: 'Eject disc' }).click();

        // The job must land in review, never in error.
        await expect(page.getByText(/review needed/i)).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText(/^error$/i)).toHaveCount(0);
    });

    test('the eject button is absent once the drive is free', async ({ page, request }) => {
        await page.goto('/');
        await insertDisc(request, {
            volume_label: 'INCEPTION_2010',
            content_type: 'movie',
            simulate_ripping: true,
        });

        await expect(page.getByTestId('eject-button')).toBeVisible({ timeout: 30_000 });
        // Once the disc completes, the drive is free and the button retires.
        await expect(page.getByTestId('eject-button')).toBeHidden({ timeout: 120_000 });
    });
});
```

Check `frontend/e2e/helpers.ts` for the actual exported helper names and signatures before running; the existing specs (for example `track-skipping.spec.ts`) show the house pattern. If `insertDisc`/`resetAllJobs` are named differently, use the real names.

- [ ] **Step 2: Run the test**

Start a backend with `DEBUG=true` on its own port first (see CLAUDE.md "Parallel sessions"), then:

```bash
cd frontend && npm run test:e2e -- disc-eject
```

Expected: PASS.

Known hazard: every modal in this app leaves a stuck `opacity-0` full-screen overlay after close when driven headlessly, which blocks subsequent clicks. It is pre-existing, not caused by this change. If a later click in the spec fails, diagnose with `document.elementFromPoint` rather than screenshots.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/disc-eject.spec.ts
git commit -m "test(e2e): cover mid-rip disc eject"
```

---

## Task 12: Documentation and changelog

**Files:**
- Modify: `CHANGELOG.md`, `docs/architecture/state-machine.md`

- [ ] **Step 1: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, in an `### Added` subsection:

```markdown
- **Mid-rip disc eject.** A damaged or dirty disc can now be ejected from the dashboard without cancelling the job. Ripping stops immediately (no more waiting out the 20-minute stall timeout), tracks that finished ripping continue to match and organize as normal, and unfinished tracks are parked in review as re-rippable so you can clean the disc, reinsert it, and recover them individually. Ejecting during the initial scan cancels the job, since nothing has been produced yet.
```

- [ ] **Step 2: Document the state path**

In `docs/architecture/state-machine.md`, add to the section covering rip outcomes:

```markdown
### Mid-rip eject

`POST /api/jobs/{id}/eject` during `RIPPING` terminates MakeMKV and releases the
drive without a state transition. The in-flight rip returns
`aborted_for_eject=True`; `_run_ripping` then routes every unfinished selected
title to `REVIEW` with `match_details.error = "rip_ejected"` and
`rerip_eligible = true`, skips the per-title fallback pass and the auto-eject
block, and falls through to the normal post-rip flow. The job settles in
`REVIEW_NEEDED`.

The routing runs before `reconcile_stuck_titles`, which would otherwise mark
those fileless titles `FAILED`.

During `IDENTIFYING` the same endpoint ejects and cancels the job. In any other
state it returns 409.
```

- [ ] **Step 3: Verify no em dashes were introduced**

```bash
grep -n $'\xe2\x80\x94\|\xe2\x80\x93' CHANGELOG.md docs/architecture/state-machine.md
```

Use this byte-based pattern, not `grep "[—–]"`: the character-class form false-positives on arrows and ellipses in Git Bash.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/architecture/state-machine.md
git commit -m "docs: document mid-rip disc eject"
```

---

## Task 13: Full verification

- [ ] **Step 1: Backend unit tier**

```bash
cd backend && uv run pytest tests/unit/ -q
```

Expected: all pass. This tier can exceed 5 minutes; do not abort it early.

- [ ] **Step 2: Backend integration and pipeline tiers**

```bash
cd backend && uv run pytest tests/integration/ tests/pipeline/ -q
```

Expected: all pass.

- [ ] **Step 3: Frontend**

```bash
cd frontend && npm run lint && npm run build && npm run test:unit
```

Expected: all clean.

- [ ] **Step 4: Lint the backend**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 5: Stop this session's servers**

Per CLAUDE.md, kill the uvicorn/Vite processes started for the E2E run, scoped by the ports you launched on, before opening the PR. Orphaned processes cause duplicate jobs and MakeMKV drive conflicts.

---

## Manual verification with a real disc

Automated tests cannot exercise the physical tray. Before merging, verify by hand:

1. Start one backend (exactly one, per CLAUDE.md) and insert a real disc.
2. Let ripping begin and at least one track finish.
3. Click **Eject**, confirm in the modal.
4. Verify: the tray opens within a few seconds; the card does not go red/error; finished tracks organize into the library; unfinished tracks appear in review with a "Re-rip" affordance and the ejected message.
5. Clean and reinsert the disc, then use Re-rip on one parked track and confirm it recovers.
