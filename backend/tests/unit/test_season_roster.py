"""Unit tests for the season-roster endpoint.

GET /api/jobs/{job_id}/season-roster returns the detected season's episode
list (code + name from TMDB) plus per-episode coverage computed across the
job's titles: assigned / duplicate / missing (gap within the covered range) /
off (outside the disc's range). Powers the review-redesign roster strip.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory

# Episodes 1-6 of a season, as fetch_season_episodes would return them.
_FAKE_EPISODES = [
    {"episode_number": 1, "name": "Polaris", "runtime": 58},
    {"episode_number": 2, "name": "Game Changer", "runtime": 57},
    {"episode_number": 3, "name": "All In", "runtime": 59},
    {"episode_number": 4, "name": "Happy Valley", "runtime": 56},
    {"episode_number": 5, "name": "Seven Minutes of Terror", "runtime": 58},
    {"episode_number": 6, "name": "New Eden", "runtime": 60},
]


async def _seed_config() -> None:
    async with _unit_session_factory() as session:
        session.add(
            AppConfig(
                makemkv_path="/usr/bin/makemkvcon",
                makemkv_key="T-test-key-1234567890",
                staging_path="/tmp/staging",
                library_movies_path="/media/movies",
                library_tv_path="/media/tv",
                tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
                ffmpeg_path="/usr/bin/ffmpeg",
            )
        )
        await session.commit()


async def _seed_tv_job(**kwargs) -> DiscJob:
    defaults = dict(
        drive_id="E:",
        volume_label="FOR_ALL_MANKIND_S3",
        content_type=ContentType.TV,
        state=JobState.REVIEW_NEEDED,
        detected_title="For All Mankind",
        detected_season=3,
        tmdb_id=12345,
        staging_path="/tmp/staging/job_1",
    )
    defaults.update(kwargs)
    async with _unit_session_factory() as session:
        job = DiscJob(**defaults)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _seed_title(job_id: int, index: int, matched_episode: str | None) -> DiscTitle:
    async with _unit_session_factory() as session:
        title = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=3400,
            file_size_bytes=4_000_000_000,
            matched_episode=matched_episode,
            state=TitleState.MATCHED if matched_episode else TitleState.REVIEW,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return title


@pytest.fixture
async def client():
    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.unit
class TestSeasonRoster:
    async def test_roster_returns_episode_names_and_coverage(self, client):
        """Roster lists season episodes with names and per-episode status.

        Scenario: titles cover E01, E02, E05, with E05 doubled and E03/E04
        empty inside the covered range (1..5). E06 is outside the range.
        """
        await _seed_config()
        job = await _seed_tv_job()
        await _seed_title(job.id, 0, "S03E01")
        await _seed_title(job.id, 1, "S03E02")
        t_c = await _seed_title(job.id, 2, "S03E05")
        t_d = await _seed_title(job.id, 3, "S03E05")  # duplicate of E05
        await _seed_title(job.id, 4, None)  # unmatched

        with patch("app.api.routes.fetch_season_episodes", return_value=_FAKE_EPISODES):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["season_number"] == 3
        episodes = {ep["episode_code"]: ep for ep in data["episodes"]}

        # Names come through.
        assert episodes["S03E01"]["name"] == "Polaris"
        assert episodes["S03E04"]["name"] == "Happy Valley"

        # Coverage status.
        assert episodes["S03E01"]["status"] == "assigned"
        assert episodes["S03E02"]["status"] == "assigned"
        assert episodes["S03E03"]["status"] == "missing"  # gap inside range
        assert episodes["S03E04"]["status"] == "missing"  # gap inside range
        assert episodes["S03E05"]["status"] == "duplicate"  # two titles
        assert episodes["S03E06"]["status"] == "off"  # outside covered range

        # Duplicate slot reports both title ids; assigned slot reports one.
        assert set(episodes["S03E05"]["assigned_title_ids"]) == {t_c.id, t_d.id}
        assert episodes["S03E01"]["assigned_title_ids"] == [(await _title_id(job.id, 0))]

    async def test_roster_unavailable_without_tmdb_id(self, client):
        """No tmdb_id → roster cannot be built; respond gracefully, not 500."""
        await _seed_config()
        job = await _seed_tv_job(tmdb_id=None)

        response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["episodes"] == []
        assert data["reason"]


async def _title_id(job_id: int, index: int) -> int:
    from sqlalchemy import select

    async with _unit_session_factory() as session:
        result = await session.execute(
            select(DiscTitle).where(DiscTitle.job_id == job_id, DiscTitle.title_index == index)
        )
        return result.scalar_one().id
