"""Test full TV pipeline: classification -> track selection -> duration filter -> organization.

Verifies the chain from Analyst classification through to Organizer path generation
using real disc metadata snapshots.
"""

import pytest
from pathlib import Path

from app.core.organizer import organize_tv_episode, organize_tv_extras
from app.models.disc_job import ContentType

from tests.pipeline.conftest import (
    _default_config,
    load_snapshot,
    snapshot_to_titles,
    snapshot_to_tmdb_signal,
)


@pytest.mark.pipeline
class TestPicardTrackSelection:
    """Star Trek Picard S1D3: Play All skipped, episodes selected, extras filtered."""

    def test_three_episodes_in_tv_range(self, analyst):
        """t00 (56.6min), t01 (44.9min), t02 (55.4min) are in TV range (18-70min)."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        config = analyst._get_config()

        episode_tracks = [
            t
            for t in titles
            if config.analyst_tv_min_duration
            <= t.duration_seconds
            <= config.analyst_tv_max_duration
        ]
        assert len(episode_tracks) == 3
        assert {t.index for t in episode_tracks} == {0, 1, 2}

    def test_extra_below_tv_range(self, analyst):
        """t04 (306s = 5.1min) is below the 18min minimum for TV episodes."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        config = analyst._get_config()

        short_tracks = [t for t in titles if t.duration_seconds < config.analyst_tv_min_duration]
        assert len(short_tracks) == 1
        assert short_tracks[0].index == 4

    def test_play_all_excluded_from_rip_selection(self, analyst):
        """After analysis, Play All (t03) should be in play_all_title_indices."""
        snap = load_snapshot("star_trek_picard_s1d3")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)

        play_all_set = set(result.play_all_title_indices)
        rippable = [t for t in titles if t.index not in play_all_set]
        rippable_indices = {t.index for t in rippable}

        assert 3 not in rippable_indices
        assert rippable_indices == {0, 1, 2, 4}

    def test_episode_organization_paths(self, tmp_path):
        """Organizer produces correct paths for Picard episodes."""
        library = tmp_path / "tv"
        snap = load_snapshot("star_trek_picard_s1d3")

        episode_map = {
            0: "S01E07",
            1: "S01E08",
            2: "S01E09",
        }

        for track in snap["tracks"]:
            ep = track.get("expected_episode")
            if not ep:
                continue

            source = tmp_path / "staging" / track["filename"]
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"x" * 1024)

            result = organize_tv_episode(source, "Star Trek Picard", ep, library_path=library)
            assert result["success"], f"Failed for {ep}: {result.get('error')}"
            assert ep.upper() in str(result["final_path"])
            assert "Season 01" in str(result["final_path"])
            assert "Star Trek Picard" in str(result["final_path"])

    def test_extras_organization_path(self, tmp_path):
        """Extra track (t04) should organize to Extras subdirectory."""
        library = tmp_path / "tv"
        source = tmp_path / "staging" / "extra.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x" * 1024)

        result = organize_tv_extras(
            source,
            "Star Trek Picard",
            season=1,
            library_path=library,
            disc_number=3,
            extra_index=1,
        )
        assert result["success"]
        assert "Extras" in str(result["final_path"])
        assert "Disc 3" in str(result["final_path"])


@pytest.mark.pipeline
class TestArrestedDevPipeline:
    """ARRESTED_Development_S1D1: 8 episodes, non-sequential tracks, 3 extras."""

    def test_eight_episodes_in_cluster(self, analyst):
        """8 tracks with durations 21.7-28.6min should form an episode cluster."""
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        config = analyst._get_config()

        episode_tracks = [
            t
            for t in titles
            if config.analyst_tv_min_duration
            <= t.duration_seconds
            <= config.analyst_tv_max_duration
        ]
        assert len(episode_tracks) == 8

    def test_three_extras_below_tv_range(self, analyst):
        """t08 (16.6min), t09 (2.5min), t10 (6.5min) are below 18min threshold."""
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        config = analyst._get_config()

        short_tracks = [t for t in titles if t.duration_seconds < config.analyst_tv_min_duration]
        assert len(short_tracks) == 3
        short_indices = {t.index for t in short_tracks}
        assert short_indices == {8, 9, 10}

    def test_multi_prefix_filenames(self):
        """Track filenames use multiple disc prefixes (B1, C1, D1, D4, E1).

        MakeMKV on multi-disc sets produces files with different prefix patterns.
        The filename-to-index mapping (regex t(\\d+)\\.mkv$) must handle these.
        """
        snap = load_snapshot("arrested_development_s1d1")
        filenames = [t["filename"] for t in snap["tracks"]]
        prefixes = {f.split("_")[0] for f in filenames}
        # Multiple distinct prefixes demonstrate multi-disc origin
        assert len(prefixes) > 1
        assert "B1" in prefixes
        assert "C1" in prefixes

    def test_episode_organization_paths(self, tmp_path):
        """Organizer produces S01E01 through S01E08 for all 8 episodes."""
        library = tmp_path / "tv"
        snap = load_snapshot("arrested_development_s1d1")
        organized_codes = []

        for track in snap["tracks"]:
            ep = track.get("expected_episode")
            if not ep:
                continue

            source = tmp_path / "staging" / track["filename"]
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"x" * 1024)

            result = organize_tv_episode(
                source,
                "Arrested Development",
                ep,
                library_path=library,
            )
            assert result["success"], f"Failed for {ep}: {result.get('error')}"
            organized_codes.append(ep)

        assert sorted(organized_codes) == [f"S01E{i:02d}" for i in range(1, 9)]

    def test_mixed_duration_episodes_in_cluster(self, analyst):
        """Cluster analysis should handle the pilot episodes at 28.6min
        alongside regular episodes at 21.7-22.2min."""
        snap = load_snapshot("arrested_development_s1d1")
        titles = snapshot_to_titles(snap)
        signal = snapshot_to_tmdb_signal(snap)
        result = analyst.analyze(titles, snap["volume_label"], tmdb_signal=signal)

        # Despite the duration variance (28.6 vs 21.7), all 8 should cluster as TV
        assert result.content_type == ContentType.TV
        assert result.confidence >= 0.7
