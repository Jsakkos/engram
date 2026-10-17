"""Play-All detection from the disc's own structure (#634).

Fixtures under tests/fixtures/makemkv_scans/ are real MakeMKV scan logs trimmed
to the CINFO/TINFO attributes the analyst reads (drive serials and messages
dropped). The two Project Runway DVDs come from the bug-report bundles attached
to #634 (jobs 68 and 70); the rest are maintainer discs.

What the logs establish:

* Blu-ray TINFO 26 lists clip (m2ts) numbers that are global to the disc, so a
  Play-All playlist's clip set contains each episode's clips (South Park).
* DVD TINFO 26 lists cell numbers local to each title's program chain. Two
  different episodes both report cells "3,4,5,6" and an 8-cell extra "covers"
  the Play-All, so DVD segment maps say nothing about shared content and must
  not be compared. DVDs fall to the duration+size sum instead, which the
  Play-All matches to the second (and to within 0.01% of bytes).
"""

from pathlib import Path

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
from app.core.extractor import MakeMKVExtractor
from app.core.tmdb_classifier import TmdbSignal
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType

SCANS = Path(__file__).parent.parent / "fixtures" / "makemkv_scans"


def _config() -> AppConfig:
    return AppConfig(
        analyst_movie_min_duration=4800,
        analyst_tv_duration_variance=120,
        analyst_tv_min_cluster_size=3,
        analyst_tv_min_duration=1080,
        analyst_tv_max_duration=4200,
        analyst_movie_dominance_threshold=0.6,
    )


def _analyst() -> DiscAnalyst:
    return DiscAnalyst(config=_config())


def _scan(name: str) -> list[TitleInfo]:
    extractor = MakeMKVExtractor(makemkv_path=Path("/usr/bin/makemkvcon"))
    titles, _ = extractor._parse_disc_info((SCANS / name).read_text(encoding="utf-8"))
    return sorted(titles, key=lambda t: t.index)


def _detect(titles: list[TitleInfo], expected: list[int] | None = None) -> list[int]:
    analyst = _analyst()
    return analyst._detect_play_all(titles, analyst._detect_tv_show(titles), expected)


def _tv_signal(name: str) -> TmdbSignal:
    return TmdbSignal(content_type=ContentType.TV, confidence=0.9, tmdb_id=1, tmdb_name=name)


@pytest.mark.unit
class TestScanParsing:
    def test_exact_byte_size_from_tinfo_11(self):
        """TINFO 10 ("7.0 GB") is rounded and binary; TINFO 11 is the exact byte count."""
        titles = _scan("project_runway_disc1_dvd.txt")
        assert titles[0].size_bytes == 7588317184

    def test_bluray_titles_carry_playlist_source(self):
        titles = _scan("south_park_s6d1_bluray.txt")
        assert titles[3].source_filename == "00002.mpls"
        assert titles[3].segment_map == "22,20,21,19,23,7,11,4,5"


@pytest.mark.unit
class TestDvdPlayAllBySum:
    """DVDs: the Play-All equals the sum of the episode titles it strings together."""

    def test_project_runway_disc1_job_68(self):
        # t00 2:49:09 = 43:52 + 41:58 + 40:39 + 42:40 exactly. The episodes spread
        # over 3 minutes, so the ±2 min duration cluster only holds three of them
        # and the old cluster-sum rule saw a 1.35 ratio and kept the Play-All.
        assert _detect(_scan("project_runway_disc1_dvd.txt")) == [0]

    def test_project_runway_disc3_job_70(self):
        # t00 2:07:38 = t01 43:25 + t02 1:24:13 (a double-length episode, which
        # sits outside the TV duration range the old rules summed over).
        assert _detect(_scan("project_runway_disc3_dvd.txt")) == [0]

    def test_disc3_double_length_episode_is_not_play_all(self):
        """t02 is one 84-min episode (no pair of other titles sums to it)."""
        assert 2 not in _detect(_scan("project_runway_disc3_dvd.txt"))

    def test_disc3_extras_compilations_are_not_play_all(self):
        """t03 (40 min) is exactly five short featurettes; t09 is a reel of clips.

        Only episode-length titles count as parts, so an extras compilation is
        not mistaken for a Play-All of episodes.
        """
        found = _detect(_scan("project_runway_disc3_dvd.txt"))
        assert 3 not in found
        assert 9 not in found

    def test_dvd_cell_maps_are_never_compared(self):
        """Arrested Development t04 has cells 1-11, a superset of every other title's
        cell numbers, but it is one 22-min episode."""
        titles = _scan("arrested_development_s1d1_dvd.txt")
        assert _analyst()._detect_play_all_by_segments(titles) == []

    @pytest.mark.parametrize(
        "name", ["arrested_development_s1d1_dvd.txt", "arrested_development_s2d2_dvd.txt"]
    )
    def test_dvd_without_play_all(self, name):
        assert _detect(_scan(name)) == []

    def test_sum_ignores_tmdb_runtime_exclusion(self):
        """A structural match is not overruled by TMDB runtimes, which is the data
        that disagrees with discs. 169 min happens to equal 4 x 42 min here."""
        titles = _scan("project_runway_disc1_dvd.txt")
        assert _detect(titles, expected=[42, 42, 42, 42, 169]) == [0]


