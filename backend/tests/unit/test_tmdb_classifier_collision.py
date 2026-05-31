from app.core.tmdb_classifier import (
    AMBIGUOUS_POPULARITY_FLOOR,
    AMBIGUOUS_POPULARITY_RATIO,
    TmdbSignal,
)
from app.models.disc_job import ContentType


def test_tmdb_signal_defaults_not_ambiguous():
    sig = TmdbSignal(content_type=ContentType.TV, confidence=0.7, tmdb_id=3452, tmdb_name="Frasier")
    assert sig.ambiguous_identity is False
    assert sig.candidates is None


def test_tmdb_signal_can_carry_candidates():
    cands = [{"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6}]
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=None,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=cands,
    )
    assert sig.ambiguous_identity is True
    assert sig.candidates == cands


def test_materiality_constants_have_sane_defaults():
    assert AMBIGUOUS_POPULARITY_FLOOR == 10.0
    assert AMBIGUOUS_POPULARITY_RATIO == 4.0
