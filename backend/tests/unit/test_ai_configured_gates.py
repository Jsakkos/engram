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
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_GATED_MODULES = [
    "app.core.curator",
    "app.services.matching_coordinator",
    "app.services.identification_coordinator",
    "app.api.routes",
]

_LOCAL_BASE_URL = "http://box:11434/v1"

# The two spellings a truthiness gate on the key actually takes in this codebase:
# a standalone `if not config.ai_api_key` and a conjunct `and config.ai_api_key`
# inside a larger condition. Matching both matters — an earlier version of this
# test only looked for the `not` form and so passed against
# identification_coordinator, which used the `and` form, while the bug was live.
#
# Deliberately NOT matched, because all three are legitimate:
#   ai_api_key=config.ai_api_key                    (pass-through to the matcher)
#   ai_is_configured(config.ai_provider, config.ai_api_key)   (the helper itself)
#   ai_api_key="***" if config.ai_api_key else ""   (redaction for GET /api/config)
# The redaction is a truthiness test too, but `if ... else` is not a gate, so the
# pattern requires the `not`/`and` keywords rather than banning bare truthiness.
_KEY_GATE_SPELLING = re.compile(r"\b(?:not|and)\s+config\.ai_api_key\b")


@pytest.mark.parametrize("module_path", _GATED_MODULES)
def test_no_module_gates_on_the_api_key_directly(module_path):
    source = inspect.getsource(importlib.import_module(module_path))
    offenders = _KEY_GATE_SPELLING.findall(source)
    assert not offenders, (
        f"{module_path} still gates on the API key directly ({len(offenders)} site(s)). "
        "Local providers have no key, so this silently disables them. "
        "Use ai_is_configured(...)."
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

    @pytest.mark.asyncio
    async def test_manual_rematch_route_posts_to_the_configured_endpoint(self):
        """The per-track re-match route (POST .../rematch) must honour it too.

        Driven end to end rather than asserted on source: the route's own gate
        and its base-URL hand-off are two separate mistakes, and only running it
        catches both at once.
        """
        from types import SimpleNamespace

        from app.api.routes import _run_llm_match_for_title
        from app.models.app_config import AppConfig

        config = AppConfig(
            ai_episode_matching_enabled=True,
            ai_provider="ollama",
            ai_api_key="",
            ai_model="llama3.1",
            ai_local_base_url=_LOCAL_BASE_URL,
            tmdb_api_key="t",
        )
        job = SimpleNamespace(id=1, detected_title="The Expanse", detected_season=1)
        title = SimpleNamespace(id=7)
        synopses = [{"episode_number": 2, "name": "Cargo", "overview": "A heist."}]
        client = _mock_transport(
            _openai_chat_response('{"episode": 2, "confidence": 0.9, "reasoning": "cargo"}')
        )
        matcher = MagicMock()
        matcher.transcribe_full.return_value = "the freighter cargo hold " * 100

        with (
            patch(
                "app.services.config_service.get_config",
                new=AsyncMock(return_value=config),
            ),
            patch("app.core.curator.curator._ensure_initialized", MagicMock()),
            patch("app.core.curator.curator._matcher", matcher),
            patch("app.matcher.tmdb_client.fetch_show_id", return_value=12345),
            patch(
                "app.services.ripping_helpers.find_staging_file",
                return_value="/staging/t7.mkv",
            ),
            patch(
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
                return_value=synopses,
            ),
            patch("app.core.ai_client.httpx.AsyncClient", return_value=client),
        ):
            outcome = await _run_llm_match_for_title(title=title, job=job)

        # A keyless local provider must not be turned away as "not_configured".
        assert outcome.reason is None, f"route refused a local provider: {outcome.reason}"
        assert _posted_url(client) == f"{_LOCAL_BASE_URL}/chat/completions"
