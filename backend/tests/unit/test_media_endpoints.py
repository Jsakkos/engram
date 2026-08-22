"""Unit tests for the external-player media endpoints.

Covers GET /api/jobs/{job_id}/titles/{title_id}/media and .../playlist.m3u:
range handling, path resolution and its organized_to fallback, the containment
guard, and the origin gate.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

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

    The gate is exercised explicitly in TestOriginGate; every other test
    overrides it so it cannot mask an unrelated failure.
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

    async def test_serves_an_imported_file_outside_the_configured_staging_root(
        self, client, tmp_path
    ):
        """Imports live where the user picked, not under config.staging_path.

        /api/import/start records that folder on the job, so the job's own
        staging_path is the file's provenance and has to count as a root.
        """
        picked = tmp_path / "downloads" / "Some Show S01"
        picked.mkdir(parents=True)
        imported = picked / "episode.mkv"
        imported.write_bytes(MEDIA_BYTES)
        await _seed_config(
            str(tmp_path / "staging"), str(tmp_path / "movies"), str(tmp_path / "tv")
        )
        job, title = await _seed_job_and_title(output_filename=str(imported))
        async with _unit_session_factory() as session:
            seeded = await session.get(DiscJob, job.id)
            seeded.staging_path = str(picked)
            await session.commit()

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200
        assert r.content == MEDIA_BYTES

    async def test_serves_a_file_under_the_import_watch_folder(self, client, tmp_path):
        watch = tmp_path / "watch"
        watch.mkdir()
        watched = watch / "episode.mkv"
        watched.write_bytes(MEDIA_BYTES)
        await _seed_config(
            str(tmp_path / "staging"), str(tmp_path / "movies"), str(tmp_path / "tv")
        )
        async with _unit_session_factory() as session:
            cfg = (await session.execute(select(AppConfig))).scalars().first()
            cfg.import_watch_path = str(watch)
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(watched))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200


class TestPlaylistEndpoint:
    async def test_returns_m3u_with_absolute_media_url(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 200
        body = r.text
        assert body.startswith("#EXTM3U")
        assert f"/api/jobs/{job.id}/titles/{title.id}/media" in body
        assert "http://test/" in body

    async def test_url_uses_request_host_not_localhost(self, tmp_path, media_file):
        """A playlist opened on another machine must not point at localhost.

        This is the failure that passes local testing and breaks every remote
        user, so it is asserted against an explicit non-loopback Host.
        """
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[require_localhost_or_lan] = lambda: None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://192.168.1.50:8000") as ac:
            r = await ac.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")
        app.dependency_overrides.clear()

        assert r.status_code == 200
        assert "192.168.1.50:8000" in r.text
        assert "localhost" not in r.text
        assert "127.0.0.1" not in r.text

    async def test_served_as_attachment_with_playlist_type(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert "audio/x-mpegurl" in r.headers["content-type"]
        disposition = r.headers["content-disposition"]
        assert disposition.startswith("attachment")
        assert f"engram-job-{job.id}-title-{title.title_index}.m3u" in disposition

    async def test_playlist_404s_when_track_not_ripped(self, client, tmp_path, media_file):
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=None, organized_to=None)

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 404

    async def test_playlist_403s_for_out_of_bounds_path(self, client, tmp_path, media_file):
        staging, _ = media_file
        outside = tmp_path / "elsewhere.mkv"
        outside.write_bytes(MEDIA_BYTES)
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(outside))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 403

    async def test_playlist_404s_when_recorded_file_is_gone_from_disk(
        self, client, tmp_path, media_file
    ):
        """output_filename can outlive the file: CleanupService removes staging

        after organizing, but the DB row keeps the old path. This is the most
        likely error a real user hits, so the playlist needs its own 404 for
        it, not just the media endpoint.
        """
        staging, _ = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(staging / "gone.mkv"))

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 404

    async def test_label_cannot_inject_a_second_playlist_entry(self, client, tmp_path, media_file):
        """A crafted disc label must not forge an extra .m3u entry.

        volume_label comes off the physical disc, and .m3u is line-oriented,
        so an unsanitised label carrying CR/LF can open a second entry pointing
        at a URL of the attacker's choosing.
        """
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))
        async with _unit_session_factory() as session:
            seeded = await session.get(DiscJob, job.id)
            seeded.detected_title = None
            seeded.volume_label = (
                "EVIL\n#EXTINF:-1,Fake Entry\nhttp://evil.example.com/malicious.mkv\n#"
            )
            await session.commit()

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 200
        body = r.text
        lines = body.splitlines()
        # The sanitizer collapses CR/LF to spaces rather than deleting the
        # surrounding text (it mirrors sanitize_log_value, which preserves
        # ordinary text), so the malicious domain and the literal substring
        # "#EXTINF" can still appear as inert text inside the one legitimate
        # comment *line*. What must never happen is a second, independently
        # parseable entry, so this counts lines, not substring occurrences:
        # exactly one line starting with #EXTINF and exactly one URL line,
        # and the forged URL must not be that line.
        extinf_lines = [ln for ln in lines if ln.startswith("#EXTINF")]
        assert len(extinf_lines) == 1
        url_lines = [ln for ln in lines if ln.startswith("http")]
        assert len(url_lines) == 1
        assert "evil.example.com" not in url_lines[0]

    async def test_label_with_carriage_return_is_flattened(self, client, tmp_path, media_file):
        staging, f = media_file
        await _seed_config(str(staging), str(tmp_path / "movies"), str(tmp_path / "tv"))
        job, title = await _seed_job_and_title(output_filename=str(f))
        async with _unit_session_factory() as session:
            seeded = await session.get(DiscJob, job.id)
            seeded.detected_title = "Line1\r\nLine2"
            await session.commit()

        r = await client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 200
        assert r.text.count("#EXTINF") == 1
        assert "Line1 Line2" in r.text


class TestOriginGate:
    """Both endpoints sit behind require_localhost_or_lan.

    ASGITransport defaults the peer address to loopback, so the 403 branch is
    only reachable by passing an explicit non-loopback client= to the transport.
    """

    @pytest.fixture
    async def lan_client(self):
        async def override_get_session():
            async with _unit_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        transport = ASGITransport(app=app, client=("192.168.1.50", 51234))
        async with AsyncClient(transport=transport, base_url="http://192.168.1.50:8000") as ac:
            yield ac
        app.dependency_overrides.clear()

    async def test_lan_peer_blocked_when_lan_access_disabled(
        self, lan_client, tmp_path, media_file
    ):
        staging, f = media_file
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    makemkv_path="/usr/bin/makemkvcon",
                    staging_path=str(staging),
                    library_movies_path=str(tmp_path / "movies"),
                    library_tv_path=str(tmp_path / "tv"),
                    allow_lan_access=False,
                )
            )
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await lan_client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 403

    async def test_lan_peer_allowed_when_lan_access_enabled(self, lan_client, tmp_path, media_file):
        staging, f = media_file
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    makemkv_path="/usr/bin/makemkvcon",
                    staging_path=str(staging),
                    library_movies_path=str(tmp_path / "movies"),
                    library_tv_path=str(tmp_path / "tv"),
                    allow_lan_access=True,
                )
            )
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await lan_client.get(f"/api/jobs/{job.id}/titles/{title.id}/media")

        assert r.status_code == 200

    async def test_playlist_blocked_for_lan_peer_when_lan_access_disabled(
        self, lan_client, tmp_path, media_file
    ):
        staging, f = media_file
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    makemkv_path="/usr/bin/makemkvcon",
                    staging_path=str(staging),
                    library_movies_path=str(tmp_path / "movies"),
                    library_tv_path=str(tmp_path / "tv"),
                    allow_lan_access=False,
                )
            )
            await session.commit()
        job, title = await _seed_job_and_title(output_filename=str(f))

        r = await lan_client.get(f"/api/jobs/{job.id}/titles/{title.id}/playlist.m3u")

        assert r.status_code == 403
