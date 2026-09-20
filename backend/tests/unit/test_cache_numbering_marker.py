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
