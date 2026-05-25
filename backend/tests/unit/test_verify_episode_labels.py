"""Unit tests for the verify_episode_labels script's pure logic.

These cover only the no-I/O, no-matcher helpers so they run fast and offline:
filename parsing, target-name computation, status classification, scope
auto-detection, sidecar expansion, and the collision-safe rename planner.
"""

import sys
from pathlib import Path

# The script lives in backend/scripts, which isn't on the package path.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import verify_episode_labels as vel  # noqa: E402


class TestParseClaim:
    def test_standard_sxxeyy(self):
        assert vel.parse_claim("Breaking Bad - S01E03.mkv") == (1, 3)

    def test_lowercase_and_single_digits(self):
        assert vel.parse_claim("show s1e3.mkv") == (1, 3)

    def test_xform(self):
        assert vel.parse_claim("Show 2x07.mkv") == (2, 7)

    def test_season_episode_words(self):
        assert vel.parse_claim("Show - Season 4 Episode 11.mkv") == (4, 11)

    def test_no_code_returns_none(self):
        assert vel.parse_claim("Show - the one with no code.mkv") is None


class TestComputeTargetName:
    def test_swaps_episode_preserving_padding(self):
        assert vel.compute_target_name("Show - S01E05.mkv", 1, 3) == "Show - S01E03.mkv"

    def test_changes_season_too(self):
        assert vel.compute_target_name("Show - S02E05.mkv", 1, 3) == "Show - S01E03.mkv"

    def test_preserves_xform_style(self):
        assert vel.compute_target_name("Show 1x05.mkv", 1, 3) == "Show 1x03.mkv"

    def test_single_digit_padding_preserved(self):
        # original used single-digit episode width, keep it
        assert vel.compute_target_name("Show S1E5.mkv", 1, 3) == "Show S1E3.mkv"


class TestClassify:
    THRESHOLD = 0.7

    def test_ok_when_match_and_confident(self):
        assert vel.classify((1, 3), (1, 3), 0.9, self.THRESHOLD) == vel.Status.OK

    def test_mismatch_when_confident_and_different(self):
        assert vel.classify((1, 5), (1, 3), 0.9, self.THRESHOLD) == vel.Status.MISMATCH

    def test_low_conf_below_threshold(self):
        assert vel.classify((1, 5), (1, 3), 0.5, self.THRESHOLD) == vel.Status.LOW_CONF

    def test_low_conf_even_when_agreeing(self):
        assert vel.classify((1, 3), (1, 3), 0.4, self.THRESHOLD) == vel.Status.LOW_CONF

    def test_no_match_when_predicted_none(self):
        assert vel.classify((1, 3), None, 0.0, self.THRESHOLD) == vel.Status.NO_MATCH

    def test_no_match_takes_priority_over_unparseable(self):
        assert vel.classify(None, None, 0.0, self.THRESHOLD) == vel.Status.NO_MATCH

    def test_unparseable_when_claim_none_but_predicted_exists(self):
        assert vel.classify(None, (1, 3), 0.9, self.THRESHOLD) == vel.Status.UNPARSEABLE


class TestDetectScope:
    def test_season_folder_uses_dir_name(self, tmp_path):
        season_dir = tmp_path / "Breaking Bad" / "Season 03"
        season_dir.mkdir(parents=True)
        (season_dir / "Breaking Bad - S03E01.mkv").touch()
        (season_dir / "Breaking Bad - S03E02.mkv").touch()

        plan = vel.detect_scope(season_dir)

        assert plan.mode == "season"
        assert plan.show_name == "Breaking Bad"
        assert [t.season for t in plan.targets] == [3]
        assert plan.targets[0].directory == season_dir

    def test_season_folder_falls_back_to_filename_season(self, tmp_path):
        # Directory name doesn't say "Season N"; infer from the files.
        disc_dir = tmp_path / "MyShow" / "Disc1"
        disc_dir.mkdir(parents=True)
        (disc_dir / "MyShow - S02E01.mkv").touch()
        (disc_dir / "MyShow - S02E02.mkv").touch()

        plan = vel.detect_scope(disc_dir)

        assert plan.mode == "season"
        assert [t.season for t in plan.targets] == [2]

    def test_show_folder_lists_seasons(self, tmp_path):
        show_dir = tmp_path / "Breaking Bad"
        s1 = show_dir / "Season 01"
        s2 = show_dir / "Season 02"
        s1.mkdir(parents=True)
        s2.mkdir(parents=True)
        (s1 / "Breaking Bad - S01E01.mkv").touch()
        (s2 / "Breaking Bad - S02E01.mkv").touch()

        plan = vel.detect_scope(show_dir)

        assert plan.mode == "show"
        assert plan.show_name == "Breaking Bad"
        assert sorted(t.season for t in plan.targets) == [1, 2]
        dirs = {t.season: t.directory for t in plan.targets}
        assert dirs[1] == s1
        assert dirs[2] == s2


