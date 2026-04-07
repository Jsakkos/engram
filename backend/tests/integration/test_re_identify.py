"""Integration tests for disc re-identification endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.job_manager import job_manager


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


async def _create_review_needed_job(content_type="movie", title="Wrong Title"):
    """Create a job in REVIEW_NEEDED state for testing."""
    async with async_session() as session:
        job = DiscJob(
            volume_label="TEST_DISC",
            drive_id="E:",
            state=JobState.REVIEW_NEEDED,
            content_type=ContentType(content_type),
            detected_title=title,
            needs_review=True,
            review_reason="TMDB suggests movie but heuristics suggest tv.",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.asyncio
async def test_re_identify_changes_content_type(client):
    """Re-identify should update content type and detected title."""
    job_id = await _create_review_needed_job(content_type="movie", title="Thunderbird")

    with patch.object(job_manager, "re_identify_job", new_callable=AsyncMock) as mock_re_id:
        response = await client.post(
            f"/api/jobs/{job_id}/re-identify",
            json={
                "title": "Thunderbirds",
                "content_type": "tv",
                "season": 4,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "re-identifying"
    assert data["job_id"] == job_id
    mock_re_id.assert_called_once_with(job_id, "Thunderbirds", "tv", 4, None)


@pytest.mark.asyncio
async def test_re_identify_with_tmdb_id(client):
    """Re-identify with explicit TMDB ID should pass it through."""
    job_id = await _create_review_needed_job()

    with patch.object(job_manager, "re_identify_job", new_callable=AsyncMock) as mock_re_id:
        response = await client.post(
            f"/api/jobs/{job_id}/re-identify",
            json={
                "title": "WandaVision",
                "content_type": "tv",
                "season": 1,
                "tmdb_id": 85271,
            },
        )

    assert response.status_code == 200
    mock_re_id.assert_called_once_with(job_id, "WandaVision", "tv", 1, 85271)


@pytest.mark.asyncio
async def test_re_identify_rejects_wrong_state(client):
    """Re-identify should return 400 for jobs not in REVIEW_NEEDED state."""
    async with async_session() as session:
        job = DiscJob(
            volume_label="TEST_DISC",
            drive_id="E:",
            state=JobState.RIPPING,
            content_type=ContentType.TV,
            detected_title="Some Show",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    response = await client.post(
        f"/api/jobs/{job_id}/re-identify",
        json={"title": "Corrected", "content_type": "tv"},
    )
    assert response.status_code == 400
    assert "review_needed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_re_identify_rejects_missing_job(client):
    """Re-identify should return 404 for non-existent job."""
    response = await client.post(
        "/api/jobs/9999/re-identify",
        json={"title": "Corrected", "content_type": "tv"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_tmdb_search_requires_api_key(client):
    """TMDB search should return 400 if API key is not configured."""
    mock_config = MagicMock()
    mock_config.tmdb_api_key = None

    with patch(
        "app.services.config_service.get_config",
        new_callable=AsyncMock,
        return_value=mock_config,
    ):
        response = await client.get("/api/tmdb/search?query=thunderbirds")

    assert response.status_code == 400
    assert "TMDB API key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tmdb_search_returns_results(client):
    """TMDB search should return merged TV + movie results."""
    mock_config = MagicMock()
    mock_config.tmdb_api_key = "test_key_short"

    mock_resp_tv = MagicMock()
    mock_resp_tv.status_code = 200
    mock_resp_tv.json.return_value = {
        "results": [
            {
                "id": 1,
                "name": "Thunderbirds",
                "first_air_date": "1965-09-30",
                "poster_path": "/abc.jpg",
                "popularity": 42.5,
            }
        ]
    }

    mock_resp_movie = MagicMock()
    mock_resp_movie.status_code = 200
    mock_resp_movie.json.return_value = {
        "results": [
            {
                "id": 2,
                "title": "Thunderbird",
                "release_date": "2022-01-01",
                "poster_path": "/def.jpg",
                "popularity": 5.2,
            }
        ]
    }

    def mock_get(url, **kwargs):
        if "search/tv" in url:
            return mock_resp_tv
        return mock_resp_movie

    with (
        patch(
            "app.services.config_service.get_config",
            new_callable=AsyncMock,
            return_value=mock_config,
        ),
        patch("requests.get", side_effect=mock_get),
    ):
        response = await client.get("/api/tmdb/search?query=thunderbirds")

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2
    # TV result should be first (better name match to "thunderbirds")
    assert data["results"][0]["name"] == "Thunderbirds"
    assert data["results"][0]["type"] == "tv"
    assert data["results"][1]["name"] == "Thunderbird"
    assert data["results"][1]["type"] == "movie"
