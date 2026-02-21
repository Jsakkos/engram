"""Integration tests for simulation endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    # Clean all data before each test
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


@pytest.mark.asyncio
async def test_simulate_insert_disc_creates_job(client):
    """Test that simulating disc insertion creates a DB record."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "drive_id": "E:",
            "volume_label": "TEST_DISC",
            "content_type": "tv",
            "detected_title": "Test Show",
            "detected_season": 1,
            "simulate_ripping": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "simulated"
    assert "job_id" in data

    # Verify job exists
    job_response = await client.get(f"/api/jobs/{data['job_id']}")
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["volume_label"] == "TEST_DISC"
    assert job["content_type"] == "tv"
    assert job["detected_title"] == "Test Show"


@pytest.mark.asyncio
async def test_simulate_insert_disc_creates_titles(client):
    """Test that simulated disc creates title records."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "TEST_TV",
            "content_type": "tv",
            "simulate_ripping": False,
            "titles": [
                {"duration_seconds": 1320, "file_size_bytes": 1000000000},
                {"duration_seconds": 1350, "file_size_bytes": 1100000000},
            ],
        },
    )
    data = response.json()
    job_id = data["job_id"]

    # Verify titles exist
    titles_response = await client.get(f"/api/jobs/{job_id}/titles")
    assert titles_response.status_code == 200
    titles = titles_response.json()
    assert len(titles) == 2
    assert titles[0]["duration_seconds"] == 1320
    assert titles[1]["duration_seconds"] == 1350


@pytest.mark.asyncio
async def test_simulate_advance_job(client):
    """Test manually advancing a job state."""
    # Create a job
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "ADVANCE_TEST",
            "content_type": "movie",
            "simulate_ripping": False,
        },
    )
    job_id = response.json()["job_id"]

    # Advance the job
    advance_response = await client.post(f"/api/simulate/advance-job/{job_id}")
    assert advance_response.status_code == 200
    data = advance_response.json()
    assert data["status"] == "advanced"


@pytest.mark.asyncio
async def test_simulate_remove_disc(client):
    """Test simulating disc removal."""
    response = await client.post(
        "/api/simulate/remove-disc?drive_id=E%3A",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "removed"


@pytest.mark.asyncio
async def test_simulation_disabled_in_production(client):
    """Test that simulation endpoints are blocked when DEBUG=false."""
    with patch("app.api.routes.settings") as mock_settings:
        mock_settings.debug = False
        response = await client.post(
            "/api/simulate/insert-disc",
            json={"volume_label": "BLOCKED"},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_clear_completed_jobs(client):
    """Test clearing completed jobs."""
    # Create and complete a simulated job
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "CLEAR_TEST",
            "content_type": "movie",
            "simulate_ripping": False,
        },
    )
    job_id = response.json()["job_id"]

    # Advance to completed
    for _ in range(5):
        try:
            await client.post(f"/api/simulate/advance-job/{job_id}")
        except Exception:
            break

    # Clear completed
    clear_response = await client.delete("/api/jobs/completed")
    assert clear_response.status_code == 200


@pytest.mark.asyncio
async def test_on_title_ripped_transitions_to_ripping(client):
    """Test that _on_title_ripped correctly transitions a title to RIPPING state.

    The title stays RIPPING (file detected but may still be written) until
    _match_single_file verifies the file is ready and transitions to MATCHING.
    This exercises the core callback chain used by the real disc ripping flow:
    extractor callback → _on_title_ripped → DB update + WebSocket broadcast.
    """
    from pathlib import Path
    from unittest.mock import patch

    from app.database import async_session as db_session
    from app.models.disc_job import DiscTitle, TitleState
    from app.services.job_manager import job_manager

    # 1. Create a job with titles via simulation (no ripping)
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "drive_id": "E:",
            "volume_label": "CALLBACK_TEST",
            "content_type": "tv",
            "detected_title": "Callback Show",
            "detected_season": 1,
            "simulate_ripping": False,
            "titles": [
                {"duration_seconds": 1320, "file_size_bytes": 500_000_000},
                {"duration_seconds": 1350, "file_size_bytes": 510_000_000},
                {"duration_seconds": 1380, "file_size_bytes": 520_000_000},
            ],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    # 2. Fetch titles to build sorted_titles (mimics _run_ripping)
    from sqlmodel import select

    async with db_session() as session:
        result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
        disc_titles = result.scalars().all()
        sorted_titles = sorted(disc_titles, key=lambda t: t.title_index)

    assert len(sorted_titles) == 3

    # 3. Mock WebSocket broadcast and episode matching
    with (
        patch(
            "app.api.websocket.manager.broadcast_title_update", new_callable=AsyncMock
        ) as mock_broadcast,
        patch.object(job_manager, "_match_single_file", new_callable=AsyncMock) as mock_match,
    ):
        # Simulate MakeMKV completing title 1 (filename pattern: B1_t01.mkv)
        fake_path = Path("/staging/B1_t01.mkv")
        await job_manager._on_title_ripped(job_id, 1, fake_path, sorted_titles)

        # 4. Verify DB was updated — title should be RIPPING (file detected,
        # but not yet verified as fully written, so matching hasn't started)
        async with db_session() as session:
            title = await session.get(DiscTitle, sorted_titles[1].id)
            assert title is not None
            assert title.state == TitleState.RIPPING, f"Expected RIPPING, got {title.state}"
            assert title.output_filename == str(fake_path), (
                f"Expected {fake_path}, got {title.output_filename}"
            )

        # 5. Verify WebSocket broadcast was called
        mock_broadcast.assert_called_once()
        call_args = mock_broadcast.call_args
        assert call_args[0][0] == job_id  # job_id
        assert call_args[0][1] == sorted_titles[1].id  # title_id
        assert call_args[0][2] == "ripping"  # state

        # 6. Verify matching was started (for TV content)
        mock_match.assert_called_once_with(job_id, sorted_titles[1].id, fake_path)


@pytest.mark.asyncio
async def test_on_title_ripped_maps_by_filename_index(client):
    """Test that _on_title_ripped correctly maps MakeMKV filenames to title indices.

    Verifies patterns like B1_t03.mkv → title_index=3.
    """
    from pathlib import Path
    from unittest.mock import patch

    from app.database import async_session as db_session
    from app.models.disc_job import DiscTitle, TitleState
    from app.services.job_manager import job_manager

    # Create a job with 5 titles (indices 0-4)
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "drive_id": "E:",
            "volume_label": "INDEX_MAP_TEST",
            "content_type": "tv",
            "detected_title": "Index Test",
            "detected_season": 1,
            "simulate_ripping": False,
            "titles": [
                {"duration_seconds": 1200 + i * 60, "file_size_bytes": 500_000_000}
                for i in range(5)
            ],
        },
    )
    job_id = response.json()["job_id"]

    from sqlmodel import select

    async with db_session() as session:
        result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
        sorted_titles = sorted(result.scalars().all(), key=lambda t: t.title_index)

    with (
        patch("app.api.websocket.manager.broadcast_title_update", new_callable=AsyncMock),
        patch.object(job_manager, "_match_single_file", new_callable=AsyncMock),
    ):
        # Rip title index 3 (filename: title_t03.mkv)
        fake_path = Path("/staging/title_t03.mkv")
        await job_manager._on_title_ripped(job_id, 99, fake_path, sorted_titles)

        # Verify title_index=3 was updated (not rip_index 99)
        async with db_session() as session:
            title_3 = await session.get(DiscTitle, sorted_titles[3].id)
            assert title_3.state == TitleState.RIPPING
            assert title_3.output_filename == str(fake_path)

            # Other titles should still be pending
            title_0 = await session.get(DiscTitle, sorted_titles[0].id)
            assert title_0.state == TitleState.PENDING