class TestExpandSidecars:
    def test_includes_same_stem_sidecars(self, tmp_path):
        src = tmp_path / "Show - S01E05.mkv"
        dst = tmp_path / "Show - S01E03.mkv"
        listing = [
            tmp_path / "Show - S01E05.mkv",
            tmp_path / "Show - S01E05.srt",
            tmp_path / "Show - S01E05.en.srt",
            tmp_path / "Show - S01E01.mkv",  # unrelated, must not move
        ]

        expanded = vel.expand_sidecars({src: dst}, listing)

        assert expanded[tmp_path / "Show - S01E05.srt"] == tmp_path / "Show - S01E03.srt"
        assert expanded[tmp_path / "Show - S01E05.en.srt"] == tmp_path / "Show - S01E03.en.srt"
        assert (tmp_path / "Show - S01E05.mkv") in expanded
        assert (tmp_path / "Show - S01E01.mkv") not in expanded


class TestPlanTwoPhase:
    def _tmp(self, p: Path) -> Path:
        return p.with_name(p.name + vel.TEMP_SUFFIX)

    def test_simple_move_to_free_name(self, tmp_path):
        a = tmp_path / "a.mkv"
        z = tmp_path / "z.mkv"
        plan = vel.plan_two_phase({a: z}, existing={a})

        assert plan.conflicts == []
        assert plan.steps == [(a, self._tmp(a)), (self._tmp(a), z)]

    def test_two_cycle_swap_uses_temps(self, tmp_path):
        a = tmp_path / "a.mkv"
        b = tmp_path / "b.mkv"
        plan = vel.plan_two_phase({a: b, b: a}, existing={a, b})

        assert plan.conflicts == []
        # phase 1: everyone to temp; phase 2: temps to finals
        assert plan.steps == [
            (a, self._tmp(a)),
            (b, self._tmp(b)),
            (self._tmp(a), b),
            (self._tmp(b), a),
        ]

    def test_conflict_with_non_participant(self, tmp_path):
        a = tmp_path / "a.mkv"
        c = tmp_path / "c.mkv"  # exists on disk but is NOT a source we move
        plan = vel.plan_two_phase({a: c}, existing={a, c})

        assert c in plan.conflicts

    def test_executing_steps_realizes_mapping(self, tmp_path):
        # Concrete proof the swap plan is safe on a real filesystem.
        a = tmp_path / "a.mkv"
        b = tmp_path / "b.mkv"
        a.write_text("content-A")
        b.write_text("content-B")

        plan = vel.plan_two_phase({a: b, b: a}, existing={a, b})
        for src, dst in plan.steps:
            src.rename(dst)

        assert a.read_text() == "content-B"
        assert b.read_text() == "content-A"


class TestDefaultCsvPath:
    def test_season_mode_uses_target_dir(self, tmp_path):
        season_dir = tmp_path / "Show" / "Season 03"
        plan = vel.ScopePlan("season", "Show", [vel.SeasonTarget(3, season_dir)])
        assert vel.default_csv_path(plan, None) == season_dir / "engram_label_check.csv"

    def test_show_mode_uses_show_root(self, tmp_path):
        show_dir = tmp_path / "Show"
        targets = [
            vel.SeasonTarget(1, show_dir / "Season 01"),
            vel.SeasonTarget(2, show_dir / "Season 02"),
        ]
        plan = vel.ScopePlan("show", "Show", targets)
        # CSV should land at the show root, not inside Season 01
        assert vel.default_csv_path(plan, None) == show_dir / "engram_label_check.csv"

    def test_override_wins(self, tmp_path):
        plan = vel.ScopePlan("season", "Show", [vel.SeasonTarget(3, tmp_path)])
        assert vel.default_csv_path(plan, "X:/out/custom.csv") == Path("X:/out/custom.csv")


class TestUndoLog:
    def test_rejects_log_without_steps(self, tmp_path):
        import json

        bad = tmp_path / "bad_undo.json"
        bad.write_text(json.dumps({"created": "20260523-000000"}), encoding="utf-8")
        try:
            vel.undo_from_log(bad)
            raise AssertionError("expected ValueError for malformed log")
        except ValueError:
            pass

    def test_recovers_files_stranded_after_partial_apply(self, tmp_path):
        # Simulate a crash after phase 1: the log (written first) must let --undo
        # recover the .engram-tmp files.
        a = tmp_path / "a.mkv"
        b = tmp_path / "b.mkv"
        a.write_text("A")
        b.write_text("B")

        plan = vel.plan_two_phase({a: b, b: a}, existing={a, b})
        log_path = vel._write_undo_log(plan, tmp_path)

        # Execute ONLY phase 1 (everyone -> temp), then "crash".
        phase1 = plan.steps[: len(plan.steps) // 2]
        for src, dst in phase1:
            src.rename(dst)

        moved = vel.undo_from_log(log_path)

        assert moved == 2
        assert a.read_text() == "A"
        assert b.read_text() == "B"
