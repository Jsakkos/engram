"""Organize failures must say WHY they failed (#563).

A Docker user hit a library-permissions problem while saving a TV review and
got only "Failed to organize files": no errno, no path, and nothing in the
diagnostics bundle. Two defects combined to erase the failure:

1. The TV organize sweeps logged ``org_result["error"]`` and threw it away; the
   job was failed with a hardcoded string. (The movie path already propagated
   the real error, which is why an identical PermissionError was legible for a
   movie and opaque for a TV disc.)
2. Review saves reached the coordinator straight from the API handler with no
   ``job_log_context``, so every line the sweep emitted was tagged ``job=-``
   and the bundle's ``| job=<id> |`` grep dropped all of them.

These tests pin both, plus the library-writability preflight that turns a
mid-sweep surprise into an upfront, actionable error.
"""

import importlib
import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

PERMISSION_ERROR = "[Errno 13] Permission denied: '/library/tv'"


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


@pytest.fixture
def failing_organize(monkeypatch):
    """Stub every TV organize entry point to a hard (non-FILE_EXISTS) failure."""
    import app.core.organizer as org

    result = {"success": False, "final_path": None, "error": PERMISSION_ERROR}
    m = Mock(return_value=result)
    monkeypatch.setattr(org.tv_organizer, "organize", m)
    monkeypatch.setattr(org, "organize_tv_episode", Mock(return_value=result))
    monkeypatch.setattr(org, "organize_tv_extras", Mock(return_value=result))
    return m


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed(titles, staging, *, state=JobState.MATCHING) -> int:
    """Seed a TV job from (title_index, episode, output_filename, state) tuples."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=state,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
            subtitle_status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx, ep, outfn, tstate in titles:
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    duration_seconds=1380,
                    matched_episode=ep,
                    match_confidence=0.8,
                    state=tstate,
                    output_filename=outfn,
                )
            )
        await session.commit()
        return job.id


async def _load(job_id):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        return job, {t.title_index: t for t in titles}


@pytest.mark.unit
class TestReviewSaveSurfacesOrganizeError:
    """The exact path from the bug report: save a review, organize fails."""

    async def test_error_message_carries_the_real_cause(self, tmp_path, failing_organize):
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed(
            [(0, None, str(f0), TitleState.REVIEW)],
            staging=str(tmp_path),
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": titles[0].id, "episode_code": "S01E01"}]
        )

        job, _ = await _load(job_id)
        assert job.state == JobState.FAILED
        # The whole point of #563: the errno and path must reach the user.
        assert PERMISSION_ERROR in job.error_message

    async def test_failed_title_records_organize_failed_details(self, tmp_path, failing_organize):
        """The Inspector renders match_details.error; an organize failure must
        populate it the way FILE_EXISTS already does."""
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed(
            [(0, None, str(f0), TitleState.REVIEW)],
            staging=str(tmp_path),
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": titles[0].id, "episode_code": "S01E01"}]
        )

        _, titles = await _load(job_id)
        details = json.loads(titles[0].match_details)
        assert details["error"] == "organize_failed"
        assert PERMISSION_ERROR in details["message"]

    async def test_missing_source_files_get_their_own_message(self, tmp_path, failing_organize):
        """Zero organized titles because staging vanished is a DIFFERENT failure
        from an unwritable library; it must not report the same opaque string."""
        job_id = await _seed(
            [(0, None, str(tmp_path / "gone_t00.mkv"), TitleState.REVIEW)],
            staging=str(tmp_path),
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": titles[0].id, "episode_code": "S01E01"}]
        )

        job, _ = await _load(job_id)
        assert job.state == JobState.FAILED
        assert "gone_t00.mkv" in job.error_message
        assert PERMISSION_ERROR not in job.error_message

    async def test_multiple_failures_are_counted_not_truncated_silently(
        self, tmp_path, failing_organize
    ):
        files = []
        for i in range(3):
            f = tmp_path / f"show_t0{i}.mkv"
            f.write_text("")
            files.append(f)
        job_id = await _seed(
            [(i, None, str(f), TitleState.REVIEW) for i, f in enumerate(files)],
            staging=str(tmp_path),
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)

        await _make_coord().apply_review_batch(
            job_id,
            [{"title_id": titles[i].id, "episode_code": f"S01E0{i + 1}"} for i in range(3)],
        )

        job, _ = await _load(job_id)
        assert PERMISSION_ERROR in job.error_message
        assert "+2 more" in job.error_message


@pytest.mark.unit
class TestAutoFinalizeSurfacesOrganizeError:
    """finalize_disc_job (the non-review path) parks failures in REVIEW; it must
    say why, or the job reason claims 'needs manual episode assignment' for what
    is really a permissions problem."""

    async def test_records_organize_failed_details(self, tmp_path, failing_organize):
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed([(0, "S01E01", str(f0), TitleState.MATCHED)], staging=str(tmp_path))

        await _make_coord().finalize_disc_job(job_id)

        _, titles = await _load(job_id)
        assert titles[0].state == TitleState.REVIEW
        details = json.loads(titles[0].match_details)
        assert details["error"] == "organize_failed"
        assert PERMISSION_ERROR in details["message"]

    async def test_review_reason_names_the_organize_failure(self, tmp_path, failing_organize):
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed([(0, "S01E01", str(f0), TitleState.MATCHED)], staging=str(tmp_path))

        await _make_coord().finalize_disc_job(job_id)

        job, _ = await _load(job_id)
        assert "episode assignment" not in job.review_reason
        assert "organize" in job.review_reason.lower()


@pytest.mark.unit
class TestProcessMatchedTitlesSurfacesOrganizeError:
    async def test_records_organize_failed_details(self, tmp_path, failing_organize):
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed([(0, "S01E01", str(f0), TitleState.MATCHED)], staging=str(tmp_path))

        await _make_coord().process_matched_titles(job_id)

        _, titles = await _load(job_id)
        details = json.loads(titles[0].match_details)
        assert details["error"] == "organize_failed"
        assert PERMISSION_ERROR in details["message"]


@pytest.mark.unit
class TestLibraryWritablePreflight:
    """Catch an unusable library root BEFORE ripping/organizing, so the user is
    told at settings-save time instead of after a 40-minute rip."""

    def test_returns_none_for_a_writable_root(self, tmp_path):
        from app.core.organizer import check_library_writable

        assert check_library_writable(tmp_path / "tv") is None

    def test_reports_the_os_error_for_an_unusable_root(self, tmp_path):
        from app.core.organizer import check_library_writable

        # A file where a directory must be: an OSError on every platform.
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("")

        reason = check_library_writable(blocker / "tv")

        assert reason is not None
        assert "not_a_dir" in reason

    def test_create_false_does_not_create_the_root(self, tmp_path):
        """Validating a path must never be the thing that creates it."""
        from app.core.organizer import check_library_writable

        root = tmp_path / "not_yet"

        assert check_library_writable(root, create=False) is None
        assert not root.exists()

    def test_create_false_still_rejects_an_existing_unwritable_root(self, tmp_path):
        """The reported bug's shape: the path EXISTS but cannot be written."""
        from app.core.organizer import check_library_writable

        blocker = tmp_path / "not_a_dir"
        blocker.write_text("")

        reason = check_library_writable(blocker, create=False)

        assert reason is not None
        assert "not writable" in reason

    def test_blank_path_is_reported_as_unconfigured(self):
        from app.core.organizer import check_library_writable

        reason = check_library_writable("")
        assert reason is not None
        assert "not configured" in reason.lower()

    async def test_review_save_fails_fast_with_the_preflight_reason(
        self, tmp_path, monkeypatch, failing_organize
    ):
        """An unwritable library must fail the job with the preflight reason
        rather than running the whole sweep and reporting a per-title error."""
        import app.services.finalization_coordinator as fc

        monkeypatch.setattr(
            fc, "check_library_writable", lambda p: "Library path /library/tv is not writable: boom"
        )
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed(
            [(0, None, str(f0), TitleState.REVIEW)],
            staging=str(tmp_path),
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": titles[0].id, "episode_code": "S01E01"}]
        )

        job, _ = await _load(job_id)
        assert job.state == JobState.FAILED
        assert "not writable" in job.error_message
        # Fail fast: the sweep must not have attempted any move.
        assert failing_organize.call_count == 0


