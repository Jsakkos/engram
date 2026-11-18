"""Tests for TheDiscDB classifier module."""

import re
from unittest.mock import patch

import pytest

from app.core.analyst import TitleInfo
from app.core.discdb_classifier import (
    HASH_LOOKUP_QUERY,
    NAME_SEARCH_QUERY,
    DiscDbSignal,
    DiscDbTitleMapping,
    _find_best_disc_by_durations,
    _find_matching_disc,
    _parse_discdb_episodes,
    _parse_duration,
    _parse_name_from_label,
    bind_mappings_to_scan,
    classify_from_discdb,
)
from app.models.disc_job import ContentType


class TestHashLookupQuery:
    """Guard against TheDiscDB schema drift: the hash filter must traverse the
    Release→Disc junction via ``disc.contentHash`` (the junction's own is unmapped
    and throws server-side). The mocked classify tests can't catch query drift."""

    def test_filters_through_disc_junction(self):
        normalized = re.sub(r"\s+", " ", HASH_LOOKUP_QUERY)
        assert (
            "releases: { some: { discs: { some: { disc: { contentHash: { eq: $hash } } } } } }"
            in normalized
        )


class TestParseDuration:
    def test_hhmmss(self):
        assert _parse_duration("1:13:14") == 4394

    def test_mmss(self):
        assert _parse_duration("52:01") == 3121

    def test_zero(self):
        assert _parse_duration("0:00:00") == 0

    def test_short(self):
        assert _parse_duration("0:02:41") == 161


class TestParseNameFromLabel:
    def test_basic_tv(self):
        assert _parse_name_from_label("BAND_OF_BROTHERS_S1D1") == "Band Of Brothers"

    def test_basic_movie(self):
        assert _parse_name_from_label("INCEPTION_2010") == "Inception"

    def test_disc_suffix(self):
        assert _parse_name_from_label("THE_OFFICE_DISC2") == "The Office"

    def test_season_only(self):
        assert _parse_name_from_label("BREAKING_BAD_S3") == "Breaking Bad"

    def test_bluray_suffix(self):
        assert _parse_name_from_label("MOVIE_BLURAY") == "Movie"

    def test_empty(self):
        assert _parse_name_from_label("") is None

    def test_hyphens(self):
        assert _parse_name_from_label("THE-OFFICE-S1D1") == "The Office"


class TestFindMatchingDisc:
    def test_finds_matching_hash(self):
        nodes = [
            {
                "title": "Test",
                "releases": [
                    {
                        "discs": [
                            {"contentHash": "ABC123", "slug": "S01D01"},
                            {"contentHash": "DEF456", "slug": "S01D02"},
                        ]
                    }
                ],
            }
        ]
        result = _find_matching_disc(nodes, "abc123")
        assert result is not None
        node, disc = result
        assert disc["slug"] == "S01D01"

    def test_no_match(self):
        nodes = [
            {
                "title": "Test",
                "releases": [{"discs": [{"contentHash": "ABC123", "slug": "S01D01"}]}],
            }
        ]
        assert _find_matching_disc(nodes, "ZZZZZ") is None


