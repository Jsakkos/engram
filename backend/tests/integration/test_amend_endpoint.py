import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_amend_rejects_non_completed_job(client):
    async with async_session() as session:
        job = DiscJob(
            volume_label="X",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            drive_id="E:",
        )
        session.add(job)
        await session.commit()
        title = DiscTitle(job_id=job.id, title_index=0, duration_seconds=1, state=TitleState.REVIEW)
        session.add(title)
        await session.commit()
        job_id, title_id = job.id, title.id

    resp = await client.post(
        f"/api/jobs/{job_id}/titles/{title_id}/amend",
        json={"target": {"kind": "extra"}},
    )
    assert resp.status_code == 409
