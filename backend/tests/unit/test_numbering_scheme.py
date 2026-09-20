"""The single definition of 'is this season numbered the way TMDB numbers it'.

Two builder scripts write the marker and the runtime reads it, so the
classification has to live in one place or the writer and the reader drift.
These tests pin the vocabulary and, in particular, the unknown-vs-divergent
boundary: fetch_season_details returns 0 for its no-key and transient-failure
paths, and a TMDB outage during a nightly build must not brand healthy seasons
as divergent.
"""

import pytest

from app.matcher.numbering_scheme import (
    SCHEME_DIVERGENT,
    SCHEME_TMDB_AIRED,
    SCHEME_UNKNOWN,
    VALID_SCHEMES,
    derive_numbering_scheme,
)


@pytest.mark.unit
class TestDeriveNumberingScheme:
    def test_equal_counts_are_tmdb_aired(self):
        assert derive_numbering_scheme(13, 13) == SCHEME_TMDB_AIRED

    def test_dexters_laboratory_season_one_is_divergent(self):
        # 13 harvested broadcast half-hours against a 38-entry segment roster.
        assert derive_numbering_scheme(13, 38) == SCHEME_DIVERGENT

    def test_corpus_larger_than_roster_is_also_divergent(self):
        assert derive_numbering_scheme(40, 36) == SCHEME_DIVERGENT

    @pytest.mark.parametrize(
        "reference_count,roster_size",
        [
            (13, 0),  # fetch_season_details no-key / transient-failure contract
            (0, 38),
            (13, None),
            (None, 38),
            (None, None),
            (13, -1),
            (-1, 38),
            (13, "38"),
            ("13", 38),
            (13, 38.0),
            (13, True),
            (True, 38),
        ],
    )
    def test_unusable_inputs_are_unknown(self, reference_count, roster_size):
        assert derive_numbering_scheme(reference_count, roster_size) == SCHEME_UNKNOWN

    def test_every_returned_value_is_a_valid_scheme(self):
        for pair in [(13, 13), (13, 38), (13, 0), (None, None)]:
            assert derive_numbering_scheme(*pair) in VALID_SCHEMES

    def test_tvdb_is_not_a_scheme(self):
        # TheTVDB numbers Dexter's Laboratory by segment in both its official
        # (38/108/36/38) and DVD (39 for season 1) orders, so the corpus's
        # 13/40/13/13 is not TVDB numbering. Recording it as such would put a
        # false provenance into a published artifact.
        assert "tvdb" not in VALID_SCHEMES
