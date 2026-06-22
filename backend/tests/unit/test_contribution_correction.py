import pytest
from sqlmodel import select

from app.database import async_session, init_db
from app.matcher.chromaprint_extractor import ChromaprintResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState
from app.models.fingerprint import FingerprintContribution, FingerprintRetraction
from app.services.contribution_correction import ContributionCorrectionService, NewTarget


def _blob() -> bytes:
    return ChromaprintResult(
        hashes=[1, 2, 3, 4], duration_seconds=10.0, fpcalc_version="t"
    ).to_blob()


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import text as _t

        await session.execute(_t("DELETE FROM fingerprint_contributions"))
        await session.execute(_t("DELETE FROM fingerprint_retractions"))
        await session.commit()


async def _make_title(session, *, uploaded: bool):
    job = DiscJob(
        drive_id="E:",
        volume_label="BB_S3",
        content_type=ContentType.TV,
        state=JobState.COMPLETED,
        tmdb_id=1396,
        tmdb_name="Breaking Bad",
        tmdb_year=2008,
        detected_season=3,
    )
    session.add(job)
    await session.commit()
    title = DiscTitle(
        job_id=job.id,
        title_index=24,
        duration_seconds=3382,
        matched_episode="S03E10",
        chromaprint_blob=_blob(),
    )
    session.add(title)
    await session.commit()
    contrib = FingerprintContribution(
        title_id=title.id,
        chromaprint_blob=_blob(),
        tmdb_id=1396,
        season=3,
        episode=10,
        match_confidence=0.8,
        match_source="engram_asr",
        pseudonym="00000000-0000-4000-8000-000000000000",
        upload_status="success" if uploaded else None,
    )
    session.add(contrib)
    await session.commit()
    return job, title


async def test_uploaded_row_enqueues_retraction_and_deletes_contribution():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=True)
        await ContributionCorrectionService().correct_title_contribution(
            session,
            title,
            NewTarget(kind="extra"),
            job=job,
            enable_contributions=True,
            pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        contribs = (await session.execute(select(FingerprintContribution))).scalars().all()
        retractions = (await session.execute(select(FingerprintRetraction))).scalars().all()
        assert contribs == []
        assert len(retractions) == 1
        assert retractions[0].season == 3 and retractions[0].episode == 10


async def test_pending_row_deletes_without_retraction():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=False)
        await ContributionCorrectionService().correct_title_contribution(
            session,
            title,
            NewTarget(kind="extra"),
            job=job,
            enable_contributions=True,
            pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        assert (await session.execute(select(FingerprintContribution))).scalars().all() == []
        assert (await session.execute(select(FingerprintRetraction))).scalars().all() == []


async def test_episode_target_recontributes_as_user_review():
    async with async_session() as session:
        job, title = await _make_title(session, uploaded=True)
        await ContributionCorrectionService().correct_title_contribution(
            session,
            title,
            NewTarget(kind="episode", episode_code="S03E11"),
            job=job,
            enable_contributions=True,
            pseudonym="00000000-0000-4000-8000-000000000000",
        )
        await session.commit()
        contribs = (await session.execute(select(FingerprintContribution))).scalars().all()
        assert len(contribs) == 1
        assert contribs[0].episode == 11
        assert contribs[0].match_source == "user_review"
        assert contribs[0].match_confidence == 1.0
