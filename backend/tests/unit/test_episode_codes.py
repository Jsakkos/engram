"""Parsing/formatting of episode codes, single and combined.

A combined code ("S01E01-E02-E03") represents ONE file holding several episodes —
what a segment-format show's DVD track actually contains. The whole point of the
anchored parser is that such a code can never be silently read as its first
episode alone, which is how a three-segment block would end up filed as S01E01
and two episodes would vanish from the library.
"""

import pytest

from app.core.episode_codes import (
    episode_parts,
    first_episode,
    format_episode_code,
    is_multi_episode,
    normalize_episode_code,
    parse_episode_code,
)


@pytest.mark.unit
class TestParseEpisodeCode:
    def test_single_episode(self):
        assert parse_episode_code("S01E07") == (1, [7])

    def test_unpadded_matcher_fallback_form(self):
        assert parse_episode_code("S1E14") == (1, [14])

    def test_hyphen_is_a_range(self):
        # Plex/Jellyfin semantics, and what hand-organized libraries mean by it:
        # E01 THROUGH E03, not "E01 and E03".
        assert parse_episode_code("S01E01-E03") == (1, [1, 2, 3])

    def test_chained_hyphens_are_still_a_run(self):
        assert parse_episode_code("S01E01-E02-E03") == (1, [1, 2, 3])

    def test_run_on_is_a_set_not_a_range(self):
        assert parse_episode_code("S01E01E03") == (1, [1, 3])
        assert parse_episode_code("S01E01E02") == (1, [1, 2])

    def test_backwards_range_is_not_inverted(self):
        assert parse_episode_code("S01E05-E02") == (1, [5, 2])

    def test_lowercase_and_surrounding_space(self):
        assert parse_episode_code(" s02e05 ") == (2, [5])

    def test_duplicate_parts_collapse(self):
        assert parse_episode_code("S01E02-E02") == (1, [2])

    @pytest.mark.parametrize("code", [None, "", "extra", "skip", "Season 1", "S01", "E01"])
    def test_non_codes_return_none(self, code):
        assert parse_episode_code(code) is None

    def test_trailing_junk_is_not_a_code(self):
        # Anchored: a prefix match would silently accept a corrupted value.
        assert parse_episode_code("S01E01 extra") is None


@pytest.mark.unit
class TestNormalizeAndFormat:
    def test_padding_variants_collapse_to_one_key(self):
        assert normalize_episode_code("S1E3") == normalize_episode_code("S01E03") == "S01E03"

    def test_multi_spellings_collapse_to_one_key(self):
        # A contiguous pair is a range however it was spelled.
        assert normalize_episode_code("S01E01E02") == "S01E01-E02"
        assert normalize_episode_code("s1e1-e2") == "S01E01-E02"
        assert normalize_episode_code("S01E01-E02-E03") == "S01E01-E03"

    def test_gapped_set_keeps_the_run_on_form(self):
        # "S01E01-E03" would claim E02, which the track doesn't contain.
        assert normalize_episode_code("S01E01E03") == "S01E01E03"

    def test_pseudo_codes_pass_through_uppercased(self):
        assert normalize_episode_code("extra") == "EXTRA"
        assert normalize_episode_code(None) == ""

    def test_format_round_trips(self):
        assert format_episode_code(1, [1, 2, 3]) == "S01E01-E03"
        assert format_episode_code(1, [1, 3]) == "S01E01E03"
        for episodes in ([7], [1, 2, 3], [1, 3], [2, 4, 5]):
            assert parse_episode_code(format_episode_code(12, episodes)) == (12, episodes)

    def test_format_rejects_an_empty_episode_list(self):
        with pytest.raises(ValueError):
            format_episode_code(1, [])


@pytest.mark.unit
class TestEpisodeParts:
    def test_combined_code_occupies_every_slot(self):
        assert episode_parts("S01E01-E03") == ["S01E01", "S01E02", "S01E03"]
        assert episode_parts("S01E01E03") == ["S01E01", "S01E03"]

    def test_single_code_is_its_own_only_part(self):
        assert episode_parts("S03E11") == ["S03E11"]

    def test_non_code_has_no_parts(self):
        assert episode_parts("extra") == []

    def test_is_multi_episode(self):
        assert is_multi_episode("S01E01-E02") is True
        assert is_multi_episode("S01E01") is False
        assert is_multi_episode("extra") is False

    def test_first_episode_for_single_episode_consumers(self):
        assert first_episode("S01E01-E03") == (1, 1)
        assert first_episode("extra") is None
