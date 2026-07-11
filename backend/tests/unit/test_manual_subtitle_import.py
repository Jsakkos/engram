"""Unit tests for manual subtitle bulk-import preview/commit logic."""

from unittest.mock import patch

from app.matcher.manual_subtitle_import import (
    MAX_CONTENT_BYTES,
    PreviewInputFile,
    classify_files,
)

VALID_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there, General Kenobi\n"


class TestClassifyFiles:
    def test_ready_when_unparseable_slot_is_missing(self, tmp_path):
        files = [PreviewInputFile(filename="Show.Name.S01E05.srt", content=VALID_SRT)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert len(results) == 1
        assert results[0].season == 1
        assert results[0].episode == 5
        assert results[0].status == "ready"
        assert results[0].warning is None

    def test_already_covered_when_reference_exists(self, tmp_path):
        files = [PreviewInputFile(filename="Show.Name.S01E02.srt", content=VALID_SRT)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E02": "downloaded"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "already_covered"

    def test_unparseable_filename(self, tmp_path):
        files = [PreviewInputFile(filename="no_episode_info.srt", content=VALID_SRT)]
        results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "unparseable"
        assert results[0].season is None
        assert results[0].episode is None

    def test_out_of_range_season_is_unparseable(self, tmp_path):
        files = [PreviewInputFile(filename="Show.S99E01.srt", content=VALID_SRT)]
        results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "unparseable"

    def test_invalid_content_rejected(self, tmp_path):
        files = [PreviewInputFile(filename="Show.S01E01.srt", content="not really a subtitle" * 5)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E01": "missing"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "invalid_content"

    def test_content_too_large_rejected(self, tmp_path):
        oversized = VALID_SRT + ("x" * (MAX_CONTENT_BYTES + 1))
        files = [PreviewInputFile(filename="Show.S01E01.srt", content=oversized)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E01": "missing"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "invalid_content"

    def test_duplicate_within_batch(self, tmp_path):
        files = [
            PreviewInputFile(filename="Show.S01E05.srt", content=VALID_SRT),
            PreviewInputFile(filename="Show.S01E05.alt.srt", content=VALID_SRT),
        ]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "ready"
        assert results[1].status == "duplicate"

    def test_encoding_warning_on_replacement_char(self, tmp_path):
        content = VALID_SRT + "caf�\n"
        files = [PreviewInputFile(filename="Show.S01E05.srt", content=content)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            results = classify_files(tmp_path, 123, "Show Name", files)
        assert results[0].status == "ready"
        assert results[0].warning == "possible encoding issue"
