# Manual Track Skipping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users manually skip a queued / not-yet-ripped track so MakeMKV does not waste rip time on it (e.g. a bogus play-all concatenation), with a distinct reversible `SKIPPED` disposition.

**Architecture:** A new terminal `TitleState.SKIPPED`, paired with the existing `is_selected` flag. Pre-rip skips shrink the selection and route to the per-title rip loop automatically. Mid-rip skips register in a live skip-set the extractor checks before each per-title command (guaranteed skip); during a full-disc `all` pass the file is written anyway and deleted at completion (best-effort). `SKIPPED` is neutral to job completion: it never blocks, never counts as a failure.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / aiosqlite (backend), pytest; React 18 / TypeScript / Vite (frontend), Playwright.

**Design spec:** `docs/superpowers/specs/2026-07-03-track-skipping-design.md`

**Conventions:**
- Backend commands run from `backend/` with `uv run` (e.g. `uv run pytest ...`). Never bare `python`/`pytest`.
- Frontend commands run from `frontend/`.
- Worktree DB may be a 0-byte stub; if a pipeline/integration test errors with "no such table", run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first.
- Do not use em dashes in code comments or changelog prose (project style).

---

## File Structure

**Backend (modify):**
- `backend/app/models/disc_job.py` — add `TitleState.SKIPPED`.
- `backend/app/core/extractor.py` — live per-job skip-set; per-title loop honors it; `commands` become `(title_index, cmd)` tuples.
- `backend/app/services/job_manager.py` — `skip_rip_title` / `unskip_rip_title`; `_on_title_ripped` deletes a SKIPPED file; `_run_ripping` treats SKIPPED as deselected.
- `backend/app/services/finalization_coordinator.py` — SKIPPED excluded from the unresolved query; all-skipped short-circuit to COMPLETED; outcome booleans computed over non-skipped titles.
- `backend/app/api/routes.py` — `skip-rip` / `unskip-rip` endpoints.

**Frontend (modify):**
- `frontend/src/types/index.ts` — `'skipped'` in `TitleState`.
- `frontend/src/types/adapters.ts` — `'skipped'` in the state map.
- `frontend/src/app/components/DiscCard.tsx` — `'skipped'` in `TrackState`; `onSkipTrack` / `onUnskipTrack` props threaded to `TrackGrid`.
- `frontend/src/app/components/TrackGrid.tsx` — `skipped` STATE entry; skip / un-skip controls.
- `frontend/src/api/client.ts` — `skipRipTitle` / `unskipRipTitle`.
- `frontend/src/app/App.tsx` (+ `hooks/useJobManagement.ts` if that is where per-track handlers live) — wire handlers.

**Tests (create/modify):**
- `backend/tests/unit/test_extractor_skip.py` (create)
- `backend/tests/unit/test_skip_rip_title.py` (create)
- `backend/tests/integration/test_track_skipping.py` (create)
- `backend/tests/unit/test_completion_skipped.py` (create)
- `frontend/e2e/track-skipping.spec.ts` (create)

---

## Task 1: Add the `SKIPPED` title state

**Files:**
- Modify: `backend/app/models/disc_job.py:31-41`
- Test: `backend/tests/unit/test_skip_rip_title.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_skip_rip_title.py
from app.models.disc_job import TitleState


def test_skipped_state_exists_and_is_distinct():
    assert TitleState.SKIPPED == "skipped"
    assert TitleState.SKIPPED not in (TitleState.COMPLETED, TitleState.FAILED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py::test_skipped_state_exists_and_is_distinct -v`
Expected: FAIL with `AttributeError: SKIPPED`.

- [ ] **Step 3: Add the enum value**

In `backend/app/models/disc_job.py`, add to `TitleState` (after `FAILED`):

```python
class TitleState(StrEnum):
    """State of an individual title."""

    PENDING = "pending"
    RIPPING = "ripping"
    QUEUED = "queued"  # Ripped/on disk, waiting for a matching slot (subtitle + semaphore wait)
    MATCHING = "matching"
    MATCHED = "matched"  # Intermediate state: matched but not yet organized
    REVIEW = "review"  # Ripped successfully but needs human review for episode assignment
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # User skipped this track before ripping; excluded, not a failure
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py::test_skipped_state_exists_and_is_distinct -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/disc_job.py backend/tests/unit/test_skip_rip_title.py
git commit -m "feat(model): add TitleState.SKIPPED for manual track skipping"
```

---

## Task 2: Extractor honors a live skip-set in the per-title loop

The extractor loops one command per title for subset rips. Register skipped `title_index` values so a title whose command has not started yet is dropped. Refactor `commands` to carry the title index.

