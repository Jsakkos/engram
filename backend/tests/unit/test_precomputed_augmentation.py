"""Runtime augmentation of the precomputed cache with downloaded gap SRTs.

The shipped precomputed cache can be incomplete (Mad Men S02 shipped without
S02E05). When the missing episode's SRT is later fetched to disk, the matcher
must graft it into the in-memory reference set — vectorized identically to the
build script (get_full_text -> transform_query) so it lands in the same feature
space — otherwise precomputed mode reads only the vectors and ignores the SRT.
"""

import json

import numpy as np
from scipy import sparse

from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

E1 = "detective solves the murder in the old mansion at midnight"
E2 = "the spaceship crew explores a distant alien planet far from earth"
E3 = "a chef cooks an elaborate pasta dinner in a small italian kitchen"


def _write_two_episode_cache(tmp_path, tmdb_id=1, show="Test Show"):
    """Precomputed cache covering only E01 and E02 — E03 is the shipped gap."""
    precomputed = tmp_path / "precomputed"
    show_dir = precomputed / str(tmdb_id)
    show_dir.mkdir(parents=True)

    counts = build_hashing_vectorizer().transform([E1, E2])
    idf = compute_idf(counts)
    u16_max = np.iinfo(np.uint16).max
    counts_u16 = sparse.csr_matrix(
        (np.minimum(counts.data, u16_max).astype(np.uint16), counts.indices, counts.indptr),
        shape=counts.shape,
    )
    np.save(precomputed / "idf.npy", idf)
    sparse.save_npz(show_dir / "S01.npz", counts_u16)
    (show_dir / "S01.index.json").write_text(json.dumps(["S01E01", "S01E02"]))

    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "shows": {str(tmdb_id): {"tmdb_id": tmdb_id, "name": show, "seasons": [1]}},
    }
    (precomputed / "manifest.json").write_text(json.dumps(manifest))
    return show


def _write_srt(path, text):
    path.write_text(f"1\n00:00:01,000 --> 00:00:30,000\n{text}\n", encoding="utf-8")


def test_downloaded_gap_episode_is_grafted(tmp_path):
    show = _write_two_episode_cache(tmp_path)
    data_dir = tmp_path / "data" / "1"
    data_dir.mkdir(parents=True)
    _write_srt(data_dir / "Test Show S01E03.srt", E3)

    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show, expected_tmdb_id=1)
    loaded = matcher._load_precomputed_season(1)
    assert loaded is not None
    ref_matrix, codes, _idf = loaded
    assert "S01E03" in codes
    assert ref_matrix.shape[0] == 3


def test_grafted_episode_is_matchable(tmp_path):
    show = _write_two_episode_cache(tmp_path)
    data_dir = tmp_path / "data" / "1"
    data_dir.mkdir(parents=True)
    _write_srt(data_dir / "Test Show S01E03.srt", E3)

    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show, expected_tmdb_id=1)
    ref_matrix, codes, idf = matcher._load_precomputed_season(1)

    tm = TfidfMatcher()
    tm.load_precomputed(ref_matrix, codes, idf)
    results = tm.match("a chef cooking an italian pasta dinner in the kitchen")
    assert results[0][0] == "S01E03"


def test_episode_already_in_index_not_duplicated(tmp_path):
    show = _write_two_episode_cache(tmp_path)
    data_dir = tmp_path / "data" / "1"
    data_dir.mkdir(parents=True)
    _write_srt(data_dir / "Test Show S01E01.srt", E1)  # already covered by the cache

    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show, expected_tmdb_id=1)
    ref_matrix, codes, _idf = matcher._load_precomputed_season(1)
    assert codes.count("S01E01") == 1
    assert ref_matrix.shape[0] == 2


def test_no_downloaded_srts_leaves_cache_unchanged(tmp_path):
    show = _write_two_episode_cache(tmp_path)  # no data dir at all
    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show, expected_tmdb_id=1)
    ref_matrix, codes, _idf = matcher._load_precomputed_season(1)
    assert codes == ["S01E01", "S01E02"]
    assert ref_matrix.shape[0] == 2
