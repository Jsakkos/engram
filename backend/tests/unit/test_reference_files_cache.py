"""get_reference_files must not cache an empty corpus (#370).

Job 3 (Eureka D3): the subtitle download never ran, the first lookup cached
[], and every later probe logged "Returning cached reference files" followed
by the no-references ERROR — even after subtitles could have been retried.
An empty result must stay a cache miss so late-arriving references become
visible to re-matches within the same process.
"""

import pytest

from app.matcher.episode_identification import EpisodeMatcher
from app.matcher.subtitle_utils import corpus_dir_name


def _matcher(tmp_path):
    """Minimal EpisodeMatcher carrying only what get_reference_files reads.

    __new__ skips the heavyweight __init__ (model registry, config, TMDB).
    """
    m = EpisodeMatcher.__new__(EpisodeMatcher)
    m.cache_dir = tmp_path
    m.show_name = "Eureka"
    m.expected_tmdb_id = 4620
    m.reference_files_cache = {}
    return m


def _add_reference(tmp_path, filename):
    ref_dir = tmp_path / "data" / corpus_dir_name(4620, "Eureka")
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / filename).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")


@pytest.mark.unit
class TestEmptyReferenceCacheNotPoisoned:
    def test_empty_result_is_not_cached(self, tmp_path):
        m = _matcher(tmp_path)
        assert m.get_reference_files(1) == []
        assert m.reference_files_cache == {}

    def test_late_arriving_references_are_picked_up(self, tmp_path):
        m = _matcher(tmp_path)
        assert m.get_reference_files(1) == []  # nothing yet — must not poison

        _add_reference(tmp_path, "Eureka - S01E01.srt")

        files = m.get_reference_files(1)
        assert [f.name for f in files] == ["Eureka - S01E01.srt"]

    def test_non_empty_result_is_cached(self, tmp_path):
        m = _matcher(tmp_path)
        _add_reference(tmp_path, "Eureka - S01E01.srt")

        first = m.get_reference_files(1)
        assert len(first) == 1
        assert m.reference_files_cache[("Eureka", 1)] == first
