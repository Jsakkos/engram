from app.matcher.episode_identification import precomputed_covers_season


def _manifest(tmdb_id):
    return {"shows": {"Frasier": {"tmdb_id": tmdb_id, "seasons": [1], "episode_counts": {"1": 24}}}}


def test_guard_rejects_mismatched_tmdb_id(tmp_path):
    # Manifest says Frasier == 3452; job expects 195241 -> no coverage, regardless of files.
    assert (
        precomputed_covers_season(
            tmp_path, "Frasier", 1, manifest=_manifest("3452"), expected_tmdb_id=195241
        )
        is False
    )


def test_guard_skipped_when_no_expected_id(tmp_path):
    # No expected id -> guard does not apply; falls through to file existence (absent -> False).
    assert precomputed_covers_season(tmp_path, "Frasier", 1, manifest=_manifest("3452")) is False


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
