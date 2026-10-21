"""The published manifest records how each harvested season is numbered.

Without the marker no backend can tell a canonical TMDB code from a
broadcast-half-hour code, because both come out of the matcher shaped like
SxxEyy under a canonical season key.

``build_subtitle_cache._season_numbering_entry`` is the single implementation
for both builder scripts; ``pack_subtitle_cache`` wraps it to add only its
``offline`` short-circuit. The two scripts publish to the same rolling release,
so the tests are split along that seam: the build class covers the shared
classification behaviour, the pack class covers only what pack adds. Neither
script gets its own copy of the logic to drift.

The roster-lookup paths get the most attention because they are where a wrong
answer is silent: a lookup that returns 0 (the no-key and transient-failure
contract of ``fetch_season_details``) must emit "unknown" rather than
"divergent", or a TMDB blip during a nightly build would brand healthy seasons
unverified for a whole release.

The `psc` and `bsc` fixtures (session-scoped, in conftest.py) load the standalone
scripts as modules. Nothing here touches the network or the real cache.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.matcher.numbering_crosswalk import RosterEpisode
from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "numbering_crosswalk"

_CODES13 = [f"S01E{n:02d}" for n in range(1, 14)]


@pytest.fixture(autouse=True)
def _no_crosswalk_network(bsc):
    """Keep the divergent-season crosswalk lookups off the network by default.

    A divergent marker now also fetches the season's TMDB roster and runs one
    OpenSubtitles search. Tests that exercise the crosswalk patch these again.
    """
    with (
        patch.object(bsc, "_season_roster", return_value=[]),
        patch.object(bsc, "season_release_titles", return_value=None),
    ):
        yield


def _fixture(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    roster = [RosterEpisode(**e) for e in data["roster"]]
    releases = {int(k): v for k, v in data["releases"].items()}
    return data["corpus_codes"], roster, releases


@pytest.mark.unit
class TestSeasonNumberingEntry:
    """The shared implementation, which lives in build_subtitle_cache.py."""

    def test_agreeing_counts_emit_tmdb_aired_with_roster_size(self, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=13) as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=1396, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_TMDB_AIRED, "roster_size": 13}
        mock_fetch.assert_called_once_with("1396", 1)

    def test_dexters_laboratory_emits_divergent_with_roster_size(self, bsc):
        # 13 harvested broadcast half-hours against a 38-entry segment roster.
        with patch.object(bsc, "fetch_season_details", return_value=38):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_missing_tmdb_id_emits_unknown_without_calling_tmdb(self, bsc):
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=None, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_roster_lookup_returning_zero_emits_unknown_not_divergent(self, bsc):
        # fetch_season_details returns 0 for a missing key or a failed request.
        # Treating that as divergent would brand healthy seasons unverified for
        # a whole nightly build whenever TMDB blips.
        with patch.object(bsc, "fetch_season_details", return_value=0):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry

    def test_roster_lookup_raising_emits_unknown(self, bsc):
        with patch.object(bsc, "fetch_season_details", side_effect=RuntimeError("boom")):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_UNKNOWN}


@pytest.mark.unit
class TestPackOfflineShortCircuit:
    """What pack_subtitle_cache adds on top: the --offline branch, and nothing else.

    Pack reads SRTs already on disk and can run with no TMDB at all, so it has an
    unknown path the build script does not. Everything past that branch is the
    shared implementation, so these tests assert delegation rather than
    re-deriving the classification: a second copy of those assertions would be
    the very drift the consolidation removed.
    """

    def test_offline_emits_unknown_and_never_looks_up_a_roster(self, psc, bsc):
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, codes=_CODES13, offline=True
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry
        mock_fetch.assert_not_called()

    def test_online_delegates_to_the_shared_implementation(self, psc, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=38) as mock_fetch:
            packed = psc._season_numbering_entry(
                tmdb_id=4229, season=1, codes=_CODES13, offline=False
            )
            built = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert packed == built
        assert packed == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}
        assert mock_fetch.call_count == 2

    def test_unresolved_show_still_reaches_the_shared_unknown_path(self, psc, bsc):
        # Pack's live unresolved-show case: a disk dir TMDB could not match.
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=None, season=1, codes=_CODES13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()


@pytest.mark.unit
class TestSeasonCrosswalk:
    """A divergent season carries the half-hour to segment crosswalk (#760)."""

    def test_divergent_season_carries_crosswalk(self, bsc):
        codes, roster, releases = _fixture("hey_arnold_s2")
        with (
            patch.object(bsc, "fetch_season_details", return_value=37),
            patch.object(bsc, "_season_roster", return_value=roster) as mock_roster,
            patch.object(bsc, "season_release_titles", return_value=releases) as mock_lookup,
        ):
            entry = bsc._season_numbering_entry(tmdb_id=537, season=2, codes=codes)
        assert entry["scheme"] == SCHEME_DIVERGENT
        assert entry["roster_size"] == 37
        assert entry["crosswalk"]["S02E06"] == "S02E11-E12"
        assert "S02E19" not in entry["crosswalk"]
        assert entry["crosswalk_sources"] == {
            "release_title": 18,
            "air_date_gap": 1,
            "unmapped": 1,
        }
        mock_roster.assert_called_once_with(537, 2)
        mock_lookup.assert_called_once_with("537", 2)

    def test_tmdb_aired_season_has_no_crosswalk(self, bsc):
        with (
            patch.object(bsc, "fetch_season_details", return_value=13),
            patch.object(bsc, "_season_roster") as mock_roster,
            patch.object(bsc, "season_release_titles") as mock_lookup,
        ):
            entry = bsc._season_numbering_entry(tmdb_id=1396, season=1, codes=_CODES13)
        assert "crosswalk" not in entry
        mock_roster.assert_not_called()
        mock_lookup.assert_not_called()

    def test_unknown_season_has_no_crosswalk_and_no_lookup(self, bsc):
        with (
            patch.object(bsc, "fetch_season_details", return_value=0),
            patch.object(bsc, "season_release_titles") as mock_lookup,
        ):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_lookup.assert_not_called()

    def test_offline_pack_never_calls_release_lookup(self, psc, bsc):
        with patch.object(bsc, "season_release_titles") as mock_lookup:
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, codes=_CODES13, offline=True
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_lookup.assert_not_called()

    def test_release_lookup_failure_falls_back_to_structure(self, bsc):
        codes, roster, _releases = _fixture("dexters_lab_s1")
        with (
            patch.object(bsc, "fetch_season_details", return_value=38),
            patch.object(bsc, "_season_roster", return_value=roster),
            patch.object(bsc, "season_release_titles", return_value=None),
        ):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=codes)
        assert len(entry["crosswalk"]) == 13
        assert entry["crosswalk"]["S01E13"] == "S01E36-E38"

    def test_release_lookup_exception_does_not_abort(self, bsc):
        codes, roster, _releases = _fixture("dexters_lab_s1")
        with (
            patch.object(bsc, "fetch_season_details", return_value=38),
            patch.object(bsc, "_season_roster", return_value=roster),
            patch.object(bsc, "season_release_titles", side_effect=RuntimeError("quota")),
        ):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=codes)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_roster_exception_does_not_abort(self, bsc):
        with (
            patch.object(bsc, "fetch_season_details", return_value=38),
            patch.object(bsc, "_season_roster", side_effect=RuntimeError("tmdb down")),
        ):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_empty_crosswalk_is_omitted(self, bsc):
        # No air dates and no releases: nothing can be mapped.
        roster = [RosterEpisode(n, f"Segment {n}", "", 7) for n in range(1, 39)]
        with (
            patch.object(bsc, "fetch_season_details", return_value=38),
            patch.object(bsc, "_season_roster", return_value=roster),
        ):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, codes=_CODES13)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}


