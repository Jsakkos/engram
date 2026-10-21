"""Unit tests for disc language-code normalization (issue #241)."""

import pytest

from app.core.languages import is_english, language_name, normalize_language


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("eng", "en"),
        ("EN", "en"),
        ("jpn", "ja"),
        ("fre", "fr"),
        ("fra", "fr"),
        ("ger", "de"),
        ("deu", "de"),
        ("chi", "zh"),
        ("zho", "zh"),
        ("spa", "es"),
        (" de ", "de"),
        ("osa", "osa"),
        ("", None),
        (None, None),
        ("und", None),
        ("zxx", None),
        ("mul", None),
        ("mis", None),
        ("qaa", None),
        ("qtz", None),
    ],
)
def test_normalize_language(raw, expected):
    assert normalize_language(raw) == expected


def test_language_name():
    assert language_name("ja") == "Japanese"
    assert language_name("jpn") == "Japanese"
    assert language_name("osa") == "OSA"
    assert language_name(None) == "an unknown language"


def test_is_english():
    assert is_english("eng")
    assert is_english("en")
    assert not is_english("jpn")
    assert not is_english(None)
