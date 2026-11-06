"""CA-bundle environment variables that point at a missing file must not break HTTPS.

``requests`` raises "Could not find a suitable TLS CA certificate bundle, invalid path"
on every call when ``REQUESTS_CA_BUNDLE``/``CURL_CA_BUNDLE`` names a file that is not
there, which took down TMDB validation for a Windows user with a stale
``C:\\certs\\root.pem`` in their environment.
"""

import pytest

from app.core.tls_env import CA_BUNDLE_ENV_VARS, drop_missing_ca_bundle_env


@pytest.fixture(autouse=True)
def _clear_ca_env(monkeypatch):
    for var in CA_BUNDLE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_missing_bundle_path_is_dropped(monkeypatch, tmp_path):
    missing = str(tmp_path / "certs" / "root.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", missing)
    monkeypatch.setenv("SSL_CERT_FILE", missing)

    dropped = drop_missing_ca_bundle_env()

    assert dropped == [("REQUESTS_CA_BUNDLE", missing), ("SSL_CERT_FILE", missing)]
    import os

    assert "REQUESTS_CA_BUNDLE" not in os.environ
    assert "SSL_CERT_FILE" not in os.environ


def test_existing_bundle_is_kept(monkeypatch, tmp_path):
    # A corporate proxy's real CA bundle must keep working.
    bundle = tmp_path / "corp.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("CURL_CA_BUNDLE", str(bundle))

    assert drop_missing_ca_bundle_env() == []
    import os

    assert os.environ["CURL_CA_BUNDLE"] == str(bundle)


def test_unset_and_empty_vars_are_ignored(monkeypatch):
    # requests already treats an empty value as unset; leave it alone.
    monkeypatch.setenv("CURL_CA_BUNDLE", "")
    assert drop_missing_ca_bundle_env() == []