**Files:**
- Modify: `backend/app/core/extractor.py` (`__init__` ~382-389; `_rip_titles_unlocked` command build ~567-613 and loop ~775-783)
- Test: `backend/tests/unit/test_extractor_skip.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_extractor_skip.py
from app.core.extractor import MakeMKVExtractor


def test_skip_set_registration_and_clear():
    ext = MakeMKVExtractor()
    ext.skip_title_index(5, 3)
    ext.skip_title_index(5, 7)
    assert ext._skipped_indices[5] == {3, 7}

    ext.unskip_title_index(5, 3)
    assert ext._skipped_indices[5] == {7}

    # Unknown job / index is a no-op, never raises.
    ext.unskip_title_index(999, 1)
    ext.unskip_title_index(5, 999)
    assert ext._skipped_indices[5] == {7}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_extractor_skip.py::test_skip_set_registration_and_clear -v`
Expected: FAIL with `AttributeError: 'MakeMKVExtractor' object has no attribute 'skip_title_index'`.

- [ ] **Step 3: Add the skip-set state and methods**

In `MakeMKVExtractor.__init__`, after `self._cancelled_jobs: set[int] = set()`:

```python
        self._cancelled_jobs: set[int] = set()
        # Per-job set of title_index values to skip. Checked before each
        # per-title rip command so a queued-but-not-yet-ripped title can be
        # dropped mid-rip. A full-disc "all" pass cannot honor this (one process
        # rips everything); those skips are handled downstream by deleting the
        # finished file. Single-writer per job under the rip thread + async
        # skip calls; set mutation is atomic under the GIL.
        self._skipped_indices: dict[int, set[int]] = {}
```

Add these methods (near `cancel`):

```python
    def skip_title_index(self, job_id: int, title_index: int) -> None:
        """Register a title_index to skip in the per-title rip loop for a job."""
        self._skipped_indices.setdefault(job_id, set()).add(title_index)

    def unskip_title_index(self, job_id: int, title_index: int) -> None:
        """Remove a previously-registered skip (no-op if absent)."""
        s = self._skipped_indices.get(job_id)
        if s:
            s.discard(title_index)
```

Also clear the set on job end. In the `finally` block of `run_rip_with_streaming` (currently `self._processes.pop(job_id, None)`), add alongside it:

```python
            finally:
                self._processes.pop(job_id, None)
                self._skipped_indices.pop(job_id, None)
```

And in `shutdown`, after `self._cancelled_jobs.clear()`:

```python
        self._processes.clear()
        self._cancelled_jobs.clear()
        self._skipped_indices.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_extractor_skip.py::test_skip_set_registration_and_clear -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_skip.py
git commit -m "feat(extractor): add per-job skip-set state + accessors"
```

---

## Task 3: Extractor per-title loop skips registered indices

Refactor `commands` to `(title_index, cmd)` tuples and check the skip-set before starting each command.

**Files:**
- Modify: `backend/app/core/extractor.py` (command build ~567-613; loop ~775-783)
- Test: `backend/tests/unit/test_extractor_skip.py`

- [ ] **Step 1: Write the failing test**

This test drives the loop's command-building + skip logic without spawning MakeMKV, by asserting on the command list shape the refactor produces. Add a small pure helper so the mapping is testable.

Add to `backend/tests/unit/test_extractor_skip.py`:

```python
from app.core.extractor import _build_rip_commands


def test_build_rip_commands_all_selected_uses_all_pass():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", None)
    assert len(cmds) == 1
    title_index, cmd = cmds[0]
    assert title_index is None  # "all" pass has no single title index
    assert cmd[-1] == "/out"
    assert "all" in cmd


def test_build_rip_commands_subset_is_per_title_with_indices():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", [2, 4])
    assert [ti for ti, _ in cmds] == [2, 4]
    assert all(str(ti) in cmd for ti, cmd in cmds)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_extractor_skip.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_rip_commands'`.

- [ ] **Step 3: Extract a command builder and use tuples**

Add this module-level function to `backend/app/core/extractor.py` (near `_files_to_ignore`):

```python
def _build_rip_commands(
    makemkv_path: str,
    drive_spec: str,
    output_dir: str,
    title_indices: list[int] | None,
) -> list[tuple[int | None, list[str]]]:
    """Build ``(title_index, argv)`` rip commands.

    ``title_index`` is None for the single full-disc "all" pass (which rips
    every title in one MakeMKV invocation and cannot drop an individual title);
    otherwise each command carries the specific title index so the rip loop can
    consult the live skip-set before starting it.
    """
    base = [makemkv_path, "-r", "--progress=-same", "mkv", drive_spec]
    if not title_indices:
        return [(None, [*base, "all", output_dir])]
    return [(idx, [*base, str(idx), output_dir]) for idx in title_indices]
```

Replace the command-building block in `_rip_titles_unlocked` (the `if not title_indices: ... elif len == 1: ... else: ...` block) with:

```python
        commands = _build_rip_commands(
            str(self.makemkv_path),
            drive_spec,
            str(output_dir),
            title_indices,
        )
        if not title_indices:
            logger.info("Ripping ALL titles")
        else:
            logger.info(f"Ripping {len(title_indices)} specific title(s): {title_indices}")
```

In `run_rip_with_streaming`, change the loop header from `for cmd in commands:` to:

