import json

from app.matcher.episode_identification import (
    EpisodeMatcher,
    precomputed_covers_season,
    precomputed_episode_codes,
)
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION, vectorizer_config_hash


def _manifest(tmdb_id):
    return {"shows": {"Frasier": {"tmdb_id": tmdb_id, "seasons": [1], "episode_counts": {"1": 24}}}}


def _write_corpus(tmp_path, tmdb_id, codes=("S01E01",)):
    """Write a valid on-disk precomputed corpus for Frasier S1 keyed to ``tmdb_id``."""
    pre = tmp_path / "precomputed"
    show_dir = pre / "Frasier"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text(json.dumps(list(codes)))
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "shows": {"Frasier": {"tmdb_id": tmdb_id, "seasons": [1], "episode_counts": {}}},
    }
    (pre / "manifest.json").write_text(json.dumps(manifest))


def test_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Manifest says Frasier == 3452; job expects 195241 -> no coverage, regardless of files.
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=195241
        )
        is False
    )


def test_guard_skipped_when_no_expected_id(tmp_path):
    # No expected id -> guard does not apply; coverage depends only on files.
    assert precomputed_covers_season(tmp_path, "Frasier", 1, manifest=_manifest("3452")) is False
    # Files present -> True. Proves the guard was SKIPPED (not that the file gate
    # masked an inverted guard): an int/string id mismatch is irrelevant when no
    # expected_tmdb_id is supplied.
    show_dir = tmp_path / "precomputed" / "Frasier"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text("[]")
    assert precomputed_covers_season(tmp_path, "Frasier", 1, manifest=_manifest("3452")) is True


def test_guard_passes_on_matching_id_then_checks_files(tmp_path):
    # Matching id -> guard passes; files absent so coverage is still False (file gate).
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=3452
        )
        is False
    )
    # Create the on-disk files so the file gate passes too.
    show_dir = tmp_path / "precomputed" / "Frasier"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text("[]")
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=3452
        )
        is True
    )


def test_episode_codes_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Corpus is the 1993 original (3452); the job is the 2023 revival (195241).
    # precomputed_episode_codes must forward the guard and refuse, else it would
    # size a "skip download" result from the WRONG show's episode list.
    _write_corpus(tmp_path, "3452", codes=["S01E01", "S01E02"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=195241) is None


def test_episode_codes_returned_on_matching_id(tmp_path):
    _write_corpus(tmp_path, "3452", codes=["S01E01", "S01E02"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=3452) == [
        "S01E01",
        "S01E02",
    ]


def test_episode_codes_backward_compatible_without_id(tmp_path):
    # No expected id -> guard skipped (backward compatible name-only matching).
    _write_corpus(tmp_path, "3452", codes=["S01E01"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1) == ["S01E01"]


def test_matcher_stores_expected_tmdb_id(tmp_path):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    assert m.expected_tmdb_id == 195241


def test_load_precomputed_returns_none_on_id_mismatch_without_pruning(tmp_path, monkeypatch):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    manifest = {
        "shows": {"Frasier": {"tmdb_id": "3452", "seasons": [1], "episode_counts": {"1": 24}}}
    }
    monkeypatch.setattr(m, "_load_precomputed_manifest", lambda: manifest)
    assert m._load_precomputed_season(1) is None
    # The valid 3452 entry must survive (not pruned as "files missing").
    assert manifest["shows"]["Frasier"]["seasons"] == [1]
