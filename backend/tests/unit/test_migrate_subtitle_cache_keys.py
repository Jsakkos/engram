"""Unit tests for migrate_subtitle_cache_keys.

The migration relocates legacy name-keyed SRT cache dirs (``data/<name>/``) to
the tmdb_id scheme (``data/<tmdb_id>/``) introduced by PR #288 so the build
script's complete-on-disk resume path finds them. These tests exercise the pure
relocation/merge logic against a tmp cache dir + an in-memory curated map — they
never touch the real ~/.engram/cache (per the real-cache verification hazard) and
inject the TMDB fallback so they stay offline.
"""

from pathlib import Path

import pytest

# `msc` (migrate_subtitle_cache_keys) is a session-scoped fixture in conftest.py.


def _mk_srt(d: Path, name: str, size: int = 200) -> Path:
    """Create an SRT under ``d`` whose body is padded to roughly ``size`` bytes."""
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    body = "1\n00:00:01,000 --> 00:00:02,000\n" + ("x" * max(0, size)) + "\n"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.unit
class TestLoadCuratedMap:
    def test_maps_sanitized_name_to_id(self, msc, tmp_path):
        csv = tmp_path / "shows.csv"
        csv.write_text(
            "rank,tmdb_id,name\n1,1396,Breaking Bad\n2,1973,24\n5,40,CSI: Miami\n",
            encoding="utf-8",
        )
        m = msc.load_curated_map(csv)
        assert m["Breaking Bad"] == "1396"
        assert m["24"] == "1973"
        # ':' is sanitized to ' -' so the key matches the on-disk dir name.
        assert m["CSI - Miami"] == "40"

    def test_rows_without_numeric_id_are_skipped(self, msc, tmp_path):
        csv = tmp_path / "shows.csv"
        csv.write_text("rank,tmdb_id,name\n1,,No Id Show\n2,1396,Breaking Bad\n", encoding="utf-8")
        m = msc.load_curated_map(csv)
        assert "No Id Show" not in m
        assert m["Breaking Bad"] == "1396"


@pytest.mark.unit
class TestMigrateRename:
    def test_name_dir_renamed_to_id_offline(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E01.srt")
        calls = []

        def fake_fetch(name):
            calls.append(name)
            return None

        tally = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=fake_fetch
        )
        assert (data / "1396" / "Breaking Bad - S01E01.srt").exists()
        assert not (data / "Breaking Bad").exists()
        assert tally.migrated == 1
        # CSV hit must not consult TMDB.
        assert calls == []


@pytest.mark.unit
class TestMergeUnionKeepLarger:
    def test_collision_unions_and_keeps_larger(self, msc, tmp_path):
        data = tmp_path / "data"
        # Legacy dir has E01 (large) + E03; fresh id dir has E01 (small) + E02.
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E01.srt", size=900)
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E03.srt", size=300)
        _mk_srt(data / "1396", "Breaking Bad - S01E01.srt", size=100)
        _mk_srt(data / "1396", "Breaking Bad - S01E02.srt", size=300)

        tally = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=lambda n: None
        )

        idd = data / "1396"
        assert {p.name for p in idd.glob("*.srt")} == {
            "Breaking Bad - S01E01.srt",
            "Breaking Bad - S01E02.srt",
            "Breaking Bad - S01E03.srt",
        }
        # E01 collision resolved in favour of the larger (legacy 900-byte) file.
        assert (idd / "Breaking Bad - S01E01.srt").stat().st_size >= 900
        assert not (data / "Breaking Bad").exists()
        assert tally.merged == 1
        assert tally.files_kept_larger == 1
        assert tally.files_dropped_smaller == 0
        assert tally.files_moved == 1  # E03 carried in

    def test_smaller_legacy_file_dropped(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E01.srt", size=100)
        _mk_srt(data / "1396", "Breaking Bad - S01E01.srt", size=900)

        tally = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=lambda n: None
        )

        # Fresh id-dir file (larger) wins; legacy dir is emptied and removed.
        assert (data / "1396" / "Breaking Bad - S01E01.srt").stat().st_size >= 900
        assert not (data / "Breaking Bad").exists()
        assert tally.files_dropped_smaller == 1
        assert tally.files_kept_larger == 0


@pytest.mark.unit
class TestAlreadyIdSkipped:
    def test_pure_numeric_dir_not_a_show_name_is_skipped(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "1396", "Breaking Bad - S01E01.srt")
        tally = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=lambda n: None
        )
        assert (data / "1396" / "Breaking Bad - S01E01.srt").exists()
        assert tally.skipped_already_id == 1
        assert tally.migrated == 0


