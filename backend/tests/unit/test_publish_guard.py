"""Unit tests for the nightly publish shrink guard.

The guard is pure arithmetic over two manifests, so these tests never touch the
network, the real ~/.engram/cache, or the live release.
"""

import json
import subprocess

import pytest


def _manifest(shows: dict[str, dict]) -> dict:
    return {
        "cache_format_version": "3",
        "content_version": "2026-09-08",
        "shows": shows,
    }


def _show(name: str, counts: dict[str, int]) -> dict:
    return {
        "tmdb_id": 1,
        "name": name,
        "seasons": [int(s) for s in counts],
        "episode_counts": counts,
    }


def _write_manifest(tmp_path, shows) -> str:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest(shows)), encoding="utf-8")
    return str(path)


class TestManifestTotals:
    def test_counts_shows_and_episodes(self, pg):
        m = _manifest({"1": _show("A", {"1": 10, "2": 12}), "2": _show("B", {"1": 5})})
        assert pg.manifest_totals(m) == (2, 27)

    def test_empty_manifest_is_zero(self, pg):
        assert pg.manifest_totals(_manifest({})) == (0, 0)

    def test_missing_shows_key_raises(self, pg):
        # A manifest missing 'shows' entirely is structurally broken, not a
        # deliberately empty cache; it must be undecidable (exit 2), not read
        # as an empty-candidate block (exit 1).
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"cache_format_version": "3"})

    def test_explicit_empty_shows_is_zero(self, pg):
        # {"shows": {}} is a valid, deliberately empty manifest and must still
        # total to (0, 0) rather than raise.
        assert pg.manifest_totals(_manifest({})) == (0, 0)