```python
                for title_index, cmd in commands:
                    if job_id in self._cancelled_jobs:
                        break

                    current_title_idx += 1

                    # Live skip: a queued title the user skipped before MakeMKV
                    # reached it is dropped here. (The "all" pass has
                    # title_index None and cannot be skipped this way; those are
                    # handled by deleting the finished file downstream.)
                    if (
                        title_index is not None
                        and title_index in self._skipped_indices.get(job_id, set())
                    ):
                        logger.info(
                            f"Skipping title {title_index} (command "
                            f"{current_title_idx}/{len(commands)}) — user-skipped"
                        )
                        continue

                    logger.info(
                        f"Executing rip command {current_title_idx}/{len(commands)}: "
                        f"{' '.join(cmd)}"
                    )
```

(Remove the now-duplicated `current_title_idx += 1` and the old `logger.info("Executing rip command ...")` that followed the original `for cmd in commands:`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_extractor_skip.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Regression-check the extractor suite**

Run: `cd backend && uv run pytest tests/ -k extractor -v`
Expected: PASS. If any test asserted on the old command-building branches, update it to call `_build_rip_commands`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/extractor.py backend/tests/unit/test_extractor_skip.py
git commit -m "feat(extractor): honor live skip-set in per-title rip loop"
```

---

## Task 4: `skip_rip_title` / `unskip_rip_title` on JobManager

**Files:**
- Modify: `backend/app/services/job_manager.py` (add methods near `skip_title` ~1271)
- Test: `backend/tests/unit/test_skip_rip_title.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/unit/test_skip_rip_title.py
import pytest
from sqlalchemy import text

from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def _clean_db():
    await init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _make_job(state=JobState.RIPPING, title_state=TitleState.PENDING):
    async with async_session() as s:
        job = DiscJob(drive_id="Z:", volume_label="TEST", state=state,
                      content_type=ContentType.TV, staging_path="/tmp/none")
        s.add(job)
        await s.commit()
        await s.refresh(job)
        title = DiscTitle(job_id=job.id, title_index=3, duration_seconds=1200,
                          state=title_state, is_selected=True)
        s.add(title)
        await s.commit()
        await s.refresh(title)
        return job.id, title.id


async def test_skip_rip_pending_title_marks_skipped():
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    ok = await job_manager.skip_rip_title(job_id, title_id)
    assert ok is True
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.SKIPPED
        assert t.is_selected is False
    assert 3 in job_manager._extractor._skipped_indices.get(job_id, set())


async def test_skip_rip_rejects_ripping_title():
    job_id, title_id = await _make_job(title_state=TitleState.RIPPING)
    ok = await job_manager.skip_rip_title(job_id, title_id)
    assert ok is False


async def test_unskip_restores_pending():
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    await job_manager.skip_rip_title(job_id, title_id)
    ok = await job_manager.unskip_rip_title(job_id, title_id)
    assert ok is True
    async with async_session() as s:
        t = await s.get(DiscTitle, title_id)
        assert t.state == TitleState.PENDING
        assert t.is_selected is True
    assert 3 not in job_manager._extractor._skipped_indices.get(job_id, set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py -v`
Expected: FAIL with `AttributeError: 'JobManager' object has no attribute 'skip_rip_title'`.

- [ ] **Step 3: Implement the methods**

Add to `JobManager` in `backend/app/services/job_manager.py` (right after `skip_title`):

```python
    async def skip_rip_title(self, job_id: int, title_id: int) -> bool:
        """Skip a queued/not-yet-ripped title so MakeMKV does not rip it.

        Acts only on PENDING or QUEUED titles (never one actively RIPPING or
        already terminal). Marks the title SKIPPED + deselected, registers its
        index in the extractor's live skip-set (honored by the per-title rip
        loop), then re-checks job completion. Returns False if the title is not
        in a skippable state.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                return False
            if title.state not in (TitleState.PENDING, TitleState.QUEUED):
                return False

            title.state = TitleState.SKIPPED
            title.is_selected = False
            title.match_details = json.dumps(
                {"reason": "Skipped by user", "skipped": True}
            )
            session.add(title)
            await session.commit()
            title_index = title.title_index

            # Live skip for an in-progress per-title rip loop.
            self._extractor.skip_title_index(job_id, title_index)

            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title_index)} skipped by user -> SKIPPED"
            )
            await ws_manager.broadcast_title_update(
                job_id, title_id, TitleState.SKIPPED.value
            )
            await self._finalization.check_job_completion(session, job_id)
        return True

    async def unskip_rip_title(self, job_id: int, title_id: int) -> bool:
        """Reverse a skip while the title's file has not been written yet.

        Allowed only while the title is still SKIPPED and no output file exists.
        Restores PENDING + selected and clears the extractor skip-set entry.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                return False
            if title.state != TitleState.SKIPPED:
                return False
            if title.output_filename:
                return False

            title.state = TitleState.PENDING
            title.is_selected = True
            title.match_details = None
            session.add(title)
            await session.commit()
            title_index = title.title_index

            self._extractor.unskip_title_index(job_id, title_index)

            logger.info(
                f"Job {sanitize_log_value(job_id)}: title "
                f"{sanitize_log_value(title_index)} un-skipped -> PENDING"
            )
            await ws_manager.broadcast_title_update(
                job_id, title_id, TitleState.PENDING.value
            )
        return True
```

(`json` and `sanitize_log_value` are already imported in this module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py -v`
Expected: PASS (all tests). If a test errors with "no such table", run the `init_db()` one-liner from Conventions first.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_skip_rip_title.py
git commit -m "feat(jobs): add skip_rip_title / unskip_rip_title"
```

---

## Task 5: `_on_title_ripped` deletes a SKIPPED file (mid-`all`-pass path)

During a full-disc `all` pass, MakeMKV writes a skipped title anyway. When its file completes, delete it and do not dispatch matching.

**Files:**
- Modify: `backend/app/services/job_manager.py` (`_on_title_ripped` ~2903-2910)
- Test: `backend/tests/unit/test_skip_rip_title.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/unit/test_skip_rip_title.py
from pathlib import Path

from app.models.disc_job import DiscTitle as _DT  # alias to avoid shadowing


async def test_on_title_ripped_deletes_skipped_file(tmp_path):
    job_id, title_id = await _make_job(title_state=TitleState.PENDING)
    # Mark it skipped first.
    await job_manager.skip_rip_title(job_id, title_id)

    fake = tmp_path / "TEST_t03.mkv"
    fake.write_bytes(b"x" * 1024)

    async with async_session() as s:
        title = await s.get(_DT, title_id)
        sorted_titles = [title]

    await job_manager._on_title_ripped(job_id, 1, fake, sorted_titles)

    assert not fake.exists()
    async with async_session() as s:
        t = await s.get(_DT, title_id)
        assert t.state == TitleState.SKIPPED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py::test_on_title_ripped_deletes_skipped_file -v`
Expected: FAIL (file still exists — no delete branch yet).

- [ ] **Step 3: Add the SKIPPED short-circuit**

In `_on_title_ripped`, immediately after the `if not title: return` guard (~line 2908), insert:

```python
            if title.state == TitleState.SKIPPED:
                # Mid-"all"-pass skip: MakeMKV wrote this title before we could
                # drop it. Best-effort — delete the file and do not match it.
                try:
                    path.unlink(missing_ok=True)
                    logger.info(
                        f"Job {job_id}: title {title.title_index} skipped by user — "
                        f"deleted ripped file {path.name}"
                    )
                except OSError as e:
                    logger.warning(
                        f"Job {job_id}: could not delete skipped file {path.name}: {e}"
                    )
                await self._finalization.check_job_completion(session, job_id)
                return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_skip_rip_title.py::test_on_title_ripped_deletes_skipped_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_skip_rip_title.py
git commit -m "feat(jobs): delete skipped title's file on completion in all-pass"
```

---

## Task 6: `_run_ripping` treats SKIPPED like deselected

Prevent the safety-net at `_run_ripping` from re-marking a SKIPPED title as extra/COMPLETED, and ensure it is excluded from the rip selection.

**Files:**
- Modify: `backend/app/services/job_manager.py:2114-2154`
- Test: covered by the integration test in Task 8 (no separate unit test — this is a filter tweak verified end-to-end).

- [ ] **Step 1: Update the selection + safety-net filters**

In `_run_ripping`, the `has_selection` loop already skips non-selected titles (a SKIPPED title has `is_selected=False`, so it is excluded from `titles_to_rip` automatically). Update only the deselected-safety-net block so it does not touch SKIPPED titles. Change:

```python
                deselected_ids = [
                    dt.id
                    for dt in disc_titles
                    if not dt.is_selected and dt.state == TitleState.PENDING
                ]
```

to:

```python
                # SKIPPED titles are already terminal — leave them alone. Only a
                # deselected-but-still-PENDING title needs the safety-net nudge.
                deselected_ids = [
                    dt.id
                    for dt in disc_titles
                    if not dt.is_selected
                    and dt.state == TitleState.PENDING
                    and dt.state != TitleState.SKIPPED
                ]
```

(The `state == PENDING` guard already excludes SKIPPED; the explicit clause documents intent and is defensive against future reordering.)

- [ ] **Step 2: Run the job_manager unit + pipeline suites**

Run: `cd backend && uv run pytest tests/unit/ tests/pipeline/ -k "rip or ripping or select" -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/job_manager.py
git commit -m "refactor(jobs): treat SKIPPED titles as terminal in _run_ripping"
```

---

## Task 7: Completion logic treats SKIPPED as neutral

`SKIPPED` is auto-excluded from `active_states`, but the "unresolved" query and the outcome booleans need updating so a partially- or fully-skipped disc completes correctly.

**Files:**
- Modify: `backend/app/services/finalization_coordinator.py` (completion decision ~713-808; unresolved query ~1477-1484)
- Test: `backend/tests/unit/test_completion_skipped.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_completion_skipped.py
import pytest
from sqlalchemy import text

from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def _clean_db():
    await init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _job_with_titles(states):
    async with async_session() as s:
        job = DiscJob(drive_id="Z:", volume_label="T", state=JobState.MATCHING,
                      content_type=ContentType.MOVIE, staging_path="/tmp/x")
        s.add(job)
        await s.commit()
        await s.refresh(job)
        for i, st in enumerate(states):
            s.add(DiscTitle(job_id=job.id, title_index=i, duration_seconds=100,
                            state=st, is_selected=(st != TitleState.SKIPPED),
                            matched_episode=("S01E01" if st == TitleState.MATCHED else None)))
        await s.commit()
        return job.id


async def _final_state(job_id):
    async with async_session() as s:
        return (await s.get(DiscJob, job_id)).state


async def test_all_skipped_completes():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.SKIPPED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.COMPLETED


async def test_skipped_plus_completed_completes():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.COMPLETED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.COMPLETED


async def test_skipped_plus_failed_is_failed():
    job_id = await _job_with_titles([TitleState.SKIPPED, TitleState.FAILED])
    async with async_session() as s:
        await job_manager._finalization.check_job_completion(s, job_id)
    assert await _final_state(job_id) == JobState.FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_completion_skipped.py -v`
Expected: FAIL — `test_skipped_plus_failed_is_failed` and/or `test_all_skipped` land in the wrong state (all_failed computed over the full list, or the subtitle/escalation path misfires).

- [ ] **Step 3: Compute outcome over non-skipped titles + short-circuit all-skipped**

In `check_job_completion`, after the `active_titles` empty-check (~line 711, before `has_matched = ...`), insert the all-skipped short-circuit and a non-skipped working set:

```python
        # All titles are terminal
        logger.info(f"All titles for job {job_id} effectively processed. Finalizing...")

        # SKIPPED titles are user-excluded and neutral to the outcome. If nothing
        # else remains, the disc is done with nothing to organize.
        matchable = [t for t in titles if t.state != TitleState.SKIPPED]
        if not matchable:
            logger.info(
                f"Job {job_id}: all titles skipped by user — completing with nothing organized"
            )
            job.progress_percent = 100.0
            await self._state_machine.transition_to_completed(job, session)
            return
```

Then change the outcome booleans to use `matchable` instead of `titles`:

```python
        has_matched = any(t.state == TitleState.MATCHED for t in matchable)
        has_review = any(t.state == TitleState.REVIEW for t in matchable)
        has_completed = any(t.state == TitleState.COMPLETED for t in matchable)
        all_failed = all(t.state == TitleState.FAILED for t in matchable)
```

Also pass `matchable` (not `titles`) to the wrong-show, no-reference-subtitle, and escalation helpers so a skipped track never triggers deep re-match or a subtitle-review misfire:

```python
        wrong_show = _detect_wrong_show(job, matchable)
        ...
        if _no_reference_subtitles(job, matchable):
        ...
        if await self._maybe_escalate_conflicts(session, job, matchable):
        ...
        if await self._maybe_escalate_reviews(
            session, job, matchable, wrong_show_suspected=bool(wrong_show)
        ):
```

Leave the `has_review` count message using `titles` or `matchable` consistently — use `matchable`:

```python
                f"{sum(1 for t in matchable if t.state == TitleState.REVIEW)} title(s) need manual episode assignment",
```

- [ ] **Step 4: Exclude SKIPPED from the unresolved query**

At `finalization_coordinator.py:1481`, change:

```python
                DiscTitle.state.notin_([TitleState.COMPLETED, TitleState.FAILED]),
```

to:

```python
                DiscTitle.state.notin_(
                    [TitleState.COMPLETED, TitleState.FAILED, TitleState.SKIPPED]
                ),
```

Search the file for any other `not_in([TitleState.COMPLETED, TitleState.FAILED])` / `notin_([TitleState.COMPLETED, TitleState.FAILED])` used to mean "still needs work" (grep below) and add `TitleState.SKIPPED` to each. The organize queries that also require `matched_episode.isnot(None)` do not need it (a SKIPPED title has no `matched_episode`), but adding it is harmless and consistent.

Run: `cd backend && grep -n "COMPLETED, TitleState.FAILED" backend/app/services/finalization_coordinator.py`
For each hit that is a "not terminal" filter, add `TitleState.SKIPPED`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_completion_skipped.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Regression-check completion**

Run: `cd backend && uv run pytest tests/ -k "completion or finaliz" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/finalization_coordinator.py backend/tests/unit/test_completion_skipped.py
git commit -m "feat(finalization): treat SKIPPED titles as neutral to completion"
```

---

## Task 8: API endpoints + integration test

**Files:**
- Modify: `backend/app/api/routes.py` (near the existing `/skip` + `/rerip` ~975-1016)
- Test: `backend/tests/integration/test_track_skipping.py` (create)

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_track_skipping.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _clean_db():
    await init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _seed():
    async with async_session() as s:
        job = DiscJob(drive_id="Z:", volume_label="T", state=JobState.RIPPING,
                      content_type=ContentType.TV, staging_path="/tmp/x")
        s.add(job)
        await s.commit()
        await s.refresh(job)
        t = DiscTitle(job_id=job.id, title_index=4, duration_seconds=300,
                      state=TitleState.PENDING, is_selected=True)
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return job.id, t.id


async def test_skip_and_unskip_endpoints(client):
    job_id, title_id = await _seed()

    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/skip-rip")
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    async with async_session() as s:
        assert (await s.get(DiscTitle, title_id)).state == TitleState.SKIPPED

    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/unskip-rip")
    assert r.status_code == 200
    async with async_session() as s:
        assert (await s.get(DiscTitle, title_id)).state == TitleState.PENDING


async def test_skip_rejects_terminal_job(client):
    job_id, title_id = await _seed()
    async with async_session() as s:
        job = await s.get(DiscJob, job_id)
        job.state = JobState.COMPLETED
        await s.commit()
    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/skip-rip")
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_track_skipping.py -v`
Expected: FAIL with 404 (routes not defined).

- [ ] **Step 3: Add the endpoints**

In `backend/app/api/routes.py`, after the existing `rerip_title` route (~line 1016):

```python
@router.post("/jobs/{job_id}/titles/{title_id}/skip-rip")
async def skip_rip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Skip a queued/not-yet-ripped title so MakeMKV does not rip it."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    ok = await job_manager.skip_rip_title(job.id, title_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Title not found, not part of this job, or not skippable "
            "(only queued/not-yet-ripped titles can be skipped)",
        )
    return {"status": "skipped", "job_id": job.id, "title_id": title_id}


@router.post("/jobs/{job_id}/titles/{title_id}/unskip-rip")
async def unskip_rip_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
) -> dict:
    """Reverse a skip while the title's file has not been written yet."""
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        raise HTTPException(status_code=400, detail="Job has already finished")

    from app.services.job_manager import job_manager

    ok = await job_manager.unskip_rip_title(job.id, title_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Title not found or no longer un-skippable (already ripped)",
        )
    return {"status": "unskipped", "job_id": job.id, "title_id": title_id}
```

(`JobState`, `HTTPException`, `Depends`, `get_job_or_404`, `DiscJob` are already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_track_skipping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_track_skipping.py
git commit -m "feat(api): add skip-rip / unskip-rip title endpoints"
```

---

## Task 9: Backend lint + full suite gate

- [ ] **Step 1: Lint + format**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: no errors. Run `uv run ruff format .` if formatting differs, then re-check.

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS. Investigate and fix any regression before proceeding (do not skip).

- [ ] **Step 3: Commit any lint/format fixups**

```bash
git add -A
git commit -m "chore: lint + format for track skipping" || echo "nothing to commit"
```

---

## Task 10: Frontend `skipped` state plumbing

**Files:**
- Modify: `frontend/src/types/index.ts:15`
- Modify: `frontend/src/types/adapters.ts:29-38`
- Modify: `frontend/src/app/components/DiscCard.tsx:18`
- Modify: `frontend/src/api/client.ts` (near `reripTitle` ~191)

- [ ] **Step 1: Add `'skipped'` to the backend title-state union**

`frontend/src/types/index.ts:15`:

```typescript
export type TitleState = 'pending' | 'ripping' | 'queued' | 'matching' | 'matched' | 'review' | 'completed' | 'failed' | 'skipped';
```

- [ ] **Step 2: Add `'skipped'` to `TrackState`**

`frontend/src/app/components/DiscCard.tsx:18`:

```typescript
export type TrackState = "pending" | "ripping" | "queued" | "matching" | "matched" | "review" | "failed" | "completed" | "skipped";
```

- [ ] **Step 3: Map it in the adapter**

`frontend/src/types/adapters.ts`, inside the `stateMap`:

```typescript
  const stateMap: Record<BackendTitleState, TrackState> = {
    'pending': 'pending',
    'ripping': 'ripping',
    'queued': 'queued',
    'matching': 'matching',
    'matched': 'matched',
    'review': 'review',
    'completed': 'completed',
    'failed': 'failed',
    'skipped': 'skipped'
  };
```

- [ ] **Step 4: Add API client helpers**

`frontend/src/api/client.ts`, after `reripTitle`:

```typescript
/** Skip a queued/not-yet-ripped title so MakeMKV does not rip it. */
export async function skipRipTitle(jobId: number, titleId: number): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/skip-rip`, { method: 'POST' });
}

/** Reverse a skip while the title has not been ripped yet. */
export async function unskipRipTitle(jobId: number, titleId: number): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/titles/${titleId}/unskip-rip`, { method: 'POST' });
}
```

- [ ] **Step 5: Verify the TypeScript build compiles**

Run: `cd frontend && npm run build`
Expected: PASS. If `TrackGrid.tsx`'s `STATE` record now errors as non-exhaustive (missing `skipped`), that is expected and fixed in Task 11 — you may proceed to Task 11 before re-running.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/types/adapters.ts frontend/src/app/components/DiscCard.tsx frontend/src/api/client.ts
git commit -m "feat(ui): add skipped track state + skip API client helpers"
```

---

## Task 11: TrackGrid skip / un-skip controls (frontend-design skill)

**REQUIRED SUB-SKILL:** Use `frontend-design:frontend-design` to design the skip (✕) and un-skip controls and the `SKIPPED` badge so they match the Synapse v2 brand system (sharp panels, mono type, cyan/magenta accents, corner ticks). The code below is a correct, wired baseline — apply the frontend-design skill's visual treatment on top of it (do not leave it visually generic).

**Files:**
- Modify: `frontend/src/app/components/TrackGrid.tsx` (STATE map ~34-45; props ~10-17; card render ~145-247)
- Modify: `frontend/src/app/components/DiscCard.tsx` (props + the 4 `<TrackGrid ... />` usages at 682, 772, 815, 825)

- [ ] **Step 1: Add the `skipped` STATE entry**

In `TrackGrid.tsx` `STATE`:

```typescript
  skipped:   { label: "SKIPPED",  color: sv.inkDim,  border: `${sv.inkDim}44`,     bg: `${sv.bg2}55`, Icon: null       },
```

- [ ] **Step 2: Add skip/un-skip props**

Extend `TrackGridProps`:

```typescript
interface TrackGridProps {
  tracks: Track[];
  conflictStatus?: string;
  /** Skip a queued/not-yet-ripped track (PENDING/QUEUED). Omit to hide the control. */
  onSkipTrack?: (titleId: number) => void;
  /** Reverse a skip while still reversible (SKIPPED, not yet ripped). */
  onUnskipTrack?: (titleId: number) => void;
}
```

And the component signature:

```typescript
export const TrackGrid = React.memo(function TrackGrid({ tracks, conflictStatus, onSkipTrack, onUnskipTrack }: TrackGridProps) {
```

- [ ] **Step 3: Render the controls**

Inside the card's top-right control cluster (the `<div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>` that holds the spinner `Icon`), add before/after the `Icon`:

```tsx
                  {onSkipTrack && (track.state === "pending" || track.state === "queued") && (
                    <button
                      type="button"
                      data-testid={`skip-track-${track.id}`}
                      aria-label={`Skip track ${track.title}`}
                      title="Skip this track (don't rip it)"
                      onClick={(e) => { e.stopPropagation(); onSkipTrack(track.id); }}
                      style={{
                        fontFamily: sv.mono, fontSize: 10, letterSpacing: "0.12em",
                        color: sv.inkDim, background: "transparent",
                        border: `1px solid ${sv.line}`, padding: "2px 6px", cursor: "pointer",
                      }}
                    >
                      SKIP ✕
                    </button>
                  )}
                  {onUnskipTrack && track.state === "skipped" && (
                    <button
                      type="button"
                      data-testid={`unskip-track-${track.id}`}
                      aria-label={`Un-skip track ${track.title}`}
                      title="Un-skip (rip this track after all)"
                      onClick={(e) => { e.stopPropagation(); onUnskipTrack(track.id); }}
                      style={{
                        fontFamily: sv.mono, fontSize: 10, letterSpacing: "0.12em",
                        color: sv.cyan, background: "transparent",
                        border: `1px solid ${sv.cyan}44`, padding: "2px 6px", cursor: "pointer",
                      }}
                    >
                      UN-SKIP
                    </button>
                  )}
```

Add a `skipped` body block near the `pending` one (~line 268) so the card reads clearly:

```tsx
              {track.state === "skipped" && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkDim, letterSpacing: "0.18em" }}>
                    SKIPPED — WILL NOT RIP
                  </span>
                </div>
              )}
```

- [ ] **Step 4: Thread the handlers through DiscCard**

In `DiscCard.tsx`, add to the props interface (near `onCancel?` ~111):

```typescript
  onSkipTrack?: (titleId: number) => void;
  onUnskipTrack?: (titleId: number) => void;
```

Destructure them in the component signature (~216) and pass to all four `<TrackGrid ... />` usages, e.g.:

```tsx
<TrackGrid tracks={disc.tracks} conflictStatus={disc.conflictStatus} onSkipTrack={onSkipTrack} onUnskipTrack={onUnskipTrack} />
```

(Add the two props to each of the four `<TrackGrid>` sites at lines 682, 772, 815, 825.)

- [ ] **Step 5: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: PASS (STATE record now exhaustive; props typed).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/TrackGrid.tsx frontend/src/app/components/DiscCard.tsx
git commit -m "feat(ui): skip / un-skip controls + SKIPPED badge in TrackGrid"
```

---

## Task 12: Wire handlers at the App level

**Files:**
- Modify: `frontend/src/app/App.tsx` (where `<DiscCard>` is rendered and `onCancel` is passed)

- [ ] **Step 1: Locate the DiscCard render + cancel wiring**

Run: `cd frontend && grep -rn "onCancel=" src/app/App.tsx`
Identify the `<DiscCard ... onCancel={...} />` site and how a job id is in scope there (the same pattern `onCancel` uses to know its job).

- [ ] **Step 2: Add skip handlers next to the existing cancel handler**

Import the client helpers at the top of `App.tsx`:

```typescript
import { skipRipTitle, unskipRipTitle } from "../api/client";
```

At the `<DiscCard>` render, using the same `job.id` (or equivalent) already used for `onCancel`, add:

```tsx
  onSkipTrack={(titleId) => { void skipRipTitle(job.id, titleId); }}
  onUnskipTrack={(titleId) => { void unskipRipTitle(job.id, titleId); }}
```

The WebSocket `title_update` broadcast from the backend drives the UI state change, so no local optimistic update is required (this matches how `reripTitle` / `onCancel` already rely on server push). If `App.tsx` uses a memoized job id via a hook, follow that same pattern.

- [ ] **Step 3: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/App.tsx
git commit -m "feat(ui): wire skip / un-skip track handlers to the API"
```

---

## Task 13: E2E test (skip / un-skip flow)

**Files:**
- Create: `frontend/e2e/track-skipping.spec.ts`

- [ ] **Step 1: Write the E2E test**

Model it on an existing spec (`cd frontend && ls e2e/`, then read the disc-flow spec for the simulation + selector helpers used in this repo). Concretely:

```typescript
// frontend/e2e/track-skipping.spec.ts
import { test, expect } from "@playwright/test";

// Requires backend running with DEBUG=true (simulation endpoints).
test("skip a queued track then un-skip it", async ({ page, request }) => {
  // Insert a TV disc that ripples slowly so tracks sit in PENDING/QUEUED.
  await request.post("http://localhost:8000/api/simulate/insert-disc", {
    data: { volume_label: "SKIP_TEST_S1D1", content_type: "tv", simulate_ripping: true },
  });

  await page.goto("/");
  const skipBtn = page.getByTestId(/^skip-track-\d+$/).first();
  await skipBtn.waitFor({ state: "visible", timeout: 15000 });
  await skipBtn.click();

  // A SKIPPED badge appears and an un-skip control replaces the skip control.
  await expect(page.getByText("SKIPPED — WILL NOT RIP").first()).toBeVisible();
  const unskipBtn = page.getByTestId(/^unskip-track-\d+$/).first();
  await expect(unskipBtn).toBeVisible();

  await unskipBtn.click();
  await expect(page.getByTestId(/^skip-track-\d+$/).first()).toBeVisible();

  // Cleanup
  await request.delete("http://localhost:8000/api/simulate/reset-all-jobs");
});
```

Adjust selectors/timeouts to match the repo's existing E2E conventions (reduced-motion + `animations: 'disabled'` for stability if the spec captures screenshots; not required here).

- [ ] **Step 2: Run the E2E test**

Start the backend with `DEBUG=true` and the frontend dev server per CLAUDE.md, then:
Run: `cd frontend && npx playwright test e2e/track-skipping.spec.ts`
Expected: PASS. If the skip button never appears, confirm the simulated rip keeps at least one track in PENDING/QUEUED long enough (increase title count or rip duration in the sim payload).

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/track-skipping.spec.ts
git commit -m "test(e2e): skip / un-skip track flow"
```

---

## Task 14: Changelog

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` section)

