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
            match_details=json.dumps({"source": "subtitle"}),
        )
        session.add(title)
        await session.commit()

        return job


@pytest.fixture
async def second_completed_job():
    """Create a second completed job for release group testing."""
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="BAND_OF_BROTHERS_S1D2",
            content_type=ContentType.TV,
            state=JobState.COMPLETED,
            content_hash="AABBCCDD11223344",
            detected_title="Band of Brothers",
            detected_season=1,
            tmdb_id=4613,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=3600,
            file_size_bytes=12000000000,
            chapter_count=10,
            state=TitleState.COMPLETED,
            matched_episode="S01E03",
            match_confidence=0.95,
            match_details=json.dumps({"source": "subtitle"}),
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
    assert job["release_group_id"] is None
    assert job["submitted_at"] is None


@pytest.mark.asyncio
async def test_stats_counts(client, completed_job):
    response = await client.get("/api/contributions/stats")
    assert response.status_code == 200

    stats = response.json()
    assert stats["pending"] >= 1
    assert isinstance(stats["exported"], int)
    assert isinstance(stats["skipped"], int)
    assert isinstance(stats["submitted"], int)


@pytest.mark.asyncio
async def test_manual_export(client, completed_job, tmp_path):
    # Set export path
    await client.put("/api/config", json={"discdb_export_path": str(tmp_path)})

    response = await client.post(f"/api/contributions/{completed_job.id}/export")
    assert response.status_code == 200
    assert response.json()["status"] == "exported"

    # Verify file was created with v1.1 schema
    export_dir = tmp_path / "D7CAB58DAC87C58C46FDA35A33759839"
    assert export_dir.exists()
    data = json.loads((export_dir / "disc_data.json").read_text())
    assert data["export_version"] == "1.1"
    assert data["disc"]["content_hash"] == "D7CAB58DAC87C58C46FDA35A33759839"
    assert len(data["titles"]) == 1

    # Verify v1.1 schema: season/episode instead of matched_episode
    t = data["titles"][0]
    assert t["season"] == 1
    assert t["episode"] == 1
    assert "matched_episode" not in t
    assert t["source_filename"] == "00001.m2ts"

    # Verify scan_log is flat string, not nested dict
    assert "scan_log" in data
    assert "makemkv_logs" not in data


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


@pytest.mark.asyncio
async def test_submit_without_api_key_attempts_submission(client, completed_job, tmp_path):
    """Submit endpoint should attempt submission even without API key configured."""
    await client.put("/api/config", json={"discdb_export_path": str(tmp_path)})

    # Export first so there's data to submit
    await client.post(f"/api/contributions/{completed_job.id}/export")

    # Submit without API key — should attempt but fail due to network (no real server)
    response = await client.post(f"/api/contributions/{completed_job.id}/submit")
    assert response.status_code == 200
    data = response.json()
    # Will fail because there's no real TheDiscDB server in tests, but it should attempt
    assert "success" in data
    assert "error" in data


@pytest.mark.asyncio
async def test_create_release_group(client, completed_job, second_completed_job):
    """Creating a release group assigns the same UUID to multiple jobs."""
    response = await client.post(
        "/api/contributions/release-group",
        json={"job_ids": [completed_job.id, second_completed_job.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "release_group_id" in data
    assert set(data["job_ids"]) == {completed_job.id, second_completed_job.id}

    # Verify both jobs now have the release group
    list_response = await client.get("/api/contributions")
    jobs = list_response.json()
    group_id = data["release_group_id"]
    grouped = [j for j in jobs if j["release_group_id"] == group_id]
    assert len(grouped) == 2


@pytest.mark.asyncio
async def test_release_group_requires_at_least_two(client, completed_job):
    """Cannot create a release group with a single job."""
    response = await client.post(
        "/api/contributions/release-group",
        json={"job_ids": [completed_job.id]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_ungroup_job(client, completed_job, second_completed_job):
    """Removing a job from its release group."""
    # First create a group
    group_resp = await client.post(
        "/api/contributions/release-group",
        json={"job_ids": [completed_job.id, second_completed_job.id]},
    )
    assert group_resp.status_code == 200

    # Ungroup the first job
    response = await client.put(
        f"/api/contributions/{completed_job.id}/release-group",
        json={"release_group_id": None},
    )
    assert response.status_code == 200

    # Verify it's ungrouped
    list_response = await client.get("/api/contributions")
    jobs = list_response.json()
    job = next(j for j in jobs if j["id"] == completed_job.id)
    assert job["release_group_id"] is None


# --- Wave 3: Batch Submit Tests ---


@pytest.mark.asyncio
async def test_batch_submit_release_group(client, completed_job, second_completed_job, tmp_path):
    """Batch-submit all jobs in a release group."""
    # Set export path (needed for submit_job to generate export)
    await client.put("/api/config", json={"discdb_export_path": str(tmp_path)})

    # Create a release group
    group_resp = await client.post(
        "/api/contributions/release-group",
        json={"job_ids": [completed_job.id, second_completed_job.id]},
    )
    assert group_resp.status_code == 200
    group_id = group_resp.json()["release_group_id"]

    # Batch submit — will fail due to no real TheDiscDB server, but endpoint should work
    response = await client.post(f"/api/contributions/release-group/{group_id}/submit")
    assert response.status_code == 200

    data = response.json()
    assert "submitted" in data
    assert "failed" in data
    assert "results" in data
    assert len(data["results"]) == 2
    # Each result should have job_id, success, error fields
    for result in data["results"]:
        assert "job_id" in result
        assert "success" in result


@pytest.mark.asyncio
async def test_batch_submit_nonexistent_group(client):
    """Batch submit with a nonexistent release group returns 404."""
    response = await client.post("/api/contributions/release-group/nonexistent-uuid-12345/submit")
    assert response.status_code == 404
