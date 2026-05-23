"""Tests for MatchingCoordinator per-job cache lifecycle (leak prevention)."""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.matching_coordinator import MatchingCoordinator


def _coordinator() -> MatchingCoordinator:
    return MatchingCoordinator(MagicMock(), MagicMock())


class TestClearJobCaches:
    async def test_clears_all_per_job_state_and_cancels_subtitle_task(self):
        mc = _coordinator()
        job_id = 5
        mc._episode_runtimes[job_id] = [1, 2]
        mc._discdb_mappings[job_id] = ["mapping"]
        mc._subtitle_ready[job_id] = asyncio.Event()

        async def _never():
            await asyncio.sleep(60)

        task = asyncio.create_task(_never())
        mc._subtitle_tasks[job_id] = task

        await mc.clear_job_caches(job_id, None)

        assert job_id not in mc._episode_runtimes
        assert job_id not in mc._discdb_mappings
        assert job_id not in mc._subtitle_ready
        assert job_id not in mc._subtitle_tasks

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_unknown_job_is_safe(self):
        mc = _coordinator()
        await mc.clear_job_caches(999, None)  # must not raise

    async def test_completed_subtitle_task_not_recancelled(self):
        mc = _coordinator()
        job_id = 7

        async def _done():
            return None

        task = asyncio.create_task(_done())
        await task
        mc._subtitle_tasks[job_id] = task

        await mc.clear_job_caches(job_id, None)
        assert job_id not in mc._subtitle_tasks
        assert not task.cancelled()
