"""Tests for stuck-job recovery, force-advance, per-track skip, and match attribution.

Covers the four fixes:
- Issue 1: reconcile_stuck_titles recovers titles orphaned in PENDING/RIPPING.
- Issue 2: a no-match title goes to REVIEW with match_source unset (not "engram").
- Issue 3/4: reconcile_and_advance / skip_title drive a stuck job to a resting state.

The unit-test conftest (isolate_database) patches ``async_session`` per module to an
in-memory SQLite DB. To share that DB, these helpers reach ``async_session`` through
the module object (``db.async_session``) so the monkeypatch is honored at call time.
Scenarios are built so finalization never organizes into the real library — stuck or
unmatched titles route to REVIEW, holding the job in REVIEW_NEEDED.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.database as db
from app.models import AppConfig, DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager


async def _make_job(
    staging: Path,
    *,
    content_type: ContentType = ContentType.TV,
    state: JobState = JobState.MATCHING,
    identity_prompt_json: str | None = None,
) -> int:
    async with db.async_session() as session:
        job = DiscJob(
            drive_id="Z:",
            volume_label="TEST_DISC",
            content_type=content_type,
            state=state,
            staging_path=str(staging),
            identity_prompt_json=identity_prompt_json,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _add_title(
    job_id: int,
    index: int,
    state: TitleState,
    *,
    output: str | None = None,
    matched_episode: str | None = None,
    selected: bool = True,
) -> int:
    async with db.async_session() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=2700,
            state=state,
            is_selected=selected,
            output_filename=output,
            matched_episode=matched_episode,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t.id


async def _title(title_id: int) -> DiscTitle:
    async with db.async_session() as session:
        return await session.get(DiscTitle, title_id)


async def _job(job_id: int) -> DiscJob:
    async with db.async_session() as session:
        return await session.get(DiscJob, job_id)


# --- Issue 1: reconcile_stuck_titles ---------------------------------------


@pytest.mark.asyncio
async def test_reconcile_stuck_no_file_fails(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.RIPPING)
    tid = await _add_title(job_id, 0, TitleState.RIPPING)  # no file anywhere

    await job_manager.reconcile_stuck_titles(job_id)

    assert (await _title(tid)).state == TitleState.FAILED


@pytest.mark.asyncio
async def test_reconcile_stuck_movie_with_file_matched(tmp_path):
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.MOVIE, state=JobState.RIPPING)
    tid = await _add_title(job_id, 0, TitleState.RIPPING, output=str(f))

    await job_manager.reconcile_stuck_titles(job_id)

    t = await _title(tid)
    assert t.state == TitleState.MATCHED
    assert t.output_filename == str(f)


@pytest.mark.asyncio
async def test_reconcile_stuck_tv_with_file_queues_match(tmp_path, monkeypatch):
    f = tmp_path / "disc_t03.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.RIPPING)
    tid = await _add_title(job_id, 3, TitleState.RIPPING, output=str(f))

    called = {}

    async def fake_discdb(jid, title, session):
        return False

    async def fake_match(jid, title_id, path):
        called["match"] = (jid, title_id, str(path))

    monkeypatch.setattr(job_manager._matching, "try_discdb_assignment", fake_discdb)
    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    await job_manager.reconcile_stuck_titles(job_id)
    await asyncio.sleep(0.05)  # let the queued match task run

    # Enqueued for matching → QUEUED (the match coroutine is monkeypatched to a
    # no-op, so the post-semaphore QUEUED→MATCHING flip never runs here).
    assert (await _title(tid)).state == TitleState.QUEUED
    assert called.get("match") == (job_id, tid, str(f))


_IDENTITY_PROMPT = '{"kind": "name", "reason": "Disc label unreadable"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [ContentType.TV, ContentType.MOVIE])
async def test_reconcile_stuck_identity_pending_parks_queued(tmp_path, monkeypatch, content_type):
    """Identity gate (walk-away Phase B): with an unanswered identity prompt, a
    recovered orphaned title parks in QUEUED — no matching dispatch, and no
    non-TV fall-through to MATCHED (that would mark it matched with no
    identity). dispatch_pending_matches releases it after the answer."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(
        tmp_path,
        content_type=content_type,
        state=JobState.RIPPING,
        identity_prompt_json=_IDENTITY_PROMPT,
    )
    tid = await _add_title(job_id, 0, TitleState.RIPPING, output=str(f))

    from unittest.mock import AsyncMock

    discdb = AsyncMock(return_value=False)
    dispatch = AsyncMock()
    monkeypatch.setattr(job_manager._matching, "try_discdb_assignment", discdb)
    monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    await job_manager.reconcile_stuck_titles(job_id)
    await asyncio.sleep(0.05)

    assert (await _title(tid)).state == TitleState.QUEUED
    discdb.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_stuck_season_prompt_dispatches_normally(tmp_path, monkeypatch):
    """kind=season is a non-blocking shortcut CTA (B2): identity is confirmed,
    so a recovered TV title dispatches into cross-season matching instead of
    parking — a parked season-prompt job would hang forever (QUEUED titles
    refresh the watchdog clock and nothing ever dispatches)."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(
        tmp_path,
        content_type=ContentType.TV,
        state=JobState.RIPPING,
        identity_prompt_json='{"kind": "season", "reason": "select a season to continue."}',
    )
    tid = await _add_title(job_id, 0, TitleState.RIPPING, output=str(f))

    from unittest.mock import AsyncMock

    discdb = AsyncMock(return_value=False)
    dispatch = AsyncMock()
    monkeypatch.setattr(job_manager._matching, "try_discdb_assignment", discdb)
    monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    await job_manager.reconcile_stuck_titles(job_id)
    await asyncio.sleep(0.05)

    assert (await _title(tid)).state == TitleState.QUEUED  # MATCHING flip is post-semaphore
    dispatch.assert_awaited_once()


# --- QUEUED state: completion check treats it as active --------------------


@pytest.mark.asyncio
async def test_all_queued_titles_do_not_finalize(tmp_path):
    """A job whose tracks are all QUEUED (waiting for a match slot) is not 'done'.

    QUEUED must count as an active state in check_job_completion, otherwise a
    freshly-enqueued job reads as 'all terminal' and finalizes with nothing matched.
    """
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))
    await _add_title(job_id, 1, TitleState.QUEUED, output=str(f))

    async with db.async_session() as session:
        await job_manager._finalization.check_job_completion(session, job_id)

    assert (await _job(job_id)).state == JobState.MATCHING


@pytest.mark.asyncio
async def test_match_timeout_routes_to_review(tmp_path, monkeypatch):
    """A match that exceeds the per-track ceiling → REVIEW with forced_review.

    The clock starts only once matching is underway (this runs under the semaphore),
    so a genuinely stuck match is recovered while waiting (QUEUED) tracks are untouched.
    The forced_review flag stops the review-escalation from re-dispatching it into a
    timeout loop.
    """
    import json as _json

    from sqlmodel import select as sa_select

    from app.models import AppConfig

    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHING, output=str(f))

    # Tiny per-track ceiling so the hung matcher trips quickly.
    async with db.async_session() as session:
        cfg = (await session.execute(sa_select(AppConfig).limit(1))).scalar_one_or_none()
        if cfg is None:
            cfg = AppConfig()
            session.add(cfg)
        cfg.timeout_matching_seconds = 1
        await session.commit()

    async def slow_match(*args, **kwargs):
        await asyncio.sleep(3)  # never finishes before the 1s ceiling
        return SimpleNamespace(
            episode_code=None, confidence=0.0, needs_review=True, match_details={}
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", slow_match
    )
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    t = await _title(tid)
    assert t.state == TitleState.REVIEW
    details = _json.loads(t.match_details)
    assert details.get("forced_review") is True
    assert "tim" in details.get("reason", "").lower()  # "match timed out"


@pytest.mark.asyncio
async def test_match_timeout_preserves_existing_match_details(tmp_path, monkeypatch):
    """The timeout handler must MERGE into match_details, not stomp a concurrent skip's reason.

    If skip_title committed {"forced_review": true, "reason": "Skipped by user"} while the
    match was still running, the timeout path must preserve that reason (audit trail), not
    overwrite it with "match timed out".
    """
    import json as _json

    from sqlmodel import select as sa_select

    from app.models import AppConfig

    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHING, output=str(f))

    async with db.async_session() as session:
        t = await session.get(DiscTitle, tid)
        t.match_details = _json.dumps({"forced_review": True, "reason": "Skipped by user"})
        await session.commit()
        cfg = (await session.execute(sa_select(AppConfig).limit(1))).scalar_one_or_none()
        if cfg is None:
            cfg = AppConfig()
            session.add(cfg)
        cfg.timeout_matching_seconds = 1
        await session.commit()

    async def slow_match(*args, **kwargs):
        await asyncio.sleep(3)
        return SimpleNamespace(
            episode_code=None, confidence=0.0, needs_review=True, match_details={}
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", slow_match
    )
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    details = _json.loads((await _title(tid)).match_details)
    assert details.get("forced_review") is True
    assert details.get("reason") == "Skipped by user"  # preserved, not stomped


@pytest.mark.asyncio
async def test_match_timeout_releases_semaphore(tmp_path, monkeypatch):
    """A timed-out match frees its slot (via the outer finally) so the queue drains."""
    from sqlmodel import select as sa_select

    from app.models import AppConfig

    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))

    async with db.async_session() as session:
        cfg = (await session.execute(sa_select(AppConfig).limit(1))).scalar_one_or_none()
        if cfg is None:
            cfg = AppConfig()
            session.add(cfg)
        cfg.timeout_matching_seconds = 1
        await session.commit()

    job_manager._matching.init_semaphore(1)

    async def slow_match(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", slow_match
    )
    monkeypatch.setattr(
        job_manager._matching, "_wait_for_file_ready", lambda *a, **k: asyncio.sleep(0, result=True)
    )
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._run_match_single_file(job_id, tid, f)

    assert (await _title(tid)).state == TitleState.REVIEW
    # Slot returned to the pool — the next QUEUED track can acquire it.
    assert job_manager._matching._match_semaphore._value == 1


@pytest.mark.asyncio
async def test_match_failure_routes_queued_title_to_review(tmp_path, monkeypatch):
    """A match task that fails while its title is still QUEUED → REVIEW (not stuck)."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))

    # Isolate the state transition from downstream finalization/review-escalation.
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._handle_match_failure(job_id, tid, "boom")

    assert (await _title(tid)).state == TitleState.REVIEW


