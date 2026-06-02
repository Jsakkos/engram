import json

from app.matcher.episode_identification import (
    EpisodeMatcher,
    precomputed_covers_season,
    precomputed_episode_codes,
)
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION, vectorizer_config_hash


def _manifest(tmdb_id, name="Frasier"):
    # v3: keyed by str(tmdb_id); the entry carries the name for the no-id fallback.
    return {
        "shows": {
            str(tmdb_id): {
                "tmdb_id": tmdb_id,
                "name": name,
                "seasons": [1],
                "episode_counts": {"1": 24},
            }
        }
    }


def _write_corpus(tmp_path, tmdb_id, codes=("S01E01",), name="Frasier"):
    """Write a valid on-disk v3 (id-keyed) precomputed corpus for season 1."""
    pre = tmp_path / "precomputed"
    show_dir = pre / str(tmdb_id)
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text(json.dumps(list(codes)))
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "shows": {
            str(tmdb_id): {"tmdb_id": tmdb_id, "name": name, "seasons": [1], "episode_counts": {}}
        },
    }
    (pre / "manifest.json").write_text(json.dumps(manifest))


def test_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Manifest holds only Frasier 3452; a job for the 2023 revival 195241 gets no
    # coverage (resolved-by-name entry's id contradicts the expected id).
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest(3452), expected_tmdb_id=195241
        )
        is False
    )


def test_guard_resolves_by_id_then_checks_files(tmp_path):
    # Known id resolves the id-keyed entry; files absent -> False (file gate)...
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest(3452), expected_tmdb_id=3452
        )
        is False
    )
    # ...then present -> True.
    show_dir = tmp_path / "precomputed" / "3452"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text("[]")
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest(3452), expected_tmdb_id=3452
        )
        is True
    )


def test_resolves_by_name_when_no_expected_id(tmp_path):
    # No expected id -> resolve by the entry's stored name; files present -> True.
    show_dir = tmp_path / "precomputed" / "3452"
    show_dir.mkdir(parents=True)
    (show_dir / "S01.npz").write_bytes(b"x")
    (show_dir / "S01.index.json").write_text("[]")
    assert precomputed_covers_season(tmp_path, "Frasier", 1, manifest=_manifest(3452)) is True


def test_episode_codes_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Corpus is the 1993 original (3452); the job is the 2023 revival (195241).
    # precomputed_episode_codes must forward the guard and refuse, else it would
    # size a "skip download" result from the WRONG show's episode list.
    _write_corpus(tmp_path, 3452, codes=["S01E01", "S01E02"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=195241) is None


def test_episode_codes_returned_on_matching_id(tmp_path):
    _write_corpus(tmp_path, 3452, codes=["S01E01", "S01E02"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=3452) == [
        "S01E01",
        "S01E02",
    ]


def test_episode_codes_resolved_by_name_without_id(tmp_path):
    # No expected id -> resolve by name (backward compatible name-only matching).
    _write_corpus(tmp_path, 3452, codes=["S01E01"])
    assert precomputed_episode_codes(tmp_path, "Frasier", 1) == ["S01E01"]


def test_matcher_stores_expected_tmdb_id(tmp_path):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    assert m.expected_tmdb_id == 195241


def test_load_precomputed_returns_none_on_id_mismatch_without_pruning(tmp_path, monkeypatch):
    m = EpisodeMatcher(cache_dir=tmp_path, show_name="Frasier", expected_tmdb_id=195241)
    # v3 id-keyed manifest holding only the 1993 original.
    manifest = {
        "shows": {
            "3452": {
                "tmdb_id": "3452",
                "name": "Frasier",
                "seasons": [1],
                "episode_counts": {"1": 24},
            }
        }
    }
    monkeypatch.setattr(m, "_load_precomputed_manifest", lambda: manifest)
    assert m._load_precomputed_season(1) is None
    # The valid 3452 entry must survive (not pruned as "files missing").
    assert manifest["shows"]["3452"]["seasons"] == [1]
