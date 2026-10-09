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
