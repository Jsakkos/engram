"""Unit tests for staging-path ownership classification and unit keys.

A staging path is OWNED by an in-flight job and merely RECORDED by a finished
one. These tests pin that boundary and the stability of the opaque unit key the
API round-trips through the client.
"""

import importlib

from app.models import DiscJob, JobState
from app.services.import_guard import (
    ImportBlock,
    classify_staging_path,
    unit_key_for,
)

# app.services.job_manager is name-shadowed by the singleton in the package
# __init__, so resolve the real module the way conftest does. Its async_session
# is the one the autouse unit fixture patches to the in-memory StaticPool DB.
jm_mod = importlib.import_module("app.services.job_manager")


async def _insert_job(staging_path: str, state: JobState) -> int:
    async with jm_mod.async_session() as session:
        job = DiscJob(
            drive_id="import",
            volume_label="SEINFELD",
            staging_path=staging_path,
            state=state,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


class TestUnitKey:
    def test_is_stable_for_the_same_path(self):
        assert unit_key_for(r"X:\rips\Seinfeld") == unit_key_for(r"X:\rips\Seinfeld")

    def test_differs_between_paths(self):
        assert unit_key_for(r"X:\rips\Seinfeld") != unit_key_for(r"X:\rips\Deadwood")

    def test_does_not_leak_the_path(self):
        """The key is opaque: a client must not be able to read a path out of it."""
        key = unit_key_for(r"X:\rips\Seinfeld")
        assert "Seinfeld" not in key
        assert len(key) == 16

    def test_ignores_a_trailing_separator(self):
        """The DB stores str(Path(...)), so the key must not fork on path spelling."""
        assert unit_key_for("/media/rips/Show/") == unit_key_for("/media/rips/Show")


class TestClassifyStagingPath:
    async def test_unknown_path_is_unblocked(self):
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, r"X:\rips\Nothing")
        assert block is None
        assert ids == []

    async def test_failed_job_does_not_block(self):
        path = r"X:\rips\Seinfeld"
        await _insert_job(path, JobState.FAILED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is None
        assert ids == []

    async def test_completed_job_soft_blocks(self):
        path = r"X:\rips\Sopranos"
        jid = await _insert_job(path, JobState.COMPLETED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.ALREADY_IMPORTED
        assert ids == [jid]

    async def test_in_flight_job_hard_blocks(self):
        path = r"X:\rips\True Detective"
        jid = await _insert_job(path, JobState.IDENTIFYING)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [jid]

    async def test_review_needed_hard_blocks(self):
        """Unlike the disc path, review still owns the files: they sit on disk."""
        path = r"X:\rips\Deadwood"
        jid = await _insert_job(path, JobState.REVIEW_NEEDED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [jid]

    async def test_in_flight_wins_over_completed(self):
        """A path with both a finished and a live job is owned by the live one."""
        path = r"X:\rips\Mixed"
        await _insert_job(path, JobState.COMPLETED)
        live = await _insert_job(path, JobState.RIPPING)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == [live]

    async def test_a_job_at_another_path_does_not_block(self):
        """Blocks are scoped to one path: a job elsewhere must not leak into this one."""
        await _insert_job(r"X:\rips\Sopranos", JobState.COMPLETED)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, r"X:\rips\Deadwood")
        assert block is None
        assert ids == []

    async def test_two_in_flight_blockers_are_returned_in_ascending_order(self):
        path = r"X:\rips\Multi"
        first = await _insert_job(path, JobState.IDENTIFYING)
        second = await _insert_job(path, JobState.RIPPING)
        async with jm_mod.async_session() as session:
            block, ids = await classify_staging_path(session, path)
        assert block is ImportBlock.IN_FLIGHT
        assert ids == sorted([first, second])
