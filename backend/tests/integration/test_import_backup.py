"""Importing an existing disc backup runs the full pipeline, not a file move.

A folder of finished MKVs is FILED into the library; a MakeMKV backup folder
(BDMV/VIDEO_TS) or an .iso is SCANNED and EXTRACTED like any other disc. These
tests pin both halves of that fork at the API seam.

The identification coroutines are stubbed for the whole module: a real one would
reach for makemkvcon (disc image) or ffprobe (MKV import), and a job that runs to
a terminal state leaks a Discord-notification task holding a pooled connection.
Stubbing also makes "which identification did this job get?" directly assertable,
which is the behaviour under test.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import require_localhost_or_lan
from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState


@pytest.fixture
async def client():
    # The import endpoints are guarded by require_localhost_or_lan; override it so
    # these tests exercise the handlers regardless of peer/LAN config (the
    # documented test pattern, mirroring test_import_endpoints.py).
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


@pytest.fixture(autouse=True)
async def identification_calls(monkeypatch):
    """Stub identification and record which entry point each job took."""
    from app.services.job_manager import job_manager

    calls: dict[int, str] = {}

    async def fake_identify_disc(job_id: int) -> None:
        calls[job_id] = "identify_disc"

    async def fake_identify_from_staging(job_id: int) -> None:
        calls[job_id] = "identify_from_staging"

    monkeypatch.setattr(job_manager._identification, "identify_disc", fake_identify_disc)
    monkeypatch.setattr(
        job_manager._identification, "identify_from_staging", fake_identify_from_staging
    )
    return calls


@pytest.fixture(autouse=True)
async def _clean_import_jobs():
    # start creates real jobs; clean import rows around each test in this module.
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs WHERE drive_id = 'import'"))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs WHERE drive_id = 'import'"))
        await session.commit()


def _bdmv(root: Path, name: str) -> Path:
    d = root / name
    (d / "BDMV" / "STREAM").mkdir(parents=True)
    (d / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"x" * 10)
    return d


def _mkv(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0" * 1024)


class TestBrowse:
    async def test_a_backup_folder_is_labelled_a_disc_image(self, client, tmp_path: Path):
        _bdmv(tmp_path, "Inception (2010)")

        res = await client.get("/api/import/browse", params={"path": str(tmp_path)})
        assert res.status_code == 200
        entries = {e["name"]: e for e in res.json()["entries"]}
        assert entries["Inception (2010)"]["type"] == "disc_image"
        # Not a folder of media, so it must not be given a media count.
        assert "mkv_count" not in entries["Inception (2010)"]

    async def test_an_iso_is_listed(self, client, tmp_path: Path):
        (tmp_path / "Inception.ISO").write_bytes(b"x")

        res = await client.get("/api/import/browse", params={"path": str(tmp_path)})
        entries = {e["name"]: e["type"] for e in res.json()["entries"]}
        assert entries["Inception.ISO"] == "iso"

    async def test_a_plain_media_folder_is_still_a_dir(self, client, tmp_path: Path):
        _mkv(tmp_path / "Season 1" / "a.mkv")

        res = await client.get("/api/import/browse", params={"path": str(tmp_path)})
        entries = {e["name"]: e for e in res.json()["entries"]}
        assert entries["Season 1"]["type"] == "dir"
        assert entries["Season 1"]["mkv_count"] == 1


class TestPreview:
    async def test_preview_reports_disc_images(self, client, tmp_path: Path):
        d = _bdmv(tmp_path, "Inception (2010)")

        res = await client.post("/api/import/preview", json={"path": str(tmp_path)})
        assert res.status_code == 200
        body = res.json()
        assert body["disc_images"] == [
            {
                "name": "Inception (2010)",
                "path": str(d),
                "kind": "backup",
                "total_bytes": 10,
            }
        ]
        # Each disc image becomes a job of its own.
        assert body["total_jobs"] == 1

    async def test_disc_images_and_units_both_count_as_jobs(self, client, tmp_path: Path):
        _bdmv(tmp_path, "Inception (2010)")
        _mkv(tmp_path / "Seinfeld" / "Season 4" / "e1.mkv")

        res = await client.post("/api/import/preview", json={"path": str(tmp_path)})
        body = res.json()
        assert len(body["disc_images"]) == 1
        assert len(body["units"]) == 1
        assert body["total_jobs"] == 2


class TestStart:
    async def test_start_creates_an_identifying_job_pointed_at_the_backup(
        self, client, tmp_path: Path, identification_calls
    ):
        d = _bdmv(tmp_path, "Inception (2010)")

        res = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert res.status_code == 200
        job_ids = res.json()["job_ids"]
        assert len(job_ids) == 1

        async with async_session() as session:
            job = await session.get(DiscJob, job_ids[0])
        assert job is not None
        assert job.source_spec == f"file:{d}"
        assert job.drive_id == "import"
        # The extracted MKVs must NOT land inside the user's preservation copy:
        # staging is a directory of its own, distinct from the image.
        assert job.staging_path is not None
        assert Path(job.staging_path) != d
        assert not Path(job.staging_path).is_relative_to(d)
        # It reads the image with MakeMKV instead of ingesting existing MKVs.
        assert job.import_manifest_json is None
        # It already is a backup: it must never enter BACKING_UP.
        assert job.state != JobState.BACKING_UP
        assert job.state == JobState.IDENTIFYING
        # The full scan/rip pipeline, not the "files already exist" shortcut.
        assert identification_calls[job_ids[0]] == "identify_disc"

    async def test_an_iso_job_carries_an_iso_spec(self, client, tmp_path: Path):
        iso = tmp_path / "Inception.iso"
        iso.write_bytes(b"x")

        res = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert res.status_code == 200
        job_ids = res.json()["job_ids"]
        assert len(job_ids) == 1

        async with async_session() as session:
            job = await session.get(DiscJob, job_ids[0])
        assert job is not None
        assert job.source_spec == f"iso:{iso}"
        assert job.state != JobState.BACKING_UP
        # An .iso path is a FILE. If staging_path pointed at it, the rip's
        # output_dir.mkdir(parents=True, exist_ok=True) would raise
        # FileExistsError and the job would die straight after the scan.
        assert job.staging_path is not None
        assert Path(job.staging_path) != iso
        assert not Path(job.staging_path).exists() or Path(job.staging_path).is_dir()

    async def test_disc_image_staging_directories_are_writable_and_distinct(
        self, client, tmp_path: Path, monkeypatch
    ):
        """Two images imported in one call get separate, mkdir-able staging dirs."""
        from app.services import config_service

        real_get_config = config_service.get_config
        staging_root = tmp_path / "staging"

        async def fake_get_config():
            config = await real_get_config()
            config.staging_path = str(staging_root)
            return config

        monkeypatch.setattr(config_service, "get_config", fake_get_config)

        a = _bdmv(tmp_path / "src", "Inception (2010)")
        b = _bdmv(tmp_path / "src", "Arrival (2016)")

        res = await client.post(
            "/api/import/start",
            json={"path": str(tmp_path / "src"), "destination_mode": "library"},
        )
        job_ids = res.json()["job_ids"]
        assert len(job_ids) == 2

        async with async_session() as session:
            jobs = [await session.get(DiscJob, jid) for jid in job_ids]

        staging = {Path(j.staging_path) for j in jobs}
        assert len(staging) == 2
        for s in staging:
            assert s not in (a, b)
            # This is exactly what _rip_titles_unlocked does with it.
            s.mkdir(parents=True, exist_ok=True)
            assert s.is_dir()

    async def test_a_second_import_of_a_live_backup_is_blocked(self, client, tmp_path: Path):
        _bdmv(tmp_path, "Inception (2010)")

        first = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert first.json()["job_ids"]

        second = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        body = second.json()
        assert body["job_ids"] == []
        assert len(body["blocked"]) == 1
        blocked = body["blocked"][0]
        assert blocked["reason"] == "in_flight"
        assert blocked["job_ids"] == first.json()["job_ids"]
        assert blocked["unit_key"]

    async def test_mkv_units_and_disc_images_both_get_jobs(
        self, client, tmp_path: Path, identification_calls
    ):
        d = _bdmv(tmp_path, "Inception (2010)")
        _mkv(tmp_path / "Seinfeld" / "Season 4" / "e1.mkv")

        res = await client.post(
            "/api/import/start", json={"path": str(tmp_path), "destination_mode": "library"}
        )
        assert res.status_code == 200
        job_ids = res.json()["job_ids"]
        assert len(job_ids) == 2

        async with async_session() as session:
            jobs = [await session.get(DiscJob, jid) for jid in job_ids]

        by_spec = {j.source_spec: j for j in jobs}
        image_job = by_spec[f"file:{d}"]
        mkv_job = by_spec[None]

        # The disc image is scanned and extracted; the MKV unit is still filed
        # through the untouched manual-import path.
        assert image_job.import_manifest_json is None
        assert identification_calls[image_job.id] == "identify_disc"
        assert mkv_job.import_manifest_json is not None
        assert identification_calls[mkv_job.id] == "identify_from_staging"

    async def test_an_empty_folder_is_still_rejected(self, client, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        res = await client.post("/api/import/start", json={"path": str(tmp_path / "empty")})
        assert res.status_code == 400
