"""The preview endpoints, from the API's side.

The property that matters most here is that the client names a *title*, never a
path: the file is resolved from the database, so the endpoint cannot be talked
into transcoding an arbitrary file off disk.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.preview import Chapter, MediaProbe
from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory


@pytest.fixture
async def client():
    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed(tmp_path, *, ripped: bool = True) -> tuple[int, int]:
    """A review-parked job with one title, optionally with a file on disk."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "title_t01.mkv"
    if ripped:
        source.write_bytes(b"not really an mkv")
    async with _unit_session_factory() as session:
        session.add(AppConfig(ffmpeg_path=""))
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=1,
            duration_seconds=1327,
            state=TitleState.REVIEW,
            output_filename=str(source) if ripped else None,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


PROBE = MediaProbe(
    duration=1327.759,
    video_codec="mpeg2video",
    audio_codec="ac3",
    chapters=[
        Chapter(index=1, start=0.0, end=31.16, title="Chapter 01"),
        Chapter(index=2, start=31.16, end=442.64, title="Chapter 02"),
    ],
)


@pytest.mark.unit
class TestPreviewInfo:
    async def test_returns_chapters_and_playability(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        with (
            patch("app.core.preview.resolve_ffmpeg_binary", return_value="ffprobe"),
            patch("app.core.preview.probe_media", AsyncMock(return_value=PROBE)),
        ):
            r = await client.get(f"/api/jobs/{job_id}/titles/{title_id}/preview")

        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        # An MPEG-2/AC3 rip has to be re-encoded — no browser plays it as-is.
        assert body["direct"] is False
        assert [c["title"] for c in body["chapters"]] == ["Chapter 01", "Chapter 02"]

    async def test_says_so_when_ffprobe_is_missing(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        with patch("app.core.preview.resolve_ffmpeg_binary", return_value=None):
            r = await client.get(f"/api/jobs/{job_id}/titles/{title_id}/preview")

        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert "ffprobe" in body["reason"].lower()

    async def test_unripped_title_is_a_conflict_not_a_crash(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path, ripped=False)
        r = await client.get(f"/api/jobs/{job_id}/titles/{title_id}/preview")
        assert r.status_code == 409

    async def test_missing_file_reports_not_found(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        (tmp_path / "title_t01.mkv").unlink()
        r = await client.get(f"/api/jobs/{job_id}/titles/{title_id}/preview")
        assert r.status_code == 404

    async def test_a_title_from_another_job_is_not_reachable(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        other_job_id, _ = await _seed(tmp_path / "other")
        r = await client.get(f"/api/jobs/{other_job_id}/titles/{title_id}/preview")
        assert r.status_code == 404


@pytest.mark.unit
class TestPreviewClip:
    async def test_streams_mp4_from_the_db_resolved_path(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        captured: dict = {}

        async def fake_stream(cmd, **_kwargs):
            captured["cmd"] = cmd
            yield b"\x00\x00\x00\x18ftypmp42"

        with (
            patch("app.core.preview.resolve_ffmpeg_binary", return_value="ffmpeg"),
            patch("app.core.preview.probe_media", AsyncMock(return_value=PROBE)),
            patch("app.core.preview.stream_preview", fake_stream),
        ):
            r = await client.get(
                f"/api/jobs/{job_id}/titles/{title_id}/preview/clip", params={"t": 31.16}
            )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/mp4")
        # Derived, cheap to regenerate, and tied to a file that can be re-ripped.
        assert r.headers["cache-control"] == "no-store"
        # The path came from the DB row, not from the request.
        assert str(tmp_path / "title_t01.mkv") in captured["cmd"]

    async def test_clip_length_is_capped(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        captured: dict = {}

        async def fake_stream(cmd, **_kwargs):
            captured["cmd"] = cmd
            yield b""

        with (
            patch("app.core.preview.resolve_ffmpeg_binary", return_value="ffmpeg"),
            patch("app.core.preview.probe_media", AsyncMock(return_value=PROBE)),
            patch("app.core.preview.stream_preview", fake_stream),
        ):
            await client.get(
                f"/api/jobs/{job_id}/titles/{title_id}/preview/clip", params={"t": 0, "d": 99999}
            )

        duration = captured["cmd"][captured["cmd"].index("-t") + 1]
        assert float(duration) <= 120

    async def test_missing_ffmpeg_is_a_service_error(self, client, tmp_path):
        job_id, title_id = await _seed(tmp_path)
        with patch("app.core.preview.resolve_ffmpeg_binary", return_value=None):
            r = await client.get(f"/api/jobs/{job_id}/titles/{title_id}/preview/clip")
        assert r.status_code == 503
