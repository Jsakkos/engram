"""Pipeline tests for TheDiscDB export JSON schema validation (v1.1)."""

import json

import pytest

from app.core.discdb_exporter import generate_export
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle

REQUIRED_TOP_LEVEL_KEYS = {
    "engram_version",
    "export_version",
    "exported_at",
    "contribution_tier",
    "disc",
    "identification",
    "titles",
    "upc",
    "asin",
    "release_date",
    "images",
    "scan_log",
}

REQUIRED_DISC_KEYS = {
    "content_hash",
    "volume_label",
    "content_type",
    "disc_number",
    "release_id",
}

REQUIRED_ID_KEYS = {
    "tmdb_id",
    "detected_title",
    "detected_season",
    "classification_source",
    "classification_confidence",
}

REQUIRED_TITLE_KEYS = {
    "index",
    "source_filename",
    "duration_seconds",
    "size_bytes",
    "chapter_count",
    "segment_count",
    "segment_map",
    "title_type",
    "season",
    "episode",
    "match_confidence",
    "match_source",
    "edition",
    "extra_description",
}


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        discdb_contributions_enabled=True,
        discdb_contribution_tier=2,
        discdb_export_path=str(tmp_path),
    )


class TestTVExportSchema:
    def test_tv_schema_has_all_required_keys(self, config, tmp_path):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state="completed",
            content_hash="ABCDEF1234567890",
            detected_title="Test Show",
            detected_season=1,
            tmdb_id=12345,
            classification_source="heuristic",
            classification_confidence=0.85,
        )
        titles = [
            DiscTitle(
                id=1,
                job_id=1,
                title_index=0,
                duration_seconds=2700,
                file_size_bytes=5000000000,
                chapter_count=8,
                source_filename="00001.m2ts",
                segment_count=1,
                segment_map="1",
                matched_episode="S01E01",
                match_confidence=0.95,
                match_details=json.dumps({"source": "subtitle"}),
            ),
        ]

        result = generate_export(job, titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert set(data.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert set(data["disc"].keys()) == REQUIRED_DISC_KEYS
        assert set(data["identification"].keys()) == REQUIRED_ID_KEYS
        for title in data["titles"]:
            assert set(title.keys()) == REQUIRED_TITLE_KEYS

    def test_season_episode_are_integers(self, config, tmp_path):
        job = DiscJob(
            id=1,
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state="completed",
            content_hash="ABCDEF1234567890",
            detected_title="Test Show",
            detected_season=1,
            tmdb_id=12345,
        )
        titles = [
            DiscTitle(
                id=1,
                job_id=1,
                title_index=0,
                duration_seconds=2700,
                file_size_bytes=5000000000,
                chapter_count=8,
                matched_episode="S01E05",
                match_confidence=0.95,
                match_details=json.dumps({"source": "subtitle"}),
            ),
        ]

        result = generate_export(job, titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        t = data["titles"][0]
        assert isinstance(t["season"], int)
        assert isinstance(t["episode"], int)
        assert t["season"] == 1
        assert t["episode"] == 5


class TestMovieExportSchema:
    def test_movie_schema_has_all_required_keys(self, config, tmp_path):
        job = DiscJob(
            id=2,
            drive_id="E:",
            volume_label="MOVIE_2024",
            content_type=ContentType.MOVIE,
            state="completed",
            content_hash="FEDCBA0987654321",
            detected_title="Test Movie",
            detected_season=None,
            tmdb_id=67890,
            classification_source="tmdb",
            classification_confidence=0.92,
        )
        titles = [
            DiscTitle(
                id=3,
                job_id=2,
                title_index=0,
                duration_seconds=7200,
                file_size_bytes=25000000000,
                chapter_count=24,
                source_filename="00800.m2ts",
                segment_count=1,
                segment_map="1",
                is_selected=True,
            ),
            DiscTitle(
                id=4,
                job_id=2,
                title_index=1,
                duration_seconds=180,
                file_size_bytes=300000000,
                chapter_count=1,
                source_filename="00801.m2ts",
                segment_count=1,
                segment_map="2",
                is_extra=True,
            ),
        ]

        result = generate_export(job, titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert set(data.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert data["disc"]["content_type"] == "movie"
        assert data["identification"]["detected_season"] is None

        # Title types
        assert data["titles"][0]["title_type"] == "MainMovie"
        assert data["titles"][1]["title_type"] == "Extra"

        # Movie titles have null season/episode
        assert data["titles"][0]["season"] is None
        assert data["titles"][0]["episode"] is None
