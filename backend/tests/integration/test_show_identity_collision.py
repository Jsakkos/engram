import pytest
from sqlalchemy import text

from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, JobState


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


def test_ambiguous_signal_produces_review_result_without_id():
    """The analyst seam: an ambiguous TV signal yields needs_review + no tmdb_id."""
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=37854,
        tmdb_name="One Piece",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 37854, "name": "One Piece", "year": "1999", "popularity": 60.0},
            {"tmdb_id": 111110, "name": "One Piece", "year": "2023", "popularity": 38.3},
        ],
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.needs_review is True
    assert out.tmdb_id is None
    assert "One Piece" in out.review_reason


async def test_match_single_file_forwards_tmdb_id(monkeypatch):
    """The curator seam: a known tmdb_id reaches _ensure_initialized."""
    from app.core.curator import EpisodeCurator

    cur = EpisodeCurator()
    seen = {}

    def fake_ensure(show_name, tmdb_id=None):
        seen["show_name"] = show_name
        seen["tmdb_id"] = tmdb_id
        return False  # matcher unavailable -> fallback path, no real matching

    monkeypatch.setattr(cur, "_ensure_initialized", fake_ensure)
    from pathlib import Path

    await cur.match_single_file(Path("nonexistent.mkv"), "Frasier", 1, tmdb_id=195241)
    assert seen == {"show_name": "Frasier", "tmdb_id": 195241}


async def test_ambiguous_disc_routes_to_review_with_candidate_reason(monkeypatch):
    """Coordinator seam: an ambiguous-identity analysis must NOT be intercepted by the
    generic 'words merged' TMDB-lookup-failed guard; it must fall through to the
    needs_review branch and surface the candidate-naming reason.
    """
    from app.core.analyst import TitleInfo
    from app.services.job_manager import job_manager

    coord = job_manager._identification

    # Seed a job in IDENTIFYING with a drive id.
    async with async_session() as session:
        job = DiscJob(
            volume_label="FRASIER_S1",
            drive_id="E:",
            state=JobState.IDENTIFYING,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    # Fake disc scan: return one plausible title so identify_disc doesn't bail early.
    async def fake_scan_disc(drive, log_dir=None, *, job_id=0):
        return (
            [TitleInfo(index=0, duration_seconds=1440, size_bytes=2_000_000_000, chapter_count=7)],
            "FRASIER_S1",
        )

    monkeypatch.setattr(coord._extractor, "scan_disc", fake_scan_disc)

    # Craft an ambiguous analysis: TV, no tmdb_id, needs_review, candidate-naming reason.
    candidate_reason = (
        'Multiple shows match "Frasier" on TMDB: '
        "Frasier (1993, #3452); Frasier (2023, #195241). Pick the correct one."
    )
    analysis = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.6)
    analysis.detected_name = "Frasier"
    analysis.detected_season = 1
    analysis.needs_review = True
    analysis.tmdb_id = None
    analysis.review_reason = candidate_reason
    analysis._tmdb_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=None,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
        ],
    )
    analysis._discdb_signal = None

    async def fake_run_classification(*args, **kwargs):
        return analysis

    monkeypatch.setattr(coord, "_run_classification", fake_run_classification)

    # Drive the real identify_disc flow.
    await coord.identify_disc(job_id)

    # Reload from DB and assert correct routing.
    async with async_session() as session:
        refreshed = await session.get(DiscJob, job_id)
        assert refreshed.state == JobState.REVIEW_NEEDED, (
            f"Expected REVIEW_NEEDED, got {refreshed.state}"
        )
        assert "Multiple shows match" in (refreshed.review_reason or ""), (
            f"Candidate reason not found in review_reason: {refreshed.review_reason!r}"
        )
        assert "words merged" not in (refreshed.review_reason or ""), (
            f"Generic 'words merged' message incorrectly set: {refreshed.review_reason!r}"
        )
