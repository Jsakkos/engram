"""Pure episode-namespace logic (spec 2026-10-08), against recorded rosters."""

import asyncio
import json
from pathlib import Path

import pytest

from app.core import episode_namespace as ns

FIX = Path(__file__).parent.parent / "fixtures" / "tvdb"


def _tmdb(name):
    data = json.loads((FIX / f"{name}_tmdb.json").read_text("utf-8"))
    return [
        {
            "episode_number": e["episode_number"],
            "name": e.get("name") or "",
            "air_date": e.get("air_date") or "",
        }
        for e in data["episodes"]
    ]


def _tvdb(name):
    from app.matcher.tvdb_client import _parse_episodes

    data = json.loads((FIX / f"{name}_tvdb.json").read_text("utf-8"))
    return _parse_episodes(data["response"], 1)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Secret Origins (1)", "secretorigins1"),
        ("Secret Origins", "secretorigins"),
        ("Secret Origins: Part I", "secretorigins1"),
        ("Secret Origins, Part One", "secretorigins1"),
        ("In Blackest Night (2)", "inblackestnight2"),
        ("The Enemy Below, Part 2", "theenemybelow2"),
        ("The Enemy Below - Pt. 2", "theenemybelow2"),
        ("The Enemy Below (Part II)", "theenemybelow2"),
        ("Parting Shot", "partingshot"),
        ("Deedeemensional", "deedeemensional"),
        ("DeeDeemensional", "deedeemensional"),
        ("Rocky IV", "rockyiv"),
        ("", ""),
    ],
)
def test_normalize_title(raw, expected):
    assert ns.normalize_title(raw) == expected


def test_context_defaults_to_tmdb_and_nests():
    assert ns.current_namespace() == "tmdb"
    with ns.namespace_context("tvdb"):
        assert ns.current_namespace() == "tvdb"
        assert ns.corpus_dir_suffix() == "@tvdb"
    assert ns.current_namespace() == "tmdb"
    assert ns.corpus_dir_suffix() == ""


def test_context_reaches_to_thread():
    async def run():
        with ns.namespace_context("tvdb"):
            return await asyncio.to_thread(ns.current_namespace)

    assert asyncio.run(run()) == "tvdb"


def test_unknown_namespace_rejected():
    with pytest.raises(ValueError):
        with ns.namespace_context("imdb"):
            pass


def test_justice_league_diverges_by_count():
    div = ns.detect_divergence(1, _tmdb("justice_league_s1"), _tvdb("justice_league_s1"))
    assert div is not None
    assert (div.season, div.tmdb_count, div.tvdb_count) == (1, 24, 26)
    assert json.loads(div.to_json()) == {"season": 1, "tmdb": 24, "tvdb": 26}


def test_dexters_lab_does_not_diverge():
    # TheTVDB official order matches TMDB for Dexter's Laboratory (spec 2026-09-20).
    # If this fails, inspect the fixture before changing detect_divergence.
    assert ns.detect_divergence(1, _tmdb("dexters_lab_s1"), _tvdb("dexters_lab_s1")) is None


def test_same_titles_reordered_diverges():
    tmdb = [{"episode_number": 1, "name": "A"}, {"episode_number": 2, "name": "B"}]
    tvdb = [{"episode_number": 1, "name": "B"}, {"episode_number": 2, "name": "A"}]
    assert ns.detect_divergence(1, tmdb, tvdb) is not None


def test_spelling_difference_alone_does_not_diverge():
    tmdb = [{"episode_number": 1, "name": "Colour"}, {"episode_number": 2, "name": "B"}]
    tvdb = [{"episode_number": 1, "name": "Color"}, {"episode_number": 2, "name": "B"}]
    assert ns.detect_divergence(1, tmdb, tvdb) is None


def test_crosswalk_skips_the_split_pilot_and_maps_the_rest():
    tvdb = _tvdb("justice_league_s1")
    cw = ns.build_crosswalk(1, _tmdb("justice_league_s1"), tvdb)
    for part in ("S01E01", "S01E02", "S01E03"):
        assert part not in cw
    assert cw["S01E04"] == "S01E02"  # In Blackest Night (1)
    assert cw["S01E05"] == "S01E03"  # In Blackest Night (2)
    assert len(set(cw.values())) == len(cw)  # strictly 1:1
    # The pairing genuinely works across the season, not only for the two
    # episodes asserted above: at least 20 of the 23 non-pilot episodes map.
    non_pilot = [f"S01E{int(e['episode_number']):02d}" for e in tvdb]
    non_pilot = [c for c in non_pilot if c not in ("S01E01", "S01E02", "S01E03")]
    assert len(non_pilot) == 23
    assert sum(c in cw for c in non_pilot) >= 20


