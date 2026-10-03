"""ASR audio input must reach faster-whisper as an in-memory array, never a file path.

Given a path, faster-whisper decodes it with its own ``decode_audio``, which calls
``av.open(..., metadata_errors="ignore")``. PyAV 19 removed that keyword, so every
chunk raised ``TypeError`` and ASR matching produced zero characters on every disc
(issue #709). Handing over the already-decoded array skips ``decode_audio`` and keeps
PyAV off the ASR path entirely.
"""

from types import SimpleNamespace

import av
import numpy as np
import pytest
import soundfile as sf

from app.matcher.asr_models import FasterWhisperModel


def _write_tone(path, *, sr=44100, seconds=1.0, amplitude=0.25, channels=1):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    data = np.stack([tone] * channels, axis=1) if channels > 1 else tone
    sf.write(str(path), data, sr)
    return path


class _RecordingWhisper:
    """Stands in for ``faster_whisper.WhisperModel``; records what it was handed."""

    def __init__(self):
        self.audio = None

    def transcribe(self, audio, **kwargs):
        self.audio = audio
        segment = SimpleNamespace(start=0.0, end=1.0, text=" Hello there.")
        return iter([segment]), SimpleNamespace(language="en")


@pytest.mark.unit
class TestPreprocessAudio:
    def test_returns_16khz_mono_float32_array(self, tmp_path):
        wav = _write_tone(tmp_path / "chunk.wav", sr=44100, seconds=1.0, channels=2)

        audio = FasterWhisperModel(device="cpu")._preprocess_audio(wav)

        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert audio.ndim == 1
        assert abs(len(audio) - 16000) <= 1
        assert np.max(np.abs(audio)) == pytest.approx(1.0, abs=1e-3)

    def test_silence_is_not_normalized_into_nan(self, tmp_path):
        wav = _write_tone(tmp_path / "silence.wav", sr=16000, amplitude=0.0)

        audio = FasterWhisperModel(device="cpu")._preprocess_audio(wav)

        assert not np.isnan(audio).any()
        assert np.max(np.abs(audio)) == 0.0

    def test_writes_no_temp_file(self, tmp_path, monkeypatch):
        # The old path wrote a preprocessed WAV to the system temp dir for
        # faster-whisper to re-decode; nothing should touch disk now.
        wav = _write_tone(tmp_path / "chunk.wav")
        writes = []
        monkeypatch.setattr(sf, "write", lambda *a, **k: writes.append(a))

        FasterWhisperModel(device="cpu")._preprocess_audio(wav)

        assert writes == []


@pytest.mark.unit
class TestTranscribeHandsOffArray:
    def test_model_receives_ndarray_not_path(self, tmp_path):
        wav = _write_tone(tmp_path / "chunk.wav")
        model = FasterWhisperModel(device="cpu")
        model._model = _RecordingWhisper()

        result = model.transcribe(wav)

        assert isinstance(model._model.audio, np.ndarray)
        assert model._model.audio.dtype == np.float32
        assert result["raw_text"] == "Hello there."

    def test_unreadable_audio_returns_empty_result(self, tmp_path):
        bogus = tmp_path / "not_audio.wav"
        bogus.write_bytes(b"definitely not a wav file")
        model = FasterWhisperModel(device="cpu")
        model._model = _RecordingWhisper()

        result = model.transcribe(bogus)

        assert result["text"] == ""
        assert model._model.audio is None


@pytest.mark.unit
@pytest.mark.xfail(
    int(av.__version__.split(".")[0]) >= 19,
    reason=(
        "faster-whisper 1.2.1 passes av.open(metadata_errors=...), removed in PyAV 19 "
        "(#709). Engram no longer calls decode_audio; if this XPASSes, upstream fixed it "
        "and the marker can go."
    ),
    raises=TypeError,
    strict=True,
)
def test_faster_whisper_decode_audio_compat(tmp_path):
    """Canary for faster-whisper/PyAV drift: flags when upstream catches up (or breaks)."""
    from faster_whisper.audio import decode_audio

    wav = _write_tone(tmp_path / "chunk.wav", sr=16000)

    audio = decode_audio(str(wav), sampling_rate=16000)

    assert audio.dtype == np.float32
    assert abs(len(audio) - 16000) <= 1
