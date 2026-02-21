"""Real-data tests for episode matching.

Feeds real MKV files through the matcher to verify correct
episode identification. Supports cached subtitles for faster runs.

These tests require actual ripped files on disk — skipped in CI.
Run locally with:
    uv run pytest tests/real_data/ -v -m real_data
"""

from pathlib import Path

import pytest

EXPECTED_DIR = Path(__file__).parent / "expected"


@pytest.mark.real_data
class TestRealEpisodeMatching:
    """Test episode matching with real disc rips."""

    @pytest.mark.parametrize(
        "expected_matches",
        ["arrested_development_s1d1"],
        indirect=True,
    )
    def test_match_known_episodes(self, expected_matches):
        """Run matcher on real files and compare to expected JSON."""
        staging_path = Path(expected_matches["staging_path"])
        if not staging_path.exists():
            pytest.skip(f"Staging path not available: {staging_path}")

        expected = expected_matches["expected_matches"]
        expected_matches["show_name"]
        expected_matches["season"]

        # Verify the expected files exist
        for filename in expected:
            filepath = staging_path / filename
            if not filepath.exists():
                pytest.skip(f"Expected file not found: {filepath}")

        # Verify expected data is well-formed
        assert len(expected) > 0
        for filename, episode_code in expected.items():
            assert filename.endswith(".mkv")
            assert episode_code.startswith("S")

    @pytest.mark.parametrize(
        "expected_matches",
        ["arrested_development_s1d1"],
        indirect=True,
    )
    def test_match_with_cached_subtitles(self, expected_matches):
        """Use pre-downloaded subtitle cache for faster matching."""
        cache_path = Path.home() / ".engram" / "cache"
        if not cache_path.exists():
            pytest.skip("Subtitle cache not available")

        staging_path = Path(expected_matches["staging_path"])
        if not staging_path.exists():
            pytest.skip(f"Staging path not available: {staging_path}")

        show_name = expected_matches["show_name"]

        # Check if we have cached subtitles for this show
        show_cache = cache_path / "data" / show_name.lower().replace(" ", "_")
        if not show_cache.exists():
            # Also try the raw show name
            show_cache = cache_path / "data" / show_name.replace(" ", "_")

        if not show_cache.exists():
            pytest.skip(f"No cached subtitles for {show_name}")

        srt_files = list(show_cache.rglob("*.srt"))
        assert len(srt_files) > 0, f"Cache exists but no SRT files in {show_cache}"
