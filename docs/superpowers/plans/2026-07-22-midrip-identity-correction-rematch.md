# Mid-rip Identity Correction Re-match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a mid-rip identity *correction* (`re_identify` while `RIPPING`) re-match titles that were already matched under the wrong-but-unblocked identity, instead of leaving them and forcing the user to re-identify again at rip end.

**Architecture:** When `re_identify` changes the *show* (`detected_title` or `tmdb_id`) mid-rip, the coordinator returns a new `resume_action = "rematch_ripped"`. `JobManager._rematch_ripped_titles` resets already-ripped, not-yet-organized titles to `QUEUED` (cancelling their in-flight match tasks) and re-dispatches them against the corrected identity; titles still ripping dispatch on completion via the existing gate. The stale subtitle download is cancelled and restarted for the corrected show.

**Tech Stack:** Python 3.11+, FastAPI, async SQLModel/SQLAlchemy, asyncio, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-07-22-midrip-identity-correction-rematch-design.md`

**Working directory for all commands:** `backend/` (i.e. `cd backend` first; commands shown assume that CWD).

---

## Task 1: Track per-title match tasks (enables cancellation)

**Files:**
- Modify: `backend/app/services/job_manager.py` (`__init__` ~line 182; `_dispatch_title_match` ~line 3287; `_on_match_dispatch_done` ~line 3293)
- Test: `backend/tests/unit/test_midrip_rematch.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_midrip_rematch.py`:

```python
"""Mid-rip identity correction re-match (spec 2026-07-22)."""

import asyncio
import json

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_coordinator_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.identification_coordinator.async_session", _unit_session_factory
    )


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


async def _seed_job(**kwargs):
    kwargs.setdefault("content_type", ContentType.TV)
    kwargs.setdefault("detected_title", "Show A")
    kwargs.setdefault("identity_prompt_json", None)
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_A_S1D1",
            state=JobState.RIPPING,
            staging_path=kwargs.pop("staging", "/tmp/staging/repro"),
            **kwargs,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _add_title(job_id, index, state, output=None, **kwargs):
    async with _unit_session_factory() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=1380,
            state=state,
            output_filename=output,
            is_selected=True,
            **kwargs,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t


async def _get_job(job_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


async def _get_title(title_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.unit
async def test_dispatch_title_match_tracks_then_clears_task(monkeypatch, tmp_path):
    released = asyncio.Event()
    started = asyncio.Event()

    async def fake_match(job_id, title_id, file_path):
        started.set()
        await released.wait()

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))

    assert await job_manager._dispatch_title_match(job.id, t.id, f)
    await started.wait()
    task = job_manager._match_tasks.get(t.id)
    assert task is not None  # tracked while running

    released.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert t.id not in job_manager._match_tasks  # cleared on completion
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_midrip_rematch.py::test_dispatch_title_match_tracks_then_clears_task -v`
Expected: FAIL — `AttributeError: 'JobManager' object has no attribute '_match_tasks'`.

- [ ] **Step 3: Add the `_match_tasks` map in `__init__`**

In `backend/app/services/job_manager.py`, immediately after the `self._inflight_match_dispatch: set[int] = set()` line (~182), add:

```python
        # Live match task per title id, so a mid-rip identity correction can
        # cancel a stale match (running under the OLD identity) before
        # re-dispatching the title against the corrected show. Populated in
        # _dispatch_title_match; removed by the task's done callback.
        self._match_tasks: dict[int, asyncio.Task] = {}
```

- [ ] **Step 4: Record the task in `_dispatch_title_match`**

Replace the task-creation tail of `_dispatch_title_match` (~3287-3291):

```python
        task = asyncio.create_task(self._matching.match_single_file(job_id, title_id, file_path))
        task.add_done_callback(
            lambda t, jid=job_id, tid=title_id: self._on_match_dispatch_done(t, jid, tid)
        )
        return True
```

with:

```python
        task = asyncio.create_task(self._matching.match_single_file(job_id, title_id, file_path))
        self._match_tasks[title_id] = task
        task.add_done_callback(
            lambda t, jid=job_id, tid=title_id: self._on_match_dispatch_done(t, jid, tid)
        )
        return True
