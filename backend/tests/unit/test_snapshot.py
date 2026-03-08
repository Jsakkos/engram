"""Tests for disc scan snapshot capture."""

import json
from unittest.mock import patch

from app.core.analyst import DiscAnalysisResult, TitleInfo
from app.core.snapshot import save_snapshot
from app.models.disc_job import ContentType


class TestSaveSnapshot:
    """Test snapshot file generation."""

    def test_saves_json_file(self, tmp_path):
        """Snapshot creates a valid JSON file in the snapshots directory."""
        analysis = DiscAnalysisResult(
            content_type=ContentType.TV,
            confidence=0.95,
            classification_source="tmdb+heuristic",
            detected_name="Arrested Development",
            detected_season=1,
            tmdb_id=4589,
            tmdb_name="Arrested Development",
            titles=[
                TitleInfo(index=0, duration_seconds=1320, size_bytes=500_000_000, chapter_count=5),
                TitleInfo(index=1, duration_seconds=1380, size_bytes=520_000_000, chapter_count=5),
            ],
            play_all_title_indices=[2],
        )

        with patch("app.core.snapshot.SNAPSHOTS_DIR", tmp_path):
            result = save_snapshot("ARRESTED_DEVELOPMENT_S1D1", analysis)

        assert result is not None
        assert result.exists()

        data = json.loads(result.read_text())
        assert data["volume_label"] == "ARRESTED_DEVELOPMENT_S1D1"
        assert data["classification"]["content_type"] == "tv"
        assert data["classification"]["confidence"] == 0.95
        assert data["classification"]["source"] == "tmdb+heuristic"
        assert data["classification"]["detected_name"] == "Arrested Development"
        assert data["tmdb"]["id"] == 4589
        assert len(data["tracks"]) == 2
        assert data["tracks"][0]["duration_seconds"] == 1320
        assert data["play_all_indices"] == [2]

    def test_filename_from_volume_label(self, tmp_path):
        """Snapshot filename is derived from volume label."""
        analysis = DiscAnalysisResult(content_type=ContentType.MOVIE, confidence=0.9)

        with patch("app.core.snapshot.SNAPSHOTS_DIR", tmp_path):
            result = save_snapshot("THE_GRANDMASTER_2013", analysis)

        assert result is not None
        assert result.name.startswith("the_grandmaster_2013_")
        assert result.suffix == ".json"

    def test_handles_write_failure_gracefully(self, tmp_path):
        """Snapshot failure returns None without raising."""
        analysis = DiscAnalysisResult(content_type=ContentType.MOVIE, confidence=0.9)

        # Point to a file (not directory) so mkdir fails
        bad_path = tmp_path / "blocker.txt"
        bad_path.write_text("not a dir")
        nested = bad_path / "snapshots"

        with patch("app.core.snapshot.SNAPSHOTS_DIR", nested):
            result = save_snapshot("TEST", analysis)

        # Should return None, not raise
        assert result is None

    def test_movie_snapshot_fields(self, tmp_path):
        """Movie snapshots include ambiguous_movie flag."""
        analysis = DiscAnalysisResult(
            content_type=ContentType.MOVIE,
            confidence=0.6,
            classification_source="tmdb",
            is_ambiguous_movie=True,
            needs_review=True,
            review_reason="Ambiguous movie detection",
        )

        with patch("app.core.snapshot.SNAPSHOTS_DIR", tmp_path):
            result = save_snapshot("SOME_DISC", analysis)

        data = json.loads(result.read_text())
        assert data["classification"]["is_ambiguous_movie"] is True
        assert data["classification"]["needs_review"] is True
        assert data["classification"]["review_reason"] == "Ambiguous movie detection"
