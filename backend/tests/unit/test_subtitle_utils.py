"""Unit tests for subtitle_utils.is_valid_srt_file."""

import pytest

from app.matcher.subtitle_utils import is_valid_srt_content, is_valid_srt_file

_SRT_TEXT = "1\r\n00:00:07,130 --> 00:00:09,000\nHello there\n\n2\r\n00:00:10,000 --> 00:00:12,000\nGeneral Kenobi\n"


@pytest.mark.unit
class TestIsValidSrtFile:
    def test_accepts_plain_utf8_srt(self, tmp_path):
        p = tmp_path / "utf8.srt"
        p.write_text(_SRT_TEXT, encoding="utf-8")
        assert is_valid_srt_file(p) is True

    def test_accepts_utf16_srt_with_bom(self, tmp_path):
        """TVsubtitles (and others) sometimes serve UTF-16-encoded SRTs.
        Read as UTF-8 they keep a NUL between every character, so the ASCII
        ``-->`` check never matches and a valid subtitle is wrongly rejected.
        The validator must decode by BOM."""
        p = tmp_path / "utf16.srt"
        p.write_text(_SRT_TEXT, encoding="utf-16")  # writes a BOM
        assert is_valid_srt_file(p) is True

    def test_rejects_html(self, tmp_path):
        p = tmp_path / "html.srt"
        p.write_text("<!DOCTYPE html><html><body>Not found</body></html>" * 5, encoding="utf-8")
        assert is_valid_srt_file(p) is False

    def test_rejects_too_short(self, tmp_path):
        p = tmp_path / "tiny.srt"
        p.write_text("1\n", encoding="utf-8")
        assert is_valid_srt_file(p) is False

    def test_rejects_text_without_timestamps(self, tmp_path):
        p = tmp_path / "notes.srt"
        p.write_text(
            "just some plain notes with no subtitle timing at all here" * 2, encoding="utf-8"
        )
        assert is_valid_srt_file(p) is False


@pytest.mark.unit
class TestIsValidSrtContent:
    def test_accepts_plain_srt_text(self):
        content = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"
        assert is_valid_srt_content(content) is True

    def test_rejects_html(self):
        content = "<!DOCTYPE html><html><body>Not a subtitle</body></html>" + "x" * 60
        assert is_valid_srt_content(content) is False

    def test_rejects_too_short(self):
        assert is_valid_srt_content("short") is False

    def test_rejects_text_without_timestamps(self):
        content = "Just some plain text with no SRT timing markers at all here." * 2
        assert is_valid_srt_content(content) is False
