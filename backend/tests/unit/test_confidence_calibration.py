"""Unit tests for episode-match confidence calibration.

The matcher's raw ``ranked_voting_score`` is a TF-IDF cosine magnitude
(~0.15-0.21 even for a correct match) and is uninterpretable as a percentage.
``calibrate_confidence`` translates the raw signals (separation, consensus,
normalized score, processed coverage) into a 0-1 confidence that a human
reviewer can read. These tests pin that behavior, anchored to real
Arrested Development S1 matcher runs.

Real AD S1 runs (from ~/.engram/engram.log), all CORRECT matches that
previously displayed as ~18%:

    Episode  raw_score  score_gap  votes/samples
    S01E13   0.165      0.1652     8/10
    S01E12   0.180      0.1795     5/10
    S01E14   0.180      0.1804     4/10

In every case score_gap == score (top2 == 0): the winner swept the field.
"""

import pytest

from app.matcher.episode_identification import (
    _attach_calibrated_confidence,
    calibrate_confidence,
)

# ~22 min episode -> 10 samples * 30s / 1320s ~= 0.227 processed coverage
AD_COVERAGE = 10 * 30 / 1320


def _calibrate(score, score_gap, votes, target=10, coverage=AD_COVERAGE):
    conf, _ = calibrate_confidence(
        score=score,
        score_gap=score_gap,
        vote_count=votes,
        target_votes=target,
        processed_coverage=coverage,
    )
    return conf


# --- Real-data anchors: correct decisive matches must read clearly high -------


def test_decisive_high_vote_match_reads_high():
    """S01E13: swept the field, 8/10 votes -> should auto-organize (>= 0.85)."""
    conf = _calibrate(0.165, 0.1652, 8)
    assert conf >= 0.85, f"E13 should read high, got {conf:.3f}"


def test_decisive_mid_vote_match_reads_high():
    """S01E12: swept, 5/10 votes -> clearly high (>= 0.75), above review gate."""
    conf = _calibrate(0.180, 0.1795, 5)
    assert conf >= 0.75, f"E12 should clear review, got {conf:.3f}"


def test_decisive_low_vote_match_reads_high():
    """S01E14: swept, 4/10 votes -> still above the 0.7 review gate."""
    conf = _calibrate(0.180, 0.1804, 4)
    assert conf >= 0.70, f"E14 should clear review, got {conf:.3f}"


def test_all_real_ad_cases_far_above_raw():
    """Every real correct match must read far higher than its raw ~18%."""
    for score, gap, votes in [(0.165, 0.1652, 8), (0.180, 0.1795, 5), (0.180, 0.1804, 4)]:
        conf = _calibrate(score, gap, votes)
        assert conf > 0.5, f"raw≈{score} correct match still low: {conf:.3f}"


# --- The dangerous cases: ambiguity must stay in review -----------------------


def test_near_tie_needs_review():
    """Two episodes neck-and-neck (top1 0.18 vs top2 0.16) -> low confidence."""
    conf = _calibrate(0.18, 0.18 - 0.16, votes=6)
    assert conf < 0.3, f"near-tie must read low, got {conf:.3f}"


def test_two_x_contest_needs_review():
    """Winner beats #2 by 2x but #2 is real -> below auto/review gate (0.7)."""
    conf = _calibrate(0.20, 0.20 - 0.10, votes=7)
    assert conf < 0.7, f"2x contest should need review, got {conf:.3f}"


def test_weak_uncontested_sweep_is_borderline():
    """Barely-above-threshold sweep with minimal votes -> at/under review gate."""
    conf = _calibrate(0.15, 0.15, votes=2)
    assert conf < 0.75, f"weak thin sweep should be modest, got {conf:.3f}"


# --- Vote-ratio path ----------------------------------------------------------
# ranked_voting_score is a per-chunk mean cosine. Two candidates with similar
# per-chunk quality but very different chunk counts produce a small score_gap,
# so Path 1 alone under-reports confidence. Path 2 (vote-ratio) captures this.


def test_overwhelming_vote_ratio_clears_review():
    """Real Gilded Age S03E01: 103/119 winner votes, 31 runner-up, score_gap tiny.

    Without runner_up_votes the old formula gave ~0.20 (review). With vote_ratio
    this should auto-match (>= 0.70).
    """
    conf, _ = calibrate_confidence(
        score=0.203,
        score_gap=0.042,
        vote_count=103,
        target_votes=119,
        processed_coverage=1.0,
        runner_up_votes=31,
    )
    assert conf >= 0.70, f"103 vs 31 votes (3.3x) should clear review, got {conf:.3f}"


