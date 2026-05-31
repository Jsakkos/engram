"""Cascade decision logic: chromaprint-first accept, ASR fallback, cross-validation, flag-off no-op."""

from pathlib import Path

import pytest

from app.core.curator import EpisodeCurator, MatchResult


def _setup(monkeypatch):
    """A curator that passes match_single_file's guards (matcher present, init stubbed)."""
    curator = EpisodeCurator()
    curator._matcher = object()  # truthy; real matcher unused because prepass/asr are mocked
    monkeypatch.setattr(
        EpisodeCurator, "_ensure_initialized", lambda self, name, tmdb_id=None: True
    )
    return curator


def _cp_result(season, episode, confidence, tier="canonical"):
    return {
        "season": season,
        "episode": episode,
        "confidence": confidence,
        "score": confidence,
        "tier": tier,
        "match_details": {
            "match_source": "chromaprint",
            "chromaprint_signal": {
                "hash_overlap": 0.95,
                "temporal_coherence": 0.9,
                "rarity_weighted_score": 0.9,
            },
        },
        "runner_ups": [],
    }


def _asr_result(code, confidence=0.8):
    return MatchResult(
        file_path=Path("/x/ep.mkv"),
        episode_code=code,
        episode_title=None,
        confidence=confidence,
        needs_review=False,
        match_details={"match_source": "engram_asr"},
    )


@pytest.mark.asyncio
async def test_canonical_high_confidence_accepts_without_asr(monkeypatch):
    curator = _setup(monkeypatch)

    async def fake_prepass(self, **kwargs):
        return _cp_result(1, 3, 0.93)  # canonical + >= 0.90 gate

    async def asr_must_not_run(self, *a, **k):
        raise AssertionError("ASR must not run on a confident canonical chromaprint hit")

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", fake_prepass)
    monkeypatch.setattr(EpisodeCurator, "_run_asr_identify", asr_must_not_run)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.episode_code == "S01E03"
    assert result.needs_review is False
    assert result.match_details.get("chromaprint_accepted") is True
    assert result.match_details.get("match_source") == "engram_chromaprint"


@pytest.mark.asyncio
async def test_below_gate_falls_through_and_agrees(monkeypatch):
    curator = _setup(monkeypatch)

    async def fake_prepass(self, **kwargs):
        return _cp_result(1, 3, 0.93, tier="confirmed")  # not canonical -> does NOT auto-accept

    async def fake_asr(self, *a, **k):
        return _asr_result("S01E03", 0.82)  # agrees with chromaprint

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", fake_prepass)
    monkeypatch.setattr(EpisodeCurator, "_run_asr_identify", fake_asr)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.episode_code == "S01E03"
    assert result.needs_review is False
    assert result.match_details.get("chromaprint_asr_agreement") is True


@pytest.mark.asyncio
async def test_conflict_forces_review(monkeypatch):
    curator = _setup(monkeypatch)

    async def fake_prepass(self, **kwargs):
        return _cp_result(1, 3, 0.93, tier="confirmed")  # falls through (not canonical)

    async def fake_asr(self, *a, **k):
        return _asr_result("S01E07", 0.8)  # DISAGREES

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", fake_prepass)
    monkeypatch.setattr(EpisodeCurator, "_run_asr_identify", fake_asr)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.needs_review is True
    assert "chromaprint_vs_asr_conflict" in (result.match_details or {})
    assert result.episode_code == "S01E07"  # ASR's answer is surfaced for review


@pytest.mark.asyncio
async def test_flag_off_is_pure_asr(monkeypatch):
    """When prepass returns None (flag off), the result is exactly the ASR result, no cross-validation."""
    curator = _setup(monkeypatch)

    async def prepass_none(self, **kwargs):
        return None

    async def fake_asr(self, *a, **k):
        return _asr_result("S01E05", 0.77)

    monkeypatch.setattr(EpisodeCurator, "_chromaprint_prepass", prepass_none)
    monkeypatch.setattr(EpisodeCurator, "_run_asr_identify", fake_asr)

    result = await curator.match_single_file(Path("/x/ep.mkv"), "Some Show", 1)
    assert result.episode_code == "S01E05"
    assert result.needs_review is False
    assert "chromaprint_vs_asr_conflict" not in (result.match_details or {})
    assert "chromaprint_accepted" not in (result.match_details or {})
