"""Integration tests for error recovery scenarios.

Tests job failure handling, cancellation, and error state queries.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import AppConfig


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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def test_config():
    async with async_session() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key",
            staging_path="/tmp/staging",
            library_movies_path="/media/movies",
            library_tv_path="/media/tv",
            transcoding_enabled=False,
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test",
            max_concurrent_matches=2,
            ffmpeg_path="/usr/bin/ffmpeg",
            conflict_resolution_default="rename",
            ripping_file_poll_interval=0.5,
            ripping_stability_checks=2,
            ripping_file_ready_timeout=60.0,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


@pytest.mark.asyncio
@pytest.mark.integration
class TestCancelDuringWorkflow:
    """Test cancellation at various workflow stages."""

    async def test_cancel_during_ripping(self, client, test_config):
        """Cancel mid-workflow → job should go to FAILED with cancel message."""
        # Insert disc with simulated ripping
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "CANCEL_TEST_S1D1",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait a moment for job to start processing
        await asyncio.sleep(1)

        # Cancel the job
        response = await client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200

        # Verify job is in failed state
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        assert job["state"] == "failed"
        assert "cancel" in (job.get("error_message") or "").lower()


@pytest.mark.asyncio
@pytest.mark.integration
class TestFailedJobQueries:
    """Test that failed jobs remain queryable."""

    async def test_failed_job_queryable(self, client, test_config):
        """Failed jobs should still be returned by GET /api/jobs."""
        # Insert and cancel to create a failed job
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "FAIL_QUERY_TEST",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        await asyncio.sleep(1)
        await client.post(f"/api/jobs/{job_id}/cancel")

        # Verify it appears in the job list
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        jobs = response.json()
        job_ids = [j["id"] for j in jobs]
        assert job_id in job_ids

        # Verify individual lookup works
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["state"] == "failed"

    async def test_failed_job_preserves_error(self, client, test_config):
        """error_message field should be populated on failed jobs."""
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "ERROR_MSG_TEST",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        await asyncio.sleep(1)
        await client.post(f"/api/jobs/{job_id}/cancel")

        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        assert job["error_message"] is not None
        assert len(job["error_message"]) > 0


@pytest.mark.asyncio
@pytest.mark.integration
class TestJobCleanup:
    """Test clearing and deleting completed/failed jobs."""

    async def test_clear_single_completed_job(self, client, test_config):
        """DELETE /api/jobs/{id} should remove a single job."""
        # Create a job
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "CLEAR_TEST",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait then cancel to get a terminal state
        await asyncio.sleep(1)
        await client.post(f"/api/jobs/{job_id}/cancel")

        # Verify it exists
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200

        # Delete it
        response = await client.delete(f"/api/jobs/{job_id}")
        assert response.status_code == 200

        # Verify it's gone
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 404
