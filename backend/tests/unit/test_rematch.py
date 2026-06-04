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
async def test_rematch_engram_pings_watchdog_clock(monkeypatch, tmp_path):
    """Engram re-match dispatch pings note_activity so the stale-job watchdog
    doesn't force-advance a job that is actively (deep) re-matching."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    fake_file = staging_dir / "title_t00.mkv"
    fake_file.touch()

    job, title = await _seed_job_and_title(
        staging_path=str(staging_dir),
        output_filename=str(fake_file),
    )

    coordinator = _make_coordinator(monkeypatch)
    coordinator.match_single_file = AsyncMock()
    pinged: list[int] = []
    coordinator._note_activity = lambda jid: pinged.append(jid)

    await coordinator.rematch_single_title(job.id, title.id, source_preference="engram")

    assert job.id in pinged


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
async def test_advisory_rematch_surfaces_for_review_without_organizing(monkeypatch, tmp_path):
    """An advisory (manual per-track) re-match holds even a CONFIDENT result in
    REVIEW with forced_review — surfaced for confirmation, never auto-organized."""
    mc_mod = importlib.import_module("app.services.matching_coordinator")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    fake_file = staging_dir / "title_t00.mkv"
    fake_file.touch()

    job, title = await _seed_job_and_title(
        match_source="engram",
        matched_episode=None,
        staging_path=str(staging_dir),
        output_filename=str(fake_file),
        state=TitleState.MATCHING,
        match_details=None,
    )

    # Curator returns a confident match (needs_review=False) — exactly the case
    # the old flow would have set MATCHED and auto-organized.
    result = MagicMock()
    result.episode_code = "S01E09"
    result.confidence = 0.93
    result.needs_review = False
    result.match_details = {"method": "full_transcription", "score": 0.93}

    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    coordinator = _make_coordinator(monkeypatch)

    await coordinator._match_single_file_inner(job.id, title.id, fake_file, advisory=True)

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        # REVIEW (not MATCHED) → check_job_completion holds the job, never organizes.
        assert title.state == TitleState.REVIEW
        # Candidate is kept as the inspector's starting suggestion.
        assert title.matched_episode == "S01E09"
        details = json.loads(title.match_details)
        assert details.get("forced_review") is True
        # No sibling holds S01E09 → no false "already exists" warning.
        assert "error" not in details

    coordinator._check_job_completion.assert_awaited()


@pytest.mark.asyncio
async def test_advisory_rematch_warns_when_episode_already_on_disc(monkeypatch, tmp_path):
    """When the advisory candidate is already organized by another COMPLETED track
    on the same disc, surface a structured file_exists warning right in review."""
    mc_mod = importlib.import_module("app.services.matching_coordinator")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    fake_file = staging_dir / "title_t02.mkv"
    fake_file.touch()

    job, title = await _seed_job_and_title(
        match_source="engram",
        matched_episode=None,
        staging_path=str(staging_dir),
        output_filename=str(fake_file),
        state=TitleState.MATCHING,
        match_details=None,
    )
    # A sibling on the same disc already organized S01E09 (unpadded to exercise
    # the format-tolerant comparison).
    async with _unit_session_factory() as session:
        session.add(
            DiscTitle(
                job_id=job.id,
                title_index=1,
                duration_seconds=2400,
                state=TitleState.COMPLETED,
                matched_episode="S1E9",
                match_confidence=0.95,
            )
        )
        await session.commit()

    result = MagicMock()
    result.episode_code = "S01E09"
    result.confidence = 0.93
    result.needs_review = False
    result.match_details = {"method": "full_transcription", "score": 0.93}

    mock_curator = MagicMock()
    mock_curator.match_single_file = AsyncMock(return_value=result)
    monkeypatch.setattr(mc_mod, "episode_curator", mock_curator)

    coordinator = _make_coordinator(monkeypatch)
    await coordinator._match_single_file_inner(job.id, title.id, fake_file, advisory=True)

    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title.id)
        assert title.state == TitleState.REVIEW
        details = json.loads(title.match_details)
        assert details.get("forced_review") is True
        assert details.get("error") == "file_exists"
        assert "message" in details


@pytest.mark.asyncio
async def test_rematch_single_title_deep_uses_strict_params(monkeypatch):
    """job_manager.rematch_single_title(deep=True) threads STRICT scan params."""
    from app.services.job_manager import job_manager
    from app.services.matching_coordinator import STRICT_MIN_VOTES, STRICT_SCAN_POINTS

    captured: dict = {}

    async def _capture(
        job_id,
        title_id,
        source_preference=None,
        num_points=None,
        min_vote_count=None,
        advisory=False,
    ):
        captured.update(
            source_preference=source_preference,
            num_points=num_points,
            min_vote_count=min_vote_count,
            advisory=advisory,
        )

    monkeypatch.setattr(job_manager._matching, "rematch_single_title", _capture)

    await job_manager.rematch_single_title(7, 3, source_preference="engram", deep=True)

    assert captured["num_points"] == STRICT_SCAN_POINTS
    assert captured["min_vote_count"] == STRICT_MIN_VOTES
    assert captured["source_preference"] == "engram"
    # The manual route always runs advisory: surface for confirmation, never auto-organize.
    assert captured["advisory"] is True


@pytest.mark.asyncio
async def test_rematch_single_title_shallow_keeps_defaults(monkeypatch):
    """Without deep, rematch_single_title leaves matcher params at their defaults."""
    from app.services.job_manager import job_manager

    captured: dict = {}

    async def _capture(
        job_id,
        title_id,
        source_preference=None,
        num_points=None,
        min_vote_count=None,
        advisory=False,
    ):
        captured.update(num_points=num_points, min_vote_count=min_vote_count, advisory=advisory)

    monkeypatch.setattr(job_manager._matching, "rematch_single_title", _capture)

    await job_manager.rematch_single_title(7, 3, source_preference="engram")

    assert captured["num_points"] is None
    assert captured["min_vote_count"] is None
    # Even a shallow manual re-match is advisory (it is the user-initiated route).
    assert captured["advisory"] is True


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
