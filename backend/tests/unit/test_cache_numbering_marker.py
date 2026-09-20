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
from unittest.mock import patch

import pytest

from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
)


@pytest.mark.unit
class TestSeasonNumberingEntry:
    """The shared implementation, which lives in build_subtitle_cache.py."""

    def test_agreeing_counts_emit_tmdb_aired_with_roster_size(self, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=13) as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=1396, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_TMDB_AIRED, "roster_size": 13}
        mock_fetch.assert_called_once_with("1396", 1)

    def test_dexters_laboratory_emits_divergent_with_roster_size(self, bsc):
        # 13 harvested broadcast half-hours against a 38-entry segment roster.
        with patch.object(bsc, "fetch_season_details", return_value=38):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_missing_tmdb_id_emits_unknown_without_calling_tmdb(self, bsc):
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = bsc._season_numbering_entry(tmdb_id=None, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_roster_lookup_returning_zero_emits_unknown_not_divergent(self, bsc):
        # fetch_season_details returns 0 for a missing key or a failed request.
        # Treating that as divergent would brand healthy seasons unverified for
        # a whole nightly build whenever TMDB blips.
        with patch.object(bsc, "fetch_season_details", return_value=0):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry

    def test_roster_lookup_raising_emits_unknown(self, bsc):
        with patch.object(bsc, "fetch_season_details", side_effect=RuntimeError("boom")):
            entry = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
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
                tmdb_id=4229, season=1, reference_count=13, offline=True
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry
        mock_fetch.assert_not_called()

    def test_online_delegates_to_the_shared_implementation(self, psc, bsc):
        with patch.object(bsc, "fetch_season_details", return_value=38) as mock_fetch:
            packed = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
            built = bsc._season_numbering_entry(tmdb_id=4229, season=1, reference_count=13)
        assert packed == built
        assert packed == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}
        assert mock_fetch.call_count == 2

    def test_unresolved_show_still_reaches_the_shared_unknown_path(self, psc, bsc):
        # Pack's live unresolved-show case: a disk dir TMDB could not match.
        with patch.object(bsc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=None, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()


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
class TestMatchStatsStamping:
    """The marker reaches match_details, which is what consumers read.

    Exercised at the dict level rather than by driving identify_episode end to
    end (that needs audio, ffmpeg and a real vector corpus). The contract these
    pin is the shape numbering_schemes_agree consumes.
    """

    def _stamp(self, numbering):
        """Reproduce the stamping branch from identify_episode."""
        match_stats = {"reference_count": 13}
        if numbering:
            match_stats["numbering_scheme"] = numbering["scheme"]
            if isinstance(numbering.get("roster_size"), int):
                match_stats["pack_roster_size"] = numbering["roster_size"]
        return match_stats

    def test_marked_season_stamps_scheme_and_roster(self):
        stats = self._stamp({"scheme": SCHEME_DIVERGENT, "roster_size": 38})
        assert stats["numbering_scheme"] == SCHEME_DIVERGENT
        assert stats["pack_roster_size"] == 38

    def test_scraped_season_stamps_nothing(self):
        stats = self._stamp(None)
        assert "numbering_scheme" not in stats
        assert "pack_roster_size" not in stats

    def test_marker_without_roster_size_stamps_only_the_scheme(self):
        stats = self._stamp({"scheme": SCHEME_TMDB_AIRED})
        assert stats["numbering_scheme"] == SCHEME_TMDB_AIRED
        assert "pack_roster_size" not in stats
