"""Every TV rename path hands the job's episode namespace to the organizer (#826).

``{episode_title}`` is looked up in the namespace the episode code was matched in,
and the finalization sweeps do not bind that ContextVar themselves, so each of
the three sweeps has to forward ``job.episode_namespace`` explicitly. A site that
forgets would quietly name a TheTVDB-numbered file with the TMDB title of the
same number.
"""

import importlib
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

organizer_module = importlib.import_module("app.core.organizer")


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


def _succeed(src, show, ep, *a, **k):
    return {"success": True, "final_path": f"/library/tv/{show}/{ep}.mkv", "error": None}


@pytest.fixture(params=["library", "default"])
def organize(request, monkeypatch, tmp_path) -> Mock:
    """The TV organize call a sweep makes: ``organize_tv_episode`` when the job has a
    library path, ``tv_organizer.organize`` otherwise. The mock records its kwargs.

    The library root lives under ``tmp_path``: finalize runs a writability preflight on it,
    and a hard-coded ``/library`` is writable on Windows but not on a Linux runner."""
    library = tmp_path / "library" / "tv"
    library.mkdir(parents=True)
    m = Mock(side_effect=_succeed)
    monkeypatch.setattr(organizer_module.tv_organizer, "organize", m)
    monkeypatch.setattr(organizer_module, "organize_tv_episode", m)
    monkeypatch.setattr(
        "app.services.finalization_coordinator._library_path_for_job",
        lambda job, kind: library if request.param == "library" else None,
    )
    return m


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed(tmp_path, *, namespace, state, job_state):
    staged = tmp_path / "show_t00.mkv"
    staged.write_text("")
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=job_state,
            detected_title="Some Show",
            detected_season=1,
            staging_path=str(tmp_path),
            subtitle_status="completed",
            episode_namespace=namespace,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1380,
            matched_episode="S01E03" if state != TitleState.REVIEW else None,
            match_confidence=0.8,
            state=state,
            output_filename=str(staged),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


@pytest.mark.unit
@pytest.mark.parametrize("namespace", ["tvdb", "tmdb"])
class TestSweepsForwardTheNamespace:
    async def test_automatic_finalize(self, tmp_path, organize, namespace):
        job_id, _ = await _seed(
            tmp_path, namespace=namespace, state=TitleState.MATCHED, job_state=JobState.MATCHING
        )
        await _make_coord().finalize_disc_job(job_id)
        assert organize.call_args.kwargs["episode_namespace"] == namespace

    async def test_process_matched_titles(self, tmp_path, organize, namespace):
        job_id, _ = await _seed(
            tmp_path,
            namespace=namespace,
            state=TitleState.MATCHED,
            job_state=JobState.REVIEW_NEEDED,
        )
        await _make_coord().process_matched_titles(job_id)
        assert organize.call_args.kwargs["episode_namespace"] == namespace

    async def test_review_batch_finalize(self, tmp_path, organize, namespace):
        job_id, title_id = await _seed(
            tmp_path, namespace=namespace, state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )
        await _make_coord().apply_review_batch(
            job_id, [{"title_id": title_id, "episode_code": "S01E03"}]
        )
        assert organize.call_args.kwargs["episode_namespace"] == namespace
