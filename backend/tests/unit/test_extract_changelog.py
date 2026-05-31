"""Unit tests for scripts/extract_changelog.py.

The script pulls a single version's section out of the repo-root CHANGELOG.md
so the release workflow can use the curated changelog as the GitHub release
body (instead of GitHub's flat auto-generated PR list). These tests pin the
parser against a synthetic changelog plus one smoke check against the real
CHANGELOG.md.

The `ecl` fixture (loaded once per pytest session) lives in conftest.py.
"""

import tomllib
from pathlib import Path

import pytest

# Repo layout: backend/tests/unit/<this file>.
#   parents[2] == backend/, parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REAL_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_PYPROJECT = _REPO_ROOT / "backend" / "pyproject.toml"

_SAMPLE = """\
# Changelog

All notable changes to Engram will be documented in this file.

## [Unreleased]

## [0.10.0] - 2026-05-28

_Highlights: bundled fpcalc and bulk review actions._

### Added

- **Bulk actions in the review queue** — select multiple titles. (#249)

### Fixed

- **/review page rendered black** — nav linked a bare route. (#247)

## [0.9.1] - 2026-05-27

### Fixed

- **Episodic TV never auto-organizing** — vote-ratio path. (#239)

## [0.9.0-no-date]

### Added

- A section whose header carries no date suffix.
"""


def _write_sample(tmp_path: Path) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_SAMPLE, encoding="utf-8")
    return path


def test_extracts_section_body(ecl):
    section = ecl.extract_section(_SAMPLE, "0.10.0")
    assert section is not None
    # Body content is present...
    assert "_Highlights: bundled fpcalc and bulk review actions._" in section
    assert "### Added" in section
    assert "Bulk actions in the review queue" in section
    assert "### Fixed" in section


def test_excludes_version_header_line(ecl):
    # The release page already shows the title, so the body must not begin
    # with the "## [0.10.0]" header line.
    section = ecl.extract_section(_SAMPLE, "0.10.0")
    assert "## [0.10.0]" not in section
    assert section.lstrip().startswith("_Highlights")


def test_stops_at_next_version(ecl):
    # Must not bleed into the following 0.9.1 section.
    section = ecl.extract_section(_SAMPLE, "0.10.0")
    assert "0.9.1" not in section
    assert "vote-ratio path" not in section
    assert "Episodic TV never auto-organizing" not in section


def test_header_without_date_parses(ecl):
    section = ecl.extract_section(_SAMPLE, "0.9.0-no-date")
    assert section is not None
    assert "A section whose header carries no date suffix." in section


def test_leading_v_is_tolerated(ecl):
    assert ecl.extract_section(_SAMPLE, "v0.10.0") == ecl.extract_section(_SAMPLE, "0.10.0")


def test_unknown_version_returns_none(ecl):
    assert ecl.extract_section(_SAMPLE, "9.9.9") is None


def test_unreleased_placeholder_is_empty_and_not_leaked(ecl):
    # The [Unreleased] header exists but has no body, so it extracts to ""
    # (falsy) — not None, and not the next version's content. main() rejects
    # this empty result (see test_empty_section_rejected_by_check).
    assert ecl.extract_section(_SAMPLE, "Unreleased") == ""
    # A concrete-version query must never bleed into the [Unreleased] block.
    assert "[Unreleased]" not in ecl.extract_section(_SAMPLE, "0.10.0")


def test_main_default_prints_body(ecl, tmp_path, capsys):
    path = _write_sample(tmp_path)
    rc = ecl.main(["--version", "0.10.0", "--changelog", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bulk actions in the review queue" in out
    assert "## [0.10.0]" not in out


def test_main_check_present_returns_zero(ecl, tmp_path):
    path = _write_sample(tmp_path)
    assert ecl.main(["--version", "0.10.0", "--changelog", str(path), "--check"]) == 0


def test_main_check_absent_returns_nonzero(ecl, tmp_path):
    path = _write_sample(tmp_path)
    assert ecl.main(["--version", "9.9.9", "--changelog", str(path), "--check"]) != 0


def test_main_missing_version_reports_error(ecl, tmp_path, capsys):
    path = _write_sample(tmp_path)
    rc = ecl.main(["--version", "9.9.9", "--changelog", str(path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "9.9.9" in err


# A header that exists but carries no body — e.g. a stub `## [X.Y.Z]` added
# before the entry is written. This must be treated as "no usable section",
# not silently accepted (which would publish a blank release body).
_EMPTY_SECTION = "## [1.0.0]\n\n## [0.9.0]\n\n### Fixed\n\n- a real entry\n"


def test_empty_section_body_returns_falsy(ecl):
    assert not ecl.extract_section(_EMPTY_SECTION, "1.0.0")


def test_empty_section_rejected_by_check(ecl, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_EMPTY_SECTION, encoding="utf-8")
    assert ecl.main(["--version", "1.0.0", "--changelog", str(path), "--check"]) != 0


def test_empty_section_rejected_in_print_mode(ecl, tmp_path, capsys):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_EMPTY_SECTION, encoding="utf-8")
    rc = ecl.main(["--version", "1.0.0", "--changelog", str(path)])
    assert rc != 0
    assert capsys.readouterr().out.strip() == ""


@pytest.mark.skipif(
    not _REAL_CHANGELOG.exists() or not _PYPROJECT.exists(),
    reason="real CHANGELOG.md / pyproject.toml not present",
)
def test_real_changelog_has_section_for_current_version(ecl):
    version = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    section = ecl.extract_section(_REAL_CHANGELOG.read_text(encoding="utf-8"), version)
    assert section is not None, f"CHANGELOG.md has no section for current version {version}"
    assert "###" in section
