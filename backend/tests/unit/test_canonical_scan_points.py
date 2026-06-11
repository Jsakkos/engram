"""Tests for ``canonical_scan_points`` — the nested scan-point lattice.

The critical invariant: every shallower lattice level is a strict subset of
every deeper one, so a transcript cache keyed by (file, offset, duration)
gets full reuse between a 10-point scan and a deeper re-match.
"""

import pytest

from app.matcher.episode_identification import canonical_scan_points

SKIP_INITIAL = 90
LEVELS = (10, 19, 37, 73, 145)
# Mix of round and awkward durations (2643 is prime).
DURATIONS = (1200, 2700, 5000, 7200, 2643)


class TestSubsetProperty:
    """Level k must be a subset of level k+1 — the cache-reuse guarantee."""

    @pytest.mark.parametrize("duration", DURATIONS)
    @pytest.mark.parametrize("k", range(len(LEVELS) - 1))
    def test_consecutive_levels_nest(self, duration, k):
        shallow = canonical_scan_points(duration, skip_initial=SKIP_INITIAL, num_points=LEVELS[k])
        deep = canonical_scan_points(duration, skip_initial=SKIP_INITIAL, num_points=LEVELS[k + 1])
        assert set(shallow) <= set(deep), (
            f"level {LEVELS[k]} not nested in level {LEVELS[k + 1]} for duration {duration}: "
            f"missing {set(shallow) - set(deep)}"
        )

    @pytest.mark.parametrize("duration", DURATIONS)
    @pytest.mark.parametrize("deep_level", (37, 145))
    def test_level_10_nests_in_non_adjacent_levels(self, duration, deep_level):
        base = canonical_scan_points(duration, skip_initial=SKIP_INITIAL, num_points=10)
        deep = canonical_scan_points(duration, skip_initial=SKIP_INITIAL, num_points=deep_level)
        assert set(base) <= set(deep)

    def test_float_duration_still_nests(self):
        shallow = canonical_scan_points(2643.7, skip_initial=SKIP_INITIAL, num_points=10)
        deep = canonical_scan_points(2643.7, skip_initial=SKIP_INITIAL, num_points=145)
        assert shallow  # sanity: not vacuous
        assert set(shallow) <= set(deep)


class TestSnapping:
    """num_points snaps UP to the smallest lattice level >= the request."""

    @pytest.mark.parametrize(
        ("requested", "expected_level"),
        [
            (10, 10),
            (11, 19),
            (19, 19),
            (25, 37),
            (37, 37),
            (50, 73),
            (100, 145),
            (145, 145),
            (146, 145),
            (500, 145),
            (None, 10),
            (1, 10),
            (0, 10),
            (2, 10),
        ],
    )
    def test_snaps_to_lattice_level(self, requested, expected_level):
        # 7200s leaves plenty of room: no tail-filter drops, no dedupe collisions,
        # so the returned count equals the snapped lattice level exactly.
        points = canonical_scan_points(7200, skip_initial=SKIP_INITIAL, num_points=requested)
        assert len(points) == expected_level


class TestDefaultBehavior:
    def test_typical_episode_default_count(self):
        # A ~40-minute episode at the default depth yields exactly 10 points,
        # all inside the usable window.
        duration = 2400
        points = canonical_scan_points(duration, skip_initial=SKIP_INITIAL)
        assert len(points) == 10
        assert all(SKIP_INITIAL <= p < duration - 30 for p in points)
        assert points == sorted(points)

    def test_even_coverage(self):
        # Floor-division jitter: consecutive gaps differ by at most 1 second.
        points = canonical_scan_points(2643, skip_initial=SKIP_INITIAL, num_points=10)
        gaps = [b - a for a, b in zip(points, points[1:], strict=False)]
        assert max(gaps) - min(gaps) <= 1


class TestEdgeCases:
    @pytest.mark.parametrize("duration", (30, 200, 209, 210))
    def test_no_usable_window_returns_empty(self, duration):
        # skip_initial(90) + skip_final(120) = 210 >= duration -> nothing usable.
        assert canonical_scan_points(duration, skip_initial=SKIP_INITIAL) == []

    def test_short_positive_window(self):
        # duration 250 -> available = 40s: valid but cramped.
        duration = 250
        points = canonical_scan_points(duration, skip_initial=SKIP_INITIAL)
        assert points
        assert all(p >= 0 for p in points)
        assert len(points) == len(set(points))
        assert all(p < duration - 30 for p in points)
        assert points == sorted(points)

    def test_very_short_window_dedupes_collisions(self):
        # available = 5s: floor division collapses adjacent lattice points.
        duration = 215
        points = canonical_scan_points(duration, skip_initial=SKIP_INITIAL)
        assert points
        assert len(points) == len(set(points))
        assert points == sorted(points)
        assert all(SKIP_INITIAL <= p < duration - 30 for p in points)

    def test_float_duration_truncates_like_int(self):
        float_points = canonical_scan_points(2643.7, skip_initial=SKIP_INITIAL, num_points=10)
        int_points = canonical_scan_points(2643, skip_initial=SKIP_INITIAL, num_points=10)
        assert float_points == int_points
        assert all(isinstance(p, int) for p in float_points)

    def test_no_negative_offsets_anywhere(self):
        for duration in (*DURATIONS, 215, 250, 300):
            for level in LEVELS:
                points = canonical_scan_points(
                    duration, skip_initial=SKIP_INITIAL, num_points=level
                )
                assert all(p >= 0 for p in points)

    def test_tail_filter_respected_at_every_level(self):
        for duration in DURATIONS:
            for level in LEVELS:
                points = canonical_scan_points(
                    duration, skip_initial=SKIP_INITIAL, num_points=level
                )
                assert all(p < duration - 30 for p in points)