```

- [ ] **Step 5: Clear the entry in `_on_match_dispatch_done`**

Replace `_on_match_dispatch_done` (~3293-3296):

```python
    def _on_match_dispatch_done(self, task: asyncio.Task, job_id: int, title_id: int) -> None:
        """Release the in-flight dispatch guard, then run the matching done callback."""
        self._inflight_match_dispatch.discard(title_id)
        self._matching.on_match_task_done(task, job_id, title_id)
```

with:

```python
    def _on_match_dispatch_done(self, task: asyncio.Task, job_id: int, title_id: int) -> None:
        """Release the in-flight dispatch guard, then run the matching done callback."""
        self._inflight_match_dispatch.discard(title_id)
        self._match_tasks.pop(title_id, None)
        self._matching.on_match_task_done(task, job_id, title_id)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_midrip_rematch.py::test_dispatch_title_match_tracks_then_clears_task -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/job_manager.py tests/unit/test_midrip_rematch.py
git commit -m "feat(rematch): track per-title match tasks for mid-rip cancellation"
```

---

## Task 2: `_rematch_ripped_titles` executor + `rematch_ripped` resume action

**Files:**
- Modify: `backend/app/services/identity_prompts.py` (`ResumeAction` ~line 25)
- Modify: `backend/app/services/job_manager.py` (`_apply_identity_resume_action` ~line 881; add `_rematch_ripped_titles`)
- Test: `backend/tests/unit/test_midrip_rematch.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_midrip_rematch.py`:

```python
@pytest.mark.unit
async def test_rematch_ripped_resets_and_dispatches_matched_title(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(
        job.id, 0, TitleState.MATCHED, output=str(f),
        matched_episode="S01E01", match_confidence=0.9,
        match_details=json.dumps({"reason": "old show"}),
    )

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    refreshed = await _get_title(t.id)
    assert refreshed.state == TitleState.QUEUED
    assert refreshed.matched_episode is None
    assert refreshed.match_confidence == 0.0
    assert refreshed.match_details is None
    assert t.id in dispatched


@pytest.mark.unit
async def test_rematch_ripped_leaves_ripping_titles_alone(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    still_ripping = await _add_title(job.id, 1, TitleState.RIPPING)  # no file yet

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert (await _get_title(still_ripping.id)).state == TitleState.RIPPING
    assert still_ripping.id not in dispatched


@pytest.mark.unit
async def test_rematch_ripped_cancels_inflight_match(monkeypatch, tmp_path):
    released = asyncio.Event()
    started = asyncio.Event()
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        # First dispatch blocks (old identity); re-dispatch is recorded.
        dispatched.append(title_id)
        if len(dispatched) == 1:
            started.set()
            await released.wait()

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job()
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.QUEUED, output=str(f))

    # Kick the first (old-identity) match; it parks in fake_match.
    await job_manager._dispatch_title_match(job.id, t.id, f)
    await started.wait()
    old_task = job_manager._match_tasks[t.id]

    # Release the parked task the instant it is cancelled so gather() returns.
    def _release_on_cancel():
        released.set()
    old_task.add_done_callback(lambda _t: _release_on_cancel())

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert old_task.cancelled() or old_task.done()
    assert dispatched.count(t.id) == 2  # re-dispatched after cancel


@pytest.mark.unit
async def test_rematch_ripped_skips_non_tv(monkeypatch, tmp_path):
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job(content_type=ContentType.MOVIE, detected_title="A Movie")
    f = tmp_path / "MOVIE_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.MATCHED, output=str(f))

    await job_manager._apply_identity_resume_action(job.id, "rematch_ripped")
    await asyncio.sleep(0)

    assert dispatched == []
    assert (await _get_title(t.id)).state == TitleState.MATCHED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k rematch_ripped -v`
Expected: FAIL — `ValueError: Unknown identity resume action: 'rematch_ripped'`.

- [ ] **Step 3: Add `"rematch_ripped"` to the `ResumeAction` type**

In `backend/app/services/identity_prompts.py`, replace the `ResumeAction` definition (~25-31):

```python
ResumeAction = Literal[
    "start_rip",
    "dispatch_matches",
    "release_movie_titles",
    "resolve_movie",
    "rerun_matching",
]
```

with:

```python
ResumeAction = Literal[
    "start_rip",
    "dispatch_matches",
    "release_movie_titles",
    "resolve_movie",
    "rerun_matching",
    "rematch_ripped",
]
```

- [ ] **Step 4: Wire `rematch_ripped` into `_apply_identity_resume_action`**

In `backend/app/services/job_manager.py`, in `_apply_identity_resume_action`, add a new inline branch right after the `release_movie_titles` block (~896, before the `coro_factory = {` dict):

```python
        if action == "release_movie_titles":
            await self._release_parked_movie_titles(job_id)
            return
        if action == "rematch_ripped":
            # Mid-rip identity CHANGE: re-match already-ripped titles inline
            # (the live rip task stays the registered _active_jobs owner — do
            # NOT spawn a task here; same double-rip guard as dispatch_matches).
            await self._rematch_ripped_titles(job_id)
            return
```

- [ ] **Step 5: Add the `_rematch_ripped_titles` method**

In `backend/app/services/job_manager.py`, add this method next to `dispatch_pending_matches` (anywhere in the class; e.g. immediately after `dispatch_pending_matches` ends ~line 3353):

```python
    async def _rematch_ripped_titles(self, job_id: int) -> None:
        """Re-match already-ripped titles after a mid-rip identity CHANGE.

        Rip-safe analogue of ``_rerun_matching`` for a job still RIPPING: only
        titles whose rip is done (file present) and that are not yet organized
        are reset to QUEUED and re-matched against the corrected identity. Titles
        still ripping (PENDING/RIPPING) are left alone — they dispatch under the
        new identity via ``_on_title_ripped`` once they finish (the prompt is
        already cleared by the answer endpoint). Any in-flight match task for an
        affected title is cancelled and AWAITED first, so its stale done-callback
        settles before the fresh re-dispatch (no bookkeeping clobber). Caller
        contract mirrors ``dispatch_pending_matches``: TV only.
        """
        resettable = (
            TitleState.QUEUED,
            TitleState.MATCHING,
            TitleState.MATCHED,
            TitleState.REVIEW,
        )
        affected: list[tuple[int, Path]] = []
        cancelled: list[asyncio.Task] = []
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job and job.content_type != ContentType.TV:
                logger.error(
                    f"Job {sanitize_log_value(job_id)}: _rematch_ripped_titles called on "
                    f"non-TV job (content_type={job.content_type}) — skipping"
                )
                return
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            for t in result.scalars().all():
                if not t.is_selected or t.state not in resettable or not t.output_filename:
                    continue
                file_path = Path(t.output_filename)
                if not file_path.exists():
                    continue
                affected.append((t.id, file_path))
                old = self._match_tasks.pop(t.id, None)
                if old is not None and not old.done():
                    old.cancel()
                    cancelled.append(old)

        # Await cancellations OUTSIDE the session so each stale done-callback
        # (_inflight_match_dispatch discard + on_match_task_done) runs before the
        # fresh dispatch below. Idempotent; return_exceptions swallows the
        # CancelledError / any late failure.
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)

        async with async_session() as session:
            for title_id, _ in affected:
                title = await session.get(DiscTitle, title_id)
                if title is None:
                    continue
                title.state = TitleState.QUEUED
                title.matched_episode = None
                title.match_confidence = 0.0
                title.match_details = None
                session.add(title)
            await session.commit()

        dispatched = 0
        for title_id, file_path in affected:
            self._inflight_match_dispatch.discard(title_id)
            await ws_manager.broadcast_title_update(job_id, title_id, TitleState.QUEUED.value)
            if await self._dispatch_title_match(job_id, title_id, file_path):
                dispatched += 1
        logger.info(
            f"Job {sanitize_log_value(job_id)}: mid-rip identity change → "
            f"re-matching {dispatched} ripped title(s)"
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k rematch_ripped -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add app/services/identity_prompts.py app/services/job_manager.py tests/unit/test_midrip_rematch.py
git commit -m "feat(rematch): rematch_ripped resume action + rip-safe executor"
```

---

## Task 3: `re_identify` returns `rematch_ripped` on a mid-rip show change

**Files:**
- Modify: `backend/app/services/identification_coordinator.py` (`re_identify` ~1348-1460)
- Test: `backend/tests/unit/test_midrip_rematch.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_midrip_rematch.py`:

```python
def _stub_reidentify_network(monkeypatch):
    """Keep re_identify off the network (TMDB re-lookup, subtitle, year)."""
    from unittest.mock import AsyncMock

    coord = job_manager._identification
    monkeypatch.setattr(coord, "_start_tv_subtitle_prefetch", AsyncMock())
    monkeypatch.setattr(coord, "_cancel_subtitle_download", AsyncMock(), raising=False)
    monkeypatch.setattr(coord, "_restart_subtitle_download", AsyncMock())
    monkeypatch.setattr(
        "app.services.identification_coordinator._resolve_show_year",
        lambda tmdb_id, signal: None,
    )


@pytest.mark.unit
async def test_midrip_reidentify_show_change_rematches_processed_title(monkeypatch, tmp_path):
    _stub_reidentify_network(monkeypatch)
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job(detected_title="Show A", tmdb_id=111)
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.MATCHED, output=str(f), match_confidence=0.9)

    await job_manager.re_identify_job(job.id, "Show B", "tv", season=1, tmdb_id=999)
    await asyncio.sleep(0)

    assert t.id in dispatched  # already-matched title re-matched against corrected show
    assert (await _get_title(t.id)).match_confidence == 0.0


@pytest.mark.unit
async def test_midrip_reidentify_unchanged_show_preserves_matches(monkeypatch, tmp_path):
    _stub_reidentify_network(monkeypatch)
    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job(detected_title="Show A", tmdb_id=111)
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    t = await _add_title(job.id, 0, TitleState.MATCHED, output=str(f), match_confidence=0.9)

    # Same title + tmdb_id → season-only/no-op change, NOT a show change.
    await job_manager.re_identify_job(job.id, "Show A", "tv", season=1, tmdb_id=111)
    await asyncio.sleep(0)

    assert dispatched == []  # dispatch_matches path — MATCHED title untouched
    assert (await _get_title(t.id)).state == TitleState.MATCHED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k midrip_reidentify -v`
Expected: `test_midrip_reidentify_show_change_rematches_processed_title` FAILS (`t.id not in dispatched` — mid-rip still returns `dispatch_matches`); the unchanged-show test PASSES already.

- [ ] **Step 3: Capture the pre-answer identity in `re_identify`**

In `backend/app/services/identification_coordinator.py`, in `re_identify`, right after the `mid_rip = job.state == JobState.RIPPING` line (~1348), add:

```python
            # Pre-answer identity, to detect a genuine SHOW change (title or
            # tmdb_id) vs. a season-only refinement — only a show change tears
            # down and re-matches already-processed titles mid-rip (spec
            # 2026-07-22).
            _prev_title = job.detected_title
            _prev_tmdb_id = job.tmdb_id
```

- [ ] **Step 4: Return `rematch_ripped` when the show changed mid-rip**

In the same method, replace the mid-rip branch (~1433-1443):

```python
            if mid_rip:
                # Mid-rip answer: metadata only — NO state change and NO new
                # tasks; the running rip continues and the rip-end re-read
                # (B4) picks up the corrected identity.
                target_state = JobState.RIPPING
                resume_action: ResumeAction = mid_rip_resume_action(is_tv)
                # The identify-time prefetch was skipped while the identity
                # question was open (B2 gates B/C) — kick it now. Known season
                # → that season; unknown → all seasons (cross-season matching).
                if is_tv and job.detected_title:
                    await self._start_tv_subtitle_prefetch(job)
```

with:

```python
            show_changed = (job.detected_title != _prev_title) or (job.tmdb_id != _prev_tmdb_id)
            mid_rip_subtitle_refresh = False
            if mid_rip:
                # Mid-rip answer: metadata only — NO state change and NO new
                # tasks; the running rip continues and the rip-end re-read (B4)
                # picks up the corrected identity. A genuine SHOW change re-matches
                # titles already processed under the old identity (dispatch_matches
                # would only release still-parked QUEUED titles); an unchanged show
                # (season-only) keeps the cheap release.
                target_state = JobState.RIPPING
                if is_tv and show_changed:
                    resume_action: ResumeAction = "rematch_ripped"
                else:
                    resume_action = mid_rip_resume_action(is_tv)
                # Subtitle refresh is deferred to AFTER the session commit (below)
                # so cancel_subtitle_download can open its own session without
                # self-deadlocking on this connection.
                mid_rip_subtitle_refresh = bool(is_tv and job.detected_title)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k midrip_reidentify -v`
Expected: PASS (2 tests). `_cancel_subtitle_download` is stubbed by `_stub_reidentify_network`; the real wiring lands in Task 4.

- [ ] **Step 6: Commit**

```bash
git add app/services/identification_coordinator.py tests/unit/test_midrip_rematch.py
git commit -m "feat(rematch): re_identify returns rematch_ripped on mid-rip show change"
```

---

## Task 4: Cancel + restart the stale subtitle download on a mid-rip show change

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (`restart_subtitle_download` ~278; add `cancel_subtitle_download`)
- Modify: `backend/app/services/identification_coordinator.py` (`__init__` ~240, `set_callbacks` ~247, `re_identify` post-session block ~1499-1502)
- Modify: `backend/app/services/job_manager.py` (`_identification.set_callbacks` call ~205)
- Test: `backend/tests/unit/test_midrip_rematch.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_midrip_rematch.py`:

```python
@pytest.mark.unit
async def test_cancel_subtitle_download_cancels_and_clears_status(monkeypatch, tmp_path):
    from sqlalchemy import update as _sqla_update

    mc = job_manager._matching

    async def _never_finish(job_id, show_name, season, tmdb_id=None):
        await asyncio.Event().wait()

    monkeypatch.setattr(mc, "download_subtitles", _never_finish)

    async def _quiet_sub_event(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _quiet_sub_event)

    job = await _seed_job()
    async with _unit_session_factory() as session:
        await session.execute(
            _sqla_update(DiscJob).where(DiscJob.id == job.id).values(subtitle_status="failed")
        )
        await session.commit()

    mc.start_subtitle_download(job.id, "Show A", 1)
    old_task = mc._subtitle_tasks[job.id]

    await mc.cancel_subtitle_download(job.id)

    assert old_task.cancelled() or old_task.done()
    assert (await _get_job(job.id)).subtitle_status is None


@pytest.mark.unit
async def test_midrip_show_change_refreshes_subtitles(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    coord = job_manager._identification
    cancels: list[int] = []

    async def fake_cancel(job_id):
        cancels.append(job_id)

    monkeypatch.setattr(coord, "_cancel_subtitle_download", fake_cancel, raising=False)
    monkeypatch.setattr(coord, "_start_tv_subtitle_prefetch", AsyncMock())
    monkeypatch.setattr(coord, "_restart_subtitle_download", AsyncMock())
    monkeypatch.setattr(
        "app.services.identification_coordinator._resolve_show_year",
        lambda tmdb_id, signal: None,
    )
    monkeypatch.setattr(job_manager._matching, "match_single_file", AsyncMock())
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    job = await _seed_job(detected_title="Show A", tmdb_id=111)

    await job_manager.re_identify_job(job.id, "Show B", "tv", season=1, tmdb_id=999)

    assert cancels == [job.id]
    coord._start_tv_subtitle_prefetch.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k "cancel_subtitle or refreshes_subtitles" -v`
Expected: FAIL — `AttributeError: 'MatchingCoordinator' object has no attribute 'cancel_subtitle_download'` and the coordinator never calls `_cancel_subtitle_download`.

- [ ] **Step 3: Extract `cancel_subtitle_download` in `MatchingCoordinator`**

In `backend/app/services/matching_coordinator.py`, replace `restart_subtitle_download` (~278-321):

```python
    async def restart_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Cancel any in-flight subtitle download and start a fresh one.

        Used after re-identification corrects the show title. Resets the
        in-memory event/task pair, clears stale `subtitle_status` and
        subtitle-related error_message in the DB, then kicks off a new download.
        """
        from sqlalchemy import update

        # Cancel a stale or in-flight task. Awaiting cancellation prevents the
        # old task's DB write from racing past the new one.
        old_task = self._subtitle_tasks.get(job_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return
            update_values: dict = {"subtitle_status": None, "subtitle_error_message": None}
            # Only wipe the catch-all error_message if it came from the subtitle
            # pipeline's exception path (the actionable "no subtitles" detail lives
            # on subtitle_error_message, cleared unconditionally above).
            if job.error_message and (
                job.error_message.startswith("Subtitle download")
                or job.error_message.startswith("Download error")
            ):
                update_values["error_message"] = None
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
            )
            await session.commit()

        # Clear the persistent UI banner immediately; the new task will emit
        # progress events as it runs.
        await ws_manager.broadcast_subtitle_event(job_id, "downloading", downloaded=0, total=0)

        self.start_subtitle_download(job_id, show_name, season, tmdb_id)
```

with (cancellation extracted into a reusable method; restart now composes it):

```python
    async def cancel_subtitle_download(self, job_id: int) -> None:
        """Cancel an in-flight subtitle download and clear its stale DB status.

        Shared by ``restart_subtitle_download`` and the mid-rip re-identify path:
        a corrected show must not keep the previous show's download — or its
        stale ``subtitle_status`` (a lingering "failed" would gate matching for
        the new show) — alive. Awaiting cancellation prevents the old task's DB
        write from racing past whatever starts next.
        """
        from sqlalchemy import update

        old_task = self._subtitle_tasks.get(job_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return
            update_values: dict = {"subtitle_status": None, "subtitle_error_message": None}
            # Only wipe the catch-all error_message if it came from the subtitle
            # pipeline's exception path (the actionable "no subtitles" detail lives
            # on subtitle_error_message, cleared unconditionally above).
            if job.error_message and (
                job.error_message.startswith("Subtitle download")
                or job.error_message.startswith("Download error")
            ):
                update_values["error_message"] = None
            await session.execute(
                update(DiscJob).where(DiscJob.id == job_id).values(**update_values)
            )
            await session.commit()

        # Clear the persistent UI banner immediately; whatever starts next emits
        # its own progress events.
        await ws_manager.broadcast_subtitle_event(job_id, "downloading", downloaded=0, total=0)

    async def restart_subtitle_download(
        self, job_id: int, show_name: str, season: int, tmdb_id: int | None = None
    ) -> None:
        """Cancel any in-flight subtitle download and start a fresh one.

        Used after re-identification corrects the show title.
        """
        await self.cancel_subtitle_download(job_id)
        self.start_subtitle_download(job_id, show_name, season, tmdb_id)
```

- [ ] **Step 4: Add the injection slot in `IdentificationCoordinator`**

In `backend/app/services/identification_coordinator.py` `__init__`, after `self._restart_subtitle_download: callable = None` (~240), add:

```python
        self._cancel_subtitle_download: callable = None
```

In `set_callbacks`, add a keyword parameter (after `restart_subtitle_download,` ~253):

```python
        cancel_subtitle_download=None,
```

and assign it in the body (after `self._restart_subtitle_download = restart_subtitle_download` ~266):

```python
        self._cancel_subtitle_download = cancel_subtitle_download
```

- [ ] **Step 5: Call cancel + prefetch after the session commit in `re_identify`**

In `re_identify`, replace the post-session restart block (~1499-1504):

```python
        # Restart outside the session block: restart_subtitle_download opens its
        # own session for cleanup and would deadlock on the same connection.
        if restart_args is not None:
            await self._restart_subtitle_download(*restart_args)

        return {"job_id": job_id, "has_ripped": has_ripped, "resume_action": resume_action}
```

with:

```python
        # Restart outside the session block: these open their own sessions for
        # cleanup and would deadlock on the same connection.
        if restart_args is not None:
            await self._restart_subtitle_download(*restart_args)
        if mid_rip_subtitle_refresh:
            # Corrected show mid-rip: cancel the previous show's in-flight
            # download (and clear its stale subtitle_status) before prefetching
            # the corrected show — known season → that season, unknown → all.
            if self._cancel_subtitle_download is not None:
                await self._cancel_subtitle_download(job_id)
            await self._start_tv_subtitle_prefetch(job)

        return {"job_id": job_id, "has_ripped": has_ripped, "resume_action": resume_action}
```

- [ ] **Step 6: Wire the callback in `JobManager`**

In `backend/app/services/job_manager.py`, in the `self._identification.set_callbacks(...)` call (~205-216), add after `restart_subtitle_download=self._matching.restart_subtitle_download,`:

```python
            cancel_subtitle_download=self._matching.cancel_subtitle_download,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_midrip_rematch.py -k "cancel_subtitle or refreshes_subtitles" -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add app/services/matching_coordinator.py app/services/identification_coordinator.py app/services/job_manager.py tests/unit/test_midrip_rematch.py
git commit -m "feat(rematch): cancel+restart stale subtitle download on mid-rip show change"
```

---

## Task 5: Integration test — mid-rip correction reaches real matching

**Files:**
- Create: `backend/tests/integration/test_midrip_correction_rematch.py`

This tier is what would have caught the bug: it drives the real coordinator + JobManager (not pure simulation) so `match_single_file` is actually exercised (stubbed to record dispatches).

- [ ] **Step 1: Write the test**

Create `backend/tests/integration/test_midrip_correction_rematch.py`:

```python
"""A mid-rip identity correction re-matches an already-processed title.

Exercises the real re_identify → _apply_identity_resume_action("rematch_ripped")
→ _rematch_ripped_titles path against the app DB (not pure simulation), with
match_single_file stubbed to record dispatches.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session, init_db
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.mark.asyncio
async def test_midrip_correction_rematches_already_matched_title(monkeypatch, tmp_path):
    # Keep re_identify off the network.
    coord = job_manager._identification
    monkeypatch.setattr(coord, "_start_tv_subtitle_prefetch", AsyncMock())
    monkeypatch.setattr(coord, "_cancel_subtitle_download", AsyncMock(), raising=False)
    monkeypatch.setattr(coord, "_restart_subtitle_download", AsyncMock())
    monkeypatch.setattr(
        "app.services.identification_coordinator._resolve_show_year",
        lambda tmdb_id, signal: None,
    )

    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    # A confidently-identified (but wrong) TV disc, one title already MATCHED.
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_A_S1D1",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            staging_path=str(tmp_path),
            detected_title="Show A",
            tmdb_id=111,
            identity_prompt_json=None,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1380,
            state=TitleState.MATCHED,
            output_filename=str(f),
            is_selected=True,
            match_confidence=0.9,
            matched_episode="S01E01",
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    await job_manager.re_identify_job(job_id, "Show B", "tv", season=1, tmdb_id=999)
    await asyncio.sleep(0)

    assert title_id in dispatched
    async with async_session() as session:
        refreshed = await session.get(DiscTitle, title_id)
        assert refreshed.state in (TitleState.QUEUED, TitleState.MATCHING)
        assert refreshed.matched_episode is None
        job_row = await session.get(DiscJob, job_id)
        assert job_row.detected_title == "Show B"
        assert job_row.identity_prompt_json is None
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_midrip_correction_rematch.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_midrip_correction_rematch.py
git commit -m "test(rematch): integration coverage for mid-rip correction re-match"
```

---

## Task 6: Changelog + full verification

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased] / Fixed`)

- [ ] **Step 1: Add the changelog entry**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Fixed`, add a bullet (user-facing prose, per repo convention):

```markdown
- **Correcting a disc's identity mid-rip now actually re-matches it.** When you used the always-available identity control to fix a show while it was still ripping, the correction updated the title but never re-ran episode matching on the tracks that had already been matched under the wrong identity — so nothing changed until the rip finished and you re-identified a second time from the review prompt. A mid-rip identity change now re-matches every already-ripped track against the corrected show (and restarts the reference-subtitle download for it), matching the behavior you previously only got after the rip completed. (#520)
```

- [ ] **Step 2: Run the full affected test set**

Run: `uv run pytest tests/unit/test_midrip_rematch.py tests/unit/test_identity_answer_midrip.py tests/integration/test_walk_away_workflow.py tests/integration/test_midrip_correction_rematch.py tests/integration/test_re_identify.py -v`
Expected: all PASS (the new tests plus the existing mid-rip / walk-away / re-identify suites, confirming no regression).

- [ ] **Step 3: Lint + format**

Run: `uv run ruff check app/services/job_manager.py app/services/identification_coordinator.py app/services/matching_coordinator.py app/services/identity_prompts.py tests/unit/test_midrip_rematch.py tests/integration/test_midrip_correction_rematch.py`
Then: `uv run ruff format app/services/job_manager.py app/services/identification_coordinator.py app/services/matching_coordinator.py app/services/identity_prompts.py tests/unit/test_midrip_rematch.py tests/integration/test_midrip_correction_rematch.py`
Expected: no lint errors; formatting clean.

- [ ] **Step 4: Broader regression sweep**

Run: `uv run pytest tests/unit tests/integration -q`
Expected: PASS (no regressions across unit + integration).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): mid-rip identity correction now re-matches"
```

---

## Notes for the implementer

- **CWD is `backend/`** for every command above.
- **Never use `--reload`** and don't start a real backend for these tasks — they're all `pytest`.
- The worktree's `engram.db` may be a 0-byte stub; `init_db()` in the integration fixture creates the schema. If a pipeline/integration test errors with "no such table", run any test that calls `init_db()` first, or `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"`.
- **In-flight cancellation latency:** `_rematch_ripped_titles` awaits cancelled match tasks; against real ASR a cancelled task finishes its current chunk (seconds) before unwinding — expected, and only on a manual mid-rip correction. Tests stub `match_single_file`, so this is instant under test.
- Keep the `#520` reference in the changelog (the feature this fixes).
```
