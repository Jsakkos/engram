"""Whisper and TF-IDF in the bound match language (#241, phase 2).

English must stay byte-identical: the same Whisper call, the same transcript keys
and the same vectorizer. Another language changes all three.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.match_language import match_language_context
from app.matcher import transcript_store
from app.matcher.asr_models import (
    FasterWhisperModel,
    model_output_key,
    set_asr_device,
    whisper_language,
)
from app.matcher.episode_identification import EpisodeMatcher, SubtitleCache, TfidfMatcher

CONFIG = {"type": "whisper", "name": "small", "device": "cpu"}


@pytest.fixture(autouse=True)
def _reset_asr_device_override():
    set_asr_device(None)
    yield
    set_asr_device(None)


class LanguageModel:
    """Records how Whisper was called; English is called exactly as before."""

    device = "cpu"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        return {"text": f" words from {Path(audio_path).stem} "}


def _matcher(tmp_path):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Show", model_name="tiny", device="cpu")
    m.extract_audio_chunk = lambda mkv, start, duration=None: str(tmp_path / f"c{start}.wav")
    return m


def _video(tmp_path):
    video = tmp_path / "t00.mkv"
    video.write_bytes(b"\x00" * 4096)
    return video


@pytest.mark.unit
class TestModelKey:
    def test_english_key_is_unchanged(self):
        assert model_output_key(CONFIG) == "whisper_small_cpu_int8"
        assert model_output_key(CONFIG, "en") == "whisper_small_cpu_int8"

    def test_another_language_gets_its_own_key(self):
        assert model_output_key(CONFIG, "de") == "whisper_small_cpu_int8_de"


@pytest.mark.unit
@pytest.mark.parametrize("code,expected", [("de", "de"), ("ja", "ja"), ("nb", "no"), ("osa", None)])
def test_whisper_language(code, expected):
    assert whisper_language(code) == expected


@pytest.mark.unit
def test_faster_whisper_is_told_the_language(tmp_path):
    seen = {}

    class Recording:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return iter([SimpleNamespace(start=0.0, end=1.0, text=" Hallo.")]), SimpleNamespace(
                language="de"
            )

    model = FasterWhisperModel(device="cpu")
    model._model = Recording()
    model._preprocess_audio = lambda path: path
    model.transcribe(tmp_path / "c.wav", language="de")
    assert seen["language"] == "de"
    model.transcribe(tmp_path / "c.wav")
    assert seen["language"] == "en"


@pytest.mark.unit
class TestChunkTranscription:
    def test_english_call_and_keys_unchanged(self, tmp_path):
        m, video, model = _matcher(tmp_path), _video(tmp_path), LanguageModel()
        m.transcribe_chunk_cached(video, 90, 30, model)

        assert model.calls == [{}]
        assert (m._source_key(video), 90, 30) in m.transcriptions
        file_key = m.transcript_file_key(video)
        assert transcript_store.get(file_key, 90, 30, "whisper_tiny_cpu_int8") is not None

    def test_german_is_transcribed_and_keyed_in_german(self, tmp_path):
        m, video, model = _matcher(tmp_path), _video(tmp_path), LanguageModel()
        with match_language_context("de"):
            m.transcribe_chunk_cached(video, 90, 30, model)

        assert model.calls == [{"language": "de"}]
        assert (m._source_key(video), 90, 30, "de") in m.transcriptions
        file_key = m.transcript_file_key(video)
        assert transcript_store.get(file_key, 90, 30, "whisper_tiny_cpu_int8_de") is not None
        assert transcript_store.get(file_key, 90, 30, "whisper_tiny_cpu_int8") is None

    def test_an_english_transcript_is_never_reused_for_german(self, tmp_path):
        m, video, model = _matcher(tmp_path), _video(tmp_path), LanguageModel()
        m.transcribe_chunk_cached(video, 90, 30, model)
        with match_language_context("de"):
            m.transcribe_chunk_cached(video, 90, 30, model)
        assert model.calls == [{}, {"language": "de"}]


def _references(tmp_path, texts):
    files = []
    for i, text in enumerate(texts, 1):
        srt = tmp_path / f"Show - S01E{i:02d}.srt"
        srt.write_text(f"1\n00:00:01,000 --> 00:00:05,000\n{text}\n\n", "utf-8")
        files.append(srt)
    return files


JAPANESE = [
    "東京の駅で待ち合わせをした。彼女は遅れて来た。電車が止まっていたらしい。",
    "海辺の町で祭りがあった。花火が夜空に上がった。子供たちが走り回っていた。",
    "病院の廊下で医者に呼ばれた。検査の結果はまだ出ていないと言われた。",
]


@pytest.mark.unit
class TestTfidfAnalyzer:
    def test_english_uses_the_word_analyzer(self, tmp_path):
        tm = TfidfMatcher()
        tm.prepare(_references(tmp_path, ["one two", "three four"]), SubtitleCache())
        assert (tm.vectorizer.analyzer, tm.vectorizer.ngram_range) == ("word", (1, 2))

    def test_japanese_uses_character_ngrams_and_ranks_the_right_episode(self, tmp_path):
        tm = TfidfMatcher()
        tm.prepare(_references(tmp_path, JAPANESE), SubtitleCache(), "ja")
        assert (tm.vectorizer.analyzer, tm.vectorizer.ngram_range) == ("char_wb", (2, 4))

        # Whisper's wording never lines up with the subtitle's sentences: the
        # word analyzer reads each unspaced sentence as one token and finds nothing.
        heard = "夜空の花火を見た"
        best, _ = tm.match(heard)[0]
        assert Path(best).stem.endswith("S01E02")

        words = TfidfMatcher()
        words.prepare(_references(tmp_path, JAPANESE), SubtitleCache())
        assert all(score == 0 for _, score in words.match(heard))

    def test_matcher_cache_keeps_languages_apart(self, tmp_path):
        m = _matcher(tmp_path)
        refs = _references(tmp_path, JAPANESE)
        english = m._get_tfidf_matcher(("sig",), using_precomputed=False, reference_files=refs)
        with match_language_context("ja"):
            japanese = m._get_tfidf_matcher(("sig",), using_precomputed=False, reference_files=refs)
        assert english is not japanese
        assert japanese.vectorizer.analyzer == "char_wb"
