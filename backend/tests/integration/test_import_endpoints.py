"""Endpoint tests for manual import: browse, preview, start."""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import require_localhost_or_lan
from app.database import async_session, init_db
from app.main import app


@pytest.fixture
async def client():
    # The import endpoints are guarded by require_localhost_or_lan; override it so
    # these tests exercise the handlers regardless of peer/LAN config (the
    # documented test pattern). The guard's own branches are covered directly in
    # TestRequireLocalhostOrLan below.
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


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


async def test_start_on_season_folder_uses_parent_show(client, tmp_path: Path):
    # Picking a "Season NN" folder directly must yield a TV job whose title is
    # the parent show and whose season is the folder's own number (not a job
    # titled "Season 4" with no season).
    season_dir = tmp_path / "Seinfeld" / "Season 4"
    _mkv(season_dir / "e1.mkv")
    _mkv(season_dir / "e2.mkv")

    res = await client.post(
        "/api/import/start", json={"path": str(season_dir), "destination_mode": "library"}
    )
    assert res.status_code == 200
    job_ids = res.json()["job_ids"]
    assert len(job_ids) == 1

    async with async_session() as session:
        from app.models.disc_job import DiscJob

        job = await session.get(DiscJob, job_ids[0])
        assert job is not None
        assert job.detected_title == "Seinfeld"
        assert job.detected_season == 4
        assert job.content_type == "tv"
        # Guard the routes->manifest serialization: a dropped key would leave the
        # assertions above green while silently breaking in-place organize.
        manifest = json.loads(job.import_manifest_json)
        assert manifest["picked_is_season"] is True


async def test_start_no_mkvs_400(client, tmp_path: Path):
    (tmp_path / "empty").mkdir()
    res = await client.post("/api/import/start", json={"path": str(tmp_path / "empty")})
    assert res.status_code == 400


class TestRequireLocalhostOrLan:
    """The import guard: loopback always, LAN peers only when opted in (#524)."""

    def _request(self, host: str | None):
        from unittest.mock import MagicMock

        req = MagicMock()
        if host is None:
            req.client = None
        else:
            req.client.host = host
        return req

    async def test_loopback_allowed_without_lan(self):
        # Loopback never touches config: allowed even with LAN access off.
        from unittest.mock import AsyncMock, patch

        from app.api.routes import require_localhost_or_lan

        with patch("app.services.config_service.get_config", new_callable=AsyncMock) as gc:
            await require_localhost_or_lan(self._request("127.0.0.1"))  # no raise
            gc.assert_not_called()

    async def test_lan_rejected_when_disabled(self):
        from unittest.mock import AsyncMock, patch

        from fastapi import HTTPException

        from app.api.routes import require_localhost_or_lan

        cfg = type("C", (), {"allow_lan_access": False})()
        with patch(
            "app.services.config_service.get_config", new_callable=AsyncMock, return_value=cfg
        ):
            with pytest.raises(HTTPException) as exc:
                await require_localhost_or_lan(self._request("192.168.1.50"))
            assert exc.value.status_code == 403

    async def test_lan_allowed_when_enabled(self):
        from unittest.mock import AsyncMock, patch

        from app.api.routes import require_localhost_or_lan

        cfg = type("C", (), {"allow_lan_access": True})()
        with patch(
            "app.services.config_service.get_config", new_callable=AsyncMock, return_value=cfg
        ):
            await require_localhost_or_lan(self._request("192.168.1.50"))  # no raise

    async def test_config_read_failure_fails_closed(self):
        from unittest.mock import AsyncMock, patch

        from fastapi import HTTPException

        from app.api.routes import require_localhost_or_lan

        with patch(
            "app.services.config_service.get_config",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            with pytest.raises(HTTPException) as exc:
                await require_localhost_or_lan(self._request("192.168.1.50"))
            assert exc.value.status_code == 403
