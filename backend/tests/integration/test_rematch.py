"""Integration tests for rematch and reassign endpoints.

Tests POST /api/jobs/{id}/titles/{tid}/rematch,
POST /api/jobs/{id}/rematch, and POST /api/jobs/{id}/titles/{tid}/reassign.
"""

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
async def job_with_matched_title():
    """Create a review-needed job with a DiscDB-matched title."""
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST_SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Test Show",
            detected_season=1,
            content_hash="ABCDEF1234567890ABCDEF1234567890",
            staging_path="/tmp/staging/test",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=2400,
            file_size_bytes=1024 * 1024 * 1024,
            chapter_count=8,
            state=TitleState.MATCHED,
            matched_episode="S01E01",
            match_confidence=0.99,
            match_details=json.dumps(
                {"source": "discdb", "episode_title": "Pilot", "matched_episode": "S01E01"}
            ),
            match_source="discdb",
            discdb_match_details=json.dumps(
                {"source": "discdb", "episode_title": "Pilot", "matched_episode": "S01E01"}
            ),
            output_filename="/tmp/staging/test/title_t00.mkv",
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)

        return job, title


@pytest.mark.asyncio
async def test_rematch_single_title_returns_200(client, job_with_matched_title):
    """POST /api/jobs/{id}/titles/{tid}/rematch with discdb source should return 200."""
    job, title = job_with_matched_title

    resp = await client.post(
        f"/api/jobs/{job.id}/titles/{title.id}/rematch",
        json={"source_preference": "discdb"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rematching"
    assert data["title_id"] == title.id


@pytest.mark.asyncio
async def test_bulk_rematch_returns_200(client, job_with_matched_title):
    """POST /api/jobs/{id}/rematch should return 200."""
    job, _ = job_with_matched_title

    resp = await client.post(
        f"/api/jobs/{job.id}/rematch",
        json={"source_preference": "discdb"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rematching"
    assert data["job_id"] == job.id


@pytest.mark.asyncio
async def test_reassign_episode_returns_200(client, job_with_matched_title):
    """POST /api/jobs/{id}/titles/{tid}/reassign should return 200 and update DB."""
    job, title = job_with_matched_title

    resp = await client.post(
        f"/api/jobs/{job.id}/titles/{title.id}/reassign",
        json={"episode_code": "S01E05"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reassigned"
    assert data["title_id"] == title.id

    # Verify in DB
    async with async_session() as session:
        reloaded = await session.get(DiscTitle, title.id)
        assert reloaded.matched_episode == "S01E05"
        assert reloaded.match_confidence == 1.0
        assert reloaded.match_source == "user"


@pytest.mark.asyncio
async def test_rematch_nonexistent_title_returns_404(client, job_with_matched_title):
    """POST /api/jobs/{id}/titles/99999/rematch should return 404."""
    job, _ = job_with_matched_title

    resp = await client.post(
        f"/api/jobs/{job.id}/titles/99999/rematch",
        json={"source_preference": "discdb"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reassign_updates_match_source_to_user(client, job_with_matched_title):
    """After reassign, job detail should show match_source='user' and confidence=1.0."""
    job, title = job_with_matched_title

    resp = await client.post(
        f"/api/jobs/{job.id}/titles/{title.id}/reassign",
        json={"episode_code": "S01E05", "edition": "Extended"},
    )
    assert resp.status_code == 200

    detail_resp = await client.get(f"/api/jobs/{job.id}/detail")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    reassigned = next(t for t in detail["titles"] if t["id"] == title.id)
    assert reassigned["match_source"] == "user"
    assert reassigned["match_confidence"] == 1.0
    assert reassigned["matched_episode"] == "S01E05"
