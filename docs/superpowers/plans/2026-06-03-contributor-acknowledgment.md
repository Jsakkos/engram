# Contributor Acknowledgment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically credit external contributors (release-notes section, PR welcome comment, CHANGELOG inline credit, `CONTRIBUTORS.md` roster) and retroactively credit `@katelovescode` for v0.15.0.

**Architecture:** One stdlib Python helper (`backend/scripts/contributors.py`) is the single source of truth for "who counts" + rendering; it powers the release-notes section (in `release.yml`) and the roster. A `pull_request_target` workflow posts a merge-time thank-you using a dependency-free, unit-tested `.cjs` decision module. Docs codify the inline-credit convention and roster-refresh step. Retroactive outward-facing actions are gated behind explicit user confirmation.

**Tech Stack:** Python 3.11 (stdlib only — runs under plain `python3` in CI), pytest, GitHub Actions (`actions/github-script`, `gh` CLI), Node built-in test runner (`node:test`).

---

## File Structure

- **Create** `backend/scripts/contributors.py` — classification + rendering (pure) and `git`/`gh` I/O (injectable seam) + CLI (`--release-section`, `--roster`).
- **Create** `backend/tests/unit/test_contributors.py` — unit tests (pure functions + fake-`run` integration).
- **Modify** `backend/tests/unit/conftest.py` — add a `contrib` session fixture (mirrors existing `ecl`).
- **Create** `.github/scripts/contributor-greeting.cjs` — pure `decide()` / `renderComment()` for the welcome workflow.
- **Create** `.github/scripts/contributor-greeting.test.cjs` — `node:test` unit tests for the decision logic.
- **Create** `.github/workflows/contributor-welcome.yml` — merge-time thank-you (comment-only, `pull_request_target`).
- **Modify** `.github/workflows/release.yml` — insert the Contributors section into the release-notes step.
- **Create** `CONTRIBUTORS.md` — roster, seeded with `@katelovescode`.
- **Modify** `README.md` — link the roster from Acknowledgments.
- **Modify** `CONTRIBUTING.md` — add "Acknowledging contributors" section + roster-refresh release step.
- **Modify** `CLAUDE.md` — document inline-credit convention + roster refresh in the release flow.
- **Modify** `CHANGELOG.md` — inline credit on the v0.15.0 `#294` entry.

---

## Task 1: `contributors.py` — classification + rendering (pure functions)

**Files:**
- Create: `backend/scripts/contributors.py`
- Modify: `backend/tests/unit/conftest.py` (add `contrib` fixture)
- Test: `backend/tests/unit/test_contributors.py`

- [ ] **Step 1: Add the `contrib` fixture to conftest**

In `backend/tests/unit/conftest.py`, after the `ecl` fixture (around line 64), add:

```python
@pytest.fixture(scope="session")
def contrib():
    """The contributors.py module, loaded once per pytest session."""
    return _load_script_module("contributors")
```

- [ ] **Step 2: Write the failing tests for classification + rendering**

Create `backend/tests/unit/test_contributors.py`:

