"""Record live TMDB + TheTVDB season rosters as test fixtures (one-off).

Usage (from backend/):
    TVDB_API_KEY=... uv run python scripts/record_tvdb_fixtures.py

Reads the TMDB token from backend/engram.db (read-only). Writes the RAW API
payloads so the parsers are tested against what the services really return.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tvdb"
SHOWS = {
    # name: (tmdb_id, season)
    "justice_league_s1": (1618, 1),
    "dexters_lab_s1": (4229, 1),
}


def main() -> int:
    tvdb_key = os.environ.get("TVDB_API_KEY")
    if not tvdb_key:
        print("TVDB_API_KEY is not set", file=sys.stderr)
        return 2
    # A worktree's engram.db is often an empty stub; ENGRAM_DB points at a real one.
    db = Path(os.environ.get("ENGRAM_DB") or Path(__file__).resolve().parent.parent / "engram.db")
    tmdb_token = (
        sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        .execute("select tmdb_api_key from app_config")
        .fetchone()[0]
    )
    tmdb_headers = {"Authorization": f"Bearer {tmdb_token}"}

    login = requests.post(
        "https://api4.thetvdb.com/v4/login", json={"apikey": tvdb_key}, timeout=30
    )
    login.raise_for_status()
    tvdb_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    OUT.mkdir(parents=True, exist_ok=True)

    def get_json(url: str, headers: dict, params: dict | None = None) -> dict:
        # Fail loudly: an error body must never be recorded as a fixture.
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    for name, (tmdb_id, season) in SHOWS.items():
        ext = get_json(f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids", tmdb_headers)
        tvdb_id = ext.get("tvdb_id")
        if not tvdb_id:
            print(f"{name}: TMDB has no TheTVDB id", file=sys.stderr)
            return 1
        tmdb_season = get_json(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}", tmdb_headers
        )
        tvdb_season = get_json(
            f"https://api4.thetvdb.com/v4/series/{tvdb_id}/episodes/official",
            tvdb_headers,
            {"season": season, "page": 0},
        )
        tvdb_season.pop("token", None)  # defensive: never persist a credential
        (OUT / f"{name}_tmdb.json").write_text(json.dumps(tmdb_season, indent=1), "utf-8")
        (OUT / f"{name}_tvdb.json").write_text(
            json.dumps({"tvdb_id": tvdb_id, "response": tvdb_season}, indent=1), "utf-8"
        )
        print(name, "tmdb", len(tmdb_season["episodes"]), "tvdb_id", tvdb_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
