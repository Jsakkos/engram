"""Unit tests for subtitle_utils.is_valid_srt_file."""

import pytest

from app.matcher.subtitle_utils import is_valid_srt_content, is_valid_srt_file

_SRT_TEXT = "1\r\n00:00:07,130 --> 00:00:09,000\nHello there\n\n2\r\n00:00:10,000 --> 00:00:12,000\nGeneral Kenobi\n"

_TIMING_WITHOUT_TEXT = (
    "1\n00:00:01,000 --> 00:00:02,000\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\n"
)


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

    def test_rejects_timing_lines_with_no_dialogue(self, tmp_path):
        p = tmp_path / "blank.srt"
        p.write_bytes(_TIMING_WITHOUT_TEXT.encode("utf-8"))
        assert is_valid_srt_file(p) is False

    def test_accepts_doubled_line_endings(self, tmp_path):
        p = tmp_path / "doubled.srt"
        clean = _SRT_TEXT.replace("\r\n", "\n")
        p.write_bytes(clean.replace("\n", "\r\r\n").encode("utf-8"))
        assert is_valid_srt_file(p) is True

    def test_accepts_cues_without_index_lines(self, tmp_path):
        p = tmp_path / "noindex.srt"
        p.write_bytes(
            b"00:00:01,000 --> 00:00:02,000\nHello there\n\n"
            b"00:00:03,000 --> 00:00:04,000\nGeneral Kenobi\n"
        )
        assert is_valid_srt_file(p) is True

    def test_accepts_dialogue_with_garbage_end_times(self, tmp_path):
        # Malcolm in the Middle S02 references in real caches look like this; they
        # must stay valid, or the download pass deletes a readable subtitle.
        p = tmp_path / "malcolm.srt"
        p.write_bytes(
            b"1\r\n00:00:00,000 --> 107:40:37,608\r\nwww.tvsubtitles.net\r\n\r\n"
            b"2\r\n00:00:02,000 --> 446:12:46,016\r\nExpired on Monday.\r\n"
        )
        assert is_valid_srt_file(p) is True


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


@pytest.mark.unit
class TestIsValidSrtContentCues:
    def test_rejects_timing_without_text(self):
        assert is_valid_srt_content(_TIMING_WITHOUT_TEXT) is False

    def test_accepts_doubled_line_endings(self):
        content = "1\n\n00:00:01,000 --> 00:00:02,000\n\nHello there, General Kenobi\n\n"
        assert is_valid_srt_content(content) is True