# --- Issue 2: match attribution --------------------------------------------


@pytest.mark.asyncio
async def test_no_match_leaves_review_without_engram_source(tmp_path, monkeypatch):
    f = tmp_path / "disc_t05.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 5, TitleState.MATCHING, output=str(f))

    async def no_match(*args, **kwargs):
        return SimpleNamespace(
            episode_code=None,
            confidence=0.0,
            needs_review=True,
            match_details={"matches_found": 0, "matches_rejected": 10},
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", no_match
    )
    # Isolate the gating assertion from finalization.
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    t = await _title(tid)
    assert t.state == TitleState.REVIEW
    assert t.match_source is None  # NOT "engram"


@pytest.mark.asyncio
async def test_successful_match_sets_engram_source(tmp_path, monkeypatch):
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHING, output=str(f))

    async def good_match(*args, **kwargs):
        return SimpleNamespace(
            episode_code="S01E01",
            confidence=0.92,
            needs_review=False,
            match_details={"score": 0.92, "vote_count": 9},
        )

    monkeypatch.setattr(
        "app.services.matching_coordinator.episode_curator.match_single_file", good_match
    )
    monkeypatch.setattr(
        job_manager._matching, "_check_job_completion", lambda *a, **k: asyncio.sleep(0)
    )

    await job_manager._matching._match_single_file_inner(job_id, tid, f)

    t = await _title(tid)
    assert t.state == TitleState.MATCHED
    assert t.match_source == "engram"


