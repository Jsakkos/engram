"""The 'is AI configured?' invariant.

Four call sites once used `if not config.ai_api_key` as a proxy for 'is AI
usable?'. Local providers have no key, so any site still testing the key
directly silently disables local AI while appearing correctly configured. This
is a source-level guard because the alternative is three separate integration
tests that each need a full job pipeline.

The second half of the file is behavioural: the gates opening is useless if the
user's configured endpoint never reaches the outbound request, so those tests
drive the real ``complete_json`` with a mocked transport and assert on the URL
that was actually POSTed to.
"""

import importlib
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_GATED_MODULES = [
    "app.core.curator",
    "app.services.matching_coordinator",
    "app.services.identification_coordinator",
]

_LOCAL_BASE_URL = "http://box:11434/v1"


@pytest.mark.parametrize("module_path", _GATED_MODULES)
def test_no_module_gates_on_the_api_key_directly(module_path):
    source = inspect.getsource(importlib.import_module(module_path))
    assert "not config.ai_api_key" not in source, (
        f"{module_path} still gates on the API key directly. Local providers have "
        "no key, so this silently disables them. Use ai_is_configured(...)."
    )


@pytest.mark.parametrize("module_path", _GATED_MODULES)
def test_each_gated_module_uses_the_shared_helper(module_path):
    source = inspect.getsource(importlib.import_module(module_path))
    assert "ai_is_configured" in source, f"{module_path} does not use ai_is_configured"


def test_complete_json_returns_none_for_a_keyless_remote_provider():
    """The relaxation must not accidentally let remote providers through."""
    import asyncio

    from app.core.ai_client import complete_json

    result = asyncio.run(complete_json(prompt="hi", schema=None, provider="openai", api_key=""))
    assert result is None


def _mock_transport(response_json: dict):
    """A stand-in for httpx.AsyncClient returning ``response_json`` from POST."""
    response = MagicMock()
    response.json.return_value = response_json
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return client


def _openai_chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _posted_url(client) -> str:
    """The URL argument of the single POST the client received."""
    assert client.post.await_count == 1, f"expected one POST, saw {client.post.await_count}"
    args, kwargs = client.post.call_args
    return args[0] if args else kwargs["url"]


class TestLocalBaseUrlIsThreadedToTheRequest:
    """A configured endpoint must survive the whole call chain, not just be stored."""

    @pytest.mark.asyncio
    async def test_identify_from_label_posts_to_the_configured_endpoint(self):
        from app.core.ai_identifier import identify_from_label

        client = _mock_transport(
            _openai_chat_response('{"title": "Inception", "year": 2010, "type": "movie"}')
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=client):
            result = await identify_from_label(
                "INCEPTION_2010",
                "ollama",
                "",
                "llama3.1",
                base_url=_LOCAL_BASE_URL,
            )

        assert result is not None and result["title"] == "Inception"
        assert _posted_url(client) == f"{_LOCAL_BASE_URL}/chat/completions"

    @pytest.mark.asyncio
    async def test_match_episode_via_llm_posts_to_the_configured_endpoint(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        synopses = [
            {"episode_number": 1, "name": "Pilot", "overview": "Aliens arrive."},
            {"episode_number": 2, "name": "Cargo", "overview": "A heist on a freighter."},
        ]
        client = _mock_transport(
            _openai_chat_response('{"episode": 2, "confidence": 0.9, "reasoning": "cargo"}')
        )
        with (
            patch(
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
                return_value=synopses,
            ),
            patch("app.core.ai_client.httpx.AsyncClient", return_value=client),
        ):
            result = await match_episode_via_llm(
                transcript="they boarded the freighter and unloaded the cargo " * 50,
                show_name="The Expanse",
                season=1,
                tmdb_show_id="12345",
                ai_provider="ollama",
                ai_api_key="",
                tmdb_api_key="t",
                ai_model="llama3.1",
                ai_local_base_url=_LOCAL_BASE_URL,
            )

        assert result is not None and result.episode == 2
        assert _posted_url(client) == f"{_LOCAL_BASE_URL}/chat/completions"
