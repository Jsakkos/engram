#!/usr/bin/env python3
"""Generate contributor acknowledgments from git/GitHub history.

Two modes:
  --release-section --from <PREV_TAG> --to <TAG>
      Print a Markdown "Contributors" block for the GitHub release body, or
      nothing (exit 0) when there are no external contributors.
  --roster
      Print the body of CONTRIBUTORS.md (external humans only).

Pure stdlib so it runs under plain ``python3`` in CI (the release job has no
``uv``). Shells out to ``git`` and ``gh``; both are present on GitHub-hosted
runners. The GitHub token must be visible to ``gh`` (set ``GH_TOKEN`` /
``GITHUB_TOKEN``).

Design: the *classification* and *rendering* logic is pure and unit-tested; the
``git`` / ``gh`` calls go through a single injectable ``run`` seam so tests never
touch the network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# --- "who counts" -----------------------------------------------------------

OWNER_LOGINS = {"jsakkos"}
OWNER_EMAILS = {"jonathansakkos@gmail.com", "jonathansakkos@protonmail.com"}
BOT_LOGINS = {"dependabot", "renovate", "github-actions"}

_VERSION_TAG = re.compile(r"^v?\d+\.\d+\.\d+$")


def is_bot(login: str) -> bool:
    """True for an automation account login."""
    lowered = login.lower()
    return lowered.endswith("[bot]") or lowered in BOT_LOGINS


def is_owner(login: str) -> bool:
    return login.lower() in OWNER_LOGINS


def is_external(login: str | None) -> bool:
    """True for a human contributor who is neither the owner nor a bot."""
    return bool(login) and not is_bot(login) and not is_owner(login)


# --- pure parsing / rendering ----------------------------------------------


def extract_compare_logins(compare_json: dict) -> list[str]:
    """Deduplicated author logins from a GitHub compare API payload.

    Commits with no resolvable GitHub account (``author == null``) are skipped —
    we never fall back to a raw email in output (privacy). Order is preserved.
    """
    seen: dict[str, None] = {}
    for commit in compare_json.get("commits", []):
        author = commit.get("author")
        login = author.get("login") if author else None
        if login:
            seen.setdefault(login, None)
    return list(seen)


def render_release_section(current: Iterable[str], first_timers: Iterable[str]) -> str:
    """Markdown 'Contributors' block, or '' when there are no externals.

    First-timers are listed first and flagged; both groups are sorted
    case-insensitively for deterministic output.
    """
    externals = sorted({c for c in current if is_external(c)}, key=str.lower)
    if not externals:
        return ""
    ft = {c.lower() for c in first_timers}
    firsts = [c for c in externals if c.lower() in ft]
    repeats = [c for c in externals if c.lower() not in ft]
    lines = [
        "### Contributors",
        "",
        "Thanks to the people whose work shipped in this release:",
        "",
    ]
    lines += [f"- @{login} 🎉 (first contribution!)" for login in firsts]
    lines += [f"- @{login}" for login in repeats]
    return "\n".join(lines)


def render_roster(entries: list[tuple[str, str | None]]) -> str:
    """CONTRIBUTORS.md body from (login, first_version) pairs.

    Sorted by first-contribution version then login. ``first_version`` may be
    None when it can't be determined; the suffix is then omitted.
    """
    intro = (
        "# Contributors\n"
        "\n"
        "Engram is built primarily by its maintainer, but these community "
        "contributors have shipped improvements — thank you!\n"
    )

    def sort_key(entry: tuple[str, str | None]) -> tuple[str, str]:
        login, version = entry
        return (version or "", login.lower())

    lines = [intro]
    for login, version in sorted(entries, key=sort_key):
        suffix = f" — first contribution: {version}" if version else ""
        lines.append(f"- [@{login}](https://github.com/{login}){suffix}")
    return "\n".join(lines) + "\n"