# --- Issue 3/4: force-advance + skip ---------------------------------------


@pytest.mark.asyncio
async def test_advance_sends_stuck_to_review(tmp_path):
    f = tmp_path / "disc_t05.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    await _add_title(job_id, 0, TitleState.MATCHED, matched_episode="S01E01")
    stuck = await _add_title(job_id, 5, TitleState.MATCHING, output=str(f))

    assert await job_manager.reconcile_and_advance(job_id, reason="test") is True

    assert (await _title(stuck)).state == TitleState.REVIEW
    assert (await _job(job_id)).state == JobState.REVIEW_NEEDED


@pytest.mark.asyncio
async def test_advance_no_file_fails_track(tmp_path):
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.RIPPING)
    stuck = await _add_title(job_id, 0, TitleState.RIPPING)  # no file

    assert await job_manager.reconcile_and_advance(job_id, reason="test") is True

    assert (await _title(stuck)).state == TitleState.FAILED


@pytest.mark.asyncio
async def test_advance_rejects_terminal(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.COMPLETED)
    assert await job_manager.reconcile_and_advance(job_id) is False


@pytest.mark.asyncio
async def test_skip_title_to_review(tmp_path):
    f = tmp_path / "disc_t02.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 2, TitleState.MATCHING, output=str(f))

    assert await job_manager.skip_title(job_id, tid) is True

    assert (await _title(tid)).state == TitleState.REVIEW
    assert (await _job(job_id)).state == JobState.REVIEW_NEEDED


