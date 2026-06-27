"""Unit tests for network helpers (LAN bind address + IP detection)."""

import re
from unittest.mock import patch

from sqlalchemy import text
from sqlmodel import create_engine

import app.services.config_service as config_service
from app.core.network import compute_effective_host, get_lan_ip, resolve_startup_host

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class TestComputeEffectiveHost:
    """Bind-address precedence: explicit env > LAN toggle > localhost default."""

    def test_default_is_localhost(self):
        assert compute_effective_host(allow_lan=False, env_host=None) == "127.0.0.1"

    def test_lan_enabled_binds_all_interfaces(self):
        assert compute_effective_host(allow_lan=True, env_host=None) == "0.0.0.0"

    def test_env_host_takes_precedence_over_toggle(self):
        # An explicit HOST env var wins even when the LAN toggle is on.
        assert compute_effective_host(allow_lan=True, env_host="192.168.1.5") == "192.168.1.5"

    def test_env_host_used_even_when_lan_disabled(self):
        assert compute_effective_host(allow_lan=False, env_host="0.0.0.0") == "0.0.0.0"

    def test_blank_env_host_is_ignored(self):
        # Empty/whitespace env value should not count as "explicitly set".
        assert compute_effective_host(allow_lan=True, env_host="") == "0.0.0.0"
        assert compute_effective_host(allow_lan=False, env_host="   ") == "127.0.0.1"


class TestGetLanIp:
    """Primary outbound interface IP detection."""

    def test_returns_ipv4_or_none(self):
        result = get_lan_ip()
        assert result is None or _IPV4_RE.match(result)

    def test_returns_none_on_socket_error(self):
        with patch("app.core.network.socket.socket", side_effect=OSError("no network")):
            assert get_lan_ip() is None


class TestReadAllowLanSync:
    """The narrow LAN read tolerates schema drift a full AppConfig SELECT can't.

    At startup ``read_allow_lan_sync`` runs before init_db()'s reconcilers, so the
    live ``app_config`` table may lack columns the model declares. The read selects
    only ``allow_lan_access`` and must never raise.
    """

    def _engine_with(self, tmp_path, value):
        engine = create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
        with engine.begin() as conn:
            # Deliberately minimal: only id + allow_lan_access. A full
            # select(AppConfig) would raise 'no such column' here.
            conn.execute(
                text("CREATE TABLE app_config (id INTEGER PRIMARY KEY, allow_lan_access BOOLEAN)")
            )
            if value is not None:
                conn.execute(
                    text("INSERT INTO app_config (id, allow_lan_access) VALUES (1, :v)"),
                    {"v": value},
                )
        return engine

    def test_reads_true_from_minimal_table(self, tmp_path, monkeypatch):
        engine = self._engine_with(tmp_path, 1)
        monkeypatch.setattr(config_service, "_get_sync_engine", lambda: engine)
        assert config_service.read_allow_lan_sync() is True

    def test_reads_false_from_minimal_table(self, tmp_path, monkeypatch):
        engine = self._engine_with(tmp_path, 0)
        monkeypatch.setattr(config_service, "_get_sync_engine", lambda: engine)
        assert config_service.read_allow_lan_sync() is False

    def test_returns_false_when_table_missing(self, tmp_path, monkeypatch):
        engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        monkeypatch.setattr(config_service, "_get_sync_engine", lambda: engine)
        assert config_service.read_allow_lan_sync() is False

    def test_returns_none_when_table_has_no_rows(self, tmp_path, monkeypatch):
        """An empty table means 'not yet configured' — distinct from explicit False."""
        engine = self._engine_with(tmp_path, value=None)  # table exists, no rows inserted
        monkeypatch.setattr(config_service, "_get_sync_engine", lambda: engine)
        assert config_service.read_allow_lan_sync() is None


class TestResolveStartupHost:
    """Host resolution must never crash on a config read (the schema-drift bug)."""

    def test_falls_back_to_localhost_when_read_raises(self):
        with (
            patch("app.core.network._env_host", return_value=None),
            patch(
                "app.services.config_service.read_allow_lan_sync",
                side_effect=Exception("no such column: app_config.episode_ordering_preference"),
            ),
        ):
            assert resolve_startup_host() == "127.0.0.1"

    def test_binds_all_interfaces_when_lan_enabled(self):
        with (
            patch("app.core.network._env_host", return_value=None),
            patch("app.services.config_service.read_allow_lan_sync", return_value=True),
        ):
            assert resolve_startup_host() == "0.0.0.0"

    def test_binds_localhost_when_lan_disabled(self):
        with (
            patch("app.core.network._env_host", return_value=None),
            patch("app.services.config_service.read_allow_lan_sync", return_value=False),
        ):
            assert resolve_startup_host() == "127.0.0.1"
