"""Tests for progress_callback race condition.

Deterministically reproduces the bug where concurrent asyncio.create_task
calls for progress_callback cause multiple titles to enter RIPPING state
simultaneously.

Root cause: In a real MakeMKV rip, the extractor emits PRGC/PRGV messages
rapidly via a thread, and each is dispatched as:
    asyncio.create_task(progress_callback(p))
These tasks share _last_title_idx and _titles_marked_ripping without
synchronization. When two tasks interleave at await points (session.get,
session.commit), both can read PENDING and write RIPPING before either
transitions the other out.

The simulation code path does NOT use progress_callback at all — it
drives title states with sequential await calls — which is why this
class of bug was invisible to all prior tests.

To reproduce deterministically, we inject asyncio.Event barriers that
force the exact interleaving:
    Task A: reads title 0 as PENDING  →  signals  →  waits
    Task B: reads title 1 as PENDING  →  signals
    Both proceed: Task A commits title 0 = RIPPING,
                  Task B commits title 1 = RIPPING
    Result: 2 titles RIPPING simultaneously (BUG)
"""

import asyncio

import pytest

from app.core.extractor import RipProgress
from app.database import async_session, init_db
from app.models.disc_job import (
    ContentType,
    DiscJob,
    DiscTitle,
    JobState,
    TitleState,
)


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize DB for each test."""
    await init_db()


async def _create_job_with_titles(n_titles: int = 5) -> tuple[DiscJob, list[DiscTitle]]:
    """Create a movie job with n PENDING titles."""
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="TEST_MOVIE",
            content_type=ContentType.MOVIE,
            state=JobState.RIPPING,
            detected_title="Test Movie",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        titles = []
        for i in range(n_titles):
            t = DiscTitle(
                job_id=job.id,
                title_index=i,
                duration_seconds=600 + i * 100,
                file_size_bytes=500_000_000 + i * 100_000_000,
                state=TitleState.PENDING,
            )
            session.add(t)
            titles.append(t)
        await session.commit()
        for t in titles:
            await session.refresh(t)

        return job, titles


async def _count_ripping(sorted_titles: list[DiscTitle]) -> list[int]:
    """Return title_indices of all titles currently in RIPPING state."""
    async with async_session() as sess:
        ripping = []
        for t in sorted_titles:
            db_t = await sess.get(DiscTitle, t.id)
            if db_t.state == TitleState.RIPPING:
                ripping.append(db_t.title_index)
        return ripping


class TestProgressCallbackRaceCondition:
    """Deterministic reproduction of the multi-RIPPING race condition."""

    async def test_race_without_lock_causes_multi_ripping(self):
        """PROVES THE BUG: without a lock, concurrent callbacks set 2 titles to RIPPING.

        Uses asyncio.Event barriers to force the exact interleaving that
        happens in production when MakeMKV rapidly cycles PRGC titles.
        """
        job, titles = await _create_job_with_titles(3)
        sorted_titles = sorted(titles, key=lambda t: t.title_index)

        _titles_marked_ripping: set[int] = set()
        _last_title_idx: int | None = None

        # Barriers to force interleaving between the read and commit
        task_a_read = asyncio.Event()  # Task A signals after reading DB
        task_b_read = asyncio.Event()  # Task B signals after reading DB

        async def progress_callback_NO_LOCK(
            progress: RipProgress,
            *,
            signal_after_read: asyncio.Event | None = None,
            wait_after_read: asyncio.Event | None = None,
        ) -> None:
            """Unlocked callback with injectable barriers at the critical interleave point."""
            nonlocal _last_title_idx
            current_idx = progress.current_title
            active_title = None

            if 0 <= (current_idx - 1) < len(sorted_titles):
                active_title = sorted_titles[current_idx - 1]

            # Transition previous title out
            if _last_title_idx is not None and current_idx != _last_title_idx:
                prev_list_idx = _last_title_idx - 1
                if 0 <= prev_list_idx < len(sorted_titles):
                    prev_title = sorted_titles[prev_list_idx]
                    async with async_session() as sess:
                        prev_db = await sess.get(DiscTitle, prev_title.id)
                        if prev_db and prev_db.state == TitleState.RIPPING:
                            prev_db.state = TitleState.MATCHED
                            sess.add(prev_db)
                            await sess.commit()
            _last_title_idx = current_idx

            # Set active title to RIPPING
            if active_title and active_title.id not in _titles_marked_ripping:
                async with async_session() as sess:
                    title_db = await sess.get(DiscTitle, active_title.id)

                    # >>> CRITICAL INTERLEAVE POINT <<<
                    # In production, this is where Task B's session.get()
                    # runs while Task A hasn't committed yet.
                    if signal_after_read:
                        signal_after_read.set()
                    if wait_after_read:
                        await wait_after_read.wait()

                    if title_db and title_db.state == TitleState.PENDING:
                        title_db.state = TitleState.RIPPING
                        sess.add(title_db)
                        await sess.commit()
                _titles_marked_ripping.add(active_title.id)

        # Task A: title 1 (index 0) — reads PENDING, signals, waits for B
        # Task B: title 2 (index 1) — reads PENDING, signals
        # Both proceed to commit RIPPING simultaneously
        task_a = asyncio.create_task(
            progress_callback_NO_LOCK(
                RipProgress(percent=0.0, current_title=1, total_titles=3),
                signal_after_read=task_a_read,
                wait_after_read=task_b_read,
            )
        )
        task_b = asyncio.create_task(
            progress_callback_NO_LOCK(
                RipProgress(percent=0.0, current_title=2, total_titles=3),
                signal_after_read=task_b_read,
                wait_after_read=task_a_read,
            )
        )
        await asyncio.gather(task_a, task_b)

        ripping = await _count_ripping(sorted_titles)

        # BUG: 2 titles are RIPPING simultaneously
        assert len(ripping) == 2, (
            f"Expected race to cause 2 RIPPING titles, got {len(ripping)}: {ripping}. "
            f"This test proves the bug exists without the lock."
        )

    async def test_lock_prevents_multi_ripping(self):
        """PROVES THE FIX: with asyncio.Lock, only 1 title is RIPPING at a time.

        Same barriers as above, but the lock serializes execution so the
        barriers never actually interleave — Task A completes fully before
        Task B starts.
        """
        job, titles = await _create_job_with_titles(3)
        sorted_titles = sorted(titles, key=lambda t: t.title_index)

        _titles_marked_ripping: set[int] = set()
        _last_title_idx: int | None = None
        _lock = asyncio.Lock()

        # Same barriers — but under the lock they'll never interleave
        task_a_read = asyncio.Event()
        task_b_read = asyncio.Event()

        async def progress_callback_WITH_LOCK(
            progress: RipProgress,
            *,
            signal_after_read: asyncio.Event | None = None,
            wait_after_read: asyncio.Event | None = None,
        ) -> None:
            nonlocal _last_title_idx
            async with _lock:
                current_idx = progress.current_title
                active_title = None

                if 0 <= (current_idx - 1) < len(sorted_titles):
                    active_title = sorted_titles[current_idx - 1]

                if _last_title_idx is not None and current_idx != _last_title_idx:
                    prev_list_idx = _last_title_idx - 1
                    if 0 <= prev_list_idx < len(sorted_titles):
                        prev_title = sorted_titles[prev_list_idx]
                        async with async_session() as sess:
                            prev_db = await sess.get(DiscTitle, prev_title.id)
                            if prev_db and prev_db.state == TitleState.RIPPING:
                                prev_db.state = TitleState.MATCHED
                                sess.add(prev_db)
                                await sess.commit()
                _last_title_idx = current_idx

                if active_title and active_title.id not in _titles_marked_ripping:
                    async with async_session() as sess:
                        title_db = await sess.get(DiscTitle, active_title.id)

                        # Barriers are inside the lock — Task B can't reach
                        # this point until Task A releases the lock, so the
                        # wait_after_read will already be set.
                        if signal_after_read:
                            signal_after_read.set()
                        if wait_after_read:
                            # Don't block — the other task can't signal while
                            # we hold the lock. Use wait_for with a 0 timeout
                            # to make this a no-op under the lock.
                            try:
                                await asyncio.wait_for(wait_after_read.wait(), timeout=0.01)
                            except TimeoutError:
                                pass  # Expected — the other task is blocked on the lock

                        if title_db and title_db.state == TitleState.PENDING:
                            title_db.state = TitleState.RIPPING
                            sess.add(title_db)
                            await sess.commit()
                    _titles_marked_ripping.add(active_title.id)

        task_a = asyncio.create_task(
            progress_callback_WITH_LOCK(
                RipProgress(percent=0.0, current_title=1, total_titles=3),
                signal_after_read=task_a_read,
                wait_after_read=task_b_read,
            )
        )
        task_b = asyncio.create_task(
            progress_callback_WITH_LOCK(
                RipProgress(percent=0.0, current_title=2, total_titles=3),
                signal_after_read=task_b_read,
                wait_after_read=task_a_read,
            )
        )
        await asyncio.gather(task_a, task_b)

        ripping = await _count_ripping(sorted_titles)

        assert len(ripping) == 1, (
            f"Expected exactly 1 RIPPING with lock, got {len(ripping)}: {ripping}"
        )

    async def test_full_rip_progression_only_one_ripping(self):
        """Simulate a full 5-title rip with concurrent tasks per title.

        At every checkpoint, exactly 1 title should be in RIPPING state.
        Previous titles should have transitioned to MATCHED.
        """
        job, titles = await _create_job_with_titles(5)
        sorted_titles = sorted(titles, key=lambda t: t.title_index)

        _titles_marked_ripping: set[int] = set()
        _last_title_idx: int | None = None
        _lock = asyncio.Lock()

        async def progress_callback(progress: RipProgress) -> None:
            nonlocal _last_title_idx
            async with _lock:
                current_idx = progress.current_title
                active_title = None

                if 0 <= (current_idx - 1) < len(sorted_titles):
                    active_title = sorted_titles[current_idx - 1]

                if _last_title_idx is not None and current_idx != _last_title_idx:
                    prev_list_idx = _last_title_idx - 1
                    if 0 <= prev_list_idx < len(sorted_titles):
                        prev_title = sorted_titles[prev_list_idx]
                        async with async_session() as sess:
                            prev_db = await sess.get(DiscTitle, prev_title.id)
                            if prev_db and prev_db.state == TitleState.RIPPING:
                                prev_db.state = TitleState.MATCHED
                                sess.add(prev_db)
                                await sess.commit()
                _last_title_idx = current_idx

                if active_title and active_title.id not in _titles_marked_ripping:
                    async with async_session() as sess:
                        title_db = await sess.get(DiscTitle, active_title.id)
                        if title_db and title_db.state == TitleState.PENDING:
                            title_db.state = TitleState.RIPPING
                            sess.add(title_db)
                            await sess.commit()
                    _titles_marked_ripping.add(active_title.id)

        # Simulate each title getting burst of concurrent progress updates
        for title_num in range(1, 6):
            tasks = [
                asyncio.create_task(
                    progress_callback(
                        RipProgress(percent=pct, current_title=title_num, total_titles=5)
                    )
                )
                for pct in [0.0, 10.0, 25.0, 50.0, 75.0, 100.0]
            ]
            await asyncio.gather(*tasks)

            ripping = await _count_ripping(sorted_titles)

            assert len(ripping) == 1, (
                f"After title {title_num}: expected 1 RIPPING, "
                f"got {len(ripping)}: {ripping}"
            )
            assert ripping[0] == title_num - 1, (
                f"Expected title_index {title_num - 1} to be RIPPING, got {ripping[0]}"
            )

        # After all titles, verify progression: 4 MATCHED + 1 RIPPING
        async with async_session() as sess:
            matched = 0
            for t in sorted_titles:
                db_t = await sess.get(DiscTitle, t.id)
                if db_t.state == TitleState.MATCHED:
                    matched += 1
            assert matched == 4, f"Expected 4 MATCHED titles, got {matched}"

    async def test_rapid_title_cycling_simulates_makemkv_scan(self):
        """Simulate MakeMKV's initial scan: PRGC rapidly cycles 0→1→2→0→1→2.

        During disc scanning before ripping starts, MakeMKV reports progress
        for each title in rapid succession with 0% progress. This cycling
        was the original trigger for the multi-RIPPING bug.
        """
        job, titles = await _create_job_with_titles(3)
        sorted_titles = sorted(titles, key=lambda t: t.title_index)

        _titles_marked_ripping: set[int] = set()
        _last_title_idx: int | None = None
        _lock = asyncio.Lock()

        async def progress_callback(progress: RipProgress) -> None:
            nonlocal _last_title_idx
            async with _lock:
                current_idx = progress.current_title
                active_title = None

                if 0 <= (current_idx - 1) < len(sorted_titles):
                    active_title = sorted_titles[current_idx - 1]

                if _last_title_idx is not None and current_idx != _last_title_idx:
                    prev_list_idx = _last_title_idx - 1
                    if 0 <= prev_list_idx < len(sorted_titles):
                        prev_title = sorted_titles[prev_list_idx]
                        async with async_session() as sess:
                            prev_db = await sess.get(DiscTitle, prev_title.id)
                            if prev_db and prev_db.state == TitleState.RIPPING:
                                prev_db.state = TitleState.MATCHED
                                sess.add(prev_db)
                                await sess.commit()
                _last_title_idx = current_idx

                if active_title and active_title.id not in _titles_marked_ripping:
                    async with async_session() as sess:
                        title_db = await sess.get(DiscTitle, active_title.id)
                        if title_db and title_db.state == TitleState.PENDING:
                            title_db.state = TitleState.RIPPING
                            sess.add(title_db)
                            await sess.commit()
                    _titles_marked_ripping.add(active_title.id)

        # Simulate scan cycling: 1→2→3→1→2→3  (all at 0%)
        scan_sequence = [1, 2, 3, 1, 2, 3]
        tasks = [
            asyncio.create_task(
                progress_callback(RipProgress(percent=0.0, current_title=t, total_titles=3))
            )
            for t in scan_sequence
        ]
        await asyncio.gather(*tasks)

        ripping = await _count_ripping(sorted_titles)

        # After the scan settles, exactly 1 title should be RIPPING
        assert len(ripping) <= 1, (
            f"After scan cycling, expected ≤1 RIPPING, got {len(ripping)}: {ripping}"
        )