@pytest.mark.asyncio
async def test_skip_queued_title_to_review(tmp_path):
    """A user can manually skip a track still waiting in the match queue."""
    f = tmp_path / "disc_t02.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    tid = await _add_title(job_id, 2, TitleState.QUEUED, output=str(f))

    assert await job_manager.skip_title(job_id, tid) is True
    assert (await _title(tid)).state == TitleState.REVIEW


@pytest.mark.asyncio
async def test_skip_title_already_resolved_returns_false(tmp_path):
    job_id = await _make_job(tmp_path, state=JobState.MATCHING)
    tid = await _add_title(job_id, 0, TitleState.MATCHED)

    assert await job_manager.skip_title(job_id, tid) is False


# --- watchdog: queue-aware (does not punish waiting tracks) ----------------


@pytest.mark.asyncio
async def test_watchdog_does_not_force_review_queued_tracks(tmp_path):
    """A MATCHING job whose tracks are QUEUED (waiting for a slot) is not 'stuck'.

    Reproduces the import-storm bug: many jobs queued behind the global match
    semaphore made no progress, so the per-job watchdog clock went stale and
    force-advanced every waiting track to REVIEW. Queued work must reset the
    clock instead.
    """
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)
    t0 = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))
    t1 = await _add_title(job_id, 1, TitleState.QUEUED, output=str(f))

    cfg = AppConfig()
    cfg.timeout_matching_seconds = 1
    # Stale clock: last activity far beyond the timeout.
    job_manager._last_activity[job_id] = time.monotonic() - 9999

    job = await _job(job_id)
    await job_manager._watchdog_check_job(job, cfg, time.monotonic())

    assert (await _title(t0)).state == TitleState.QUEUED
    assert (await _title(t1)).state == TitleState.QUEUED
    # Clock refreshed so the next sweep also leaves the draining queue alone.
    assert job_manager._last_activity[job_id] > time.monotonic() - 5


@pytest.mark.asyncio
async def test_watchdog_advances_genuinely_stale_ripping_job(tmp_path):
    """The watchdog still force-advances a non-matching phase that truly stalls."""
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.RIPPING)
    stuck = await _add_title(job_id, 0, TitleState.RIPPING)  # no file → FAILED on advance

    cfg = AppConfig()
    cfg.timeout_ripping_seconds = 1
    job_manager._last_activity[job_id] = time.monotonic() - 9999

    job = await _job(job_id)
    await job_manager._watchdog_check_job(job, cfg, time.monotonic())

    assert (await _title(stuck)).state == TitleState.FAILED


# --- watchdog × identity gate (walk-away B4) --------------------------------


@pytest.mark.asyncio
async def test_watchdog_leaves_healthy_identity_pending_rip_alone(tmp_path):
    """(B4a) During a healthy rip with titles parked QUEUED behind an identity
    prompt, the watchdog must not advance: RIPPING liveness is measured by rip
    output growth (the fs monitor calls _note_activity on active file growth),
    not by title-state advancement — so parked titles never make a live rip
    look stale, and no QUEUED exemption is needed for the RIPPING phase."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(
        tmp_path, state=JobState.RIPPING, identity_prompt_json=_IDENTITY_PROMPT
    )
    parked = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))
    active = await _add_title(job_id, 1, TitleState.RIPPING)

    cfg = AppConfig()
    cfg.timeout_ripping_seconds = 1
    # Fresh clock — what the fs monitor maintains while rip output grows.
    job_manager._last_activity[job_id] = time.monotonic()

    job = await _job(job_id)
    await job_manager._watchdog_check_job(job, cfg, time.monotonic())

    assert (await _title(parked)).state == TitleState.QUEUED
    assert (await _title(active)).state == TitleState.RIPPING
    assert (await _job(job_id)).state == JobState.RIPPING


@pytest.mark.asyncio
async def test_stale_rip_force_advance_spares_parked_queued_titles(tmp_path):
    """(B4a residual) A genuinely stale RIPPING job with identity pending IS
    still force-advanced (rip stall detection stays live), but its parked
    QUEUED titles survive untouched — reconcile_and_advance excludes QUEUED, so
    the review flow / answer endpoints keep owning them."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(
        tmp_path, state=JobState.RIPPING, identity_prompt_json=_IDENTITY_PROMPT
    )
    parked = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))
    stuck = await _add_title(job_id, 1, TitleState.RIPPING)  # no file → FAILED

    cfg = AppConfig()
    cfg.timeout_ripping_seconds = 1
    job_manager._last_activity[job_id] = time.monotonic() - 9999

    job = await _job(job_id)
    await job_manager._watchdog_check_job(job, cfg, time.monotonic())

    assert (await _title(parked)).state == TitleState.QUEUED
    assert (await _title(stuck)).state == TitleState.FAILED
    # The prompt is untouched — answering it later still releases the parked
    # title via dispatch_pending_matches.
    assert (await _job(job_id)).identity_prompt_json == _IDENTITY_PROMPT


