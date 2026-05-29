"""Chromaprint signal is additive to calibrate_confidence — never lowers, no-op when absent."""

from app.matcher.episode_identification import calibrate_confidence

BASE = dict(score=0.5, score_gap=0.4, vote_count=8, target_votes=10, processed_coverage=0.5)


def test_absent_signal_is_byte_identical():
    a = calibrate_confidence(**BASE)
    b = calibrate_confidence(**BASE, chromaprint_signal=None)
    assert a == b


def test_strong_signal_raises_confidence():
    base_conf, _ = calibrate_confidence(
        score=0.2, score_gap=0.05, vote_count=3, target_votes=10, processed_coverage=0.3
    )
    cp_conf, comps = calibrate_confidence(
        score=0.2,
        score_gap=0.05,
        vote_count=3,
        target_votes=10,
        processed_coverage=0.3,
        chromaprint_signal={
            "hash_overlap": 0.95,
            "temporal_coherence": 0.9,
            "rarity_weighted_score": 0.9,
        },
    )
    assert cp_conf > base_conf
    assert "cp_confidence" in comps
    assert "hash_overlap" in comps


def test_weak_signal_never_lowers_asr_strong():
    strong, _ = calibrate_confidence(
        score=0.9, score_gap=0.8, vote_count=20, target_votes=20, processed_coverage=0.9
    )
    with_weak, _ = calibrate_confidence(
        score=0.9,
        score_gap=0.8,
        vote_count=20,
        target_votes=20,
        processed_coverage=0.9,
        chromaprint_signal={
            "hash_overlap": 0.1,
            "temporal_coherence": 0.0,
            "rarity_weighted_score": 0.0,
        },
    )
    assert with_weak >= strong
