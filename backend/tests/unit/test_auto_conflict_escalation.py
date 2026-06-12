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
from app.matcher.episode_identification import snap_to_lattice_level
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.finalization_coordinator import (
    _CONFLICT_FIXED_DEPTHS,
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

    def test_detect_conflicts_includes_review_titles_with_borderline_episode(self):
        """A borderline REVIEW title colliding with a confidently MATCHED title
        on the same episode IS a conflict — auto-resolution needs to break the
        tie together, not leave the confident one riding alongside the borderline
        one."""
        titles = [
            _matched("S01E03"),
            SimpleNamespace(
                state=TitleState.REVIEW,
                matched_episode="S01E03",
                duration_seconds=1380,
                is_extra=False,
                match_details=None,
            ),
        ]
        conflicts = _detect_conflicts(titles)
        assert list(conflicts) == ["S01E03"]
        assert len(conflicts["S01E03"]) == 2

    def test_detect_conflicts_excludes_extras_and_non_rematchable_reviews(self):
        """Extras and titles parked in REVIEW for non-matching reasons (file_exists,
        forced_review, etc.) don't count as conflict candidates even with a
        matched_episode set."""
        import json

        titles = [
            _matched("S01E03"),
            SimpleNamespace(
                state=TitleState.REVIEW,
                matched_episode="S01E03",
                duration_seconds=1380,
                is_extra=True,
                match_details=None,
            ),
            SimpleNamespace(
                state=TitleState.REVIEW,
                matched_episode="S01E03",
                duration_seconds=1380,
                is_extra=False,
                match_details=json.dumps({"forced_review": True}),
            ),
        ]
        assert _detect_conflicts(titles) == {}

    def test_full_coverage_points_from_duration(self):
        # 3000s / 30s + 1 = 101 chunks to cover the whole track.
        assert _full_coverage_points([_matched("S01E03", duration=3000)]) == 101

    def test_full_coverage_points_unknown_duration_goes_max(self):
        assert _full_coverage_points([_matched("S01E03", duration=0)]) == 200

    def test_ladder_collapses_for_short_episodes(self):
        # 23-min episode: full coverage (47) floors to lattice level 37, which
        # swallows both fixed tiers → single-pass ladder.
        assert _conflict_scan_ladder([_matched("S01E03", duration=1380)]) == [37]

    def test_ladder_is_two_tier_for_typical_long_episodes(self):
        # 50-min track: full coverage (101) floors to 73 — the second fixed
        # tier IS the cost ceiling, so there is no third pass.
        assert _conflict_scan_ladder([_matched("S01E03", duration=3000)]) == [37, 73]

    def test_ladder_is_three_tier_for_very_long_tracks(self):
        # full coverage (201) caps at _MAX_SCAN_POINTS (200), floors to 145.
        assert _conflict_scan_ladder([_matched("S01E03", duration=6000)]) == [37, 73, 145]


@pytest.mark.unit
class TestLadderLatticeAlignment:
    """The realized ladder — what ``canonical_scan_points`` actually scans for
    each requested depth — must equal the requested ladder: every tier a lattice
    level (a fixed point of the snap), strictly increasing with no duplicate
    effective depths, and the final tier never above the full-coverage point
    count (cost ceiling). Pre-lattice depths like 25/50 silently snapped UP,
    collapsing adjacent tiers onto one grid and overshooting full coverage."""

    @pytest.mark.parametrize(
        "duration",
        [870, 2520, 7200, 0],
        ids=["short-full~30", "typical-42min", "long-full>200", "unknown-duration"],
    )
    def test_realized_ladder_is_lattice_true(self, duration):
        titles = [_matched("S01E03", duration=duration)]
        ladder = _conflict_scan_ladder(titles)
        assert ladder
        # Lattice-true: each tier is a fixed point of the snap, so the realized
        # ladder is identical and no two tiers can collapse onto one grid.
        assert [snap_to_lattice_level(d) for d in ladder] == ladder
        # Strictly increasing — no duplicate or decreasing effective depths.
        assert all(a < b for a, b in zip(ladder, ladder[1:], strict=False))
        # Cost ceiling: the final tier never overshoots full coverage.
        # Exception: sub-5-min tracks whose full-coverage point (< 10) falls
        # below the base lattice level — floor_to_lattice_level clamps to 10,
        # so ladder[-1] can be 10 while _full_coverage_points returns 9.
        # That edge case is covered separately by test_tiny_duration_ladder_base_clamp.
        assert ladder[-1] <= _full_coverage_points(titles)

    def test_tiny_duration_ladder_base_clamp(self):
        """A sub-5-min track (duration=240 s) has full_coverage_points = 9, which
        is below the base lattice level (10).  floor_to_lattice_level clamps to 10,
        so the ladder is [10] even though 10 > full_coverage_points (9).  This is
        the documented exception to the cost-ceiling property: the matcher can't
        scan shallower than the base level, and canonical_scan_points deduplicates
        the colliding chunk positions on such a short file anyway."""
        titles = [_matched("S01E03", duration=240)]
        assert _full_coverage_points(titles) == 9
        ladder = _conflict_scan_ladder(titles)
        assert ladder == [10]
        # Ladder is still lattice-true and strictly non-empty.
        assert [snap_to_lattice_level(d) for d in ladder] == ladder

    def test_short_episode_ladder_shape(self):
        # full = ceil(870/30) + 1 = 30 → floor 19; both fixed tiers exceed the
        # ceiling and drop out.
        assert _conflict_scan_ladder([_matched("S01E03", duration=870)]) == [19]

    def test_depth_constants_are_lattice_levels(self):
        """One source of truth: deep-rematch and ladder depth constants must be
        fixed points of the lattice snap, or requested != realized depth."""
        from app.services.matching_coordinator import STRICT_SCAN_POINTS

        for depth in (*_CONFLICT_FIXED_DEPTHS, STRICT_SCAN_POINTS):
            assert snap_to_lattice_level(depth) == depth


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
        assert coord._conflict_passes[job_id] == 37
        assert calls and all(np == 37 for np, _mv in calls)
        # Depth-only: the vote gate is never raised on the auto path.
        assert all(mv is None for _np, mv in calls)
        assert status and "pass 1 of 2" in status

    async def test_escalates_37_73_145_then_exhausts(self):
        # full coverage = 201, capped at 200, floored to lattice level 145.
        job_id = await _seed_conflict(duration=6000)
        depths: list[int] = []

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            depths.append(num_points)
            return {"dispatched": [1], "skipped": []}

        coord = _coord_with(fake)

        for expected in (37, 73, 145):
            result, _status = await _escalate(coord, job_id)
            assert result is True
            assert coord._conflict_passes[job_id] == expected

        # Ladder exhausted (full coverage reached): hand back to the review path.
        result, status = await _escalate(coord, job_id)
        assert result is False
        # Counter stays at last_depth so a recheck (e.g. unrelated title
        # finishing) sees "exhausted" again and doesn't re-fire pass 1.
        assert coord._conflict_passes[job_id] == 145
        assert status is None
        assert {37, 73, 145}.issubset(set(depths))

    async def test_exhausted_does_not_re_dispatch_on_recheck(self):
        """After exhaustion, a re-entry from check_job_completion must NOT
        re-fire pass 1 — that loops forever on conflicts that depth alone
        can't break."""
        job_id = await _seed_conflict(duration=6000)  # ladder = [37, 73, 145]
        depths: list[int] = []

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            depths.append(num_points)
            return {"dispatched": [1], "skipped": []}

        coord = _coord_with(fake)

        for _ in range(3):
            await _escalate(coord, job_id)
        result, _status = await _escalate(coord, job_id)
        assert result is False  # exhausted

        depths.clear()
        result, status = await _escalate(coord, job_id)
        assert result is False, "Exhausted conflict-escalation must not re-fire on recheck"
        assert depths == [], f"Expected no re-dispatch, got depths {depths}"
        assert status is None

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

    async def test_review_title_with_episode_counts_as_conflict(self):
        """A title in REVIEW with a (low-confidence) matched_episode that collides
        with a confidently MATCHED title on the same episode must count as a conflict
        — otherwise the borderline guess silently rides alongside the confident one
        and the user sees an unresolved collision in the UI (only the borderline
        title gets re-matched via review-escalation, not the pair together)."""
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
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=0,
                    duration_seconds=2700,
                    matched_episode="S01E05",
                    match_confidence=0.85,
                    state=TitleState.MATCHED,
                )
            )
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=1,
                    duration_seconds=2700,
                    matched_episode="S01E05",
                    match_confidence=0.17,
                    state=TitleState.REVIEW,
                )
            )
            await session.commit()
            job_id = job.id

        calls: list[tuple] = []

        async def fake(jid, ep, num_points=None, min_vote_count=None):
            calls.append((ep, num_points))
            return {"dispatched": [1, 2], "skipped": []}

        coord = _coord_with(fake)
        result, status = await _escalate(coord, job_id)
        assert result is True
        assert calls and calls[0][0] == "S01E05"
        assert status and "pass 1" in status

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
        coord._conflict_passes[job_id] = 37

        await coord.on_terminal_clear_conflicts(job_id, JobState.FAILED)

        assert job_id not in coord._conflict_passes
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.conflict_status is None

    async def test_rerun_matching_clears_db_note_from_matching(self, monkeypatch):
        """rerun_matching must clear the persisted note even from MATCHING, not
        just REVIEW_NEEDED — "rerun starts over" applies to the DB column too."""
        from app.services.job_manager import job_manager

        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="SHOW",
                content_type=ContentType.TV,
                state=JobState.MATCHING,
                conflict_status="Resolving episode conflicts — pass 2 of 3",
                staging_path="/tmp/s",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(job_manager, "_rerun_matching", _noop)
        monkeypatch.setattr(job_manager._matching, "restart_subtitle_download", _noop)

        await job_manager.rerun_matching(job_id)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.conflict_status is None

    async def test_reset_conflict_passes(self):
        coord = _coord_with(None)
        coord._conflict_passes[42] = 73
        coord.reset_conflict_passes(42)
        assert 42 not in coord._conflict_passes
