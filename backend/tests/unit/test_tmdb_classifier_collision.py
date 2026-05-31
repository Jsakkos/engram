from unittest.mock import MagicMock, patch

import app.core.tmdb_classifier as tc
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


def _resp(results):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"results": results}
    return r


def test_search_tmdb_returns_best_and_results():
    results = [
        {"id": 1, "name": "Frasier", "popularity": 75.6},
        {"id": 2, "name": "Frasier", "popularity": 5.7},
    ]
    with patch.object(tc.requests, "get", return_value=_resp(results)):
        best, raw = tc._search_tmdb(tc.TMDB_SEARCH_TV_URL, "Frasier", {}, {}, 5.0)
    assert best is not None and best["id"] == 1
    assert raw == results


def test_search_tmdb_empty_returns_none_and_empty_list():
    with patch.object(tc.requests, "get", return_value=_resp([])):
        best, raw = tc._search_tmdb(tc.TMDB_SEARCH_TV_URL, "Nothing", {}, {}, 5.0)
    assert best is None
    assert raw == []


def _patch_searches(tv_results, movie_results=None):
    """Patch _search_tmdb to return canned TV/movie results regardless of URL."""
    movie_results = movie_results or []

    def fake(url, query, headers, params, timeout):
        if url == tc.TMDB_SEARCH_TV_URL:
            return (tv_results[0] if tv_results else None), tv_results
        return (movie_results[0] if movie_results else None), movie_results

    return patch.object(tc, "_search_tmdb", side_effect=fake)


def test_collision_flagged_when_both_substantial_and_close():
    # One Piece: anime 1999 p60 vs live-action 2023 p38.3 -> ratio 1.57, both >= 10
    tv = [
        {"id": 37854, "name": "One Piece", "popularity": 60.0, "first_air_date": "1999-10-20"},
        {"id": 111110, "name": "One Piece", "popularity": 38.3, "first_air_date": "2023-08-31"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("One Piece", "k" * 41)
    assert sig is not None
    assert sig.ambiguous_identity is True
    assert sig.tmdb_id is not None  # tentative best still reported
    ids = {c["tmdb_id"] for c in sig.candidates}
    assert ids == {37854, 111110}


def test_dominant_twin_not_flagged():
    # Frasier: 1993 p75.6 vs 2023 p5.7 -> ratio 13.3 AND runner-up below floor.
    tv = [
        {"id": 3452, "name": "Frasier", "popularity": 75.6, "first_air_date": "1993-09-16"},
        {"id": 195241, "name": "Frasier", "popularity": 5.7, "first_air_date": "2023-10-12"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Frasier", "k" * 41)
    assert sig is not None
    assert sig.ambiguous_identity is False


def test_noise_twin_not_flagged():
    # Yellowstone 2018 p159 vs 2009 p1.2 -> runner-up below floor.
    tv = [
        {"id": 73586, "name": "Yellowstone", "popularity": 159.7, "first_air_date": "2018-06-20"},
        {"id": 19355, "name": "Yellowstone", "popularity": 1.2, "first_air_date": "2009-01-01"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Yellowstone", "k" * 41)
    assert sig.ambiguous_identity is False


def test_unique_name_not_flagged():
    tv = [{"id": 1396, "name": "Breaking Bad", "popularity": 300.0, "first_air_date": "2008-01-20"}]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Breaking Bad", "k" * 41)
    assert sig.ambiguous_identity is False


def test_collision_lists_all_same_name_candidates():
    # Doctor Who: 2005 p109 vs 1963 p62 (gate fires on these two) plus a 2024 entry.
    # All three legitimate same-name shows must appear in candidates — the user may
    # own any of them — even though only the top two drove the materiality gate.
    tv = [
        {"id": 57243, "name": "Doctor Who", "popularity": 109.9, "first_air_date": "2005-03-26"},
        {"id": 121, "name": "Doctor Who", "popularity": 62.7, "first_air_date": "1963-11-23"},
        {"id": 239770, "name": "Doctor Who", "popularity": 21.9, "first_air_date": "2024-01-01"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Doctor Who", "k" * 41)
    assert sig.ambiguous_identity is True
    ids = {c["tmdb_id"] for c in sig.candidates}
    assert ids == {57243, 121, 239770}
