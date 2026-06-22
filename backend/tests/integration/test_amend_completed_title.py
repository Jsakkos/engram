"""Integration tests for JobManager.amend_title_assignment.

Verifies that a track on a COMPLETED job can be corrected in place:
the organized library file is moved to its new home, the DiscTitle is
updated, and the job stays COMPLETED.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.contribution_correction import NewTarget
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import text

        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


async def _seed_completed_tv(tmp_path: Path):
    lib = tmp_path / "lib"
    season_dir = lib / "Breaking Bad (2008) [tmdbid-1396]" / "Season 03"
    season_dir.mkdir(parents=True)
    organized = season_dir / "Breaking Bad - S03E10.mkv"
    organized.write_text("fake video")

    async with async_session() as session:
        job = DiscJob(
            volume_label="BREAKING_BAD_S3_D2",
            content_type=ContentType.TV,
            state=JobState.COMPLETED,
            tmdb_id=1396,
            tmdb_name="Breaking Bad",
            tmdb_year=2008,
            detected_title="Breaking Bad",
            detected_season=3,
            disc_number=2,
            drive_id="E:",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=24,
            duration_seconds=3382,
            matched_episode="S03E10",
            state=TitleState.COMPLETED,
            organized_to=str(organized),
            is_extra=False,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id, lib


async def test_amend_to_extra_moves_file_and_clears_episode(tmp_path, monkeypatch):
    job_id, title_id, lib = await _seed_completed_tv(tmp_path)

    # Patch get_config in config_service so amend_title_assignment passes the
    # right library_path to the organizers (which also call get_config_sync
    # for naming formats — redirect those too to avoid writing into the real library).
    from app.models.app_config import AppConfig
    from app.services import config_service

    # Build a minimal config pointing at our tmp lib.
    fake_cfg = AppConfig(
        library_tv_path=str(lib),
        library_movies_path=str(tmp_path / "movies"),
        enable_fingerprint_contributions=False,
        contribution_pseudonym=None,
    )

    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value=fake_cfg))
    monkeypatch.setattr(config_service, "get_config_sync", lambda: fake_cfg)

    # Stub out the contribution service — no fingerprint rows exist in this test.
    from app.services import contribution_correction

    monkeypatch.setattr(
        contribution_correction.ContributionCorrectionService,
        "correct_title_contribution",
        AsyncMock(return_value=None),
    )

    await job_manager.amend_title_assignment(job_id, title_id, NewTarget(kind="extra"))

    async with async_session() as session:
        title = await session.get(DiscTitle, title_id)
        assert title.is_extra is True
        assert title.matched_episode is None
        assert title.organized_to is not None
        assert "Extras" in title.organized_to
        assert Path(title.organized_to).exists()
        # Original episode file must be gone
        assert not (
            lib / "Breaking Bad (2008) [tmdbid-1396]" / "Season 03" / "Breaking Bad - S03E10.mkv"
        ).exists()
        # Job stays COMPLETED
        job = await session.get(DiscJob, job_id)
        assert job.state == JobState.COMPLETED


async def test_amend_to_episode_moves_file_and_updates_episode(tmp_path, monkeypatch):
    job_id, title_id, lib = await _seed_completed_tv(tmp_path)

    from app.models.app_config import AppConfig
    from app.services import config_service

    fake_cfg = AppConfig(
        library_tv_path=str(lib),
        library_movies_path=str(tmp_path / "movies"),
        enable_fingerprint_contributions=False,
        contribution_pseudonym=None,
    )

    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value=fake_cfg))
    monkeypatch.setattr(config_service, "get_config_sync", lambda: fake_cfg)

    from app.services import contribution_correction

    monkeypatch.setattr(
        contribution_correction.ContributionCorrectionService,
        "correct_title_contribution",
        AsyncMock(return_value=None),
    )

    await job_manager.amend_title_assignment(
        job_id, title_id, NewTarget(kind="episode", episode_code="S03E11")
    )

    async with async_session() as session:
        title = await session.get(DiscTitle, title_id)
        assert title.matched_episode == "S03E11"
        assert title.is_extra is False
        assert title.organized_to is not None
        assert "S03E11" in title.organized_to
        assert Path(title.organized_to).exists()
        # Original path must be gone
        assert not (
            lib / "Breaking Bad (2008) [tmdbid-1396]" / "Season 03" / "Breaking Bad - S03E10.mkv"
        ).exists()
