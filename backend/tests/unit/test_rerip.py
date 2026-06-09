"""Unit tests for single-track re-rip (Feature C)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services import matching_coordinator as mc
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import (
    RERIP_MAX_ATTEMPTS,
    MatchingCoordinator,
)
from tests.unit.conftest import _unit_session_factory


def test_disc_title_has_rerip_attempts_default_zero():
    t = DiscTitle(job_id=1, title_index=0, duration_seconds=100)
    assert t.rerip_attempts == 0


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    monkeypatch.setattr(mc, "async_session", _unit_session_factory)


def _make_coord() -> MatchingCoordinator:
    broadcaster = MagicMock()
    coord = MatchingCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord._check_job_completion = AsyncMock()
    return coord


async def _seed_title(state: TitleState, attempts: int = 0) -> tuple[int, int]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            staging_path="/tmp/staging",
            content_hash="ABC123",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            state=state,
            rerip_attempts=attempts,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _reload(title_id: int) -> DiscTitle:
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.asyncio
async def test_route_marks_review_with_code_and_eligible(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.QUEUED, attempts=0)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    title = await _reload(title_id)
    assert title.state == TitleState.REVIEW
    d = json.loads(title.match_details)
    assert d["error"] == "incomplete_rip"
    assert d["rerip_eligible"] is True
    assert d["rerip_attempts"] == 0
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_route_marks_ineligible_at_cap(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.RIPPING, attempts=RERIP_MAX_ATTEMPTS)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    d = json.loads((await _reload(title_id)).match_details)
    assert d["rerip_eligible"] is False
    assert "stopped after" in d["message"].lower()


@pytest.mark.asyncio
async def test_route_ignores_terminal_title(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.MATCHED)
    coord = _make_coord()
    await coord.route_rip_failure_to_review(job_id, title_id, "incomplete_rip", "boom")
    assert (await _reload(title_id)).state == TitleState.MATCHED  # untouched


@pytest.mark.asyncio
async def test_on_title_error_routes_to_review_not_failed(monkeypatch):
    """A ripping stall now holds the title in REVIEW (rip_stalled), not FAILED."""
    from app.services.job_manager import job_manager

    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_title(TitleState.RIPPING)
    # Real coordinator with a stubbed completion check.
    monkeypatch.setattr(job_manager._matching, "_check_job_completion", AsyncMock())

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title_id)
        sorted_titles = [title]

    await job_manager._on_title_error(job_id, 1, "disc dirty", sorted_titles)

    t = await _reload(title_id)
    assert t.state == TitleState.REVIEW
    d = json.loads(t.match_details)
    assert d["error"] == "rip_stalled"
    assert d["rerip_eligible"] is True


@pytest.mark.asyncio
async def test_rerip_titles_transitions_deletes_and_rips(monkeypatch, tmp_path):
    from app.core.extractor import RipResult
    from app.services.job_manager import job_manager

    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

    # Seed a REVIEW_NEEDED job with one incomplete_rip REVIEW title + a stale file.
    stale = tmp_path / "show_t02.mkv"
    stale.write_bytes(b"truncated")
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            staging_path=str(tmp_path),
            content_hash="ABC123",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            state=TitleState.REVIEW,
            output_filename=str(stale),
            rerip_attempts=0,
            match_details=json.dumps({"error": "incomplete_rip", "rerip_eligible": True}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    captured = {}

    async def fake_rip_titles(drive, output_dir, title_indices=None, **kw):
        captured["drive"] = drive
        captured["indices"] = title_indices
        return RipResult(success=True, output_files=[], error_message=None, stalled_titles=None)

    monkeypatch.setattr(job_manager._extractor, "rip_titles", fake_rip_titles)
    monkeypatch.setattr(job_manager, "_drive_monitor", MagicMock())
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda d: None)

    await job_manager.rerip_titles(job_id, [title_id])

    assert captured["indices"] == [2]
    assert captured["drive"] == "F:"
    assert not stale.exists()  # stale file deleted before re-rip
    t = await _reload(title_id)
    assert t.rerip_attempts == 1


@pytest.mark.asyncio
async def test_rerip_titles_bails_when_job_not_transitionable(monkeypatch, tmp_path):
    """A job that can't transition to RIPPING (e.g. still MATCHING / double-fire)
    must not re-rip — titles stay untouched."""
    from app.services.job_manager import job_manager

    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="X",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            staging_path=str(tmp_path),
            content_hash="ABC123",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=100,
            state=TitleState.REVIEW,
            rerip_attempts=0,
            match_details=json.dumps({"error": "incomplete_rip", "rerip_eligible": True}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    called = {"rip": False}

    async def fake_rip_titles(*a, **k):
        called["rip"] = True
        from app.core.extractor import RipResult

        return RipResult(success=True, output_files=[], error_message=None, stalled_titles=None)

    monkeypatch.setattr(job_manager._extractor, "rip_titles", fake_rip_titles)

    await job_manager.rerip_titles(job_id, [title_id])

    assert called["rip"] is False  # bailed before ripping
    t = await _reload(title_id)
    assert t.rerip_attempts == 0  # untouched
    assert t.state == TitleState.REVIEW


async def _seed_rerip_job(*, eligible: bool, hash_="ABC123", state=JobState.REVIEW_NEEDED):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=state,
            staging_path="/tmp/staging",
            content_hash=hash_,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            state=TitleState.REVIEW,
            match_details=json.dumps({"error": "incomplete_rip", "rerip_eligible": eligible}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


@pytest.mark.asyncio
async def test_find_rerip_job_hash_match_eligible():
    from app.services.job_manager import job_manager

    job_id, title_id = await _seed_rerip_job(eligible=True)
    found = await job_manager._find_rerip_job("ABC123")
    assert found == (job_id, [title_id])


@pytest.mark.asyncio
async def test_find_rerip_job_hash_mismatch():
    from app.services.job_manager import job_manager

    await _seed_rerip_job(eligible=True)
    assert await job_manager._find_rerip_job("DIFFERENT") is None
    assert await job_manager._find_rerip_job(None) is None


@pytest.mark.asyncio
async def test_find_rerip_job_excludes_ineligible_and_busy():
    from app.services.job_manager import job_manager

    await _seed_rerip_job(eligible=False)  # cap reached
    await _seed_rerip_job(eligible=True, hash_="OTHER", state=JobState.MATCHING)  # still busy
    assert await job_manager._find_rerip_job("ABC123") is None
    assert await job_manager._find_rerip_job("OTHER") is None


@pytest.mark.asyncio
async def test_rerip_title_manual_verifies_hash_and_spawns(monkeypatch):
    import asyncio

    from app.services.job_manager import job_manager

    job_id, title_id = await _seed_rerip_job(eligible=False)  # cap reached: manual bypasses it

    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value="ABC123"))
    spawned = {}

    async def fake_rerip(jid, tids):
        spawned["args"] = (jid, tids)

    monkeypatch.setattr(job_manager, "rerip_titles", fake_rerip)

    await job_manager.rerip_title_manual(job_id, title_id)
    await asyncio.sleep(0)
    assert spawned["args"] == (job_id, [title_id])


@pytest.mark.asyncio
async def test_rerip_title_manual_rejects_wrong_disc(monkeypatch):
    from app.services.job_manager import job_manager

    job_id, title_id = await _seed_rerip_job(eligible=True)
    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value="WRONG"))
    monkeypatch.setattr(job_manager, "rerip_titles", AsyncMock())

    with pytest.raises(ValueError, match="different disc"):
        await job_manager.rerip_title_manual(job_id, title_id)


@pytest.mark.asyncio
async def test_rerip_title_manual_rejects_busy_job(monkeypatch):
    """Manual re-rip is rejected when the job isn't settled in REVIEW_NEEDED."""
    from app.services.job_manager import job_manager

    job_id, title_id = await _seed_rerip_job(eligible=True, state=JobState.MATCHING)
    monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value="ABC123"))
    monkeypatch.setattr(job_manager, "rerip_titles", AsyncMock())

    with pytest.raises(ValueError, match="not awaiting re-rip review"):
        await job_manager.rerip_title_manual(job_id, title_id)
