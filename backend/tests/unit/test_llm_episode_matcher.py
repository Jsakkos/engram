"""Tests for the LLM episode matcher."""

from unittest.mock import AsyncMock, patch

import pytest


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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
                return_value=synopses,
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                new=AsyncMock(return_value=ai_response),
            ),
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
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
