"""Pre-rip short-title filter (app.core.episode_runtime.find_short_titles).

Cases are real disc shapes. The filter must skip only when every available
signal agrees a track is too short to be an episode, because a wrongly skipped
episode means re-inserting an already-ejected disc.
"""

from app.core.episode_runtime import ShortTitle, find_short_titles


def _m(minutes: float) -> int:
    return int(minutes * 60)


def _disc(*minutes: float) -> list[tuple[int, int]]:
    return [(i, _m(m)) for i, m in enumerate(minutes)]


def _skipped(result: list[ShortTitle]) -> set[int]:
    return {s.index for s in result}


class TestBothSignals:
    def test_hour_long_drama_extras_skipped(self):
        """Jahan's report: ~35-40 min episodes plus 5-12 min extras."""
        disc = _disc(38, 39, 37, 40, 5, 9, 12)
        runtimes = [36, 38, 37, 39, 38, 40]
        assert _skipped(find_short_titles(disc, runtimes)) == {4, 5, 6}

    def test_reason_names_both_signals(self):
        disc = _disc(38, 39, 37, 12)
        [short] = find_short_titles(disc, [38, 38, 38])
        assert short.index == 3
        assert "TMDB" in short.reason
        assert "this disc" in short.reason

    def test_extras_heavy_disc_skips_featurettes_not_episodes(self):
        """Two episodes and six featurettes: the featurettes outnumber the episodes,
        so a plain median would call the episodes long and the featurettes normal."""
        disc = _disc(43, 44, 6, 7, 7, 8, 14, 3)
        runtimes = [42, 43, 44]
        assert _skipped(find_short_titles(disc, runtimes)) == {2, 3, 4, 5, 6, 7}

    def test_gilmore_long_episode_kept(self):
        """A 49.8-minute episode against TMDB's 44 is long, never short."""
        disc = _disc(44, 45, 49.8, 46, 8)
        assert _skipped(find_short_titles(disc, [44, 44, 44, 44])) == {4}

    def test_double_length_pilot_kept(self):
        """DS9 "Emissary": a 90-minute pilot among 45-minute episodes."""
        disc = _disc(90, 45, 45, 46, 4)
        assert _skipped(find_short_titles(disc, [92, 45, 45, 45])) == {4}


class TestDisagreementProtects:
    def test_cartoon_conjoined_tracks_kept(self):
        """TMDB lists ~11-min segments; the disc carries 22-min conjoined tracks.
        Nothing on the disc is shorter than a single segment."""
        disc = _disc(22, 23, 22, 23, 22)
        assert find_short_titles(disc, [11, 11, 11, 11, 11, 11, 11, 11, 11, 11]) == []

    def test_disc_splits_what_tmdb_combines(self):
        """TMDB lists 22-min episodes but the disc splits them into 11-min
        segments: TMDB alone says short, the disc's own episode group says
        normal, so nothing is skipped."""
        disc = _disc(11, 11, 12, 11, 12, 11)
        assert find_short_titles(disc, [22, 22, 22]) == []

    def test_short_show_with_tmdb(self):
        """An 11-minute show whose TMDB agrees: its episodes are not short, only
        the 2-minute bumper is."""
        disc = _disc(11, 11, 12, 11, 2)
        assert _skipped(find_short_titles(disc, [11, 11, 11, 11])) == {4}


class TestOneSignal:
    def test_tmdb_only_when_no_disc_group(self):
        """Every track a different length: no disc group, TMDB decides alone."""
        disc = _disc(36, 41, 46, 8)
        assert _skipped(find_short_titles(disc, [40, 41, 42])) == {3}

    def test_disc_only_when_runtimes_unknown(self):
        disc = _disc(38, 39, 38, 40, 9, 12)
        assert _skipped(find_short_titles(disc, None)) == {4, 5}

    def test_disc_only_uses_stricter_ratio(self):
        """18 min vs ~38 min episodes is under 50% but not under 40%."""
        disc = _disc(38, 39, 38, 18)
        assert find_short_titles(disc, [38, 38, 38])[0].index == 3
        assert find_short_titles(disc, None) == []

    def test_disc_only_needs_group_of_three(self):
        disc = _disc(38, 39, 9, 30)
        assert find_short_titles(disc, None) == []

    def test_zero_runtimes_treated_as_unknown(self):
        disc = _disc(38, 39, 38, 9)
        assert _skipped(find_short_titles(disc, [0, 0, 0])) == {3}


class TestDriftingDurations:
    """Greedy grouping against a running mean can chain upward-drifting tracks
    (20, 21, 22, 23, 24 min) or split them at an arbitrary point. Either way no
    episode-length track may be skipped; only the real outlier is."""

    def test_drift_with_tmdb(self):
        disc = _disc(20, 21, 22, 23, 24, 3)
        assert _skipped(find_short_titles(disc, [22, 22, 22, 22, 22])) == {5}

    def test_drift_disc_only(self):
        disc = _disc(20, 21, 22, 23, 24, 3)
        assert _skipped(find_short_titles(disc, None)) == {5}

    def test_drift_split_group_never_skips_its_tail(self):
        """The 24-minute track lands outside the chained group but is far above
        half of it, so a split boundary cannot turn into a skip."""
        disc = _disc(20, 21, 22, 23, 24)
        assert find_short_titles(disc, None) == []


class TestNoAction:
    def test_too_few_titles(self):
        assert find_short_titles(_disc(40, 5), [40]) == []

    def test_no_signal_at_all(self):
        assert find_short_titles(_disc(30, 36, 8), None) == []

    def test_never_skips_everything(self):
        """Wrong season runtimes and no disc group: every track under the TMDB
        floor. A disc of nothing but extras is far likelier a bad reference."""
        disc = _disc(20, 24, 28)
        assert find_short_titles(disc, [45, 45]) == []

    def test_excluded_titles_ignored(self):
        """Play All rows neither count as candidates nor shape the reference."""
        disc = _disc(38, 39, 38, 152, 9)
        result = find_short_titles(disc, [38, 38, 38], exclude={3})
        assert _skipped(result) == {4}

    def test_zero_duration_ignored(self):
        disc = [(0, _m(38)), (1, _m(39)), (2, 0), (3, _m(38))]
        assert find_short_titles(disc, [38]) == []
