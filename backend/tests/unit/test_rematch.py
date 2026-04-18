"""Unit tests for rematch and reassign functionality.

Tests MatchingCoordinator.rematch_single_title and JobManager.reassign_episode.
"""

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory


async def _seed_job_and_title(
    match_source="discdb",
    matched_episode="S01E01",
    discdb_match_details=None,
    staging_path="/tmp/staging/test",
    output_filename=None,
    **title_overrides,
) -> tuple[DiscJob, DiscTitle]:
    """Create a job and a single title for rematch tests."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="D:",
            volume_label="TEST_DISC_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Test Show",
            detected_season=1,
            staging_path=staging_path,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title_defaults = dict(
            job_id=job.id,
            title_index=0,
            duration_seconds=2400,
            file_size_bytes=1024 * 1024 * 1024,
            state=TitleState.MATCHED,
            matched_episode=matched_episode,
            match_confidence=0.99,
            match_details=json.dumps(
                {"source": "discdb", "episode_title": "Pilot", "matched_episode": "S01E01"}
            ),
            match_source=match_source,
            discdb_match_details=discdb_match_details
            or json.dumps(
                {"source": "discdb", "episode_title": "Pilot", "matched_episode": "S01E01"}
            ),
            output_filename=output_filename,
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

    monkeypatch.setattr(mc_mod, "async_session", _unit_session_factory)

    mock_ws = MagicMock()
    mock_ws.broadcast_title_update = AsyncMock()
    monkeypatch.setattr(mc_mod, "ws_manager", mock_ws)

    mock_broadcaster = MagicMock()
    mock_broadcaster.broadcast_title_matched = AsyncMock()
    mock_broadcaster.broadcast_title_state_changed = AsyncMock()
    mock_broadcaster.broadcast_job_state_changed = AsyncMock()
    mock_state_machine = MagicMock()

    coordinator = mc_mod.MatchingCoordinator(
        event_broadcaster=mock_broadcaster,
        state_machine=mock_state_machine,
    )
    coordinator._check_job_completion = AsyncMock()

    if discdb_mappings is not None:
        coordinator._discdb_mappings = discdb_mappings

    return coordinator


@pytest.mark.asyncio
async def test_rematch_title_with_discdb_restores_stored_details(monkeypatch):
    """rematch_single_title with source_preference='discdb' restores from discdb_match_details."""
    discdb_details = json.dumps(
        {
            "source": "discdb",
            "episode_title": "Pilot",
            "matched_episode": "S01E01",
        }
    )
    job, title = await _seed_job_and_title(
        match_source="engram",
        matched_episode="S01E03",
        discdb_match_details=discdb_details,
    )

    coordinator = _make_coordinator(monkeypatch)

    await coordinator.rematch_single_title(job.id, title.id, source_preference="discdb")

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.match_source == "discdb"
        assert title.matched_episode == "S01E01"  # Restored from discdb details
        assert title.match_details == discdb_details
        assert title.state == TitleState.MATCHED


@pytest.mark.asyncio
async def test_rematch_title_with_engram_clears_and_rematches(monkeypatch, tmp_path):
    """rematch_single_title with source_preference='engram' resets and triggers matching."""
    mc_mod = importlib.import_module("app.services.matching_coordinator")

    # Create a fake staging file
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    fake_file = staging_dir / "title_t00.mkv"
    fake_file.touch()

    job, title = await _seed_job_and_title(
        staging_path=str(staging_dir),
        output_filename=str(fake_file),
    )

    # Mock episode_curator so match_single_file doesn't actually run audio matching
    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock()
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    coordinator = _make_coordinator(monkeypatch)
    # Mock match_single_file on coordinator to track it was called
    coordinator.match_single_file = AsyncMock()

    await coordinator.rematch_single_title(job.id, title.id, source_preference="engram")

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.state == TitleState.MATCHING
        assert title.matched_episode is None
        assert title.match_confidence == 0.0
        assert title.match_source is None

    # Verify match_single_file was triggered
    coordinator.match_single_file.assert_called_once()


@pytest.mark.asyncio
async def test_rematch_title_missing_file_raises(monkeypatch):
    """rematch_single_title raises ValueError when staging file doesn't exist."""
    job, title = await _seed_job_and_title(
        staging_path="/tmp/nonexistent",
        output_filename="/tmp/nonexistent/title_t00.mkv",
    )

    coordinator = _make_coordinator(monkeypatch)

    with pytest.raises(ValueError, match="[Ss]taging file"):
        await coordinator.rematch_single_title(job.id, title.id, source_preference="engram")


@pytest.mark.asyncio
async def test_reassign_episode_sets_user_source(monkeypatch):
    """reassign_episode sets match_source='user' and match_confidence=1.0."""
    jm_mod = importlib.import_module("app.services.job_manager")
    monkeypatch.setattr(jm_mod, "async_session", _unit_session_factory)

    # Patch ws_manager for broadcast
    mock_ws = MagicMock()
    mock_ws.broadcast_title_update = AsyncMock()
    ws_mod = importlib.import_module("app.api.websocket")
    monkeypatch.setattr(ws_mod, "manager", mock_ws)

    job, title = await _seed_job_and_title()

    from app.services.job_manager import job_manager

    await job_manager.reassign_episode(job.id, title.id, "S01E05")

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.matched_episode == "S01E05"
        assert title.match_confidence == 1.0
        assert title.match_source == "user"
        assert title.state == TitleState.MATCHED
