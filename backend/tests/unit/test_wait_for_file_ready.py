"""Unit tests for MatchingCoordinator._wait_for_file_ready truncation handling.

A rip aborted by an uncorrectable disc read error leaves a stable file far below
the scanned size estimate. The waiter must recognize that as a truncated rip and
bail out quickly (FileWaitResult.TRUNCATED) instead of spinning to the
size-proportional timeout (which wedged job #99 / Breaking Bad S2 t02 for ~4.3h).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services import matching_coordinator as mc
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import FileWaitResult, MatchingCoordinator
from tests.unit.conftest import _unit_session_factory


def _make_coord() -> MatchingCoordinator:
    broadcaster = MagicMock()
    return MatchingCoordinator(broadcaster, JobStateMachine(broadcaster))


def _patch_config(monkeypatch, *, poll=0.01, stable=2, timeout=5.0):
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(
            return_value=SimpleNamespace(
                ripping_file_poll_interval=poll,
                ripping_stability_checks=stable,
                ripping_file_ready_timeout=timeout,
            )
        ),
    )


async def _seed_title(expected_bytes: int) -> int:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=2,
            disc_number=1,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            file_size_bytes=expected_bytes,
            state=TitleState.QUEUED,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return title.id


@pytest.mark.asyncio
async def test_ready_when_file_matches_expected(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 1000)  # 100% of expected
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.READY


@pytest.mark.asyncio
async def test_truncated_when_stable_far_below_expected(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    # The t02 case, scaled: expected ~8.2 GB, real file tiny → ratio ~0.
    title_id = await _seed_title(expected_bytes=8_200_000_000)
    f = tmp_path / "t02.mkv"
    f.write_bytes(b"x" * 1000)
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.TRUNCATED


@pytest.mark.asyncio
async def test_ready_after_grace_when_modestly_undersized(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 600)  # 60% — smaller than projected but NOT truncated
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    assert result == FileWaitResult.READY


@pytest.mark.asyncio
async def test_timeout_when_file_never_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    _patch_config(monkeypatch, timeout=0.05)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "missing.mkv"  # never created
    coord = _make_coord()
    result = await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=0.05)
    assert result == FileWaitResult.TIMEOUT


@pytest.mark.asyncio
async def test_wait_broadcasts_queued_not_ripping(tmp_path, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_title_update", spy)
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.05)
    _patch_config(monkeypatch)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "t.mkv"
    f.write_bytes(b"x" * 600)  # undersized → loops a few times before grace
    coord = _make_coord()
    await coord._wait_for_file_ready(f, title_id, job_id=1, timeout=5.0)
    states = [c.args[2] for c in spy.call_args_list if len(c.args) > 2]
    assert states, "expected at least one title broadcast during the wait"
    assert TitleState.RIPPING.value not in states
    assert TitleState.QUEUED.value in states


@pytest.mark.asyncio
async def test_growing_file_is_never_truncated(tmp_path, monkeypatch):
    """A file still being written (size growing) must never be judged TRUNCATED."""
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    # Large grace window so transient scheduling jitter can't accumulate enough
    # stable polls to trip truncation while the file is still growing.
    monkeypatch.setattr(mc, "TRUNCATED_STABLE_GRACE_SECONDS", 0.2)
    _patch_config(monkeypatch, poll=0.01, stable=2, timeout=2.0)
    title_id = await _seed_title(expected_bytes=1000)
    f = tmp_path / "growing.mkv"
    f.write_bytes(b"x" * 100)

    async def _grow():
        for size in range(200, 1001, 100):  # 200..1000 bytes
            await asyncio.sleep(0.02)
            f.write_bytes(b"x" * size)

    coord = _make_coord()
    _, result = await asyncio.gather(
        _grow(),
        coord._wait_for_file_ready(f, title_id, job_id=1, timeout=2.0),
    )
    assert result == FileWaitResult.READY
    assert result != FileWaitResult.TRUNCATED


async def _seed_queued_title() -> tuple[int, int]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S2D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=2,
            disc_number=1,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=2819,
            file_size_bytes=8_200_000_000,
            state=TitleState.QUEUED,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _reload_title(title_id: int) -> DiscTitle:
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.asyncio
async def test_truncated_result_routes_title_to_review(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.TRUNCATED, job_id, title_id, Path("t02.mkv")
    )
    assert handled is True
    title = await _reload_title(title_id)
    assert title.state == TitleState.REVIEW
    assert "Incomplete rip" in (title.match_details or "")
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_timeout_result_fails_title(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.TIMEOUT, job_id, title_id, Path("t02.mkv")
    )
    assert handled is True
    title = await _reload_title(title_id)
    assert title.state == TitleState.FAILED
    assert title.match_details is None  # TIMEOUT leaves match_details unset (unlike TRUNCATED)
    coord._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_ready_result_proceeds(monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(
        FileWaitResult.READY, job_id, title_id, Path("t02.mkv")
    )
    assert handled is False
    title = await _reload_title(title_id)
    assert title.state == TitleState.QUEUED  # untouched — caller proceeds to match


@pytest.mark.asyncio
async def test_legacy_truthy_result_proceeds(monkeypatch):
    # Existing integration tests patch _wait_for_file_ready to return True;
    # the dispatcher must treat any non-TRUNCATED/non-TIMEOUT value as "proceed".
    monkeypatch.setattr(ws_manager, "broadcast_title_update", AsyncMock())
    job_id, title_id = await _seed_queued_title()
    coord = _make_coord()
    coord._check_job_completion = AsyncMock()
    handled = await coord._handle_file_wait_result(True, job_id, title_id, Path("t02.mkv"))
    assert handled is False
