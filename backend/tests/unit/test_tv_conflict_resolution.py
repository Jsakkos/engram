"""TV library conflicts honor an explicit per-track choice, never the default.

Follow-up to #685 (docs/superpowers/specs/2026-09-23-tv-conflict-resolution.md).
A TV FILE_EXISTS is usually a duplicate track or a mis-matched episode, so the
configured ``conflict_resolution_default`` (meant for "I already have this
movie") must not reach a TV organize: "overwrite" would replace a correct
episode with a wrong one. A reviewer who explicitly says "Replace" for a track
is honored, and that choice is dropped when the track moves to another episode.
"""

import importlib
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

organizer_module = importlib.import_module("app.core.organizer")
config_service_module = importlib.import_module("app.services.config_service")


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


@pytest.fixture(autouse=True)
def default_is_overwrite(monkeypatch):
    """Configure the most dangerous default, so a leak into TV would show up."""
    real_get_config = config_service_module.get_config

    async def _cfg():
        cfg = await real_get_config()
        return cfg.model_copy(update={"conflict_resolution_default": "overwrite"})

    monkeypatch.setattr(config_service_module, "get_config", _cfg)


@pytest.fixture
def organize(monkeypatch) -> Mock:
    """Library-mode TV organize, succeeding; the mock records the strategy."""
    m = Mock(
        side_effect=lambda src, show, ep, **k: {
            "success": True,
            "final_path": f"/library/tv/{show}/{ep}.mkv",
            "error": None,
        }
    )
    monkeypatch.setattr(organizer_module.tv_organizer, "organize", m)
    return m


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed(tmp_path, *, episode, state, job_state, conflict_resolution=None):
    """One TV track with a staged file. Returns (job_id, title_id)."""
    staged = tmp_path / "show_t00.mkv"
    staged.write_text("")
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=job_state,
            detected_title="Some Show",
            detected_season=1,
            staging_path=str(tmp_path),
            subtitle_status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1380,
            matched_episode=episode,
            match_confidence=0.8,
            state=state,
            output_filename=str(staged),
            conflict_resolution=conflict_resolution,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job.id, title.id


async def _title(job_id) -> DiscTitle:
    async with _unit_session_factory() as session:
        result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
        return result.scalars().one()


def _strategy(organize: Mock) -> str:
    return organize.call_args.kwargs["conflict_resolution"]


@pytest.mark.unit
class TestConfiguredDefaultNeverAppliesToTV:
    async def test_automatic_finalize_asks(self, tmp_path, organize):
        job_id, _ = await _seed(
            tmp_path, episode="S01E03", state=TitleState.MATCHED, job_state=JobState.MATCHING
        )

        await _make_coord().finalize_disc_job(job_id)

        assert _strategy(organize) == "ask"

    async def test_review_without_a_choice_asks(self, tmp_path, organize):
        job_id, title_id = await _seed(
            tmp_path, episode=None, state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": title_id, "episode_code": "S01E03"}]
        )

        assert _strategy(organize) == "ask"


@pytest.mark.unit
class TestExplicitChoiceIsHonored:
    @pytest.mark.parametrize("choice", ["overwrite", "rename"])
    async def test_batch_review_choice_reaches_the_organizer(self, tmp_path, organize, choice):
        job_id, title_id = await _seed(
            tmp_path, episode="S01E03", state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )

        await _make_coord().apply_review_batch(
            job_id,
            [{"title_id": title_id, "episode_code": "S01E03", "conflict_resolution": choice}],
        )

        assert _strategy(organize) == choice
        assert (await _title(job_id)).state == TitleState.COMPLETED

    async def test_single_review_choice_reaches_the_organizer(self, tmp_path, organize):
        job_id, title_id = await _seed(
            tmp_path, episode="S01E03", state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )

        await _make_coord().apply_review(
            job_id, title_id, episode_code="S01E03", conflict_resolution="overwrite"
        )

        assert _strategy(organize) == "overwrite"

    async def test_process_matched_reads_the_recorded_choice(self, tmp_path, organize):
        job_id, _ = await _seed(
            tmp_path,
            episode="S01E03",
            state=TitleState.MATCHED,
            job_state=JobState.REVIEW_NEEDED,
            conflict_resolution="rename",
        )

        await _make_coord().process_matched_titles(job_id)

        assert _strategy(organize) == "rename"

    async def test_automatic_finalize_reads_the_recorded_choice(self, tmp_path, organize):
        job_id, _ = await _seed(
            tmp_path,
            episode="S01E03",
            state=TitleState.MATCHED,
            job_state=JobState.MATCHING,
            conflict_resolution="overwrite",
        )

        await _make_coord().finalize_disc_job(job_id)

        assert _strategy(organize) == "overwrite"


