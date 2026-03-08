"""Tests for TheDiscDB classifier module."""

from unittest.mock import patch

from app.core.analyst import TitleInfo
from app.core.discdb_classifier import (
    DiscDbSignal,
    DiscDbTitleMapping,
    _find_best_disc_by_durations,
    _find_matching_disc,
    _parse_duration,
    _parse_name_from_label,
    classify_from_discdb,
)
from app.models.disc_job import ContentType


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
