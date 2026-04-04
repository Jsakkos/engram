"""Integration tests for TheDiscDB contribution pipeline."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def completed_job():
    """Create a completed job with titles for contribution testing."""
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="BAND_OF_BROTHERS_S1D1",
            content_type=ContentType.TV,
            state=JobState.COMPLETED,
            content_hash="D7CAB58DAC87C58C46FDA35A33759839",
            detected_title="Band of Brothers",
            detected_season=1,
            tmdb_id=4613,
            classification_source="discdb_hash_match",
            classification_confidence=0.98,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=4394,
            file_size_bytes=18405949440,
            chapter_count=12,
            source_filename="00001.m2ts",
            segment_count=1,
            segment_map="1",
            state=TitleState.COMPLETED,
            matched_episode="S01E01",
            match_confidence=0.99,
            match_details=json.dumps({"source": "discdb"}),
        )
        session.add(title)
        await session.commit()

        return job


@pytest.mark.asyncio
async def test_list_contributions(client, completed_job):
    response = await client.get("/api/contributions")
    assert response.status_code == 200

    jobs = response.json()
    assert len(jobs) >= 1
    job = next(j for j in jobs if j["volume_label"] == "BAND_OF_BROTHERS_S1D1")
    assert job["export_status"] == "pending"
    assert job["content_hash"] == "D7CAB58DAC87C58C46FDA35A33759839"


@pytest.mark.asyncio
async def test_stats_counts(client, completed_job):
    response = await client.get("/api/contributions/stats")
    assert response.status_code == 200

    stats = response.json()
    assert stats["pending"] >= 1
    assert isinstance(stats["exported"], int)
    assert isinstance(stats["skipped"], int)


@pytest.mark.asyncio
async def test_manual_export(client, completed_job, tmp_path):
    # Set export path
    await client.put("/api/config", json={"discdb_export_path": str(tmp_path)})

    response = await client.post(f"/api/contributions/{completed_job.id}/export")
    assert response.status_code == 200
    assert response.json()["status"] == "exported"

    # Verify file was created
    export_dir = tmp_path / "D7CAB58DAC87C58C46FDA35A33759839"
    assert export_dir.exists()
    data = json.loads((export_dir / "disc_data.json").read_text())
    assert data["disc"]["content_hash"] == "D7CAB58DAC87C58C46FDA35A33759839"
    assert len(data["titles"]) == 1
    assert data["titles"][0]["source_filename"] == "00001.m2ts"


@pytest.mark.asyncio
async def test_export_nonexistent_job(client):
    response = await client.post("/api/contributions/99999/export")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_skip_job(client, completed_job):
    response = await client.post(f"/api/contributions/{completed_job.id}/skip")
    assert response.status_code == 200

    # Verify status changed
    list_response = await client.get("/api/contributions")
    jobs = list_response.json()
    skipped = next(j for j in jobs if j["id"] == completed_job.id)
    assert skipped["export_status"] == "skipped"


@pytest.mark.asyncio
async def test_enhance_with_upc(client, completed_job, tmp_path):
    await client.put("/api/config", json={"discdb_export_path": str(tmp_path)})

    response = await client.post(
        f"/api/contributions/{completed_job.id}/enhance",
        json={"upc_code": "883929123456"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "enhanced"

    # Verify UPC in export
    export_dir = tmp_path / "D7CAB58DAC87C58C46FDA35A33759839"
    data = json.loads((export_dir / "disc_data.json").read_text())
    assert data["upc"] == "883929123456"
    assert data["contribution_tier"] == 3
