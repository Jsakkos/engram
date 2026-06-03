"""Regression tests for the import/staging job-state + broadcast behaviour.

These guard the bug where import watch-folder jobs progressed on the backend but
the dashboard stayed frozen on the scanning radar: ``identify_from_staging`` tried
``IDENTIFYING -> MATCHING`` directly, which the state machine rejected, so the job
never broadcast its transition out of IDENTIFYING (the per-title matching ran
anyway). See ``identification_coordinator.identify_from_staging``.

The coordinator is driven directly with its DB collaborators stubbed; the
in-memory engine comes from the autouse ``isolate_database`` fixture, but that
fixture does not patch ``identification_coordinator.async_session`` — this module
redirects it explicitly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import app.services.identification_coordinator as idc_mod
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, TitleState
from app.services.event_broadcaster import EventBroadcaster
from app.services.identification_coordinator import IdentificationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory


def _fake_analysis(content_type: ContentType) -> SimpleNamespace:
    """Minimal stand-in for the classification pipeline's result object."""
    return SimpleNamespace(
        content_type=content_type,
        detected_name="Battlestar Galactica",
        detected_season=1,
        confidence=0.95,
        classification_source="staging_import",
        tmdb_id=None,
        tmdb_name="Battlestar Galactica",
        is_ambiguous_movie=False,
        play_all_title_indices=None,
        review_reason=None,
        _tmdb_signal=None,
    )


async def _make_job(staging_path: str, volume_label: str) -> int:
    """Insert an IDENTIFYING import job into the in-memory DB and return its id."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="import",
            volume_label=volume_label,
            staging_path=staging_path,
            state=JobState.IDENTIFYING,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


def _build_coordinator(content_type: ContentType, monkeypatch):
    """Wire an IdentificationCoordinator with DB/IO collaborators stubbed.

    Returns ``(coordinator, broadcaster_ws, module_ws)`` where ``broadcaster_ws``
    captures per-title broadcasts and ``module_ws`` captures job-level broadcasts.
    """
    broadcaster_ws = AsyncMock()  # ConnectionManager behind EventBroadcaster
    broadcaster = EventBroadcaster(broadcaster_ws)
    state_machine = JobStateMachine(broadcaster)

    coordinator = IdentificationCoordinator(
        analyst=MagicMock(),
        extractor=MagicMock(),
        event_broadcaster=broadcaster,
        state_machine=state_machine,
    )

    # Stub the IO-heavy collaborators so the test stays a pure unit test.
    coordinator._probe_duration = AsyncMock(return_value=1200.0)
    coordinator._run_classification = AsyncMock(return_value=_fake_analysis(content_type))
    coordinator._try_discdb_assignment = AsyncMock(return_value=False)
    coordinator._match_single_file = AsyncMock(return_value=None)
    coordinator._on_match_task_done = Mock()
    coordinator._finalize_disc_job = AsyncMock(return_value=None)
    coordinator._start_subtitle_download = Mock()

    # The autouse isolate_database fixture patches async_session in several
    # modules but NOT this one — redirect it to the in-memory engine, and capture
    # the job-level broadcasts that go through the module-level ws_manager.
    module_ws = AsyncMock()
    monkeypatch.setattr(idc_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(idc_mod, "ws_manager", module_ws)

    return coordinator, broadcaster_ws, module_ws


def _make_staging(tmp_path, count: int):
    staging_dir = tmp_path / "Season 1"
    staging_dir.mkdir()
    for i in range(count):
        (staging_dir / f"episode_t{i:02d}.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)
    return staging_dir


@pytest.mark.asyncio
async def test_tv_import_advances_to_matching_and_broadcasts(tmp_path, monkeypatch):
    """A TV import must leave IDENTIFYING for MATCHING, broadcast the job-level
    transition, and broadcast each title's MATCHING state so the UI shows tracks."""
    staging_dir = _make_staging(tmp_path, count=3)
    coordinator, broadcaster_ws, module_ws = _build_coordinator(ContentType.TV, monkeypatch)

    job_id = await _make_job(str(staging_dir), "BATTLESTAR_GALACTICA_S1D1")
    await coordinator.identify_from_staging(job_id)

    # Job-level state persisted as MATCHING (the core bug: transition was rejected).
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        assert job.state == JobState.MATCHING

    # UI gets the job-level matching signal.
    module_ws.broadcast_job_update.assert_any_await(job_id, JobState.MATCHING.value)

    # Each of the 3 titles broadcasts its MATCHING state immediately (polish #2),
    # so tracks render as "matching" without waiting for the matcher.
    matching_title_calls = [
        c
        for c in broadcaster_ws.broadcast_title_update.await_args_list
        if c.kwargs.get("state") == TitleState.MATCHING.value
    ]
    assert len(matching_title_calls) == 3


@pytest.mark.asyncio
async def test_movie_import_advances_to_organizing_via_state_machine(tmp_path, monkeypatch):
    """A movie import must reach ORGANIZING through the validated state machine so
    transition observers (e.g. the stale-job watchdog) fire — not a silent
    direct assignment."""
    staging_dir = _make_staging(tmp_path, count=1)
    coordinator, _broadcaster_ws, module_ws = _build_coordinator(ContentType.MOVIE, monkeypatch)

    transitions: list = []
    coordinator._state_machine.on_transition(lambda jid, state: transitions.append((jid, state)))

    job_id = await _make_job(str(staging_dir), "INCEPTION_2010")
    await coordinator.identify_from_staging(job_id)

    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        assert job.state == JobState.ORGANIZING

    # The transition observer fired for ORGANIZING — proves we went through the
    # state machine rather than assigning job.state directly.
    assert (job_id, JobState.ORGANIZING) in transitions