@pytest.mark.unit
class TestSeasonReleaseTitles:
    """One OpenSubtitles search per divergent season; never a download."""

    @staticmethod
    def _sub(season, episode, release):
        return SimpleNamespace(season_number=season, episode_number=episode, release=release)

    @staticmethod
    def _passthrough(fn, *args, **kwargs):
        kwargs.pop("max_attempts", None)
        kwargs.pop("base_delay", None)
        return fn(*args, **kwargs)

    def test_release_titles_grouped_by_episode_and_season_filtered(self):
        from app.matcher import testing_service as ts

        response = SimpleNamespace(
            data=[
                self._sub(2, 6, "S02E06.Ransom.Ms.Perfect"),
                self._sub(2, 6, "Hey.Arnold.S02E06.DVDRip"),
                self._sub(2, 7, "S02E07.Arnold's Halloween"),
                self._sub(1, 6, "S01E06.Wrong.Season"),
                self._sub(2, None, "no episode number"),
            ]
        )
        calls = []

        def search(**kwargs):
            calls.append(kwargs)
            return response

        client = SimpleNamespace(search=search)
        with (
            patch.object(ts, "_os_client_and_reason", return_value=(client, None)),
            patch.object(ts, "os_api_call", side_effect=self._passthrough),
        ):
            result = ts.season_release_titles("537", 2)
        assert result == {
            6: ["S02E06.Ransom.Ms.Perfect", "Hey.Arnold.S02E06.DVDRip"],
            7: ["S02E07.Arnold's Halloween"],
        }
        assert calls == [
            {"parent_tmdb_id": "537", "season_number": 2, "languages": "en", "type": "episode"}
        ]

    def test_release_titles_none_without_client(self):
        from app.matcher import testing_service as ts

        with patch.object(ts, "_os_client_and_reason", return_value=(None, "no credentials")):
            assert ts.season_release_titles("537", 2) is None

    def test_release_titles_none_when_search_raises(self):
        from app.matcher import testing_service as ts

        def boom(**_kw):
            raise RuntimeError("429")

        client = SimpleNamespace(search=boom)
        with (
            patch.object(ts, "_os_client_and_reason", return_value=(client, None)),
            patch.object(ts, "os_api_call", side_effect=self._passthrough),
        ):
            assert ts.season_release_titles("537", 2) is None


