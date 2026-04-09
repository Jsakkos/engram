"""Integration tests for DiscDB flagging endpoint and match_source fields.

Tests the POST /api/jobs/{job_id}/flag-discdb endpoint and verifies
new fields (match_source, discdb_match_details, discdb_flagged, discdb_flag_reason)
appear in API responses.
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
async def job_with_discdb_title():
    """Create a completed job with a DiscDB-matched title."""
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST_SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.COMPLETED,
            detected_title="Test Show",
            detected_season=1,
            content_hash="ABCDEF1234567890ABCDEF1234567890",
            classification_source="discdb_hash_match",
            classification_confidence=0.98,
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
            state=TitleState.COMPLETED,
            matched_episode="S01E01",
            match_confidence=0.99,
            match_details=json.dumps({"source": "discdb", "episode_title": "Pilot"}),
            match_source="discdb",
            discdb_match_details=json.dumps({"source": "discdb", "episode_title": "Pilot"}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)

        return job, title


@pytest.mark.asyncio
async def test_flag_discdb_returns_200_and_persists(client, job_with_discdb_title):
    """POST /api/jobs/{id}/flag-discdb should return 200 and persist the flag."""
    job, title = job_with_discdb_title

    resp = await client.post(
        f"/api/jobs/{job.id}/flag-discdb",
        json={"title_id": title.id, "reason": "Wrong episode assignment"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "flagged"
    assert data["title_id"] == title.id

    # Verify persisted in DB
    async with async_session() as session:
        reloaded = await session.get(DiscTitle, title.id)
        assert reloaded.discdb_flagged is True
        assert reloaded.discdb_flag_reason == "Wrong episode assignment"


@pytest.mark.asyncio
async def test_flag_nonexistent_title_returns_404(client, job_with_discdb_title):
    """POST /api/jobs/{id}/flag-discdb with nonexistent title_id should return 404."""
    job, _ = job_with_discdb_title

    resp = await client.post(
        f"/api/jobs/{job.id}/flag-discdb",
        json={"title_id": 99999, "reason": "Bad match"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_flag_persists_in_job_detail(client, job_with_discdb_title):
    """After flagging, GET /api/jobs/{id}/detail should show the flag on the title."""
    job, title = job_with_discdb_title

    # Flag the title
    resp = await client.post(
        f"/api/jobs/{job.id}/flag-discdb",
        json={"title_id": title.id, "reason": "Should be S01E02"},
    )
    assert resp.status_code == 200

    # Check job detail
    detail_resp = await client.get(f"/api/jobs/{job.id}/detail")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    flagged_title = next(t for t in detail["titles"] if t["id"] == title.id)
    assert flagged_title["discdb_flagged"] is True
    assert flagged_title["discdb_flag_reason"] == "Should be S01E02"


@pytest.mark.asyncio
async def test_job_detail_includes_new_fields(client, job_with_discdb_title):
    """GET /api/jobs/{id}/detail should include match_source and discdb fields."""
    job, title = job_with_discdb_title

    resp = await client.get(f"/api/jobs/{job.id}/detail")
    assert resp.status_code == 200
    detail = resp.json()

    assert len(detail["titles"]) >= 1
    t = detail["titles"][0]

    # New fields should be present
    assert "match_source" in t
    assert "discdb_match_details" in t
    assert "discdb_flagged" in t
    assert "discdb_flag_reason" in t

    # Check values from fixture
    assert t["match_source"] == "discdb"
    assert t["discdb_match_details"] is not None
    assert t["discdb_flagged"] is False  # Not flagged yet
