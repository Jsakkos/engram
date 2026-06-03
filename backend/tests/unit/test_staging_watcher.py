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


class TestImportWatcherStructureDetection:
    """Tests for ARM output structure detection in _scan_import_dir."""

    async def test_pattern_a_per_disc_subfolders(self, tmp_path):
        """Direct subdirectory with MKVs → one import unit per subdir."""
        watch_root = tmp_path / "arm_output"
        watch_root.mkdir()
        disc1 = watch_root / "THE_OFFICE_S1D1"
        disc1.mkdir()
        (disc1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        (disc1 / "title_t02.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 1
        path, mkv_count, total_size, meta = units[0]
        assert path == disc1
        assert mkv_count == 2
        assert total_size == 3072
        assert meta["structure"] == "disc_folder"
        assert meta["show_name"] is None
        assert meta["season"] is None

    async def test_pattern_b_show_organised(self, tmp_path):
        """Show dir with Season subdirs → one job unit per season."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        show = watch_root / "The Office"
        show.mkdir()
        s1 = show / "Season 1"
        s1.mkdir()
        (s1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        s2 = show / "Season 2"
        s2.mkdir()
        (s2 / "title_t01.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 2
        seasons = {meta["season"]: (path, meta) for path, _, _, meta in units}
        assert 1 in seasons and 2 in seasons
        assert seasons[1][1]["show_name"] == "The Office"
        assert seasons[1][1]["structure"] == "show_organised"
        assert seasons[1][0] == s1
        assert seasons[2][0] == s2

    async def test_pattern_c_flat(self, tmp_path):
        """MKVs directly in root → single job unit for whole directory."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        (watch_root / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        (watch_root / "title_t02.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 1
        path, mkv_count, total_size, meta = units[0]
        assert path == watch_root
        assert mkv_count == 2
        assert total_size == 3072
        assert meta["structure"] == "flat"

    async def test_mixed_patterns_in_root(self, tmp_path):
        """Root can contain both per-disc subfolders and show-organised subdirs."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "BOB_S1D1"
        disc.mkdir()
        (disc / "title_t01.mkv").write_bytes(b"\x00" * 1024)
        show = watch_root / "The Wire"
        show.mkdir()
        s1 = show / "Season 1"
        s1.mkdir()
        (s1 / "title_t01.mkv").write_bytes(b"\x00" * 1024)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert len(units) == 2
        structures = {meta["structure"] for _, _, _, meta in units}
        assert structures == {"disc_folder", "show_organised"}

    async def test_empty_subdir_ignored(self, tmp_path):
        """Subdirectories with no MKV files are not returned."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        empty = watch_root / "EMPTY_DIR"
        empty.mkdir()

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(watch_root))
        units = watcher._scan_import_dir(watch_root)

        assert units == []

    async def test_destination_mode_in_metadata(self, tmp_path):
        """destination_mode from constructor appears in metadata."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "DISC1"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(
            "/tmp/staging",
            import_watch_path=str(watch_root),
            import_destination_mode="in_place",
        )
        units = watcher._scan_import_dir(watch_root)

        assert units[0][3]["destination_mode"] == "in_place"

    async def test_watch_root_is_show_with_season_subdirs(self, tmp_path):
        """Watch root pointed directly at a show folder with Season NN subdirs.

        User layout: import_watch_path = .../The Expanse, containing Season 01/, Season 02/.
        Each season folder should yield a show_organised unit whose show_name is the
        watch-root folder name and whose season is parsed from the folder name.
        """
        show_root = tmp_path / "The Expanse"
        show_root.mkdir()
        s1 = show_root / "Season 01"
        s1.mkdir()
        (s1 / "title_t00.mkv").write_bytes(b"\x00" * 1024)
        s2 = show_root / "Season 02"
        s2.mkdir()
        (s2 / "title_t00.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(show_root))
        units = watcher._scan_import_dir(show_root)

        assert len(units) == 2
        seasons = {meta["season"]: (path, meta) for path, _, _, meta in units}
        assert set(seasons) == {1, 2}
        assert seasons[1][0] == s1
        assert seasons[1][1]["show_name"] == "The Expanse"
        assert seasons[1][1]["structure"] == "show_organised"
        assert seasons[2][0] == s2
        assert seasons[2][1]["show_name"] == "The Expanse"

    async def test_loose_root_files_do_not_shadow_season_subdirs(self, tmp_path):
        """Loose top-level MKVs must not shadow Season subfolders.

        Data-loss regression (Seinfeld): the watch root held both stray
        top-level rips (01.mkv, 02.mkv) and Season NN/ subfolders. The scanner
        early-returned a single flat unit on the first loose file and never
        imported the seasons — which were then deleted by staging cleanup. A
        root containing structured subfolders is a container, not a flat dump:
        the seasons must be imported and the ambiguous loose files left alone.
        """
        show_root = tmp_path / "Seinfeld"
        show_root.mkdir()
        (show_root / "01.mkv").write_bytes(b"\x00" * 1024)
        (show_root / "02.mkv").write_bytes(b"\x00" * 1024)
        s1 = show_root / "Season 1"
        s1.mkdir()
        (s1 / "title_t00.mkv").write_bytes(b"\x00" * 2048)
        s2 = show_root / "Season 2"
        s2.mkdir()
        (s2 / "title_t00.mkv").write_bytes(b"\x00" * 2048)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(show_root))
        units = watcher._scan_import_dir(show_root)

        seasons = {meta["season"]: path for path, _, _, meta in units}
        assert set(seasons) == {1, 2}, f"expected only Season 1 & 2 units, got {units}"
        assert seasons[1] == s1
        assert seasons[2] == s2
        assert all(meta["structure"] != "flat" for *_, meta in units), (
            "no flat unit should be emitted when Season subfolders exist"
        )
        assert all(path != show_root for path, *_ in units), (
            "the watch root itself must not become an import unit"
        )

    @pytest.mark.parametrize(
        "folder_name,expected_season",
        [
            ("Season 1", 1),
            ("Season 01", 1),
            ("Season1", 1),
            ("season 2", 2),
            ("Season 12", 12),
        ],
    )
    async def test_season_folder_spelling_variants(self, tmp_path, folder_name, expected_season):
        """Single-digit, double-digit, and zero-padded season spellings all parse."""
        show_root = tmp_path / "The Expanse"
        show_root.mkdir()
        season_dir = show_root / folder_name
        season_dir.mkdir()
        (season_dir / "title_t00.mkv").write_bytes(b"\x00" * 512)

        watcher = StagingWatcher("/tmp/staging", import_watch_path=str(show_root))
        units = watcher._scan_import_dir(show_root)

        assert len(units) == 1
        _, _, _, meta = units[0]
        assert meta["season"] == expected_season
        assert meta["show_name"] == "The Expanse"
        assert meta["structure"] == "show_organised"


class TestImportWatcherPolling:
    """Tests for the full poll loop with import paths."""

    async def test_import_dir_fires_after_stability(self, tmp_path):
        """Import unit fires callback after STABILITY_THRESHOLD stable polls."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "THE_OFFICE_S1D1"
        disc.mkdir()
        (disc / "title_t01.mkv").write_bytes(b"\x00" * 1024)

        staging = tmp_path / "staging"
        staging.mkdir()
        watcher = StagingWatcher(str(staging), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        await watcher._check_staging()
        callback.assert_not_called()

        await watcher._check_staging()
        callback.assert_not_called()

        await watcher._check_staging()
        callback.assert_called_once()

        args = callback.call_args[0]
        assert args[0] == "staging_ready"
        assert args[1] == str(disc)
        metadata = args[3] if len(args) > 3 else None
        assert metadata is not None
        assert metadata["source"] == "import"
        assert metadata["structure"] == "disc_folder"

    async def test_import_not_retriggered_after_fire(self, tmp_path):
        """Import unit is not re-triggered in subsequent polls."""
        watch_root = tmp_path / "arm"
        watch_root.mkdir()
        disc = watch_root / "DISC1"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        staging = tmp_path / "staging"
        staging.mkdir()
        watcher = StagingWatcher(str(staging), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(3):
            await watcher._check_staging()
        assert callback.call_count == 1

        for _ in range(5):
            await watcher._check_staging()
        assert callback.call_count == 1

    async def test_staging_and_import_fire_independently(self, tmp_path):
        """Existing staging scan and import scan are both active simultaneously."""
        staging = tmp_path / "staging"
        staging.mkdir()
        watch_root = tmp_path / "arm"
        watch_root.mkdir()

        disc = watch_root / "IMPORT_DISC"
        disc.mkdir()
        (disc / "t.mkv").write_bytes(b"\x00" * 100)

        staging_sub = staging / "STAGING_DISC"
        staging_sub.mkdir()
        (staging_sub / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(str(staging), import_watch_path=str(watch_root))
        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(3):
            await watcher._check_staging()

        assert callback.call_count == 2
        # One call should have metadata with source="import", other with metadata=None
        sources = set()
        for call in callback.call_args_list:
            args = call[0]
            meta = args[3] if len(args) > 3 else None
            sources.add(meta["source"] if meta else "staging")
        assert "import" in sources
        assert "staging" in sources

    async def test_metadata_fourth_arg_is_none_for_staging(self, tmp_path):
        """Existing staging path callback passes None as fourth argument."""
        staging = tmp_path / "staging"
        staging.mkdir()
        sub = staging / "DISC"
        sub.mkdir()
        (sub / "t.mkv").write_bytes(b"\x00" * 100)

        watcher = StagingWatcher(str(staging))
        callback = AsyncMock()
        watcher._async_callback = callback

        for _ in range(3):
            await watcher._check_staging()

        callback.assert_called_once()
        args = callback.call_args[0]
        # Fourth arg should be None for staging-path callbacks
        assert len(args) == 4
        assert args[3] is None
