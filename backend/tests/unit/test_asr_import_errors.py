"""A faster-whisper import failure must report its real cause (#760).

In a frozen build faster-whisper is bundled, yet job 100 logged "faster-whisper not
installed" because every ``ImportError`` was rewritten to that message. A broken
transitive import (CUDA DLL load, ctranslate2, av) looks identical to a missing package
unless the original exception is kept.
"""

import sys
import types

import pytest

from app.matcher.asr_models import FasterWhisperModel, list_available_models


def _model():
    return FasterWhisperModel("small", device="cpu")


def test_missing_package_still_says_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(ImportError, match="faster-whisper not installed") as exc:
        _model().load()
    assert isinstance(exc.value.__cause__, ImportError)


def test_broken_import_keeps_underlying_error(monkeypatch):
    # An installed package whose import fails for another reason: no WhisperModel export.
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    with pytest.raises(ImportError) as exc:
        _model().load()
    message = str(exc.value)
    assert "not installed" not in message
    assert "cannot import name 'WhisperModel'" in message
    assert "ImportError" in message
    assert exc.value.__cause__ is not None


def test_missing_transitive_dependency_is_not_reported_as_not_installed(monkeypatch):
    fake = types.ModuleType("faster_whisper")

    def _raise(name):
        raise ModuleNotFoundError("No module named 'ctranslate2'", name="ctranslate2")

    fake.__getattr__ = _raise
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    with pytest.raises(ImportError) as exc:
        _model().load()
    assert "not installed" not in str(exc.value)
    assert "ctranslate2" in str(exc.value)


def test_availability_reports_real_cause(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    info = list_available_models()["whisper"]
    assert info["available"] is False
    assert "not installed" not in info["error"]
    assert "WhisperModel" in info["error"] or "faster_whisper" in info["error"]


def test_availability_missing_package_says_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    info = list_available_models()["whisper"]
    assert info["available"] is False
    assert "not installed" in info["error"]