def test_crosswalk_ambiguous_title_is_skipped():
    tmdb = [
        {"episode_number": 1, "name": "Dup", "air_date": "2001-01-01"},
        {"episode_number": 2, "name": "Dup", "air_date": "2001-01-08"},
        {"episode_number": 3, "name": "Solo", "air_date": "2001-01-15"},
    ]
    tvdb = [
        {"episode_number": 1, "name": "Dup", "air_date": "2001-01-01"},
        {"episode_number": 2, "name": "Solo", "air_date": "2001-01-15"},
    ]
    assert ns.build_crosswalk(1, tmdb, tvdb) == {"S01E02": "S01E03"}


def test_crosswalk_pairs_by_air_date_when_titles_differ():
    tmdb = [{"episode_number": 1, "name": "Colour", "air_date": "2001-01-01"}]
    tvdb = [{"episode_number": 1, "name": "Color", "air_date": "2001-01-01"}]
    assert ns.build_crosswalk(1, tmdb, tvdb) == {"S01E01": "S01E01"}


def test_translation_helpers():
    cw_json = json.dumps({"S01E04": "S01E02"})
    assert ns.to_tmdb_code("tvdb", cw_json, "S01E04") == "S01E02"
    assert ns.to_tmdb_code("tvdb", cw_json, "S01E01") is None
    assert ns.to_tmdb_code("tvdb", None, "S01E04") is None
    assert ns.to_tmdb_code("tmdb", None, "S01E04") == "S01E04"
    assert ns.from_tmdb_code("tvdb", cw_json, "S01E02") == "S01E04"
    assert ns.from_tmdb_code("tvdb", cw_json, "S01E01") is None
    assert ns.from_tmdb_code("tmdb", None, "S01E01") == "S01E01"
    # A combined code translates only if every part does.
    cw2 = json.dumps({"S01E04": "S01E02", "S01E05": "S01E03"})
    assert ns.to_tmdb_code("tvdb", cw2, "S01E04-E05") == "S01E02-E03"
    assert ns.to_tmdb_code("tvdb", cw2, "S01E03-E04") is None


def test_season_episodes_tmdb_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.matcher.tmdb_client.fetch_season_episodes",
        lambda show, season, key: [{"episode_number": 1, "name": "TMDB"}],
    )
    assert ns.season_episodes("1618", 1, "tok")[0]["name"] == "TMDB"


def test_season_episodes_tvdb_in_context(monkeypatch):
    monkeypatch.setattr("app.matcher.tmdb_client.fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(
        "app.matcher.tvdb_client.fetch_season_roster",
        lambda tvdb_id, season, api_key: [{"episode_number": 1, "name": "TVDB"}],
    )
    monkeypatch.setattr("app.matcher.tvdb_client.resolve_api_key", lambda cfg: "k")
    monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: type("C", (), {})())
    with ns.namespace_context("tvdb"):
        assert ns.season_episodes("1618", 1, "tok")[0]["name"] == "TVDB"


def test_season_episodes_tvdb_falls_back_to_tmdb(monkeypatch):
    monkeypatch.setattr("app.matcher.tmdb_client.fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(
        "app.matcher.tvdb_client.fetch_season_roster", lambda tvdb_id, season, api_key: None
    )
    monkeypatch.setattr("app.matcher.tvdb_client.resolve_api_key", lambda cfg: "k")
    monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: type("C", (), {})())
    monkeypatch.setattr(
        "app.matcher.tmdb_client.fetch_season_episodes",
        lambda show, season, key: [{"episode_number": 1, "name": "TMDB"}],
    )
    with ns.namespace_context("tvdb"):
        assert ns.season_episodes("1618", 1, "tok")[0]["name"] == "TMDB"


def test_season_runtimes_and_count(monkeypatch):
    monkeypatch.setattr(
        ns, "season_episodes", lambda show, season, key: [{"runtime": 24}, {"runtime": 0}]
    )
    assert ns.season_runtimes("1618", 1, "tok") == [24, 0]
    assert ns.season_episode_count("1618", 1, "tok") == 2
