"""Unit tests for srt_utils text-cleaning helpers and the SRT cue parser."""

import re

import pytest

from app.matcher.srt_utils import clean_text, has_srt_cues, iter_srt_cues, parse_srt_timestamp


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

    def test_leaves_unclosed_annotation_words(self):
        # No closing delimiter -> not stripped. Unlike _clean_subtitle_text,
        # clean_text has no special-char scrub, so the stray "{" survives too.
        assert clean_text("{ Scoffs I haven't slept") == "{ scoffs i haven't slept"


_CLEAN_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nDexter, get out of my lab!\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nOmelette du fromage.\nOmelette du fromage.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nDee Dee!\n"
)
_EXPECTED_CUES = [
    (1.0, 3.0, ("Dexter, get out of my lab!",)),
    (4.0, 6.5, ("Omelette du fromage.", "Omelette du fromage.")),
    (7.0, 9.0, ("Dee Dee!",)),
]
_NO_INDEX_SRT = re.sub(r"(?m)^\d+\n(?=\d{2}:)", "", _CLEAN_SRT)
_TIMING_WITHOUT_TEXT = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\n"
_AD_CUE = "0\n00:00:00,000 --> 00:00:00,500\nSubtitles by SITE\n\n"
_AD_EXPECTED = (0.0, 0.5, ("Subtitles by SITE",))


def _cue_tuples(content):
    return [(cue.start, cue.end, cue.lines) for cue in iter_srt_cues(content)]


