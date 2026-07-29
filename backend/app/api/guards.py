"""Network-origin FastAPI dependencies shared by the API routers.

These live here rather than in ``routes.py`` so a router that needs a gate does
not have to import the whole route module to get one. ``validation.py`` importing
``routes.py`` for ``require_localhost_or_lan`` created an import cycle
(``routes.py`` imports ``validation.py`` back, inside function bodies), which
CodeQL flags and which makes the dependency direction between the two routers
ambiguous. Both now depend on this leaf module instead.

``routes.py`` re-exports these names, so existing imports and the
``app.dependency_overrides[require_localhost]`` pattern used across the test
suite keep working unchanged.
"""

import ipaddress

from fastapi import HTTPException, Request


def is_loopback(host: str | None) -> bool:
    """True if ``host`` names the local machine.

    Covers every loopback form a peer address can take: IPv4 (`127.0.0.0/8`),
    IPv6 (`::1`), and the IPv4-mapped IPv6 loopback (`::ffff:127.0.0.1`) that
    arrives on dual-stack binds (`HOST=::`). `localhost` is kept as an explicit
    fallback: Starlette normally reports a numeric peer address, but a literal
    hostname is accepted rather than silently rejected.
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    if addr.is_loopback:
        return True
    # Python < 3.13's is_loopback does not unwrap IPv4-mapped addresses, so
    # check the mapped IPv4 explicitly for version-independent correctness.
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def require_localhost(request: Request) -> None:
    """FastAPI dependency: 403 unless the request came from the host machine.

    Used by endpoints that surface or mutate privacy-sensitive local data
    (e.g. ripping history, DiscDB/fingerprint contributions) so they stay
    reachable from the dashboard but not from LAN peers when
    `allow_lan_access=True` opens the bind to all interfaces. Tests should
    override the dependency via
    `app.dependency_overrides[require_localhost] = lambda: None` rather than
    spoofing peer addresses.
    """
    if not is_loopback(request.client.host if request.client else None):
        raise HTTPException(
            status_code=403, detail="This endpoint is only reachable from the host machine"
        )


async def require_localhost_or_lan(request: Request) -> None:
    """FastAPI dependency: allow loopback always, LAN peers only when opted in.

    Like `require_localhost`, but a non-loopback (LAN) client is also permitted
    when the user has explicitly enabled `allow_lan_access`. Used by the manual
    *import* endpoints so headless/Docker deployments, where the dashboard is
    reached from another machine, can browse and import, while the more
    sensitive fingerprint/contribution endpoints stay strictly host-only via
    `require_localhost`. Also used by the AI connection test, which spends the
    user's money on each call.

    The filesystem-browse surface this exposes is why the gate is the explicit
    opt-in rather than always-on: enabling `allow_lan_access` is the user's
    statement that they trust their network.
    """
    if is_loopback(request.client.host if request.client else None):
        return
    try:
        from app.services.config_service import get_config

        config = await get_config()
        allow_lan = bool(config.allow_lan_access)
    except Exception:  # noqa: BLE001 — a config read failure must fail closed
        allow_lan = False
    if not allow_lan:
        raise HTTPException(
            status_code=403,
            detail=(
                "This endpoint is only reachable from the host machine. To use it "
                "from another device (e.g. a Docker deployment), enable LAN access "
                "in Settings."
            ),
        )
