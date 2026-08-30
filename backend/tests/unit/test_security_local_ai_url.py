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
