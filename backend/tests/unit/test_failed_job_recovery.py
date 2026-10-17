"""Recovery actions for FAILED jobs (#713).

A failed job used to be a dead end: the only way back was discovering that the
Import modal accepts the staging folder. These tests pin the three recovery
surfaces: the assessment (can this job be retried, and if not, why), the retry
itself (a NEW job through the import pipeline, never a FAILED -> * transition),
and opening the staging folder on the host.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.failed_job_recovery import assess_staging_recovery
from app.services.import_guard import ImportBlock, StagingJobResult
from tests.unit.conftest import _unit_session_factory


def _mkv(path, size=2048):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * size)
    return path


def _job(**kw) -> DiscJob:
    defaults = {"drive_id": "E:", "volume_label": "SHOW_S1D1", "state": JobState.FAILED}
    defaults.update(kw)
    return DiscJob(**defaults)


class TestAssessStagingRecovery:
    def test_non_failed_job_is_not_retryable(self, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job = _job(state=JobState.COMPLETED, staging_path=str(tmp_path))
        r = assess_staging_recovery(job, [])
        assert not r.can_retry
        assert "failed" in r.reason.lower()

    def test_job_without_staging_path(self):
        r = assess_staging_recovery(_job(staging_path=None), [])
        assert not r.can_retry
        assert not r.staging_exists
        assert r.reason

    def test_staging_folder_gone(self, tmp_path):
        r = assess_staging_recovery(_job(staging_path=str(tmp_path / "gone")), [])
        assert not r.can_retry
        assert not r.staging_exists
        assert "no longer exists" in r.reason

    def test_staging_folder_without_files(self, tmp_path):
        r = assess_staging_recovery(_job(staging_path=str(tmp_path)), [])
        assert not r.can_retry
        assert r.staging_exists
        assert "no ripped files" in r.reason

    def test_flat_staging_with_files_is_retryable_without_manifest(self, tmp_path):
        a = _mkv(tmp_path / "title_t00.mkv")
        b = _mkv(tmp_path / "title_t01.mkv")
        r = assess_staging_recovery(_job(staging_path=str(tmp_path)), [])
        assert r.can_retry
        assert r.reason is None
        assert r.files == (str(a), str(b))
        # Every file is usable, so the import pipeline's own glob is enough.
        assert r.retry_manifest is None

    def test_partial_file_of_interrupted_track_is_excluded(self, tmp_path):
        done = _mkv(tmp_path / "title_t00.mkv")
        partial = _mkv(tmp_path / "title_t01.mkv")
        titles = [
            DiscTitle(
                job_id=1,
                title_index=0,
                state=TitleState.COMPLETED,
                output_filename=str(done),
            ),
            DiscTitle(
                job_id=1,
                title_index=1,
                state=TitleState.RIPPING,
                output_filename=str(partial),
            ),
        ]
        r = assess_staging_recovery(_job(staging_path=str(tmp_path)), titles)
        assert r.can_retry
        assert r.files == (str(done),)
        # The glob would pick the partial file back up, so an explicit manifest
        # has to pin the usable files.
        assert r.retry_manifest is not None
        assert r.retry_manifest["files"] == [str(done)]

    def test_only_partial_files_is_not_retryable(self, tmp_path):
        partial = _mkv(tmp_path / "title_t00.mkv")
        titles = [
            DiscTitle(
                job_id=1, title_index=0, state=TitleState.RIPPING, output_filename=str(partial)
            )
        ]
        r = assess_staging_recovery(_job(staging_path=str(tmp_path)), titles)
        assert not r.can_retry
        assert "no ripped files" in r.reason

    def test_import_manifest_is_reused_and_filtered(self, tmp_path):
        kept = _mkv(tmp_path / "Season 01" / "a.mkv")
        missing = tmp_path / "Season 01" / "b.mkv"
        manifest = {
            "root": str(tmp_path),
            "files": [str(kept), str(missing)],
            "picked_is_show": True,
            "picked_is_season": False,
        }
        job = _job(
            drive_id="import",
            staging_path=str(tmp_path / "Season 01"),
            import_manifest_json=json.dumps(manifest),
        )
        r = assess_staging_recovery(job, [])
        assert r.can_retry
        assert r.files == (str(kept),)
        assert r.retry_manifest == {**manifest, "files": [str(kept)]}


@pytest.fixture
async def client():
    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _persist(job: DiscJob, titles: list[DiscTitle] | None = None) -> int:
    async with _unit_session_factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for t in titles or []:
            t.job_id = job.id
            session.add(t)
        await session.commit()
        return job.id


class TestRecoveryEndpoint:
    async def test_reports_retryable_failed_job(self, client, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job_id = await _persist(_job(staging_path=str(tmp_path)))
        resp = await client.get(f"/api/jobs/{job_id}/recovery")
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_retry"] is True
        assert body["staging_exists"] is True
        assert body["file_count"] == 1
        assert body["staging_path"] == str(tmp_path)
        assert body["reason"] is None

    async def test_explains_missing_staging(self, client, tmp_path):
        job_id = await _persist(_job(staging_path=str(tmp_path / "gone")))
        body = (await client.get(f"/api/jobs/{job_id}/recovery")).json()
        assert body["can_retry"] is False
        assert "no longer exists" in body["reason"]

    async def test_unknown_job_404(self, client):
        assert (await client.get("/api/jobs/99999/recovery")).status_code == 404


class TestRetryFromStaging:
    async def test_refuses_non_failed_job(self, client, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job_id = await _persist(_job(state=JobState.REVIEW_NEEDED, staging_path=str(tmp_path)))
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
        ) as create:
            resp = await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        assert resp.status_code == 409
        create.assert_not_called()

    async def test_refuses_when_staging_gone(self, client, tmp_path):
        job_id = await _persist(_job(staging_path=str(tmp_path / "gone")))
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
        ) as create:
            resp = await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        assert resp.status_code == 409
        assert "no longer exists" in resp.json()["detail"]
        create.assert_not_called()

    async def test_disc_job_retries_as_staging_job_with_its_identity(self, client, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job_id = await _persist(
            _job(
                staging_path=str(tmp_path),
                content_type=ContentType.TV,
                detected_title="Arrested Development",
                detected_season=1,
                destination_mode="library",
            )
        )
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=StagingJobResult(job_id=4242),
        ) as create:
            resp = await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        assert resp.status_code == 200
        assert resp.json() == {"job_id": 4242, "retried_from": job_id}
        kwargs = create.await_args.kwargs
        assert kwargs["staging_path"] == str(tmp_path)
        assert kwargs["volume_label"] == "SHOW_S1D1"
        assert kwargs["content_type"] == "tv"
        assert kwargs["detected_title"] == "Arrested Development"
        assert kwargs["detected_season"] == 1
        assert kwargs["destination_mode"] == "library"
        # Not the physical drive (the retry must never touch it) and not
        # "import" (cleanup would then treat Engram's own staging as the
        # user's source folder and never reclaim it).
        assert kwargs["drive_id"] == "staging"
        assert kwargs["import_manifest"] is None
        assert kwargs.get("force", False) is False

    async def test_import_job_retry_stays_an_import(self, client, tmp_path):
        f = _mkv(tmp_path / "a.mkv")
        manifest = {"root": str(tmp_path), "files": [str(f)], "picked_is_show": False}
        job_id = await _persist(
            _job(
                drive_id="import",
                staging_path=str(tmp_path),
                destination_mode="in_place",
                import_manifest_json=json.dumps(manifest),
            )
        )
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=StagingJobResult(job_id=7),
        ) as create:
            resp = await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        assert resp.status_code == 200
        kwargs = create.await_args.kwargs
        assert kwargs["drive_id"] == "import"
        assert kwargs["destination_mode"] == "in_place"
        assert kwargs["import_manifest"] == manifest

    async def test_blocked_by_in_flight_job_is_409(self, client, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job_id = await _persist(_job(staging_path=str(tmp_path)))
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=StagingJobResult(
                block=ImportBlock.IN_FLIGHT, blocking_job_ids=(job_id + 1,)
            ),
        ):
            resp = await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        assert resp.status_code == 409
        assert str(job_id + 1) in resp.json()["detail"]

    async def test_failed_job_is_left_untouched(self, client, tmp_path):
        _mkv(tmp_path / "title_t00.mkv")
        job_id = await _persist(_job(staging_path=str(tmp_path), error_message="MakeMKV crashed"))
        with patch(
            "app.services.job_manager.job_manager.create_job_from_staging",
            new_callable=AsyncMock,
            return_value=StagingJobResult(job_id=job_id + 1),
        ):
            await client.post(f"/api/jobs/{job_id}/retry-from-staging")
        async with _unit_session_factory() as session:
            old = await session.get(DiscJob, job_id)
        assert old.state == JobState.FAILED
        assert old.error_message == "MakeMKV crashed"


class TestOpenStaging:
    async def test_opens_existing_folder(self, client, tmp_path):
        job_id = await _persist(_job(staging_path=str(tmp_path)))
        with patch("app.services.failed_job_recovery.open_in_file_manager") as opener:
            resp = await client.post(f"/api/jobs/{job_id}/open-staging")
        assert resp.status_code == 200
        opener.assert_called_once()
        assert str(opener.call_args.args[0]) == str(tmp_path)

    async def test_missing_folder_is_404(self, client, tmp_path):
        job_id = await _persist(_job(staging_path=str(tmp_path / "gone")))
        with patch("app.services.failed_job_recovery.open_in_file_manager") as opener:
            resp = await client.post(f"/api/jobs/{job_id}/open-staging")
        assert resp.status_code == 404
        opener.assert_not_called()

    async def test_lan_peer_is_refused(self, tmp_path):
        job_id = await _persist(_job(staging_path=str(tmp_path)))

        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        transport = ASGITransport(app=app, client=("172.18.0.14", 50000))
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                with patch("app.services.failed_job_recovery.open_in_file_manager") as opener:
                    resp = await ac.post(f"/api/jobs/{job_id}/open-staging")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 403
        opener.assert_not_called()
