"""Part E: precomputed corpus + subtitle cache keyed by tmdb_id (v3 layout).

Two same-named shows (Frasier 1993 #3452 vs the 2023 revival #195241) must get
DISTINCT on-disk corpora instead of colliding into one ``precomputed/Frasier/``
dir. The cache format version is bumped so old name-keyed (v2) caches are
ignored and auto-replaced by the id-keyed (v3) download.
"""

import json

from app.matcher.episode_identification import (
    _resolve_corpus_entry,
    precomputed_covers_season,
    precomputed_episode_codes,
)
from app.matcher.subtitle_utils import corpus_dir_name
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION, vectorizer_config_hash


def test_corpus_dir_name_prefers_tmdb_id():
    assert corpus_dir_name(3452, "Frasier") == "3452"
    assert corpus_dir_name("195241", "Frasier") == "195241"


def test_corpus_dir_name_falls_back_to_sanitized_name():
    assert corpus_dir_name(None, "Frasier") == "Frasier"
    assert corpus_dir_name(None, "Marvel's: Daredevil") == "Marvel's - Daredevil"


def _write_corpus(root, *shows):
    """Write a v3 id-keyed precomputed corpus. ``shows`` = (tmdb_id, name, codes)."""
    pre = root / "precomputed"
    pre.mkdir(parents=True, exist_ok=True)
    manifest_shows = {}
    for tmdb_id, name, codes in shows:
        key = str(tmdb_id)
        d = pre / key
        d.mkdir(parents=True, exist_ok=True)
        (d / "S01.npz").write_bytes(b"x")
        (d / "S01.index.json").write_text(json.dumps(list(codes)))
        manifest_shows[key] = {
            "tmdb_id": tmdb_id,
            "name": name,
            "seasons": [1],
            "episode_counts": {"1": len(codes)},
        }
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "shows": manifest_shows,
    }
    (pre / "manifest.json").write_text(json.dumps(manifest))


def test_resolve_corpus_entry_by_id_and_by_name():
    manifest = {
        "shows": {
            "3452": {"tmdb_id": 3452, "name": "Frasier", "seasons": [1]},
            "195241": {"tmdb_id": 195241, "name": "Frasier", "seasons": [1]},
        }
    }
    key, entry = _resolve_corpus_entry(manifest, "Frasier", 195241)
    assert key == "195241" and entry["tmdb_id"] == 195241
    # Unknown id -> first entry whose name matches.
    key, entry = _resolve_corpus_entry(manifest, "Frasier", None)
    assert entry is not None and entry["name"] == "Frasier"
    # No such show.
    assert _resolve_corpus_entry(manifest, "Cheers", 999) == (None, None)


def test_same_name_shows_get_distinct_corpora(tmp_path):
    _write_corpus(
        tmp_path,
        (3452, "Frasier", ["S01E01"]),
        (195241, "Frasier", ["S01E01", "S01E02"]),
    )
    # The 1993 job resolves its own 1-episode corpus...
    assert precomputed_covers_season(tmp_path, "Frasier", 1, expected_tmdb_id=3452) is True
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=3452) == ["S01E01"]
    # ...and the 2023 revival resolves ITS own 2-episode corpus, not the 1993 one.
    assert precomputed_episode_codes(tmp_path, "Frasier", 1, expected_tmdb_id=195241) == [
        "S01E01",
        "S01E02",
    ]


def test_unknown_tmdb_id_resolves_by_name(tmp_path):
    _write_corpus(tmp_path, (3452, "Frasier", ["S01E01"]))
    assert precomputed_episode_codes(tmp_path, "Frasier", 1) == ["S01E01"]


def test_missing_show_returns_false(tmp_path):
    _write_corpus(tmp_path, (3452, "Frasier", ["S01E01"]))
    assert precomputed_covers_season(tmp_path, "Cheers", 1, expected_tmdb_id=999) is False
