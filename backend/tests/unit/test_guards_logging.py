"""The LAN gate's refusal log must not carry unsanitized request data.

The log line exists so a Docker-behind-a-proxy 403 stops reading as an
unexplained failure. It interpolates the request path, which is attacker
controlled and arrives percent-decoded (uvicorn unquotes it into the ASGI
scope), so it goes through sanitize_log_value like every other tainted value in
this codebase (py/log-injection).
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.guards import require_localhost_or_lan


def _request(path: str, peer: str = "172.18.0.14") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("engram", 8000),
            "client": (peer, 56896),
        }
    )


async def _refuse(request: Request) -> None:
    """Drive the gate with LAN access off, regardless of this machine's DB."""
    stub = MagicMock(allow_lan_access=False)
    with patch("app.services.config_service.get_config", new=AsyncMock(return_value=stub)):
        with pytest.raises(HTTPException) as exc:
            await require_localhost_or_lan(request)
    assert exc.value.status_code == 403


class TestRefusalLogIsSanitized:
    @pytest.mark.asyncio
    async def test_control_characters_are_stripped_from_the_path(self, caplog):
        # A terminal escape survives Starlette's URL parsing (which drops CR/LF
        # of its own accord), so it is the case that proves the sanitizer runs.
        with caplog.at_level(logging.WARNING, logger="app.api.guards"):
            await _refuse(_request("/api/validate/ai\x1b[2JCLEARED"))

        assert caplog.records, "the refusal must be logged at all"
        message = caplog.records[-1].getMessage()
        assert "\x1b" not in message
        assert "\r" not in message and "\n" not in message

    @pytest.mark.asyncio
    async def test_the_log_still_says_what_closed_the_gate(self, caplog):
        """Sanitizing must not gut the diagnostic this line was added for."""
        with caplog.at_level(logging.WARNING, logger="app.api.guards"):
            await _refuse(_request("/api/validate/ai"))

        message = caplog.records[-1].getMessage()
        assert "/api/validate/ai" in message
        assert "172.18.0.14" in message
        assert "allow_lan_access" in message

    @pytest.mark.asyncio
    async def test_loopback_is_not_logged_or_refused(self):
        """The gate opens for loopback, so there is nothing to report."""
        await require_localhost_or_lan(_request("/api/validate/ai", peer="127.0.0.1"))
