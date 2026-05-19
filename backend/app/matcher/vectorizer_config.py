"""Shared, stateless vectorizer config for the precomputed subtitle cache.

This module is the single source of truth for the hashed TF-IDF feature space.
It is imported by BOTH the offline build script (scripts/build_subtitle_cache.py)
and the runtime matcher (episode_identification.py) so the two can never drift.

The shipped cache contains HASHED TF-IDF vectors only. ``HashingVectorizer`` is
stateless -- no vocabulary is stored or distributed -- so the published artifact
is a non-invertible statistical fingerprint rather than verbatim subtitle text.
A single global IDF array is fit once at build time and shipped alongside it.
"""

import hashlib
import json

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.preprocessing import normalize as _sk_normalize

# Bump whenever VECTORIZER_PARAMS, HASHING_N_FEATURES, SUBLINEAR_TF, the artifact
# layout, or the subtitle-cleaning function changes. A mismatch between this value
# and a cache manifest invalidates the cache (runtime falls back to scraping).
CACHE_FORMAT_VERSION = "1"

HASHING_N_FEATURES = 2**18

# Frozen HashingVectorizer params. ``alternate_sign=False`` keeps token counts
# non-negative (required for the TF-IDF transform); ``norm=None`` defers
# normalization so sublinear TF + IDF weighting + the final L2 norm are applied
# explicitly and identically at build time and at query time.
VECTORIZER_PARAMS: dict = {
    "analyzer": "word",
    "ngram_range": (1, 2),
    "n_features": HASHING_N_FEATURES,
    "alternate_sign": False,
    "norm": None,
}

# Matches the original TfidfVectorizer config used by the scraped (fallback) path.
SUBLINEAR_TF = True


def build_hashing_vectorizer() -> HashingVectorizer:
    """Return a HashingVectorizer with the frozen, shared config."""
    return HashingVectorizer(**VECTORIZER_PARAMS)


def vectorizer_config_hash() -> str:
    """Stable sha256 of the frozen config, embedded in the cache manifest.

    Recomputed at runtime; any divergence from the manifest's stored value means
    the cache was built with an incompatible config and must not be used.
    """
    payload = json.dumps(
        {
            "format": CACHE_FORMAT_VERSION,
            "n_features": HASHING_N_FEATURES,
            "params": {
                k: (list(v) if isinstance(v, tuple) else v)
                for k, v in sorted(VECTORIZER_PARAMS.items())
            },
            "sublinear_tf": SUBLINEAR_TF,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_idf(count_matrix) -> np.ndarray:
    """Compute the global smoothed IDF array from a hashed term-count matrix.

    Uses the same smoothed formula as ``sklearn``'s TfidfTransformer
    (``smooth_idf=True``): ``idf(t) = ln((1 + n) / (1 + df(t))) + 1``.

    Args:
        count_matrix: sparse matrix (rows = documents) of raw hashed term counts.

    Returns:
        1-D float32 array of length ``HASHING_N_FEATURES``.
    """
    count_matrix = sparse.csr_matrix(count_matrix)
    n_docs = count_matrix.shape[0]
    df = np.asarray((count_matrix > 0).sum(axis=0)).ravel().astype(np.float64)
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0
    return idf.astype(np.float32)


def apply_tfidf(counts, idf_array):
    """Convert a hashed term-count matrix into L2-normalized TF-IDF rows.

    Build time and query time both route through this function, so reference
    vectors and query vectors always land in the identical feature space.

    Args:
        counts: sparse matrix of raw hashed term counts (rows = documents).
        idf_array: 1-D IDF array of length ``HASHING_N_FEATURES``.

    Returns:
        L2-normalized CSR matrix.
    """
    counts = sparse.csr_matrix(counts, dtype=np.float64, copy=True)
    if SUBLINEAR_TF and counts.nnz:
        counts.data = 1.0 + np.log(counts.data)
    idf_row = np.asarray(idf_array, dtype=np.float64).reshape(1, -1)
    weighted = sparse.csr_matrix(counts.multiply(idf_row))
    return _sk_normalize(weighted, norm="l2", copy=False)


def transform_query(text: str, idf_array):
    """Transform raw query text into a 1xN L2-normalized TF-IDF vector.

    Stateless: relies only on the frozen HashingVectorizer config and the shipped
    global IDF array, so it reproduces the build-time feature space exactly.
    """
    counts = build_hashing_vectorizer().transform([text or ""])
    return apply_tfidf(counts, idf_array)