```python
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
    assert contrib.is_external("Jsakkos") is False          # owner (case-insensitive)
    assert contrib.is_external("jsakkos") is False
    assert contrib.is_external("dependabot[bot]") is False   # [bot] suffix
    assert contrib.is_external("renovate") is False          # explicit bot login
    assert contrib.is_external("github-actions") is False
    assert contrib.is_external("") is False
    assert contrib.is_external(None) is False


def test_extract_compare_logins_skips_null_and_dedups(contrib):
    payload = {
        "commits": [
            {"author": {"login": "katelovescode"}},
            {"author": None},                       # no GitHub account -> skipped
            {"author": {"login": "katelovescode"}}, # duplicate -> collapsed
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_contributors.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `contributors` (script doesn't exist yet).

- [ ] **Step 4: Create `contributors.py` with the pure logic**

Create `backend/scripts/contributors.py`:

```python
#!/usr/bin/env python3
"""Generate contributor acknowledgments from git/GitHub history.

Two modes:
  --release-section --from <PREV_TAG> --to <TAG>
      Print a Markdown "Contributors" block for the GitHub release body, or
      nothing (exit 0) when there are no external contributors.
  --roster
      Print the body of CONTRIBUTORS.md (external humans only).

Pure stdlib so it runs under plain `python3` in CI (the release job has no
`uv`). Shells out to `git` and `gh`; both are present on GitHub-hosted runners.
The GitHub token must be visible to `gh` (set `GH_TOKEN`/`GITHUB_TOKEN`).

Design: the *classification* and *rendering* logic is pure and unit-tested; the
`git`/`gh` calls go through a single injectable `run` seam so tests never touch
the network.
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_contributors.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint + format**

Run: `cd backend && uv run ruff check scripts/contributors.py tests/unit/test_contributors.py && uv run ruff format scripts/contributors.py tests/unit/test_contributors.py`
Expected: no errors. (Note: `OWNER_EMAILS` is defined but unused until Task 2 — ruff's `F` rules don't flag module-level constants, so this is fine.)

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/contributors.py backend/tests/unit/test_contributors.py backend/tests/unit/conftest.py
git commit -m "feat(scripts): contributor classification + rendering helpers"
```

---

## Task 2: `contributors.py` — git/gh I/O + CLI

**Files:**
- Modify: `backend/scripts/contributors.py` (append I/O + `main`)
- Test: `backend/tests/unit/test_contributors.py` (append fake-`run` tests)

- [ ] **Step 1: Write the failing tests for the I/O layer + CLI**

Append to `backend/tests/unit/test_contributors.py`:

```python
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
    run = _fake_run({
        "compare/v0.14.1...v0.15.0": json.dumps(
            {"commits": [
                {"author": {"login": "katelovescode"}},
                {"author": {"login": "Jsakkos"}},
            ]}
        ),
        # No prior commits by Kate before v0.14.1 -> first-timer.
        "author=katelovescode": json.dumps([]),
    })
    out = contrib.build_release_section("Jsakkos/engram", "v0.14.1", "v0.15.0", run=run)
    assert out == (
        "### Contributors\n"
        "\n"
        "Thanks to the people whose work shipped in this release:\n"
        "\n"
        "- @katelovescode 🎉 (first contribution!)"
    )


def test_build_release_section_returning_contributor_not_flagged(contrib):
    run = _fake_run({
        "compare/v0.15.0...v0.16.0": json.dumps(
            {"commits": [{"author": {"login": "katelovescode"}}]}
        ),
        # Kate already has a commit before v0.15.0 -> returning.
        "author=katelovescode": json.dumps([{"sha": "abc"}]),
    })
    out = contrib.build_release_section("Jsakkos/engram", "v0.15.0", "v0.16.0", run=run)
    assert out.endswith("- @katelovescode")
    assert "first contribution" not in out


def test_main_release_section_degrades_to_empty_on_error(contrib, capsys):
    def boom(cmd):
        raise RuntimeError("gh exploded")

    # Inject the failing run via the module-level default so main() picks it up.
    rc = contrib.main(
        ["--release-section", "--from", "v0.14.1", "--to", "v0.15.0", "--repo", "x/y"],
        run=boom,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""           # nothing printed -> section omitted
    assert "warning" in captured.err.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_contributors.py -k "build_release or main_release" -v`
Expected: FAIL — `AttributeError: module 'contributors' has no attribute 'build_release_section'` (and `main` doesn't accept `run=`).

- [ ] **Step 3: Append the I/O layer + CLI to `contributors.py`**

Append to `backend/scripts/contributors.py`:

```python
# --- git / gh I/O (single injectable seam) ---------------------------------


def _default_run(cmd: list[str]) -> str:
    """Run a command, returning stdout text; raises on non-zero exit."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _gh_json(path: str, run: Callable[[list[str]], str]):
    return json.loads(run(["gh", "api", path]))


def gh_compare_logins(repo: str, base: str, head: str, run: Callable[[list[str]], str]) -> list[str]:
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


def build_release_section(repo: str, prev: str, head: str, run: Callable[[list[str]], str] = _default_run) -> str:
    logins = gh_compare_logins(repo, prev, head, run)
    externals = [login for login in logins if is_external(login)]
    first_timers = [login for login in externals if not gh_author_has_commits(repo, login, prev, run)]
    return render_release_section(externals, first_timers)