def _write_manifest(cache_dir, shows):
    """Write a minimal valid precomputed manifest under ``cache_dir``."""
    from app.matcher.vectorizer_config import (
        CACHE_FORMAT_VERSION,
        HASHING_N_FEATURES,
        vectorizer_config_hash,
    )

    precomputed = cache_dir / "precomputed"
    precomputed.mkdir(parents=True, exist_ok=True)
    (precomputed / "manifest.json").write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "vectorizer_config_hash": vectorizer_config_hash(),
                "content_version": "test",
                "n_features": HASHING_N_FEATURES,
                "shows": shows,
            }
        ),
        encoding="utf-8",
    )


def _matcher(cache_dir, show_name="Dexter's Laboratory", tmdb_id=4229):
    from app.matcher.episode_identification import EpisodeMatcher

    m = EpisodeMatcher.__new__(EpisodeMatcher)
    m.cache_dir = cache_dir
    m.show_name = show_name
    m.expected_tmdb_id = tmdb_id
    m._precomputed_manifest = None
    m._precomputed_idf = None
    return m


_DEXTER_SHOWS = {
    "4229": {
        "tmdb_id": 4229,
        "name": "Dexter's Laboratory",
        "seasons": [1, 2],
        "episode_counts": {"1": 13, "2": 40},
        "season_numbering": {
            "1": {"scheme": SCHEME_DIVERGENT, "roster_size": 38},
            "2": {"scheme": SCHEME_TMDB_AIRED, "roster_size": 40},
        },
    }
}


