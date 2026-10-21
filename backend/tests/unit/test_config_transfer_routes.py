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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as ac:
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
    resp = await client.post(
        "/api/config/import/preview",
        content=json.dumps(big).encode(),
        headers={"content-type": "application/json"},
    )
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


@pytest.mark.parametrize("path", ["/api/config/import/preview", "/api/config/import/apply"])
async def test_import_requires_json_content_type(client, path):
    """A cross-site form or text/plain POST needs no CORS preflight, so the body
    must not be parsed unless it is declared JSON (FastAPI's own CSRF stance)."""
    await _seed(extras_policy="keep")
    file = {"format": "engram-settings", "settings": {"extras_policy": "skip"}}
    body = file if path.endswith("preview") else {"file": file, "keys": ["extras_policy"]}
    resp = await client.post(
        path, content=json.dumps(body).encode(), headers={"content-type": "text/plain"}
    )
    assert resp.status_code == 415
    assert (await client.get("/api/config")).json()["extras_policy"] == "keep"


async def test_export_with_keys_refuses_foreign_host_name(client):
    """DNS rebinding reaches the loopback API under the attacker's host name, so
    the one response that carries plaintext keys checks the Host header."""
    await _seed(tmdb_api_key=TOKEN)
    resp = await client.get(
        "/api/config/export",
        params={"include_secrets": "true"},
        headers={"host": "evil.example.com:8000"},
    )
    assert resp.status_code == 403
    assert TOKEN not in resp.text
    # Without keys there is nothing to steal, so any host may export.
    resp = await client.get("/api/config/export", headers={"host": "evil.example.com:8000"})
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "host", ["localhost:8000", "127.0.0.1:8000", "192.168.1.50:8000", "[::1]:8000"]
)
async def test_export_with_keys_allows_loopback_and_ip_hosts(client, host):
    await _seed(tmdb_api_key=TOKEN)
    resp = await client.get(
        "/api/config/export", params={"include_secrets": "true"}, headers={"host": host}
    )
    assert resp.status_code == 200


async def test_export_with_keys_ignores_configured_dashboard_host(client):
    """dashboard_base_url is writable through PUT /api/config by the same
    rebinding page, so it must not be able to vouch for a host name."""
    await _seed(tmdb_api_key=TOKEN, dashboard_base_url="http://evil.example.com:5173")
    resp = await client.get(
        "/api/config/export",
        params={"include_secrets": "true"},
        headers={"host": "evil.example.com:8000"},
    )
    assert resp.status_code == 403
    assert TOKEN not in resp.text
