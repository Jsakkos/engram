"""Security helpers: SSRF URL validation and path-traversal containment.

These functions back the hardening of CodeQL-flagged sinks:
- ``validate_image_url`` — guards the ``fetch_cover`` outbound HTTP request.
- ``safe_static_path`` — confines the SPA catch-all route to its asset root.
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


def validate_image_url(url: str) -> str:
    """Validate a user-supplied cover-image URL against SSRF.

    Returns the URL unchanged when it is safe to fetch; raises ``ValueError``
    otherwise. The checks, in order: a parseable URL, an ``http``/``https``
    scheme, a present host, no IP-literal host in a private/reserved range,
    and a host that matches the image-source allowlist.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:  # malformed URL (e.g. bad IPv6 literal)
        raise ValueError(f"Malformed URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got '{parsed.scheme}'")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL has no host")

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
        raise ValueError(f"URL host is a non-public address: {host}")

    # Allowlist by dot-delimited host suffix (a bare endswith would let an
    # attacker-registered "evilmedia-amazon.com" through).
    if not any(host == s or host.endswith("." + s) for s in _ALLOWED_IMAGE_HOST_SUFFIXES):
        raise ValueError(f"Image host not in allowlist: {host}")

    return url


def safe_static_path(static_root: str, requested: str) -> str | None:
    """Resolve ``requested`` beneath ``static_root`` for static-file serving.

    Returns the absolute, symlink-resolved path when it stays within the root
    directory; returns ``None`` for traversal attempts, absolute paths, or
    prefix-collision siblings (``static`` vs ``static_evil``).
    """
    root = os.path.realpath(static_root)
    candidate = os.path.realpath(os.path.join(root, requested))
    if candidate == root or candidate.startswith(root + os.sep):
        return candidate
    return None


def executable_basename_allowed(path: str, keywords: Sequence[str]) -> bool:
    """Return True if the executable's filename contains one of ``keywords``.

    Used to constrain validation subprocess calls to executables that look
    like the expected tool, so the endpoint cannot be coerced into running an
    arbitrary binary (e.g. a shell) supplied as a config path.
    """
    name = os.path.basename(path).lower()
    return any(keyword.lower() in name for keyword in keywords)
