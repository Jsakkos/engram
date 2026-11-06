"""Tests for the half-hour to segment crosswalk (#760).

The three seasons below are captured verbatim from TMDB and OpenSubtitles
(``tests/fixtures/numbering_crosswalk/``), not synthesized: the failure modes this
module exists for (typo'd release titles, a mis-uploaded segment file sharing a
feature with a half-hour file, two broadcasts aired the same day) only show up in
real data. The synthetic cases further down pin the individual rules.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.episode_codes import parse_episode_code
from app.matcher.numbering_crosswalk import (
    RosterEpisode,
    broadcast_groups,
    crosswalk_applies,
    derive_crosswalk,
    is_segmented_season,
    translate_codes,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "numbering_crosswalk"


def _load(name: str) -> SimpleNamespace:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return SimpleNamespace(
        season=data["season"],
        codes=data["corpus_codes"],
        roster=[RosterEpisode(**e) for e in data["roster"]],
        releases={int(k): v for k, v in data["releases"].items()},
    )


@pytest.fixture
def ha_s2():
    return _load("hey_arnold_s2")


@pytest.fixture
def dl_s1():
    return _load("dexters_lab_s1")


@pytest.fixture
def ha_s3():
    return _load("hey_arnold_s3")


def _ep(n: int, name: str, air: str = "", runtime: int = 11) -> RosterEpisode:
    return RosterEpisode(episode_number=n, name=name, air_date=air, runtime=runtime)


# --------------------------------------------------------------------------- real data


def test_hey_arnold_s2_matches_reporter_expectation(ha_s2):
    cw = derive_crosswalk(2, ha_s2.codes, ha_s2.roster, ha_s2.releases)
    assert [cw.mapping.get(f"S02E{n:02d}") for n in range(6, 11)] == [
        "S02E11-E12",
        "S02E13",
        "S02E14-E15",
        "S02E16-E17",
        "S02E18-E19",
    ]
    assert cw.mapping["S02E05"] == "S02E09-E10"  # typo'd release, filled by gap
    assert cw.mapping["S02E15"] == "S02E28-E29"  # E29 title only, expanded to its group
    assert "S02E19" not in cw.mapping  # Part Time Friends lives in TMDB S3
    assert cw.sources == {"release_title": 18, "air_date_gap": 1, "unmapped": 1}


def test_dexters_lab_s1_conflicting_uploads_fall_back_to_structure(dl_s1):
    cw = derive_crosswalk(1, dl_s1.codes, dl_s1.roster, dl_s1.releases)
    assert cw.mapping["S01E01"] == "S01E01-E03"
    assert cw.mapping["S01E12"] == "S01E34-E35"
    assert cw.mapping["S01E13"] == "S01E36-E38"
    assert cw.sources == {"release_title": 0, "air_date_gap": 13, "unmapped": 0}


def test_same_day_double_broadcast_is_split_by_runtime(ha_s3):
    groups = broadcast_groups(ha_s3.roster)
    assert [30, 31] in groups
    assert [32, 33] in groups
    cw = derive_crosswalk(3, ha_s3.codes, ha_s3.roster, ha_s3.releases)
    assert cw.mapping["S03E16"] == "S03E30-E31"


@pytest.mark.parametrize("name", ["hey_arnold_s2", "dexters_lab_s1", "hey_arnold_s3"])
def test_values_are_strictly_increasing_and_non_overlapping(name):
    f = _load(name)
    cw = derive_crosswalk(f.season, f.codes, f.roster, f.releases)
    last = 0
    for key in sorted(cw.mapping):
        season, episodes = parse_episode_code(cw.mapping[key])
        assert season == f.season
        assert episodes[0] > last, f"{key} -> {cw.mapping[key]} overlaps or goes backwards"
        last = episodes[-1]


# --------------------------------------------------------------------------- synthetic


def _pairs_roster(n_pairs: int) -> list[RosterEpisode]:
    """n_pairs half-hours of two 11-minute segments each, one air date per pair."""
    roster = []
    for i in range(n_pairs):
        air = f"1997-01-{i + 1:02d}"
        roster.append(_ep(2 * i + 1, f"Alpha Segment {2 * i + 1}", air))
        roster.append(_ep(2 * i + 2, f"Bravo Segment {2 * i + 2}", air))
    return roster


def _codes(n: int, season: int = 1) -> list[str]:
    return [f"S{season:02d}E{i:02d}" for i in range(1, n + 1)]


def test_release_naming_nothing_is_dissent_not_abstention():
    roster = _pairs_roster(3)
    releases = {2: ["S01E02.Alpha Segment 3.Bravo Segment 4", "Show.S01E02.WEBRip.x264"]}
    cw = derive_crosswalk(1, _codes(3), roster, releases)
    # No anchor, but structure still maps all three in order.
    assert cw.sources["release_title"] == 0
    assert cw.mapping["S01E02"] == "S01E03-E04"


def test_disagreeing_releases_give_no_anchor():
    roster = _pairs_roster(3)
    releases = {
        2: ["S01E02.Alpha Segment 3.Bravo Segment 4", "S01E02.Alpha Segment 5"],
    }
    cw = derive_crosswalk(1, _codes(3), roster, releases)
    assert cw.sources["release_title"] == 0


def test_title_inside_show_name_prefix_is_ignored():
    roster = [_ep(1, "Arnold", "d1"), _ep(2, "Helga", "d1"), _ep(3, "Gerald", "d2")]
    roster += [_ep(4, "Phoebe", "d2")]
    # "Arnold" appears only before the SxxEyy marker, in the show name.
    releases = {1: ["Hey.Arnold.S01E01.Gerald.Phoebe"]}
    cw = derive_crosswalk(1, _codes(1), roster, releases)
    assert cw.mapping == {"S01E01": "S01E03-E04"}


def test_title_shorter_than_min_chars_is_ignored():
    roster = [_ep(1, "Pie", "d1"), _ep(2, "Sky", "d1"), _ep(3, "Ice", "d2"), _ep(4, "Fog", "d2")]
    releases = {1: ["S01E01.Ice.Fog"], 2: ["S01E02.Pie.Sky"]}
    cw = derive_crosswalk(1, _codes(2), roster, releases)
    # Three-letter titles cannot anchor; structure maps in order instead.
    assert cw.sources["release_title"] == 0
    assert cw.mapping == {"S01E01": "S01E01-E02", "S01E02": "S01E03-E04"}


def test_longer_title_wins_over_contained_title():
    roster = [
        _ep(1, "Mermaid Man and Barnacle Boy", "d1"),
        _ep(2, "Pickles", "d1"),
        _ep(3, "Mermaid Man and Barnacle Boy II", "d2"),
        _ep(4, "Hooky", "d2"),
    ]
    releases = {2: ["S01E02 - Mermaid Man and Barnacle Boy II_Hooky"]}
    cw = derive_crosswalk(1, _codes(2), roster, releases)
    assert cw.mapping["S01E02"] == "S01E03-E04"
    assert cw.sources["release_title"] == 1


def test_inverted_anchors_are_both_dropped():
    roster = _pairs_roster(4)
    releases = {
        1: ["S01E01.Alpha Segment 5.Bravo Segment 6"],
        2: ["S01E02.Alpha Segment 1.Bravo Segment 2"],
    }
    cw = derive_crosswalk(1, _codes(4), roster, releases)
    assert cw.sources["release_title"] == 0
    # With both dropped, the season is one gap: 4 slots, 4 groups.
    assert cw.mapping["S01E01"] == "S01E01-E02"


def test_gap_with_unequal_counts_stays_unmapped():
    # E1-E3 aired as ONE group, so the gap before the anchor holds two corpus
    # slots but only one broadcast group: no honest way to split it.
    roster = [
        _ep(1, "Alpha One", "d1"),
        _ep(2, "Bravo Two", "d1"),
        _ep(3, "Charlie Three", "d1"),
        _ep(4, "Delta Four", "d3"),
        _ep(5, "Echo Five", "d3"),
    ]
    releases = {3: ["S01E03.Delta Four.Echo Five"]}
    cw = derive_crosswalk(1, _codes(3), roster, releases)
    assert cw.mapping == {"S01E03": "S01E04-E05"}
    assert cw.sources == {"release_title": 1, "air_date_gap": 0, "unmapped": 2}


def test_anchor_naming_part_of_a_group_expands_to_the_whole_group():
    roster = _pairs_roster(2)
    releases = {2: ["S01E02.Bravo Segment 4"]}  # only the second segment's title
    cw = derive_crosswalk(1, _codes(2), roster, releases)
    assert cw.mapping["S01E02"] == "S01E03-E04"


def test_missing_slot_inside_gap_keeps_neighbours_aligned():
    roster = _pairs_roster(4)
    codes = ["S01E01", "S01E02", "S01E04"]  # half-hour 3 has no subtitle in the pack
    cw = derive_crosswalk(1, codes, roster, {})
    assert cw.mapping == {
        "S01E01": "S01E01-E02",
        "S01E02": "S01E03-E04",
        "S01E04": "S01E07-E08",
    }


def test_no_releases_and_no_air_dates_maps_nothing():
    roster = [_ep(n, f"Segment {n}") for n in range(1, 7)]
    cw = derive_crosswalk(1, _codes(3), roster, None)
    assert cw.mapping == {}
    assert cw.sources == {"release_title": 0, "air_date_gap": 0, "unmapped": 3}


# --------------------------------------------------------------------------- applying


def _marker(crosswalk):
    return {"scheme": "divergent", "roster_size": 37, "crosswalk": crosswalk}


def test_crosswalk_applies_only_to_divergent_with_entries():
    assert crosswalk_applies(_marker({"S02E06": "S02E11-E12"})) is True
    assert crosswalk_applies(_marker({})) is False
    assert crosswalk_applies({"scheme": "tmdb_aired", "crosswalk": {"S02E06": "S02E11"}}) is False
    assert crosswalk_applies(None) is False


def test_translate_returns_none_without_crosswalk():
    assert translate_codes(["S02E06"], {"scheme": "divergent", "roster_size": 37}) is None
    assert translate_codes(["S02E06"], None) is None


def test_translate_drops_unmapped():
    marker = _marker({"S02E06": "S02E11-E12", "S02E07": "S02E13"})
    assert translate_codes(["S02E06", "S02E07", "S02E19"], marker) == [
        "S02E11-E12",
        "S02E13",
        None,
    ]


def test_all_unmapped_crosswalk_is_treated_as_absent():
    marker = _marker({"S02E99": "S02E36-E37"})  # key not in this index
    assert translate_codes(["S02E06", "S02E07"], marker) is None


# --------------------------------------------------------------------------- segmented


@pytest.mark.parametrize("name", ["hey_arnold_s2", "dexters_lab_s1", "hey_arnold_s3"])
def test_segment_format_seasons_are_segmented(name):
    assert is_segmented_season(_load(name).roster) is True


def _weekly(n, runtime=44, double_premiere=False):
    roster = []
    for i in range(1, n + 1):
        air = f"2010-01-{i:02d}"
        if double_premiere and i == 2:
            air = "2010-01-01"
        roster.append(_ep(i, f"Drama Episode {i}", air, runtime))
    return roster


def test_weekly_drama_is_not_segmented():
    assert is_segmented_season(_weekly(13)) is False


def test_double_premiere_drama_is_not_segmented():
    assert is_segmented_season(_weekly(13, double_premiere=True)) is False


def test_sitcom_airing_two_a_night_is_not_segmented():
    # Groups of two, but 22-minute episodes: two broadcasts, not two segments.
    roster = []
    for i in range(6):
        air = f"2010-02-{i + 1:02d}"
        roster += [_ep(2 * i + 1, f"Sitcom {2 * i + 1}", air, 22)]
        roster += [_ep(2 * i + 2, f"Sitcom {2 * i + 2}", air, 22)]
    assert is_segmented_season(roster) is False


def test_unknown_runtimes_are_not_segmented():
    roster = [_ep(n, f"Seg {n}", f"d{(n + 1) // 2}", 0) for n in range(1, 13)]
    assert is_segmented_season(roster) is False


def test_double_premiere_with_a_missing_episode_derives_nothing_once_gated():
    # The reviewer's probe: a same-day two-part premiere (62 + 44 minutes) and a
    # pack missing E13. Air-date gap fill alone would shift every track by one;
    # the segmented gate is what keeps such a season out of derivation.
    roster = _weekly(13)
    roster[0] = _ep(1, "Pilot Part One", "2010-01-01", 62)
    roster[1] = _ep(2, "Pilot Part Two", "2010-01-01", 44)
    assert is_segmented_season(roster) is False


def test_non_adjacent_inverted_anchors_are_all_dropped():
    # The reviewer's probe: anchors 1->E5, 2->E6, 3->E1, 4->E2. Checking only
    # neighbours flags (2, 3) and keeps 1->E5 and 4->E2, which are still inverted.
    names = ["Alpha One", "Bravo Two", "Charlie Three", "Delta Four", "Echo Five", "Foxtrot Six"]
    roster = [_ep(n, name, f"d{n}") for n, name in enumerate(names, start=1)]
    releases = {
        1: ["S01E01.Echo Five"],
        2: ["S01E02.Foxtrot Six"],
        3: ["S01E03.Alpha One"],
        4: ["S01E04.Bravo Two"],
    }
    cw = derive_crosswalk(1, _codes(4), roster, releases)
    assert cw.sources["release_title"] == 0
    assert cw.mapping == {}
