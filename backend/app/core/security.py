"""Security helpers: SSRF URL validation, executable allowlisting, log sanitizing.

These back the hardening of CodeQL-flagged sinks:

- ``is_allowed_image_url`` — guards the ``fetch_cover`` outbound HTTP request.
- ``executable_basename_allowed`` — constrains the tool-validation subprocess
  calls to executables that actually look like the expected tool.
- ``is_within_configured_roots`` — constrains media-endpoint file paths to the
  configured staging and library roots.
- ``sanitize_log_value`` — strips line breaks/control characters from
  disc/user-controlled values before they are written to logs.
- ``sanitize_playlist_field`` — strips line breaks/control characters from
  disc-derived text before it is embedded in an ``.m3u`` playlist.

The first three are boolean *predicates* — they return ``True``/``False`` so
the validation is recognised as a barrier guard by static analysis at the call
site (``if not guard(x): ...``). ``sanitize_log_value`` and
``sanitize_playlist_field`` instead return the cleaned value, the recognised
barrier shape for log/playlist injection.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

# C0 control characters and DEL, excluding tab (0x09), LF (0x0a) and CR (0x0d).
# CR/LF are stripped separately so the explicit str.replace stays the barrier
# that static analysis recognises for log injection.
_LOG_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

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
    ``http``/``https`` scheme, a present host, no bare IP-literal host, and
    a host on the image-source allowlist.
    """
    # .hostname is inside the try too: it parses the netloc lazily and can
    # raise ValueError for some malformed literals, not just urlparse() itself.
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:  # malformed URL or netloc (e.g. bad IPv6 literal)
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if not host:
        return False

    # Reject every IP-literal host — allowlisted CDNs are reached by DNS name,
    # so a bare IP is never legitimate and rejecting all of them avoids any
    # private/internal target slipping through.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal — a hostname, continue to the allowlist
    else:
        return False

    # Allowlist by dot-delimited host suffix (a bare endswith would let an
    # attacker-registered "evilmedia-amazon.com" through). Note: this is a
    # hostname allowlist, so it does not defend against DNS rebinding — an
    # acceptable boundary here, since the URL is a user-chosen CDN link.
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


def is_within_configured_roots(path: os.PathLike[str] | str | None, roots: Sequence[str]) -> bool:
    """Return True if ``path`` resolves inside at least one of ``roots``.

    Barrier guard for the media endpoints: the file path comes out of the
    database (``DiscTitle.output_filename`` / ``organized_to``), so a corrupted
    or hand-edited row must not become an arbitrary file read.

    Both sides are fully resolved first, so ``..`` segments and symlinks are
    followed before the comparison. A symlink sitting inside a root but
    pointing outside it is therefore rejected, not followed into.
    Comparison uses ``os.path.commonpath``,
    which compares path *components*: a bare string prefix test would let
    ``/media/staging-old`` pass a ``/media/staging`` root.

    Empty roots are skipped. Unconfigured ``AppConfig`` paths default to ``""``,
    which would otherwise resolve to the process working directory and silently
    authorise it.

    A falsy or otherwise unusable ``path`` (``None`` or ``""``, as both
    ``output_filename`` and ``organized_to`` are before a title is placed)
    returns False rather than raising, so the guard always fails closed.
    """
    if not path:
        return False
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError, TypeError):
        return False

    for root in roots:
        if not root:
            continue
        try:
            resolved_root = Path(root).resolve(strict=False)
            if os.path.commonpath([resolved, resolved_root]) == str(resolved_root):
                return True
        except (OSError, ValueError):
            # commonpath raises ValueError for paths on different Windows
            # drives, which simply means "not contained".
            continue
    return False


_DISALLOWED_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "metadata.google.internal", "169.254.169.254", "::1"}
)


def is_safe_remote_url(url: str) -> bool:
    """Return True if ``url`` is safe to use as a user-configured remote endpoint.

    Guards SSRF: rejects non-http(s) schemes, missing or IP-literal hosts
    (including all private/loopback/link-local ranges and cloud metadata IPs),
    and a small list of well-known internal hostnames.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if not host:
        return False

    if host in _DISALLOWED_HOSTNAMES:
        return False

    # Reject all IP-literal hosts — legitimate remote servers use DNS names.
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or addr.is_link_local or addr.is_private or addr.is_reserved:
            return False
        return False  # reject even public IPs — servers should have hostnames
    except ValueError:
        pass  # Not an IP literal; falls through.

    return True


def is_safe_dashboard_url(url: str) -> bool:
    """Return True if ``url`` is usable as this dashboard's own base URL.

    Deliberately NOT is_safe_remote_url. That guard rejects private, loopback
    and link-local hosts to prevent SSRF, but the dashboard base URL is a LAN
    address by design and the server never fetches it: the value is only
    embedded as a clickable link in a Discord notification. The threat model is
    therefore scheme injection (javascript:, data:, file:) and credential
    leakage, not request forgery.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not host:
        return False
    # Credentials in a URL that gets posted to a chat channel leak them.
    if parsed.username or parsed.password:
        return False
    return True


def sanitize_log_value(value: object) -> str:
    """Strip line breaks and control characters from a value before logging.

    Disc/user-controlled strings — most notably optical-disc volume labels read
    via ``GetVolumeInformationW``/``blkid`` — can contain CR/LF, which would let
    an attacker forge or split log entries (py/log-injection). Other C0/DEL
    control characters (e.g. terminal escapes) are stripped as defence in depth;
    tabs and ordinary (incl. non-ASCII) text are preserved.

    The trailing ``str.replace`` of CR and LF is deliberately the final, taint-
    clearing operation so static analysis recognises the result as sanitised.
    """
    # Remove control chars except tab (0x09), CR (0x0d) and LF (0x0a); CR/LF are
    # handled by the explicit replaces below so they remain the recognised barrier.
    text = _LOG_CONTROL_CHARS_RE.sub("", str(value))
    return text.replace("\r", "").replace("\n", "")


def sanitize_playlist_field(value: str) -> str:
    """Strip line breaks and control characters from text embedded in an .m3u.

    Disc volume labels and detected titles reach the playlist body, and an
    ``.m3u`` is line-oriented: a label carrying CR/LF can terminate the
    ``#EXTINF`` line and open a second, fully-formed entry pointing anywhere
    the crafted label likes, which the user's player would then parse as a real
    track. Returns the cleaned value, the recognised barrier shape for
    injection into a line-oriented format, mirroring ``sanitize_log_value``.
    """
    if not value:
        return ""
    collapsed = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return _LOG_CONTROL_CHARS_RE.sub("", collapsed).strip()
