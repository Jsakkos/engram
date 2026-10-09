# TheTVDB Episode Namespace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a TV show opt into TheTVDB's episode numbering so discs whose layout follows TVDB (Justice League 2001 S1: one TMDB pilot, three TVDB/Blu-ray parts) download the right references, match, and file as `S01E01`..`S01E26`.

**Architecture:** A per-job `episode_namespace` (`"tmdb"` | `"tvdb"`). A `ContextVar` in `app/core/episode_namespace.py` carries the namespace into the matcher threads (the same mechanism loguru's `job=<id>` tag already uses through `asyncio.to_thread`), so the two chokepoints (`corpus_dir_name` and `load_precomputed_manifest`) isolate TVDB-numbered references without threading a parameter through 13 call sites. A persisted 1:1 crosswalk translates codes at the few TMDB-identity boundaries (fingerprint contribution, TheDiscDB export, inbound DiscDB/network hints) and omits anything not 1:1.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/aiosqlite, Alembic, `requests`, pytest; React 19 + TypeScript, Vitest + RTL.

**Spec:** `docs/superpowers/specs/2026-10-08-thetvdb-episode-namespace-design.md`

---

## Deviations from the spec (decided while mapping the code)

1. **Namespace decision point.** The spec says "at identification". Identification has 6+ exits (`next_state_after_identify` call sites, imports). The single chokepoint every TV job passes once its show and season are known is `MatchingCoordinator.download_subtitles`, and matching already blocks on that task's `_subtitle_ready` event. The decision runs there.
2. **TVDB roster cache** uses the existing generic key/value `tmdb_cache` table with a `tvdb_roster:` key prefix, not a new table. The table is already a generic JSON KV store with TTLs.
3. **Crosswalk is persisted on the job** (`DiscJob.episode_crosswalk_json`) when the namespace is decided, so contribution/export (sync code, run long after matching) need no network.
4. **`coverage_tracker` needs no change.** Nothing under `app/` writes it; only the cache builder scripts do.
5. **Organize "episode titles from TVDB"** is moot: `organize_tv_episode` names files from the code only, never the title.
6. **Playwright test dropped.** Simulation bypasses identification and there is no generic seed endpoint; adding one is new debug surface for one test. UI behavior is covered by Vitest component tests and backend API tests instead.
7. **Divergence by title** is "same set of normalized titles, different order" rather than "any title differs", so provider spelling differences do not nag users of every show.

## File structure

**Create**
| File | Responsibility |
|---|---|
| `backend/app/matcher/tvdb_client.py` | TheTVDB v4 HTTP client: key resolution, login/token, season roster, cache |
| `backend/app/core/episode_namespace.py` | Namespace ContextVar, title normalization, divergence, crosswalk, code translation, roster dispatch |
| `backend/app/services/episode_namespace_service.py` | DB-facing: decide a job's namespace, switch it, dismiss the suggestion |
| `backend/migrations/versions/b7d1e4a9c2f3_tvdb_episode_namespace.py` | Alembic revision for the new columns |
| `backend/scripts/record_tvdb_fixtures.py` | One-off: record live TMDB + TVDB rosters as test fixtures |
| `backend/tests/fixtures/tvdb/*.json` | Recorded rosters (Justice League S1, Dexter's Laboratory S1) |
| `backend/tests/unit/test_tvdb_client.py` | |
| `backend/tests/unit/test_episode_namespace.py` | |
| `backend/tests/unit/test_episode_namespace_service.py` | |
| `backend/tests/unit/test_tvdb_corpus_isolation.py` | |
| `backend/tests/unit/test_tvdb_boundaries.py` | |
| `backend/tests/unit/test_tvdb_namespace_api.py` | |
| `backend/tests/unit/test_matched_episode_consumers.py` | Guard rail |
| `backend/tests/unit/test_tvdb_schema_and_config.py` | |
| `frontend/src/components/ReviewQueue/TvdbSuggestionNotice.tsx` (+ `.test.tsx`) | Suggestion banner |
| `frontend/src/components/ReviewQueue/TvdbAttribution.tsx` (+ `.test.tsx`) | Attribution link |

**Modify**
`backend/app/models/{disc_job,show_ordering,app_config}.py`, `backend/app/matcher/{tmdb_client,subtitle_utils,episode_identification,testing_service}.py`, `backend/app/core/{episode_ordering,organizer}.py`, `backend/app/services/{matching_coordinator,disc_contribution_queue,episode_ordering_service,config_service}.py`, `backend/app/core/discdb_exporter.py`, `backend/app/api/routes.py`, `backend/tests/unit/conftest.py`, `backend/engram.spec`, `.github/workflows/release.yml`, `.gitignore`, `frontend/src/components/ReviewQueue/{types.ts,OrderingSelector.tsx}`, `frontend/src/components/ReviewQueue.tsx`, `frontend/src/api/client.ts`, `frontend/src/components/ConfigWizard.tsx`, `frontend/src/components/HistoryPage.tsx`, `CLAUDE.md`, `CHANGELOG.md`.

## Conventions for every task

- Run backend commands from `backend/` with `uv run`. Run frontend commands from `frontend/`.
- A narrow test run is `uv run pytest <file> -q -p no:cacheprovider`. Do **not** run the full unit suite inside a task (it takes >5 minutes); Task 16 does that once.
- Commit after each task with a `feat(tvdb): ...` / `test(tvdb): ...` subject and the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em dashes or en dashes in prose, comments, or UI strings (project style).
- Never delete `backend/engram.db`.

---

### Task 1: Schema: new columns and the Alembic revision

**Files:**
- Modify: `backend/app/models/disc_job.py` (after `tmdb_degraded_reason`, ~line 160)
- Modify: `backend/app/models/show_ordering.py`
- Modify: `backend/app/models/app_config.py` (after `tmdb_api_key`, line 57)
- Create: `backend/migrations/versions/b7d1e4a9c2f3_tvdb_episode_namespace.py`
- Test: `backend/tests/unit/test_tvdb_schema_and_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""Schema + config surface for the TheTVDB episode namespace (spec 2026-10-08)."""

from app.models.app_config import AppConfig
from app.models.disc_job import DiscJob
from app.models.show_ordering import ShowOrderingPreference


def test_disc_job_defaults_to_tmdb_namespace():
    job = DiscJob(drive_id="E:", volume_label="JUSTICE_LEAGUE_S1D1")
    assert job.episode_namespace == "tmdb"
    assert job.tvdb_divergence_json is None
    assert job.episode_namespace_note is None
    assert job.episode_crosswalk_json is None


def test_episode_namespace_has_server_default_for_upgraded_rows():
    # Without a server_default, rows that predate the column read NULL, and
    # every `== "tvdb"` check would treat them correctly but `== "tmdb"` would not.
    col = DiscJob.__table__.c.episode_namespace
    assert col.server_default is not None
    assert "tmdb" in str(col.server_default.arg)


def test_show_preference_tvdb_columns():
    pref = ShowOrderingPreference(tmdb_id=1618)
    assert pref.tvdb_id is None
    assert pref.tvdb_suggestion_dismissed is False
    col = ShowOrderingPreference.__table__.c.tvdb_suggestion_dismissed
    assert str(col.server_default.arg) == "0"


def test_appconfig_tvdb_key_defaults_blank():
    assert AppConfig().tvdb_api_key == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_tvdb_schema_and_config.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError` / `TypeError` on `episode_namespace`.

- [ ] **Step 3: Add the model fields**

`backend/app/models/disc_job.py`, directly after `tmdb_degraded_reason: str | None = None` (`text` is already imported from `sqlalchemy`):

```python
    # Which episode numbering this job's matched_episode codes, references and
    # filenames use (TheTVDB spec 2026-10-08). "tmdb" is the canonical default;
    # "tvdb" means TheTVDB official order. server_default so rows that predate
    # the column read "tmdb", not NULL.
    episode_namespace: str = Field(
        default="tmdb", sa_column_kwargs={"server_default": text("'tmdb'")}
    )
    # JSON {"season", "tmdb", "tvdb"} when TheTVDB numbers this season
    # differently; drives the review-page suggestion. None = no divergence known.
    tvdb_divergence_json: str | None = None
    # Prose shown on review/history when a TVDB job had to fall back to TMDB.
    episode_namespace_note: str | None = None
    # JSON {"S01E04": "S01E02", ...}: TVDB code -> TMDB code, 1:1 pairs only.
    # Persisted so contribution/export translate without a network call.
    episode_crosswalk_json: str | None = None
```

`backend/app/models/show_ordering.py`: add `from sqlalchemy import text` to the imports, then after `episode_group_id`:

```python
    # TheTVDB series id, resolved from TMDB external_ids and cached here so the
    # namespace decision does not re-resolve it per disc.
    tvdb_id: int | None = Field(default=None)
    # The user dismissed the "TheTVDB numbers this differently" suggestion.
    # server_default 0: an upgraded row must read as "not dismissed".
    tvdb_suggestion_dismissed: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
```

Also change the `ordering` comment to: `# One of episode_ordering.PER_SHOW_ORDERINGS ("aired", "dvd", "tvdb"); "" means "use the global default".`

`backend/app/models/app_config.py`, after `tmdb_api_key: str = ""`:

```python
    # TheTVDB v4 project key override. Blank means "use the key built into this
    # release" (TVDB_API_KEY env var, baked into frozen builds). Redacted on GET.
    tvdb_api_key: str = ""
```

- [ ] **Step 4: Write the Alembic revision**

Create `backend/migrations/versions/b7d1e4a9c2f3_tvdb_episode_namespace.py`:

```python
"""TheTVDB episode namespace columns

Per-job episode numbering (tmdb|tvdb), divergence summary, fallback note and
crosswalk; per-show tvdb_id + suggestion dismissal; tvdb_api_key override.
Mirrors database.py's _add_missing_columns reconciler, which is the path
frozen builds take (they skip Alembic). The two must stay in agreement.

Revision ID: b7d1e4a9c2f3
Revises: 02946b05fe8d
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

revision: str = "b7d1e4a9c2f3"
down_revision: str | Sequence[str] | None = "02946b05fe8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing(
        "disc_jobs",
        sa.Column(
            "episode_namespace", sa.String(), nullable=False, server_default=sa.text("'tmdb'")
        ),
    )
    add_column_if_missing("disc_jobs", sa.Column("tvdb_divergence_json", sa.String(), nullable=True))
    add_column_if_missing("disc_jobs", sa.Column("episode_namespace_note", sa.String(), nullable=True))
    add_column_if_missing("disc_jobs", sa.Column("episode_crosswalk_json", sa.String(), nullable=True))
    add_column_if_missing(
        "show_ordering_preferences", sa.Column("tvdb_id", sa.Integer(), nullable=True)
    )
    add_column_if_missing(
        "show_ordering_preferences",
        sa.Column(
            "tvdb_suggestion_dismissed", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    add_column_if_missing(
        "app_config",
        sa.Column("tvdb_api_key", sa.String(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("episode_crosswalk_json")
        batch_op.drop_column("episode_namespace_note")
        batch_op.drop_column("tvdb_divergence_json")
        batch_op.drop_column("episode_namespace")
    with op.batch_alter_table("show_ordering_preferences", schema=None) as batch_op:
        batch_op.drop_column("tvdb_suggestion_dismissed")
        batch_op.drop_column("tvdb_id")
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("tvdb_api_key")
```

- [ ] **Step 5: Run the test and the existing migration tests**

Run: `uv run pytest tests/unit/test_tvdb_schema_and_config.py tests/unit/test_episode_ordering_migration.py -q -p no:cacheprovider`
Expected: PASS.

Then verify the head is single: `uv run alembic heads`
Expected: exactly one line, `b7d1e4a9c2f3 (head)`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models backend/migrations/versions/b7d1e4a9c2f3_tvdb_episode_namespace.py backend/tests/unit/test_tvdb_schema_and_config.py
git commit -m "feat(tvdb): add episode-namespace columns and migration"
```

---

### Task 2: Record live roster fixtures

The tests in Tasks 3-4 parse **real** API responses, never synthetic ones (synthetic fixtures masked three provider-parser bugs in the past).

**Files:**
- Create: `backend/scripts/record_tvdb_fixtures.py`
- Create: `backend/tests/fixtures/tvdb/` (4 JSON files, produced by the script)

- [ ] **Step 1: Write the recording script**

```python
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
    # name: (tmdb_id, tvdb_id, season)
    "justice_league_s1": (1618, None, 1),
    "dexters_lab_s1": (4229, None, 1),
}


def main() -> int:
    tvdb_key = os.environ.get("TVDB_API_KEY")
    if not tvdb_key:
        print("TVDB_API_KEY is not set", file=sys.stderr)
        return 2
    db = Path(__file__).resolve().parent.parent / "engram.db"
    tmdb_token = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
        "select tmdb_api_key from app_config"
    ).fetchone()[0]
    tmdb_headers = {"Authorization": f"Bearer {tmdb_token}"}

    login = requests.post(
        "https://api4.thetvdb.com/v4/login", json={"apikey": tvdb_key}, timeout=30
    )
    login.raise_for_status()
    tvdb_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    OUT.mkdir(parents=True, exist_ok=True)
    for name, (tmdb_id, _, season) in SHOWS.items():
        ext = requests.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids",
            headers=tmdb_headers,
            timeout=30,
        ).json()
        tvdb_id = ext["tvdb_id"]
        tmdb_season = requests.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}",
            headers=tmdb_headers,
            timeout=30,
        ).json()
        tvdb_season = requests.get(
            f"https://api4.thetvdb.com/v4/series/{tvdb_id}/episodes/official",
            params={"season": season, "page": 0},
            headers=tvdb_headers,
            timeout=30,
        ).json()
        (OUT / f"{name}_tmdb.json").write_text(json.dumps(tmdb_season, indent=1), "utf-8")
        (OUT / f"{name}_tvdb.json").write_text(
            json.dumps({"tvdb_id": tvdb_id, "response": tvdb_season}, indent=1), "utf-8"
        )
        print(name, "tmdb", len(tmdb_season["episodes"]), "tvdb_id", tvdb_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it** (the user's TVDB key is required; ask for it if `TVDB_API_KEY` is unset)

Run: `TVDB_API_KEY=<key> uv run python scripts/record_tvdb_fixtures.py`
Expected output includes `justice_league_s1 tmdb 24` and `dexters_lab_s1 tmdb 38`.

- [ ] **Step 3: Sanity-check the TVDB payload shape**

Run: `uv run python -c "import json;d=json.load(open('tests/fixtures/tvdb/justice_league_s1_tvdb.json'));e=d['response']['data']['episodes'];print(d['tvdb_id'],len(e),e[0]['seasonNumber'],e[0]['number'],e[0]['name'],e[0].get('runtime'),e[0].get('aired'));print(d['response'].get('links'))"`
Expected: `26` episodes, first is season 1 number 1 named `Secret Origins (1)` (or `Secret Origins (Part 1)`). Note the exact field names and `links.next`; if they differ from `seasonNumber`/`number`/`name`/`runtime`/`aired`/`links.next`, use the observed names in Task 3's `_parse_episodes`.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/record_tvdb_fixtures.py backend/tests/fixtures/tvdb
git commit -m "test(tvdb): record live TMDB/TVDB roster fixtures"
```

---

### Task 3: `tvdb_client`: key resolution, login, season roster

**Files:**
- Create: `backend/app/matcher/tvdb_client.py`
- Modify: `backend/app/matcher/tmdb_persistent_cache.py` (add one TTL constant)
- Test: `backend/tests/unit/test_tvdb_client.py`

- [ ] **Step 1: Write the failing tests**

```python
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
    monkeypatch.setattr(
        tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used")
    )
    assert tvdb_client.fetch_season_roster(76290, 1, api_key="") is None


def test_invalid_series_id_rejected(monkeypatch):
    monkeypatch.setattr(
        tvdb_client.requests, "post", lambda *a, **k: pytest.fail("network used")
    )
    assert tvdb_client.fetch_season_roster("1/../x", 1, api_key="k") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_client.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: app.matcher.tvdb_client`.

- [ ] **Step 3: Add the TTL constant**

In `backend/app/matcher/tmdb_persistent_cache.py`, after `TTL_EPISODE_GROUP`:

```python
# TheTVDB season rosters share this KV table under a "tvdb_roster:" prefix.
TTL_TVDB_ROSTER = 7 * 86400
```

- [ ] **Step 4: Implement the client**

`backend/app/matcher/tvdb_client.py`:

```python
"""TheTVDB v4 client: the secondary episode-numbering source (spec 2026-10-08).

Only what the episode namespace needs: log in with the project key and fetch a
season's episodes in TheTVDB's *official* order. Everything degrades to None
("no TVDB data"); callers then behave exactly as they did before TVDB existed.

Licensing: Engram uses TheTVDB's free licensed tier, which requires a visible
attribution link wherever TVDB metadata is shown (see TvdbAttribution.tsx).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import requests
from loguru import logger

from app.matcher import tmdb_persistent_cache

_BASE = "https://api4.thetvdb.com/v4"
_MAX_PAGES = 20  # a season never needs more; bounds a misbehaving `links.next`
_RETRIES = 2


@dataclass
class _TokenState:
    token: str | None = None
    key: str | None = None


_token_state = _TokenState()
_token_lock = threading.Lock()


def resolve_api_key(config) -> str:
    """The key to use: the config override, else the built-in TVDB_API_KEY env var."""
    override = (getattr(config, "tvdb_api_key", "") or "").strip()
    if override:
        return override
    return (os.environ.get("TVDB_API_KEY") or "").strip()


def _login(api_key: str) -> str | None:
    resp = requests.post(f"{_BASE}/login", json={"apikey": api_key}, timeout=30)
    resp.raise_for_status()
    token = (resp.json().get("data") or {}).get("token")
    return token or None


def _token(api_key: str, *, force: bool = False) -> str | None:
    with _token_lock:
        if force or not _token_state.token or _token_state.key != api_key:
            _token_state.token = _login(api_key)
            _token_state.key = api_key
        return _token_state.token


def _get(path: str, params: dict, api_key: str) -> dict:
    """Authenticated GET with one re-login on 401. Raises on failure."""
    token = _token(api_key)
    resp = requests.get(
        f"{_BASE}{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )
    if resp.status_code == 401:
        token = _token(api_key, force=True)
        resp = requests.get(
            f"{_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()


def _parse_episodes(payload: dict, season: int) -> list[dict]:
    episodes = ((payload.get("data") or {}).get("episodes")) or []
    out = []
    for ep in episodes:
        if ep.get("seasonNumber") != season or ep.get("number") is None:
            continue
        out.append(
            {
                "episode_number": int(ep["number"]),
                "name": ep.get("name") or "",
                "runtime": ep.get("runtime") or 0,
                "overview": ep.get("overview") or "",
                "air_date": ep.get("aired") or "",
            }
        )
    return out


def fetch_season_roster(tvdb_id, season: int, *, api_key: str) -> list[dict] | None:
    """Episodes of ``season`` in TheTVDB official order, or None on any failure.

    Same dict shape as ``tmdb_client.fetch_season_episodes`` (plus ``air_date``)
    so callers can swap sources without branching on shape.
    """
    if not api_key:
        return None
    if not str(tvdb_id).isdigit():
        logger.warning(f"Invalid TheTVDB series id: {tvdb_id!r}")
        return None
    series = int(tvdb_id)
    cache_key = f"tvdb_roster:{series}:{season}"
    cached = tmdb_persistent_cache.get(cache_key)
    if cached is not None:
        return cached

    for attempt in range(_RETRIES + 1):
        try:
            roster: list[dict] = []
            for page in range(_MAX_PAGES):
                payload = _get(
                    f"/series/{series}/episodes/official",
                    {"season": season, "page": page},
                    api_key,
                )
                roster.extend(_parse_episodes(payload, season))
                if not ((payload.get("links") or {}).get("next")):
                    break
            roster.sort(key=lambda e: e["episode_number"])
            if roster:
                tmdb_persistent_cache.put(
                    cache_key, roster, tmdb_persistent_cache.TTL_TVDB_ROSTER
                )
            return roster or None
        except (requests.RequestException, ConnectionError, TimeoutError, ValueError) as e:
            if attempt == _RETRIES:
                logger.warning(f"TheTVDB roster fetch failed for {series} S{season}: {e}")
                return None
            time.sleep(1.0 * (attempt + 1))
    return None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_tvdb_client.py -q -p no:cacheprovider`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/tvdb_client.py backend/app/matcher/tmdb_persistent_cache.py backend/tests/unit/test_tvdb_client.py
git commit -m "feat(tvdb): TheTVDB v4 client for season rosters"
```

---

### Task 4: `tmdb_client`: `fetch_tvdb_id` and `air_date` on season episodes

**Files:**
- Modify: `backend/app/matcher/tmdb_client.py` (`fetch_season_episodes` ~line 870; new function after `fetch_episode_group`)
- Test: append to `backend/tests/unit/test_tvdb_client.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.matcher import tmdb_client


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_client.py -q -p no:cacheprovider`
Expected: FAIL (`fetch_tvdb_id` missing; `air_date` KeyError).

- [ ] **Step 3: Implement**

In `fetch_season_episodes`, add to the returned dict: `"air_date": ep.get("air_date") or "",`.

After `fetch_episode_group`, add:

```python
def fetch_tvdb_id(show_id: str, api_key: str) -> int | None:
    """TheTVDB series id for a TMDB show, via /tv/{id}/external_ids.

    Lets the episode namespace link a show to TheTVDB without a fuzzy name
    search. Positive results are cached with the show-id TTL; a show TMDB has
    no TVDB link for returns None (and is not cached, so a later TMDB edit is
    picked up).
    """
    if not api_key or not str(show_id).isdigit():
        return None
    show_id_int = int(show_id)
    persistent_key = f"tvdb_id:{show_id_int}"
    cached = tmdb_persistent_cache.get(persistent_key)
    if cached is not None:
        return int(cached)
    data = _tmdb_get_json(f"https://api.themoviedb.org/3/tv/{show_id_int}/external_ids", api_key)
    tvdb_id = (data or {}).get("tvdb_id")
    if not tvdb_id:
        return None
    tmdb_persistent_cache.put(persistent_key, int(tvdb_id), tmdb_persistent_cache.TTL_SHOW_ID)
    return int(tvdb_id)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tvdb_client.py tests/unit/test_tmdb_client.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/tmdb_client.py backend/tests/unit/test_tvdb_client.py
git commit -m "feat(tvdb): resolve TheTVDB id from TMDB external ids"
```

---

### Task 5: `episode_namespace` core: context, normalization, divergence, crosswalk

**Files:**
- Create: `backend/app/core/episode_namespace.py`
- Test: `backend/tests/unit/test_episode_namespace.py`

- [ ] **Step 1: Write the failing tests**

```python
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
        ("Secret Origins (1)", "secretorigins"),
        ("Secret Origins", "secretorigins"),
        ("Secret Origins: Part I", "secretorigins"),
        ("In Blackest Night (2)", "inblackestnight"),
        ("The Enemy Below, Part 2", "theenemybelow"),
        ("Parting Shot", "partingshot"),
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
    cw = ns.build_crosswalk(1, _tmdb("justice_league_s1"), _tvdb("justice_league_s1"))
    for part in ("S01E01", "S01E02", "S01E03"):
        assert part not in cw
    assert cw["S01E04"] == "S01E02"  # In Blackest Night (1)
    assert cw["S01E05"] == "S01E03"  # In Blackest Night (2)
    assert len(set(cw.values())) == len(cw)  # strictly 1:1


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_episode_namespace.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Before writing `to_tmdb_code`, confirm the helpers in `backend/app/core/episode_codes.py`: `parse_episode_code(code) -> (season, [episodes]) | None` and `format_episode_code(season, episodes) -> str` (both are already used by `matching_coordinator.try_discdb_assignment`).

`backend/app/core/episode_namespace.py`:

```python
"""Which episode numbering a job uses: TMDB (canonical) or TheTVDB (spec 2026-10-08).

The namespace rides in a ContextVar so the matcher's 13 ``corpus_dir_name``
call sites and its precomputed-pack reads pick it up without a parameter being
threaded through every signature. ``asyncio.to_thread`` copies the context
into the worker thread, which is the same mechanism that carries loguru's
``job=<id>`` tag (``app/core/log_context.py``). Code that runs in a thread it
did not spawn through ``to_thread`` (long-lived scheduler workers) sees the
default, "tmdb"; none of those workers computes cache paths.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.episode_codes import format_episode_code, parse_episode_code

NAMESPACE_TMDB = "tmdb"
NAMESPACE_TVDB = "tvdb"
NAMESPACES = frozenset({NAMESPACE_TMDB, NAMESPACE_TVDB})

_namespace_var: ContextVar[str] = ContextVar("episode_namespace", default=NAMESPACE_TMDB)


def current_namespace() -> str:
    return _namespace_var.get()


@contextlib.contextmanager
def namespace_context(namespace: str | None):
    """Bind ``namespace`` for the enclosed code (None means "tmdb")."""
    value = namespace or NAMESPACE_TMDB
    if value not in NAMESPACES:
        raise ValueError(f"unknown episode namespace: {value!r}")
    token = _namespace_var.set(value)
    try:
        yield
    finally:
        _namespace_var.reset(token)


def corpus_dir_suffix() -> str:
    """Suffix for the reference-cache folder, so TVDB-numbered references never
    share a folder with the TMDB-numbered ones (or the published pack)."""
    return "@tvdb" if current_namespace() == NAMESPACE_TVDB else ""


_PART_SUFFIX_RE = re.compile(
    r"(?:[\s:,\-]+\(?\s*(?:part|pt\.?)\s*(?:\d+|[ivx]+|one|two|three|four|five)\s*\)?"
    r"|\s*\(\s*\d+\s*\))\s*$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def normalize_title(name: str) -> str:
    """Lowercase, drop a trailing part marker ("(1)", "Part I", ", Part 2"), keep a-z0-9."""
    base = _PART_SUFFIX_RE.sub("", (name or "").strip())
    return _NON_ALNUM_RE.sub("", base.lower())


@dataclass(frozen=True)
class Divergence:
    season: int
    tmdb_count: int
    tvdb_count: int

    def to_json(self) -> str:
        return json.dumps({"season": self.season, "tmdb": self.tmdb_count, "tvdb": self.tvdb_count})


def detect_divergence(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> Divergence | None:
    """TheTVDB numbers the season differently from TMDB, or None.

    Divergent when the episode counts differ, or when both lists hold the same
    titles in a different order. A title spelled differently by the two
    catalogues is deliberately NOT divergence: that would nag every show.
    """
    if not tmdb_eps or not tvdb_eps:
        return None
    div = Divergence(season, len(tmdb_eps), len(tvdb_eps))
    if len(tmdb_eps) != len(tvdb_eps):
        return div
    a = [normalize_title(e.get("name", "")) for e in tmdb_eps]
    b = [normalize_title(e.get("name", "")) for e in tvdb_eps]
    if Counter(a) == Counter(b) and a != b:
        return div
    return None


def build_crosswalk(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> dict[str, str]:
    """TVDB code -> TMDB code for episodes that pair 1:1, else absent.

    Pairs by normalized title first; a title held by more than one episode on
    either side (the three Secret Origins parts) is ambiguous and skipped. Any
    episodes still unpaired pair by air date when exactly one of each shares it.
    """

    def code(ep: dict) -> str:
        return f"S{season:02d}E{int(ep['episode_number']):02d}"

    tmdb_by_title: dict[str, list[dict]] = {}
    for ep in tmdb_eps:
        tmdb_by_title.setdefault(normalize_title(ep.get("name", "")), []).append(ep)
    tvdb_by_title: dict[str, list[dict]] = {}
    for ep in tvdb_eps:
        tvdb_by_title.setdefault(normalize_title(ep.get("name", "")), []).append(ep)

    crosswalk: dict[str, str] = {}
    used_tmdb: set[str] = set()
    for title, tv in tvdb_by_title.items():
        tm = tmdb_by_title.get(title, [])
        if title and len(tv) == 1 and len(tm) == 1:
            crosswalk[code(tv[0])] = code(tm[0])
            used_tmdb.add(code(tm[0]))

    ambiguous_titles = {
        t for t in set(tmdb_by_title) | set(tvdb_by_title)
        if len(tmdb_by_title.get(t, [])) > 1 or len(tvdb_by_title.get(t, [])) > 1
    }

    def by_date(eps: list[dict], taken: set[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for ep in eps:
            if code(ep) in taken or normalize_title(ep.get("name", "")) in ambiguous_titles:
                continue
            if ep.get("air_date"):
                out.setdefault(ep["air_date"], []).append(ep)
        return out

    tm_dates = by_date(tmdb_eps, used_tmdb)
    tv_dates = by_date(tvdb_eps, set(crosswalk))
    for date, tv in tv_dates.items():
        tm = tm_dates.get(date, [])
        if len(tv) == 1 and len(tm) == 1:
            crosswalk[code(tv[0])] = code(tm[0])
    return crosswalk


def _translate(code: str | None, mapping: dict[str, str]) -> str | None:
    parsed = parse_episode_code(code)
    if parsed is None:
        return None
    season, episodes = parsed
    out = []
    out_season = None
    for ep in episodes:
        mapped = mapping.get(f"S{season:02d}E{ep:02d}")
        if mapped is None:
            return None
        m = parse_episode_code(mapped)
        if m is None or (out_season is not None and m[0] != out_season):
            return None
        out_season = m[0]
        out.extend(m[1])
    return format_episode_code(out_season, out)


def to_tmdb_code(namespace: str | None, crosswalk_json: str | None, code: str | None) -> str | None:
    """A stored code as the TMDB code it denotes, or None when not 1:1."""
    if (namespace or NAMESPACE_TMDB) == NAMESPACE_TMDB:
        return code
    if not crosswalk_json:
        return None
    return _translate(code, json.loads(crosswalk_json))


def from_tmdb_code(namespace: str | None, crosswalk_json: str | None, code: str | None) -> str | None:
    """A TMDB code (e.g. a DiscDB hint) in the job's namespace, or None when not 1:1."""
    if (namespace or NAMESPACE_TMDB) == NAMESPACE_TMDB:
        return code
    if not crosswalk_json:
        return None
    inverse = {tm: tv for tv, tm in json.loads(crosswalk_json).items()}
    return _translate(code, inverse)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_episode_namespace.py -q -p no:cacheprovider`
Expected: PASS. If `test_crosswalk_skips_the_split_pilot_and_maps_the_rest` fails on `S01E04`, print `cw` and the two fixtures' first six titles; TVDB may title the parts differently (for example `Secret Origins, Part I`). Extend `_PART_SUFFIX_RE` to cover the observed form and add it to `test_normalize_title`. Do not loosen the 1:1 rule.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/episode_namespace.py backend/tests/unit/test_episode_namespace.py
git commit -m "feat(tvdb): episode-namespace context, divergence and crosswalk"
```

---

### Task 6: Roster dispatch: one function that serves either namespace

**Files:**
- Modify: `backend/app/core/episode_namespace.py` (append)
- Test: append to `backend/tests/unit/test_episode_namespace.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
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
    monkeypatch.setattr(
        "app.services.config_service.get_config_sync", lambda: type("C", (), {})()
    )
    with ns.namespace_context("tvdb"):
        assert ns.season_episodes("1618", 1, "tok")[0]["name"] == "TVDB"


def test_season_episodes_tvdb_falls_back_to_tmdb(monkeypatch):
    monkeypatch.setattr("app.matcher.tmdb_client.fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(
        "app.matcher.tvdb_client.fetch_season_roster", lambda tvdb_id, season, api_key: None
    )
    monkeypatch.setattr("app.matcher.tvdb_client.resolve_api_key", lambda cfg: "k")
    monkeypatch.setattr(
        "app.services.config_service.get_config_sync", lambda: type("C", (), {})()
    )
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_episode_namespace.py -q -p no:cacheprovider`
Expected: FAIL (`season_episodes` missing).

- [ ] **Step 3: Implement** (append to `episode_namespace.py`)

```python
def season_episodes(tmdb_show_id: str, season: int, tmdb_api_key: str) -> list[dict]:
    """The season roster in the CURRENT namespace (sync; call off the event loop).

    TVDB when bound and reachable; otherwise TMDB, so a TVDB outage degrades
    to today's behavior instead of an empty roster.
    """
    from app.matcher import tmdb_client

    if current_namespace() == NAMESPACE_TVDB:
        from app.matcher import tvdb_client
        from app.services.config_service import get_config_sync

        key = tvdb_client.resolve_api_key(get_config_sync())
        tvdb_id = tmdb_client.fetch_tvdb_id(str(tmdb_show_id), tmdb_api_key)
        if key and tvdb_id:
            roster = tvdb_client.fetch_season_roster(tvdb_id, season, api_key=key)
            if roster:
                return roster
    return tmdb_client.fetch_season_episodes(str(tmdb_show_id), season, tmdb_api_key)


def season_runtimes(tmdb_show_id: str, season: int, tmdb_api_key: str) -> list[int]:
    return [int(e.get("runtime") or 0) for e in season_episodes(tmdb_show_id, season, tmdb_api_key)]


def season_episode_count(tmdb_show_id: str, season: int, tmdb_api_key: str) -> int:
    return len(season_episodes(tmdb_show_id, season, tmdb_api_key))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_episode_namespace.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/episode_namespace.py backend/tests/unit/test_episode_namespace.py
git commit -m "feat(tvdb): namespace-aware season roster dispatch"
```

---

### Task 7: Reference-corpus isolation (the two chokepoints + download)

**Files:**
- Modify: `backend/app/matcher/subtitle_utils.py:295` (`corpus_dir_name`)
- Modify: `backend/app/matcher/episode_identification.py:147` (`load_precomputed_manifest`) and `:1327` (`EpisodeMatcher._load_precomputed_manifest`)
- Modify: `backend/app/matcher/testing_service.py:715` (`download_subtitles` episode count)
- Test: `backend/tests/unit/test_tvdb_corpus_isolation.py`

- [ ] **Step 1: Write the failing tests**

```python
"""TVDB-numbered references never mix with TMDB ones or the published pack."""

from app.core.episode_namespace import namespace_context
from app.matcher import episode_identification
from app.matcher.subtitle_utils import corpus_dir_name


def test_corpus_dir_name_suffixes_under_tvdb():
    assert corpus_dir_name(1618, "Justice League") == "1618"
    with namespace_context("tvdb"):
        assert corpus_dir_name(1618, "Justice League") == "1618@tvdb"
        assert corpus_dir_name(None, "Justice League") == "Justice League@tvdb"


def test_precomputed_manifest_hidden_under_tvdb(tmp_path, monkeypatch):
    monkeypatch.setattr(
        episode_identification, "_read_and_validate_manifest", lambda p: {"shows": {}}, raising=False
    )
    (tmp_path / "precomputed").mkdir()
    (tmp_path / "precomputed" / "manifest.json").write_text("{}", "utf-8")
    with namespace_context("tvdb"):
        assert episode_identification.load_precomputed_manifest(tmp_path) is None


def test_matcher_instance_cache_does_not_leak_tmdb_manifest(monkeypatch):
    matcher = episode_identification.EpisodeMatcher.__new__(episode_identification.EpisodeMatcher)
    matcher._precomputed_manifest = {"cached": "tmdb-manifest"}
    with namespace_context("tvdb"):
        assert matcher._load_precomputed_manifest() is None
    assert matcher._load_precomputed_manifest() == {"cached": "tmdb-manifest"}


def test_download_uses_namespace_episode_count(monkeypatch, tmp_path):
    from app.matcher import testing_service

    seen = {}

    def fake_count(show_id, season, key):
        seen["count_called"] = True
        return 26

    monkeypatch.setattr(testing_service, "season_episode_count", fake_count)
    monkeypatch.setattr(testing_service, "fetch_season_details", lambda show, season: 24)
    assert testing_service._season_episode_count("1618", 1) == 24  # TMDB path
    with namespace_context("tvdb"):
        assert testing_service._season_episode_count("1618", 1) == 26
    assert seen["count_called"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_corpus_isolation.py -q -p no:cacheprovider`
Expected: FAIL (no suffix; manifest returned; `_season_episode_count` missing). If `test_precomputed_manifest_hidden_under_tvdb` errors because the monkeypatched helper name does not exist, delete that `monkeypatch.setattr` line: the test only needs the function to return None under tvdb before reading anything.

- [ ] **Step 3: Implement the chokepoints**

`subtitle_utils.corpus_dir_name`:

```python
def corpus_dir_name(tmdb_id, show_name: str) -> str:
    """... (keep the existing docstring) ...

    Under a TheTVDB-numbered job (``episode_namespace`` context) the name gains
    an ``@tvdb`` suffix: those references are numbered differently and must
    never share a folder with the TMDB-numbered ones (spec 2026-10-08).
    """
    from app.core.episode_namespace import corpus_dir_suffix

    if tmdb_id is not None and str(tmdb_id).strip():
        return f"{tmdb_id}{corpus_dir_suffix()}"
    return f"{sanitize_filename(show_name)}{corpus_dir_suffix()}"
```

`episode_identification.load_precomputed_manifest`: first statement of the function body:

```python
    # The published pack is TMDB-numbered; a TheTVDB-numbered job must never
    # read it (spec 2026-10-08). Every precomputed read goes through here.
    from app.core.episode_namespace import NAMESPACE_TVDB, current_namespace

    if current_namespace() == NAMESPACE_TVDB:
        return None
```

`EpisodeMatcher._load_precomputed_manifest`: same guard as its first statement (before the instance cache check), because the singleton matcher caches the manifest across jobs:

```python
        from app.core.episode_namespace import NAMESPACE_TVDB, current_namespace

        if current_namespace() == NAMESPACE_TVDB:
            return None
```

- [ ] **Step 4: Implement the download count**

In `testing_service.py`, add the import near the other `tmdb_client` imports:

```python
from app.core.episode_namespace import season_episode_count
```

and a helper above `download_subtitles`:

```python
def _season_episode_count(show_id: str, season: int) -> int:
    """Episodes in the season under the current namespace (TVDB counts 26 for
    Justice League S1 where TMDB counts 24). TMDB path keeps the cached
    ``fetch_season_details`` so its persistent cache is still used."""
    from app.core.episode_namespace import NAMESPACE_TVDB, current_namespace
    from app.services.config_service import get_config_sync

    if current_namespace() == NAMESPACE_TVDB:
        return season_episode_count(show_id, season, get_config_sync().tmdb_api_key)
    return fetch_season_details(show_id, season)
```

Replace line 715 `episode_count = fetch_season_details(show_id, season)` with `episode_count = _season_episode_count(show_id, season)`. Leave line 492 (the precomputed heal path) alone: it is unreachable under tvdb because the manifest is hidden.

- [ ] **Step 5: Run tests** (new file plus the suites that cover these functions)

Run: `uv run pytest tests/unit/test_tvdb_corpus_isolation.py tests/unit/test_download_subtitles_tmdb_id.py -q -p no:cacheprovider`
Then: `uv run pytest tests/unit -q -p no:cacheprovider -k "precomputed or corpus or subtitle_cache"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher backend/tests/unit/test_tvdb_corpus_isolation.py
git commit -m "feat(tvdb): isolate TVDB-numbered references from the TMDB corpus"
```

---

### Task 8: Namespace service: decide, switch, dismiss

**Files:**
- Create: `backend/app/services/episode_namespace_service.py`
- Modify: `backend/tests/unit/conftest.py` (patch the new module's `async_session`)
- Test: `backend/tests/unit/test_episode_namespace_service.py`

- [ ] **Step 1: Register the module in the unit-test DB isolation**

In `tests/unit/conftest.py` `isolate_database`, beside the other `importlib.import_module` lines add
`_ns_mod = importlib.import_module("app.services.episode_namespace_service")` and beside the other `monkeypatch.setattr(..., "async_session", _unit_session_factory)` lines add
`monkeypatch.setattr(_ns_mod, "async_session", _unit_session_factory)`.

- [ ] **Step 2: Write the failing tests**

```python
"""Deciding and switching a job's episode namespace (spec 2026-10-08)."""

import json

import pytest

from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscJob
from app.models.show_ordering import ShowOrderingPreference
from app.services import episode_namespace_service as svc
from tests.unit.conftest import _unit_session_factory

TMDB = [{"episode_number": i, "name": f"Ep {i}", "air_date": ""} for i in range(1, 25)]
TMDB[0]["name"] = "Secret Origins"
TVDB = [{"episode_number": i, "name": f"Ep {i - 2}", "air_date": ""} for i in range(1, 27)]
for i, part in enumerate(("(1)", "(2)", "(3)")):
    TVDB[i]["name"] = f"Secret Origins {part}"


@pytest.fixture
def stubs(monkeypatch):
    state = {"tvdb": TVDB}

    async def fake_config():
        return AppConfig(tmdb_api_key="tok", tvdb_api_key="k")

    monkeypatch.setattr("app.services.config_service.get_config", fake_config)
    monkeypatch.setattr(svc.tmdb_client, "fetch_tvdb_id", lambda show, key: 76290)
    monkeypatch.setattr(svc.tmdb_client, "fetch_season_episodes", lambda s, n, k: TMDB)
    monkeypatch.setattr(
        svc.tvdb_client, "fetch_season_roster", lambda t, n, api_key: state["tvdb"]
    )
    return state


async def _job(**kw) -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
            detected_season=1,
            **kw,
        )
        s.add(job)
        await s.commit()
        return job.id


async def _get(job_id):
    async with _unit_session_factory() as s:
        return await s.get(DiscJob, job_id)


@pytest.mark.unit
class TestDecide:
    async def test_records_divergence_and_stays_tmdb(self, stubs):
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert json.loads(job.tvdb_divergence_json) == {"season": 1, "tmdb": 24, "tvdb": 26}

    async def test_show_preference_tvdb_starts_in_tvdb(self, stubs):
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
            await s.commit()
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tvdb"
        job = await _get(job_id)
        assert job.episode_namespace == "tvdb"
        assert json.loads(job.episode_crosswalk_json)["S01E04"] == "S01E02"
        assert job.episode_namespace_note is None

    async def test_preference_tvdb_but_tvdb_down_falls_back_with_note(self, stubs):
        stubs["tvdb"] = None
        async with _unit_session_factory() as s:
            s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb", tvdb_id=76290))
            await s.commit()
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        job = await _get(job_id)
        assert "TheTVDB unavailable" in job.episode_namespace_note

    async def test_no_key_is_a_no_op(self, stubs, monkeypatch):
        async def no_key():
            return AppConfig(tmdb_api_key="tok", tvdb_api_key="")

        monkeypatch.setattr("app.services.config_service.get_config", no_key)
        monkeypatch.delenv("TVDB_API_KEY", raising=False)
        job_id = await _job()
        assert await svc.decide_job_namespace(job_id) == "tmdb"
        assert (await _get(job_id)).tvdb_divergence_json is None

    async def test_movie_job_is_a_no_op(self, stubs):
        async with _unit_session_factory() as s:
            job = DiscJob(drive_id="E:", volume_label="M", content_type=ContentType.MOVIE)
            s.add(job)
            await s.commit()
            job_id = job.id
        assert await svc.decide_job_namespace(job_id) == "tmdb"


@pytest.mark.unit
class TestSwitchAndDismiss:
    async def test_switch_to_tvdb_sets_job_and_show(self, stubs):
        job_id = await _job()
        await svc.switch_job_namespace(job_id, "tvdb")
        job = await _get(job_id)
        assert job.episode_namespace == "tvdb"
        assert job.episode_crosswalk_json
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.ordering == "tvdb" and pref.tvdb_id == 76290

    async def test_switch_to_tvdb_when_tvdb_down_raises_and_changes_nothing(self, stubs):
        stubs["tvdb"] = None
        job_id = await _job()
        with pytest.raises(svc.TvdbUnavailableError):
            await svc.switch_job_namespace(job_id, "tvdb")
        assert (await _get(job_id)).episode_namespace == "tmdb"

    async def test_switch_back_to_tmdb_clears_crosswalk_and_preference(self, stubs):
        job_id = await _job()
        await svc.switch_job_namespace(job_id, "tvdb")
        await svc.switch_job_namespace(job_id, "tmdb")
        job = await _get(job_id)
        assert job.episode_namespace == "tmdb" and job.episode_crosswalk_json is None
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.ordering == ""  # falls back to the global default

    async def test_dismiss_does_not_override_global_ordering(self, stubs):
        await svc.dismiss_tvdb_suggestion(1618)
        async with _unit_session_factory() as s:
            pref = await s.get(ShowOrderingPreference, 1618)
        assert pref.tvdb_suggestion_dismissed is True
        assert pref.ordering == ""
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_episode_namespace_service.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement**

`backend/app/services/episode_namespace_service.py`:

```python
"""Decide, switch and dismiss a job's episode namespace (spec 2026-10-08).

``decide_job_namespace`` runs once per subtitle download (the one point every
TV job passes after its show and season are known, and the point matching
already waits on). It never raises: any TVDB problem leaves the job on TMDB.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from app.core import episode_namespace as ns
from app.database import async_session
from app.matcher import tmdb_client, tvdb_client
from app.models.disc_job import ContentType, DiscJob
from app.models.show_ordering import ShowOrderingPreference

TVDB_UNAVAILABLE_NOTE = "TheTVDB unavailable; matched with TMDB numbering"


class TvdbUnavailableError(RuntimeError):
    """TheTVDB could not supply a roster, so the switch was not applied."""


async def _rosters(tmdb_id: int, season: int, config, tvdb_id: int | None):
    key = tvdb_client.resolve_api_key(config)
    if not key:
        return None, None, None
    if tvdb_id is None:
        tvdb_id = await asyncio.to_thread(
            tmdb_client.fetch_tvdb_id, str(tmdb_id), config.tmdb_api_key
        )
    if not tvdb_id:
        return None, None, None
    tmdb_eps, tvdb_eps = await asyncio.gather(
        asyncio.to_thread(
            tmdb_client.fetch_season_episodes, str(tmdb_id), season, config.tmdb_api_key
        ),
        asyncio.to_thread(tvdb_client.fetch_season_roster, tvdb_id, season, api_key=key),
    )
    return tvdb_id, tmdb_eps or None, tvdb_eps or None


async def decide_job_namespace(job_id: int) -> str:
    """Set and return the job's namespace; record divergence when on TMDB."""
    from app.services.config_service import get_config

    try:
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return ns.NAMESPACE_TMDB
            current = job.episode_namespace or ns.NAMESPACE_TMDB
            if (
                job.content_type != ContentType.TV
                or not job.tmdb_id
                or job.detected_season is None
            ):
                return current
            config = await get_config()
            pref = await session.get(ShowOrderingPreference, job.tmdb_id)
            tvdb_id, tmdb_eps, tvdb_eps = await _rosters(
                job.tmdb_id, job.detected_season, config, pref.tvdb_id if pref else None
            )
            if pref is not None and tvdb_id and pref.tvdb_id != tvdb_id:
                pref.tvdb_id = tvdb_id

            if pref is not None and pref.ordering == ns.NAMESPACE_TVDB:
                if tvdb_eps and tmdb_eps:
                    job.episode_namespace = ns.NAMESPACE_TVDB
                    job.episode_crosswalk_json = _crosswalk_json(
                        job.detected_season, tmdb_eps, tvdb_eps
                    )
                    job.episode_namespace_note = None
                else:
                    job.episode_namespace = ns.NAMESPACE_TMDB
                    job.episode_namespace_note = TVDB_UNAVAILABLE_NOTE
            elif tmdb_eps and tvdb_eps:
                div = ns.detect_divergence(job.detected_season, tmdb_eps, tvdb_eps)
                job.tvdb_divergence_json = div.to_json() if div else None
            await session.commit()
            return job.episode_namespace or ns.NAMESPACE_TMDB
    except Exception as e:  # noqa: BLE001 - the namespace decision must never fail a job
        logger.warning(f"Episode-namespace decision failed for job {job_id}: {e}", exc_info=True)
        return ns.NAMESPACE_TMDB


def _crosswalk_json(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> str:
    import json

    return json.dumps(ns.build_crosswalk(season, tmdb_eps, tvdb_eps))


async def switch_job_namespace(job_id: int, namespace: str) -> DiscJob:
    """Move a job (and its show's preference) to ``namespace``.

    Raises ``TvdbUnavailableError`` when switching to TVDB without a roster,
    ``ValueError`` for an unknown namespace or a job that is not an identified
    TV job. Re-downloading and re-matching are the caller's job (the route).
    """
    from app.services.config_service import get_config

    if namespace not in ns.NAMESPACES:
        raise ValueError(f"namespace must be one of {sorted(ns.NAMESPACES)}")
    async with async_session() as session:
        job = await session.get(DiscJob, job_id)
        if job is None or job.content_type != ContentType.TV or not job.tmdb_id:
            raise ValueError("not an identified TV job")
        if job.detected_season is None:
            raise ValueError("the job's season is not known yet")
        pref = await session.get(ShowOrderingPreference, job.tmdb_id)
        if pref is None:
            pref = ShowOrderingPreference(tmdb_id=job.tmdb_id, ordering="")
            session.add(pref)

        if namespace == ns.NAMESPACE_TVDB:
            config = await get_config()
            tvdb_id, tmdb_eps, tvdb_eps = await _rosters(
                job.tmdb_id, job.detected_season, config, pref.tvdb_id
            )
            if not (tvdb_id and tmdb_eps and tvdb_eps):
                await session.rollback()
                raise TvdbUnavailableError("TheTVDB did not return this season")
            pref.tvdb_id = tvdb_id
            pref.ordering = ns.NAMESPACE_TVDB
            pref.episode_group_id = None
            job.episode_namespace = ns.NAMESPACE_TVDB
            job.episode_crosswalk_json = _crosswalk_json(job.detected_season, tmdb_eps, tvdb_eps)
        else:
            if pref.ordering == ns.NAMESPACE_TVDB:
                pref.ordering = ""
            job.episode_namespace = ns.NAMESPACE_TMDB
            job.episode_crosswalk_json = None
        job.episode_namespace_note = None
        pref.updated_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(job)
        return job


async def dismiss_tvdb_suggestion(tmdb_id: int) -> None:
    """Remember that the user declined TheTVDB numbering for this show.

    A new preference row is created with ordering "" so it keeps following the
    global default ordering rather than pinning the show to aired.
    """
    async with async_session() as session:
        pref = await session.get(ShowOrderingPreference, tmdb_id)
        if pref is None:
            pref = ShowOrderingPreference(tmdb_id=tmdb_id, ordering="")
            session.add(pref)
        pref.tvdb_suggestion_dismissed = True
        pref.updated_at = datetime.now(UTC)
        await session.commit()
```

If `DiscJob` has no `updated_at` field, drop that line (check with `grep -n "updated_at" app/models/disc_job.py`).

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_episode_namespace_service.py -q -p no:cacheprovider`
Expected: PASS (9 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/episode_namespace_service.py backend/tests/unit/conftest.py backend/tests/unit/test_episode_namespace_service.py
git commit -m "feat(tvdb): decide/switch/dismiss a job's episode namespace"
```

---

### Task 9: Ordering resolution understands "tvdb" (per show only)

**Files:**
- Modify: `backend/app/core/episode_ordering.py` (constants, ~line 30 and the ALLOWED_ORDERINGS comment)
- Modify: `backend/app/services/episode_ordering_service.py`
- Modify: `backend/app/core/organizer.py:808` (projection guard)
- Modify: `backend/app/api/routes.py` (`get_show_ordering` ~line 4627)
- Test: append to `backend/tests/unit/test_episode_ordering_service.py`

- [ ] **Step 1: Write the failing tests** (append; reuse the file's existing session fixture/imports, adding any missing)

```python
from app.core import episode_ordering
from app.models.show_ordering import ShowOrderingPreference
from app.services.episode_ordering_service import resolve_show_ordering
from tests.unit.conftest import _unit_session_factory


async def test_tvdb_preference_resolves_to_tvdb_without_group():
    async with _unit_session_factory() as s:
        s.add(ShowOrderingPreference(tmdb_id=1618, ordering="tvdb"))
        await s.commit()
        assert await resolve_show_ordering(1618, s) == ("tvdb", None)


async def test_blank_preference_follows_global_default():
    async with _unit_session_factory() as s:
        s.add(ShowOrderingPreference(tmdb_id=1619, ordering=""))
        await s.commit()
        ordering, _ = await resolve_show_ordering(1619, s)
        assert ordering == "aired"


def test_tvdb_is_per_show_only():
    assert episode_ordering.ORDERING_TVDB == "tvdb"
    assert "tvdb" in episode_ordering.PER_SHOW_ORDERINGS
    assert "tvdb" not in episode_ordering.ALLOWED_ORDERINGS
```

(The organizer's "never project a tvdb job" behavior is tested end to end in Task 13.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_episode_ordering_service.py -q -p no:cacheprovider`
Expected: FAIL (`ORDERING_TVDB` missing; tvdb resolves to aired).

- [ ] **Step 3: Implement**

`episode_ordering.py`, after `ORDERING_TV = "tv"`:

```python
# TheTVDB official order (spec 2026-10-08). NOT a TMDB episode group and never
# projected: a TVDB job's codes are already in TVDB numbering. Per-show only.
ORDERING_TVDB = "tvdb"
```

Replace the last line of the ALLOWED_ORDERINGS comment block and add a second set after it:

```python
# Re-enable one by adding its constant back to this frozenset (and the Config UI).
# The per-show selector additionally offers TheTVDB (PER_SHOW_ORDERINGS); the
# global dropdown deliberately does not, because TVDB numbering is wrong for
# most shows. This intentionally ends the per-show/global lock-step above.
ALLOWED_ORDERINGS = frozenset({ORDERING_AIRED, ORDERING_DVD})
PER_SHOW_ORDERINGS = ALLOWED_ORDERINGS | {ORDERING_TVDB}
```

`episode_ordering_service.resolve_show_ordering`, directly after `ordering = pref.ordering if (pref and pref.ordering) else global_default`:

```python
    if ordering == episode_ordering.ORDERING_TVDB:
        return (episode_ordering.ORDERING_TVDB, None)
```

`organizer.py` line `if ordering != "aired" and tmdb_id:` becomes:

```python
    if ordering not in ("aired", "tvdb") and tmdb_id:
```

and extend the comment above it with: `A TheTVDB-numbered job ("tvdb") is never projected: its code is already the output number.`

`routes.py` `get_show_ordering`: change `"source": "show" if pref else "default",` to
`"source": "show" if (pref and pref.ordering) else "default",` and add `"tvdb_suggestion_dismissed": bool(pref and pref.tvdb_suggestion_dismissed),` to the returned dict.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_episode_ordering_service.py tests/unit/test_episode_ordering.py tests/unit/test_episode_ordering_api.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/episode_ordering.py backend/app/services/episode_ordering_service.py backend/app/core/organizer.py backend/app/api/routes.py backend/tests/unit/test_episode_ordering_service.py
git commit -m "feat(tvdb): per-show tvdb ordering resolves without projection"
```

---

### Task 10: Wire the matching coordinator

**Files:**
- Modify: `backend/app/services/matching_coordinator.py`: `download_subtitles` (~2570), `match_single_file` (~1014), `_episode_runtimes_for_job` (~1363), `try_discdb_assignment` (~766); add `forget_episode_runtimes`
- Modify: `backend/app/matcher/llm_episode_matcher.py:18,119`
- Test: `backend/tests/unit/test_tvdb_boundaries.py` (inbound half)

- [ ] **Step 1: Write the failing tests**

```python
"""TMDB-identity boundaries for TheTVDB-numbered jobs (spec 2026-10-08)."""

import json
from types import SimpleNamespace

import pytest

from app.models.disc_job import ContentType, DiscJob
from app.services.matching_coordinator import MatchingCoordinator
from tests.unit.conftest import _unit_session_factory

CROSSWALK = json.dumps({"S01E04": "S01E02", "S01E05": "S01E03"})


async def _tvdb_job() -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
            detected_season=1,
            episode_namespace="tvdb",
            episode_crosswalk_json=CROSSWALK,
        )
        s.add(job)
        await s.commit()
        return job.id


def _mapping(index, season, episode):
    return SimpleNamespace(
        index=index,
        season=season,
        episode=episode,
        episodes=[episode],
        title_type="Episode",
        source="discdb",
        episode_title="x",
    )


@pytest.mark.unit
class TestInboundHints:
    async def test_discdb_hint_translated_into_tvdb_numbering(self, monkeypatch):
        from app.models.disc_job import DiscTitle

        job_id = await _tvdb_job()
        coord = MatchingCoordinator.__new__(MatchingCoordinator)
        coord._discdb_mappings = {job_id: [_mapping(3, 1, 2)]}  # TMDB S01E02
        monkeypatch.setattr(
            "app.services.matching_coordinator.ws_manager.broadcast_title_update",
            lambda *a, **k: _noop(),
        )
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=3)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is True
            assert title.matched_episode == "S01E04"

    async def test_untranslatable_hint_is_dropped(self):
        from app.models.disc_job import DiscTitle

        job_id = await _tvdb_job()
        coord = MatchingCoordinator.__new__(MatchingCoordinator)
        coord._discdb_mappings = {job_id: [_mapping(0, 1, 1)]}  # TMDB S01E01 = 3 TVDB parts
        async with _unit_session_factory() as s:
            title = DiscTitle(job_id=job_id, title_index=0)
            s.add(title)
            await s.commit()
            assert await coord.try_discdb_assignment(job_id, title, s) is False
            assert title.matched_episode is None


async def _noop():
    return None


@pytest.mark.unit
async def test_match_single_file_binds_the_job_namespace(monkeypatch):
    from app.core.episode_namespace import current_namespace

    job_id = await _tvdb_job()
    coord = MatchingCoordinator.__new__(MatchingCoordinator)
    seen = {}

    async def fake_run(*a, **k):
        seen["ns"] = current_namespace()

    coord._run_match_single_file = fake_run
    await coord.match_single_file(job_id, 1, None)
    assert seen["ns"] == "tvdb"
```

(If `DiscTitle` lives in a different module, import it from where `matching_coordinator.py` imports it.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_boundaries.py -q -p no:cacheprovider`
Expected: FAIL (hint applied untranslated as `S01E02`; namespace is `tmdb`).

- [ ] **Step 3: Implement**

Add near the top-level imports of `matching_coordinator.py`:

```python
from app.core.episode_namespace import from_tmdb_code, namespace_context
```

Add a helper method to `MatchingCoordinator` (next to `clear_job_caches`):

```python
    async def _job_namespace(self, job_id: int) -> str:
        """The job's episode namespace. Read fresh each time (one PK lookup) so
        a switch from the review page is seen without cache invalidation."""
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            return (job.episode_namespace if job else None) or "tmdb"

    def forget_episode_runtimes(self, job_id: int) -> None:
        """Drop the cached runtimes after a namespace switch (24 vs 26 episodes)."""
        self._episode_runtimes.pop(job_id, None)
```

`match_single_file` body becomes:

```python
        namespace = await self._job_namespace(job_id)
        with job_log_context(job_id), namespace_context(namespace):
            await self._run_match_single_file(
                job_id, title_id, file_path, num_points, min_vote_count, advisory=advisory
            )
```

`_episode_runtimes_for_job`: replace

```python
            if show_id:
                runtimes = await asyncio.to_thread(
                    fetch_season_episode_runtimes, show_id, detected_season
                )
```

with

```python
            if show_id:
                from app.core.episode_namespace import (
                    NAMESPACE_TVDB,
                    current_namespace,
                    season_runtimes,
                )

                if current_namespace() == NAMESPACE_TVDB:
                    from app.services.config_service import get_config

                    cfg = await get_config()
                    runtimes = await asyncio.to_thread(
                        season_runtimes, show_id, detected_season, cfg.tmdb_api_key
                    )
                else:
                    runtimes = await asyncio.to_thread(
                        fetch_season_episode_runtimes, show_id, detected_season
                    )
```

`download_subtitles` (the single-season coroutine at ~2570): directly after the `try:` line, before the first `async with async_session()`, add

```python
            from app.services.episode_namespace_service import decide_job_namespace

            namespace = await decide_job_namespace(job_id)
```

and replace

```python
            result = await asyncio.to_thread(download_subtitles, show_name, season, tmdb_id=tmdb_id)
```

with

```python
            # asyncio.to_thread copies this context, so the matcher's cache paths
            # and precomputed reads see the namespace (episode_namespace.py).
            with namespace_context(namespace):
                result = await asyncio.to_thread(
                    download_subtitles,
                    show_name,
                    season,
                    tmdb_id=tmdb_id,
                    use_precomputed=namespace != "tvdb",
                )
```

`try_discdb_assignment`: replace

```python
        _eps = getattr(mapping, "episodes", None) or [mapping.episode]
        episode_code = format_episode_code(mapping.season, _eps)
```

with

```python
        _eps = getattr(mapping, "episodes", None) or [mapping.episode]
        episode_code = format_episode_code(mapping.season, _eps)
        # DiscDB and the disc network publish TMDB numbering. A TheTVDB job
        # takes the hint only when it maps 1:1; otherwise the matcher decides.
        job = await session.get(DiscJob, job_id)
        if job is not None and (job.episode_namespace or "tmdb") != "tmdb":
            translated = from_tmdb_code(
                job.episode_namespace, job.episode_crosswalk_json, episode_code
            )
            if translated is None:
                logger.info(
                    f"Job {job_id}: dropping {origin} hint {episode_code} for title "
                    f"{title.title_index}; it has no 1:1 TheTVDB equivalent"
                )
                return False
            episode_code = translated
```

Note: `origin` is assigned a few lines above `_eps` in the existing code, so it is in scope.

`llm_episode_matcher.py`: replace `from app.matcher.tmdb_client import fetch_season_episodes` with `from app.core.episode_namespace import season_episodes` and line 119 `episodes = fetch_season_episodes(tmdb_show_id, season, tmdb_api_key)` with `episodes = season_episodes(tmdb_show_id, season, tmdb_api_key)`. Then fix any test that monkeypatches `app.matcher.llm_episode_matcher.fetch_season_episodes`:
`grep -rn "llm_episode_matcher.fetch_season_episodes\|llm_episode_matcher, \"fetch_season_episodes\"" tests` and retarget them at `"app.matcher.llm_episode_matcher.season_episodes"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tvdb_boundaries.py tests/unit/test_matching_coordinator.py -q -p no:cacheprovider`
Then: `uv run pytest tests/unit -q -p no:cacheprovider -k "llm or discdb_assignment or runtimes"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/matching_coordinator.py backend/app/matcher/llm_episode_matcher.py backend/tests
git commit -m "feat(tvdb): bind the job namespace through download and matching"
```

---

### Task 11: Outbound boundaries: fingerprint contribution and TheDiscDB export

**Files:**
- Modify: `backend/app/services/disc_contribution_queue.py:76-84` (`_derive_assignment`)
- Modify: `backend/app/core/discdb_exporter.py:109`
- Test: append to `backend/tests/unit/test_tvdb_boundaries.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.core.discdb_exporter import _tvdb_safe_code
from app.services.disc_contribution_queue import _derive_assignment


def _job(ns="tvdb"):
    return DiscJob(
        drive_id="E:",
        volume_label="JL",
        content_type=ContentType.TV,
        tmdb_id=1618,
        episode_namespace=ns,
        episode_crosswalk_json=CROSSWALK if ns == "tvdb" else None,
    )


def _title(code):
    return SimpleNamespace(is_extra=False, matched_episode=code, state=None)


@pytest.mark.unit
class TestOutbound:
    def test_contribution_translates_to_tmdb(self):
        assert _derive_assignment(_job(), _title("S01E04")) == ("episode", 1, 2)

    def test_contribution_omits_split_parts(self):
        assert _derive_assignment(_job(), _title("S01E02"))[0] == "discarded"

    def test_tmdb_job_unchanged(self):
        assert _derive_assignment(_job("tmdb"), _title("S01E02")) == ("episode", 1, 2)

    def test_export_code_translated_or_none(self):
        assert _tvdb_safe_code(_job(), "S01E05") == "S01E03"
        assert _tvdb_safe_code(_job(), "S01E01") is None
        assert _tvdb_safe_code(_job("tmdb"), "S01E01") == "S01E01"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_boundaries.py -q -p no:cacheprovider`
Expected: FAIL (`_tvdb_safe_code` missing; contribution sends TVDB numbers).

- [ ] **Step 3: Implement**

`disc_contribution_queue._derive_assignment`: replace `parsed = parse_episode_code(title.matched_episode)` with

```python
    # The network keys TMDB numbering. A TheTVDB job's code is translated; one
    # with no 1:1 TMDB equivalent (a split part) is sent as "discarded" so the
    # shared network never receives a TVDB-numbered key (spec 2026-10-08).
    from app.core.episode_namespace import to_tmdb_code

    code = to_tmdb_code(job.episode_namespace, job.episode_crosswalk_json, title.matched_episode)
    parsed = parse_episode_code(code)
```

Add one sentence to the function docstring: `A TheTVDB-numbered job's code is translated to TMDB first; untranslatable titles become "discarded".`

`discdb_exporter.py`: add a module-level helper above `generate_export`:

```python
def _tvdb_safe_code(job: DiscJob, code: str | None) -> str | None:
    """The TMDB code TheDiscDB should receive, or None for a TheTVDB-numbered
    title with no 1:1 TMDB equivalent (it is then exported without an episode)."""
    from app.core.episode_namespace import to_tmdb_code

    return to_tmdb_code(job.episode_namespace, job.episode_crosswalk_json, code)
```

and change line 109 to `season, episode = discdb_episode_fields(_tvdb_safe_code(job, title.matched_episode))`. Also change the `title.matched_episode` check at line 59 (`_derive_title_type`) only if it decides "Episode" vs other types from the code; read it first. If it does, pass the translated code the same way so an untranslatable title is not typed "Episode" with no episode number.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tvdb_boundaries.py tests/pipeline/test_export_schema.py -q -p no:cacheprovider`
Then: `uv run pytest tests/unit -q -p no:cacheprovider -k "contribution or discdb_export"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/disc_contribution_queue.py backend/app/core/discdb_exporter.py backend/tests/unit/test_tvdb_boundaries.py
git commit -m "feat(tvdb): translate or omit TVDB codes at TMDB-keyed boundaries"
```

---

### Task 12: API: config field, roster fields, switch and dismiss endpoints, job detail

**Files:**
- Modify: `backend/app/api/routes.py`: `ConfigResponse` (~274), `ConfigUpdate` (~383), GET config builder (~1680), `SeasonRosterResponse` (~758), the roster endpoint (~800-945), manual subtitle preview/commit (~1003-1080), `_run_llm_match_for_title` (~4722), `JobDetailResponse` (~196) + `build_job_detail` (~1146), new endpoints after `set_show_ordering` (~4690)
- Modify: `backend/app/services/config_service.py:212` (`sensitive_fields`)
- Test: `backend/tests/unit/test_tvdb_namespace_api.py`; append config cases to `test_tvdb_schema_and_config.py`

- [ ] **Step 1: Write the failing config tests** (append to `test_tvdb_schema_and_config.py`)

```python
from app.api.routes import ConfigResponse, ConfigUpdate


def test_config_three_way_sync_for_tvdb_key():
    assert ConfigUpdate(tvdb_api_key="k").model_dump()["tvdb_api_key"] == "k"
    assert ConfigUpdate().model_dump()["tvdb_api_key"] is None
    assert "tvdb_api_key" in ConfigResponse.model_fields
    assert "tvdb_configured" in ConfigResponse.model_fields


def test_tvdb_key_is_a_protected_secret():
    import inspect

    from app.services import config_service

    assert '"tvdb_api_key"' in inspect.getsource(config_service.update_config)
```

- [ ] **Step 2: Write the failing API tests**

```python
"""Episode-namespace API (spec 2026-10-08)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_session
from app.main import app
from app.models.disc_job import ContentType, DiscJob, JobState
from tests.unit.conftest import _unit_session_factory


@pytest.fixture
async def client():
    async def override():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _job(**kw) -> int:
    async with _unit_session_factory() as s:
        job = DiscJob(
            drive_id="E:",
            volume_label="JL",
            content_type=ContentType.TV,
            tmdb_id=1618,
            detected_title="Justice League",
            detected_season=1,
            state=JobState.REVIEW_NEEDED,
            **kw,
        )
        s.add(job)
        await s.commit()
        return job.id


@pytest.mark.unit
class TestSwitchEndpoint:
    async def test_switch_to_tvdb_redownloads_and_rematches(self, client, monkeypatch):
        job_id = await _job(tvdb_divergence_json=json.dumps({"season": 1, "tmdb": 24, "tvdb": 26}))
        calls = []

        async def fake_switch(jid, namespace):
            calls.append(("switch", namespace))

        async def fake_restart(jid, show, season, tmdb_id=None):
            calls.append(("restart", show, season, tmdb_id))

        async def fake_rerun(jid, source_preference=None):
            calls.append(("rerun",))

        monkeypatch.setattr(
            "app.services.episode_namespace_service.switch_job_namespace", fake_switch
        )
        from app.services.job_manager import job_manager

        monkeypatch.setattr(job_manager._matching, "restart_subtitle_download", fake_restart)
        monkeypatch.setattr(job_manager._matching, "forget_episode_runtimes", lambda jid: None)
        monkeypatch.setattr(job_manager, "rerun_matching", fake_rerun)

        resp = await client.post(f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "tvdb"})

        assert resp.status_code == 200
        assert calls == [
            ("switch", "tvdb"),
            ("restart", "Justice League", 1, 1618),
            ("rerun",),
        ]

    async def test_tvdb_unavailable_is_503_and_nothing_runs(self, client, monkeypatch):
        from app.services import episode_namespace_service as svc

        job_id = await _job()

        async def unavailable(jid, namespace):
            raise svc.TvdbUnavailableError("down")

        monkeypatch.setattr(svc, "switch_job_namespace", unavailable)
        resp = await client.post(f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "tvdb"})
        assert resp.status_code == 503
        assert "TheTVDB" in resp.json()["detail"]

    async def test_unknown_namespace_is_422(self, client):
        job_id = await _job()
        resp = await client.post(f"/api/jobs/{job_id}/episode-namespace", json={"namespace": "imdb"})
        assert resp.status_code == 422


@pytest.mark.unit
async def test_dismiss_endpoint(client):
    resp = await client.post("/api/shows/1618/tvdb-suggestion/dismiss")
    assert resp.status_code == 200
    get = await client.get("/api/shows/1618/ordering")
    assert get.json()["tvdb_suggestion_dismissed"] is True
    assert get.json()["source"] == "default"


@pytest.mark.unit
async def test_roster_reports_source_and_suggestion(client, monkeypatch):
    job_id = await _job(tvdb_divergence_json=json.dumps({"season": 1, "tmdb": 24, "tvdb": 26}))
    monkeypatch.setattr(
        "app.core.episode_namespace.season_episodes",
        lambda show, season, key: [{"episode_number": 1, "name": "Secret Origins", "runtime": 72}],
    )
    monkeypatch.setattr(
        "app.core.episode_ordering.build_ordering_options",
        lambda *a, **k: {"available": False, "diverges": False, "current": "aired", "options": []},
    )
    resp = await client.get(f"/api/jobs/{job_id}/season-roster")
    body = resp.json()
    assert body["episode_source"] == "tmdb"
    assert body["tvdb_suggestion"] == {"season": 1, "tmdb": 24, "tvdb": 26}
```

Before writing this file, confirm the roster route path with `grep -n "SeasonRosterResponse)" app/api/routes.py` and the job-detail route path; adjust `"/api/jobs/{job_id}/season-roster"` to the real path.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_tvdb_namespace_api.py tests/unit/test_tvdb_schema_and_config.py -q -p no:cacheprovider`
Expected: FAIL (404s; missing fields).

- [ ] **Step 4: Implement config surfacing**

- `ConfigResponse`: after `tmdb_configured: bool` add
  ```python
      tvdb_api_key: str  # "***" if an override is stored
      # True when SOME TheTVDB key is usable (override or built-in), so the UI
      # can say "built in" without exposing whether one is baked in.
      tvdb_configured: bool
  ```
- `ConfigUpdate`: after `tmdb_api_key: str | None = None` add `tvdb_api_key: str | None = None`.
- GET builder: after the `tmdb_configured=` line add
  ```python
          tvdb_api_key="***" if config.tvdb_api_key else "",  # Redacted
          tvdb_configured=bool(tvdb_client.resolve_api_key(config)),
  ```
  with `from app.matcher import tvdb_client` imported at the top of `routes.py` beside the `tmdb_client` import.
- `config_service.update_config`: add `"tvdb_api_key",` to `sensitive_fields`, and after the `tmdb_api_key` cache-clear block add
  ```python
          if "tvdb_api_key" in kwargs:
              from app.matcher import tvdb_client

              tvdb_client._token_state.token = None  # re-login with the new key
  ```

- [ ] **Step 5: Implement the roster changes**

`SeasonRosterResponse`: add fields

```python
    # TheTVDB episode namespace (spec 2026-10-08). episode_source drives the
    # attribution link; tvdb_suggestion drives the "numbered differently" notice.
    episode_source: str = "tmdb"
    tvdb_suggestion: dict | None = None
    namespace_note: str | None = None
```

In the roster endpoint, replace the `fetch_season_episodes` call with a namespace-bound one, and bind the namespace around the coverage scan:

```python
    from app.core.episode_namespace import namespace_context, season_episodes

    namespace = job.episode_namespace or "tmdb"
    with namespace_context(namespace):
        episodes_raw = await asyncio.to_thread(
            season_episodes, str(job.tmdb_id), season_num, config.tmdb_api_key
        )
```

and wrap the existing `coverage = await asyncio.to_thread(reference_coverage, ...)` call in `with namespace_context(namespace):` (keep its try/except outside).

Compute the suggestion before the final `return` (after `current_ordering` is known):

```python
    from app.models.show_ordering import ShowOrderingPreference

    pref = await session.get(ShowOrderingPreference, job.tmdb_id)
    tvdb_suggestion = None
    if (
        namespace == "tmdb"
        and job.tvdb_divergence_json
        and not (pref and pref.tvdb_suggestion_dismissed)
    ):
        tvdb_suggestion = json.loads(job.tvdb_divergence_json)
```

and pass `episode_source=namespace, tvdb_suggestion=tvdb_suggestion, namespace_note=job.episode_namespace_note,` into the success `SeasonRosterResponse(...)`.

Offer TheTVDB in the selector: after `ordering_data = await asyncio.to_thread(...)` add

```python
    # TheTVDB is offered beside the TMDB orderings once Engram knows TVDB
    # numbers this season differently, or while the job already uses it.
    if namespace == "tvdb" or job.tvdb_divergence_json:
        ordering_data["options"].append(
            {
                "ordering": "tvdb",
                "label": "TheTVDB",
                "tmdb_type": 0,
                "diverges": True,
                "projection": {},
            }
        )
        ordering_data["available"] = True
        ordering_data["diverges"] = True
        if namespace == "tvdb":
            ordering_data["current"] = "tvdb"
```

- [ ] **Step 6: Bind the namespace in the manual-subtitle and LLM routes**

In `preview_manual_subtitles` and `commit_manual_subtitles`, wrap the `await asyncio.to_thread(classify_files, ...)` / `commit_files` call:

```python
    from app.core.episode_namespace import namespace_context

    with namespace_context(job.episode_namespace):
        results = await asyncio.to_thread(classify_files, ...)  # unchanged arguments
```

In `_run_llm_match_for_title`, wrap everything from `transcript = await asyncio.to_thread(...)` through the `match_episode_via_llm(...)` call in `with namespace_context(job.episode_namespace):`. This is what fixes the reporter's "uploaded the right subtitle and it still failed": the upload is now filed and matched in TVDB numbering.

- [ ] **Step 7: Implement the endpoints** (after `set_show_ordering`)

```python
class EpisodeNamespaceRequest(BaseModel):
    """Switch a job between TMDB and TheTVDB numbering (spec 2026-10-08)."""

    namespace: Literal["tmdb", "tvdb"]


@router.post("/jobs/{job_id}/episode-namespace")
async def set_job_episode_namespace(
    job_id: int,
    request: EpisodeNamespaceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Switch numbering, re-download references in it, and re-match. No re-rip.

    The show's preference moves with the job, so later discs start in the
    chosen numbering. Already-organized files are never renamed.
    """
    from app.services import episode_namespace_service as svc
    from app.services.job_manager import job_manager

    try:
        await svc.switch_job_namespace(job_id, request.namespace)
    except svc.TvdbUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="TheTVDB is unavailable right now; numbering was not changed.",
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Read through the request session (get_session), never a module-level
    # async_session: unit tests redirect only the former to the test database.
    job = await session.get(DiscJob, job_id)
    job_manager._matching.forget_episode_runtimes(job_id)
    await job_manager._matching.restart_subtitle_download(
        job_id, job.detected_title, job.detected_season, job.tmdb_id
    )
    await job_manager.rerun_matching(job_id)
    return {"job_id": job_id, "episode_namespace": request.namespace}


@router.post("/shows/{tmdb_id}/tvdb-suggestion/dismiss")
async def dismiss_tvdb_suggestion(tmdb_id: int) -> dict:
    """Stop suggesting TheTVDB numbering for this show."""
    from app.services import episode_namespace_service as svc

    await svc.dismiss_tvdb_suggestion(tmdb_id)
    return {"tmdb_id": tmdb_id, "tvdb_suggestion_dismissed": True}
```

Check that `Literal`, `AsyncSession`, `get_session` and `DiscJob` are already imported in `routes.py` (`grep -n "^from typing\|AsyncSession\|def get_session\|import DiscJob" app/api/routes.py`); add whatever is missing. A `Literal` validation failure returns 422 automatically, which satisfies `test_unknown_namespace_is_422`.

- [ ] **Step 8: Job detail**

`JobDetailResponse`: add `episode_namespace: str = "tmdb"`, `episode_namespace_note: str | None = None`, `tvdb_divergence_json: str | None = None`. In `build_job_detail` add the same three keys from `job`. The diagnostics bundle reuses `build_job_detail`, so it picks them up.

- [ ] **Step 9: Run tests**

Run: `uv run pytest tests/unit/test_tvdb_namespace_api.py tests/unit/test_tvdb_schema_and_config.py tests/unit/test_episode_ordering_api.py tests/unit/test_config.py -q -p no:cacheprovider`
Then the roster/LLM/manual-subtitle suites: `uv run pytest tests/unit -q -p no:cacheprovider -k "roster or manual_subtitle or llm_match"`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/routes.py backend/app/services/config_service.py backend/tests/unit
git commit -m "feat(tvdb): namespace switch/dismiss endpoints and roster fields"
```

---

### Task 13: End-to-end naming and contribution check (pipeline tier)

**Files:**
- Create: `backend/tests/pipeline/test_tvdb_namespace_flow.py`

- [ ] **Step 1: Write the test**

```python
"""Justice League S1 on TheTVDB numbering: names and network rows (spec 2026-10-08).

Pure logic, no ASR: given a TVDB job whose 26 tracks matched S01E01..S01E26,
the organizer files them under those numbers (no TMDB projection) and the
contribution payload carries only TMDB-translatable rows.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.core import episode_namespace as ns
from app.core.organizer import organize_tv_episode
from app.matcher.tvdb_client import _parse_episodes
from app.models.disc_job import ContentType, DiscJob
from app.services.disc_contribution_queue import _derive_assignment

FIX = Path(__file__).parent.parent / "fixtures" / "tvdb"


def _rosters():
    tmdb = json.loads((FIX / "justice_league_s1_tmdb.json").read_text("utf-8"))["episodes"]
    tvdb = _parse_episodes(
        json.loads((FIX / "justice_league_s1_tvdb.json").read_text("utf-8"))["response"], 1
    )
    return tmdb, tvdb


def test_tvdb_job_files_26_episodes_without_projection(tmp_path, monkeypatch):
    import app.core.episode_ordering as eo

    monkeypatch.setattr(
        eo, "project_episode", lambda *a: (_ for _ in ()).throw(AssertionError("projected"))
    )
    names = []
    for ep in range(1, 27):
        src = tmp_path / f"t{ep:02d}.mkv"
        src.write_bytes(b"x")
        result = organize_tv_episode(
            src,
            "Justice League",
            f"S01E{ep:02d}",
            library_path=tmp_path / "lib",
            conflict_resolution="skip",
            tmdb_id="1618",
            ordering="tvdb",
        )
        assert result.get("success", True), result
        names.append(Path(result["final_path"]).name)
    assert any("S01E01" in n for n in names) and any("S01E26" in n for n in names)
    assert len(set(names)) == 26


def test_contribution_rows_only_for_translatable_tracks():
    tmdb, tvdb = _rosters()
    crosswalk = json.dumps(ns.build_crosswalk(1, tmdb, tvdb))
    job = DiscJob(
        drive_id="E:",
        volume_label="JL",
        content_type=ContentType.TV,
        tmdb_id=1618,
        episode_namespace="tvdb",
        episode_crosswalk_json=crosswalk,
    )
    rows = [
        _derive_assignment(job, SimpleNamespace(is_extra=False, matched_episode=f"S01E{e:02d}", state=None))
        for e in range(1, 27)
    ]
    assert [r[0] for r in rows[:3]] == ["discarded"] * 3  # Secret Origins parts
    assert rows[3] == ("episode", 1, 2)  # In Blackest Night (1)
    sent = [r for r in rows if r[0] == "episode"]
    assert len({(s, e) for _, s, e in sent}) == len(sent)  # no duplicate TMDB keys
```

Before running, check what `organize_tv_episode` returns on success (`grep -n "return {" app/core/organizer.py | head`) and adjust the `result.get("success", True)` / `final_path` keys to the real ones. It reads `get_config_sync()`, which the pipeline tier may not patch; if it touches the real DB, add the `isolate_database`-style patch from `tests/unit/conftest.py` locally in this file via a fixture that sets `app.services.config_service.get_config_sync` to `lambda: AppConfig()`.

- [ ] **Step 2: Run**

Run: `uv run pytest tests/pipeline/test_tvdb_namespace_flow.py -q -p no:cacheprovider`
Expected: PASS. A failure here means an earlier task's boundary is wrong; fix that task's code, not this test.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/pipeline/test_tvdb_namespace_flow.py
git commit -m "test(tvdb): Justice League S1 naming and contribution rows"
```

---

### Task 14: Guard rail for `matched_episode` consumers

**Files:**
- Create: `backend/tests/unit/test_matched_episode_consumers.py`

- [ ] **Step 1: Write the test**

```python
"""Every module that parses matched_episode must say how it treats TheTVDB codes.

A TheTVDB-numbered job stores TVDB codes in matched_episode (spec 2026-10-08).
Code that publishes or compares against TMDB identity must translate through
app.core.episode_namespace; code that only displays or files the stored code
is safe as-is. A new consumer fails this test until it is classified here.
"""

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PARSERS = ("parse_episode_code", "episode_parts", "discdb_episode_fields")

CLASSIFIED = {
    # Translates through episode_namespace (to_tmdb_code / from_tmdb_code / roster).
    "api/routes.py": "namespace-aware",
    "core/discdb_exporter.py": "namespace-aware",
    "services/disc_contribution_queue.py": "namespace-aware",
    "services/matching_coordinator.py": "namespace-aware",
    # Uses the stored code as the output number; correct in either namespace.
    "core/organizer.py": "display-safe",
    "core/discord_notifier.py": "display-safe",
    "services/job_manager.py": "display-safe",
    # The parser itself.
    "core/episode_codes.py": "parser",
}


def _consumers() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        text = path.read_text("utf-8")
        if "matched_episode" in text and any(p in text for p in PARSERS):
            found.add(path.relative_to(APP).as_posix())
    return found


def test_every_matched_episode_parser_is_classified():
    unclassified = _consumers() - set(CLASSIFIED)
    assert not unclassified, (
        f"Classify these in CLASSIFIED (namespace-aware or display-safe): {sorted(unclassified)}"
    )


def test_classification_has_no_stale_entries():
    stale = set(CLASSIFIED) - _consumers()
    assert not stale, f"No longer parse matched_episode; remove: {sorted(stale)}"
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/unit/test_matched_episode_consumers.py -q -p no:cacheprovider`
Expected: PASS. If a module appears that is not listed (for example because a task above added a parser import), classify it after reading how it uses the code.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_matched_episode_consumers.py
git commit -m "test(tvdb): guard rail for matched_episode consumers"
```

---

### Task 15: Frontend: types, client, notice, attribution, selector, settings, history

**Files:**
- Modify: `frontend/src/components/ReviewQueue/types.ts`
- Modify: `frontend/src/api/client.ts` (after `setShowOrdering`, line 151)
- Create: `frontend/src/components/ReviewQueue/TvdbAttribution.tsx` + `.test.tsx`
- Create: `frontend/src/components/ReviewQueue/TvdbSuggestionNotice.tsx` + `.test.tsx`
- Modify: `frontend/src/components/ReviewQueue/OrderingSelector.tsx`
- Modify: `frontend/src/components/ReviewQueue.tsx` (~lines 194-215 and 1035-1056)
- Modify: `frontend/src/components/ConfigWizard.tsx` (~lines 189, 283, 307, 392-448, 674, after the OpenSubtitles block ~1200)
- Modify: `frontend/src/components/HistoryPage.tsx` (~line 118 and ~845)

Run `npm install` once in `frontend/` if `node_modules` is absent, then **restore `package-lock.json`** with `git checkout package-lock.json` (a worktree `npm install` rewrites it).

- [ ] **Step 1: Types and client**

`types.ts` `SeasonRoster`: add

```ts
    /** Which catalogue the roster came from; "tvdb" requires the attribution link. */
    episode_source?: 'tmdb' | 'tvdb';
    /** Set when TheTVDB numbers this season differently and the user has not dismissed it. */
    tvdb_suggestion?: { season: number; tmdb: number; tvdb: number } | null;
    /** Prose fallback note, e.g. "TheTVDB unavailable; matched with TMDB numbering". */
    namespace_note?: string | null;
```

and in `OrderingOption`, change the comment to `// "aired" | "dvd" | "tvdb"`.

`client.ts`, after `setShowOrdering`:

```ts
/** Switch a job between TMDB and TheTVDB numbering; the backend re-downloads and re-matches. */
export async function setEpisodeNamespace(jobId: number, namespace: 'tmdb' | 'tvdb'): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/episode-namespace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ namespace }),
  });
}

/** Stop suggesting TheTVDB numbering for a show. */
export async function dismissTvdbSuggestion(tmdbId: number): Promise<void> {
  return apiFetchVoid(`/api/shows/${tmdbId}/tvdb-suggestion/dismiss`, { method: 'POST' });
}
```

- [ ] **Step 2: Write the failing component tests**

`TvdbAttribution.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TvdbAttribution } from './TvdbAttribution';

describe('TvdbAttribution', () => {
    it('links to TheTVDB in a new tab', () => {
        render(<TvdbAttribution />);
        const link = screen.getByRole('link', { name: /TheTVDB/ });
        expect(link.getAttribute('href')).toBe('https://thetvdb.com');
        expect(link.getAttribute('target')).toBe('_blank');
        expect(link.getAttribute('rel')).toContain('noopener');
    });
});
```

`TvdbSuggestionNotice.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TvdbSuggestionNotice } from './TvdbSuggestionNotice';

const SUGGESTION = { season: 1, tmdb: 24, tvdb: 26 };

describe('TvdbSuggestionNotice', () => {
    it('states both counts', () => {
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={vi.fn()} onDismiss={vi.fn()} />);
        const text = screen.getByTestId('tvdb-suggestion').textContent ?? '';
        expect(text).toContain('Season 1');
        expect(text).toContain('26');
        expect(text).toContain('24');
    });

    it('switch and dismiss call their handlers', async () => {
        const onSwitch = vi.fn();
        const onDismiss = vi.fn();
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={onSwitch} onDismiss={onDismiss} />);
        await userEvent.click(screen.getByRole('button', { name: /Switch to TheTVDB numbering/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(onSwitch).toHaveBeenCalledOnce();
        expect(onDismiss).toHaveBeenCalledOnce();
    });

    it('shows the attribution link', () => {
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={vi.fn()} onDismiss={vi.fn()} />);
        expect(screen.getByRole('link', { name: /TheTVDB/ })).toBeTruthy();
    });

    it('surfaces a switch error', async () => {
        const onSwitch = vi.fn().mockRejectedValue(new Error('TheTVDB is unavailable right now'));
        render(<TvdbSuggestionNotice suggestion={SUGGESTION} onSwitch={onSwitch} onDismiss={vi.fn()} />);
        await userEvent.click(screen.getByRole('button', { name: /Switch to TheTVDB numbering/ }));
        expect(await screen.findByText(/unavailable/)).toBeTruthy();
    });
});
```

Run: `npm run test:unit -- TvdbAttribution TvdbSuggestionNotice`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement the components**

`TvdbAttribution.tsx`:

```tsx
import { sv } from '../../app/components/synapse';

/**
 * Required attribution for TheTVDB's free licensed API tier: shown wherever
 * TheTVDB metadata is displayed to the user (spec 2026-10-08).
 */
export function TvdbAttribution() {
    return (
        <span style={{ fontFamily: sv.sans, fontSize: 10, color: sv.inkDim }}>
            Episode data:{' '}
            <a
                href="https://thetvdb.com"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: sv.cyan }}
            >
                TheTVDB
            </a>
        </span>
    );
}
```

`TvdbSuggestionNotice.tsx`:

```tsx
import { useState } from 'react';
import { sv } from '../../app/components/synapse';
import { TvdbAttribution } from './TvdbAttribution';

interface Props {
    suggestion: { season: number; tmdb: number; tvdb: number };
    onSwitch: () => Promise<void> | void;
    onDismiss: () => Promise<void> | void;
}

/**
 * Offered when TheTVDB numbers this season differently from TMDB (Justice
 * League S1: 26 vs 24). Nothing changes until the user switches; switching
 * re-downloads references and re-matches without a re-rip.
 */
export function TvdbSuggestionNotice({ suggestion, onSwitch, onDismiss }: Props) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const run = async (fn: () => Promise<void> | void) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Request failed');
        } finally {
            setBusy(false);
        }
    };

    const button = (primary: boolean) => ({
        fontFamily: sv.mono,
        fontSize: 11,
        padding: '5px 10px',
        cursor: busy ? 'wait' : 'pointer',
        background: primary ? sv.cyan : 'transparent',
        color: primary ? sv.bg0 : sv.inkDim,
        border: `1px solid ${primary ? sv.cyan : sv.lineMid}`,
    });

    return (
        <div
            data-testid="tvdb-suggestion"
            style={{ padding: '12px 14px', marginBottom: 14, border: `1px solid ${sv.cyan}66`, background: sv.bg0 }}
        >
            <div style={{ fontFamily: sv.sans, fontSize: 12, color: sv.ink, marginBottom: 10 }}>
                TheTVDB numbers Season {suggestion.season} differently ({suggestion.tvdb} episodes vs
                TMDB's {suggestion.tmdb}). If this disc follows TheTVDB, switching re-matches it with
                TheTVDB numbering.
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button type="button" disabled={busy} style={button(true)} onClick={() => run(onSwitch)}>
                    Switch to TheTVDB numbering
                </button>
                <button type="button" disabled={busy} style={button(false)} onClick={() => run(onDismiss)}>
                    Dismiss
                </button>
                <TvdbAttribution />
            </div>
            {error && (
                <div style={{ marginTop: 8, fontFamily: sv.sans, fontSize: 11, color: sv.amber }}>{error}</div>
            )}
        </div>
    );
}
```

If `sv.amber` or `sv.ink` does not exist on the `sv` token object, use the token `MovieConflictNotice.tsx` uses for warnings and body text.

Run: `npm run test:unit -- TvdbAttribution TvdbSuggestionNotice`
Expected: PASS.

- [ ] **Step 4: Wire into ReviewQueue and OrderingSelector**

`OrderingSelector.tsx`: import `TvdbAttribution` and render `{options.some((o) => o.ordering === 'tvdb') && <TvdbAttribution />}` after the explanatory `<span>`; change the explanatory text to

```tsx
                {current === 'tvdb'
                    ? 'Matching and files use TheTVDB numbering for this show.'
                    : activeDiverges
                      ? 'Files use this ordering; matching & history stay canonical.'
                      : 'Aired order: matches the canonical numbering.'}
```

`ReviewQueue.tsx`:
- import `setEpisodeNamespace, dismissTvdbSuggestion` from `'../api/client'`, and `TvdbSuggestionNotice`, `TvdbAttribution` from `'./ReviewQueue/...'`.
- Replace the body of `handleOrderingChange` (keep its error handling) so TVDB moves go through the job endpoint:

```tsx
    const handleOrderingChange = async (ordering: string) => {
        if (!roster?.show_id) return;
        setOrderingError(null);
        try {
            if (ordering === 'tvdb') {
                await setEpisodeNamespace(job.id, 'tvdb');
            } else {
                if (roster.episode_source === 'tvdb') {
                    await setEpisodeNamespace(job.id, 'tmdb');
                }
                await setShowOrdering(roster.show_id, ordering);
            }
            reloadRoster();
        } catch (e) {
            console.error('Failed to set show ordering', e);
            setOrderingError(
                'Could not save the ordering preference: the selection was not applied. Please try again.',
            );
        }
    };
```

(Keep whatever the original called after a successful save, for example `reloadRoster()`; read lines 199-215 first and preserve them.)

- Directly above the `{/* Episode ordering (#200) ... */}` block, add:

```tsx
                {roster?.tvdb_suggestion && roster.show_id && (
                    <TvdbSuggestionNotice
                        suggestion={roster.tvdb_suggestion}
                        onSwitch={async () => {
                            await setEpisodeNamespace(job.id, 'tvdb');
                            reloadRoster();
                        }}
                        onDismiss={async () => {
                            await dismissTvdbSuggestion(roster.show_id as number);
                            reloadRoster();
                        }}
                    />
                )}
                {roster?.namespace_note && <SvNotice tone="warn">› {roster.namespace_note}</SvNotice>}
```

- In the Season roster block, next to the `<SvLabel>` whose text starts `Season roster`, add `{roster.episode_source === 'tvdb' && <TvdbAttribution />}` inside the same header `div` (wrap the label and attribution in a flex row with `gap: 10`).

- [ ] **Step 5: ConfigWizard field**

Following the OpenSubtitles API key pattern exactly:
- config state type (~189): `tvdbApiKey: string;`; initial (~283): `tvdbApiKey: '',`.
- `savedKeys` state type and initial (~307): add `tvdb: boolean` / `tvdb: false`.
- load (~392): `tvdb: data.tvdb_api_key === '***',` in `setSavedKeys`, and `tvdbApiKey: data.tvdb_api_key === '***' ? '' : (data.tvdb_api_key || ''),` in `setConfig`.
- save (~674): `...optional('tvdb_api_key', config.tvdbApiKey),`.
- UI, after the OpenSubtitles block:

```tsx
                        <h4 style={{marginTop: '1.5rem', marginBottom: '0.25rem', fontSize: '1rem', fontWeight: 600}}>
                            TheTVDB <span style={{fontWeight: 400, fontSize: '0.85rem', opacity: 0.7}}>(Optional)</span>
                        </h4>
                        <p className="step-description" style={{marginTop: 0}}>
                            Used for shows whose discs follow TheTVDB's episode numbering. A key is built in;
                            only enter one to override it.{' '}
                            <a href="https://thetvdb.com" target="_blank" rel="noopener noreferrer">Episode data: TheTVDB</a>
                        </p>
                        <div className="form-group">
                            <label htmlFor="tvdbApiKey">
                                API Key override
                                <SavedKeyBadge saved={savedKeys.tvdb} text="Key saved" />
                            </label>
                            <input
                                id="tvdbApiKey"
                                type="password"
                                value={config.tvdbApiKey}
                                onChange={(e) => handleInputChange('tvdbApiKey', e.target.value)}
                                placeholder={savedKeys.tvdb ? 'Enter new key to replace existing' : 'Leave blank to use the built-in key'}
                            />
                        </div>
```

- [ ] **Step 6: History panel line**

`HistoryPage.tsx` job-detail interface (~118): add `episode_namespace?: string;` and `episode_namespace_note?: string | null;`. In the panel (~845, after the Backup row):

```tsx
                    {detail.episode_namespace === 'tvdb' && (
                      <KvRow label="Episode numbering" value="TheTVDB" />
                    )}
                    {detail.episode_namespace_note && (
                      <KvRow label="Numbering" value={detail.episode_namespace_note} valueColor={sv.amber} alignTop />
                    )}
```

- [ ] **Step 7: Verify the frontend**

Run: `npm run test:unit`, then `npm run lint`, then `npm run build`
Expected: all pass. Then `git checkout package-lock.json` if `npm install` touched it.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(tvdb): review suggestion, attribution, selector option and settings field"
```

---

### Task 16: Ship the key in builds, docs, changelog, full verification

**Files:**
- Modify: `backend/engram.spec` (line 155 `runtime_hooks=`)
- Modify: `.github/workflows/release.yml` (every `run: uv run pyinstaller engram.spec` step's `env:`)
- Modify: `.gitignore`
- Modify: `CLAUDE.md` (Key Patterns), `CHANGELOG.md` (`[Unreleased]`), `README.md` (credits line)

- [ ] **Step 1: Bake the key at build time**

In `engram.spec`, above the `Analysis(...)` call:

```python
# TheTVDB project key (free licensed tier). Injected by release.yml from the
# TVDB_API_KEY Actions secret into a generated, gitignored runtime hook, so the
# key ships in the binary without ever being committed. Absent locally: the
# feature stays off unless the user enters a key or sets the env var.
runtime_hooks = ["hooks/rthook_headless.py"] if HEADLESS else []
_tvdb_key = os.environ.get("TVDB_API_KEY", "").strip()
if _tvdb_key:
    _hook = os.path.join("hooks", "rthook_tvdb_generated.py")
    with open(_hook, "w", encoding="utf-8") as fh:
        fh.write("import os\n\n")
        fh.write(f"os.environ.setdefault('TVDB_API_KEY', {_tvdb_key!r})\n")
    runtime_hooks.append(_hook)
```

and change `runtime_hooks=["hooks/rthook_headless.py"] if HEADLESS else [],` to `runtime_hooks=runtime_hooks,`.

`.gitignore`: add `backend/hooks/rthook_tvdb_generated.py`.

`release.yml`: for each step whose `run:` is `uv run pyinstaller engram.spec` (lines ~71, 217, 313, 401, and any later ones: `grep -n "pyinstaller engram.spec" .github/workflows/release.yml`), add to that step's `env:` (create the `env:` block if the step has none):

```yaml
          TVDB_API_KEY: ${{ secrets.TVDB_API_KEY }}
```

Tell the user to add the `TVDB_API_KEY` repository secret (Settings -> Secrets and variables -> Actions). Do not add it yourself.

- [ ] **Step 2: Docs**

`CLAUDE.md`, Key Patterns, add a bullet after the "Episode codes are multi-episode capable" bullet:

```markdown
- **A job's episode numbering is a value: `DiscJob.episode_namespace` (`tmdb` | `tvdb`).**
  TMDB aired order is the identity unless a job opts into TheTVDB official order (per show,
  via the review-page suggestion when the two catalogues disagree, e.g. Justice League S1:
  24 vs 26). In TVDB mode `matched_episode`, references, the review roster and filenames are
  TVDB-numbered. The namespace rides in a ContextVar (`app/core/episode_namespace.py`),
  bound in `MatchingCoordinator.download_subtitles`/`match_single_file` and the roster,
  manual-subtitle and LLM routes; `corpus_dir_name` (`@tvdb` suffix) and
  `load_precomputed_manifest` (hidden) read it, so TVDB references never mix with the
  TMDB corpus. TMDB-keyed boundaries (fingerprint contribution, TheDiscDB export,
  inbound DiscDB/network hints) translate through the job's persisted 1:1 crosswalk and
  omit anything not 1:1. `tests/unit/test_matched_episode_consumers.py` fails until a new
  `matched_episode` parser is classified. TheTVDB's free tier requires the in-app
  attribution link (`TvdbAttribution.tsx`); the key ships via the `TVDB_API_KEY` secret.
  Spec: `docs/superpowers/specs/2026-10-08-thetvdb-episode-namespace-design.md`.
```

`CHANGELOG.md`, under `## [Unreleased]` -> `### Added` (create the subsection if absent):

```markdown
- TheTVDB episode numbering for shows whose discs follow it. When TheTVDB numbers a season differently from TMDB (Justice League's three-part pilot is one TMDB episode), the review page offers to switch the show; Engram then re-downloads subtitles and re-matches in TheTVDB numbering, without re-ripping, and files episodes under TheTVDB's numbers.
```

`README.md`: in the credits/acknowledgements section (find it with `grep -n -i "credit\|acknowledg\|tmdb" README.md`), add: `Episode data for TheTVDB-numbered shows is provided by [TheTVDB](https://thetvdb.com).`

- [ ] **Step 3: Full verification** (from `backend/`)

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean (run `uv run ruff format .` if format fails, then re-check).

Run: `uv run pytest tests/unit -q -p no:cacheprovider -p no:logging` (about 7 minutes)
Then re-run without `-p no:logging` only the files that error on `caplog` (that flag breaks caplog; known).
Expected: all pass.

Run: `uv run pytest tests/pipeline -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 4: Manual smoke test** (one backend only; follow CLAUDE.md "Parallel sessions")

Start the backend on a free port with a scratch DB copy and `TVDB_API_KEY` set:

```powershell
$env:DATABASE_URL = "sqlite+aiosqlite:///./engram-tvdb.db"; $env:TVDB_API_KEY = "<key>"; $env:DEBUG = "true"
uv run uvicorn app.main:app --port 8100
```

Then `curl localhost:8100/api/config` shows `"tvdb_configured": true` and `"tvdb_api_key": ""`. Stop the server by port when done (CLAUDE.md snippet with `8100`). Real-disc verification (Justice League Blu-ray, or the reporting user on a beta build with a diagnostic bundle) is the user's call.

- [ ] **Step 5: Commit**

```bash
git add backend/engram.spec .gitignore .github/workflows/release.yml CLAUDE.md CHANGELOG.md README.md
git commit -m "feat(tvdb): ship the TheTVDB key in release builds; docs and changelog"
```

---

## Self-review (done while writing)

**Spec coverage**
| Spec requirement | Task |
|---|---|
| `tvdb_client` login/token/roster, never raises, cache | 3 |
| Show-to-TVDB link via TMDB external_ids | 4 |
| `roster_for` / crosswalk / `to_tmdb` / `from_tmdb` | 5, 6 (named `season_episodes`, `to_tmdb_code`, `from_tmdb_code`) |
| Data model columns + reconciler + Alembic | 1 |
| Deciding the namespace; divergence summary; fallback note | 8, 10 (deviation 1) |
| Accept/dismiss endpoints; no re-rip; preference moves with the job | 8, 12 |
| Subtitle download count, `@tvdb` folder, skip published pack | 7, 10 |
| Runtime checks and LLM matcher | 10 |
| Review roster in TVDB numbering; manual uploads in TVDB numbering | 12 |
| Organize uses stored code; projection skipped | 9, 13 |
| Fingerprint contribution / DiscDB export translate-or-omit | 11, 13 |
| Inbound DiscDB / network hints | 10 |
| Published cache + coverage never written | 7 (pack isolation), deviation 4 |
| Display-only sites; history line; diagnostics bundle | 12 (job detail), 15 (history) |
| Guard-rail test | 14 |
| Error handling table | 3 (client), 8 (decision/switch), 12 (503) |
| `tvdb_api_key` three-way sync + redaction + built-in key | 1, 12, 15, 16 |
| "tvdb" per-show only; lock-step comment updated | 9 |
| Suggestion notice, selector option, attribution link, fallback note | 12, 15 |
| Tests: unit, pipeline, Vitest; manual | 3-15, 16 |
| Playwright | deviation 6 |

**Type/name consistency checked:** `NAMESPACE_TMDB/TVDB`, `namespace_context`, `current_namespace`, `corpus_dir_suffix`, `season_episodes`, `season_runtimes`, `season_episode_count`, `to_tmdb_code`, `from_tmdb_code`, `build_crosswalk`, `detect_divergence`/`Divergence.to_json`, `decide_job_namespace`, `switch_job_namespace`, `dismiss_tvdb_suggestion`, `TvdbUnavailableError`, `forget_episode_runtimes`, `resolve_api_key`, `fetch_season_roster(tvdb_id, season, *, api_key)`, `fetch_tvdb_id(show_id, api_key)`, `ORDERING_TVDB`, `PER_SHOW_ORDERINGS`, response fields `episode_source` / `tvdb_suggestion` / `namespace_note`, client `setEpisodeNamespace` / `dismissTvdbSuggestion`.
