"""Integration tests for multi-drive scenarios.

Validates that two optical drives can operate independently:
- Concurrent ripping on different drives
- Cancel isolation (canceling one job doesn't affect another)
- Drive removal isolation
- Mixed content types (TV + movie on different drives)

Ref: https://github.com/Jsakkos/engram/issues/57
Ref: https://github.com/Jsakkos/engram/issues/64
Ref: https://github.com/Jsakkos/engram/issues/65
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


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


async def insert_disc(client, drive_id: str, volume_label: str, **kwargs):
    """Helper to insert a simulated disc on a specific drive."""
    payload = {
        "drive_id": drive_id,
        "volume_label": volume_label,
        "content_type": kwargs.get("content_type", "tv"),
        "detected_title": kwargs.get("detected_title", "Test Show"),
        "detected_season": kwargs.get("detected_season", 1),
        "simulate_ripping": kwargs.get("simulate_ripping", True),
        "rip_speed_multiplier": kwargs.get("rip_speed_multiplier", 100),
    }
    if "force_review_needed" in kwargs:
        payload["force_review_needed"] = kwargs["force_review_needed"]
    response = await client.post("/api/simulate/insert-disc", json=payload)
    assert response.status_code == 200, f"Insert failed: {response.text}"
    return response.json()["job_id"]


async def get_job(client, job_id: int) -> dict:
    """Helper to get a job's current state."""
    response = await client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    return response.json()


async def wait_for_state(client, job_id: int, states: set[str], timeout: float = 10.0) -> dict:
    """Poll until job reaches one of the target states, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await get_job(client, job_id)
        if job["state"] in states:
            return job
        await asyncio.sleep(0.2)
    raise TimeoutError(
        f"Job {job_id} did not reach {states} within {timeout}s (stuck at {job['state']})"
    )


@pytest.mark.asyncio
async def test_concurrent_ripping_on_two_drives(client):
    """Two drives ripping simultaneously should both complete independently."""
    job_a = await insert_disc(client, "E:", "SHOW_S01D1", detected_title="Show A")
    job_b = await insert_disc(client, "F:", "SHOW_S02D1", detected_title="Show B")

    assert job_a != job_b, "Jobs on different drives should have different IDs"

    # Both jobs should progress (at minimum past IDLE)
    job_a_data = await wait_for_state(
        client, job_a, {"ripping", "matching", "organizing", "completed"}, timeout=10.0
    )
    job_b_data = await wait_for_state(
        client, job_b, {"ripping", "matching", "organizing", "completed"}, timeout=10.0
    )

    # Verify they're on different drives
    assert job_a_data["drive_id"] == "E:"
    assert job_b_data["drive_id"] == "F:"

    # Neither should have failed
    assert job_a_data["state"] != "failed"
    assert job_b_data["state"] != "failed"


@pytest.mark.asyncio
async def test_cancel_one_drive_does_not_affect_other(client):
    """Canceling a job on drive E: must not affect a job ripping on drive F:."""
    job_a = await insert_disc(client, "E:", "CANCEL_ME", detected_title="Cancel Show")
    job_b = await insert_disc(client, "F:", "KEEP_GOING", detected_title="Keep Show")

    # Wait for both to start ripping
    await wait_for_state(client, job_a, {"ripping", "matching", "organizing", "completed"})
    await wait_for_state(client, job_b, {"ripping", "matching", "organizing", "completed"})

    # Cancel job A
    cancel_response = await client.post(f"/api/jobs/{job_a}/cancel")
    assert cancel_response.status_code == 200

    # Verify job A is failed/cancelled
    job_a_data = await get_job(client, job_a)
    assert job_a_data["state"] == "failed"
    assert "Cancelled" in (job_a_data.get("error_message") or "")

    # Give a moment for any cross-contamination to manifest
    await asyncio.sleep(0.5)

    # Job B must NOT be failed — it should still be running or completed
    job_b_data = await get_job(client, job_b)
    assert job_b_data["state"] != "failed", (
        f"Job B on drive F: was incorrectly affected by canceling Job A on drive E:. "
        f"State: {job_b_data['state']}, Error: {job_b_data.get('error_message')}"
    )


@pytest.mark.asyncio
async def test_drive_removal_does_not_affect_other_drive(client):
    """Simulating disc removal on drive E: must not affect drive F:."""
    await insert_disc(
        client, "E:", "REMOVE_ME", detected_title="Remove Show", simulate_ripping=False
    )
    job_b = await insert_disc(client, "F:", "KEEP_ME", detected_title="Keep Show")

    # Wait for job B to start ripping
    await wait_for_state(client, job_b, {"ripping", "matching", "organizing", "completed"})

    # Simulate disc removal on drive E:
    remove_response = await client.post("/api/simulate/remove-disc?drive_id=E%3A")
    assert remove_response.status_code == 200

    await asyncio.sleep(0.5)

    # Job B must still be running or completed, NOT failed
    job_b_data = await get_job(client, job_b)
    assert job_b_data["state"] != "failed", (
        f"Job B on drive F: was incorrectly affected by disc removal on drive E:. "
        f"State: {job_b_data['state']}, Error: {job_b_data.get('error_message')}"
    )


@pytest.mark.asyncio
async def test_mixed_content_types_on_different_drives(client):
    """TV on drive E: and movie on drive F: should both work independently."""
    job_tv = await insert_disc(
        client,
        "E:",
        "TV_SHOW_S01D1",
        content_type="tv",
        detected_title="Some TV Show",
        detected_season=1,
    )
    job_movie = await insert_disc(
        client,
        "F:",
        "INCEPTION_2010",
        content_type="movie",
        detected_title="Inception",
    )

    # Both should progress
    tv_data = await wait_for_state(
        client, job_tv, {"ripping", "matching", "organizing", "completed"}, timeout=10.0
    )
    movie_data = await wait_for_state(
        client, job_movie, {"ripping", "matching", "organizing", "completed"}, timeout=10.0
    )

    assert tv_data["content_type"] == "tv"
    assert movie_data["content_type"] == "movie"
    assert tv_data["state"] != "failed"
    assert movie_data["state"] != "failed"


@pytest.mark.asyncio
async def test_dual_identification_independent(client):
    """Two discs inserted near-simultaneously get identified independently."""
    job_a = await insert_disc(
        client,
        "E:",
        "DISC_A",
        detected_title="Show Alpha",
        simulate_ripping=False,
    )
    job_b = await insert_disc(
        client,
        "F:",
        "DISC_B",
        detected_title="Show Beta",
        simulate_ripping=False,
    )

    # Both should exist with correct metadata
    data_a = await get_job(client, job_a)
    data_b = await get_job(client, job_b)

    assert data_a["volume_label"] == "DISC_A"
    assert data_b["volume_label"] == "DISC_B"
    assert data_a["detected_title"] == "Show Alpha"
    assert data_b["detected_title"] == "Show Beta"
    assert data_a["drive_id"] == "E:"
    assert data_b["drive_id"] == "F:"