@pytest.mark.asyncio
async def test_watchdog_review_needed_is_resting_state(tmp_path):
    """(B4b) REVIEW_NEEDED has no phase timeout: even with a stale clock the
    watchdog never advances a job parked in review — the post-rip identity
    convergence lands jobs in a genuine resting state."""
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    job_id = await _make_job(tmp_path, state=JobState.REVIEW_NEEDED)
    parked = await _add_title(job_id, 0, TitleState.QUEUED, output=str(f))

    cfg = AppConfig()
    job_manager._last_activity[job_id] = time.monotonic() - 9999

    job = await _job(job_id)
    await job_manager._watchdog_check_job(job, cfg, time.monotonic())

    assert (await _job(job_id)).state == JobState.REVIEW_NEEDED
    assert (await _title(parked)).state == TitleState.QUEUED


# --- watchdog config helper ------------------------------------------------


def test_phase_timeout_reads_config():
    cfg = AppConfig()
    assert job_manager._phase_timeout(cfg, JobState.IDENTIFYING) == cfg.timeout_identifying_seconds
    assert job_manager._phase_timeout(cfg, JobState.RIPPING) == cfg.timeout_ripping_seconds
    assert job_manager._phase_timeout(cfg, JobState.MATCHING) == cfg.timeout_matching_seconds
    assert job_manager._phase_timeout(cfg, JobState.ORGANIZING) == cfg.timeout_organizing_seconds
    # Resting / untimed states have no ceiling.
    assert job_manager._phase_timeout(cfg, JobState.REVIEW_NEEDED) is None
    assert job_manager._phase_timeout(cfg, JobState.IDLE) is None


# --- ORGANIZING restart recovery -------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_stale_jobs_spares_organizing(tmp_path):
    """A job interrupted mid-move (ORGANIZING) must NOT be failed on startup —
    _recover_organizing_jobs re-drives it instead."""
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.ORGANIZING)

    await job_manager._cleanup_stale_jobs()

    assert (await _job(job_id)).state == JobState.ORGANIZING


@pytest.mark.asyncio
async def test_cleanup_stale_jobs_still_fails_matching(tmp_path):
    """Regression guard: non-resumable phases (MATCHING) are still failed on restart."""
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.MATCHING)

    await job_manager._cleanup_stale_jobs()

    assert (await _job(job_id)).state == JobState.FAILED


@pytest.mark.asyncio
async def test_recover_organizing_redrives_tv(tmp_path, monkeypatch):
    """A stranded TV ORGANIZING job re-runs the idempotent finalize (in the background)."""
    job_id = await _make_job(tmp_path, content_type=ContentType.TV, state=JobState.ORGANIZING)

    called: list[int] = []

    async def fake_finalize(jid):
        called.append(jid)

    monkeypatch.setattr(job_manager._finalization, "finalize_disc_job", fake_finalize)

    await job_manager._recover_organizing_jobs()

    task = job_manager._active_jobs.get(job_id)
    assert task is not None
    # gather() (a call) rather than a bare `await task` (a name) so CodeQL's
    # ineffectual-statement heuristic doesn't flag the await as having no effect.
    await asyncio.gather(task)
    job_manager._active_jobs.pop(job_id, None)

    assert called == [job_id]


@pytest.mark.asyncio
async def test_recover_organizing_fails_movie(tmp_path, monkeypatch):
    """A stranded movie ORGANIZING job is failed (no idempotent re-organize path)."""
    job_id = await _make_job(tmp_path, content_type=ContentType.MOVIE, state=JobState.ORGANIZING)

    called: list[int] = []

    async def fake_finalize(jid):
        called.append(jid)

    monkeypatch.setattr(job_manager._finalization, "finalize_disc_job", fake_finalize)

    await job_manager._recover_organizing_jobs()

    assert (await _job(job_id)).state == JobState.FAILED
    assert called == []  # finalize never invoked for a movie