def test_close_vote_ratio_stays_review():
    """Winner barely ahead in raw votes (10 vs 8) -> ratio path not triggered."""
    conf, _ = calibrate_confidence(
        score=0.18,
        score_gap=0.04,
        vote_count=10,
        target_votes=25,
        processed_coverage=0.30,
        runner_up_votes=8,
    )
    assert conf < 0.70, f"1.25x vote ratio should not auto-match, got {conf:.3f}"


def test_vote_ratio_requires_minimum_consensus():
    """3/119 winner, 1/119 runner-up: 3x ratio but ~2% consensus -> still review."""
    conf, _ = calibrate_confidence(
        score=0.18,
        score_gap=0.04,
        vote_count=3,
        target_votes=119,
        processed_coverage=0.05,
        runner_up_votes=1,
    )
    assert conf < 0.70, f"low consensus (2.5%%) must block ratio path, got {conf:.3f}"


def test_vote_ratio_disabled_without_runner_up():
    """Default runner_up_votes=0 must give the same result as omitting the arg."""
    common = dict(
        score=0.18, score_gap=0.04, vote_count=10, target_votes=25, processed_coverage=0.30
    )
    conf_explicit, _ = calibrate_confidence(**common, runner_up_votes=0)
    conf_default, _ = calibrate_confidence(**common)
    assert conf_explicit == conf_default


# --- Monotonicity: the formula must move the right way ------------------------


def test_more_votes_increases_confidence():
    base = _calibrate(0.18, 0.18, votes=3)
    more = _calibrate(0.18, 0.18, votes=8)
    assert more > base


def test_bigger_separation_increases_confidence():
    """Same score/votes, a more decisive win reads more confident."""
    contested = _calibrate(0.20, 0.04, votes=6)  # top2 = 0.16
    decisive = _calibrate(0.20, 0.18, votes=6)  # top2 = 0.02
    assert decisive > contested


def test_longer_file_not_more_confident():
    """Guards the rejected score/coverage design: dividing by coverage made
    longer episodes (lower coverage) read MORE confident. Same score/gap/votes,
    a longer file (less processed) must read <= a shorter file."""
    short = _calibrate(0.18, 0.18, votes=6, coverage=10 * 30 / 1320)  # 22 min
    long = _calibrate(0.18, 0.18, votes=6, coverage=10 * 30 / 2640)  # 44 min
    assert long <= short


# --- Components & edges -------------------------------------------------------


def test_components_reported_and_in_range():
    """All components are present; all except vote_ratio are bounded [0, 1].

    vote_ratio is the raw winner/runner-up chunk count ratio — a diagnostic
    value intentionally not clamped (e.g. 103/31 = 3.32 for Gilded Age).
    vote_ratio_score is its [0, 1] projection used inside the formula.
    """
    bounded_keys = (
        "separation",
        "consensus",
        "normalized_score",
        "coverage",
        "evidence",
        "vote_boost",
        "effective_separation",
        "vote_ratio_score",
        "ratio_confidence",
    )
    all_keys = bounded_keys + ("vote_ratio",)

    # Path 1 only (no runner_up_votes) — vote_ratio stays 0.0
    conf, comp = calibrate_confidence(
        score=0.18, score_gap=0.18, vote_count=8, target_votes=10, processed_coverage=AD_COVERAGE
    )
    for key in all_keys:
        assert key in comp, f"missing component {key}"
    for key in bounded_keys:
        assert 0.0 <= comp[key] <= 1.0, f"{key} out of range: {comp[key]}"
    assert comp["vote_ratio"] == 0.0
    assert 0.0 <= conf <= 1.0

    # Path 2 active (Gilded Age anchor) — vote_ratio is raw (> 1 is expected)
    _, comp2 = calibrate_confidence(
        score=0.203,
        score_gap=0.042,
        vote_count=103,
        target_votes=119,
        processed_coverage=1.0,
        runner_up_votes=31,
    )
    for key in all_keys:
        assert key in comp2, f"missing component {key} (ratio path)"
    for key in bounded_keys:
        assert 0.0 <= comp2[key] <= 1.0, f"{key} out of range (ratio path): {comp2[key]}"
    assert comp2["vote_ratio"] > 1.0, "vote_ratio should be the raw >1 ratio when ratio path fires"


