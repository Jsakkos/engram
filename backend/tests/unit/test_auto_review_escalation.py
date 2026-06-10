"""Unit tests for automatic, escalating deep re-match of needs-review titles.

Complements the conflict escalation: when a single title lands in REVIEW with a
low-confidence / no match (and no same-episode collision), FinalizationCoordinator
deep re-matches it at progressively denser sampling before handing it to manual
review. The audio matcher is stubbed; these tests cover the escalation,
termination, and eligibility logic.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator, _is_rematchable_review
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)


async def _seed_review(
    *,
    content_type=ContentType.TV,
    duration: int = 3000,
    state=TitleState.REVIEW,
    is_extra: bool = False,
    match_details: str | None = None,
) -> int:
    """Seed a TV job with one needs-review title (no collision). Returns job id."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
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
                duration_seconds=duration,
                matched_episode=None,
                match_confidence=0.2,
                state=state,
                is_extra=is_extra,
                match_details=match_details,
            )
        )
        await session.commit()
        return job.id


def _coord_with(rematch_title):
    coord = FinalizationCoordinator(Mock(), Mock())
    coord._rematch_title = rematch_title
    return coord


async def _escalate(coord, job_id):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        result = await coord._maybe_escalate_reviews(session, job, titles)
        return result, job.conflict_status


@pytest.mark.unit
class TestReviewEscalation:
    async def test_first_pass_dispatches_depth_only(self):
        job_id = await _seed_review()
        calls: list[tuple] = []

        async def fake(jid, tid, source_preference=None, num_points=None, min_vote_count=None):
            calls.append((source_preference, num_points, min_vote_count))

        coord = _coord_with(fake)
        result, status = await _escalate(coord, job_id)

        assert result is True
        assert coord._review_passes[job_id] == 25
        assert calls and all(np == 25 for _src, np, _mv in calls)
        # Depth-only: vote gate stays default; engram source forced.
        assert all(mv is None for _src, _np, mv in calls)
        assert all(src == "engram" for src, _np, _mv in calls)
        assert status and "pass 1 of 3" in status

    async def test_escalates_then_exhausts(self):
        job_id = await _seed_review(duration=3000)  # full coverage = 101
        depths: list[int] = []

        async def fake(jid, tid, source_preference=None, num_points=None, min_vote_count=None):
            depths.append(num_points)

        coord = _coord_with(fake)

        for expected in (25, 50, 101):
            result, _status = await _escalate(coord, job_id)
            assert result is True
            assert coord._review_passes[job_id] == expected

        result, status = await _escalate(coord, job_id)
        assert result is False
        # Counter must NOT be popped on exhaustion — see
        # test_exhausted_does_not_re_dispatch_on_recheck for why.
        assert coord._review_passes[job_id] == 101
        assert status is None
        assert {25, 50, 101}.issubset(set(depths))

    async def test_exhausted_does_not_re_dispatch_on_recheck(self):
        """After the ladder exhausts on titles still in REVIEW, a subsequent
        ``check_job_completion`` re-entry must NOT re-fire pass 1 — that's
        an infinite loop. Caught live: titles whose precomputed-cache deps
        are missing match nothing on every pass; if exhaustion pops the
        counter, the next recheck reads last_depth=0 and starts pass 1 again.
        """
        job_id = await _seed_review(duration=3000)  # ladder = [25, 50, 101]
        depths: list[int] = []

        async def fake(jid, tid, source_preference=None, num_points=None, min_vote_count=None):
            depths.append(num_points)

        coord = _coord_with(fake)

        # Walk the full ladder to exhaustion.
        for _ in range(3):
            await _escalate(coord, job_id)
        result, _status = await _escalate(coord, job_id)
        assert result is False  # exhausted

        # Titles are still in REVIEW; the next re-entry must be a no-op.
        depths.clear()
        result, status = await _escalate(coord, job_id)
        assert result is False, "Re-entry on exhausted review escalation must NOT re-fire"
        assert depths == [], f"Expected no re-dispatch on recheck, got depths {depths}"
        assert status is None

    async def test_resolved_title_clears_state(self):
        job_id = await _seed_review()

        async def fake(jid, tid, source_preference=None, num_points=None, min_vote_count=None):
            return None

        coord = _coord_with(fake)
        await _escalate(coord, job_id)
        assert job_id in coord._review_passes

        # Simulate the deep re-match having resolved the title.
        async with _unit_session_factory() as session:
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            titles[0].state = TitleState.MATCHED
            titles[0].matched_episode = "S01E04"
            await session.commit()

        result, status = await _escalate(coord, job_id)
        assert result is False
        assert job_id not in coord._review_passes
        assert status is None

    async def test_no_dispatch_falls_through_without_looping(self):
        job_id = await _seed_review()

        async def fake(jid, tid, source_preference=None, num_points=None, min_vote_count=None):
            raise ValueError("staging file missing")

        coord = _coord_with(fake)
        result, status = await _escalate(coord, job_id)

        assert result is False
        assert job_id not in coord._review_passes
        assert status is None

    async def test_no_review_titles_returns_false(self):
        job_id = await _seed_review(state=TitleState.MATCHED)

        async def fake(*a, **k):
            raise AssertionError("nothing to escalate")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_extra_review_title_not_escalated(self):
        job_id = await _seed_review(
            is_extra=True,
            match_details=json.dumps({"auto_sorted": "extras", "action": "review"}),
        )

        async def fake(*a, **k):
            raise AssertionError("extras must not be re-matched as episodes")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_file_exists_review_not_escalated(self):
        job_id = await _seed_review(
            match_details=json.dumps({"error": "file_exists", "message": "exists"})
        )

        async def fake(*a, **k):
            raise AssertionError("organization conflicts are not a matching problem")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_forced_review_title_not_escalated(self):
        # A title the watchdog force-advanced or the user skipped to REVIEW must
        # not be auto re-matched — that would undo the deliberate "hand to human"
        # decision and risk re-entering a stuck matching state.
        job_id = await _seed_review(
            match_details=json.dumps({"forced_review": True, "reason": "stale timeout"})
        )

        async def fake(*a, **k):
            raise AssertionError("force-advanced/skipped titles must not be re-matched")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_subtitle_failure_review_not_escalated(self):
        job_id = await _seed_review(
            match_details=json.dumps({"error": "subtitle_download_failed", "message": "no refs"})
        )

        async def fake(*a, **k):
            raise AssertionError("no reference subtitles → deeper scan cannot help")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False

    async def test_review_pass_advances_across_no_conflict_reentry(self):
        """A check_job_completion re-entry where conflict-escalate finds nothing
        must NOT wipe review-escalation's pass counter. Otherwise review-escalate
        always re-dispatches at depth 25 and never advances to 50 / full coverage
        — visible as 'one track keeps re-matching forever.'"""
        job_id = await _seed_review(duration=3000)  # full coverage = 101

        depths: list[int] = []

        async def fake_rematch(
            jid, tid, source_preference=None, num_points=None, min_vote_count=None
        ):
            depths.append(num_points)

        async def fake_conflict(*a, **k):
            return {"dispatched": [], "skipped": []}

        coord = _coord_with(fake_rematch)
        coord._rematch_conflict = fake_conflict

        # First review-escalation pass dispatches at depth 25.
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            assert await coord._maybe_escalate_reviews(session, job, titles) is True
            assert coord._review_passes[job_id] == 25

        # Now simulate the next check_job_completion re-entry: conflict-escalate runs
        # first, finds no conflicts (titles are REVIEW, not MATCHED), and bails. That
        # bail path MUST NOT touch the review-escalation pass counter.
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            await coord._maybe_escalate_conflicts(session, job, titles)
            # Review pass counter must survive.
            assert coord._review_passes[job_id] == 25

        # Review-escalate fires again — must now escalate to depth 50, not 25.
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
            assert await coord._maybe_escalate_reviews(session, job, titles) is True
            assert coord._review_passes[job_id] == 50

        assert depths == [25, 50]

    async def test_movie_job_never_escalates(self):
        job_id = await _seed_review(content_type=ContentType.MOVIE)

        async def fake(*a, **k):
            raise AssertionError("movies must not trigger episode re-match")

        coord = _coord_with(fake)
        result, _status = await _escalate(coord, job_id)
        assert result is False
        assert job_id not in coord._review_passes


