"""Unit tests for the external-player media endpoints.

Covers GET /api/jobs/{job_id}/titles/{title_id}/media: range handling, path
resolution and its organized_to fallback, and the containment guard.

The origin gate (require_localhost_or_lan) is overridden in the client fixture
here and is covered separately alongside the playlist endpoint.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.guards import require_localhost_or_lan
from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory

MEDIA_BYTES = b"MKVFAKEPAYLOAD" * 16  # 224 bytes


async def _seed_config(staging: str, movies: str, tv: str) -> AppConfig:
    async with _unit_session_factory() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key-1234567890",
            staging_path=staging,
            library_movies_path=movies,
            library_tv_path=tv,
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
            ffmpeg_path="/usr/bin/ffmpeg",
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


async def _seed_job_and_title(**title_kwargs) -> tuple[DiscJob, DiscTitle]:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="D:",
            volume_label="TEST_DISC",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Test Show",
            detected_season=1,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        defaults = dict(
            job_id=job.id,
            title_index=1,
            duration_seconds=1320,
            file_size_bytes=len(MEDIA_BYTES),
            state=TitleState.REVIEW,
        )
        defaults.update(title_kwargs)
        title = DiscTitle(**defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


@pytest.fixture
def media_file(tmp_path):
    """A staging root containing one fake MKV. Returns (staging_root, file)."""
    staging = tmp_path / "staging"
    staging.mkdir()
    f = staging / "title_01.mkv"
    f.write_bytes(MEDIA_BYTES)
    return staging, f


@pytest.fixture
async def client(tmp_path):
    """Async client with the patched DB session and the origin gate disabled.

    The gate is overridden so a gate failure cannot masquerade as a routing or
    resolution bug in these tests; it is covered by its own tests.
    """

    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestMediaEndpoint:
    async def test_full_request_returns_whole_file(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES
        assert r.headers["accept-ranges"] == "bytes"

    async def test_range_request_returns_206_partial(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(
            f"/api/jobs/{job.id}/titles/{title.id}/media",
            headers={"Range": "bytes=0-9"},
        )

        assert r.status_code == 206
        assert r.content == MEDIA_BYTES[:10]
        assert r.headers["content-range"] == f"bytes 0-9/{len(MEDIA_BYTES)}"

    async def test_unsatisfiable_range_returns_416(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(
            f"/api/jobs/{job.id}/titles/{title.id}/media",
            headers={"Range": "bytes=99999-"},
        )

        assert r.status_code == 416

    async def test_falls_back_to_organized_to(self, client, tmp_path):
        library = tmp_path / "tv"
        library.mkdir()
        organized = library / "Show - S01E01.mkv"
        organized.write_bytes(MEDIA_BYTES)
        await _seed_config(str(tmp_path / "staging"), str(tmp_path / "movies"), str(library))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=str(organized))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES

    async def test_prefers_output_filename_over_organized_to(self, client, tmp_path, media_file):
        staging, f = media_file
        library = tmp_path / "tv"
        library.mkdir()
        organized = library / "other.mkv"
        organized.write_bytes(b"WRONGFILE")
        await _seed_config(str(staging), str(tmp_path / "movies"), str(library))
        job, title = await _seed_job_and_title(output_filename=str(f), organized_to=str(organized))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES

    async def test_no_recorded_path_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=None)

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 404
        assert "not been ripped" in r.json()["detail"]

    async def test_missing_file_on_disk_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(staging / "gone.mkv"))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 404
        assert "no longer on disk" in r.json()["detail"]

    async def test_path_outside_configured_roots_returns_403(self, client, tmp_path, media_file):
        staging, _ = media_file
        outside = tmp_path / "elsewhere.mkv"
        outside.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(outside))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_traversal_path_returns_403(self, client, tmp_path, media_file):
        staging, _ = media_file
        secret = tmp_path / "secret.mkv"
        secret.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(staging / ".." / "secret.mkv"))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_title_belonging_to_another_job_returns_404(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        _job_a, title_a = await _seed_job_and_title(output_filename=str(f))
        job_b, _title_b = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job_b.id}/titles/{title_a.id}/media")

        assert r.status_code == 404

    async def test_unknown_job_returns_404(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))

        r = await client.get("/api/jobs/99999/titles/1/media")

        assert r.status_code == 404