- [ ] **Step 1: Add an entry under `[Unreleased] > ### Added`**

```markdown
### Added
- Manual track skipping: skip a queued or not-yet-ripped track (e.g. a bogus play-all title) from the dashboard so MakeMKV does not waste time ripping it. Skipped tracks show a distinct SKIPPED badge, are excluded from the library, and can be un-skipped until ripping reaches them. (#NNN)
```

Replace `#NNN` with the PR number once the PR is open.

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): manual track skipping"
```

---

## Self-Review Notes (author checklist, already applied)

- **Spec coverage:** model (Task 1), extractor best-effort per-title skip (Tasks 2-3), service methods (Task 4), mid-`all`-pass delete (Task 5), `_run_ripping` (Task 6), completion neutrality incl. all-skipped + unresolved-query fix (Task 7), API (Task 8), frontend state + controls + wiring (Tasks 10-12), tests (Tasks 4/5/7/8/13), changelog (Task 14). No migration (verified: no CHECK constraint on `disc_titles.state`).
- **Naming consistency:** backend `skip_rip_title` / `unskip_rip_title`, extractor `skip_title_index` / `unskip_title_index` / `_skipped_indices`, endpoints `skip-rip` / `unskip-rip`, frontend `skipRipTitle` / `unskipRipTitle` / `onSkipTrack` / `onUnskipTrack`, state literal `skipped` / `SKIPPED` — used identically across every task.
- **Verification items from the spec:** no-CHECK-constraint confirmed during planning; other active/terminal state enumerations handled in Task 7 Step 4 (grep-and-fix) and Task 6.