@pytest.mark.unit
class TestConfigSaveRejectsUnwritableLibrary:
    """Catch the misconfiguration at settings-save time, before a disc is ripped."""

    @pytest.fixture
    async def client(self):
        from httpx import ASGITransport, AsyncClient

        from app.api.routes import get_session
        from app.main import app

        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.clear()

    async def test_existing_unwritable_library_is_rejected(self, client, tmp_path):
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("")

        r = await client.put("/api/config", json={"library_tv_path": str(blocker)})

        assert r.status_code == 422
        assert "library_tv_path" in r.json()["detail"]

    async def test_a_path_that_does_not_exist_yet_is_accepted(self, client, tmp_path):
        """Saving a path before its share is mounted must not be blocked.

        (config_service.update_config then calls ensure_paths_exist, which does
        create it; that is long-standing save behavior, separate from the
        validation added here. The non-creating property of the check itself is
        covered by test_create_false_does_not_create_the_root.)
        """
        root = tmp_path / "not_yet"

        r = await client.put("/api/config", json={"library_tv_path": str(root)})

        assert r.status_code == 200


@pytest.mark.unit
class TestReviewSaveIsJobTagged:
    """Defect 2: the bundle greps '| job=<id> |'. Work driven straight from an
    API handler must bind the job into the logging context or its lines (including
    organizer.py's logger.exception traceback) are invisible in diagnostics."""

    def _capture_job_id(self):
        """Return (recorder, probe) where probe() logs a line and records its tag."""
        from loguru import logger as loguru_logger

        seen: list = []

        def probe():
            tags: list = []
            sink = loguru_logger.add(
                lambda m: tags.append(m.record["extra"].get("job_id")), level="DEBUG"
            )
            loguru_logger.info("probe")
            loguru_logger.remove(sink)
            seen.append(tags[-1] if tags else None)

        return seen, probe

    async def test_apply_review_batch_binds_job_log_context(self, monkeypatch):
        # importlib, not `import app.services.job_manager as jm`: the package
        # __init__ re-exports the singleton, shadowing the submodule name.
        jm = importlib.import_module("app.services.job_manager")

        seen, probe = self._capture_job_id()

        async def _fake(job_id, decisions):
            probe()

        monkeypatch.setattr(jm.job_manager._finalization, "apply_review_batch", _fake)
        monkeypatch.setattr(jm.job_manager._prewarmer, "cancel_for_job", lambda jid: None)

        await jm.job_manager.apply_review_batch(42, [{"title_id": 1, "episode_code": "S01E01"}])

        assert seen == [42]

    async def test_apply_review_binds_job_log_context(self, monkeypatch):
        # importlib, not `import app.services.job_manager as jm`: the package
        # __init__ re-exports the singleton, shadowing the submodule name.
        jm = importlib.import_module("app.services.job_manager")

        seen, probe = self._capture_job_id()

        async def _fake(job_id, title_id, episode_code=None, edition=None):
            probe()

        monkeypatch.setattr(jm.job_manager._finalization, "apply_review", _fake)
        monkeypatch.setattr(jm.job_manager._prewarmer, "cancel_for_job", lambda jid: None)

        await jm.job_manager.apply_review(42, 1, episode_code="S01E01")

        assert seen == [42]
