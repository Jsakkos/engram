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

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable

# --- "who counts" -----------------------------------------------------------

OWNER_LOGINS = {"jsakkos"}
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


# --- git / gh I/O (single injectable seam) ---------------------------------


def _default_run(cmd: list[str]) -> str:
    """Run a command, returning stdout text; raises on non-zero exit.

    Decode as UTF-8 explicitly: GitHub API payloads carry UTF-8 (em-dashes and
    emoji in commit messages), and the Windows default cp1252 would otherwise
    raise UnicodeDecodeError on the compare endpoint's large response.
    """
    return subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _gh_json(path: str, run: Callable[[list[str]], str]):
    return json.loads(run(["gh", "api", path]))


def gh_compare_logins(
    repo: str, base: str, head: str, run: Callable[[list[str]], str]
) -> list[str]:
    return extract_compare_logins(_gh_json(f"repos/{repo}/compare/{base}...{head}", run))


def gh_author_has_commits(repo: str, login: str, ref: str, run: Callable[[list[str]], str]) -> bool:
    """True if ``login`` authored any commit reachable from ``ref``."""
    data = _gh_json(f"repos/{repo}/commits?sha={ref}&author={login}&per_page=1", run)
    return len(data) > 0


def gh_all_contributor_logins(repo: str, run: Callable[[list[str]], str]) -> list[str]:
    """All contributor logins (single page of 100 — engram has far fewer)."""
    data = _gh_json(f"repos/{repo}/contributors?per_page=100", run)
    return [c["login"] for c in data if c.get("login")]


def git_version_tags_ascending(run: Callable[[list[str]], str]) -> list[str]:
    out = run(["git", "tag", "--sort=version:refname"])
    return [t for t in out.splitlines() if _VERSION_TAG.match(t.strip())]


# --- orchestration ----------------------------------------------------------


def build_release_section(
    repo: str, prev: str, head: str, run: Callable[[list[str]], str] = _default_run
) -> str:
    logins = gh_compare_logins(repo, prev, head, run)
    externals = [login for login in logins if is_external(login)]
    first_timers = [
        login for login in externals if not gh_author_has_commits(repo, login, prev, run)
    ]
    return render_release_section(externals, first_timers)


def build_roster(repo: str, run: Callable[[list[str]], str] = _default_run) -> str:
    """Build the full CONTRIBUTORS.md body.

    Makes up to O(contributors × tags) GitHub API calls to find each
    contributor's first version tag — acceptable at Engram's current scale
    (a handful of contributors, ~15 tags) but worth noting for future growth.
    """
    externals = [login for login in gh_all_contributor_logins(repo, run) if is_external(login)]
    tags = git_version_tags_ascending(run)
    entries: list[tuple[str, str | None]] = []
    for login in externals:
        first_version: str | None = None
        for tag in tags:
            if gh_author_has_commits(repo, login, tag, run):
                first_version = tag
                break
        entries.append((login, first_version))
    return render_roster(entries)


def _resolve_repo(explicit: str | None, run: Callable[[list[str]], str]) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    return run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()


def main(argv: list[str] | None = None, run: Callable[[list[str]], str] = _default_run) -> int:
    parser = argparse.ArgumentParser(description="Render contributor acknowledgments.")
    parser.add_argument(
        "--release-section", action="store_true", help="print the release-notes block"
    )
    parser.add_argument("--roster", action="store_true", help="print the CONTRIBUTORS.md body")
    parser.add_argument("--from", dest="from_ref", help="previous tag (release-section mode)")
    parser.add_argument("--to", dest="to_ref", help="current tag (release-section mode)")
    parser.add_argument(
        "--repo", help="owner/name (defaults to $GITHUB_REPOSITORY or `gh repo view`)"
    )
    args = parser.parse_args(argv)

    # Pin stdout to UTF-8 so emoji / em-dashes don't crash on a cp1252 console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if args.release_section:
        if not (args.from_ref and args.to_ref):
            parser.error("--release-section requires --from and --to")
        # Never fail the release build: any error -> omit the section.
        try:
            repo = _resolve_repo(args.repo, run)
            section = build_release_section(repo, args.from_ref, args.to_ref, run=run)
        except Exception as exc:  # noqa: BLE001 — intentional: acknowledgment must never fail a release
            print(f"warning: contributor section skipped: {exc}", file=sys.stderr)
            return 0
        if section:
            print(section)
        return 0

    if args.roster:
        # Run by a human at release-PR time — let errors surface loudly.
        repo = _resolve_repo(args.repo, run)
        print(build_roster(repo, run=run), end="")
        return 0

    parser.error("one of --release-section or --roster is required")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
