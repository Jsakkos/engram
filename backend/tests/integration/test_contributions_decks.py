"""Integration tests for GET /api/contributions/decks."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState


@pytest.fixture(autouse=True)
async def setup_db():
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


async def _seed_grouped_tv(release_group_id: str, episodes_per_disc: int = 3, discs: int = 4) -> list[int]:
    """Seed N COMPLETED TV discs sharing one release_group_id, each with matched titles."""
    from datetime import UTC, datetime

    job_ids: list[int] = []
    async with async_session() as session:
        for d in range(1, discs + 1):
            job = DiscJob(
                drive_id="E:",
                volume_label=f"FOR_ALL_MANKIND_S1_D{d}",
                content_type=ContentType.TV,
                detected_title="For All Mankind",
                detected_season=1,
                tmdb_id=87567,
                state=JobState.COMPLETED,
                content_hash=f"HASH{d:08d}",
                disc_number=d,
                release_group_id=release_group_id,
                completed_at=datetime.now(UTC),
                exported_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_ids.append(job.id)

            for i in range(episodes_per_disc):
                ep_num = (d - 1) * episodes_per_disc + i + 1
                session.add(
                    DiscTitle(
                        job_id=job.id,
                        title_index=i,
                        duration_seconds=2700,
                        is_selected=True,
                        matched_episode=f"S1E{ep_num}",
                        state=TitleState.COMPLETED,
                    )
                )
            await session.commit()
    return job_ids


@pytest.mark.asyncio
@pytest.mark.integration
class TestDecksEndpoint:
    async def test_empty_returns_empty_list(self, client):
        response = await client.get("/api/contributions/decks")
        assert response.status_code == 200
        assert response.json() == []

    async def test_grouped_discs_become_one_deck(self, client):
        await _seed_grouped_tv("rg-fam-s1", discs=4, episodes_per_disc=3)
        response = await client.get("/api/contributions/decks")
        assert response.status_code == 200
        decks = response.json()

        assert len(decks) == 1
        deck = decks[0]
        assert deck["release_group_id"] == "rg-fam-s1"
        assert deck["is_solo"] is False
        assert deck["title"] == "For All Mankind"
        assert deck["season"] == 1
        assert deck["tmdb_id"] == 87567
        assert deck["content_type"] == "tv"
        assert len(deck["discs"]) == 4
        # 4 discs * 3 episodes = 12 matched/12 selected
        assert deck["matched_episodes"] == "12/12"
        # 4 discs * 3 titles * 2700s = 32400s
        assert deck["total_runtime_seconds"] == 32400
        # Discs sorted by disc_number
        assert [d["disc_number"] for d in deck["discs"]] == [1, 2, 3, 4]
        assert deck["submission_status"] == {
            "pending": 0,
            "exported": 4,
            "skipped": 0,
            "submitted": 0,
        }

    async def test_solo_disc_uses_synthetic_id(self, client):
        from datetime import UTC, datetime

        async with async_session() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="INCEPTION",
                content_type=ContentType.MOVIE,
                detected_title="Inception",
                tmdb_id=27205,
                state=JobState.COMPLETED,
                completed_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        response = await client.get("/api/contributions/decks")
        decks = response.json()
        assert len(decks) == 1
        assert decks[0]["release_group_id"] == f"solo-{job_id}"
        assert decks[0]["is_solo"] is True
        assert decks[0]["content_type"] == "movie"
        assert len(decks[0]["discs"]) == 1

    async def test_episode_range_compact(self, client):
        await _seed_grouped_tv("rg-x", discs=2, episodes_per_disc=3)
        response = await client.get("/api/contributions/decks")
        deck = response.json()[0]
        # First disc has S1E1, S1E2, S1E3
        assert deck["discs"][0]["episode_range"] == "S1E1-S1E3"
        # Second disc has S1E4, S1E5, S1E6
        assert deck["discs"][1]["episode_range"] == "S1E4-S1E6"

    async def test_two_groups_sorted_by_recent_completion(self, client):
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)

        async with async_session() as session:
            old = DiscJob(
                drive_id="E:",
                volume_label="OLD_S1_D1",
                content_type=ContentType.TV,
                detected_title="Old Show",
                state=JobState.COMPLETED,
                release_group_id="rg-old",
                completed_at=now - timedelta(days=2),
                exported_at=now - timedelta(days=2),
            )
            new = DiscJob(
                drive_id="E:",
                volume_label="NEW_S1_D1",
                content_type=ContentType.TV,
                detected_title="New Show",
                state=JobState.COMPLETED,
                release_group_id="rg-new",
                completed_at=now,
                exported_at=now,
            )
            session.add_all([old, new])
            await session.commit()

        response = await client.get("/api/contributions/decks")
        decks = response.json()
        assert len(decks) == 2
        assert decks[0]["release_group_id"] == "rg-new"
        assert decks[1]["release_group_id"] == "rg-old"

    async def test_pending_export_counted_correctly(self, client):
        from datetime import UTC, datetime

        async with async_session() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="PENDING_S1_D1",
                content_type=ContentType.TV,
                detected_title="Pending",
                state=JobState.COMPLETED,
                release_group_id="rg-pending",
                completed_at=datetime.now(UTC),
                exported_at=None,  # never exported
            )
            session.add(job)
            await session.commit()

        response = await client.get("/api/contributions/decks")
        deck = response.json()[0]
        assert deck["submission_status"]["pending"] == 1
        assert deck["submission_status"]["exported"] == 0
