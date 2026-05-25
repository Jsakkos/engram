"""Network helpers for LAN access.

Determines the address uvicorn should bind to, and detects the host's primary
LAN IP so the dashboard can show a reachable URL for other devices.
"""

import logging
import os
import socket

logger = logging.getLogger(__name__)

LOCALHOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 — intentional: opt-in LAN exposure


def _env_host() -> str | None:
    """Return an explicitly set HOST env var, if any (case-tolerant)."""
    return os.environ.get("HOST") or os.environ.get("host")


def compute_effective_host(
    allow_lan: bool, env_host: str | None, default_host: str = LOCALHOST
) -> str:
    """Pick the bind address.

    Precedence: an explicitly set HOST env var wins (docker/.env, power users),
    then the LAN toggle (bind all interfaces), otherwise localhost only.
    A blank/whitespace env value does not count as "explicitly set".
    """
    if env_host and env_host.strip():
        return env_host
    if allow_lan:
        return ALL_INTERFACES
    return default_host


def resolve_startup_host(default_host: str = LOCALHOST) -> str:
    """Resolve the bind address at startup, reading the persisted LAN toggle.

    Called before the event loop exists, so it uses the synchronous config
    reader. Any failure (e.g. DB tables not yet created on first run) falls
    back to the safe default — the LAN toggle defaults off anyway.
    """
    allow_lan = False
    try:
        from app.services.config_service import get_config_sync

        allow_lan = bool(get_config_sync().allow_lan_access)
    except Exception as e:  # noqa: BLE001 — startup must never crash on a config read
        logger.warning("Could not read LAN access setting, binding localhost: %s", e, exc_info=True)
        allow_lan = False
    return compute_effective_host(
        allow_lan=allow_lan, env_host=_env_host(), default_host=default_host
    )


def get_lan_ip() -> str | None:
    """Return the host's primary outbound interface IP, or None if undetectable.

    Uses a UDP socket "connect" to a public address. No packets are sent — the
    OS just resolves which local interface would route there, revealing its IP.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None
