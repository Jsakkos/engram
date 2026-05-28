"""Integration tests for the batch review endpoint.

Tests POST /api/jobs/{id}/review/batch — applying multiple review decisions in a
single atomic request. This is what backs the review-tab multiselect "mark all
as extras" workflow, and the single finalize pass it runs is what keeps many
extras from colliding on FILE_EXISTS during organization.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean job data between tests."""
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


async def _make_tv_review_job(title_count: int, *, with_files_dir=None):
    """Seed a REVIEW_NEEDED TV job with ``title_count`` titles.

    If ``with_files_dir`` is given, each title gets a real on-disk staging file
    so the organization path can run; otherwise output_filename is None and
    organization is skipped.
    """
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST_SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Test Show",
            detected_season=1,
            disc_number=1,
            staging_path=str(with_files_dir) if with_files_dir else "/tmp/staging/test",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        titles = []
        for i in range(title_count):
            output_filename = None
            if with_files_dir is not None:
                f = with_files_dir / f"title_t{i:02d}.mkv"
                f.write_bytes(b"fake mkv content")
                output_filename = str(f)
            title = DiscTitle(
                job_id=job.id,
                title_index=i,
                duration_seconds=300,
                file_size_bytes=1024 * 1024,
                chapter_count=2,
                state=TitleState.MATCHED,
                output_filename=output_filename,
            )
            session.add(title)
            titles.append(title)
        await session.commit()
        for t in titles:
            await session.refresh(t)

        return job, titles


@pytest.mark.asyncio
async def test_batch_review_marks_multiple_as_extra(client):
    """A single batch call marks every listed title as an extra in the DB."""
    job, titles = await _make_tv_review_job(4)
    # Mark the first three as extra; leave the fourth unresolved so the job
    # stays in review and no organization is triggered.
    decisions = [{"title_id": t.id, "episode_code": "extra"} for t in titles[:3]]

    resp = await client.post(
        f"/api/jobs/{job.id}/review/batch",
        json={"decisions": decisions},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reviewed"
    assert data["job_id"] == job.id

    async with async_session() as session:
        for t in titles[:3]:
            reloaded = await session.get(DiscTitle, t.id)
            assert reloaded.is_extra is True
            assert reloaded.matched_episode == "extra"
        # Untouched title remains unresolved.
        fourth = await session.get(DiscTitle, titles[3].id)
        assert fourth.matched_episode is None
        # Job still awaiting review because one title is unresolved.
        reloaded_job = await session.get(DiscJob, job.id)
        assert reloaded_job.state == JobState.REVIEW_NEEDED


@pytest.mark.asyncio
async def test_batch_review_rejects_non_review_state(client):
    """A batch on a job that is not awaiting review must be rejected."""
    job, titles = await _make_tv_review_job(2)
    async with async_session() as session:
        reloaded = await session.get(DiscJob, job.id)
        reloaded.state = JobState.COMPLETED
        session.add(reloaded)
        await session.commit()

    resp = await client.post(
        f"/api/jobs/{job.id}/review/batch",
        json={"decisions": [{"title_id": titles[0].id, "episode_code": "extra"}]},
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_review_organizes_extras_without_collision(client, tmp_path, monkeypatch):
    """Marking several titles as extra in one batch organizes them all to unique
    Extras paths in a single pass — no FILE_EXISTS collision."""
    tv_lib = tmp_path / "TV"
    tv_lib.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()

    # Redirect extras organization to the tmp library instead of the real one.
    fake_config = AppConfig(library_tv_path=str(tv_lib))
    monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: fake_config)

    job, titles = await _make_tv_review_job(3, with_files_dir=staging)
    decisions = [{"title_id": t.id, "episode_code": "extra"} for t in titles]

    resp = await client.post(
        f"/api/jobs/{job.id}/review/batch",
        json={"decisions": decisions},
    )

    assert resp.status_code == 200

    # All three extras landed as distinct files under .../Extras/.
    extras_dir = tv_lib / "Test Show" / "Season 01" / "Extras"
    organized = sorted(p.name for p in extras_dir.glob("*.mkv"))
    assert len(organized) == 3, f"expected 3 unique extras, got {organized}"
    assert len(set(organized)) == 3

    async with async_session() as session:
        for t in titles:
            reloaded = await session.get(DiscTitle, t.id)
            assert reloaded.is_extra is True
            assert reloaded.state == TitleState.COMPLETED
