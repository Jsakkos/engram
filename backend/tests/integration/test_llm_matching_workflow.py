"""End-to-end LLM episode matching workflow integration tests.

These tests verify the full LLM fallback chain:
  curator.match_single_file → primary matcher (mocked low/no confidence)
    → _maybe_add_llm_suggestion → match_episode_via_llm (mocked stable result)
    → MatchResult with llm_suggestion in match_details

And that the matching coordinator persists a MatchResult carrying llm_suggestion
to the disc_titles table (match_details JSON, state=REVIEW, not match_source='engram').

NOTE: The /api/simulate/insert-disc endpoint uses SimulationService._simulate_matching,
which generates random fake matching results without going through EpisodeCurator.
The integration tests therefore test the curator and coordinator paths directly,
rather than going through the HTTP simulation endpoint.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.curator import EpisodeCurator, MatchResult
from app.database import async_session, init_db
from app.main import app
from app.models import AppConfig, DiscJob, TitleState
from app.models.disc_job import ContentType, DiscTitle, JobState


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean job data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def llm_config():
    """Seed an AppConfig row with AI matching enabled.

    Returns the config object. Note: setup_db does NOT delete app_config, so
    seeded values persist across tests in the same session — which is fine
    since enabling LLM matching is harmless for other tests.
    """
    from sqlmodel import select

    async with async_session() as session:
        rows = (await session.execute(select(AppConfig))).scalars().all()
        if rows:
            cfg = rows[0]
            cfg.ai_episode_matching_enabled = True
            cfg.ai_provider = "gemini"
            cfg.ai_api_key = "test-key"
            cfg.tmdb_api_key = "test-tmdb"
            session.add(cfg)
        else:
            cfg = AppConfig(
                ai_episode_matching_enabled=True,
                ai_provider="gemini",
                ai_api_key="test-key",
                tmdb_api_key="test-tmdb",
            )
            session.add(cfg)
        await session.commit()
        return cfg


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_LLM_SUGGESTION_EPISODE = 7
_LLM_SUGGESTION_MODEL = "gemini-2.5-flash-lite"

_LLM_SUGGESTION_DICT = {
    "episode": _LLM_SUGGESTION_EPISODE,
    "confidence": 0.93,
    "reasoning": "distinct cargo dialogue",
    "runner_up": {"episode": 6, "confidence": 0.12},
    "model": _LLM_SUGGESTION_MODEL,
}


def _build_stable_llm_match():
    """Build a LLMEpisodeMatch fixture using the dataclasses from Task 9."""
    from app.matcher.llm_episode_matcher import LLMEpisodeMatch, RunnerUp

    return LLMEpisodeMatch(
        episode=_LLM_SUGGESTION_EPISODE,
        confidence=0.93,
        reasoning="distinct cargo dialogue",
        runner_up=RunnerUp(episode=6, confidence=0.12),
        model=_LLM_SUGGESTION_MODEL,
    )


# ---------------------------------------------------------------------------
# Test: curator.match_single_file attaches llm_suggestion when primary is empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
class TestCuratorLLMFallback:
    """Integration tests for the EpisodeCurator → LLM fallback chain.

    Uses a real EpisodeCurator instance with the inner EpisodeMatcher and
    match_episode_via_llm replaced by mocks, exercising the full conditional
    logic inside match_single_file / _maybe_add_llm_suggestion.
    """

    async def test_attaches_llm_suggestion_when_primary_returns_no_match(
        self, tmp_path, llm_config
    ):
        """When identify_episode returns None (no match found), the curator's
        fallback path should still call _maybe_add_llm_suggestion and attach
        the result to MatchResult.match_details['llm_suggestion'].
        """
        fake_file = tmp_path / "title_01.mkv"
        fake_file.write_bytes(b"\x00" * 100)  # tiny stub so .exists() passes

        stable = _build_stable_llm_match()
        fake_matcher = MagicMock()
        fake_matcher.identify_episode.return_value = None  # primary found nothing
        fake_matcher.transcribe_full.return_value = "x" * 600  # enough for LLM path

        curator = EpisodeCurator()
        curator._matcher = fake_matcher
        curator._initialized = True
        curator._current_show = "Test Show"
        curator._cache_dir = tmp_path

        with (
            patch(
                "app.matcher.tmdb_client.fetch_show_id",
                return_value="1234",
            ),
            patch(
                "app.core.curator.match_episode_via_llm",
                new=AsyncMock(return_value=stable),
            ),
        ):
            result = await curator.match_single_file(
                fake_file,
                series_name="Test Show",
                season=1,
            )

        # Primary returned nothing → needs_review, no confirmed episode
        assert result.needs_review is True
        assert result.episode_code is None  # filename "title_01.mkv" has no S##E## pattern

        # LLM suggestion must be embedded in match_details
        assert result.match_details is not None, "match_details should not be None"
        llm = result.match_details.get("llm_suggestion")
        assert llm is not None, f"llm_suggestion missing from match_details: {result.match_details}"
        assert llm["episode"] == _LLM_SUGGESTION_EPISODE
        assert llm["model"] == _LLM_SUGGESTION_MODEL
        assert llm["runner_up"]["episode"] == 6

    async def test_attaches_llm_suggestion_when_primary_is_low_confidence(
        self, tmp_path, llm_config
    ):
        """When identify_episode returns a low-confidence match (< 0.7 threshold),
        the curator should still run the LLM fallback and embed the suggestion.
        """
        fake_file = tmp_path / "episode_file.mkv"
        fake_file.write_bytes(b"\x00" * 100)

        stable = _build_stable_llm_match()
        fake_matcher = MagicMock()
        # Low-confidence primary match (0.4 < HIGH_CONFIDENCE_THRESHOLD=0.7)
        fake_matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.4,
            "score": 0.4,
            "match_details": {},
            "runner_ups": [],
            "transcript": "x" * 600,  # pass transcript through to skip re-transcription
        }

        curator = EpisodeCurator()
        curator._matcher = fake_matcher
        curator._initialized = True
        curator._current_show = "Test Show"
        curator._cache_dir = tmp_path

        with (
            patch(
                "app.matcher.tmdb_client.fetch_show_id",
                return_value="1234",
            ),
            patch(
                "app.core.curator.match_episode_via_llm",
                new=AsyncMock(return_value=stable),
            ),
        ):
            result = await curator.match_single_file(
                fake_file,
                series_name="Test Show",
                season=1,
            )

        # Confirmed episode from primary (even though low confidence)
        assert result.episode_code == "S01E03"
        assert result.needs_review is True  # confidence < HIGH_CONFIDENCE_THRESHOLD

        # LLM suggestion attached
        assert result.match_details is not None
        llm = result.match_details.get("llm_suggestion")
        assert llm is not None, f"llm_suggestion missing: {result.match_details}"
        assert llm["episode"] == _LLM_SUGGESTION_EPISODE
        assert llm["model"] == _LLM_SUGGESTION_MODEL

    async def test_no_llm_when_disabled_in_config(self, tmp_path, llm_config):
        """When ai_episode_matching_enabled=False, match_episode_via_llm must
        not be called, even if the primary matcher returns nothing.
        """
        # Override config to disable LLM
        from sqlmodel import select

        async with async_session() as session:
            rows = (await session.execute(select(AppConfig))).scalars().all()
            if rows:
                rows[0].ai_episode_matching_enabled = False
                session.add(rows[0])
                await session.commit()

        fake_file = tmp_path / "episode_file.mkv"
        fake_file.write_bytes(b"\x00" * 100)

        fake_matcher = MagicMock()
        fake_matcher.identify_episode.return_value = None

        curator = EpisodeCurator()
        curator._matcher = fake_matcher
        curator._initialized = True
        curator._current_show = "Test Show"
        curator._cache_dir = tmp_path

        llm_mock = AsyncMock(return_value=_build_stable_llm_match())
        with patch("app.core.curator.match_episode_via_llm", new=llm_mock):
            result = await curator.match_single_file(fake_file, series_name="Test Show", season=1)

        llm_mock.assert_not_called()
        assert result.match_details is None or "llm_suggestion" not in (result.match_details or {})


# ---------------------------------------------------------------------------
# Test: MatchingCoordinator persists match_details with llm_suggestion to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
class TestMatchingCoordinatorPersistence:
    """Verify the matching coordinator persists a MatchResult carrying an
    llm_suggestion to disc_titles.match_details, sets state=REVIEW, and does
    NOT set match_source='engram' when no episode_code is confirmed.
    """

    async def _seed_job_and_title(self, tmp_path: Path) -> tuple[int, int, Path]:
        """Create a minimal DiscJob + DiscTitle in the DB and return their IDs."""
        fake_file = tmp_path / "title_01.mkv"
        fake_file.write_bytes(b"\x00" * 100)

        async with async_session() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="TEST_S1D1",
                state=JobState.MATCHING,
                content_type=ContentType.TV,
                detected_title="Test Show",
                detected_season=1,
            )
            session.add(job)
            await session.flush()

            title = DiscTitle(
                job_id=job.id,
                title_index=1,  # NOT NULL in schema
                duration_seconds=1800,
                file_size_bytes=1_000_000,
                state=TitleState.MATCHING,
                file_path=str(fake_file),
            )
            session.add(title)
            await session.commit()
            await session.refresh(job)
            await session.refresh(title)
            return job.id, title.id, fake_file

    async def test_coordinator_persists_llm_suggestion_and_sets_review_state(
        self, tmp_path, llm_config
    ):
        """Coordinator stores llm_suggestion in disc_titles.match_details,
        sets state=REVIEW, and does not set match_source='engram'.
        """
        from app.api.websocket import manager as ws_manager
        from app.services.event_broadcaster import EventBroadcaster
        from app.services.job_state_machine import JobStateMachine
        from app.services.matching_coordinator import MatchingCoordinator

        job_id, title_id, fake_file = await self._seed_job_and_title(tmp_path)

        # MatchResult with no confirmed episode + llm_suggestion in match_details
        stub_result = MatchResult(
            file_path=fake_file,
            episode_code=None,
            episode_title=None,
            confidence=0.0,
            needs_review=True,
            match_details={"llm_suggestion": _LLM_SUGGESTION_DICT},
        )

        # Build coordinator with minimal mocked dependencies
        mock_broadcaster = MagicMock(spec=EventBroadcaster)
        mock_broadcaster.broadcast_job_state_changed = AsyncMock()
        mock_state_machine = MagicMock(spec=JobStateMachine)

        coordinator = MatchingCoordinator(mock_broadcaster, mock_state_machine)
        coordinator.set_callbacks(check_job_completion=AsyncMock(), note_activity=None)
        coordinator.init_semaphore(concurrency=1)
        # Pre-populate _episode_runtimes so duration pre-filter is a no-op
        coordinator._episode_runtimes[job_id] = []

        with (
            patch(
                "app.services.matching_coordinator.episode_curator.match_single_file",
                new=AsyncMock(return_value=stub_result),
            ),
            patch.object(coordinator, "_wait_for_file_ready", new=AsyncMock(return_value=True)),
            patch.object(ws_manager, "broadcast_title_update", new=AsyncMock()),
        ):
            await coordinator.match_single_file(job_id, title_id, fake_file)

        # Verify DB state
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)

        assert title is not None
        assert title.match_details is not None, "match_details should be persisted"
        details = json.loads(title.match_details)
        assert "llm_suggestion" in details, f"llm_suggestion missing from DB: {details}"
        assert details["llm_suggestion"]["episode"] == _LLM_SUGGESTION_EPISODE
        assert details["llm_suggestion"]["model"] == _LLM_SUGGESTION_MODEL

        # No confirmed episode → state must be REVIEW, not MATCHED
        assert title.state == TitleState.REVIEW, f"expected REVIEW, got {title.state!r}"

        # match_source must NOT be 'engram' when no episode was auto-confirmed
        assert title.match_source != "engram", (
            f"match_source should not be 'engram'; got {title.match_source!r}"
        )
