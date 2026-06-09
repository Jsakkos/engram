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


@pytest.fixture(autouse=True)
def _no_episode_groups(monkeypatch):
    """Keep roster tests offline: the endpoint now also fetches episode groups
    (#200). Default to "none" so the common single-ordering path is exercised
    without a TMDB call; tests that want options patch this themselves."""
    monkeypatch.setattr(
        "app.core.episode_ordering.tmdb_client.fetch_episode_groups",
        lambda show_id, api_key: [],
    )


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

    async def test_unknown_season_reports_season_count_for_picker(self, client):
        """detected_season=None → available:false but show_id + season_count are
        present so the season prompt / review picker can render options (#370)."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)

        with patch("app.api.routes.get_number_of_seasons", return_value=5):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["show_id"] == 12345
        assert data["season_count"] == 5

    async def test_unknown_season_count_failure_degrades_gracefully(self, client):
        """A TMDB hiccup on the count lookup must not 500 the roster."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)

        with patch("app.api.routes.get_number_of_seasons", side_effect=RuntimeError("tmdb down")):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["season_count"] is None

    async def test_season_override_loads_that_seasons_roster(self, client):
        """?season=2 on an unknown-season job loads season 2's episodes (#370)."""
        await _seed_config()
        job = await _seed_tv_job(detected_season=None)
        await _seed_title(job.id, 0, "S02E01")

        seen: dict = {}

        def fake_fetch(show_id, season, api_key):
            seen["season"] = season
            return _FAKE_EPISODES

        with (
            patch("app.api.routes.fetch_season_episodes", side_effect=fake_fetch),
            patch("app.api.routes.get_number_of_seasons", return_value=5),
        ):
            response = await client.get(f"/api/jobs/{job.id}/season-roster?season=2")

        assert response.status_code == 200
        data = response.json()
        assert seen["season"] == 2
        assert data["available"] is True
        assert data["season_number"] == 2
        assert data["season_count"] == 5
        episodes = {ep["episode_code"]: ep for ep in data["episodes"]}
        assert episodes["S02E01"]["status"] == "assigned"

    async def test_roster_without_groups_does_not_surface_ordering(self, client):
        """The 90% case: a show with no episode groups -> selector stays hidden."""
        await _seed_config()
        job = await _seed_tv_job()
        await _seed_title(job.id, 0, "S03E01")

        with patch("app.api.routes.fetch_season_episodes", return_value=_FAKE_EPISODES):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        data = response.json()
        assert data["ordering_available"] is False
        assert data["ordering_diverges"] is False
        assert data["current_ordering"] == "aired"
        assert [o["ordering"] for o in data["ordering_options"]] == ["aired"]

    async def test_roster_surfaces_divergent_dvd_ordering(self, client, monkeypatch):
        """A show with a divergent DVD group -> selector data is present."""
        await _seed_config()
        job = await _seed_tv_job()
        # Episode 2 ("Game Changer") is the disc's matched episode; the fake DVD
        # group renumbers canonical S03E02 -> S03E05, so the ordering diverges.
        await _seed_title(job.id, 0, "S03E02")

        monkeypatch.setattr(
            "app.core.episode_ordering.tmdb_client.fetch_episode_groups",
            lambda show_id, api_key: [{"id": "g_dvd", "name": "DVD Order", "type": 3}],
        )
        monkeypatch.setattr(
            "app.core.episode_ordering.tmdb_client.fetch_episode_group",
            lambda gid, api_key: {
                "id": "g_dvd",
                "type": 3,
                "groups": [
                    {
                        "name": "Season 3",
                        "order": 1,
                        "episodes": [
                            {"season_number": 3, "episode_number": 2, "order": 4},
                        ],
                    }
                ],
            },
        )

        with patch("app.api.routes.fetch_season_episodes", return_value=_FAKE_EPISODES):
            response = await client.get(f"/api/jobs/{job.id}/season-roster")

        data = response.json()
        assert data["ordering_available"] is True
        assert data["ordering_diverges"] is True
        dvd = next(o for o in data["ordering_options"] if o["ordering"] == "dvd")
        assert dvd["projection"]["S03E02"] == "S03E05"


async def _title_id(job_id: int, index: int) -> int:
    from sqlalchemy import select

    async with _unit_session_factory() as session:
        result = await session.execute(
            select(DiscTitle).where(DiscTitle.job_id == job_id, DiscTitle.title_index == index)
        )
        return result.scalar_one().id
