"""Unit tests for automatic, escalating conflict re-match.

When two titles match the same episode, FinalizationCoordinator deep re-matches
the contested titles at progressively denser sampling (depth-only — the vote
gate stays at its default) before falling back to manual review. The audio
matcher is stubbed here; these tests cover the escalation/termination logic and
the pure helpers that drive it.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.finalization_coordinator import (
    FinalizationCoordinator,
    _conflict_scan_ladder,
    _detect_conflicts,
    _full_coverage_points,
    _normalize_episode_code,
)
from tests.unit.conftest import _unit_session_factory


def _matched(episode: str, duration: int = 1380):
    return SimpleNamespace(
        state=TitleState.MATCHED, matched_episode=episode, duration_seconds=duration
    )


@pytest.mark.unit
class TestConflictHelpers:
    def test_normalize_pads_unpadded_codes(self):
        assert _normalize_episode_code("S1E3") == "S01E03"
        assert _normalize_episode_code("S01E03") == "S01E03"
        assert _normalize_episode_code("s1e14") == "S01E14"
        assert _normalize_episode_code(None) == ""

    def test_detect_conflicts_collapses_padded_and_unpadded(self):
        titles = [_matched("S01E03"), _matched("S1E3"), _matched("S01E07")]
        conflicts = _detect_conflicts(titles)
        assert list(conflicts) == ["S01E03"]
        assert len(conflicts["S01E03"]) == 2

    def test_detect_conflicts_ignores_non_matched_titles(self):
        titles = [
            _matched("S01E03"),
            SimpleNamespace(
                state=TitleState.REVIEW, matched_episode="S01E03", duration_seconds=1380
            ),
        ]
        assert _detect_conflicts(titles) == {}

    def test_full_coverage_points_from_duration(self):
        # 3000s / 30s + 1 = 101 chunks to cover the whole track.
        assert _full_coverage_points([_matched("S01E03", duration=3000)]) == 101

    def test_full_coverage_points_unknown_duration_goes_max(self):
        assert _full_coverage_points([_matched("S01E03", duration=0)]) == 200

    def test_ladder_collapses_for_short_episodes(self):
        # 23-min episode: full coverage (47) < the 50 tier, so the ladder is
        # capped and de-duplicated to two strictly-increasing tiers.
        assert _conflict_scan_ladder([_matched("S01E03", duration=1380)]) == [25, 47]

    def test_ladder_is_three_tier_for_long_episodes(self):
        assert _conflict_scan_ladder([_matched("S01E03", duration=3000)]) == [25, 50, 101]


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)


async def _seed_conflict(duration: int = 3000) -> int:
    """Seed a TV job with two titles colliding on S01E05. Returns job id."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=1,
            staging_path="/tmp/staging/job",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx, ep in ((0, "S01E05"), (1, "S01E05"), (2, "S01E02")):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    duration_seconds=duration,
                    matched_episode=ep,
                    match_confidence=0.6,
                    state=TitleState.MATCHED,
                )
            )
        await session.commit()
        return job.id


def _coord_with(rematch):
    coord = FinalizationCoordinator(Mock(), Mock())
    coord._rematch_conflict = rematch
    return coord


async def _escalate(coord, job_id):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        result = await coord._maybe_escalate_conflicts(session, job, titles)
        return result, job.conflict_status


@pytest.mark.unit
class TestEscalation:
    async def test_first_pass_dispatches_depth_only(self):
        job_id = await _seed_conflict()
        calls: list[tuple] = []

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            calls.append((num_points, min_vote_count))
            return {"dispatched": [1], "skipped": []}

        coord = _coord_with(fake)
        result, status = await _escalate(coord, job_id)

        assert result is True
        assert coord._conflict_passes[job_id] == 25
        assert calls and all(np == 25 for np, _mv in calls)
        # Depth-only: the vote gate is never raised on the auto path.
        assert all(mv is None for _np, mv in calls)
        assert status and "pass 1 of 3" in status

    async def test_escalates_25_50_full_then_exhausts(self):
        job_id = await _seed_conflict(duration=3000)  # full coverage = 101
        depths: list[int] = []

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            depths.append(num_points)
            return {"dispatched": [1], "skipped": []}

        coord = _coord_with(fake)

        for expected in (25, 50, 101):
            result, _status = await _escalate(coord, job_id)
            assert result is True
            assert coord._conflict_passes[job_id] == expected

        # Ladder exhausted (full coverage reached): hand back to the review path.
        result, status = await _escalate(coord, job_id)
        assert result is False
        assert job_id not in coord._conflict_passes
        assert status is None
        assert {25, 50, 101}.issubset(set(depths))

    async def test_resolved_conflict_clears_state(self):
        job_id = await _seed_conflict()

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            return {"dispatched": [1], "skipped": []}

        coord = _coord_with(fake)
        await _escalate(coord, job_id)
        assert job_id in coord._conflict_passes

        # Simulate the re-match having broken the tie.
        async with _unit_session_factory() as session:
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            titles[1].matched_episode = "S01E06"
            await session.commit()

        result, status = await _escalate(coord, job_id)
        assert result is False
        assert job_id not in coord._conflict_passes
        assert status is None

    async def test_no_dispatch_falls_through_without_looping(self):
        job_id = await _seed_conflict()

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            # All contested files missing from staging.
            return {"dispatched": [], "skipped": [{"title_id": 1, "reason": "missing"}]}

        coord = _coord_with(fake)
        result, status = await _escalate(coord, job_id)

        assert result is False
        assert job_id not in coord._conflict_passes  # no progress recorded → no loop
        assert status is None

    async def test_no_conflict_returns_false(self):
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="SHOW",
                content_type=ContentType.TV,
                state=JobState.MATCHING,
                staging_path="/tmp/s",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=0,
                    duration_seconds=1380,
                    matched_episode="S01E01",
                    state=TitleState.MATCHED,
                )
            )
            await session.commit()
            job_id = job.id

        async def fake(*a, **k):
            raise AssertionError("should not re-match without a conflict")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_movie_job_never_escalates(self):
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="INCEPTION_2010",
                content_type=ContentType.MOVIE,
                state=JobState.MATCHING,
                staging_path="/tmp/s",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            for idx in (0, 1):
                session.add(
                    DiscTitle(
                        job_id=job.id,
                        title_index=idx,
                        duration_seconds=8400,
                        matched_episode="Inception",
                        state=TitleState.MATCHED,
                    )
                )
            await session.commit()
            job_id = job.id

        async def fake(*a, **k):
            raise AssertionError("movies must not trigger episode re-match")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False
        assert job_id not in coord._conflict_passes

    async def test_terminal_hook_clears_db_conflict_status(self, monkeypatch):
        # The hook opens its own session; point it at the test DB.
        monkeypatch.setattr(
            "app.services.finalization_coordinator.async_session", _unit_session_factory
        )
        job_id = await _seed_conflict()
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            job.conflict_status = "Resolving episode conflicts — pass 1 of 3"
            await session.commit()

        coord = _coord_with(None)
        coord._conflict_passes[job_id] = 25

        await coord.on_terminal_clear_conflicts(job_id, JobState.FAILED)

        assert job_id not in coord._conflict_passes
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.conflict_status is None

    async def test_reset_conflict_passes(self):
        coord = _coord_with(None)
        coord._conflict_passes[42] = 50
        coord.reset_conflict_passes(42)
        assert 42 not in coord._conflict_passes
