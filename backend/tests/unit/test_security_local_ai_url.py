"""Tests for the local-AI base-URL guard.

This guard is deliberately weaker than is_safe_remote_url: it must ACCEPT
loopback and private addresses, because that is where a local inference server
lives. What it still rejects is what makes it a guard at all.
"""

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.1.50:11434/v1",
        "https://ai.lan:1234/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_accepts_local_and_private_endpoints(url):
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "ftp://localhost/v1",
        "javascript:alert(1)",
        "http://",
        "//localhost:11434/v1",
        "http://user:pass@localhost:11434/v1",
        "http://localhost:11434/v1?x=1",
        "http://localhost:11434/v1#frag",
    ],
)
def test_rejects_unsafe_shapes(url):
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is False


def test_is_not_an_alias_for_the_remote_guard():
    """Regression guard: the two must stay distinct.

    If someone ever points is_safe_local_ai_url at is_safe_remote_url, every
    local provider breaks with a confusing validation error rather than an
    obvious one, so pin the difference.
    """
    from app.core.security import is_safe_local_ai_url, is_safe_remote_url

    assert is_safe_remote_url("http://localhost:11434/v1") is False
    assert is_safe_local_ai_url("http://localhost:11434/v1") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1 ",
        " http://localhost:11434/v1",
        "http://localhost:11434/v1\n",
        "http://localhost\n.evil.com:11434/v1",
        "http://local\thost:11434/v1",
        "http://localhost:11434/v1\x00",
    ],
)
def test_rejects_whitespace_and_control_characters(url):
    """urlparse strips tab/CR/LF before parsing, so without a raw-string check
    the guard would validate a DIFFERENT string than the caller sends, and httpx
    would then raise InvalidURL, which is not an httpx.HTTPError and so escapes
    the transport handler in ai_client."""
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is False


def test_rejects_path_traversal():
    """Callers append "/chat/completions", so a traversal segment would move the
    request outside the prefix the user approved."""
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url("http://localhost:11434/v1/../../admin") is False


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/v1",
        "http://169.254.1.1:11434/v1",
    ],
)
def test_rejects_metadata_and_link_local_hosts(url):
    """No inference server runs here, and the model-list endpoint reflects part
    of the response back to the caller."""
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is False


def test_rejects_an_out_of_range_port():
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url("http://localhost:99999/v1") is False


def test_rejects_path_parameters():
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url("http://localhost:11434/v1;x=1") is False


@pytest.mark.parametrize("value", [None, 123, b"http://localhost:11434/v1", object()])
def test_fails_closed_on_non_string_input(value):
    """A guard must never raise; raising skips the caller's `if not guard(...)`."""
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(value) is False


def test_deliberately_allows_a_non_local_host():
    """Pins the load-bearing surprise: this guard imposes no locality restriction
    beyond the denied metadata/link-local hosts, because a user may run their
    inference server on another machine. It is NOT what stops a hostile endpoint
    being configured; require_localhost_or_lan on the writing endpoints is."""
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url("https://gpubox.example.com/v1") is True
