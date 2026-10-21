"""HTTP surface of settings export/import (#731): headers, errors, and the gate."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.app_config import AppConfig
from tests.unit.conftest import _unit_session_factory

TOKEN = "eyJsecret-token-value"


async def _seed(**fields) -> None:
    async with _unit_session_factory() as session:
        session.add(AppConfig(**fields))
        await session.commit()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_export_headers(client):
    await _seed(tmdb_api_key=TOKEN)
    resp = await client.get("/api/config/export")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert "engram-settings-" in disposition
    assert disposition.endswith('.json"')
    assert "-with-keys" not in disposition
    assert resp.headers["cache-control"] == "no-store"
    assert TOKEN not in resp.text

    resp = await client.get("/api/config/export", params={"include_secrets": "true"})
    assert "-with-keys.json" in resp.headers["content-disposition"]
    assert resp.json()["settings"]["tmdb_api_key"] == TOKEN


async def test_preview_endpoint_round_trip(client):
    await _seed(extras_policy="skip")
    exported = (await client.get("/api/config/export")).json()
    resp = await client.post("/api/config/import/preview", json=exported)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unknown"] == []
    assert any(f["key"] == "extras_policy" and f["status"] == "same" for f in body["fields"])


async def test_preview_bad_json_400(client):
    resp = await client.post(
        "/api/config/import/preview",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]


async def test_preview_wrong_format_400(client):
    resp = await client.post("/api/config/import/preview", json={"format": "other"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This is not an Engram settings file."


async def test_preview_oversize_413(client):
    big = {"format": "engram-settings", "settings": {"x": "a" * 300_000}}
    resp = await client.post("/api/config/import/preview", content=json.dumps(big).encode())
    assert resp.status_code == 413


async def test_apply_endpoint_applies_and_422s(client):
    await _seed(extras_policy="keep")
    file = {"format": "engram-settings", "schema_version": 1, "settings": {"extras_policy": "skip"}}

    resp = await client.post(
        "/api/config/import/apply", json={"file": file, "keys": ["extras_policy"]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"applied": ["extras_policy"]}
    assert (await client.get("/api/config")).json()["extras_policy"] == "skip"

    resp = await client.post(
        "/api/config/import/apply", json={"file": file, "keys": ["future_thing"]}
    )
    assert resp.status_code == 422
    assert "future_thing" in resp.json()["detail"]["errors"]


async def test_apply_wrong_format_400(client):
    resp = await client.post(
        "/api/config/import/apply", json={"file": {"format": "x"}, "keys": ["extras_policy"]}
    )
    assert resp.status_code == 400


async def test_transfer_routes_refuse_lan_peer():
    await _seed(allow_lan_access=False, tmdb_api_key=TOKEN)
    transport = ASGITransport(app=app, client=("172.18.0.14", 50000))
    file = {"format": "engram-settings", "settings": {}}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for resp in (
            await ac.get("/api/config/export", params={"include_secrets": "true"}),
            await ac.post("/api/config/import/preview", json=file),
            await ac.post("/api/config/import/apply", json={"file": file, "keys": []}),
        ):
            assert resp.status_code == 403
            assert TOKEN not in resp.text
