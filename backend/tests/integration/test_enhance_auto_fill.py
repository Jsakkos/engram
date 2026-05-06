"""Integration tests for /contributions/{job_id}/enhance auto-fill behavior."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.upc_lookup import UPCLookupResult
from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType


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


async def _seed_job(release_group_id: str | None = None, label: str = "TEST_S1_D1") -> int:
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label=label,
            content_type=ContentType.TV,
            detected_title="Test Show",
            detected_season=1,
            tmdb_id=12345,
            state=JobState.COMPLETED,
            content_hash="ABCDEF1234567890",
            release_group_id=release_group_id,
            completed_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.asyncio
@pytest.mark.integration
class TestEnhanceAutoFill:
    async def test_asin_auto_filled_when_upc_provided_without_asin(self, client):
        job_id = await _seed_job()

        fake_lookup = UPCLookupResult(
            success=True,
            product_title="Test Show Season 1",
            asins=["B0EXPECTEDASIN", "B0SECONDASIN"],
            images=["https://example.com/cover.jpg"],
        )

        with patch("app.core.upc_lookup.lookup_upc", return_value=fake_lookup) as mock_lookup:
            res = await client.post(
                f"/api/contributions/{job_id}/enhance",
                json={"upc_code": "043996634404"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["asin_auto_filled"] is True
        mock_lookup.assert_called_once_with("043996634404")

        async with async_session() as session:
            updated = await session.get(DiscJob, job_id)
            assert updated.upc_code == "043996634404"
            assert updated.asin == "B0EXPECTEDASIN"

    async def test_explicit_asin_skips_lookup(self, client):
        job_id = await _seed_job()

        with patch("app.core.upc_lookup.lookup_upc") as mock_lookup:
            res = await client.post(
                f"/api/contributions/{job_id}/enhance",
                json={"upc_code": "043996634404", "asin": "B0USERPICKED"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["asin_auto_filled"] is False
        mock_lookup.assert_not_called()

        async with async_session() as session:
            updated = await session.get(DiscJob, job_id)
            assert updated.asin == "B0USERPICKED"

    async def test_failed_lookup_leaves_asin_unset(self, client):
        job_id = await _seed_job()

        fake_failure = UPCLookupResult(success=False, error="Not found")
        with patch("app.core.upc_lookup.lookup_upc", return_value=fake_failure):
            res = await client.post(
                f"/api/contributions/{job_id}/enhance",
                json={"upc_code": "999999999999"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["asin_auto_filled"] is False

        async with async_session() as session:
            updated = await session.get(DiscJob, job_id)
            assert updated.upc_code == "999999999999"
            assert updated.asin is None

    async def test_upc_propagates_to_release_group(self, client):
        rg = "rg-spread-test"
        job_ids = [
            await _seed_job(release_group_id=rg, label="FAM_S1_D1"),
            await _seed_job(release_group_id=rg, label="FAM_S1_D2"),
            await _seed_job(release_group_id=rg, label="FAM_S1_D3"),
        ]

        fake_lookup = UPCLookupResult(success=True, asins=["B0GROUP1ASIN"])
        with patch("app.core.upc_lookup.lookup_upc", return_value=fake_lookup):
            res = await client.post(
                f"/api/contributions/{job_ids[0]}/enhance",
                json={"upc_code": "043996634404"},
            )

        body = res.json()
        assert body["applied_to_group_size"] == 3

        async with async_session() as session:
            for jid in job_ids:
                row = await session.get(DiscJob, jid)
                assert row.upc_code == "043996634404"
                assert row.asin == "B0GROUP1ASIN"

    async def test_solo_disc_only_updates_itself(self, client):
        # Two jobs with no release_group_id; updating one must not touch the other.
        a = await _seed_job(release_group_id=None, label="SOLO_A")
        b = await _seed_job(release_group_id=None, label="SOLO_B")

        with patch("app.core.upc_lookup.lookup_upc") as mock_lookup:
            res = await client.post(
                f"/api/contributions/{a}/enhance",
                json={"upc_code": "111111111111", "asin": "B0SOLOA"},
            )

        body = res.json()
        assert body["applied_to_group_size"] == 1
        mock_lookup.assert_not_called()

        async with async_session() as session:
            row_a = await session.get(DiscJob, a)
            row_b = await session.get(DiscJob, b)
            assert row_a.upc_code == "111111111111"
            assert row_a.asin == "B0SOLOA"
            assert row_b.upc_code is None
            assert row_b.asin is None

    async def test_release_date_persisted(self, client):
        job_id = await _seed_job()

        with patch("app.core.upc_lookup.lookup_upc"):
            res = await client.post(
                f"/api/contributions/{job_id}/enhance",
                json={"upc_code": "043996634404", "asin": "B0X", "release_date": "2019-11-01"},
            )

        assert res.status_code == 200
        async with async_session() as session:
            row = await session.get(DiscJob, job_id)
            assert row.release_date == "2019-11-01"
