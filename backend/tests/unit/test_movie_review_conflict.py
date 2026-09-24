"""A movie review must be able to resolve a library conflict (#685).

A reviewer picked "Theatrical" for Hairspray and clicked SELECT. The target
``Hairspray (2007) {edition-Theatrical}.mkv`` already existed, so the organize
returned FILE_EXISTS and the job went straight back to REVIEW_NEEDED. Nothing
the reviewer could send changed that: ``apply_review`` never passed a conflict
strategy, so ``organize_movie`` always ran with its "ask" default, and the
``conflict_resolution_default`` setting was read by nothing. Every SELECT
re-parked the job and sent another "needs review" notification.

These tests pin the two ways out: an explicit per-review choice, and the
configured default when the review does not carry one.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

organizer_module = importlib.import_module("app.core.organizer")
config_service_module = importlib.import_module("app.services.config_service")

EXISTING = "/library/movies/Hairspray (2007)/Hairspray (2007) {edition-Theatrical}.mkv"


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


def _set_conflict_default(monkeypatch, value: str) -> None:
    async def _cfg():
        return SimpleNamespace(conflict_resolution_default=value)

    monkeypatch.setattr(config_service_module, "get_config", _cfg)


@pytest.fixture
def conflict_default(monkeypatch):
    """Configured default; "ask" unless a test overrides it."""
    _set_conflict_default(monkeypatch, "ask")
    return lambda value: _set_conflict_default(monkeypatch, value)


def _organize_returning(monkeypatch, result: dict) -> Mock:
    """Stub the library-mode movie organizer and return the mock."""
    m = Mock(return_value=result)
    monkeypatch.setattr(organizer_module.movie_organizer, "organize", m)
    return m


def _file_exists_result() -> dict:
    return {
        "success": False,
        "error": f"File already exists: {EXISTING}",
        "error_code": "FILE_EXISTS",
        "existing_path": EXISTING,
        "main_file": None,
        "extras": [],
        "extras_mapping": {},
    }


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed_movie(tmp_path) -> tuple[int, int]:
    """A movie job parked in review on a file_exists conflict, as in job 280."""
    staged = tmp_path / "title_t02.mkv"
    staged.write_bytes(b"x")
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="/dev/sr0",
            volume_label="HAIRSPRAY_DISC1",
            content_type=ContentType.MOVIE,
            state=JobState.REVIEW_NEEDED,
            detected_title="Hairspray",
            tmdb_name="Hairspray",
            tmdb_year=2007,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=2,
            duration_seconds=6967,
            file_size_bytes=1,
            is_selected=True,
            output_filename=str(staged),
            state=TitleState.REVIEW,
            edition="Theatrical",
            match_details=json.dumps(
                {"error": "file_exists", "message": f"File already exists: {EXISTING}"}
            ),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _load(job_id: int, title_id: int) -> tuple[DiscJob, DiscTitle]:
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id), await session.get(DiscTitle, title_id)


class TestReviewConflictChoice:
    async def test_without_a_choice_the_conflict_still_parks_for_review(
        self, tmp_path, monkeypatch, conflict_default
    ):
        organize = _organize_returning(monkeypatch, _file_exists_result())
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(job_id, title_id, edition="Theatrical")

        assert organize.call_args.kwargs["conflict_resolution"] == "ask"
        job, title = await _load(job_id, title_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert title.state == TitleState.REVIEW

    @pytest.mark.parametrize("choice", ["overwrite", "rename"])
    async def test_explicit_choice_reaches_the_organizer_and_completes(
        self, tmp_path, monkeypatch, conflict_default, choice
    ):
        final = "/library/movies/Hairspray (2007)/Hairspray (2007) {edition-Theatrical}.mkv"
        organize = _organize_returning(
            monkeypatch,
            {"success": True, "main_file": final, "extras": [], "extras_mapping": {}},
        )
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(
            job_id, title_id, edition="Theatrical", conflict_resolution=choice
        )

        assert organize.call_args.kwargs["conflict_resolution"] == choice
        job, title = await _load(job_id, title_id)
        assert job.state == JobState.COMPLETED
        assert title.state == TitleState.COMPLETED
        assert title.organized_to == final
        # The choice is recorded on the title (the column existed but was never written).
        assert title.conflict_resolution == choice
        # The resolved conflict no longer reads as a pending file_exists review.
        assert "error" not in json.loads(title.match_details or "{}")

    async def test_explicit_choice_overrides_the_configured_default(
        self, tmp_path, monkeypatch, conflict_default
    ):
        conflict_default("skip")
        organize = _organize_returning(
            monkeypatch,
            {"success": True, "main_file": "/x.mkv", "extras": [], "extras_mapping": {}},
        )
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(job_id, title_id, conflict_resolution="overwrite")

        assert organize.call_args.kwargs["conflict_resolution"] == "overwrite"


class TestConfiguredDefault:
    async def test_configured_default_applies_when_the_review_has_no_choice(
        self, tmp_path, monkeypatch, conflict_default
    ):
        conflict_default("rename")
        organize = _organize_returning(
            monkeypatch,
            {"success": True, "main_file": "/x (v2).mkv", "extras": [], "extras_mapping": {}},
        )
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(job_id, title_id, edition="Theatrical")

        assert organize.call_args.kwargs["conflict_resolution"] == "rename"
        job, _ = await _load(job_id, title_id)
        assert job.state == JobState.COMPLETED

    async def test_unknown_configured_value_falls_back_to_ask(
        self, tmp_path, monkeypatch, conflict_default
    ):
        conflict_default("bogus")
        organize = _organize_returning(monkeypatch, _file_exists_result())
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(job_id, title_id)

        assert organize.call_args.kwargs["conflict_resolution"] == "ask"


class TestSkipKeepsTheExistingFile:
    async def test_skip_completes_without_claiming_an_organized_file(
        self, tmp_path, monkeypatch, conflict_default
    ):
        # resolve_conflict("skip") reports success with no main_file. Treating it
        # like a normal success used to be impossible (nothing passed "skip"), but
        # would have recorded organized_to="None" and final_path="None".
        _organize_returning(
            monkeypatch,
            {"success": True, "skipped": True, "main_file": None, "extras": []},
        )
        job_id, title_id = await _seed_movie(tmp_path)

        await _make_coord().apply_review(job_id, title_id, conflict_resolution="skip")

        job, title = await _load(job_id, title_id)
        assert job.state == JobState.COMPLETED
        assert job.final_path is None
        assert title.organized_to is None
        assert title.state == TitleState.FAILED
        assert "already exists" in json.loads(title.match_details)["reason"]


class TestReviewRouteCarriesTheChoice:
    @pytest.fixture
    async def client(self):
        from httpx import ASGITransport, AsyncClient

        from app.database import get_session
        from app.main import app

        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.clear()

    @pytest.fixture
    def apply_review(self, monkeypatch):
        # importlib, not `import ... as`: the package re-exports the singleton.
        jm = importlib.import_module("app.services.job_manager")
        m = AsyncMock()
        monkeypatch.setattr(jm.job_manager, "apply_review", m)
        return m

    async def test_single_review_forwards_conflict_resolution(self, client, apply_review, tmp_path):
        job_id, title_id = await _seed_movie(tmp_path)

        response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"title_id": title_id, "edition": "Theatrical", "conflict_resolution": "rename"},
        )

        assert response.status_code == 200
        assert apply_review.call_args.kwargs["conflict_resolution"] == "rename"

    async def test_unknown_strategy_is_rejected(self, client, apply_review, tmp_path):
        job_id, title_id = await _seed_movie(tmp_path)

        response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"title_id": title_id, "conflict_resolution": "delete-everything"},
        )

        assert response.status_code == 422
        apply_review.assert_not_called()