@pytest.mark.unit
class TestSkipIsDiscard:
    async def test_skip_discards_the_track_without_organizing(self, tmp_path, organize):
        # "Keep the library copy" for one TV track is exactly Discard. Mapping it
        # there keeps the organizer's skip result out of the TV sweeps, which
        # would otherwise mark the track COMPLETED with nothing moved.
        job_id, title_id = await _seed(
            tmp_path, episode="S01E03", state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": title_id, "conflict_resolution": "skip"}]
        )

        organize.assert_not_called()
        title = await _title(job_id)
        assert title.state == TitleState.FAILED
        assert title.matched_episode == "skip"
        assert title.conflict_resolution is None
        assert (tmp_path / "show_t00.mkv").exists()


@pytest.mark.unit
class TestChoiceFollowsTheTarget:
    async def test_reassignment_drops_a_stale_choice(self, tmp_path, organize):
        # "Replace" was chosen for S01E03; the reviewer then moves the track to
        # S01E05. Replacing S01E05's library file was never asked for.
        job_id, title_id = await _seed(
            tmp_path,
            episode="S01E03",
            state=TitleState.REVIEW,
            job_state=JobState.REVIEW_NEEDED,
            conflict_resolution="overwrite",
        )

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": title_id, "episode_code": "S01E05"}]
        )

        assert _strategy(organize) == "ask"

    async def test_choice_sent_with_the_reassignment_holds(self, tmp_path, organize):
        job_id, title_id = await _seed(
            tmp_path, episode="S01E03", state=TitleState.REVIEW, job_state=JobState.REVIEW_NEEDED
        )

        await _make_coord().apply_review_batch(
            job_id,
            [{"title_id": title_id, "episode_code": "S01E05", "conflict_resolution": "rename"}],
        )

        assert _strategy(organize) == "rename"

    async def test_resubmitting_the_same_episode_keeps_the_choice(self, tmp_path, organize):
        # Unpadded spelling of the same episode is not a move.
        job_id, title_id = await _seed(
            tmp_path,
            episode="S01E03",
            state=TitleState.REVIEW,
            job_state=JobState.REVIEW_NEEDED,
            conflict_resolution="overwrite",
        )

        await _make_coord().apply_review_batch(
            job_id, [{"title_id": title_id, "episode_code": "S1E3"}]
        )

        assert _strategy(organize) == "overwrite"


@pytest.mark.unit
class TestMovieEditionChangeDropsTheChoice:
    def test_new_edition_clears_a_recorded_choice(self):
        title = DiscTitle(
            job_id=1, title_index=0, edition="Theatrical", conflict_resolution="rename"
        )

        FinalizationCoordinator._apply_decision_fields(title, None, "Extended")

        assert title.conflict_resolution is None

    def test_same_edition_keeps_it(self):
        title = DiscTitle(
            job_id=1, title_index=0, edition="Theatrical", conflict_resolution="rename"
        )

        FinalizationCoordinator._apply_decision_fields(title, None, "Theatrical")

        assert title.conflict_resolution == "rename"

    def test_movie_skip_is_recorded_not_discarded(self):
        # Only TV maps skip to Discard; a movie "skip" keeps its #685 meaning.
        title = DiscTitle(job_id=1, title_index=0, state=TitleState.REVIEW)

        FinalizationCoordinator._apply_decision_fields(title, None, None, "skip")

        assert title.conflict_resolution == "skip"
        assert title.state == TitleState.REVIEW
