"""Tests for the LLM episode matcher."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, MockTransport, Response


@pytest.fixture
def match_response(monkeypatch):
    from app.matcher import llm_episode_matcher as matcher

    monkeypatch.setattr(
        matcher,
        "season_episodes",
        lambda *args: [
            {"episode_number": 1, "name": "Pilot"},
            {"episode_number": 3, "name": "Cargo"},
        ],
    )

    async def match(raw):
        monkeypatch.setattr(matcher, "complete_json", AsyncMock(return_value=raw))
        return await matcher.match_episode_via_llm(
            transcript="they boarded the freighter and unloaded the cargo " * 30,
            show_name="The Expanse",
            season=1,
            tmdb_show_id="123",
            ai_provider="lmstudio",
            ai_api_key="",
            tmdb_api_key="test",
            ai_model="qwen3-4b",
        )

    return match


@pytest.mark.asyncio
@pytest.mark.parametrize("episode", [0, 2, 99, -1, 1.9, 1.0, True, "1", None])
async def test_rejects_episodes_outside_the_roster_and_nonintegers(match_response, episode):
    assert await match_response({"episode": episode, "confidence": 0.9}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confidence", [-0.1, 1.7, float("nan"), float("inf"), float("-inf"), True, "0.9", None]
)
async def test_rejects_invalid_confidence(match_response, confidence):
    assert await match_response({"episode": 1, "confidence": confidence}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runner_up",
    [
        {"episode": 2, "confidence": 0.5},
        {"episode": 1, "confidence": 0.5},
        {"episode": 3.9, "confidence": 0.5},
        {"episode": True, "confidence": 0.5},
        {"episode": 3, "confidence": 1.7},
        {"episode": 3, "confidence": float("nan")},
        {"episode": 3, "confidence": True},
        {"episode": 3},
        "invalid",
    ],
)
async def test_drops_invalid_runner_up_without_losing_primary(match_response, runner_up):
    result = await match_response({"episode": 1, "confidence": 0.9, "runner_up": runner_up})
    assert result is not None
    assert result.episode == 1
    assert result.runner_up is None


@pytest.mark.asyncio
async def test_accepts_confidence_boundaries_and_valid_runner_up(match_response):
    from app.matcher.llm_episode_matcher import RunnerUp

    result = await match_response(
        {
            "episode": 1,
            "confidence": 1,
            "runner_up": {"episode": 3, "confidence": 0},
        }
    )
    assert result.confidence == 1.0
    assert result.runner_up == RunnerUp(episode=3, confidence=0.0)
    assert await match_response({"episode": 1, "confidence": 0}) is None


class TestMatchEpisodeViaLLM:
    @pytest.mark.asyncio
    async def test_happy_path_returns_llm_episode_match(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        synopses = [
            {"episode_number": 1, "name": "Pilot", "overview": "Aliens arrive."},
            {"episode_number": 2, "name": "Cargo", "overview": "A heist on a freighter."},
            {"episode_number": 3, "name": "Echo", "overview": "Mysterious signals."},
        ]
        ai_response = {
            "episode": 2,
            "confidence": 0.91,
            "reasoning": "Mentions of cargo and freighter alignment.",
            "runner_up": {"episode": 1, "confidence": 0.04},
        }

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=synopses,
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value=ai_response),
            ) as mock_ai,
        ):
            result = await match_episode_via_llm(
                transcript="they boarded the freighter and unloaded the cargo " * 50,
                show_name="The Expanse",
                season=1,
                tmdb_show_id="12345",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        from app.matcher.llm_episode_matcher import RunnerUp

        assert result is not None
        assert result.episode == 2
        assert result.confidence == 0.91
        assert result.runner_up == RunnerUp(episode=1, confidence=0.04)
        assert result.model == "gemini-2.5-flash-lite"
        assert mock_ai.await_args.kwargs["max_tokens"] == 512

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", ["lmstudio", "ollama"])
    @pytest.mark.parametrize("completion_tokens", [1024, 8192])
    async def test_local_reasoning_output_budget(self, provider, completion_tokens):
        from app.core.errors import AIProviderError
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        def complete(request):
            body = json.loads(request.content)
            # Greedy decoding can loop indefinitely in a local reasoning model.
            finished = "temperature" not in body and body["max_tokens"] >= completion_tokens
            return Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop" if finished else "length",
                            "message": {
                                "content": '{"episode": 2, "confidence": 0.9}' if finished else ""
                            },
                        }
                    ]
                },
            )

        provider_client = AsyncClient(transport=MockTransport(complete))
        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 2, "name": "Cargo", "overview": "A heist."}],
            ),
            patch("app.core.ai_client.httpx.AsyncClient", return_value=provider_client),
        ):
            match = match_episode_via_llm(
                transcript="they unloaded the cargo from the freighter " * 30,
                show_name="The Expanse",
                season=1,
                tmdb_show_id="12345",
                ai_provider=provider,
                ai_api_key="",
                tmdb_api_key="t",
                ai_model="qwen3-4b-thinking",
                raise_on_error=True,
            )
            if completion_tokens > 4096:
                with pytest.raises(AIProviderError) as exc_info:
                    await match
                assert exc_info.value.code == "response_truncated"
            else:
                result = await match
                assert result is not None
                assert result.episode == 2

    @pytest.mark.asyncio
    async def test_confidence_zero_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        synopses = [{"episode_number": 1, "name": "X", "overview": "y"}]
        ai_response = {
            "episode": 0,
            "confidence": 0.0,
            "reasoning": "wrong show",
            "runner_up": None,
        }

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=synopses,
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value=ai_response),
            ),
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_short_transcript_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value={"episode": 1, "confidence": 0.9}),
            ) as mock_ai,
        ):
            result = await match_episode_via_llm(
                transcript="too short",
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_synopses_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value={"episode": 1, "confidence": 0.9}),
            ) as mock_ai,
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_response_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value={"reasoning": "oops"}),  # missing episode/confidence
            ),
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )
        assert result is None


class TestMatchEpisodeViaLlmRaiseOnError:
    @pytest.mark.asyncio
    async def test_raise_on_error_propagates_aiprovidererror(self):
        from unittest.mock import AsyncMock, patch

        from app.core.errors import AIProviderError
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        transcript = "the detective examined the case file carefully " * 20  # >500 chars
        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 1, "name": "Pilot", "overview": "x"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                AsyncMock(side_effect=AIProviderError("boom")),
            ) as mock_cj,
        ):
            with pytest.raises(AIProviderError):
                await match_episode_via_llm(
                    transcript=transcript,
                    show_name="X",
                    season=1,
                    tmdb_show_id="123",
                    ai_provider="gemini",
                    ai_api_key="k",
                    tmdb_api_key="t",
                    raise_on_error=True,
                )
        assert mock_cj.await_args.kwargs["raise_on_error"] is True

    @pytest.mark.asyncio
    async def test_default_threads_false_and_returns_none(self):
        from unittest.mock import AsyncMock, patch

        from app.matcher.llm_episode_matcher import match_episode_via_llm

        transcript = "the detective examined the case file carefully " * 20
        with (
            patch(
                "app.matcher.llm_episode_matcher.season_episodes",
                return_value=[{"episode_number": 1, "name": "Pilot", "overview": "x"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                AsyncMock(return_value=None),
            ) as mock_cj,
        ):
            result = await match_episode_via_llm(
                transcript=transcript,
                show_name="X",
                season=1,
                tmdb_show_id="123",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )
        assert result is None
        assert mock_cj.await_args.kwargs["raise_on_error"] is False
