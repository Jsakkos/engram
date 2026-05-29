"""Pure scoring functions — Python twin of src/db_identify.ts. Golden vectors locked."""

import math

from app.matcher.chromaprint_scoring import (
    combined_window_score,
    hash_overlap_pct,
    rarity_weighted_overlap,
    temporal_coherence,
)


def test_golden_vector_1():
    query = [1, 2, 3, 4, 5, 6, 7, 8]
    ref = {1, 2, 3, 5, 6, 7}
    df = dict.fromkeys(ref, 1)
    assert hash_overlap_pct(query, ref) == 0.75
    assert temporal_coherence(query, ref) == 0.75
    assert math.isclose(rarity_weighted_overlap(query, ref, df, 10), 0.75, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.75, 0.75, 0.75), 0.75, abs_tol=1e-9)


def test_golden_vector_2_scattered():
    query = [1, 9, 2, 9, 3, 9, 4]
    ref = {1, 2, 3, 4}
    assert math.isclose(hash_overlap_pct(query, ref), 4 / 7, abs_tol=1e-9)
    assert temporal_coherence(query, ref) == 0.0


def test_rarity_falls_back_to_overlap_without_df():
    query = [1, 9, 2, 9, 3, 9, 4]
    ref = {1, 2, 3, 4}
    assert math.isclose(rarity_weighted_overlap(query, ref, {}, 10), 4 / 7, abs_tol=1e-9)


def test_rarity_upweights_rare_hashes():
    ref = {1, 2}
    df = {1: 1, 2: 9}  # hash 1 rare, hash 2 common
    only_rare = rarity_weighted_overlap([1, 3], ref, df, 10)
    only_common = rarity_weighted_overlap([2, 3], ref, df, 10)
    assert only_rare > only_common


def test_combined_weights():
    assert math.isclose(combined_window_score(1.0, 0.0, 0.0), 0.3, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.0, 1.0, 0.0), 0.2, abs_tol=1e-9)
    assert math.isclose(combined_window_score(0.0, 0.0, 1.0), 0.5, abs_tol=1e-9)


def test_empty_query_returns_zero():
    assert hash_overlap_pct([], {1, 2}) == 0.0
    assert temporal_coherence([], {1, 2}) == 0.0
    assert rarity_weighted_overlap([], {1, 2}, {1: 1}, 10) == 0.0