@pytest.mark.unit
class TestPrecomputedNumbering:
    """`EpisodeMatcher.precomputed_numbering` reads the marker out of the manifest.

    It returns None for anything a caller must not treat as an answer, so the
    caller stamps nothing and the runtime heuristic stays in charge.
    """

    def test_divergent_season_returns_its_marker(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(1) == {
            "scheme": SCHEME_DIVERGENT,
            "roster_size": 38,
        }

    def test_agreeing_season_returns_its_marker(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(2) == {
            "scheme": SCHEME_TMDB_AIRED,
            "roster_size": 40,
        }

    def test_season_absent_from_the_marker_returns_none(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        assert _matcher(tmp_path).precomputed_numbering(3) is None

    def test_pack_predating_the_marker_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_unknown_scheme_returns_none_so_the_heuristic_keeps_control(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": {"scheme": SCHEME_UNKNOWN}},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_garbage_scheme_value_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": {"scheme": "tvdb", "roster_size": 38}},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_non_dict_marker_returns_none(self, tmp_path):
        shows = {
            "4229": {
                "tmdb_id": 4229,
                "name": "Dexter's Laboratory",
                "seasons": [1],
                "episode_counts": {"1": 13},
                "season_numbering": {"1": "divergent"},
            }
        }
        _write_manifest(tmp_path, shows)
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_no_manifest_at_all_returns_none(self, tmp_path):
        assert _matcher(tmp_path).precomputed_numbering(1) is None

    def test_unknown_show_returns_none(self, tmp_path):
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        matcher = _matcher(tmp_path, show_name="Some Other Show", tmdb_id=99999)
        assert matcher.precomputed_numbering(1) is None

    def test_marker_is_not_cached_on_the_instance(self, tmp_path):
        # The matcher singleton is shared across concurrent identify_episode
        # threads, so a season-scoped value in an instance slot would be
        # clobbered by a sibling thread's scan. Two different seasons must give
        # two different answers from the same instance, in either order.
        _write_manifest(tmp_path, _DEXTER_SHOWS)
        matcher = _matcher(tmp_path)
        assert matcher.precomputed_numbering(1)["scheme"] == SCHEME_DIVERGENT
        assert matcher.precomputed_numbering(2)["scheme"] == SCHEME_TMDB_AIRED
        assert matcher.precomputed_numbering(1)["scheme"] == SCHEME_DIVERGENT
        assert not any("numbering" in a for a in vars(matcher))


@pytest.mark.unit
class TestStampNumbering:
    """The marker reaches match_details, which is what consumers read.

    Calls the real ``stamp_numbering`` that ``identify_episode`` calls. It was
    pulled out to module level precisely so a test could reach it: driving
    ``identify_episode`` needs audio, ffmpeg and a real vector corpus, and a
    test that re-implemented the branch would be free to drift from it.
    """

    def test_marked_season_stamps_scheme_and_roster(self):
        from app.matcher.episode_identification import stamp_numbering

        stats = {"reference_count": 13}
        stamp_numbering(stats, {"scheme": SCHEME_DIVERGENT, "roster_size": 38})
        assert stats["numbering_scheme"] == SCHEME_DIVERGENT
        assert stats["pack_roster_size"] == 38

    def test_scraped_season_stamps_nothing(self):
        from app.matcher.episode_identification import stamp_numbering

        stats = {"reference_count": 13}
        stamp_numbering(stats, None)
        assert stats == {"reference_count": 13}

    def test_marker_without_roster_size_stamps_only_the_scheme(self):
        from app.matcher.episode_identification import stamp_numbering

        stats = {"reference_count": 13}
        stamp_numbering(stats, {"scheme": SCHEME_TMDB_AIRED})
        assert stats["numbering_scheme"] == SCHEME_TMDB_AIRED
        assert "pack_roster_size" not in stats

    def test_a_boolean_roster_size_is_not_stamped(self):
        # bool is an int subclass, so a naive isinstance check would stamp
        # True as a roster of 1. The shared usable_count predicate excludes it.
        from app.matcher.episode_identification import stamp_numbering

        stats = {"reference_count": 13}
        stamp_numbering(stats, {"scheme": SCHEME_TMDB_AIRED, "roster_size": True})
        assert "pack_roster_size" not in stats

    def test_a_zero_roster_size_is_not_stamped(self):
        from app.matcher.episode_identification import stamp_numbering

        stats = {"reference_count": 13}
        stamp_numbering(stats, {"scheme": SCHEME_DIVERGENT, "roster_size": 0})
        assert "pack_roster_size" not in stats
