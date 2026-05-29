"""API tests for the episode-ordering config + per-show endpoints (GitHub #200)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_session
from app.main import app
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


@pytest.mark.unit
class TestConfigOrderingPreference:
    async def test_get_config_exposes_default_aired(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        assert resp.json()["episode_ordering_preference"] == "aired"

    async def test_put_config_accepts_dvd(self, client):
        resp = await client.put("/api/config", json={"episode_ordering_preference": "dvd"})
        assert resp.status_code == 200
        got = await client.get("/api/config")
        assert got.json()["episode_ordering_preference"] == "dvd"

    async def test_put_config_rejects_absolute(self, client):
        # absolute is deferred in v1 — must be rejected, not silently stored.
        resp = await client.put("/api/config", json={"episode_ordering_preference": "absolute"})
        assert resp.status_code == 422

    async def test_put_config_rejects_unknown(self, client):
        resp = await client.put("/api/config", json={"episode_ordering_preference": "bogus"})
        assert resp.status_code == 422


@pytest.mark.unit
class TestPerShowOrderingEndpoints:
    async def test_get_defaults_to_aired_for_unknown_show(self, client):
        resp = await client.get("/api/shows/1437/ordering")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ordering"] == "aired"
        assert body["source"] == "default"

    async def test_put_then_get_roundtrips(self, client, monkeypatch):
        # Avoid a real TMDB call when resolving the group id.
        monkeypatch.setattr(
            "app.core.episode_ordering.resolve_episode_group_id",
            lambda show_id, ordering, key: "grp_dvd",
        )
        put = await client.put("/api/shows/1437/ordering", json={"ordering": "dvd"})
        assert put.status_code == 200
        assert put.json()["episode_group_id"] == "grp_dvd"

        get = await client.get("/api/shows/1437/ordering")
        assert get.json()["ordering"] == "dvd"
        assert get.json()["source"] == "show"

    async def test_put_rejects_absolute(self, client):
        resp = await client.put("/api/shows/1437/ordering", json={"ordering": "absolute"})
        assert resp.status_code == 422
