"""Neutralize CA-bundle environment variables that point at a file that is not there.

``requests`` reads ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE`` on every call and raises
"Could not find a suitable TLS CA certificate bundle, invalid path" when the named file is
missing, instead of falling back to certifi. ``httpx`` reads ``SSL_CERT_FILE`` the same way.
A stale value left behind by another tool (a proxy, a VPN client, an old Python setup)
therefore breaks every TMDB, subtitle and update request with an error that names a path
Engram never set. A bundle that exists is left alone, so a real corporate CA keeps working.
"""

import os

CA_BUNDLE_ENV_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")


def drop_missing_ca_bundle_env() -> list[tuple[str, str]]:
    """Remove CA-bundle env vars whose path does not exist; return the ``(var, value)`` dropped."""
    dropped: list[tuple[str, str]] = []
    for var in CA_BUNDLE_ENV_VARS:
        value = os.environ.get(var)
        if value and not os.path.exists(value):
            del os.environ[var]
            dropped.append((var, value))
    return dropped