def build_roster(repo: str, run: Callable[[list[str]], str] = _default_run) -> str:
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
    parser.add_argument("--release-section", action="store_true", help="print the release-notes block")
    parser.add_argument("--roster", action="store_true", help="print the CONTRIBUTORS.md body")
    parser.add_argument("--from", dest="from_ref", help="previous tag (release-section mode)")
    parser.add_argument("--to", dest="to_ref", help="current tag (release-section mode)")
    parser.add_argument("--repo", help="owner/name (defaults to $GITHUB_REPOSITORY or `gh repo view`)")
    args = parser.parse_args(argv)

    # Pin stdout to UTF-8 so emoji/em-dashes don't crash on a cp1252 console.
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
        except Exception as exc:  # noqa: BLE001 — intentional catch-all; see docstring
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_contributors.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Lint + format**

Run: `cd backend && uv run ruff check scripts/contributors.py tests/unit/test_contributors.py && uv run ruff format scripts/contributors.py tests/unit/test_contributors.py`
Expected: no errors. (`OWNER_EMAILS` remains defined for the documented email-fallback contract even though login resolution covers the current cases.)

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/contributors.py backend/tests/unit/test_contributors.py
git commit -m "feat(scripts): contributors git/gh I/O + CLI (release-section, roster)"
```

---

## Task 3: Wire the Contributors section into `release.yml`

**Files:**
- Modify: `.github/workflows/release.yml` (the "Generate release notes from CHANGELOG" step, ~lines 418–431)

- [ ] **Step 1: Replace the release-notes step**

In `.github/workflows/release.yml`, replace this exact block:

```yaml
      - name: Generate release notes from CHANGELOG
        run: |
          TAG="${{ inputs.tag || github.ref_name }}"
          VERSION="${TAG#v}"
          python3 backend/scripts/extract_changelog.py \
            --version "$VERSION" --changelog CHANGELOG.md > release-notes.md
          # Append a compare link to the previous tag (omitted for the first release).
          PREV=$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)
          if [ -n "$PREV" ]; then
            printf '\n\n---\n**Full Changelog**: https://github.com/%s/compare/%s...%s\n' \
              "${{ github.repository }}" "$PREV" "$TAG" >> release-notes.md
          fi
          echo "----- release-notes.md -----"
          cat release-notes.md
```

with:

```yaml
      - name: Generate release notes from CHANGELOG
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          TAG="${{ inputs.tag || github.ref_name }}"
          VERSION="${TAG#v}"
          python3 backend/scripts/extract_changelog.py \
            --version "$VERSION" --changelog CHANGELOG.md > release-notes.md
          # Previous tag drives both the contributors range and the compare link
          # (both omitted for the very first release).
          PREV=$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)
          if [ -n "$PREV" ]; then
            # Contributors section: external humans only, first-timers flagged.
            # Inserted between the changelog body and the Full Changelog footer.
            # The helper degrades to empty output on any error, so `|| true`
            # plus the emptiness check means acknowledgment never fails a release.
            SECTION=$(python3 backend/scripts/contributors.py \
              --release-section --from "$PREV" --to "$TAG" \
              --repo "${{ github.repository }}" || true)
            if [ -n "$SECTION" ]; then
              printf '\n\n%s\n' "$SECTION" >> release-notes.md
            fi
            printf '\n\n---\n**Full Changelog**: https://github.com/%s/compare/%s...%s\n' \
              "${{ github.repository }}" "$PREV" "$TAG" >> release-notes.md
          fi
          echo "----- release-notes.md -----"
          cat release-notes.md
