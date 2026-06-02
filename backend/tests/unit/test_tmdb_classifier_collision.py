from unittest.mock import MagicMock, patch

import app.core.tmdb_classifier as tc
from app.models.disc_job import ContentType


def test_tmdb_signal_defaults_not_ambiguous():
    sig = tc.TmdbSignal(
        content_type=ContentType.TV, confidence=0.7, tmdb_id=3452, tmdb_name="Frasier"
    )
    assert sig.ambiguous_identity is False
    assert sig.candidates is None
    assert sig.all_candidates is None


def test_tmdb_signal_can_carry_candidates():
    cands = [{"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6}]
    sig = tc.TmdbSignal(
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
    assert tc.AMBIGUOUS_POPULARITY_FLOOR == 10.0
    assert tc.AMBIGUOUS_POPULARITY_RATIO == 4.0
    # Lower floor for the no-year backstop (label can't self-disambiguate twins).
    assert tc.AMBIGUOUS_NO_YEAR_FLOOR == 3.0


# all_candidates are popularity-sorted desc; runner-up is index 1.
_FRASIER_CANDS = [
    {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
    {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
]


def test_should_flag_no_year_flags_dominant_twin_without_year():
    # No year on the disc label + a real twin (runner-up 5.7 >= floor 3.0) -> flag.
    assert tc._should_flag_no_year(_FRASIER_CANDS, has_year=False) is True


def test_should_flag_no_year_skips_when_year_present():
    # A year disambiguates via popularity+year, so no proactive flag.
    assert tc._should_flag_no_year(_FRASIER_CANDS, has_year=True) is False


def test_should_flag_no_year_skips_noise_runner_up():
    noise = [
        {"tmdb_id": 73586, "name": "Yellowstone", "year": "2018", "popularity": 159.7},
        {"tmdb_id": 19355, "name": "Yellowstone", "year": "2009", "popularity": 1.2},
    ]
    assert tc._should_flag_no_year(noise, has_year=False) is False


def test_should_flag_no_year_skips_without_twin():
    assert tc._should_flag_no_year(None, has_year=False) is False
    assert tc._should_flag_no_year([_FRASIER_CANDS[0]], has_year=False) is False


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


def test_all_candidates_records_dominant_twin_even_when_gate_does_not_fire():
    # Frasier dominant twin: the materiality gate stays False (revival below the
    # popularity floor), but BOTH ids must still be RECORDED in all_candidates so
    # the downstream wrong-show detector can suggest the 2023 revival. This is the
    # "record the ambiguity" vs "proactively flag it" split.
    tv = [
        {"id": 3452, "name": "Frasier", "popularity": 75.6, "first_air_date": "1993-09-16"},
        {"id": 195241, "name": "Frasier", "popularity": 5.7, "first_air_date": "2023-10-12"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Frasier", "k" * 41)
    assert sig.ambiguous_identity is False
    assert sig.all_candidates is not None
    ids = {c["tmdb_id"] for c in sig.all_candidates}
    assert ids == {3452, 195241}


def test_all_candidates_none_for_unique_name():
    tv = [{"id": 1396, "name": "Breaking Bad", "popularity": 300.0, "first_air_date": "2008-01-20"}]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("Breaking Bad", "k" * 41)
    assert sig.all_candidates is None


def test_all_candidates_populated_when_gate_fires():
    tv = [
        {"id": 37854, "name": "One Piece", "popularity": 60.0, "first_air_date": "1999-10-20"},
        {"id": 111110, "name": "One Piece", "popularity": 38.3, "first_air_date": "2023-08-31"},
    ]
    with _patch_searches(tv):
        sig = tc.classify_from_tmdb("One Piece", "k" * 41)
    assert sig.ambiguous_identity is True
    assert sig.all_candidates is not None
    ids = {c["tmdb_id"] for c in sig.all_candidates}
    assert ids == {37854, 111110}


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
