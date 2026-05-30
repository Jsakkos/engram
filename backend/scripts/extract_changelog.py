"""Extract a single version's section from the repo-root ``CHANGELOG.md``.

The release workflow (``.github/workflows/release.yml``) uses this to turn the
curated changelog into the GitHub release body, replacing GitHub's flat
auto-generated PR list. The CI guard in ``.github/workflows/ci.yml`` runs it in
``--check`` mode so a ``chore: release`` PR fails fast if its CHANGELOG section
is missing — before the ~20-minute binary build, not after.

Pure stdlib so it runs under plain ``python3`` in CI (the create-release job
has no ``uv``)::

    python3 scripts/extract_changelog.py --version 0.10.0    # print the section body
    python3 scripts/extract_changelog.py --version 0.10.0 --check   # validate only

The matched section excludes its own ``## [version]`` header line (the release
page already shows the title) and stops at the next ``##`` heading.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _normalize_version(version: str) -> str:
    """Drop a single leading ``v`` from a tag-style version (``v0.10.0`` → ``0.10.0``)."""
    if version[:1] == "v" and version[1:2].isdigit():
        return version[1:]
    return version


def extract_section(changelog_text: str, version: str) -> str | None:
    """Return the body of the ``## [version]`` section, or ``None`` if absent.

    The header line itself is excluded; the body runs up to (not including) the
    next level-2 ``##`` heading and is stripped of surrounding whitespace.
    """
    version = _normalize_version(version)
    header_re = re.compile(r"^##\s+\[" + re.escape(version) + r"\]")
    next_section_re = re.compile(r"^##\s")

    lines = changelog_text.splitlines()
    start = next((i for i, line in enumerate(lines) if header_re.match(line)), None)
    if start is None:
        return None

    body: list[str] = []
    for line in lines[start + 1 :]:
        if next_section_re.match(line):
            break
        body.append(line)
    return "\n".join(body).strip()


def _default_changelog() -> Path:
    """Repo-root ``CHANGELOG.md`` — scripts/ -> backend/ -> repo root."""
    return Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract a version's section from CHANGELOG.md.")
    parser.add_argument(
        "--version",
        required=True,
        help="version to extract, e.g. 0.10.0 (a leading 'v' is tolerated)",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=_default_changelog(),
        help="path to CHANGELOG.md (default: repo-root CHANGELOG.md)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the section exists; print a status line and emit no body",
    )
    args = parser.parse_args(argv)

    # The changelog contains non-Latin-1 characters (em dashes, arrows). Pin
    # stdout to UTF-8 so `print(section)` doesn't crash when the platform's
    # default encoding is cp1252 (Windows) or a non-UTF-8 CI locale. Guarded
    # because capture streams (pytest) don't always support reconfigure.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        # A capture/replacement stream (e.g. pytest) may lack reconfigure or
        # reject an encoding change mid-stream; fall back to default stdout.
        pass

    try:
        text = args.changelog.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read changelog {args.changelog}: {exc}", file=sys.stderr)
        return 1

    section = extract_section(text, args.version)
    if section is None:
        print(
            f"error: no CHANGELOG section found for version {args.version!r} in {args.changelog}",
            file=sys.stderr,
        )
        return 1

    if args.check:
        print(f"ok: CHANGELOG has a section for {args.version}")
        return 0

    print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
