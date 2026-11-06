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


# ---------------------------------------------------------------------------
# Config surfacing (Task 12)
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

from app.api.routes import ConfigResponse, ConfigUpdate  # noqa: E402


def test_config_three_way_sync_for_tvdb_key():
    assert ConfigUpdate(tvdb_api_key="k").model_dump()["tvdb_api_key"] == "k"
    assert ConfigUpdate().model_dump()["tvdb_api_key"] is None
    assert "tvdb_api_key" in ConfigResponse.model_fields
    assert "tvdb_configured" in ConfigResponse.model_fields


def test_tvdb_key_is_a_protected_secret():
    import inspect

    from app.services import config_service

    assert '"tvdb_api_key"' in inspect.getsource(config_service.update_config)


@pytest.mark.unit
class TestTvdbConfigApi:
    async def test_get_config_redacts_stored_key(self, monkeypatch):
        from app.api import routes

        monkeypatch.delenv("TVDB_API_KEY", raising=False)
        cfg = AppConfig(tvdb_api_key="secret-tvdb")
        monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
        resp = await routes.get_config()
        assert resp.tvdb_api_key == "***"
        assert resp.tvdb_configured is True

    async def test_get_config_blank_without_key(self, monkeypatch):
        from app.api import routes

        monkeypatch.delenv("TVDB_API_KEY", raising=False)
        cfg = AppConfig(tvdb_api_key="")
        monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
        resp = await routes.get_config()
        assert resp.tvdb_api_key == ""
        assert resp.tvdb_configured is False

    async def test_built_in_env_key_counts_as_configured(self, monkeypatch):
        from app.api import routes

        monkeypatch.setenv("TVDB_API_KEY", "built-in")
        cfg = AppConfig(tvdb_api_key="")
        monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=cfg))
        resp = await routes.get_config()
        # The override field stays blank: the built-in key is never exposed.
        assert resp.tvdb_api_key == ""
        assert resp.tvdb_configured is True

    async def test_blank_update_keeps_stored_key(self):
        from app.services.config_service import update_config
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            session.add(AppConfig(staging_path="/tmp", tvdb_api_key="stored-key"))
            await session.commit()

        updated = await update_config(tvdb_api_key="")
        assert updated.tvdb_api_key == "stored-key"

    async def test_key_change_resets_cached_token(self):
        from app.matcher import tvdb_client
        from app.services.config_service import update_config
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            session.add(AppConfig(staging_path="/tmp", tvdb_api_key="old"))
            await session.commit()

        tvdb_client._token_state.token = "tok"
        tvdb_client._token_state.key = "old"
        try:
            await update_config(tvdb_api_key="new")
            assert tvdb_client._token_state.token is None
            assert tvdb_client._token_state.key is None
        finally:
            tvdb_client._token_state.token = None
            tvdb_client._token_state.key = None


@pytest.mark.unit
class TestTvdbTokenResetOnBlank:
    async def test_blank_key_update_keeps_cached_token(self):
        from app.matcher import tvdb_client
        from app.services.config_service import update_config
        from tests.unit.conftest import _unit_session_factory

        async with _unit_session_factory() as session:
            session.add(AppConfig(staging_path="/tmp", tvdb_api_key="stored"))
            await session.commit()

        tvdb_client._token_state.token = "tok"
        tvdb_client._token_state.key = "stored"
        try:
            await update_config(tvdb_api_key="  ")
            assert tvdb_client._token_state.token == "tok"
            assert tvdb_client._token_state.key == "stored"
        finally:
            tvdb_client._token_state.token = None
            tvdb_client._token_state.key = None


# ---------------------------------------------------------------------------
# Frozen-build reconciler (frozen builds skip Alembic)
# ---------------------------------------------------------------------------

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@pytest.fixture
async def legacy_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


async def test_add_missing_columns_backfills_tvdb_columns(legacy_engine):
    import app.database as db_mod

    original_engine = db_mod.engine
    db_mod.engine = legacy_engine
    try:
        async with legacy_engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE disc_jobs (id INTEGER PRIMARY KEY, drive_id TEXT)")
            )
            await conn.execute(text("INSERT INTO disc_jobs (drive_id) VALUES ('E:')"))
            await conn.execute(
                text("CREATE TABLE show_ordering_preferences (tmdb_id INTEGER PRIMARY KEY)")
            )
            await conn.execute(
                text("INSERT INTO show_ordering_preferences (tmdb_id) VALUES (1618)")
            )
            await conn.execute(text("CREATE TABLE app_config (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO app_config (id) VALUES (1)"))

        await db_mod._add_missing_columns()

        async with legacy_engine.connect() as conn:
            job = (
                await conn.execute(
                    text(
                        "SELECT episode_namespace, tvdb_divergence_json, "
                        "episode_namespace_note, episode_crosswalk_json FROM disc_jobs"
                    )
                )
            ).fetchone()
            pref = (
                await conn.execute(
                    text("SELECT tvdb_id, tvdb_suggestion_dismissed FROM show_ordering_preferences")
                )
            ).fetchone()
            cfg = (await conn.execute(text("SELECT tvdb_api_key FROM app_config"))).fetchone()
        assert job[0] == "tmdb"
        assert job[1] is None and job[3] is None
        assert job[2] in (None, "")
        assert pref[0] is None
        assert pref[1] == 0
        assert cfg[0] == ""
    finally:
        db_mod.engine = original_engine


# ---------------------------------------------------------------------------
# Cache scripts ignore TheTVDB-numbered reference dirs
# ---------------------------------------------------------------------------


def test_pack_discover_shows_ignores_tvdb_dirs(psc, tmp_path):
    for name in ("123", "123@tvdb"):
        d = tmp_path / name
        d.mkdir()
        (d / "Show - S01E01.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    shows = psc._discover_shows(tmp_path)
    assert "123" in shows
    assert "123@tvdb" not in shows
