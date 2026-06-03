"""Unit tests for scripts/contributors.py.

The script renders contributor acknowledgments (a release-notes section and the
CONTRIBUTORS.md roster) from git/GitHub history. These tests pin the pure
classification + rendering logic and exercise the git/gh I/O through a fake
`run` seam so nothing touches the network.

The `contrib` fixture (loaded once per session) lives in conftest.py.
"""

import json


def test_is_external_excludes_owner_and_bots(contrib):
    assert contrib.is_external("katelovescode") is True
    assert contrib.is_external("Jsakkos") is False  # owner (case-insensitive)
    assert contrib.is_external("jsakkos") is False
    assert contrib.is_external("dependabot[bot]") is False  # [bot] suffix
    assert contrib.is_external("renovate") is False  # explicit bot login
    assert contrib.is_external("github-actions") is False
    assert contrib.is_external("") is False
    assert contrib.is_external(None) is False


def test_extract_compare_logins_skips_null_and_dedups(contrib):
    payload = {
        "commits": [
            {"author": {"login": "katelovescode"}},
            {"author": None},  # no GitHub account -> skipped
            {"author": {"login": "katelovescode"}},  # duplicate -> collapsed
            {"author": {"login": "Jsakkos"}},
        ]
    }
    assert contrib.extract_compare_logins(payload) == ["katelovescode", "Jsakkos"]


def test_render_release_section_first_timers_first_and_flagged(contrib):
    out = contrib.render_release_section(
        current=["zoe", "katelovescode", "Jsakkos"],
        first_timers=["katelovescode"],
    )
    assert out == (
        "### Contributors\n"
        "\n"
        "Thanks to the people whose work shipped in this release:\n"
        "\n"
        "- @katelovescode 🎉 (first contribution!)\n"
        "- @zoe"
    )


def test_render_release_section_empty_when_no_externals(contrib):
    assert contrib.render_release_section(["Jsakkos", "dependabot[bot]"], []) == ""


def test_render_roster_sorted_and_formatted(contrib):
    out = contrib.render_roster([("zoe", "v0.16.0"), ("katelovescode", "v0.15.0")])
    assert out == (
        "# Contributors\n"
        "\n"
        "Engram is built primarily by its maintainer, but these community "
        "contributors have shipped improvements — thank you!\n"
        "\n"
        "- [@katelovescode](https://github.com/katelovescode) — first contribution: v0.15.0\n"
        "- [@zoe](https://github.com/zoe) — first contribution: v0.16.0\n"
    )


def _fake_run(responses):
    """Return a fake `run` that maps a substring of the joined command to stdout."""

    def run(cmd):
        joined = " ".join(cmd)
        for needle, output in responses.items():
            if needle in joined:
                return output
        raise AssertionError(f"unexpected command: {joined}")

    return run


def test_build_release_section_flags_first_timer(contrib):
    run = _fake_run(
        {
            "compare/v0.14.1...v0.15.0": json.dumps(
                {
                    "commits": [
                        {"author": {"login": "katelovescode"}},
                        {"author": {"login": "Jsakkos"}},
                    ]
                }
            ),
            # No prior commits by Kate before v0.14.1 -> first-timer.
            "author=katelovescode": json.dumps([]),
        }
    )
    out = contrib.build_release_section("Jsakkos/engram", "v0.14.1", "v0.15.0", run=run)
    assert out == (
        "### Contributors\n"
        "\n"
        "Thanks to the people whose work shipped in this release:\n"
        "\n"
        "- @katelovescode 🎉 (first contribution!)"
    )


def test_build_release_section_returning_contributor_not_flagged(contrib):
    run = _fake_run(
        {
            "compare/v0.15.0...v0.16.0": json.dumps(
                {"commits": [{"author": {"login": "katelovescode"}}]}
            ),
            # Kate already has a commit before v0.15.0 -> returning.
            "author=katelovescode": json.dumps([{"sha": "abc"}]),
        }
    )
    out = contrib.build_release_section("Jsakkos/engram", "v0.15.0", "v0.16.0", run=run)
    assert out.endswith("- @katelovescode")
    assert "first contribution" not in out


def test_main_release_section_degrades_to_empty_on_error(contrib, capsys):
    def boom(cmd):
        raise RuntimeError("gh exploded")

    rc = contrib.main(
        ["--release-section", "--from", "v0.14.1", "--to", "v0.15.0", "--repo", "x/y"],
        run=boom,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""  # nothing printed -> section omitted
    assert "warning" in captured.err.lower()
