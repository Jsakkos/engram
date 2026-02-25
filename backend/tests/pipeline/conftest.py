"""Shared fixtures for pipeline tests.

These tests use frozen disc snapshots (JSON metadata) to exercise
the real Analyst, Curator, and Organizer decision logic without
requiring actual MKV files or a running database.
"""

import json
from pathlib import Path

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
from app.core.tmdb_classifier import TmdbSignal
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType

SNAPSHOT_DIR = Path(__file__).parent.parent / "fixtures" / "disc_snapshots"


def _default_config() -> AppConfig:
    """Config with production-default analyst thresholds."""
    return AppConfig(
        analyst_movie_min_duration=4800,  # 80 min
        analyst_tv_duration_variance=120,  # +/-2 min
        analyst_tv_min_cluster_size=3,
        analyst_tv_min_duration=1080,  # 18 min
        analyst_tv_max_duration=4200,  # 70 min
        analyst_movie_dominance_threshold=0.6,
    )


@pytest.fixture
def analyst_config():
    """Provide default AppConfig for analyst tests."""
    return _default_config()


@pytest.fixture
def analyst(analyst_config):
    """Provide a configured DiscAnalyst instance."""
    return DiscAnalyst(config=analyst_config)


def load_snapshot(name: str) -> dict:
    """Load a disc snapshot JSON by name."""
    path = SNAPSHOT_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Snapshot not found: {path}")
    return json.loads(path.read_text())


def snapshot_to_titles(snapshot: dict) -> list[TitleInfo]:
    """Convert a disc snapshot into TitleInfo objects for the Analyst."""
    return [
        TitleInfo(
            index=track["index"],
            duration_seconds=track["duration_seconds"],
            size_bytes=track["size_bytes"],
            chapter_count=track["chapter_count"],
            name=track.get("filename", ""),
            video_resolution=track.get("video_resolution", ""),
        )
        for track in snapshot["tracks"]
    ]


def snapshot_to_tmdb_signal(snapshot: dict) -> TmdbSignal | None:
    """Convert a snapshot's tmdb_signal dict into a TmdbSignal object."""
    sig = snapshot.get("tmdb_signal")
    if not sig:
        return None
    return TmdbSignal(
        content_type=ContentType(sig["content_type"]),
        confidence=sig["confidence"],
        tmdb_id=sig.get("tmdb_id"),
        tmdb_name=sig.get("tmdb_name"),
    )