class TestVerdictForGrowth:
    def test_growth_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(600, 37000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.GROWTH

    def test_identical_is_allowed(self, pg):
        v = pg.verdict_for(candidate=(467, 36742), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True


class TestVerdictForShrink:
    def test_shrink_within_tolerance_is_allowed(self, pg):
        # 36,742 * 0.98 = 36,007.16, so 36,100 is inside tolerance.
        v = pg.verdict_for(candidate=(467, 36100), published=(467, 36742), tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.WITHIN_TOLERANCE

    def test_shrink_beyond_tolerance_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(467, 30000), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EPISODES_SHRANK
        assert "30000" in v.reason and "36742" in v.reason

    def test_show_count_drop_is_blocked_even_when_episodes_hold(self, pg):
        # A resolution regression can collapse many shows into few while the
        # episode total barely moves. Shows are guarded independently.
        v = pg.verdict_for(candidate=(300, 36700), published=(467, 36742), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.SHOWS_SHRANK


class TestVerdictForNoBaseline:
    def test_no_published_baseline_is_allowed(self, pg):
        # First publish ever, or a release with no manifest asset yet.
        v = pg.verdict_for(candidate=(467, 36742), published=None, tolerance=0.02)
        assert v.allowed is True
        assert v.verdict is pg.ShrinkVerdict.NO_BASELINE

    def test_empty_candidate_is_always_blocked(self, pg):
        v = pg.verdict_for(candidate=(0, 0), published=None, tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.EMPTY_CANDIDATE


class TestTolerance:
    @pytest.mark.parametrize("tolerance", [-0.01, 1.01])
    def test_out_of_range_tolerance_rejected(self, pg, tolerance):
        with pytest.raises(ValueError):
            pg.verdict_for(candidate=(1, 1), published=(1, 1), tolerance=tolerance)


class TestManifestValidation:
    """Well-formed JSON with the wrong shape must be diagnosed, never coerced."""

    def test_shows_as_list_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": [{"a": 1}]})

    def test_shows_null_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": None})

    def test_entry_not_a_mapping_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": {"1": "nope"}})

    def test_episode_counts_not_a_mapping_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": {"1": {"episode_counts": [1, 2]}}})

    def test_non_int_episode_count_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": {"1": {"episode_counts": {"1": "ten"}}}})

    def test_bool_episode_count_raises(self, pg):
        # bool is an int subclass; a truthy flag must not read as one episode.
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals({"shows": {"1": {"episode_counts": {"1": True}}}})

    def test_top_level_not_a_mapping_raises(self, pg):
        with pytest.raises(pg.ManifestError):
            pg.manifest_totals([1, 2, 3])


class TestBaselineUnusable:
    """A published baseline of zero must not silently disable every floor."""

    def test_zero_zero_baseline_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(1, 1), published=(0, 0), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.BASELINE_UNUSABLE

    def test_zero_episode_baseline_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(500, 30000), published=(467, 0), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.BASELINE_UNUSABLE

    def test_reason_points_at_the_release(self, pg):
        v = pg.verdict_for(candidate=(1, 1), published=(0, 0), tolerance=0.02)
        assert "inspect" in v.reason.lower()


class TestToleranceBoundary:
    """Pin the float boundary so a later int()/round() cannot move the line."""

    def test_exactly_on_the_floor_is_allowed(self, pg):
        # 100 * 0.98 == 98.0 and 10000 * 0.98 == 9800.0, both exactly on it.
        v = pg.verdict_for(candidate=(98, 9800), published=(100, 10000), tolerance=0.02)
        assert v.allowed is True

    def test_one_below_the_floor_is_blocked(self, pg):
        v = pg.verdict_for(candidate=(97, 9700), published=(100, 10000), tolerance=0.02)
        assert v.allowed is False
        assert v.verdict is pg.ShrinkVerdict.SHOWS_SHRANK


class TestReasonFormatting:
    def test_floor_is_not_rounded_up_into_a_contradiction(self, pg):
        # 467 * 0.98 = 457.66. "below the floor of 458" reads as a lie when the
        # candidate is 458.
        v = pg.verdict_for(candidate=(400, 36742), published=(467, 36742), tolerance=0.02)
        assert "458" not in v.reason
        assert "457.7" in v.reason

    def test_fractional_tolerance_is_not_rounded_away(self, pg):
        v = pg.verdict_for(candidate=(100, 100), published=(467, 36742), tolerance=0.025)
        assert "2%" not in v.reason
        assert "2.5" in v.reason


class TestBaselineFetchClassification:
    """The three baseline outcomes must stay distinguishable, no network."""

    def test_release_not_found_is_absent(self, pg):
        assert pg.classify_gh_failure("release not found") is pg.BaselineStatus.ABSENT

    def test_no_assets_match_is_absent(self, pg):
        status = pg.classify_gh_failure("no assets match the file pattern")
        assert status is pg.BaselineStatus.ABSENT

    def test_auth_failure_is_unavailable(self, pg):
        status = pg.classify_gh_failure("gh: To get started, please run: gh auth login")
        assert status is pg.BaselineStatus.UNAVAILABLE

    def test_http_404_from_expired_auth_is_unavailable(self, pg):
        # gh emits a generic "Not Found" for expired/under-scoped tokens, a
        # renamed repo, etc, not only for a genuinely absent release. Reading
        # this as ABSENT would let a broken credential publish unvalidated.
        status = pg.classify_gh_failure(
            "HTTP 404: Not Found (https://api.github.com/repos/Jsakkos/engram/releases)"
        )
        assert status is pg.BaselineStatus.UNAVAILABLE

    def test_gh_not_found_http_variant_is_unavailable(self, pg):
        status = pg.classify_gh_failure("gh: Not Found (HTTP 404)")
        assert status is pg.BaselineStatus.UNAVAILABLE

    def test_network_failure_is_unavailable(self, pg):
        status = pg.classify_gh_failure("dial tcp: lookup api.github.com: no such host")
        assert status is pg.BaselineStatus.UNAVAILABLE

    def test_gh_missing_binary_is_unavailable(self, pg, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(pg.subprocess, "run", _boom)
        outcome = pg.fetch_published_totals("some-tag")
        assert outcome.status is pg.BaselineStatus.UNAVAILABLE

    def test_gh_timeout_is_unavailable(self, pg, monkeypatch):
        def _boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=60)

        monkeypatch.setattr(pg.subprocess, "run", _boom)
        outcome = pg.fetch_published_totals("some-tag")
        assert outcome.status is pg.BaselineStatus.UNAVAILABLE

    def test_gh_call_passes_a_timeout(self, pg, monkeypatch):
        seen: dict = {}

        def _fake(cmd, **kwargs):
            seen.update(kwargs)
            raise FileNotFoundError("gh")

        monkeypatch.setattr(pg.subprocess, "run", _fake)
        pg.fetch_published_totals("some-tag")
        assert seen.get("timeout") == 60

    def test_gh_call_passes_repo_flag(self, pg, monkeypatch):
        # The guard must read the baseline from the same repo the publishing
        # wrapper uploads to (--repo Jsakkos/engram), or a re-pointed remote
        # or a fork compares against the wrong release.
        seen: dict = {}

        def _fake(cmd, **kwargs):
            seen["cmd"] = cmd
            raise FileNotFoundError("gh")

        monkeypatch.setattr(pg.subprocess, "run", _fake)
        pg.fetch_published_totals("some-tag", repo="someone/fork")
        cmd = seen["cmd"]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "someone/fork"

    def test_gh_call_defaults_repo_to_default_repo(self, pg, monkeypatch):
        seen: dict = {}

        def _fake(cmd, **kwargs):
            seen["cmd"] = cmd
            raise FileNotFoundError("gh")

        monkeypatch.setattr(pg.subprocess, "run", _fake)
        pg.fetch_published_totals("some-tag")
        cmd = seen["cmd"]
        assert cmd[cmd.index("--repo") + 1] == pg.DEFAULT_REPO

    def test_called_process_error_with_http_404_is_unavailable_end_to_end(self, pg, monkeypatch):
        # Drives fetch_published_totals's CalledProcessError branch end to
        # end (not just classify_gh_failure in isolation), stubbing
        # subprocess.run the way gh actually fails on expired/under-scoped
        # auth. No network call.
        def _boom(cmd, **kwargs):
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                output="",
                stderr="HTTP 404: Not Found (https://api.github.com/repos/Jsakkos/engram)",
            )

        monkeypatch.setattr(pg.subprocess, "run", _boom)
        outcome = pg.fetch_published_totals("some-tag")
        assert outcome.status is pg.BaselineStatus.UNAVAILABLE


class TestMainExitCodes:
    """main() owns the contract the bash wrapper's safety rests on."""

    def _run(self, pg, monkeypatch, argv, outcome=None):
        if outcome is not None:
            monkeypatch.setattr(pg, "fetch_published_totals", lambda tag, repo=None: outcome)
        monkeypatch.setattr(pg.sys, "argv", ["publish_guard.py", *argv])
        return pg.main()

    def test_growth_exits_zero(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (1, 9))
        assert self._run(pg, monkeypatch, ["--candidate", path], outcome) == 0

    def test_shrink_exits_one(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (5, 500))
        assert self._run(pg, monkeypatch, ["--candidate", path], outcome) == 1

    def test_absent_baseline_exits_zero(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.ABSENT, None)
        assert self._run(pg, monkeypatch, ["--candidate", path], outcome) == 0

    def test_unavailable_baseline_exits_two(self, pg, monkeypatch, tmp_path, capsys):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.UNAVAILABLE, None, "gh exploded")
        assert self._run(pg, monkeypatch, ["--candidate", path], outcome) == 2
        assert "gh exploded" in capsys.readouterr().out

    def test_unusable_baseline_exits_one(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (0, 0))
        assert self._run(pg, monkeypatch, ["--candidate", path], outcome) == 1

    def test_missing_candidate_exits_two(self, pg, monkeypatch, tmp_path):
        missing = str(tmp_path / "nope.json")
        assert self._run(pg, monkeypatch, ["--candidate", missing]) == 2

    def test_malformed_candidate_shape_exits_two(self, pg, monkeypatch, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text('{"shows": [{"a": 1}]}', encoding="utf-8")
        assert self._run(pg, monkeypatch, ["--candidate", str(path)]) == 2

    def test_unparseable_candidate_exits_two(self, pg, monkeypatch, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text("{not json", encoding="utf-8")
        assert self._run(pg, monkeypatch, ["--candidate", str(path)]) == 2

    def test_unexpected_exception_exits_two_not_one(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})

        def _boom(tag):
            raise RuntimeError("surprise")

        monkeypatch.setattr(pg, "fetch_published_totals", _boom)
        monkeypatch.setattr(pg.sys, "argv", ["publish_guard.py", "--candidate", path])
        assert pg.main() == 2


class TestAllowShrinkGating:
    def _run_allow_shrink(self, pg, monkeypatch, path, outcome):
        monkeypatch.setattr(pg, "fetch_published_totals", lambda tag, repo=None: outcome)
        monkeypatch.setattr(
            pg.sys, "argv", ["publish_guard.py", "--candidate", path, "--allow-shrink"]
        )
        return pg.main()

    def test_allow_shrink_overrides_a_real_shrink(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (5, 500))
        assert self._run_allow_shrink(pg, monkeypatch, path, outcome) == 0

    def test_allow_shrink_does_not_override_an_empty_candidate(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (5, 500))
        assert self._run_allow_shrink(pg, monkeypatch, path, outcome) == 1

    def test_allow_shrink_does_not_override_an_unusable_baseline(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        outcome = pg.BaselineOutcome(pg.BaselineStatus.RETRIEVED, (0, 0))
        assert self._run_allow_shrink(pg, monkeypatch, path, outcome) == 1


class TestToleranceArgparse:
    def test_out_of_range_tolerance_is_an_argparse_usage_error(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        monkeypatch.setattr(
            pg.sys, "argv", ["publish_guard.py", "--candidate", path, "--tolerance", "5"]
        )
        with pytest.raises(SystemExit) as exc:
            pg.main()
        assert exc.value.code == 2

    def test_non_numeric_tolerance_is_an_argparse_usage_error(self, pg, monkeypatch, tmp_path):
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        monkeypatch.setattr(
            pg.sys, "argv", ["publish_guard.py", "--candidate", path, "--tolerance", "abc"]
        )
        with pytest.raises(SystemExit) as exc:
            pg.main()
        assert exc.value.code == 2

    def test_tolerance_above_half_is_an_argparse_usage_error(self, pg, monkeypatch, tmp_path):
        # A tolerance of 1.0 zeros every floor and waves through any shrink
        # while still exiting 0; the CLI entry point caps well short of that.
        # verdict_for's own [0, 1] contract is exercised separately in
        # TestTolerance and is unaffected by this CLI-level cap.
        path = _write_manifest(tmp_path, {"1": _show("A", {"1": 10})})
        monkeypatch.setattr(
            pg.sys, "argv", ["publish_guard.py", "--candidate", path, "--tolerance", "0.9"]
        )
        with pytest.raises(SystemExit) as exc:
            pg.main()
        assert exc.value.code == 2
