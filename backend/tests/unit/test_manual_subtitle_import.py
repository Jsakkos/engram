"""Unit tests for manual subtitle bulk-import preview/commit logic."""

from unittest.mock import patch

from app.matcher.manual_subtitle_import import (
    MAX_CONTENT_BYTES,
    CommitInputFile,
    PreviewInputFile,
    classify_files,
    commit_files,
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


class TestCommitFiles:
    def test_writes_file_to_expected_path(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=5, content=VALID_SRT)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "imported"
        dest = tmp_path / "data" / "123" / "Show Name - S01E05.srt"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == VALID_SRT

    def test_skips_when_already_covered_at_commit_time(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=2, content=VALID_SRT)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E02": "downloaded"},
        ):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "skipped"
        assert outcomes[0].reason == "already_covered"
        dest = tmp_path / "data" / "123" / "Show Name - S01E02.srt"
        assert not dest.exists()

    def test_rejects_out_of_range_season(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=999, episode=1, content=VALID_SRT)]
        outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "error"
        assert "range" in outcomes[0].reason

    def test_rejects_invalid_content(self, tmp_path):
        files = [
            CommitInputFile(filename="x.srt", season=1, episode=1, content="not a subtitle" * 5)
        ]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E01": "missing"},
        ):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "error"

    def test_duplicate_within_batch_skips_second(self, tmp_path):
        files = [
            CommitInputFile(filename="a.srt", season=1, episode=5, content=VALID_SRT),
            CommitInputFile(filename="b.srt", season=1, episode=5, content=VALID_SRT),
        ]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E05": "missing"},
        ):
            outcomes = commit_files(tmp_path, 123, "Show Name", files)
        assert outcomes[0].status == "imported"
        assert outcomes[1].status == "skipped"
        assert outcomes[1].reason == "duplicate within this batch"

    def test_sanitizes_show_name_in_filename(self, tmp_path):
        files = [CommitInputFile(filename="x.srt", season=1, episode=1, content=VALID_SRT)]
        with patch(
            "app.matcher.manual_subtitle_import.reference_coverage",
            return_value={"S01E01": "missing"},
        ):
            commit_files(tmp_path, 123, "Law & Order: SVU", files)
        dest = tmp_path / "data" / "123" / "Law & Order - SVU - S01E01.srt"
        assert dest.exists()
