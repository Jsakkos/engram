"""Unit tests for match_source tracking in MatchingCoordinator.

Verifies that try_discdb_assignment sets match_source='discdb' and
_match_single_file_inner sets match_source='engram'.
"""

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory


async def _seed_job_and_title(
    **title_overrides,
) -> tuple[DiscJob, DiscTitle]:
    """Create a job and a single title for matching tests."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="D:",
            volume_label="TEST_DISC_S1D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Test Show",
            detected_season=1,
            staging_path="/tmp/staging/test",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title_defaults = dict(
            job_id=job.id,
            title_index=0,
            duration_seconds=2400,
            file_size_bytes=1024 * 1024 * 1024,
            state=TitleState.MATCHING,
        )
        title_defaults.update(title_overrides)
        title = DiscTitle(**title_defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)

        return job, title


def _make_coordinator(monkeypatch, discdb_mappings=None):
    """Create a MatchingCoordinator with ws_manager and async_session patched."""
    mc_mod = importlib.import_module("app.services.matching_coordinator")

    # Patch async_session in matching_coordinator module
    monkeypatch.setattr(mc_mod, "async_session", _unit_session_factory)

    # Patch ws_manager.broadcast_title_update to async no-op
    mock_ws = MagicMock()
    mock_ws.broadcast_title_update = AsyncMock()
    monkeypatch.setattr(mc_mod, "ws_manager", mock_ws)

    # Create coordinator with mock dependencies
    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_title_matched = AsyncMock()
    mock_broadcaster.broadcast_title_state_changed = AsyncMock()
    mock_broadcaster.broadcast_job_state_changed = AsyncMock()
    mock_state_machine = MagicMock()

    coordinator = mc_mod.MatchingCoordinator(
        event_broadcaster=mock_broadcaster,
        state_machine=mock_state_machine,
    )

    # _check_job_completion is set externally by JobManager
    coordinator._check_job_completion = AsyncMock()

    if discdb_mappings is not None:
        coordinator._discdb_mappings = discdb_mappings

    return coordinator


@pytest.mark.asyncio
async def test_discdb_assignment_sets_match_source_and_details(monkeypatch):
    """try_discdb_assignment should set match_source='discdb' and populate discdb_match_details."""
    from app.core.discdb_classifier import DiscDbTitleMapping

    job, title = await _seed_job_and_title()

    mapping = DiscDbTitleMapping(
        index=0,
        title_type="Episode",
        episode_title="Pilot",
        season=1,
        episode=1,
        duration_seconds=2400,
        size_bytes=1024 * 1024 * 1024,
    )
    coordinator = _make_coordinator(
        monkeypatch,
        discdb_mappings={job.id: [mapping]},
    )

    async with _unit_session_factory() as session:
        # Re-load title in this session
        title = await session.get(DiscTitle, title.id)
        result = await coordinator.try_discdb_assignment(job.id, title, session)

    assert result is True

    # Reload from DB to verify persistence
    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.match_source == "discdb"
        assert title.discdb_match_details is not None
        assert title.matched_episode == "S01E01"
        assert title.state == TitleState.MATCHED


@pytest.mark.asyncio
async def test_engram_matching_sets_match_source(monkeypatch):
    """_match_single_file_inner should set match_source='engram' after audio matching."""
    mc_mod = importlib.import_module("app.services.matching_coordinator")

    job, title = await _seed_job_and_title()

    # Mock episode_curator.match_single_file
    mock_result = MagicMock()
    mock_result.episode_code = "S01E03"
    mock_result.confidence = 0.85
    mock_result.needs_review = False
    mock_result.match_details = {"method": "subtitle", "score": 0.85}

    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    coordinator = _make_coordinator(monkeypatch, discdb_mappings={})

    # Use a fake file path (curator is mocked so it won't be read)
    fake_path = Path("/tmp/staging/test/title_t00.mkv")

    await coordinator._match_single_file_inner(job.id, title.id, fake_path)

    # Reload from DB
    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.match_source == "engram"
        assert title.matched_episode == "S01E03"
        assert title.match_confidence == 0.85


@pytest.mark.asyncio
async def test_discdb_match_details_stored_separately(monkeypatch):
    """discdb_match_details should be a copy of match_details at assignment time."""
    from app.core.discdb_classifier import DiscDbTitleMapping

    job, title = await _seed_job_and_title()

    mapping = DiscDbTitleMapping(
        index=0,
        title_type="Episode",
        episode_title="Currahee",
        season=1,
        episode=1,
        duration_seconds=2400,
        size_bytes=1024 * 1024 * 1024,
    )
    coordinator = _make_coordinator(
        monkeypatch,
        discdb_mappings={job.id: [mapping]},
    )

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        await coordinator.try_discdb_assignment(job.id, title, session)

    # Reload and verify both fields match
    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.discdb_match_details is not None
        assert title.match_details is not None
        # discdb_match_details should equal match_details at assignment time
        assert title.discdb_match_details == title.match_details
        # Both should contain "discdb" source indicator
        assert "discdb" in title.discdb_match_details
