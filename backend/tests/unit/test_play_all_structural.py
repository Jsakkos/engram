"""Structural "Play All" detection from MakeMKV's own description of a disc.

A Play All is a playlist over the episode titles, and MakeMKV says so directly:
the concatenation reports several segments while each episode reports one. That
is a fact about the disc rather than an inference from runtime arithmetic.

Being deselected costs the user a whole episode when the guess is wrong, so all
three signals must agree — segment structure, chapter counts, duration. These
tests are built from a real disc (job 14): a 155min title of 7 segments,
"1-6,7-11,12-16,17-21,22-27,28-32,33-37", beside seven ~22min episodes whose
chapter counts are exactly 6, 5, 5, 5, 6, 5, 5.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.analyst import (
    DiscAnalyst,
    TitleInfo,
    _parse_segment_groups,
    _structural_play_all,
)


def _title(index, minutes, chapters, segments=1, segment_map=None):
    return TitleInfo(
        index=index,
        duration_seconds=int(minutes * 60),
        size_bytes=1_000_000,
        chapter_count=chapters,
        segment_count=segments,
        segment_map=segment_map or f"1-{chapters}",
    )


# The disc as MakeMKV reported it.
REAL_DISC = [
    _title(0, 155.4, 37, 7, "1-6,7-11,12-16,17-21,22-27,28-32,33-37"),
    _title(1, 22.3, 6),
    _title(2, 22.0, 5),
    _title(3, 22.3, 5),
    _title(4, 22.1, 5),
    _title(5, 22.1, 6),
    _title(6, 22.4, 5),
    _title(7, 22.3, 5),
]


@pytest.mark.unit
class TestParseSegmentGroups:
    def test_dvd_cell_ranges(self):
        assert _parse_segment_groups("1-6,7-11,12-16") == [6, 5, 5]

    def test_bluray_clip_list(self):
        assert _parse_segment_groups("1,2,3") == [1, 1, 1]

    def test_single_segment(self):
        assert _parse_segment_groups("1-5") == [5]

    @pytest.mark.parametrize("value", [None, "", "not-a-map", "1-", "5-1", "1-x"])
    def test_unparseable_maps_yield_nothing(self, value):
        # "No structure" must read as "cannot corroborate", never as proof.
        assert _parse_segment_groups(value) == []


@pytest.mark.unit
class TestStructuralPlayAll:
    def test_finds_the_concatenation_on_a_real_disc(self):
        assert _structural_play_all(REAL_DISC) == [0]

    def test_leaves_every_episode_alone(self):
        found = _structural_play_all(REAL_DISC)
        assert all(index not in found for index in range(1, 8))

    def test_needs_the_chapter_counts_to_line_up(self):
        # Same segment count and duration, but its groups describe a different
        # shape than the episodes on the disc — not provably this disc's Play All.
        disc = [_title(0, 155.4, 37, 7, "1-7,8-12,13-17,18-22,23-28,29-33,34-37")] + REAL_DISC[1:]
        assert _structural_play_all(disc) == []

    def test_needs_the_duration_to_agree(self):
        # Structure matches, but it runs far longer than its parts combined — so
        # it holds something they do not, and discarding it would lose that.
        disc = [_title(0, 190.0, 37, 7, "1-6,7-11,12-16,17-21,22-27,28-32,33-37")] + REAL_DISC[1:]
        assert _structural_play_all(disc) == []

    def test_needs_the_map_to_match_the_segment_count(self):
        disc = [_title(0, 155.4, 37, 7, "1-6,7-11")] + REAL_DISC[1:]
        assert _structural_play_all(disc) == []

    def test_a_multi_segment_episode_is_not_a_concatenation(self):
        # A two-part episode presented as one playlist reports two segments. With
        # no pair of titles matching its shape, it stays.
        disc = [
            _title(0, 44.0, 10, 2, "1-5,6-10"),
            _title(1, 22.0, 5),
            _title(2, 30.0, 7),
        ]
        assert _structural_play_all(disc) == []

    def test_extras_around_the_episodes_do_not_break_the_match(self):
        # The run of parts is found positionally, so a featurette before and after
        # the episode block is simply not part of it.
        disc = [_title(9, 4.0, 2)] + REAL_DISC + [_title(20, 6.0, 3)]
        assert _structural_play_all(disc) == [0]

    def test_a_disc_with_no_concatenation_yields_nothing(self):
        assert _structural_play_all(REAL_DISC[1:]) == []


@pytest.mark.unit
class TestDetectPlayAllRouting:
    """Which detector runs: the disc's structure when it reports any, the older
    duration heuristic when it reports none, and neither when switched off."""

    def _analyzer(self, *, enabled=True):
        analyzer = DiscAnalyst()
        analyzer._get_config = lambda: SimpleNamespace(
            play_all_detection_enabled=enabled,
            analyst_movie_min_duration=80 * 60,
            analyst_tv_min_duration=18 * 60,
            analyst_tv_max_duration=70 * 60,
        )
        return analyzer

    def test_uses_the_structure_when_the_disc_reports_it(self):
        analyzer = self._analyzer()
        assert analyzer._detect_play_all(REAL_DISC, {"episode_indices": [1, 2, 3, 4, 5, 6, 7]}) == [
            0
        ]

    def test_falls_back_to_durations_when_no_segment_data_exists(self):
        # Older MakeMKV builds and some disc types report nothing structural;
        # those discs must keep the behaviour they had.
        disc = [
            TitleInfo(
                index=t.index, duration_seconds=t.duration_seconds, size_bytes=1, chapter_count=0
            )
            for t in REAL_DISC
        ]
        analyzer = self._analyzer()
        with patch("app.core.analyst._structural_play_all") as structural:
            found = analyzer._detect_play_all(disc, {"episode_indices": [1, 2, 3, 4, 5, 6, 7]})
        structural.assert_not_called()
        assert found == [0]

    def test_disabled_keeps_every_title(self):
        analyzer = self._analyzer(enabled=False)
        assert (
            analyzer._detect_play_all(REAL_DISC, {"episode_indices": [1, 2, 3, 4, 5, 6, 7]}) == []
        )