def test_zero_target_votes_is_safe():
    """No scan points -> no division-by-zero; consensus is 0 and conf stays valid.

    In practice zero votes means score is 0 too (score is the mean of voting
    chunks), so the realistic degenerate state yields 0 confidence; the point of
    this test is that target_votes=0 never raises.
    """
    conf, comp = calibrate_confidence(
        score=0.0, score_gap=0.0, vote_count=0, target_votes=0, processed_coverage=0.0
    )
    assert comp["consensus"] == 0.0
    assert 0.0 <= conf <= 1.0
    assert conf == 0.0


def test_zero_score_is_safe():
    conf, _ = calibrate_confidence(
        score=0.0, score_gap=0.0, vote_count=0, target_votes=10, processed_coverage=0.0
    )
    assert conf == 0.0


# --- _attach_calibrated_confidence wiring ------------------------------------


def _make_best_match(score, vote_count, target_votes=10):
    return {
        "season": 1,
        "episode": 13,
        "confidence": score,  # raw, pre-calibration
        "score": score,  # raw ranked_voting_score (must stay raw)
        "match_details": {
            "episode": "S1E13",
            "score": score,
            "vote_count": vote_count,
            "target_votes": target_votes,
        },
    }


def _results_summary(*pairs):
    """pairs = (episode_str, score, vote_count)."""
    return [{"episode": ep, "score": s, "vote_count": v, "target_votes": 10} for ep, s, v in pairs]


def test_attach_sets_calibrated_confidence_keeps_raw_score():
    best = _make_best_match(0.165, 8)
    rs = _results_summary(("S1E13", 0.165, 8))
    _attach_calibrated_confidence(best, rs, video_duration=1320)

    assert best["confidence"] >= 0.85  # calibrated headline
    assert best["score"] == 0.165  # raw signal preserved for accept-gate
    assert best["match_details"]["score"] == 0.165  # raw preserved for conflict resolution
    assert best["match_details"]["confidence"] == pytest.approx(best["confidence"])


def test_attach_winner_runner_up_equals_headline():
    """The winner's leaderboard entry confidence must equal the headline."""
    best = _make_best_match(0.18, 6)
    rs = _results_summary(("S1E13", 0.18, 6), ("S1E07", 0.05, 1))
    _attach_calibrated_confidence(best, rs, video_duration=1320)

    runner_ups = best["match_details"]["runner_ups"]
    winner_entry = runner_ups[0]
    assert winner_entry["episode"] == "S1E13"
    assert winner_entry["confidence"] == pytest.approx(best["confidence"])
    # raw score still present for conflict resolution / cascading reassignment
    assert winner_entry["score"] == 0.18


def test_attach_runner_up_confidence_scaled_below_winner():
    best = _make_best_match(0.18, 6)
    rs = _results_summary(("S1E13", 0.18, 6), ("S1E07", 0.09, 2))
    _attach_calibrated_confidence(best, rs, video_duration=1320)

    runner_ups = best["match_details"]["runner_ups"]
    assert runner_ups[1]["score"] == 0.09
    # half the winner's raw score -> ~half the winner's calibrated confidence
    assert runner_ups[1]["confidence"] == pytest.approx(best["confidence"] * 0.5, abs=0.02)
    assert runner_ups[1]["confidence"] < runner_ups[0]["confidence"]


def test_attach_runner_ups_not_shared_between_keys():
    """best_match['runner_ups'] and match_details['runner_ups'] must be distinct
    list objects so a downstream in-place mutation of one cannot corrupt the
    other (curator shallow-copies match_details but not the inner list)."""
    best = _make_best_match(0.18, 6)
    rs = _results_summary(("S1E13", 0.18, 6), ("S1E07", 0.09, 2))
    _attach_calibrated_confidence(best, rs, video_duration=1320)

    top_level = best["runner_ups"]
    nested = best["match_details"]["runner_ups"]
    assert top_level == nested  # same contents
    assert top_level is not nested  # but independent objects
    nested.append({"episode": "X", "score": 0.0, "confidence": 0.0})
    assert len(best["runner_ups"]) == 2  # unaffected by mutating the nested list


def test_attach_reports_independent_metrics():
    best = _make_best_match(0.165, 8)
    rs = _results_summary(("S1E13", 0.165, 8))
    _attach_calibrated_confidence(best, rs, video_duration=1320)
    md = best["match_details"]
    for key in (
        "separation",
        "vote_boost",
        "effective_separation",
        "normalized_score",
        "consensus",
        "coverage",
        "score_gap",
        "vote_ratio",
        "ratio_confidence",
    ):
        assert key in md, f"missing reported metric {key}"
