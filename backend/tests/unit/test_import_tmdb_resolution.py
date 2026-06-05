"""Regression tests: imports / generic-label jobs must resolve a missing ``tmdb_id``.

Bug: a watch-folder import derives show + season from the folder tree, but the
identify-time TMDB lookup is gated on the (nameless) volume label ("SEASON_3"),
so ``job.tmdb_id`` was persisted as null. Since the subtitle/reference cache is
keyed by tmdb_id (#288), a null id sent the matcher to a non-existent name-keyed
directory (``cache/data/Seinfeld``) instead of ``cache/data/1400`` -> "No reference
subtitle files found" -> every track degraded to a filename guess and went to
review. ``identify_from_staging`` (and ``set_name_and_resume``) must resolve and
persist the id from the known title, while still leaving same-name twins (Frasier
1993 vs 2023) for review.

The coordinator is driven directly with its DB / IO collaborators stubbed; the
in-memory engine comes from the autouse ``isolate_database`` fixture, which does
NOT patch ``identification_coordinator.async_session`` — this module redirects it,
plus the TMDB resolver (``classify_from_tmdb``) and config loader.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import app.services.identification_coordinator as idc_mod
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.event_broadcaster import EventBroadcaster
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory


def _fake_analysis(detected_name: str = "Seinfeld") -> SimpleNamespace:
    """Classification result for a generic-label import: title/season known, id null."""
    return SimpleNamespace(
        content_type=ContentType.TV,
        detected_name=detected_name,
        detected_season=3,
        confidence=0.95,
        classification_source="staging_import",
        tmdb_id=None,
        tmdb_name=detected_name,
        is_ambiguous_movie=False,
        play_all_title_indices=None,
        review_reason="Same-name collision",
        _tmdb_signal=None,
    )


def _confident_signal() -> SimpleNamespace:
    """A confident single TMDB match (no same-name twins). ``all_candidates`` carries
    the show's own year so ``_resolve_show_year`` short-circuits without a network call."""
    return SimpleNamespace(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=1400,
        tmdb_name="Seinfeld",
        ambiguous_identity=False,
        candidates=[],
        all_candidates=[{"tmdb_id": 1400, "name": "Seinfeld", "year": "1989", "popularity": 80.0}],
    )


def _ambiguous_signal() -> SimpleNamespace:
    """A same-name collision (Frasier 1993 vs 2023) — must NOT be auto-picked."""
    return SimpleNamespace(
        content_type=ContentType.TV,
        confidence=0.60,
        tmdb_id=3452,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993"},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023"},
        ],
        all_candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 30.0},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 20.0},
        ],
    )


def _make_staging(tmp_path, count: int):
    staging_dir = tmp_path / "Season 3"
    staging_dir.mkdir()
    for i in range(count):
        (staging_dir / f"{i:02d}.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)
    return staging_dir


async def _make_import_job(staging_path: str, volume_label: str) -> int:
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


def _build_coordinator(analysis, monkeypatch, *, signal):
    """Wire an IdentificationCoordinator with DB / IO / TMDB collaborators stubbed."""
    broadcaster_ws = AsyncMock()
    broadcaster = EventBroadcaster(broadcaster_ws)
    state_machine = JobStateMachine(broadcaster)

    coordinator = idc_mod.IdentificationCoordinator(
        analyst=MagicMock(),
        extractor=MagicMock(),
        event_broadcaster=broadcaster,
        state_machine=state_machine,
    )

    coordinator._run_classification = AsyncMock(return_value=analysis)
    coordinator._try_discdb_assignment = AsyncMock(return_value=False)
    coordinator._match_single_file = AsyncMock(return_value=None)
    coordinator._on_match_task_done = Mock()
    coordinator._finalize_disc_job = AsyncMock(return_value=None)
    coordinator._start_subtitle_download = Mock()

    module_ws = AsyncMock()
    monkeypatch.setattr(idc_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(idc_mod, "ws_manager", module_ws)
    # raising=False: the production import promotes classify_from_tmdb to a module
    # attribute as part of the fix; keep the patch valid in the pre-fix (RED) run.
    monkeypatch.setattr(
        idc_mod, "classify_from_tmdb", MagicMock(return_value=signal), raising=False
    )
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(tmdb_api_key="testkey")),
    )

    return coordinator, broadcaster_ws, module_ws


@pytest.mark.asyncio
async def test_import_resolves_missing_tmdb_id_and_keys_subtitles(tmp_path, monkeypatch):
    """A generic-label TV import resolves its tmdb_id from the folder-derived title,
    persists it, and keys the subtitle download on the real id (1400) — so the matcher
    reads ``cache/data/1400`` instead of the empty name-keyed dir."""
    staging = _make_staging(tmp_path, count=3)
    coordinator, _bw, _mw = _build_coordinator(
        _fake_analysis(), monkeypatch, signal=_confident_signal()
    )
    job_id = await _make_import_job(str(staging), "SEASON_3")

    await coordinator.identify_from_staging(job_id)

    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        assert job.tmdb_id == 1400  # resolved, not left null
        assert job.state == JobState.MATCHING

    coordinator._start_subtitle_download.assert_called_once_with(job_id, "Seinfeld", 3, 1400)


@pytest.mark.asyncio
async def test_import_ambiguous_show_stays_null_and_routes_to_review(tmp_path, monkeypatch):
    """A same-name twin (Frasier 1993 vs 2023) must NOT be auto-resolved: tmdb_id stays
    null and the job routes to review for the user to disambiguate (#287 safety)."""
    staging = _make_staging(tmp_path, count=3)
    coordinator, _bw, _mw = _build_coordinator(
        _fake_analysis(detected_name="Frasier"), monkeypatch, signal=_ambiguous_signal()
    )
    job_id = await _make_import_job(str(staging), "SEASON_1")

    await coordinator.identify_from_staging(job_id)

    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        assert job.tmdb_id is None  # never auto-picked
        assert job.state == JobState.REVIEW_NEEDED

    coordinator._start_subtitle_download.assert_not_called()


@pytest.mark.asyncio
async def test_set_name_and_resume_resolves_tmdb_id(tmp_path, monkeypatch):
    """The generic-label disc path (user names an unreadable disc) has the identical
    bug: ``set_name_and_resume`` must resolve+persist the id for the provided name."""
    coordinator, _bw, _mw = _build_coordinator(
        _fake_analysis(), monkeypatch, signal=_confident_signal()
    )
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SEASON_3",
            state=JobState.REVIEW_NEEDED,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    await coordinator.set_name_and_resume(job_id, "Seinfeld", "tv", season=3)

    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        assert job.tmdb_id == 1400
        assert job.detected_title == "Seinfeld"
        assert job.state == JobState.RIPPING
