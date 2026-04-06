"""Unit tests for TheDiscDB contribution exporter."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.discdb_exporter import (
    EXPORT_SCHEMA_VERSION,
    _parse_episode_code,
    generate_export,
    get_export_directory,
    get_makemkv_log_dir,
    get_pending_exports,
    mark_exported,
    mark_skipped,
)
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState


@pytest.fixture
def config():
    return AppConfig(
        discdb_contributions_enabled=True,
        discdb_contribution_tier=2,
        discdb_export_path="",
    )


@pytest.fixture
def tv_job():
    return DiscJob(
        id=1,
        drive_id="E:",
        volume_label="BAND_OF_BROTHERS_S1D1",
        content_type=ContentType.TV,
        state=JobState.COMPLETED,
        content_hash="D7CAB58DAC87C58C46FDA35A33759839",
        detected_title="Band of Brothers",
        detected_season=1,
        tmdb_id=4613,
        classification_source="discdb_hash_match",
        classification_confidence=0.98,
        disc_number=1,
    )


@pytest.fixture
def movie_job():
    return DiscJob(
        id=2,
        drive_id="E:",
        volume_label="INCEPTION_2010",
        content_type=ContentType.MOVIE,
        state=JobState.COMPLETED,
        content_hash="A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6",
        detected_title="Inception",
        detected_season=None,
        tmdb_id=27205,
        classification_source="tmdb",
        classification_confidence=0.95,
        disc_number=1,
    )


@pytest.fixture
def tv_titles():
    return [
        DiscTitle(
            id=1,
            job_id=1,
            title_index=0,
            duration_seconds=4394,
            file_size_bytes=18405949440,
            chapter_count=12,
            source_filename="00001.m2ts",
            segment_count=1,
            segment_map="1",
            matched_episode="S01E01",
            match_confidence=0.99,
            match_details=json.dumps({"source": "subtitle"}),
        ),
        DiscTitle(
            id=2,
            job_id=1,
            title_index=1,
            duration_seconds=3600,
            file_size_bytes=12000000000,
            chapter_count=10,
            source_filename="00002.m2ts",
            segment_count=1,
            segment_map="2",
            matched_episode="S01E02",
            match_confidence=0.95,
            match_details=json.dumps({"source": "subtitle"}),
        ),
    ]


@pytest.fixture
def movie_titles():
    return [
        DiscTitle(
            id=3,
            job_id=2,
            title_index=0,
            duration_seconds=8880,
            file_size_bytes=32405949440,
            chapter_count=28,
            source_filename="00800.m2ts",
            segment_count=1,
            segment_map="1",
            is_selected=True,
        ),
        DiscTitle(
            id=4,
            job_id=2,
            title_index=1,
            duration_seconds=300,
            file_size_bytes=500000000,
            chapter_count=1,
            source_filename="00801.m2ts",
            segment_count=1,
            segment_map="2",
            is_selected=False,
            is_extra=True,
        ),
    ]


class TestParseEpisodeCode:
    def test_standard_code(self):
        assert _parse_episode_code("S01E01") == (1, 1)

    def test_high_numbers(self):
        assert _parse_episode_code("S12E24") == (12, 24)

    def test_none_input(self):
        assert _parse_episode_code(None) == (None, None)

    def test_empty_string(self):
        assert _parse_episode_code("") == (None, None)

    def test_malformed(self):
        assert _parse_episode_code("Episode 5") == (None, None)

    def test_multi_episode_returns_first(self):
        assert _parse_episode_code("S01E01E02") == (1, 1)

    def test_case_insensitive(self):
        assert _parse_episode_code("s03e07") == (3, 7)


class TestGenerateExport:
    def test_tv_export_has_season_episode_fields(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config, app_version="0.4.4")

        assert result is not None
        data = json.loads((result / "disc_data.json").read_text())

        assert data["export_version"] == "1.1"

        t0 = data["titles"][0]
        assert t0["season"] == 1
        assert t0["episode"] == 1
        assert "matched_episode" not in t0

        t1 = data["titles"][1]
        assert t1["season"] == 1
        assert t1["episode"] == 2

    def test_tv_export_structure(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config, app_version="0.4.4")

        assert result is not None
        json_path = result / "disc_data.json"
        assert json_path.exists()

        data = json.loads(json_path.read_text())
        assert data["engram_version"] == "0.4.4"
        assert data["export_version"] == EXPORT_SCHEMA_VERSION
        assert data["contribution_tier"] == 2

        # Disc section
        assert data["disc"]["content_hash"] == "D7CAB58DAC87C58C46FDA35A33759839"
        assert data["disc"]["content_type"] == "tv"
        assert data["disc"]["volume_label"] == "BAND_OF_BROTHERS_S1D1"
        assert data["disc"]["disc_number"] == 1

        # Identification
        assert data["identification"]["tmdb_id"] == 4613
        assert data["identification"]["detected_title"] == "Band of Brothers"
        assert data["identification"]["detected_season"] == 1

        # Titles
        assert len(data["titles"]) == 2
        t0 = data["titles"][0]
        assert t0["index"] == 0
        assert t0["source_filename"] == "00001.m2ts"
        assert t0["duration_seconds"] == 4394
        assert t0["size_bytes"] == 18405949440
        assert t0["segment_count"] == 1
        assert t0["segment_map"] == "1"
        assert t0["match_source"] == "subtitle"

    def test_movie_export_structure(self, movie_job, movie_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(movie_job, movie_titles, config, app_version="0.4.4")

        assert result is not None
        data = json.loads((result / "disc_data.json").read_text())

        assert data["disc"]["content_type"] == "movie"
        assert data["identification"]["detected_season"] is None

        # Main movie title — no episode info
        t0 = data["titles"][0]
        assert t0["title_type"] == "MainMovie"
        assert t0["season"] is None
        assert t0["episode"] is None

        # Extra title
        t1 = data["titles"][1]
        assert t1["title_type"] == "Extra"

    def test_no_export_without_content_hash(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        tv_job.content_hash = None
        result = generate_export(tv_job, tv_titles, config)
        assert result is None

    def test_export_includes_track_metadata(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config)

        data = json.loads((result / "disc_data.json").read_text())
        for title in data["titles"]:
            assert "source_filename" in title
            assert "segment_count" in title
            assert "segment_map" in title

    def test_export_directory_uses_content_hash(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config)
        assert result.name == "D7CAB58DAC87C58C46FDA35A33759839"

    def test_export_with_discdb_mappings(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        tv_job.discdb_mappings_json = json.dumps(
            [
                {
                    "index": 0,
                    "title_type": "Episode",
                    "episode_title": "Currahee",
                    "season": 1,
                    "episode": 1,
                },
                {
                    "index": 1,
                    "title_type": "Episode",
                    "episode_title": "Day of Days",
                    "season": 1,
                    "episode": 2,
                },
            ]
        )

        result = generate_export(tv_job, tv_titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert data["titles"][0]["title_type"] == "Episode"
        assert data["titles"][1]["title_type"] == "Episode"

    def test_export_with_upc(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        config.discdb_contribution_tier = 3
        tv_job.upc_code = "883929123456"

        result = generate_export(tv_job, tv_titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert data["upc"] == "883929123456"
        assert data["contribution_tier"] == 3

    def test_scan_log_is_flat_string(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        # scan_log should be a string or None, not a nested dict
        assert "scan_log" in data
        assert "makemkv_logs" not in data
        assert data["scan_log"] is None or isinstance(data["scan_log"], str)

    def test_release_id_from_release_group(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        tv_job.release_group_id = "550e8400-e29b-41d4-a716-446655440000"

        result = generate_export(tv_job, tv_titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert data["disc"]["release_id"] == "550e8400-e29b-41d4-a716-446655440000"

    def test_release_id_none_when_no_group(self, tv_job, tv_titles, config, tmp_path):
        config.discdb_export_path = str(tmp_path)
        result = generate_export(tv_job, tv_titles, config)
        data = json.loads((result / "disc_data.json").read_text())

        assert data["disc"]["release_id"] is None

    def test_skip_export_when_all_discdb_sourced(self, tv_job, config, tmp_path):
        """If all matched titles came from discdb, export should be skipped."""
        config.discdb_export_path = str(tmp_path)
        titles = [
            DiscTitle(
                id=1,
                job_id=1,
                title_index=0,
                duration_seconds=4394,
                file_size_bytes=18405949440,
                chapter_count=12,
                matched_episode="S01E01",
                match_details=json.dumps({"source": "discdb"}),
            ),
            DiscTitle(
                id=2,
                job_id=1,
                title_index=1,
                duration_seconds=3600,
                file_size_bytes=12000000000,
                chapter_count=10,
                matched_episode="S01E02",
                match_details=json.dumps({"source": "discdb"}),
            ),
        ]
        result = generate_export(tv_job, titles, config)
        assert result is None

    def test_export_when_mixed_sources(self, tv_job, tv_titles, config, tmp_path):
        """If some titles are discdb-sourced but not all, export should proceed."""
        config.discdb_export_path = str(tmp_path)
        # tv_titles fixture has source=discdb and source=subtitle (mixed)
        # Override first title to discdb
        tv_titles[0].match_details = json.dumps({"source": "discdb"})
        tv_titles[1].match_details = json.dumps({"source": "subtitle"})

        result = generate_export(tv_job, tv_titles, config)
        assert result is not None


class TestGetExportDirectory:
    def test_default_directory(self, config):
        result = get_export_directory(config)
        assert result == Path.home() / ".engram" / "discdb-exports"

    def test_custom_directory(self, config, tmp_path):
        config.discdb_export_path = str(tmp_path / "custom")
        result = get_export_directory(config)
        assert result == tmp_path / "custom"
        assert result.exists()


class TestGetMakemkvLogDir:
    def test_returns_job_specific_path(self):
        result = get_makemkv_log_dir(42)
        assert result == Path.home() / ".engram" / "logs" / "makemkv" / "42"


class TestMarkExported:
    async def test_mark_exported_sets_timestamp(self, isolate_database):
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST",
                state=JobState.COMPLETED,
                content_hash="ABC123",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        async with _unit_session_factory() as session:
            await mark_exported(job_id, session)

        async with _unit_session_factory() as session:
            from sqlmodel import select

            result = await session.execute(select(DiscJob).where(DiscJob.id == job_id))
            updated = result.scalar_one()
            assert updated.exported_at is not None
            assert updated.exported_at.year >= 2026


class TestMarkSkipped:
    async def test_mark_skipped_sets_epoch(self, isolate_database):
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST",
                state=JobState.COMPLETED,
                content_hash="ABC123",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        async with _unit_session_factory() as session:
            await mark_skipped(job_id, session)

        async with _unit_session_factory() as session:
            from sqlmodel import select

            result = await session.execute(select(DiscJob).where(DiscJob.id == job_id))
            updated = result.scalar_one()
            assert updated.exported_at is not None
            assert updated.exported_at.year == 1970


class TestGetPendingExports:
    async def test_returns_completed_unexported_jobs(self, isolate_database):
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            # Pending export
            j1 = DiscJob(
                drive_id="E:",
                volume_label="DISC1",
                state=JobState.COMPLETED,
                content_hash="HASH1",
            )
            # Already exported
            j2 = DiscJob(
                drive_id="E:",
                volume_label="DISC2",
                state=JobState.COMPLETED,
                content_hash="HASH2",
                exported_at=datetime.now(UTC),
            )
            # Failed (not completed)
            j3 = DiscJob(
                drive_id="E:",
                volume_label="DISC3",
                state=JobState.FAILED,
                content_hash="HASH3",
            )
            session.add_all([j1, j2, j3])
            await session.commit()

        async with _unit_session_factory() as session:
            pending = await get_pending_exports(session)
            assert len(pending) == 1
            assert pending[0].volume_label == "DISC1"