@pytest.mark.unit
class TestAmbiguousNumericName:
    def test_numeric_show_name_reported_not_migrated(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "24", "24 - S01E01.srt")
        tally = msc.migrate_cache(data, {"24": "1973"}, dry_run=False, fetch_id_fn=lambda n: None)
        assert (data / "24" / "24 - S01E01.srt").exists()  # untouched
        assert "24" in tally.ambiguous
        assert not (data / "1973").exists()

    def test_treat_as_name_forces_migration(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "24", "24 - S01E01.srt")
        tally = msc.migrate_cache(
            data,
            {"24": "1973"},
            dry_run=False,
            treat_as_name={"24"},
            fetch_id_fn=lambda n: None,
        )
        assert (data / "1973" / "24 - S01E01.srt").exists()
        assert not (data / "24").exists()
        assert tally.migrated == 1


@pytest.mark.unit
class TestUnresolved:
    def test_unknown_name_left_and_reported(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Obscure Show", "Obscure Show - S01E01.srt")
        tally = msc.migrate_cache(data, {}, dry_run=False, fetch_id_fn=lambda n: None)
        assert (data / "Obscure Show").exists()
        assert "Obscure Show" in tally.unresolved
        assert tally.migrated == 0

    def test_tmdb_fallback_resolves_when_not_in_csv(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Obscure Show", "Obscure Show - S01E01.srt")
        tally = msc.migrate_cache(data, {}, dry_run=False, fetch_id_fn=lambda n: "555")
        assert (data / "555" / "Obscure Show - S01E01.srt").exists()
        assert not (data / "Obscure Show").exists()
        assert tally.migrated == 1


@pytest.mark.unit
class TestDryRun:
    def test_dry_run_makes_no_changes(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E01.srt")
        tally = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=True, fetch_id_fn=lambda n: None
        )
        # Counted as a would-migrate, but nothing on disk moved.
        assert (data / "Breaking Bad" / "Breaking Bad - S01E01.srt").exists()
        assert not (data / "1396").exists()
        assert tally.migrated == 1


@pytest.mark.unit
class TestIdempotent:
    def test_second_run_is_noop(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Breaking Bad", "Breaking Bad - S01E01.srt")
        msc.migrate_cache(data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=lambda n: None)
        tally2 = msc.migrate_cache(
            data, {"Breaking Bad": "1396"}, dry_run=False, fetch_id_fn=lambda n: None
        )
        assert tally2.migrated == 0
        assert tally2.merged == 0
        # 1396 now exists, is numeric, and is not a curated show name → skipped.
        assert tally2.skipped_already_id == 1


@pytest.mark.unit
class TestNormalizedMatch:
    """Dir names diverge from the curated CSV by case and by Windows silently
    stripping trailing dots from directory names. Matching normalizes both so
    these resolve deterministically — no risky fuzzy TMDB guess needed."""

    def test_case_insensitive(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "ONE PIECE", "ONE PIECE - S01E01.srt")
        tally = msc.migrate_cache(
            data, {"One Piece": "37854"}, dry_run=False, fetch_id_fn=lambda n: None
        )
        assert (data / "37854" / "ONE PIECE - S01E01.srt").exists()
        assert tally.migrated == 1

    def test_windows_trailing_dot(self, msc, tmp_path):
        data = tmp_path / "data"
        # Windows drops the trailing dot, so "S.W.A.T." lands on disk as "S.W.A.T".
        _mk_srt(data / "S.W.A.T", "S.W.A.T - S01E01.srt")
        tally = msc.migrate_cache(
            data, {"S.W.A.T.": "71790"}, dry_run=False, fetch_id_fn=lambda n: None
        )
        assert (data / "71790" / "S.W.A.T - S01E01.srt").exists()
        assert tally.migrated == 1


@pytest.mark.unit
class TestBackupDirSkipped:
    def test_backup_suffix_never_migrated(self, msc, tmp_path):
        data = tmp_path / "data"
        _mk_srt(data / "Frasier.1993-bak", "Frasier - S01E01.srt")
        # Even when the fallback WOULD resolve it, a backup-looking dir is left
        # untouched so deliberate manual backups are never clobbered.
        tally = msc.migrate_cache(data, {}, dry_run=False, fetch_id_fn=lambda n: "3452")
        assert (data / "Frasier.1993-bak").exists()
        assert not (data / "3452").exists()
        assert "Frasier.1993-bak" in tally.skipped_backup
        assert tally.migrated == 0