class TestFindBestDiscByDurations:
    def test_exact_match(self):
        titles = [
            TitleInfo(index=0, duration_seconds=4394, size_bytes=100, chapter_count=10),
            TitleInfo(index=1, duration_seconds=3121, size_bytes=100, chapter_count=10),
        ]
        nodes = [
            {
                "releases": [
                    {
                        "discs": [
                            {
                                "slug": "S01D01",
                                "titles": [
                                    {"index": 0, "duration": "1:13:14", "size": 100},
                                    {"index": 1, "duration": "0:52:01", "size": 100},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
        result = _find_best_disc_by_durations(nodes, titles)
        assert result is not None
        _, disc, score = result
        assert disc["slug"] == "S01D01"
        assert score > 0.99

    def test_no_match_different_count(self):
        titles = [
            TitleInfo(index=0, duration_seconds=4394, size_bytes=100, chapter_count=10),
        ]
        nodes = [
            {
                "releases": [
                    {
                        "discs": [
                            {
                                "slug": "S01D01",
                                "titles": [
                                    {"index": 0, "duration": "1:13:14", "size": 100},
                                    {"index": 1, "duration": "0:52:01", "size": 100},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
        assert _find_best_disc_by_durations(nodes, titles) is None

    def test_empty_titles(self):
        assert _find_best_disc_by_durations([], []) is None


class TestClassifyFromDiscdb:
    @patch("app.core.discdb_classifier._graphql_request")
    def test_hash_match(self, mock_request):
        """ContentHash lookup returns a match."""
        mock_request.return_value = {
            "mediaItems": {
                "nodes": [
                    {
                        "title": "Band of Brothers",
                        "type": "Series",
                        "year": 2001,
                        "slug": "band-of-brothers-2001",
                        "externalids": {"tmdb": "4613", "imdb": "tt0185906"},
                        "releases": [
                            {
                                "slug": "2015-blu-ray",
                                "discs": [
                                    {
                                        "contentHash": "D7CAB58DAC87C58C46FDA35A33759839",
                                        "slug": "S01D01",
                                        "titles": [
                                            {
                                                "index": 0,
                                                "duration": "1:13:14",
                                                "size": 18405949440,
                                                "item": {
                                                    "title": "Currahee",
                                                    "type": "Episode",
                                                    "season": "1",
                                                    "episode": "1",
                                                },
                                            },
                                            {
                                                "index": 1,
                                                "duration": "0:52:01",
                                                "size": 12947779584,
                                                "item": {
                                                    "title": "Day of Days",
                                                    "type": "Episode",
                                                    "season": "1",
                                                    "episode": "2",
                                                },
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }

        titles = [
            TitleInfo(index=0, duration_seconds=4394, size_bytes=18405949440, chapter_count=10),
            TitleInfo(index=1, duration_seconds=3121, size_bytes=12947779584, chapter_count=10),
        ]
        signal = classify_from_discdb(
            "BAND_OF_BROTHERS_D1",
            titles,
            content_hash="D7CAB58DAC87C58C46FDA35A33759839",
        )

        assert signal is not None
        assert signal.content_type == ContentType.TV
        assert signal.confidence == 0.98
        assert signal.source == "hash_match"
        assert signal.matched_title == "Band of Brothers"
        assert len(signal.title_mappings) == 2
        assert signal.title_mappings[0].episode == 1
        assert signal.title_mappings[0].episode_title == "Currahee"
        assert signal.tmdb_id == 4613

    @patch("app.core.discdb_classifier._graphql_request")
    def test_no_results(self, mock_request):
        """No results from API."""
        mock_request.return_value = {"mediaItems": {"nodes": []}}
        titles = [TitleInfo(index=0, duration_seconds=100, size_bytes=100, chapter_count=1)]
        signal = classify_from_discdb("UNKNOWN_DISC", titles)
        assert signal is None

    @patch("app.core.discdb_classifier._graphql_request")
    def test_api_failure(self, mock_request):
        """API returns None (network error)."""
        mock_request.return_value = None
        titles = [TitleInfo(index=0, duration_seconds=100, size_bytes=100, chapter_count=1)]
        signal = classify_from_discdb("SOME_DISC", titles)
        assert signal is None

    @patch("app.core.discdb_classifier._graphql_request")
    def test_movie_hash_match(self, mock_request):
        """Movie disc identified by hash."""
        mock_request.return_value = {
            "mediaItems": {
                "nodes": [
                    {
                        "title": "Inception",
                        "type": "Movie",
                        "year": 2010,
                        "slug": "inception-2010",
                        "externalids": {"tmdb": "27205", "imdb": "tt1375666"},
                        "releases": [
                            {
                                "slug": "2010-blu-ray",
                                "discs": [
                                    {
                                        "contentHash": "AABBCCDD",
                                        "slug": "D01",
                                        "titles": [
                                            {
                                                "index": 0,
                                                "duration": "2:28:10",
                                                "size": 40000000000,
                                                "item": {
                                                    "title": "Inception",
                                                    "type": "MainMovie",
                                                    "season": None,
                                                    "episode": None,
                                                },
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }

        titles = [
            TitleInfo(index=0, duration_seconds=8890, size_bytes=40000000000, chapter_count=20)
        ]
        signal = classify_from_discdb("INCEPTION_2010", titles, content_hash="AABBCCDD")

        assert signal is not None
        assert signal.content_type == ContentType.MOVIE
        assert signal.confidence == 0.98
        assert signal.title_mappings[0].title_type == "MainMovie"


class TestDiscDbTitleMapping:
    def test_episode_mapping(self):
        m = DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Currahee",
            season=1,
            episode=1,
            duration_seconds=4394,
        )
        assert m.season == 1
        assert m.episode == 1

    def test_extra_mapping(self):
        m = DiscDbTitleMapping(index=2, title_type="Extra")
        assert m.season is None
        assert m.episode is None


class TestDiscDbSignal:
    def test_repr(self):
        s = DiscDbSignal(
            content_type=ContentType.TV,
            confidence=0.98,
            matched_title="Test",
            source="hash_match",
        )
        assert "tv" in repr(s)
        assert "98%" in repr(s)


@pytest.mark.unit
class TestCombinedEpisodeMappings:
    """TheDiscDB records a combined title's whole claim in its episode field.

    A plain int() raised on "17-18", so those mappings used to arrive with no
    episode at all — the database knew the answer for exactly the tracks that are
    hardest to guess, and we threw it away.
    """

    def test_a_plain_number_is_one_episode(self):
        assert _parse_discdb_episodes("7") == [7]
        assert _parse_discdb_episodes(7) == [7]

    def test_a_range_expands_through_its_end(self):
        # "17-18" is Courage the Cowardly Dog S2D2's two-segment block.
        assert _parse_discdb_episodes("17-18") == [17, 18]
        assert _parse_discdb_episodes("5-7") == [5, 6, 7]

    def test_a_comma_list_keeps_every_part(self):
        assert _parse_discdb_episodes("1,3") == [1, 3]

    def test_a_mixed_list_from_real_data(self):
        # "22,24,23,25-28" appears verbatim in TheDiscDb/data.
        assert _parse_discdb_episodes("22,24,23,25-28") == [22, 24, 23, 25, 26, 27, 28]

    def test_the_ampersand_spelling(self):
        assert _parse_discdb_episodes("1 & 2 & 3") == [1, 2, 3]

    def test_missing_or_unparseable_is_empty(self):
        assert _parse_discdb_episodes(None) == []
        assert _parse_discdb_episodes("") == []
        assert _parse_discdb_episodes("Play All") == []

    def test_a_backwards_range_is_not_invented(self):
        assert _parse_discdb_episodes("9-3") == []

    def test_episode_stays_the_first_for_legacy_readers(self):
        mapping = DiscDbTitleMapping(index=0, title_type="Episode", season=2, episodes=[17, 18])
        assert mapping.episodes == [17, 18]
        # A mapping persisted before `episodes` existed still deserializes.
        legacy = DiscDbTitleMapping(index=0, title_type="Episode", season=1, episode=4)
        assert legacy.episodes == []
        assert legacy.episode == 4


def _discdb_title(index, duration, size, source_file, item=None, segment_map=None):
    return {
        "index": index,
        "duration": duration,
        "size": size,
        "sourceFile": source_file,
        "segmentMap": segment_map,
        "item": item,
    }


def _episode(season, episode, title):
    return {"title": title, "type": "Episode", "season": season, "episode": episode}


# Bluey "Season 2 First Half" Blu-ray, abridged from TheDiscDB (#912). TheDiscDB's
# scan kept the 11s and 104s titles; a MakeMKV with a 120s minimum drops them,
# so every later title is renumbered on the user's scan.
BLUEY_DISCDB_TITLES = [
    _discdb_title(0, "0:02:45", 551331840, "00002.mpls", segment_map="33,10"),
    _discdb_title(1, "0:00:11", 3938304, "00001.mpls", segment_map="0,5"),
    _discdb_title(2, "0:01:44", 45840384, "00035.m2ts", {"title": "Archaeology", "type": "Extra"}),
    _discdb_title(3, "0:07:20", 1936594944, "00029.m2ts", _episode(2, 25, "Helicopter")),
    _discdb_title(4, "0:07:20", 1936367616, "00028.m2ts", _episode(2, 24, "Flat Pack")),
    _discdb_title(5, "0:07:20", 1924632576, "00003.m2ts", _episode(2, 2, "Hammerbarn")),
    _discdb_title(6, "0:07:20", 1928011776, "00002.m2ts", _episode(2, 1, "Dance Mode")),
    _discdb_title(
        7, "0:02:35", 134301696, "00034.m2ts", {"title": "Green Bottles", "type": "Extra"}
    ),
]

# The same disc as a MakeMKV with minlength=120 numbers it.
BLUEY_SCAN = [
    TitleInfo(0, 165, 551331840, 0, source_filename="00002.mpls", segment_map="33,10"),
    TitleInfo(1, 440, 1936594944, 0, source_filename="00029.m2ts", segment_map="29"),
    TitleInfo(2, 440, 1936367616, 0, source_filename="00028.m2ts", segment_map="28"),
    TitleInfo(3, 440, 1924632576, 0, source_filename="00003.m2ts", segment_map="3"),
    TitleInfo(4, 440, 1928011776, 0, source_filename="00002.m2ts", segment_map="2"),
    TitleInfo(5, 155, 134301696, 0, source_filename="00034.m2ts", segment_map="34"),
]


def _bluey_response():
    return {
        "mediaItems": {
            "nodes": [
                {
                    "title": "Bluey",
                    "type": "Series",
                    "year": 2018,
                    "slug": "bluey-2018",
                    "externalids": {"tmdb": "82728", "imdb": None},
                    "releases": [
                        {
                            "slug": "2024-seasons-1-3-blu-ray",
                            "discs": [
                                {
                                    "contentHash": "D63B7179566956A498976D95E7271C1A",
                                    "slug": "season-2-first-half",
                                    "titles": BLUEY_DISCDB_TITLES,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }


def _ep_mapping(index, episode, duration, size=0):
    return DiscDbTitleMapping(
        index=index,
        title_type="Episode",
        season=1,
        episode=episode,
        episodes=[episode],
        duration_seconds=duration,
        size_bytes=size,
    )


@pytest.mark.unit
class TestMappingsBindToScannedTitles:
    """TheDiscDB's title index is ITS scan's index, not ours (#912).

    MakeMKV numbers titles after its minimum-length filter, so a user whose
    minimum drops a short title TheDiscDB kept sees every later title shifted.
    Pairing by raw index handed the Bluey disc's episodes to the wrong tracks
    and called a 155s extra S02E02. Mappings must be re-bound to the scanned
    title they actually describe, and dropped when nothing on the disc fits.
    """

    @patch("app.core.discdb_classifier._graphql_request")
    def test_hash_match_rebinds_shifted_indices(self, mock_request):
        mock_request.return_value = _bluey_response()
        signal = classify_from_discdb(
            "Bluey S2 First Half",
            BLUEY_SCAN,
            content_hash="D63B7179566956A498976D95E7271C1A",
        )

        assert signal is not None
        by_index = {m.index: m for m in signal.title_mappings}
        assert by_index[1].episodes == [25]
        assert by_index[1].episode_title == "Helicopter"
        assert by_index[2].episodes == [24]
        assert by_index[3].episodes == [2]
        assert by_index[4].episodes == [1]
        # The 155s extra is Green Bottles, not the episode at TheDiscDB index 5.
        assert by_index[5].title_type == "Extra"
        assert by_index[5].episode is None
        assert by_index[0].title_type == ""
        # Titles our scan never produced (11s, 104s) have nothing to bind to.
        assert len(signal.title_mappings) == len(BLUEY_SCAN)

    def test_a_title_that_fits_nothing_is_dropped(self):
        scan = [TitleInfo(0, 2600, 5_000_000_000, 0)]
        assert bind_mappings_to_scan([_ep_mapping(0, 1, 1300)], scan) == []

    def test_aligned_disc_without_source_files_keeps_its_index(self):
        # A staging import carries no source file and its sizes are the MKV's,
        # not TheDiscDB's: identical durations then fall back to the index.
        mappings = [_ep_mapping(i, i + 1, 1320, 9_000_000_000) for i in range(3)]
        scan = [TitleInfo(i, 1320, 700_000_000, 0) for i in range(3)]
        bound = bind_mappings_to_scan(mappings, scan)
        assert [(m.index, m.episode) for m in bound] == [(0, 1), (1, 2), (2, 3)]

    def test_exact_size_separates_equal_durations(self):
        scan = [TitleInfo(0, 1320, 2_222_222_222, 0), TitleInfo(1, 1320, 1_111_111_111, 0)]
        bound = bind_mappings_to_scan([_ep_mapping(5, 9, 1320, 1_111_111_111)], scan)
        assert [(m.index, m.episode) for m in bound] == [(1, 9)]

    def test_ambiguous_title_is_dropped_not_guessed(self):
        scan = [TitleInfo(0, 1320, 1, 0), TitleInfo(1, 1320, 2, 0)]
        assert bind_mappings_to_scan([_ep_mapping(7, 3, 1320)], scan) == []

    def test_a_scanned_title_is_claimed_once(self):
        scan = [TitleInfo(0, 600, 1, 0)]
        bound = bind_mappings_to_scan([_ep_mapping(0, 1, 600), _ep_mapping(1, 2, 600)], scan)
        assert [(m.index, m.episode) for m in bound] == [(0, 1)]

    def test_persisted_mapping_without_source_fields_still_loads(self):
        legacy = {
            "index": 2,
            "title_type": "Episode",
            "episode_title": "x",
            "season": 1,
            "episode": 2,
            "episodes": [2],
            "duration_seconds": 1300,
            "size_bytes": 0,
            "source": "discdb",
        }
        m = DiscDbTitleMapping(**legacy)
        assert m.source_file == ""
        assert m.segment_map == ""

    def test_queries_ask_for_source_file_and_segment_map(self):
        for query in (HASH_LOOKUP_QUERY, NAME_SEARCH_QUERY):
            assert "sourceFile" in query
            assert "segmentMap" in query
