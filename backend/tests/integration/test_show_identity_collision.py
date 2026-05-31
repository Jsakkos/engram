import pytest
from sqlalchemy import text

from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.database import async_session, init_db
from app.models.disc_job import ContentType


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
