"""Unit tests for the precomputed subtitle-vector cache.

Covers the shared vectorizer config, TfidfMatcher precomputed mode, and the
EpisodeMatcher cache loader (including format/config-mismatch fallback).
"""

import json

import numpy as np
import pytest
from scipy import sparse

from app.matcher.episode_identification import (
    EpisodeMatcher,
    TfidfMatcher,
    load_precomputed_manifest,
    precomputed_covers_season,
    precomputed_episode_codes,
)
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


def _build_counts(docs=_DOCS):
    """Return (uint16 counts, idf) — the on-disk shape for cache v2.

    Mirrors scripts/build_subtitle_cache.py exactly, including the defensive
    clip to uint16 range, so a future larger/pathological corpus can't
    silently overflow here in a way the real build would have clipped.
    """
    counts = build_hashing_vectorizer().transform(docs)
    idf = compute_idf(counts)
    u16_max = np.iinfo(np.uint16).max
    counts_u16 = sparse.csr_matrix(
        (np.minimum(counts.data, u16_max).astype(np.uint16), counts.indices, counts.indptr),
        shape=counts.shape,
    )
    return counts_u16, idf


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

        counts, idf = _build_counts()
        np.save(precomputed / "idf.npy", idf)
        sparse.save_npz(show_dir / "S01.npz", counts)
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


class TestPrecomputedCoversSeason:
    """The download-skip gate. Must agree with the matcher's load gate, so it
    shares the same manifest validation. A True result means the matcher will
    use the cache, so skipping the download can't strand a title."""

    def _write_cache(self, tmp_path, manifest_overrides=None, write_files=True):
        show = "Test Show"
        precomputed = tmp_path / "precomputed"
        show_dir = precomputed / show  # sanitize_filename("Test Show") == "Test Show"
        show_dir.mkdir(parents=True)

        if write_files:
            # The gate only checks existence, not contents.
            (precomputed / "idf.npy").write_bytes(b"")
            (show_dir / "S01.npz").write_bytes(b"")
            (show_dir / "S01.index.json").write_text(json.dumps(["S01E01", "S01E02"]))

        manifest = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "vectorizer_config_hash": vectorizer_config_hash(),
            "content_version": "test",
            "shows": {show: {"tmdb_id": 1, "seasons": [1]}},
        }
        manifest.update(manifest_overrides or {})
        (precomputed / "manifest.json").write_text(json.dumps(manifest))
        return show

    def test_covered_season_returns_true(self, tmp_path):
        show = self._write_cache(tmp_path)
        assert precomputed_covers_season(tmp_path, show, 1) is True

    def test_missing_manifest_returns_false(self, tmp_path):
        assert precomputed_covers_season(tmp_path, "Test Show", 1) is False

    def test_unknown_show_returns_false(self, tmp_path):
        self._write_cache(tmp_path)
        assert precomputed_covers_season(tmp_path, "Other Show", 1) is False

    def test_uncovered_season_returns_false(self, tmp_path):
        show = self._write_cache(tmp_path)
        assert precomputed_covers_season(tmp_path, show, 2) is False

    def test_listed_but_files_missing_returns_false(self, tmp_path):
        show = self._write_cache(tmp_path, write_files=False)
        assert precomputed_covers_season(tmp_path, show, 1) is False

    def test_format_version_mismatch_returns_false(self, tmp_path):
        show = self._write_cache(tmp_path, {"cache_format_version": "999"})
        assert precomputed_covers_season(tmp_path, show, 1) is False
        assert load_precomputed_manifest(tmp_path) is None

    def test_accepts_preloaded_manifest(self, tmp_path):
        show = self._write_cache(tmp_path)
        manifest = load_precomputed_manifest(tmp_path)
        # A caller-supplied manifest is used as-is (no re-read).
        assert precomputed_covers_season(tmp_path, show, 1, manifest=manifest) is True
        # A manifest that doesn't list the show wins even though files are on disk.
        empty = {**manifest, "shows": {}}
        assert precomputed_covers_season(tmp_path, show, 1, manifest=empty) is False

    def test_episode_codes_returns_index_for_covered(self, tmp_path):
        show = self._write_cache(tmp_path)
        assert precomputed_episode_codes(tmp_path, show, 1) == ["S01E01", "S01E02"]

    def test_episode_codes_none_when_uncovered(self, tmp_path):
        show = self._write_cache(tmp_path)
        assert precomputed_episode_codes(tmp_path, show, 2) is None
        assert precomputed_episode_codes(tmp_path, "Other Show", 1) is None


@pytest.mark.unit
class TestPrecomputedCacheService:
    """The cache-service layer is the entry point on startup. The rolling
    `subtitle-cache-latest` tag means an old backend can pull a new
    incompatible cache from the same URL — the format-version check has to
    work or we will load garbage vectors into the matcher."""

    def test_cache_tag_is_rolling(self):
        """Guards against silently reverting to per-format-version tags.

        If someone refactors and reintroduces ``f"subtitle-cache-v{...}"``,
        this test catches it before the next release pushes garbage.
        """
        from app.services.precomputed_cache_service import _CACHE_TAG

        assert _CACHE_TAG == "subtitle-cache-latest"

    @pytest.mark.asyncio
    async def test_incompatible_remote_format_skips_download(self, monkeypatch):
        """When the remote manifest reports a format version we don't
        understand, we must log and bail — NOT download the tarball."""
        from app.services import precomputed_cache_service as svc

        # The remote manifest reports an alien format version. The local
        # code only understands `CACHE_FORMAT_VERSION` (a string); use a value
        # we know it will never match. Earlier this concatenated `+ 100`,
        # which TypeErrored on a string and was silently swallowed by the
        # safety wrapper — the test passed without actually exercising the
        # format-version branch.
        async def fake_manifest():
            return {
                "cache_format_version": "999",
                "content_version": "2099-01-01",
                "shows": {},
            }

        async def fake_download(*args, **kwargs):
            raise AssertionError("must not download when format-version mismatches")

        async def fake_get_config():
            return type(
                "Cfg",
                (),
                {
                    "precomputed_cache_enabled": True,
                    "subtitles_cache_path": "~/.engram/cache",
                    "precomputed_cache_version": "",
                },
            )()

        async def fake_update_config(**kwargs):
            raise AssertionError("must not update config when format-version mismatches")

        monkeypatch.setattr(svc, "_fetch_remote_manifest", fake_manifest)
        monkeypatch.setattr(svc, "_download_and_extract", fake_download)
        # _ensure_precomputed_cache_inner imports these at call time.
        from app.services import config_service

        monkeypatch.setattr(config_service, "get_config", fake_get_config)
        monkeypatch.setattr(config_service, "update_config", fake_update_config)

        # Wrapper swallows all exceptions; if we got an AssertionError out
        # of fake_download/fake_update_config, the function we're testing
        # didn't honor the format-version check.
        await svc.ensure_precomputed_cache()
