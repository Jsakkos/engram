"""Integration tests for GroupingCoordinator sibling resolution.

Uses the real SQLite session so the SELECT path is exercised end-to-end.
"""

import pytest
from sqlalchemy import text

from app.database import async_session, init_db
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.grouping_coordinator import (
    auto_assign_release_group,
    derive_group_key,
)


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean job data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


def _make_job(label: str, content_type: ContentType = ContentType.TV, **kwargs) -> DiscJob:
    return DiscJob(
        drive_id="E:",
        volume_label=label,
        content_type=content_type,
        state=JobState.COMPLETED,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.integration
class TestAutoAssignReleaseGroup:
    async def test_first_disc_mints_new_uuid(self):
        async with async_session() as session:
            job = _make_job("FOR_ALL_MANKIND_S1_D1")
            session.add(job)
            await session.commit()
            await session.refresh(job)

            result = await auto_assign_release_group(job, session)
            await session.commit()

            assert result is not None
            assert len(result) == 36  # UUID4 length
            assert job.release_group_id == result

    async def test_sibling_disc_adopts_existing_group(self):
        async with async_session() as session:
            d1 = _make_job("FOR_ALL_MANKIND_S1_D1")
            session.add(d1)
            await session.commit()
            await auto_assign_release_group(d1, session)
            await session.commit()

            d2 = _make_job("FOR_ALL_MANKIND_S1_D2")
            session.add(d2)
            await session.commit()
            assigned = await auto_assign_release_group(d2, session)
            await session.commit()

            assert assigned == d1.release_group_id

    async def test_different_seasons_get_different_groups(self):
        async with async_session() as session:
            d1 = _make_job("FRIENDS_S1_D1")
            d2 = _make_job("FRIENDS_S2_D1")
            session.add_all([d1, d2])
            await session.commit()
            await auto_assign_release_group(d1, session)
            await auto_assign_release_group(d2, session)
            await session.commit()

            assert d1.release_group_id != d2.release_group_id

    async def test_existing_group_id_is_preserved(self):
        # User manually assigned a group beforehand — coordinator must not change it.
        async with async_session() as session:
            d1 = _make_job(
                "FOR_ALL_MANKIND_S1_D1",
                release_group_id="00000000-0000-0000-0000-000000000001",
            )
            session.add(d1)
            await session.commit()

            result = await auto_assign_release_group(d1, session)
            assert result == "00000000-0000-0000-0000-000000000001"
            assert d1.release_group_id == "00000000-0000-0000-0000-000000000001"

    async def test_no_group_key_returns_none(self):
        # Generic label, no TMDB ID → derive_group_key returns None.
        async with async_session() as session:
            d1 = _make_job(
                "LOGICAL_VOLUME_ID",
                content_type=ContentType.UNKNOWN,
                tmdb_id=None,
            )
            session.add(d1)
            await session.commit()

            result = await auto_assign_release_group(d1, session)
            assert result is None
            assert d1.release_group_id is None

    async def test_tmdb_fallback_groups_siblings_without_label(self):
        async with async_session() as session:
            d1 = _make_job(
                "DISC_1",  # not parsable to (show, season)
                content_type=ContentType.TV,
                tmdb_id=87567,
                detected_season=1,
            )
            d2 = _make_job(
                "VIDEO_DISC",  # also not parsable
                content_type=ContentType.TV,
                tmdb_id=87567,
                detected_season=1,
            )
            session.add_all([d1, d2])
            await session.commit()
            await auto_assign_release_group(d1, session)
            await auto_assign_release_group(d2, session)
            await session.commit()

            assert d1.release_group_id == d2.release_group_id

    async def test_movie_groups_by_tmdb_id_only(self):
        async with async_session() as session:
            m1 = _make_job(
                "INCEPTION",
                content_type=ContentType.MOVIE,
                tmdb_id=27205,
            )
            m2 = _make_job(
                "INCEPTION",
                content_type=ContentType.MOVIE,
                tmdb_id=27205,
            )
            session.add_all([m1, m2])
            await session.commit()
            await auto_assign_release_group(m1, session)
            await auto_assign_release_group(m2, session)
            await session.commit()

            assert m1.release_group_id == m2.release_group_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_derive_group_key_against_persisted_job():
    """Smoke test: a freshly-loaded ORM instance still derives the same key."""
    async with async_session() as session:
        job = _make_job("FRIENDS_S2_D3")
        session.add(job)
        await session.commit()
        await session.refresh(job)

        assert derive_group_key(job) == ("label", "FRIENDS", 2)
