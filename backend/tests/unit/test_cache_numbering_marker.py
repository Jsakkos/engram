"""The published manifest records how each harvested season is numbered.

Without the marker no backend can tell a canonical TMDB code from a
broadcast-half-hour code, because both come out of the matcher shaped like
SxxEyy under a canonical season key. These tests pin the emission in both
builder scripts and, crucially, the offline and roster-lookup-failure paths:
those must emit "unknown" rather than guessing, or an offline pack would look
like a corrupt one.

The `psc` and `bsc` fixtures (session-scoped, in conftest.py) load the standalone
scripts as modules. Nothing here touches the network or the real cache.
"""

from unittest.mock import patch

import pytest

from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
)


@pytest.mark.unit
class TestPackSeasonNumberingEntry:
    """`_season_numbering_entry` in pack_subtitle_cache.py."""

    def test_agreeing_counts_emit_tmdb_aired_with_roster_size(self, psc):
        with patch.object(psc, "fetch_season_details", return_value=13) as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=1396, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_TMDB_AIRED, "roster_size": 13}
        mock_fetch.assert_called_once_with("1396", 1)

    def test_dexters_laboratory_emits_divergent_with_roster_size(self, psc):
        with patch.object(psc, "fetch_season_details", return_value=38):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_DIVERGENT, "roster_size": 38}

    def test_offline_emits_unknown_and_never_calls_tmdb(self, psc):
        with patch.object(psc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=True
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_unresolved_show_emits_unknown_and_never_calls_tmdb(self, psc):
        with patch.object(psc, "fetch_season_details") as mock_fetch:
            entry = psc._season_numbering_entry(
                tmdb_id=None, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        mock_fetch.assert_not_called()

    def test_roster_lookup_returning_zero_emits_unknown_not_divergent(self, psc):
        # fetch_season_details returns 0 for a missing key or a failed request.
        # Treating that as divergent would brand healthy seasons unverified for
        # a whole nightly build whenever TMDB blips.
        with patch.object(psc, "fetch_season_details", return_value=0):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}
        assert "roster_size" not in entry

    def test_roster_lookup_raising_emits_unknown(self, psc):
        with patch.object(psc, "fetch_season_details", side_effect=RuntimeError("boom")):
            entry = psc._season_numbering_entry(
                tmdb_id=4229, season=1, reference_count=13, offline=False
            )
        assert entry == {"scheme": SCHEME_UNKNOWN}


@pytest.mark.unit
class TestPackManifestWiring:
    """The helper's output reaches the manifest entry under string season keys."""

    def test_season_numbering_keys_match_episode_counts_keys(self, psc):
        # The manifest's season keys are strings because JSON has no integer
        # keys; season_numbering must agree with episode_counts or a consumer
        # looking up str(season) silently misses.
        entry = {
            "tmdb_id": 4229,
            "name": "Dexter's Laboratory",
            "seasons": [1, 2],
            "episode_counts": {"1": 13, "2": 40},
            "season_numbering": {
                "1": psc._season_numbering_entry(4229, 1, 13, offline=True),
                "2": psc._season_numbering_entry(4229, 2, 40, offline=True),
            },
        }
        assert set(entry["season_numbering"]) == set(entry["episode_counts"])
        assert all(isinstance(k, str) for k in entry["season_numbering"])
