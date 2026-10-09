"""TheTVDB v4 client (spec 2026-10-08). Parses recorded live payloads."""

import json
from pathlib import Path

import pytest
import requests

from app.matcher import tvdb_client
from app.models.app_config import AppConfig

FIX = Path(__file__).parent.parent / "fixtures" / "tvdb"


def _jl_tvdb():
    return json.loads((FIX / "justice_league_s1_tvdb.json").read_text("utf-8"))


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    tvdb_client._token_state.token = None
    # Never touch the real ~/.engram cache.
    monkeypatch.setattr(tvdb_client.tmdb_persistent_cache, "get", lambda k: None)
    monkeypatch.setattr(tvdb_client.tmdb_persistent_cache, "put", lambda k, v, ttl: None)
    monkeypatch.delenv("TVDB_API_KEY", raising=False)


def test_resolve_api_key_prefers_config_then_env(monkeypatch):
    assert tvdb_client.resolve_api_key(AppConfig(tvdb_api_key="cfg")) == "cfg"
    monkeypatch.setenv("TVDB_API_KEY", "env")
    assert tvdb_client.resolve_api_key(AppConfig(tvdb_api_key="")) == "env"
    monkeypatch.delenv("TVDB_API_KEY")
    assert tvdb_client.resolve_api_key(AppConfig(tvdb_api_key="  ")) == ""


def test_fetch_season_roster_parses_justice_league(monkeypatch):
    fixture = _jl_tvdb()
    calls = []

    def fake_post(url, json, timeout):
        calls.append(("login", json["apikey"]))
        return _Resp(200, {"data": {"token": "tok"}})

    def fake_get(url, params, headers, timeout):
        calls.append(("get", params["page"]))
        assert headers["Authorization"] == "Bearer tok"
        return _Resp(200, fixture["response"])

    monkeypatch.setattr(tvdb_client.requests, "post", fake_post)
    monkeypatch.setattr(tvdb_client.requests, "get", fake_get)

    roster = tvdb_client.fetch_season_roster(fixture["tvdb_id"], 1, api_key="k")

    assert roster is not None
    assert len(roster) == 26
    assert roster[0]["episode_number"] == 1
    assert "secret origins" in roster[0]["name"].lower()
    assert roster[3]["episode_number"] == 4
    assert "blackest night" in roster[3]["name"].lower()
    assert set(roster[0]) >= {"episode_number", "name", "runtime", "overview", "air_date"}
    assert calls[0] == ("login", "k")


def test_relogin_once_on_401(monkeypatch):
    fixture = _jl_tvdb()
    logins = []
    gets = iter([_Resp(401, {}), _Resp(200, fixture["response"])])
    monkeypatch.setattr(
        tvdb_client.requests,
        "post",
        lambda url, json, timeout: logins.append(1) or _Resp(200, {"data": {"token": "t"}}),
    )
    monkeypatch.setattr(tvdb_client.requests, "get", lambda *a, **k: next(gets))

    roster = tvdb_client.fetch_season_roster(fixture["tvdb_id"], 1, api_key="k")

    assert roster is not None and len(roster) == 26
    assert len(logins) == 2


def test_failure_returns_none_never_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(tvdb_client.requests, "post", boom)
    monkeypatch.setattr(tvdb_client.time, "sleep", lambda s: None)
    assert tvdb_client.fetch_season_roster(76290, 1, api_key="k") is None


def test_no_key_returns_none_without_network(monkeypatch):
    monkeypatch.setattr(tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used"))
    assert tvdb_client.fetch_season_roster(76290, 1, api_key="") is None


def test_invalid_series_id_rejected(monkeypatch):
    monkeypatch.setattr(tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used"))
    assert tvdb_client.fetch_season_roster("1/../x", 1, api_key="k") is None


from app.matcher import tmdb_client  # noqa: E402


def test_fetch_tvdb_id_from_external_ids(monkeypatch):
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "get", lambda k: None)
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "put", lambda k, v, ttl: None)
    seen = {}

    def fake_get_json(url, api_key, query_params=None):
        seen["url"] = url
        return {"id": 1618, "tvdb_id": 76290}

    monkeypatch.setattr(tmdb_client, "_tmdb_get_json", fake_get_json)
    assert tmdb_client.fetch_tvdb_id("1618", "tok") == 76290
    assert seen["url"].endswith("/tv/1618/external_ids")


def test_fetch_tvdb_id_none_when_absent_or_bad_id(monkeypatch):
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "get", lambda k: None)
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "put", lambda k, v, ttl: None)
    monkeypatch.setattr(tmdb_client, "_tmdb_get_json", lambda *a, **k: {"tvdb_id": None})
    assert tmdb_client.fetch_tvdb_id("1618", "tok") is None
    assert tmdb_client.fetch_tvdb_id("16/18", "tok") is None
    assert tmdb_client.fetch_tvdb_id("1618", "") is None


def test_fetch_season_episodes_includes_air_date(monkeypatch):
    payload = json.loads((FIX / "justice_league_s1_tmdb.json").read_text("utf-8"))
    monkeypatch.setattr(tmdb_client, "_tmdb_get_json", lambda *a, **k: payload)
    eps = tmdb_client.fetch_season_episodes("1618", 1, "tok")
    assert len(eps) == 24
    assert eps[0]["air_date"]