@pytest.mark.unit
class TestBlurayPlayAllBySegments:
    def test_south_park_s6d1(self):
        """t03's playlist holds the clips of all nine episode playlists."""
        titles = _scan("south_park_s6d1_bluray.txt")
        assert _analyst()._detect_play_all_by_segments(titles) == [3]
        assert _detect(titles) == [3]

    def test_south_park_s16d2_shared_clip(self):
        """Every episode is '<own clip>,14' (a shared clip); the Play-All lists clip 14
        once, so its bytes are well short of the episode sum but the clip sets still
        nest."""
        titles = _scan("south_park_s16d2_bluray.txt")
        assert _analyst()._detect_play_all_by_segments(titles) == [7]
        assert _detect(titles) == [7]

    def test_extras_compilation_on_movie_disc_not_flagged(self):
        """Glass: extras playlists nest short featurette clips (2-3 min). Parts must
        be episode-length, so none of them is a Play-All."""
        assert _analyst()._detect_play_all_by_segments(_scan("glass_bluray.txt")) == []

    def test_long_episode_not_flagged(self):
        """For All Mankind S1D4: a 76-min episode next to a 48-min one."""
        assert _detect(_scan("for_all_mankind_s1d4_bluray.txt")) == []

    def test_shared_clip_title_needs_matching_runtime(self):
        """Episodes that cut one big clip with in/out points all list the same clip.
        A title that adds an intro clip nests over all of them but is one episode
        long, so the runtime check rejects it."""
        titles = [
            TitleInfo(0, 1320, 0, 6, source_filename="00001.mpls", segment_map="1"),
            TitleInfo(1, 1330, 0, 6, source_filename="00002.mpls", segment_map="2"),
            TitleInfo(2, 1400, 0, 6, source_filename="00003.mpls", segment_map="0,1"),
            TitleInfo(3, 1310, 0, 6, source_filename="00004.mpls", segment_map="0,2"),
            TitleInfo(4, 1500, 0, 6, source_filename="00005.mpls", segment_map="0,1,2"),
        ]
        assert _analyst()._detect_play_all_by_segments(titles) == []

    def test_duplicate_playlists_count_once(self):
        """The same clip exposed as an .mpls and as a bare .m2ts is one part."""
        titles = [
            TitleInfo(0, 2600, 0, 6, source_filename="00001.mpls", segment_map="5"),
            TitleInfo(1, 2600, 0, 0, source_filename="00005.m2ts", segment_map="5"),
            TitleInfo(2, 2700, 0, 6, source_filename="00002.mpls", segment_map="5,6"),
        ]
        assert _analyst()._detect_play_all_by_segments(titles) == []


@pytest.mark.unit
class TestNoFalsePositives:
    def test_single_title_disc(self):
        """Every episode in one title: nothing to be a concatenation of."""
        titles = [TitleInfo(0, 4 * 1320, 0, 24)]
        assert _detect(titles) == []

    def test_coincidental_sum_of_short_titles_ignored(self):
        """Many short extras can sum to anything; only episode-length parts count."""
        titles = [TitleInfo(0, 2700, 1_000_000_000, 6)] + [
            TitleInfo(i, 300, 1_000_000_000 // 9, 1) for i in range(1, 10)
        ]
        assert _analyst()._detect_play_all_by_sum(titles) == []

    def test_sum_needs_matching_size(self):
        """A double-length episode that lands on the sum of two others in runtime,
        but is encoded at a different bitrate, is not a Play-All."""
        titles = [
            TitleInfo(0, 1300, 700_000_000, 6),
            TitleInfo(1, 1302, 690_000_000, 6),
            TitleInfo(2, 2602, 1_100_000_000, 10),
        ]
        assert _analyst()._detect_play_all_by_sum(titles) == []

    def test_ds9_pilot_is_not_a_sum(self):
        """DS9 S1D1 (job 153): the 90-min 'Emissary' is 4 s off t1 + t2, more than
        two truncated runtimes can explain."""
        titles = [
            TitleInfo(0, 5429, 2_000_000_000, 18),
            TitleInfo(1, 2718, 1_000_000_000, 8),
            TitleInfo(2, 2715, 1_000_000_000, 8),
        ]
        assert _analyst()._detect_play_all_by_sum(titles) == []

    def test_two_part_sum_yields_to_expected_runtime(self):
        """An exact two-part match on a title TMDB says is one long episode is kept."""
        titles = [
            TitleInfo(0, 5433, 2_000_000_000, 18),
            TitleInfo(1, 2718, 1_000_000_000, 8),
            TitleInfo(2, 2715, 1_000_000_000, 8),
        ]
        assert _analyst()._detect_play_all_by_sum(titles) == [0]
        assert _analyst()._detect_play_all_by_sum(titles, [90, 45, 45]) == []

    def test_sum_without_sizes_uses_duration_only(self):
        """Staging imports and simulations may have no sizes; runtime alone decides."""
        titles = [
            TitleInfo(0, 1300, 0, 6),
            TitleInfo(1, 1302, 0, 6),
            TitleInfo(2, 1310, 0, 6),
            TitleInfo(3, 3912, 0, 18),
        ]
        assert _analyst()._detect_play_all_by_sum(titles) == [3]


@pytest.mark.unit
class TestAnalyzeEndToEnd:
    @pytest.mark.parametrize(
        ("name", "label", "expected"),
        [
            ("project_runway_disc1_dvd.txt", "PROJECT_RUNWAY_DISC1", [0]),
            ("project_runway_disc3_dvd.txt", "PROJECT_RUNWAY_DISC3", [0]),
        ],
    )
    def test_result_carries_play_all(self, name, label, expected):
        result = _analyst().analyze(_scan(name), label, tmdb_signal=_tv_signal("Project Runway"))
        assert result.content_type == ContentType.TV
        assert result.play_all_title_indices == expected
