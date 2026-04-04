"""Unit tests for the StagingWatcher.

Tests directory detection, debouncing, stability threshold, and callback firing.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.staging_watcher import StagingWatcher


@pytest.fixture
def staging_dir(tmp_path):
    """Create a staging directory."""
    d = tmp_path / "staging"
    d.mkdir()
    return d


@pytest.fixture
def watcher(staging_dir):
    """Create a StagingWatcher instance (not started)."""
    return StagingWatcher(str(staging_dir))


class TestStagingWatcherDetection:
    """Tests for directory detection and scanning."""

    async def test_empty_staging_dir_no_callback(self, watcher, staging_dir):
        """An empty staging directory should not fire any callbacks."""
        callback = AsyncMock()
        watcher._async_callback = callback

        await watcher._check_staging()
        callback.assert_not_called()

    async def test_dir_without_mkv_files_ignored(self, watcher, staging_dir):
        """Subdirectories with no MKV files should be ignored."""
        sub = staging_dir / "some_disc"
        sub.mkdir()
        (sub / "readme.txt").write_text("not a video")

        callback = AsyncMock()
        watcher._async_callback = callback

        # Run multiple polls to exceed stability threshold
        for _ in range(5):
            await watcher._check_staging()

        callback.assert_not_called()

    async def test_job_prefix_dirs_skipped(self, watcher, staging_dir):
        """Directories starting with job_ should be skipped (managed by ripping pipeline)."""
        sub = staging_dir / "job_20260404_120000"
        sub.mkdir()
        (sub / "title_t00.mkv").write_bytes(b"\x00" * 1024)

        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(5):
            await watcher._check_staging()

        callback.assert_not_called()

    async def test_nonexistent_staging_dir_no_error(self, tmp_path):
        """A nonexistent staging path should not raise errors."""
        watcher = StagingWatcher(str(tmp_path / "nonexistent"))
        callback = AsyncMock()
        watcher._async_callback = callback

        await watcher._check_staging()  # Should not raise
        callback.assert_not_called()


class TestStagingWatcherStability:
    """Tests for the stability debouncing mechanism."""

    async def test_fires_after_stability_threshold(self, watcher, staging_dir):
        """Callback should fire after STABILITY_THRESHOLD consecutive stable polls."""
        sub = staging_dir / "MY_SHOW_S1D1"
        sub.mkdir()
        (sub / "title_t00.mkv").write_bytes(b"\x00" * 1024)
        (sub / "title_t01.mkv").write_bytes(b"\x00" * 2048)

        callback = AsyncMock()
        watcher._async_callback = callback

        # Poll 1: directory discovered, stable_polls=0
        await watcher._check_staging()
        callback.assert_not_called()

        # Poll 2: same state, stable_polls=1
        await watcher._check_staging()
        callback.assert_not_called()

        # Poll 3: same state, stable_polls=2 → fires
        await watcher._check_staging()
        callback.assert_called_once()

        # Verify callback args
        args = callback.call_args[0]
        assert args[0] == "staging_ready"
        assert args[1] == str(sub)
        assert args[2] == "MY_SHOW_S1D1"  # Label derived from dir name

    async def test_changing_file_size_resets_stability(self, watcher, staging_dir):
        """If file sizes change between polls, stability counter should reset."""
        sub = staging_dir / "COPYING_DISC"
        sub.mkdir()
        mkv_file = sub / "title_t00.mkv"
        mkv_file.write_bytes(b"\x00" * 1024)

        callback = AsyncMock()
        watcher._async_callback = callback

        # Poll 1: discovered
        await watcher._check_staging()

        # Poll 2: same size, stable_polls=1
        await watcher._check_staging()

        # File grows (still copying)
        mkv_file.write_bytes(b"\x00" * 2048)

        # Poll 3: size changed, stability reset to 0
        await watcher._check_staging()
        callback.assert_not_called()

        # Poll 4: same size, stable_polls=1
        await watcher._check_staging()
        callback.assert_not_called()

        # Poll 5: same size, stable_polls=2 → fires
        await watcher._check_staging()
        callback.assert_called_once()

    async def test_adding_new_file_resets_stability(self, watcher, staging_dir):
        """Adding a new MKV file should reset the stability counter."""
        sub = staging_dir / "DISC"
        sub.mkdir()
        (sub / "title_t00.mkv").write_bytes(b"\x00" * 1024)

        callback = AsyncMock()
        watcher._async_callback = callback

        # Polls 1-2
        await watcher._check_staging()
        await watcher._check_staging()

        # Add another file
        (sub / "title_t01.mkv").write_bytes(b"\x00" * 1024)

        # Poll 3: file count changed, stability reset
        await watcher._check_staging()
        callback.assert_not_called()

        # Need 2 more stable polls
        await watcher._check_staging()
        callback.assert_not_called()
        await watcher._check_staging()
        callback.assert_called_once()


class TestStagingWatcherProcessedTracking:
    """Tests for preventing re-processing of already imported directories."""

    async def test_processed_dir_not_retriggered(self, watcher, staging_dir):
        """Once a directory is processed, it should not trigger again."""
        sub = staging_dir / "DISC"
        sub.mkdir()
        (sub / "title.mkv").write_bytes(b"\x00" * 1024)

        callback = AsyncMock()
        watcher._async_callback = callback

        # Reach stability and fire
        for _ in range(3):
            await watcher._check_staging()

        assert callback.call_count == 1

        # Additional polls should not fire again
        for _ in range(5):
            await watcher._check_staging()

        assert callback.call_count == 1

    async def test_multiple_directories_independent(self, watcher, staging_dir):
        """Multiple directories should be tracked independently."""
        sub1 = staging_dir / "DISC_1"
        sub1.mkdir()
        (sub1 / "title.mkv").write_bytes(b"\x00" * 1024)

        callback = AsyncMock()
        watcher._async_callback = callback

        # Polls 1-3: DISC_1 fires
        for _ in range(3):
            await watcher._check_staging()
        assert callback.call_count == 1

        # Add second directory
        sub2 = staging_dir / "DISC_2"
        sub2.mkdir()
        (sub2 / "movie.mkv").write_bytes(b"\x00" * 2048)

        # Polls 4-6: DISC_2 fires
        for _ in range(3):
            await watcher._check_staging()
        assert callback.call_count == 2

        # Verify both callbacks had correct paths
        calls = callback.call_args_list
        assert calls[0][0][1] == str(sub1)
        assert calls[1][0][1] == str(sub2)


class TestStagingWatcherLifecycle:
    """Tests for start/stop lifecycle."""

    async def test_start_stop(self, staging_dir):
        """Watcher should start and stop cleanly."""
        watcher = StagingWatcher(str(staging_dir))
        loop = asyncio.get_event_loop()
        callback = AsyncMock()
        watcher.set_async_callback(callback, loop)

        watcher.start()
        assert watcher._running is True
        assert watcher._task is not None

        watcher.stop()
        assert watcher._running is False
        assert watcher._task is None

    async def test_double_start_idempotent(self, staging_dir):
        """Calling start() twice should be safe."""
        watcher = StagingWatcher(str(staging_dir))
        loop = asyncio.get_event_loop()
        watcher.set_async_callback(AsyncMock(), loop)

        watcher.start()
        first_task = watcher._task
        watcher.start()  # Should be no-op
        assert watcher._task is first_task

        watcher.stop()

    async def test_double_stop_idempotent(self, staging_dir):
        """Calling stop() twice should be safe."""
        watcher = StagingWatcher(str(staging_dir))
        loop = asyncio.get_event_loop()
        watcher.set_async_callback(AsyncMock(), loop)

        watcher.start()
        watcher.stop()
        watcher.stop()  # Should be no-op, no error
        assert watcher._running is False
