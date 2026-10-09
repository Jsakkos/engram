"""TheTVDB v4 client (spec 2026-10-08). Parses recorded live payloads."""

import json
from pathlib import Path

import pytest
import requests

from app.matcher import tmdb_client, tvdb_client
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
def _reset(monkeypatch):
    tvdb_client._token_state.token = None
    tvdb_client._token_state.key = None
    # Never touch the real ~/.engram cache.
    monkeypatch.setattr(tvdb_client.tmdb_persistent_cache, "get", lambda k: None)
    monkeypatch.setattr(tvdb_client.tmdb_persistent_cache, "put", lambda k, v, ttl: None)
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    yield
    tvdb_client._token_state.token = None
    tvdb_client._token_state.key = None


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


def _install(monkeypatch, *, login=None, gets=None):
    """Stub the network. Returns (log, sleeps); log records ('login'|'get', ...)."""
    log, sleeps = [], []
    gets = iter(gets or [])

    def fake_post(url, json, timeout):
        log.append(("login",))
        return login if login is not None else _Resp(200, {"data": {"token": "t"}})

    def fake_get(url, params, headers, timeout):
        log.append(("get", params["page"]))
        return next(gets)

    monkeypatch.setattr(tvdb_client.requests, "post", fake_post)
    monkeypatch.setattr(tvdb_client.requests, "get", fake_get)
    monkeypatch.setattr(tvdb_client.time, "sleep", lambda s: sleeps.append(s))
    return log, sleeps


def test_404_is_not_retried(monkeypatch):
    log, sleeps = _install(monkeypatch, gets=[_Resp(404, {})] * 5)
    assert tvdb_client.fetch_season_roster(1, 1, api_key="k") is None
    assert sleeps == []
    assert [e[0] for e in log].count("get") == 1


def test_login_401_is_not_retried(monkeypatch):
    log, sleeps = _install(monkeypatch, login=_Resp(401, {}))
    assert tvdb_client.fetch_season_roster(1, 1, api_key="bad") is None
    assert sleeps == []
    assert log == [("login",)]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "oops"},
        ["x"],
        {"data": {"episodes": 5}},
        {"data": {"episodes": ["x"]}},
        {"data": {"episodes": [{"seasonNumber": 1, "number": "abc"}]}},
    ],
)
def test_malformed_payload_is_not_retried(monkeypatch, payload):
    log, sleeps = _install(monkeypatch, gets=[_Resp(200, payload)] * 5)
    assert tvdb_client.fetch_season_roster(1, 1, api_key="k") is None
    assert sleeps == []
    assert [e[0] for e in log].count("get") == 1


def test_login_without_token_issues_no_get(monkeypatch):
    log, sleeps = _install(monkeypatch, login=_Resp(200, {"data": {}}))
    assert tvdb_client.fetch_season_roster(1, 1, api_key="k") is None
    assert sleeps == []
    assert [e[0] for e in log] == ["login"]


def test_second_401_after_relogin_is_not_retried(monkeypatch):
    log, sleeps = _install(monkeypatch, gets=[_Resp(401, {})] * 5)
    assert tvdb_client.fetch_season_roster(1, 1, api_key="k") is None
    assert sleeps == []
    assert [e[0] for e in log].count("get") == 2


def test_pagination_combines_and_sorts(monkeypatch):
    fixture = _jl_tvdb()
    eps = fixture["response"]["data"]["episodes"]
    first = {"data": {"episodes": eps[13:]}, "links": {"next": "more"}}
    second = {"data": {"episodes": eps[:13]}, "links": {"next": None}}
    log, _ = _install(monkeypatch, gets=[_Resp(200, first), _Resp(200, second)])
    roster = tvdb_client.fetch_season_roster(1, 1, api_key="k")
    assert [e["episode_number"] for e in roster] == list(range(1, 27))
    assert [e for e in log if e[0] == "get"] == [("get", 0), ("get", 1)]


def test_season_filter_drops_other_seasons(monkeypatch):
    payload = {
        "data": {
            "episodes": [
                {"seasonNumber": 1, "number": 1, "name": "a"},
                {"seasonNumber": 2, "number": 1, "name": "b"},
            ]
        }
    }
    _install(monkeypatch, gets=[_Resp(200, payload)])
    roster = tvdb_client.fetch_season_roster(1, 1, api_key="k")
    assert [e["name"] for e in roster] == ["a"]


def test_persistent_cache_hit_skips_network(monkeypatch):
    cached = [{"episode_number": 1, "name": "x"}]
    monkeypatch.setattr(tvdb_client.tmdb_persistent_cache, "get", lambda k: cached)
    monkeypatch.setattr(tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used"))
    assert tvdb_client.fetch_season_roster(1, 1, api_key="k") == cached


def test_503_then_200_retries_once(monkeypatch):
    fixture = _jl_tvdb()
    _, sleeps = _install(monkeypatch, gets=[_Resp(503, {}), _Resp(200, fixture["response"])])
    roster = tvdb_client.fetch_season_roster(fixture["tvdb_id"], 1, api_key="k")
    assert roster is not None and len(roster) == 26
    assert len(sleeps) == 1


def test_no_key_returns_none_without_network(monkeypatch):
    monkeypatch.setattr(tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used"))
    assert tvdb_client.fetch_season_roster(76290, 1, api_key="") is None


def test_invalid_series_id_rejected(monkeypatch):
    monkeypatch.setattr(tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used"))
    assert tvdb_client.fetch_season_roster("1/../x", 1, api_key="k") is None


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


def test_fetch_tvdb_id_non_numeric_tvdb_id_is_none(monkeypatch):
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "get", lambda k: None)
    monkeypatch.setattr(tmdb_client.tmdb_persistent_cache, "put", lambda k, v, ttl: None)
    monkeypatch.setattr(tmdb_client, "_tmdb_get_json", lambda *a, **k: {"tvdb_id": "abc"})
    assert tmdb_client.fetch_tvdb_id("1618", "tok") is None


def test_fetch_season_episodes_includes_air_date(monkeypatch):
    payload = json.loads((FIX / "justice_league_s1_tmdb.json").read_text("utf-8"))
    monkeypatch.setattr(tmdb_client, "_tmdb_get_json", lambda *a, **k: payload)
    eps = tmdb_client.fetch_season_episodes("1618", 1, "tok")
    assert len(eps) == 24
    assert eps[0]["air_date"]