@pytest.mark.unit
class TestIterSrtCues:
    @pytest.mark.parametrize(
        "variant",
        [
            pytest.param(_CLEAN_SRT, id="lf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r\n"), id="crlf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r\r\n"), id="cr-cr-lf"),
            pytest.param(_CLEAN_SRT.replace("\n", "\n\n"), id="doubled-after-text-read"),
            pytest.param(_CLEAN_SRT.replace("\n\n", "\n \n"), id="whitespace-separators"),
            pytest.param("\ufeff" + _CLEAN_SRT, id="bom"),
            pytest.param(_NO_INDEX_SRT, id="no-index-lines"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r"), id="cr-only"),
            pytest.param(_CLEAN_SRT.replace("\n", "\r\r\r\n"), id="tripled"),
        ],
    )
    def test_damaged_layouts_yield_the_clean_cues(self, variant):
        assert _cue_tuples(variant) == _EXPECTED_CUES

    def test_malformed_timing_line_drops_only_that_cue(self):
        content = _CLEAN_SRT.replace("00:00:04,000 --> 00:00:06,500", "00:00:04 -> bad -->")
        assert _cue_tuples(content) == [_EXPECTED_CUES[0], _EXPECTED_CUES[2]]

    def test_missing_blank_separator_splits_on_the_next_timing_line(self):
        content = (
            "1\n00:00:01,000 --> 00:00:03,000\nFirst line\n"
            "2\n00:00:04,000 --> 00:00:05,000\nSecond line\n"
        )
        assert _cue_tuples(content) == [
            (1.0, 3.0, ("First line",)),
            (4.0, 5.0, ("Second line",)),
        ]

    def test_text_before_the_first_timing_line_is_ignored(self):
        assert _cue_tuples("Subtitles by someone\n\n" + _CLEAN_SRT) == _EXPECTED_CUES

    def test_trailing_block_without_timing_is_ignored(self):
        assert _cue_tuples(_CLEAN_SRT + "\nDownloaded from somewhere\n") == _EXPECTED_CUES

    def test_dot_millisecond_separator(self):
        assert _cue_tuples("00:00:01.250 --> 00:00:02.500\nHi\n") == [(1.25, 2.5, ("Hi",))]

    def test_empty_and_none_content(self):
        assert _cue_tuples("") == []
        assert _cue_tuples(None) == []

    def test_doubled_file_with_a_single_spaced_header_keeps_its_cues(self):
        content = "Synced by X\nwww.x.com\n\n" + _CLEAN_SRT.replace("\n", "\r\r\n")
        assert _cue_tuples(content) == _EXPECTED_CUES

    def test_doubled_file_with_a_single_spaced_trailer_keeps_its_cues(self):
        doubled = _CLEAN_SRT.replace("\n", "\r\r\n")
        cues = _cue_tuples(doubled + "Downloaded from example\nwww.example.com\n")
        assert len(cues) == 3
        assert cues[:2] == _EXPECTED_CUES[:2]
        assert cues[2][:2] == (7.0, 9.0)
        assert cues[2][2][0] == "Dee Dee!"

    def test_dialogue_containing_an_arrow_is_kept(self):
        content = "1\n00:00:01,000 --> 00:00:02,000\nGo --> there\n"
        assert _cue_tuples(content) == [(1.0, 2.0, ("Go --> there",))]

    def test_hours_above_nine(self):
        content = "10:00:01,500 --> 10:00:02,000\nLate\n"
        assert _cue_tuples(content) == [(36001.5, 36002.0, ("Late",))]

    def test_doubled_file_with_a_single_spaced_cue_first_keeps_every_cue(self):
        content = _AD_CUE + _CLEAN_SRT.replace("\n", "\r\r\n")
        assert _cue_tuples(content) == [_AD_EXPECTED, *_EXPECTED_CUES]

    def test_doubled_file_with_a_single_spaced_cue_last_keeps_every_cue(self):
        trailer = "4\n00:09:00,000 --> 00:09:01,000\nSupport us at SITE\n"
        content = _CLEAN_SRT.replace("\n", "\r\r\n") + trailer
        assert _cue_tuples(content) == [
            *_EXPECTED_CUES,
            (540.0, 541.0, ("Support us at SITE",)),
        ]

    def test_gap_tie_prefers_the_wider_layout(self):
        single = "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
        doubled = "2\n00:00:03,000 --> 00:00:04,000\nWorld\n".replace("\n", "\r\r\n")
        assert _cue_tuples(single + doubled) == [
            (1.0, 2.0, ("Hello",)),
            (3.0, 4.0, ("World",)),
        ]

    def test_textless_and_numeric_cues_do_not_set_the_gap(self):
        content = (
            "1\n00:00:01,000 --> 00:00:02,000\n42\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nHello there\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nGeneral Kenobi\n\n"
            "4\n00:00:07,000 --> 00:00:08,000\n"
        ).replace("\n", "\r\r\n")
        assert _cue_tuples(content) == [
            (1.0, 2.0, ("42",)),
            (3.0, 4.0, ("Hello there",)),
            (5.0, 6.0, ("General Kenobi",)),
            (7.0, 8.0, ()),
        ]

    def test_blank_line_before_text_in_a_normal_file_empties_only_that_cue(self):
        content = _CLEAN_SRT.replace("00:00:03,000\nDexter", "00:00:03,000\n\nDexter")
        assert _cue_tuples(content) == [(1.0, 3.0, ()), *_EXPECTED_CUES[1:]]

    def test_single_digit_minute_and_second_fields(self):
        # Real cached subtitles (e.g. Castle S01E07) write "00:00:0,616"; the
        # previous parser read them, so rejecting them would silently drop dialogue.
        content = (
            "1\n00:00:0,616 --> 00:00:2,784\nFirst line\n\n"
            "2\n00:00:59,173 --> 00:01:0,187\nSecond line\n\n"
            "3\n00:01:2,226 --> 00:01:3,627\nThird line\n"
        )
        cues = _cue_tuples(content)
        assert [cue[2] for cue in cues] == [("First line",), ("Second line",), ("Third line",)]
        assert cues[0][:2] == pytest.approx((0.616, 2.784))
        assert cues[1][:2] == pytest.approx((59.173, 60.187))
        assert cues[2][:2] == pytest.approx((62.226, 63.627))

    def test_single_digit_timing_line_after_a_textless_cue_is_not_measured_as_text(self):
        content = (
            "1\n00:00:01,000 --> 00:00:02,000\n\n"
            "2\n00:00:3,000 --> 00:00:4,000\nHello there\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nGeneral Kenobi\n"
        ).replace("\n", "\r\r\n")
        assert _cue_tuples(content) == [
            (1.0, 2.0, ()),
            (3.0, 4.0, ("Hello there",)),
            (5.0, 6.0, ("General Kenobi",)),
        ]


@pytest.mark.unit
class TestHasSrtCues:
    def test_true_for_dialogue(self):
        assert has_srt_cues(_CLEAN_SRT) is True

    def test_true_for_doubled_line_endings(self):
        assert has_srt_cues(_CLEAN_SRT.replace("\n", "\r\r\n")) is True

    def test_false_for_timing_without_text(self):
        assert has_srt_cues(_TIMING_WITHOUT_TEXT) is False

    def test_false_for_plain_text(self):
        assert has_srt_cues("just some notes with no timing") is False


@pytest.mark.unit
def test_parse_srt_timestamp_accepts_comma_and_dot():
    assert parse_srt_timestamp("01:02:03,500") == pytest.approx(3723.5)
    assert parse_srt_timestamp(" 00:00:07.25 ") == pytest.approx(7.25)
