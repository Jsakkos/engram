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
