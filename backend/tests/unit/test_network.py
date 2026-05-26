"""Unit tests for network helpers (LAN bind address + IP detection)."""

import re
from unittest.mock import patch

from app.core.network import compute_effective_host, get_lan_ip

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
