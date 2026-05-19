"""Unit tests for the precomputed subtitle-vector cache.

Covers the shared vectorizer config, TfidfMatcher precomputed mode, and the
EpisodeMatcher cache loader (including format/config-mismatch fallback).
"""

import json

import numpy as np
import pytest
from scipy import sparse

from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    apply_tfidf,
    build_hashing_vectorizer,
    compute_idf,
    transform_query,
    vectorizer_config_hash,
)

_DOCS = [
    "detective solves the murder in the old mansion at midnight",
    "the spaceship crew explores a distant alien planet",
    "a chef cooks an elaborate pasta dinner in a small kitchen",
]


def _build_refs(docs=_DOCS):
    """Return (ref_matrix, idf) for a small corpus."""
    counts = build_hashing_vectorizer().transform(docs)
    idf = compute_idf(counts)
    return apply_tfidf(counts, idf), idf


class TestVectorizerConfig:
    def test_config_hash_is_stable(self):
        assert vectorizer_config_hash() == vectorizer_config_hash()

    def test_transform_query_is_deterministic(self):
        _, idf = _build_refs()
        v1 = transform_query("the alien planet", idf)
        v2 = transform_query("the alien planet", idf)
        assert (v1 != v2).nnz == 0

    def test_apply_tfidf_rows_are_l2_normalized(self):
        ref, _ = _build_refs()
        norms = np.sqrt(np.asarray(ref.multiply(ref).sum(axis=1)).ravel())
        # Every non-empty row is unit length.
        assert np.allclose(norms, 1.0, atol=1e-6)

    def test_compute_idf_length_matches_feature_space(self):
        _, idf = _build_refs()
        assert idf.shape[0] == build_hashing_vectorizer().n_features


class TestTfidfMatcherPrecomputed:
    def test_load_precomputed_match_picks_correct_episode(self):
        ref, idf = _build_refs()
        matcher = TfidfMatcher()
        matcher.load_precomputed(ref, ["S01E01", "S01E02", "S01E03"], idf)

        results = matcher.match("the crew explores a far away planet")
        assert results[0][0] == "S01E02"
        assert results[0][1] > results[1][1]

    def test_match_before_load_raises(self):
        with pytest.raises(RuntimeError):
            TfidfMatcher().match("anything")


class TestEpisodeMatcherCacheLoader:
    def _write_cache(self, tmp_path, manifest_overrides=None):
        """Write a minimal valid precomputed cache under tmp_path. Returns the show name."""
        show = "Test Show"
        precomputed = tmp_path / "precomputed"
        show_dir = precomputed / show  # sanitize_filename("Test Show") == "Test Show"
        show_dir.mkdir(parents=True)

        ref, idf = _build_refs()
        np.save(precomputed / "idf.npy", idf)
        sparse.save_npz(show_dir / "S01.npz", ref)
        (show_dir / "S01.index.json").write_text(json.dumps(["S01E01", "S01E02", "S01E03"]))

        manifest = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "vectorizer_config_hash": vectorizer_config_hash(),
            "content_version": "test",
            "shows": {show: {"tmdb_id": 1, "seasons": [1]}},
        }
        manifest.update(manifest_overrides or {})
        (precomputed / "manifest.json").write_text(json.dumps(manifest))
        return show

    def test_loads_valid_cache(self, tmp_path):
        show = self._write_cache(tmp_path)
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show)
        loaded = matcher._load_precomputed_season(1)
        assert loaded is not None
        ref_matrix, codes, idf = loaded
        assert ref_matrix.shape[0] == 3
        assert codes == ["S01E01", "S01E02", "S01E03"]

    def test_missing_manifest_returns_none(self, tmp_path):
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")
        assert matcher._load_precomputed_season(1) is None

    def test_format_version_mismatch_falls_back(self, tmp_path):
        show = self._write_cache(tmp_path, {"cache_format_version": "999"})
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show)
        assert matcher._load_precomputed_season(1) is None

    def test_config_hash_mismatch_falls_back(self, tmp_path):
        show = self._write_cache(tmp_path, {"vectorizer_config_hash": "tampered"})
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show)
        assert matcher._load_precomputed_season(1) is None

    def test_uncovered_season_returns_none(self, tmp_path):
        show = self._write_cache(tmp_path)
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=show)
        assert matcher._load_precomputed_season(2) is None

    def test_unknown_show_returns_none(self, tmp_path):
        self._write_cache(tmp_path)
        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Other Show")
        assert matcher._load_precomputed_season(1) is None
