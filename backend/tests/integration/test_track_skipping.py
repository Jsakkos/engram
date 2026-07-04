import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _clean_db():
    await init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


async def _seed():
    async with async_session() as s:
        job = DiscJob(
            drive_id="Z:",
            volume_label="T",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            staging_path="/tmp/x",
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        # Seed two titles: skipping one must not auto-complete the job, so the
        # un-skip round-trip stays exercisable against a still-running job.
        t = DiscTitle(
            job_id=job.id,
            title_index=4,
            duration_seconds=300,
            state=TitleState.PENDING,
            is_selected=True,
        )
        other = DiscTitle(
            job_id=job.id,
            title_index=5,
            duration_seconds=300,
            state=TitleState.PENDING,
            is_selected=True,
        )
        s.add(t)
        s.add(other)
        await s.commit()
        await s.refresh(t)
        return job.id, t.id


async def test_skip_and_unskip_endpoints(client):
    job_id, title_id = await _seed()

    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/skip-rip")
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    async with async_session() as s:
        assert (await s.get(DiscTitle, title_id)).state == TitleState.SKIPPED

    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/unskip-rip")
    assert r.status_code == 200
    async with async_session() as s:
        assert (await s.get(DiscTitle, title_id)).state == TitleState.PENDING


async def test_skip_rejects_terminal_job(client):
    job_id, title_id = await _seed()
    async with async_session() as s:
        job = await s.get(DiscJob, job_id)
        job.state = JobState.COMPLETED
        await s.commit()
    r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/skip-rip")
    assert r.status_code == 400
