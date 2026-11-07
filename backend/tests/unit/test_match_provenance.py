"""A match result records the language and audio stream it came from (#241, phase 2)."""

from types import SimpleNamespace

import pytest

import app.matcher.episode_identification as ei
from app.core.match_language import match_language_context
from app.matcher.audio_streams import AudioPlan, AudioStream
from app.matcher.episode_identification import EpisodeMatcher


def _matcher(tmp_path, monkeypatch, plan):
    e01, e02 = (str(tmp_path / f"Show - S01E0{i}.srt") for i in (1, 2))
    tfidf = SimpleNamespace(
        match=lambda text: [(e01, 0.30), (e02, 0.05)], ref_file_order=[e01, e02]
    )
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Show", device="cpu")
    m._load_precomputed_season = lambda season: None
    m.get_reference_files = lambda season, **kwargs: [e01, e02]
    m._get_tfidf_matcher = lambda *a, **k: tfidf
    m.transcribe_chunk_cached = lambda video, start, *a, **k: f"transcript at {start}"
    m._match_full_file = lambda *a, **k: None
    m.audio_plan = lambda video_file, language=None: plan
    monkeypatch.setattr(ei, "get_video_duration", lambda *a, **k: 1800.0)
    return m


@pytest.mark.unit
def test_result_records_language_and_stream(tmp_path, monkeypatch):
    german = AudioStream(1, "de", "", 2, False, False)
    m = _matcher(tmp_path, monkeypatch, AudioPlan("match", german, auto_index=0))
    with match_language_context("de"):
        result = m.identify_episode(tmp_path / "disc_t00.mkv", tmp_path, season_number=1)
    assert result["episode"] == 1
    assert result["match_details"]["match_language"] == "de"
    assert result["match_details"]["audio_stream"] == 1


@pytest.mark.unit
def test_unprobed_english_result_records_no_stream(tmp_path, monkeypatch):
    m = _matcher(tmp_path, monkeypatch, AudioPlan("unprobed"))
    result = m.identify_episode(tmp_path / "disc_t00.mkv", tmp_path, season_number=1)
    assert result["match_details"]["match_language"] == "en"
    assert result["match_details"]["audio_stream"] is None