def _review_title(match_details: dict | None = None, is_extra: bool = False) -> SimpleNamespace:
    """Minimal title-like object for _is_rematchable_review tests."""
    return SimpleNamespace(
        state=TitleState.REVIEW,
        is_extra=is_extra,
        match_details=json.dumps(match_details) if match_details is not None else None,
    )


@pytest.mark.unit
class TestIsRematchableReview:
    """Direct unit tests for _is_rematchable_review — guards the rerip_eligible fix.

    Damaged tracks (incomplete_rip / rip_stalled) must be excluded from
    review-escalation so that their match_details (and the rerip_eligible flag)
    survive intact for the auto re-rip on the next disc insertion.
    """

    def test_incomplete_rip_not_rematchable(self):
        t = _review_title({"error": "incomplete_rip", "rerip_eligible": True})
        assert _is_rematchable_review(t) is False

    def test_rip_stalled_not_rematchable(self):
        t = _review_title({"error": "rip_stalled", "rerip_eligible": True})
        assert _is_rematchable_review(t) is False

    def test_low_confidence_is_rematchable(self):
        """A normal low-confidence REVIEW title must still be escalated — the fix
        must not accidentally break the happy path."""
        t = _review_title({"error": "low_confidence"})
        assert _is_rematchable_review(t) is True

    def test_no_match_details_is_rematchable(self):
        """A REVIEW title with no match_details is a plain no-match → escalate."""
        t = _review_title(None)
        assert _is_rematchable_review(t) is True

    def test_file_exists_still_excluded(self):
        """Pre-existing exclusion must still hold after the set-union change."""
        t = _review_title({"error": "file_exists"})
        assert _is_rematchable_review(t) is False

    def test_subtitle_download_failed_still_excluded(self):
        """Pre-existing exclusion must still hold after the set-union change."""
        t = _review_title({"error": "subtitle_download_failed"})
        assert _is_rematchable_review(t) is False
