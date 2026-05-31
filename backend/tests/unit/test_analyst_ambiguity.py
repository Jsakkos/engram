from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.models.disc_job import ContentType


def test_ambiguous_signal_clears_id_and_forces_review():
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=3452,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
        ],
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.tmdb_id is None
    assert out.needs_review is True
    assert out.review_reason and "Frasier" in out.review_reason
    assert "1993" in out.review_reason and "2023" in out.review_reason


def test_non_ambiguous_signal_sets_id_normally():
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV, confidence=0.85, tmdb_id=1396, tmdb_name="Breaking Bad"
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.tmdb_id == 1396
    assert out.needs_review is False
