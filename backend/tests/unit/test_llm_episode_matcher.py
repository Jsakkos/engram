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
