"""Endpoint tests for manual import: browse, preview, start."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mkv(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0" * 1024)


async def test_browse_lists_dirs_and_mkvs(client, tmp_path: Path):
    _mkv(tmp_path / "Season 1" / "a.mkv")
    _mkv(tmp_path / "loose.mkv")

    res = await client.get("/api/import/browse", params={"path": str(tmp_path)})
    assert res.status_code == 200
    data = res.json()
    names = {e["name"]: e for e in data["entries"]}
    assert names["Season 1"]["type"] == "dir"
    assert names["loose.mkv"]["type"] == "mkv"
    assert data["cwd"] == str(tmp_path.resolve())


async def test_browse_empty_path_returns_roots(client):
    res = await client.get("/api/import/browse", params={"path": ""})
    assert res.status_code == 200
    assert isinstance(res.json()["roots"], list)


async def test_browse_bad_path_400(client, tmp_path: Path):
    res = await client.get("/api/import/browse", params={"path": str(tmp_path / "nope")})
    assert res.status_code == 400


async def test_preview_groups_per_season(client, tmp_path: Path):
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "a.mkv")
    _mkv(show / "Season 1" / "Disc 2" / "b.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "c.mkv")

    res = await client.post("/api/import/preview", json={"path": str(show)})
    assert res.status_code == 200
    data = res.json()
    assert data["total_jobs"] == 2
    assert data["total_files"] == 3
    seasons = sorted(u["season"] for u in data["units"])
    assert seasons == [1, 2]


async def test_preview_bad_path_400(client, tmp_path: Path):
    res = await client.post("/api/import/preview", json={"path": str(tmp_path / "nope")})
    assert res.status_code == 400


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


async def test_start_creates_one_job_per_season_with_manifest(client, tmp_path: Path):
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "a.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "b.mkv")

    res = await client.post(
        "/api/import/start", json={"path": str(show), "destination_mode": "library"}
    )
    assert res.status_code == 200
    job_ids = res.json()["job_ids"]
    assert len(job_ids) == 2

    async with async_session() as session:
        from app.models.disc_job import DiscJob

        for jid in job_ids:
            job = await session.get(DiscJob, jid)
            assert job is not None
            assert job.drive_id == "import"
            assert job.import_manifest_json is not None


async def test_start_no_mkvs_400(client, tmp_path: Path):
    (tmp_path / "empty").mkdir()
    res = await client.post("/api/import/start", json={"path": str(tmp_path / "empty")})
    assert res.status_code == 400
