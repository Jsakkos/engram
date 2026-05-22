"""Integration tests for rematch and reassign endpoints.

Tests POST /api/jobs/{id}/titles/{tid}/rematch,
POST /api/jobs/{id}/rematch, and POST /api/jobs/{id}/titles/{tid}/reassign.
"""

import asyncio
import json
from unittest.mock import AsyncMock

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


@pytest.fixture
async def engram_review_job(tmp_path):
    """Create a REVIEW_NEEDED TV job whose title was matched via engram (audio).

    No DiscDB details, subtitles already completed — the realistic state when a
    disc lands in review because one episode could not be auto-matched.
    """
    ripped_file = tmp_path / "B1_t00.mkv"
    ripped_file.write_bytes(b"fake mkv")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="ARRESTED_DEVELOPMENT_S1D2",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Arrested Development",
            detected_season=1,
            subtitle_status="completed",
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1300,
            file_size_bytes=1024 * 1024 * 1024,
            chapter_count=8,
            state=TitleState.MATCHED,
            matched_episode="S01E01",
            match_confidence=0.9,
            match_source="engram",
            is_selected=True,
            output_filename=str(ripped_file),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)

        return job, title


@pytest.mark.asyncio
async def test_bulk_rematch_transitions_job_to_matching(client, engram_review_job, monkeypatch):
    """Re-match all must move the job out of REVIEW_NEEDED into MATCHING.

    Otherwise the review page stays mounted and never reflects the re-matching
    that runs in the background — the reported bug.
    """
    job, title = engram_review_job

    from app.services.job_manager import job_manager

    monkeypatch.setattr(job_manager._matching, "match_single_file", AsyncMock())

    resp = await client.post(
        f"/api/jobs/{job.id}/rematch",
        json={"source_preference": "engram"},
    )
    assert resp.status_code == 200

    async with async_session() as session:
        reloaded_job = await session.get(DiscJob, job.id)
        reloaded_title = await session.get(DiscTitle, title.id)
        assert reloaded_job.state == JobState.MATCHING
        assert reloaded_title.matched_episode is None


@pytest.mark.asyncio
async def test_bulk_rematch_dispatches_matching(client, engram_review_job, monkeypatch):
    """Re-match all must actually dispatch matching for each ripped title."""
    job, title = engram_review_job

    from app.services.job_manager import job_manager

    mock_match = AsyncMock()
    monkeypatch.setattr(job_manager._matching, "match_single_file", mock_match)

    resp = await client.post(
        f"/api/jobs/{job.id}/rematch",
        json={"source_preference": "engram"},
    )
    assert resp.status_code == 200

    # Background tasks are fire-and-forget; let the event loop run them.
    await asyncio.sleep(0.1)

    assert mock_match.await_count == 1
    dispatched_job_id, dispatched_title_id, _path = mock_match.await_args.args
    assert dispatched_job_id == job.id
    assert dispatched_title_id == title.id


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
