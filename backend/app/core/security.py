"""Security helpers: SSRF URL validation, executable allowlisting, log sanitizing.

These back the hardening of CodeQL-flagged sinks:

- ``is_allowed_image_url`` — guards the ``fetch_cover`` outbound HTTP request.
- ``executable_basename_allowed`` — constrains the tool-validation subprocess
  calls to executables that actually look like the expected tool.
- ``is_within_configured_roots`` — constrains media-endpoint file paths to the
  configured staging and library roots.
- ``is_safe_local_ai_url`` — a deliberately permissive guard for the
  user-configured local AI server base URL, which must accept loopback.
- ``sanitize_log_value`` — strips line breaks/control characters from
  disc/user-controlled values before they are written to logs.
- ``sanitize_playlist_field`` — strips line breaks/control characters from
  disc-derived text before it is embedded in an ``.m3u`` playlist.

The first four are boolean *predicates* — they return ``True``/``False`` so
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
            # CodeQL py/path-injection flags this Path() because `root` derives
            # from config and job rows, which trace back to HTTP input. It is a
            # false positive on the barrier itself: nothing in this function
            # opens, reads, writes or deletes. `resolved_root` reaches only
            # commonpath() and a string compare, and resolving is precisely what
            # makes that compare sound, since it collapses `..` and symlinks
            # before the components are matched. Removing the resolve() to quiet
            # the scanner would weaken the guard rather than fix anything.
            #
            # An inline `# codeql[py/path-injection]` marker does NOT suppress it
            # in this repo's code-scanning setup (tried; the alert simply moved to
            # the new line number), so the alert is handled by dismissal in the
            # Security tab. Excluding the module via paths-ignore was rejected:
            # that would blind the scanner to the other guards in this file.
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


# Rejected regardless of the deliberately permissive host policy below. No
# inference server runs on a cloud metadata endpoint, and the model-list caller
# reflects part of the response back to the requester, so leaving these
# reachable would turn a config field into a metadata read.
_LOCAL_AI_DENIED_HOSTS: frozenset[str] = frozenset(
    {"169.254.169.254", "metadata.google.internal", "metadata.goog"}
)


def _ip_and_embedded_ipv4(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """``addr`` plus any IPv4 address reachable through an IPv6 wrapper.

    An IPv6 literal can name an IPv4 destination in several ways, and the
    ``is_*`` properties do not all see through the wrapper. Modern CPython does
    delegate for the IPv4-*mapped* form, so ``::ffff:169.254.169.254`` already
    reports ``is_link_local``; the deprecated IPv4-*compatible* form
    ``::169.254.169.254`` does not, and 6to4 and Teredo wrappers do not either.
    Checking the unwrapped address as well as the literal closes that gap
    without relying on which forms a given Python version happens to handle.

    No legitimate inference server is addressed this way, so unwrapping costs
    nothing real.
    """
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [addr]
    if isinstance(addr, ipaddress.IPv6Address):
        for attr in ("ipv4_mapped", "sixtofour"):
            embedded = getattr(addr, attr, None)
            if embedded is not None:
                out.append(embedded)
        teredo = getattr(addr, "teredo", None)
        if teredo:
            out.extend(teredo)
        # "::a.b.c.d" — the whole top 96 bits are zero. Excludes :: and ::1,
        # whose embedded values (0.0.0.0 / 0.0.0.1) are meaningless, and which
        # must keep working: ::1 is a normal way to reach a local server.
        packed = int(addr)
        if packed >> 32 == 0 and packed > 1:
            out.append(ipaddress.IPv4Address(packed & 0xFFFFFFFF))
    return out


def is_safe_local_ai_url(url: str) -> bool:
    """Return True if ``url`` is usable as a local AI server's base URL.

    Deliberately NOT is_safe_remote_url. That guard rejects loopback, private
    and link-local hosts to prevent SSRF, and those are precisely the addresses
    an Ollama or LM Studio server listens on, so this is an intentional
    exemption rather than an oversight.

    Scope of that exemption, stated plainly because the function name understates
    it: apart from the link-local and metadata hosts denied below, this imposes
    NO restriction on the host. A public address passes. That is intended, since
    a user may legitimately run their inference server on another machine, but it
    means this guard is not what stops a hostile endpoint being configured.

    The server issues real requests to this URL, so it is a request-forgery
    primitive. It is accepted because the config surface is already fully trusted
    (it holds API keys, filesystem paths and library roots) and because the
    endpoints that write it are gated by require_localhost_or_lan. Note that the
    primitive is blind only on the completion path: the model-list endpoint
    returns the ids it parses out of the response, so a JSON body shaped like
    OpenAI's /v1/models is partially reflected to the caller. That is why the
    metadata hosts are denied outright. See
    docs/superpowers/specs/2026-08-29-local-ai-provider-design.md.

    Whitespace and control characters are rejected against the RAW string, before
    parsing. CPython strips tab, CR and LF out of a URL before urlparse sees it,
    so without this check "http://localhost\n.evil.com" would be validated as
    the host "localhost.evil.com" while the caller sent the original bytes, and
    httpx would raise InvalidURL, which is not an httpx.HTTPError subclass and so
    escapes the transport handler in ai_client. Keeping the judged string and the
    sent string identical is what makes this a real barrier. It also keeps CR/LF
    out of a value that reaches the logs.

    Also rejected: non-http(s) schemes (file:, javascript:, ftp:), a missing
    host, embedded credentials, query, fragment, path parameters, a port outside
    the valid range, and any ".." path segment. The traversal check matters
    because callers append "/chat/completions" or "/models" to this value, so a
    traversal segment would move the request outside the prefix the user
    approved.
    """
    if not isinstance(url, str):
        return False

    # Against the raw string, before urlparse can normalise it away.
    if url != url.strip() or any(ch in url for ch in "\t\r\n"):
        return False
    if _LOG_CONTROL_CHARS_RE.search(url):
        return False

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not host:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment or parsed.params:
        return False
    if host in _LOCAL_AI_DENIED_HOSTS:
        return False

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass  # Not an IP literal; a hostname is fine.
    else:
        for candidate in _ip_and_embedded_ipv4(addr):
            if candidate.is_link_local or str(candidate) in _LOCAL_AI_DENIED_HOSTS:
                return False

    if ".." in parsed.path.split("/"):
        return False

    # An out-of-range port parses here but raises inside httpx.
    try:
        _ = parsed.port
    except ValueError:
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
    # str.splitlines() already knows every boundary Python calls a line break
    # (LF, CR, CRLF, and the exotics: NEL, U+2028, U+2029), so rejoining its
    # parts collapses all of them without hardcoding a character class that
    # would drift from that definition. CRLF counts as one boundary, so a pair
    # becomes a single space. Mainstream .m3u demuxers only split on 0x0a, so
    # the exotics are not exploitable against VLC or MPV today; covering them
    # keeps the guarantee tied to the function's name rather than to which
    # parser happens to read the output.
    collapsed = " ".join(value.splitlines())
    return _LOG_CONTROL_CHARS_RE.sub("", collapsed).strip()
