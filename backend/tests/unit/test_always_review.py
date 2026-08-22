"""The "review every disc" override: hold each disc for confirmation before organizing.

Without it, a disc whose titles all matched (or were all auto-sorted into Extras)
finalizes and completes untouched — which is how a whole box set can file every
one of its episodes as an extra and report success. With it on, the same disc
parks in REVIEW_NEEDED with the matcher's guesses pre-filled and waits for a human.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.routes import ConfigResponse, ConfigUpdate
from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

FIELD = "always_review"


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)


def _patch_always_review(monkeypatch, enabled: bool):
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(always_review=enabled)),
    )


async def _seed(title_states: list[TitleState], *, as_extras: bool = False) -> int:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SEGMENT_SHOW_S1_D6",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Segment Show",
            detected_season=1,
            staging_path="/tmp/staging/job",
            subtitle_status="completed",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for i, state in enumerate(title_states):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=i,
                    duration_seconds=1340,
                    state=state,
                    matched_episode=(
                        ("extra" if as_extras else f"S01E{i + 1:02d}")
                        if state == TitleState.MATCHED
                        else None
                    ),
                    match_confidence=1.0 if state == TitleState.MATCHED else 0.0,
                    is_extra=as_extras and state == TitleState.MATCHED,
                )
            )
        await session.commit()
        return job.id


def _coord() -> tuple[FinalizationCoordinator, Mock]:
    broadcaster = AsyncMock()
    coord = FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord.finalize_disc_job = AsyncMock()
    return coord, broadcaster


async def _run_completion(coord, job_id: int) -> JobState:
    async with _unit_session_factory() as session:
        await coord.check_job_completion(session, job_id)
        job = await session.get(DiscJob, job_id)
        await session.refresh(job)
        return job.state


@pytest.mark.unit
class TestAlwaysReviewParking:
    async def test_fully_matched_disc_is_held_for_confirmation(self, monkeypatch):
        _patch_always_review(monkeypatch, True)
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED, TitleState.MATCHED])

        state = await _run_completion(coord, job_id)

        assert state == JobState.REVIEW_NEEDED
        coord.finalize_disc_job.assert_not_awaited()

    async def test_disabled_by_default_disc_organizes_as_before(self, monkeypatch):
        _patch_always_review(monkeypatch, False)
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED, TitleState.MATCHED])

        await _run_completion(coord, job_id)

        coord.finalize_disc_job.assert_awaited_once()

    async def test_review_reason_explains_the_hold(self, monkeypatch):
        _patch_always_review(monkeypatch, True)
        coord, _ = _coord()
        job_id = await _seed([TitleState.MATCHED])

        await _run_completion(coord, job_id)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert "Manual review" in (job.review_reason or "")

    async def test_a_disc_with_nothing_left_to_organize_is_not_parked(self, monkeypatch):
        """All titles already COMPLETED (nothing to move, nothing to decide).
        Parking here would strand a job the review page cannot finish."""
        _patch_always_review(monkeypatch, True)
        coord, _ = _coord()
        job_id = await _seed([TitleState.COMPLETED, TitleState.COMPLETED])

        state = await _run_completion(coord, job_id)

        assert state == JobState.COMPLETED

    async def test_all_titles_skipped_still_completes(self, monkeypatch):
        _patch_always_review(monkeypatch, True)
        coord, _ = _coord()
        job_id = await _seed([TitleState.SKIPPED, TitleState.SKIPPED])

        state = await _run_completion(coord, job_id)

        assert state == JobState.COMPLETED


@pytest.mark.unit
class TestAlwaysReviewConfigField:
    """A new AppConfig field must reach ConfigUpdate (PUT accepts it) and
    ConfigResponse (GET returns it), or the toggle silently does nothing."""

    def test_defaults_off(self):
        assert getattr(AppConfig(), FIELD) is False

    def test_config_update_accepts_and_carries_the_field(self):
        assert ConfigUpdate(**{FIELD: True}).model_dump()[FIELD] is True
        assert ConfigUpdate().model_dump()[FIELD] is None

    def test_config_response_exposes_the_field(self):
        assert FIELD in ConfigResponse.model_fields