```

- [ ] **Step 2: Validate the workflow YAML**

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml',encoding='utf-8')); print('release.yml parses OK')"`
Expected: `release.yml parses OK`. (If `actionlint` is installed, also run `actionlint .github/workflows/release.yml` and expect no errors.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat(release): append Contributors section to release notes"
```

---

## Task 4: PR welcome workflow + tested decision module

**Files:**
- Create: `.github/scripts/contributor-greeting.cjs`
- Create: `.github/scripts/contributor-greeting.test.cjs`
- Create: `.github/workflows/contributor-welcome.yml`

- [ ] **Step 1: Write the failing Node test for the decision logic**

Create `.github/scripts/contributor-greeting.test.cjs`:

```javascript
// Run with: node --test .github/scripts/
// Uses Node's built-in test runner — no npm install required.
const test = require("node:test");
const assert = require("node:assert");
const { decide } = require("./contributor-greeting.cjs");

test("owner / member / collaborator are skipped", () => {
  assert.equal(decide("OWNER", "Jsakkos"), "skip");
  assert.equal(decide("MEMBER", "someone"), "skip");
  assert.equal(decide("COLLABORATOR", "someone"), "skip");
});

test("bots are skipped regardless of association", () => {
  assert.equal(decide("CONTRIBUTOR", "dependabot[bot]"), "skip");
  assert.equal(decide("CONTRIBUTOR", "renovate"), "skip");
  assert.equal(decide("FIRST_TIME_CONTRIBUTOR", "github-actions"), "skip");
});

test("first-time external contributor gets the first-timer greeting", () => {
  assert.equal(decide("FIRST_TIME_CONTRIBUTOR", "katelovescode"), "first");
});

test("returning external contributor gets repeat thanks", () => {
  assert.equal(decide("CONTRIBUTOR", "katelovescode"), "repeat");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && node --test .github/scripts/`
Expected: FAIL — `Cannot find module './contributor-greeting.cjs'`.

- [ ] **Step 3: Create the decision module**

Create `.github/scripts/contributor-greeting.cjs`:

```javascript
// Pure decision + message templates for the contributor-welcome workflow.
// Dependency-free and standalone so it can be unit-tested with
// `node --test .github/scripts/` (Node's built-in runner, no npm install).

const INTERNAL = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
const BOT_LOGINS = new Set(["dependabot", "renovate", "github-actions"]);

function isBot(login) {
  const lowered = String(login).toLowerCase();
  return lowered.endsWith("[bot]") || BOT_LOGINS.has(lowered);
}

/** Returns "first" | "repeat" | "skip". */
function decide(association, login) {
  if (isBot(login) || INTERNAL.has(association)) return "skip";
  if (association === "FIRST_TIME_CONTRIBUTOR") return "first";
  return "repeat";
}

function renderComment(action, login) {
  if (action === "first") {
    return [
      `🎉 Thank you for your first contribution to Engram, @${login}!`,
      ``,
      `Your work will be credited in the next release's notes and added to ` +
        `[CONTRIBUTORS.md](../blob/main/CONTRIBUTORS.md). If you'd like to keep ` +
        `contributing, the [contributing guide](../blob/main/CONTRIBUTING.md) ` +
        `has everything you need to get a dev environment running.`,
      ``,
      `Welcome aboard! 🚀`,
    ].join("\n");
  }
  return (
    `Thanks again for another contribution, @${login}! 🙌 ` +
    `It'll be credited in the next release's notes.`
  );
}

module.exports = { decide, renderComment, isBot };
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && node --test .github/scripts/`
Expected: PASS (4 tests, `# pass 4`).

- [ ] **Step 5: Create the workflow**

Create `.github/workflows/contributor-welcome.yml`:

```yaml
name: Contributor welcome

# Thank external contributors when their PR merges, with an extra-warm welcome
# for first-timers. Comment-only: this job never checks out or executes the
# PR's code, so pull_request_target (which grants a write token, needed to
# comment on fork PRs) is safe here.
on:
  pull_request_target:
    types: [closed]

permissions:
  pull-requests: write

jobs:
  thank:
    if: github.event.pull_request.merged == true
    runs-on: ubuntu-latest
    steps:
      # Default ref under pull_request_target is the BASE branch (trusted repo
      # code), not the fork's PR head — so requiring the greeting script is safe.
      - uses: actions/checkout@v4
      - uses: actions/github-script@v7
        with:
          script: |
            const { decide, renderComment } = require(
              `${process.env.GITHUB_WORKSPACE}/.github/scripts/contributor-greeting.cjs`
            );
            const pr = context.payload.pull_request;
            const action = decide(pr.author_association, pr.user.login);
            if (action === "skip") {
              core.info(`Skipping @${pr.user.login} (${pr.author_association})`);
              return;
            }
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: pr.number,
              body: renderComment(action, pr.user.login),
            });
```

- [ ] **Step 6: Validate the workflow YAML**

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/contributor-welcome.yml',encoding='utf-8')); print('contributor-welcome.yml parses OK')"`
Expected: `contributor-welcome.yml parses OK`. (If `actionlint` is available, run it too and expect no errors.)

- [ ] **Step 7: Commit**

```bash
git add .github/scripts/contributor-greeting.cjs .github/scripts/contributor-greeting.test.cjs .github/workflows/contributor-welcome.yml
git commit -m "feat(ci): thank external contributors on PR merge"
```

---

## Task 5: `CONTRIBUTORS.md` roster + README link

**Files:**
- Create: `CONTRIBUTORS.md`
- Modify: `README.md` (Acknowledgments section)

- [ ] **Step 1: Create the seeded roster**

Create `CONTRIBUTORS.md` (this is exactly what `contributors.py --roster` produces today, with one external contributor):

```markdown
# Contributors

Engram is built primarily by its maintainer, but these community contributors have shipped improvements — thank you!

- [@katelovescode](https://github.com/katelovescode) — first contribution: v0.15.0
```

- [ ] **Step 2: Link the roster from the README Acknowledgments**

In `README.md`, the Acknowledgments section currently reads:

```markdown
## Acknowledgments

- [MakeMKV](https://www.makemkv.com/) for disc decryption
- [mkv-episode-matcher](https://github.com/Jsakkos/mkv-episode-matcher) for audio fingerprinting
- [TheDiscDB](https://thediscdb.com/) for disc content-hash lookups
- [TMDB](https://www.themoviedb.org/) for media metadata and poster art
```

Add a contributors line after the tools list (kept visually separate with a blank line):

```markdown
## Acknowledgments

- [MakeMKV](https://www.makemkv.com/) for disc decryption
- [mkv-episode-matcher](https://github.com/Jsakkos/mkv-episode-matcher) for audio fingerprinting
- [TheDiscDB](https://thediscdb.com/) for disc content-hash lookups
- [TMDB](https://www.themoviedb.org/) for media metadata and poster art

And thank you to Engram's community [contributors](CONTRIBUTORS.md) 💜
```

- [ ] **Step 3: Verify the helper reproduces the committed roster (manual, optional)**

If you have `gh` authenticated locally, sanity-check that the generator matches the seeded file:

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && python3 backend/scripts/contributors.py --roster --repo Jsakkos/engram`
Expected: output identical to `CONTRIBUTORS.md`. (Skip if `gh` isn't authenticated — the seeded file stands on its own; the unit tests already pin the rendering.)

- [ ] **Step 4: Commit**

```bash
git add CONTRIBUTORS.md README.md
git commit -m "docs: add CONTRIBUTORS.md roster and link from README"
```

---

## Task 6: Document the convention + roster-refresh step

**Files:**
- Modify: `CONTRIBUTING.md` (add "Acknowledging contributors" section + release-step note)
- Modify: `CLAUDE.md` (Release/Changelog section)

- [ ] **Step 1: Add an "Acknowledging contributors" section to CONTRIBUTING.md**

In `CONTRIBUTING.md`, after the `## Releases` section, add a new section:

```markdown
## Acknowledging contributors

External contributors are credited automatically and durably:

- **Release notes** — every release body gets a **Contributors** section listing
  external contributors (first-timers flagged 🎉), generated by
  `backend/scripts/contributors.py --release-section` inside `release.yml`. No
  manual step.
- **PR thank-you** — `.github/workflows/contributor-welcome.yml` comments a
  thank-you when an external PR merges (extra-warm for first-timers).
- **Changelog inline credit (convention)** — when a changelog entry describes an
  external contribution, append `(#NNN, thanks @user!)`. This is curation, not a
  CI gate; the release-notes section is the can't-forget safety net.
- **Roster** — `CONTRIBUTORS.md` lists external contributors. Regenerate it as a
  step in each `chore: release vX.Y.Z` PR (see below) so it never needs a bot
  push to protected `main`.
```

- [ ] **Step 2: Add the roster-refresh step to the release checklist**

Still in `CONTRIBUTING.md`, in the `## Releases` section, add a bullet to the release steps:

```markdown
- Refresh the contributor roster: run
  `python backend/scripts/contributors.py --roster --repo Jsakkos/engram > CONTRIBUTORS.md`
  (requires an authenticated `gh`) and include the diff in the release PR.
```

- [ ] **Step 3: Document in CLAUDE.md**

In `CLAUDE.md`, under the `## Release and Changelog` section, add a bullet:

```markdown
- **Contributor credit is automated.** External contributors are listed in each
  release body via `backend/scripts/contributors.py --release-section` (wired into
  `release.yml`) and thanked on PR merge by `contributor-welcome.yml`. Convention:
  changelog entries for external contributions append `(#NNN, thanks @user!)`.
  As part of the `chore: release` PR, regenerate `CONTRIBUTORS.md` with
  `python backend/scripts/contributors.py --roster --repo Jsakkos/engram > CONTRIBUTORS.md`
  (a human commit — protected `main` rejects bot pushes, GH006).
```

- [ ] **Step 4: Commit**

```bash
git add CONTRIBUTING.md CLAUDE.md
git commit -m "docs: document contributor acknowledgment process"
```

---

## Task 7: Retroactive credit for v0.15.0 (CHANGELOG, in-repo)

**Files:**
- Modify: `CHANGELOG.md` (v0.15.0 `#294` entry)

- [ ] **Step 1: Add inline credit to the v0.15.0 #294 entry**

In `CHANGELOG.md`, in the `## [0.15.0]` section, the `#294` entry currently ends:

```markdown
Both clear automatically the moment a token is saved, with no page reload. (#294)
```

Change the trailing reference to credit Kate:

```markdown
Both clear automatically the moment a token is saved, with no page reload. (#294, thanks @katelovescode!)
```

- [ ] **Step 2: Verify the changelog still extracts cleanly**

Run: `cd "C:\Github\engram\.claude\worktrees\wonderful-mendel-3b6b89" && python3 backend/scripts/extract_changelog.py --version 0.15.0 --changelog CHANGELOG.md | head -5`
Expected: prints the 0.15.0 section (now showing `thanks @katelovescode!`), exit 0.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): credit @katelovescode for #294 in v0.15.0"
```

---

## Task 8: Retroactive outward-facing actions (GATED — confirm with user)

> ⚠️ These actions are **outward-facing and not part of the automated commits**. Before running each, show the user the exact text and get explicit confirmation. Do NOT run unprompted.

**8a — Edit the published v0.15.0 release body**

- [ ] **Step 1: Show the user the exact section to append, then run**

Append the Contributors section to the live release body (this is the same block the automation would have produced):

```bash
gh release view v0.15.0 --json body -q .body > /tmp/v0150-body.md
# Append (after confirming text with the user):
printf '\n\n### Contributors\n\nThanks to the people whose work shipped in this release:\n\n- @katelovescode 🎉 (first contribution!)\n' >> /tmp/v0150-body.md
gh release edit v0.15.0 --notes-file /tmp/v0150-body.md
```

Note: the `**Full Changelog**` footer is the last line of the existing body; appending after it is acceptable, or insert the section before that footer if the user prefers. Confirm placement with the user.

**8b — Post a thank-you on PR #294**

- [ ] **Step 1: Show the user the comment text, then post**

```bash
gh pr comment 294 --body "🎉 Belated but heartfelt thanks for this, @katelovescode — your first contribution to Engram shipped in v0.15.0 (the TMDB health-warning banner). We've added you to CONTRIBUTORS.md and credited you in the release notes. Going forward this'll be automatic — sorry it took a manual nudge this time. Welcome aboard! 🚀"
```

(Adjust wording per the user's tone preference — see the external-comms tone memory: no over-apologizing.)

---

## Self-Review

**1. Spec coverage:**
- Shared definition / `contributors.py` helper → Tasks 1–2. ✅
- Release-notes Contributors section (§2) → Task 3. ✅
- PR welcome workflow (§3) → Task 4. ✅
- CHANGELOG inline-credit convention (§4) → documented in Task 6; applied retroactively in Task 7. ✅
- `CONTRIBUTORS.md` roster (§5), external-humans-only, README link → Task 5. ✅
- Retroactive: thank-you, roster seed, changelog credit, live release edit (§6) → roster seed in Task 5, changelog credit in Task 7, thank-you + release edit gated in Task 8. ✅
- Testing (§7): `contributors.py` unit tests → Tasks 1–2; workflow YAML parse + `actionlint` → Tasks 3–4; welcome decision unit test → Task 4. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows complete content. The only gated/ambiguous steps (Task 8) are intentionally human-confirmed outward-facing actions, with exact commands shown. ✅

**3. Type consistency:** `is_external`, `extract_compare_logins`, `render_release_section(current, first_timers)`, `render_roster(entries)`, `build_release_section(repo, prev, head, run)`, `build_roster(repo, run)`, `main(argv, run)` — names/signatures are identical wherever referenced across Tasks 1–3. The `.cjs` exports `decide`/`renderComment`/`isBot`, used consistently in the test and workflow. ✅
