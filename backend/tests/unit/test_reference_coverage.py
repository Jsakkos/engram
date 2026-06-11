"""Tests for reference_coverage — per-episode subtitle-reference availability.

This is the single source of truth the review endpoint, the download phase, and
the match phase all share to know which episodes actually have a usable reference
(precomputed vector, downloaded SRT) versus none at all (the silent-skip gap that
sent the real Mad Men S02E05 to review with no candidate to vote for).
"""

import json

from app.matcher.episode_identification import reference_coverage
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION, vectorizer_config_hash


def _write_corpus(tmp_path, tmdb_id, season, codes, name="Frasier"):
    """Write a valid on-disk v3 (id-keyed) precomputed corpus covering ``codes``."""
    pre = tmp_path / "precomputed"
    show_dir = pre / str(tmdb_id)
    show_dir.mkdir(parents=True, exist_ok=True)
    (show_dir / f"S{season:02d}.npz").write_bytes(b"x")
    (show_dir / f"S{season:02d}.index.json").write_text(json.dumps(list(codes)))
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "shows": {
            str(tmdb_id): {
                "tmdb_id": tmdb_id,
                "name": name,
                "seasons": [season],
                "episode_counts": {},
            }
        },
    }
    (pre / "manifest.json").write_text(json.dumps(manifest))


def test_episode_absent_from_index_reported_missing(tmp_path):
    # The Mad Men S02E05 shape: the precomputed index covers every episode but one.
    _write_corpus(tmp_path, 3452, 1, ["S01E01", "S01E02", "S01E04"])
    cov = reference_coverage(tmp_path, 3452, "Frasier", 1, [1, 2, 3, 4])
    assert cov == {
        "S01E01": "precomputed",
        "S01E02": "precomputed",
        "S01E03": "missing",
        "S01E04": "precomputed",
    }


def test_downloaded_srt_fills_the_gap(tmp_path):
    # E03 is absent from the precomputed index, but a scraped SRT now sits on disk
    # under the id-keyed data dir — it must read as "downloaded", not "missing".
    _write_corpus(tmp_path, 3452, 1, ["S01E01", "S01E02", "S01E04"])
    data_dir = tmp_path / "data" / "3452"
    data_dir.mkdir(parents=True)
    (data_dir / "Frasier S01E03.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8"
    )
    cov = reference_coverage(tmp_path, 3452, "Frasier", 1, [1, 2, 3, 4])
    assert cov["S01E03"] == "downloaded"
    # Precomputed still wins when both exist (vectors are what the matcher reads).
    assert cov["S01E01"] == "precomputed"


def test_no_precomputed_corpus_all_missing(tmp_path):
    # No cache at all → every roster episode is missing a reference.
    cov = reference_coverage(tmp_path, 9999, "Nonesuch", 3, [1, 2])
    assert cov == {"S03E01": "missing", "S03E02": "missing"}
