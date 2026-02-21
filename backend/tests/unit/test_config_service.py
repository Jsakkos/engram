"""Unit tests for the configuration service.

Tests get/update config and path creation logic.
"""

from app.models.app_config import AppConfig
from app.services.config_service import ensure_paths_exist, get_config, update_config
from tests.unit.conftest import _unit_session_factory


class TestGetConfig:
    """Tests for get_config()."""

    async def test_get_config_creates_default(self):
        """Empty DB should return a default config with platform paths."""
        config = await get_config()
        assert config is not None
        assert config.id is not None
        # Default staging path should exist (platform-dependent)
        assert config.staging_path is not None
        assert len(config.staging_path) > 0

    async def test_get_config_returns_existing(self):
        """If a config already exists, get_config should return it."""
        async with _unit_session_factory() as session:
            existing = AppConfig(
                staging_path="/custom/staging",
                library_movies_path="/custom/movies",
                library_tv_path="/custom/tv",
            )
            session.add(existing)
            await session.commit()

        config = await get_config()
        assert config.staging_path == "/custom/staging"


class TestUpdateConfig:
    """Tests for update_config()."""

    async def test_update_config_writes_fields(self):
        """Update staging_path and verify it persists."""
        # Seed initial config
        async with _unit_session_factory() as session:
            session.add(AppConfig(staging_path="/old/path"))
            await session.commit()

        updated = await update_config(staging_path="/updated/path")
        assert updated.staging_path == "/updated/path"

        # Verify via fresh read
        config = await get_config()
        assert config.staging_path == "/updated/path"

    async def test_update_skips_empty_sensitive_fields(self):
        """Empty string for tmdb_api_key should NOT overwrite existing value."""
        async with _unit_session_factory() as session:
            session.add(
                AppConfig(
                    staging_path="/tmp",
                    tmdb_api_key="eyJoriginal_token",
                )
            )
            await session.commit()

        updated = await update_config(tmdb_api_key="")
        assert updated.tmdb_api_key == "eyJoriginal_token"

    async def test_update_skips_none_values(self):
        """None values should be ignored."""
        async with _unit_session_factory() as session:
            session.add(AppConfig(staging_path="/original"))
            await session.commit()

        updated = await update_config(staging_path=None)
        assert updated.staging_path == "/original"

    async def test_update_creates_config_if_missing(self):
        """If no config exists, update_config should create one."""
        updated = await update_config(staging_path="/brand-new")
        assert updated.staging_path == "/brand-new"


class TestEnsurePathsExist:
    """Tests for ensure_paths_exist()."""

    async def test_ensure_paths_exist_creates_dirs(self, tmp_path):
        """Should create directories from config paths."""
        config = AppConfig(
            staging_path=str(tmp_path / "staging"),
            library_movies_path=str(tmp_path / "movies"),
            library_tv_path=str(tmp_path / "tv"),
            subtitles_cache_path=str(tmp_path / "cache"),
        )

        await ensure_paths_exist(config)

        assert (tmp_path / "staging").exists()
        assert (tmp_path / "movies").exists()
        assert (tmp_path / "tv").exists()
        assert (tmp_path / "cache").exists()

    async def test_ensure_paths_exist_no_error_on_existing(self, tmp_path):
        """Should not fail if directories already exist."""
        (tmp_path / "staging").mkdir()
        config = AppConfig(
            staging_path=str(tmp_path / "staging"),
            library_movies_path=str(tmp_path / "movies"),
            library_tv_path=str(tmp_path / "tv"),
        )
        await ensure_paths_exist(config)
        assert (tmp_path / "staging").exists()
