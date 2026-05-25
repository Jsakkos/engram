"""Unit tests for srt_utils text-cleaning helpers."""

import pytest

from app.matcher.srt_utils import clean_text


@pytest.mark.unit
class TestCleanText:
    def test_lowercases_and_strips(self):
        assert clean_text("  Hello WORLD  ") == "hello world"

    def test_removes_tags_and_brackets(self):
        assert clean_text("Hi <i>there</i> [music]") == "hi there"

    def test_collapses_stutters(self):
        assert clean_text("I-I think") == "i think"

    def test_strips_mismatched_open_brace_annotation(self):
        # Mirror of the matcher path: "{ Sighs]" style annotations from sources
        # like tvsubtitles.net must be stripped despite the mismatched delimiters.
        assert clean_text("{ Sighs] Hello there") == "hello there"
