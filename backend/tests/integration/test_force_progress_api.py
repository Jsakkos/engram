"""Integration tests for the force-progress + skip-track HTTP endpoints.

Verifies route wiring and status codes for POST /api/jobs/{id}/advance and
POST /api/jobs/{id}/titles/{tid}/skip. Scenarios route stuck tracks to REVIEW so
finalization never organizes into the real library.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _job_with_stuck_title(tmp_path, *, state=JobState.MATCHING):
    f = tmp_path / "disc_t00.mkv"
    f.write_bytes(b"x")
    async with async_session() as session:
        job = DiscJob(
            drive_id="Z:",
            volume_label="API_TEST",
            content_type=ContentType.TV,
            state=state,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=2700,
            state=TitleState.MATCHING,
            output_filename=str(f),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


@pytest.mark.asyncio
@pytest.mark.integration
class TestForceProgressApi:
    async def test_advance_unknown_job_404(self, client):
        resp = await client.post("/api/jobs/999999/advance")
        assert resp.status_code == 404

    async def test_advance_happy_path(self, client, tmp_path):
        job_id, _ = await _job_with_stuck_title(tmp_path)
        resp = await client.post(f"/api/jobs/{job_id}/advance")
        assert resp.status_code == 200
        assert resp.json()["status"] == "advanced"

        # Stuck MATCHING title (file present) routed to REVIEW → job in REVIEW_NEEDED.
        detail = await client.get(f"/api/jobs/{job_id}")
        assert detail.json()["state"] == "review_needed"

    async def test_advance_terminal_job_400(self, client, tmp_path):
        job_id, _ = await _job_with_stuck_title(tmp_path, state=JobState.COMPLETED)
        resp = await client.post(f"/api/jobs/{job_id}/advance")
        assert resp.status_code == 400

    async def test_skip_title_happy_path(self, client, tmp_path):
        job_id, title_id = await _job_with_stuck_title(tmp_path)
        resp = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/skip")
        assert resp.status_code == 200
        assert resp.json()["target"] == "review"

        async with async_session() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW

    async def test_skip_unknown_title_400(self, client, tmp_path):
        job_id, _ = await _job_with_stuck_title(tmp_path)
        resp = await client.post(f"/api/jobs/{job_id}/titles/888888/skip")
        assert resp.status_code == 400

    async def test_skip_invalid_target_400(self, client, tmp_path):
        job_id, title_id = await _job_with_stuck_title(tmp_path)
        resp = await client.post(
            f"/api/jobs/{job_id}/titles/{title_id}/skip", json={"target": "nope"}
        )
        assert resp.status_code == 400
