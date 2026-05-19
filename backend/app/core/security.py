"""Security helpers: SSRF URL validation and executable allowlisting.

These back the hardening of CodeQL-flagged sinks. Each helper is a boolean
*predicate* — it returns ``True``/``False`` rather than raising or returning a
sanitized value, so the validation is recognised as a barrier guard by static
analysis at the call site (``if not guard(x): ...``):

- ``is_allowed_image_url`` — guards the ``fetch_cover`` outbound HTTP request.
- ``executable_basename_allowed`` — constrains the tool-validation subprocess
  calls to executables that actually look like the expected tool.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Sequence
from urllib.parse import urlparse

# Host suffixes permitted as cover-image sources for the DiscDB contribution
# flow. Intentionally narrow: ``fetch_cover`` fetches a URL chosen by the user
# from UPC-lookup results, so an allowlist is the primary SSRF control.
# Extend deliberately — every entry widens the outbound-request surface.
_ALLOWED_IMAGE_HOST_SUFFIXES: tuple[str, ...] = (
    "media-amazon.com",
    "ssl-images-amazon.com",
    "images-amazon.com",
    "tmdb.org",
    "themoviedb.org",
    "thediscdb.com",
)


def is_allowed_image_url(url: str) -> bool:
    """Return True if ``url`` is safe to fetch as a cover image.

    Guards the ``fetch_cover`` SSRF sink. Requires a parseable URL, an
    ``http``/``https`` scheme, a present host, no IP-literal host in a
    private/reserved range, and a host on the image-source allowlist.
    """
    try:
        parsed = urlparse(url)
    except ValueError:  # malformed URL (e.g. bad IPv6 literal)
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    # Reject IP-literal hosts that point at private/reserved address space.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False

    # Allowlist by dot-delimited host suffix (a bare endswith would let an
    # attacker-registered "evilmedia-amazon.com" through).
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES
    )


def executable_basename_allowed(path: str, allowed_basenames: Sequence[str]) -> bool:
    """Return True if the executable's filename exactly matches an allowed name.

    Used to constrain validation subprocess calls to known tool executables,
    so the endpoint cannot be coerced into running an arbitrary binary supplied
    as a config path. Exact basename match (case-insensitive) — a substring
    check would let ``makemkv-exploit.sh`` through. Backslashes are normalised
    to ``/`` first so a Windows-style path is parsed correctly on any platform.
    """
    name = os.path.basename(path.replace("\\", "/")).lower()
    return name in {allowed.lower() for allowed in allowed_basenames}
