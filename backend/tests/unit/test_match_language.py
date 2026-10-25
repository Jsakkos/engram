"""The match-language context (#241, phase 2): folders, the precomputed pack, TMDB codes."""

import pytest

from app.core.episode_namespace import namespace_context
from app.core.match_language import (
    content_language,
    current_match_language,
    is_local_only_corpus_dir,
    language_dir_suffix,
    match_language_context,
)
from app.matcher import episode_identification
from app.matcher.subtitle_utils import corpus_dir_name


def test_context_defaults_to_english_and_nests():
    assert current_match_language() == "en"
    assert language_dir_suffix() == ""
    with match_language_context("de"):
        assert current_match_language() == "de"
        assert language_dir_suffix() == "@de"
        with match_language_context("en"):
            assert language_dir_suffix() == ""
    assert current_match_language() == "en"


@pytest.mark.parametrize("raw,expected", [("ger", "de"), ("deu", "de"), ("JA", "ja"), (None, "en")])
def test_context_normalizes_codes(raw, expected):
    with match_language_context(raw):
        assert current_match_language() == expected


@pytest.mark.parametrize("raw", ["und", "", "qaa"])
def test_unknown_codes_bind_english(raw):
    with match_language_context(raw):
        assert current_match_language() == "en"


def test_english_folder_is_unchanged():
    assert corpus_dir_name(70523, "Babylon Berlin") == "70523"
    with match_language_context("en"):
        assert corpus_dir_name(70523, "Babylon Berlin") == "70523"
        assert corpus_dir_name(None, "Babylon Berlin") == "Babylon Berlin"


def test_language_folder_suffix():
    with match_language_context("de"):
        assert corpus_dir_name(70523, "Babylon Berlin") == "70523@de"
        assert corpus_dir_name(None, "Babylon Berlin") == "Babylon Berlin@de"


def test_namespace_suffix_comes_first():
    with namespace_context("tvdb"), match_language_context("de"):
        assert corpus_dir_name(70523, "Babylon Berlin") == "70523@tvdb@de"


def test_precomputed_manifest_hidden_for_another_language(tmp_path):
    (tmp_path / "precomputed").mkdir()
    (tmp_path / "precomputed" / "manifest.json").write_text("{}", "utf-8")
    with match_language_context("de"):
        assert episode_identification.load_precomputed_manifest(tmp_path) is None


def test_matcher_instance_cache_does_not_leak_english_manifest():
    matcher = episode_identification.EpisodeMatcher.__new__(episode_identification.EpisodeMatcher)
    matcher._precomputed_manifest = {"cached": "english-manifest"}
    with match_language_context("de"):
        assert matcher._load_precomputed_manifest() is None
    assert matcher._load_precomputed_manifest() == {"cached": "english-manifest"}


@pytest.mark.parametrize(
    "details,expected",
    [
        ({"original_language": "de"}, "de"),
        ({"original_language": "en"}, "en"),
        ({"original_language": "cn"}, "zh"),
        ({"original_language": "xx"}, None),
        ({"original_language": ""}, None),
        ({}, None),
        (None, None),
    ],
)
def test_content_language_from_tmdb(details, expected):
    assert content_language(details) == expected


@pytest.mark.parametrize(
    "name,local_only",
    [("70523", False), ("Babylon Berlin", False), ("70523@tvdb", True), ("70523@de", True)],
)
def test_local_only_corpus_dirs(name, local_only):
    assert is_local_only_corpus_dir(name) is local_only
