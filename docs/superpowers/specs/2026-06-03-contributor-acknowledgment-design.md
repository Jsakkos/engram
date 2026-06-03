# Contributor Acknowledgment — Design

**Date:** 2026-06-03
**Status:** Approved (brainstorm) — pending spec review
**Author:** Jonathan Sakkos (with Claude)

## Problem

External contributors — especially first-time contributors — ship work into Engram
with no visible acknowledgment. Release **v0.15.0** is the concrete missed
opportunity: [Kate Donaldson (`@katelovescode`)](https://github.com/katelovescode)
landed her **first** merged PR ([#294](https://github.com/Jsakkos/engram/pull/294),
the TMDB health-warning banner), and it shipped with zero credit anywhere.

### Why it happened (root cause)

The release body is generated **entirely** from the curated `CHANGELOG.md` via
`backend/scripts/extract_changelog.py`, and `release.yml` sets
`generate_release_notes: false`. That is a deliberate, good trade — curated prose
over GitHub's flat auto-PR list — but a side effect is that GitHub's built-in
**"New Contributors 🎉"** callout (the standard first-contribution
acknowledgment) is suppressed. Compounding it, the changelog convention references
PRs as `(#NNN)` but never the author, so authorship is erased by design. There is
no people-acknowledgment surface in the repo today: the README "Acknowledgments"
section lists upstream **tools** (MakeMKV, TMDB, …), there is no `CONTRIBUTORS`
file, and no all-contributors config.

## Goals

1. **Retroactive:** acknowledge Kate now for v0.15.0.
2. **Forward-looking:** a repeatable, low-discipline process so future external
   contributors — first-timers especially — are credited automatically.

## Non-goals

- No third-party GitHub App / bot install (rejected Approach B — all-contributors).
- No CI gate that can block a release on missing credit (the auto release section
  is the safety net; we don't want a brittle enforcement check).
- No acknowledgment of the repo owner or bots in any contributor surface.
- No social-media / external-channel automation.

## Strategy

**Approach A — lean & native automation.** Custom GitHub Actions plus one small
stdlib Python helper that fits the existing curated-changelog architecture. The two
surfaces where *forgetting* is the failure mode (release notes + the merge-time
thank-you) are automated; the roster rides the existing human-authored release PR
(sidestepping the protected-`main` bot-push block, GH006); the changelog inline
credit is a documented convention.

## Definitions (single source of truth)

These rules live in `backend/scripts/contributors.py` and are reused everywhere.

- **External contributor:** a GitHub **login** that is neither the owner nor a bot.
  - **Owner exclusion:** login `Jsakkos`; also his commit emails
    `jonathansakkos@gmail.com` and `jonathansakkos@protonmail.com` (email match is a
    fallback when a login can't be resolved).
  - **Bot exclusion:** any login ending in `[bot]`, plus the explicit set
    `{dependabot, renovate, github-actions}`.
- **Identity:** always resolved to GitHub **logins** via the GitHub commits/compare
  API (`gh api`), **never** raw emails in output (privacy). The helper degrades
  gracefully: if a login can't be resolved for a commit, that commit's author is
  matched against the owner-email fallback and otherwise skipped (logged to stderr).
- **First-timer (release section):** an external login with **zero merged commits
  reachable before `<PREV_TAG>`**. Computed from `git log` (the release job checks
  out with `fetch-depth: 0`, so full history is present).
- **First-timer (PR welcome):** GitHub's native `author_association` value
  `FIRST_TIME_CONTRIBUTOR` on the merge event — no API call needed.
- **Roster scope:** **external humans only.** The owner and all bots are excluded
  from `CONTRIBUTORS.md`.

## Components

### 1. `backend/scripts/contributors.py` — shared helper

Pure stdlib (mirrors `extract_changelog.py`), so it runs under plain `python3` in CI
(the release job has no `uv`). Shells out to `git` and `gh` (both present in the
release runner). Two modes:

- `--release-section --from <PREV_TAG> --to <TAG>`
  Prints a Markdown block for the release body, or **nothing** (exit 0, empty
  output) when there are no external contributors:

  ```markdown
  ### Contributors

  Thanks to the people whose work shipped in this release:

  - @katelovescode 🎉 (first contribution!)
  - @someoneelse
  ```

  First-timers are listed first and flagged `🎉 (first contribution!)`; returning
  contributors follow, each as a bare `- @login`. Logins are sorted
  case-insensitively within each group for determinism.

- `--roster`
  Prints the full `CONTRIBUTORS.md` body (see §5).

**Implementation notes**
- Resolve commit→login for the `<PREV_TAG>..<TAG>` range via
  `gh api repos/{owner}/{repo}/compare/{prev}...{to} --jq '.commits[].author.login'`
  (handles squash-merge authorship correctly — the PR author is the commit author
  on a squash merge). Deduplicate logins.
- For first-timer detection, get the set of all external logins that authored any
  commit reachable from `<PREV_TAG>` (one compare call from the repo's first commit,
  or `gh api ...?per_page=100` pagination / `git log` author scan as a fallback).
- `{owner}/{repo}` is derived from `gh repo view --json nameWithOwner` (or the
  `GITHUB_REPOSITORY` env var when set) so the script isn't hard-coded to a fork.
- All network/`gh` failures degrade to "omit the section / skip the contributor"
  with a stderr warning — a flaky API call must never fail the release build.

### 2. Release-notes Contributors section (`.github/workflows/release.yml`)

Extend the existing **"Generate release notes from CHANGELOG"** step. Order in
`release-notes.md`:

1. Curated changelog body (unchanged — `extract_changelog.py`).
2. **Contributors section** (new — `contributors.py --release-section`), inserted
   only if non-empty.
3. `**Full Changelog**` compare-link footer (unchanged).

`generate_release_notes: false` and the curated-changelog flow are **untouched**.
The new step computes `PREV` the same way the footer already does
(`git describe --tags --abbrev=0 "${TAG}^"`) and skips the section for the very
first release (no `PREV`).

### 3. PR welcome / thank-you (`.github/workflows/contributor-welcome.yml`)

- **Trigger:** `pull_request_target: { types: [closed] }`,
  guarded `if: github.event.pull_request.merged == true`.
- **Why `pull_request_target`:** fork PRs get a read-only token under
  `pull_request`, so a comment would 403. `pull_request_target` runs in the base
  repo with a write token. **Safe here** because the job only posts a comment and
  **never checks out or executes** the contributor's code.
- **Permissions:** `pull-requests: write` (nothing else).
- **Logic (`actions/github-script`):**
  - Skip if author is a bot (`login` ends `[bot]`) or
    `author_association` ∈ {`OWNER`, `MEMBER`, `COLLABORATOR`}.
  - `FIRST_TIME_CONTRIBUTOR` → warm first-timer welcome: thanks by name, notes they
    will appear in the next release's Contributors section and in `CONTRIBUTORS.md`,
    links `CONTRIBUTING.md`.
  - `CONTRIBUTOR` → concise repeat thank-you.
  - Exactly one comment per merged PR.

### 4. CHANGELOG inline-credit convention (documentation only)

Document in **CONTRIBUTING.md** (under a new "## Acknowledging contributors"
section) and in **CLAUDE.md** (Release/Changelog section): a changelog entry for an
external contribution appends `(#NNN, thanks @user!)`.

- **Not** CI-enforced — the auto release section (§2) is the can't-forget net.
- Overlap with §2 is intentional and accepted: a contributor may appear both inline
  (tied to the specific feature) and in the Contributors roster section.

### 5. `CONTRIBUTORS.md` roster (external humans only)

- Generated by `python backend/scripts/contributors.py --roster > CONTRIBUTORS.md`.
- Content: a short intro line + a bullet per external contributor —
  `- [@login](https://github.com/login) — first contribution: vX.Y.Z`.
  First-contribution version is derived from the earliest tag whose range first
  includes one of that login's commits (best-effort; falls back to omitting the
  version suffix if it can't be determined).
- **Refresh cadence:** a documented step in the existing `chore: release vX.Y.Z`
  PR ritual (already a human-authored commit → no GH006). Added to the release
  checklist in CONTRIBUTING.md / CLAUDE.md.
- README "Acknowledgments" gains a single line linking to `CONTRIBUTORS.md`, kept
  visually separate from the upstream-tools bullets.

### 6. Retroactive — credit Kate for v0.15.0 (one-time)

- Post a warm (belated) thank-you comment on
  [#294](https://github.com/Jsakkos/engram/pull/294).
- Seed `CONTRIBUTORS.md` with `@katelovescode` — first contribution: v0.15.0.
- Add inline credit to the v0.15.0 changelog entry for #294:
  `… with no page reload. (#294, thanks @katelovescode!)`.
- **Edit the published v0.15.0 release body** via `gh release edit v0.15.0` to
  append the Contributors section. ⚠️ Outward-facing edit to a live release — the
  exact appended text is shown to the user and confirmed **before** running.

## Testing

- **`contributors.py` unit tests** (`backend/tests/unit/`): drive the helper with
  canned `git log` / compare-API fixtures (inject via a thin seam around the
  `git`/`gh` calls). Cover: owner excluded (by login and by email fallback), bots
  excluded, first-timer flagged vs. returning contributor, empty input → empty
  section (no header), deterministic sort. Pure stdlib, no network.
- **Workflow static checks:** lint `release.yml` and `contributor-welcome.yml` with
  `actionlint` if available locally; otherwise YAML parse + manual review.
- **Welcome-workflow logic:** extract the author-association branching into a small
  pure JS function and unit-test its decision (skip / first-timer / repeat) over the
  five `author_association` values + a bot login. Document one manual end-to-end
  check on a throwaway fork PR.

## Risks & mitigations

- **Flaky `gh`/API during release** → helper degrades to omitting the section;
  release build never fails on acknowledgment.
- **Squash-merge authorship** → the squash commit's `author.login` is the PR author,
  so the compare-API approach attributes correctly; verified against #294.
- **`pull_request_target` security** → comment-only job, no checkout/execution of PR
  code; minimal `pull-requests: write` permission.
- **Protected `main` (GH006)** → roster regenerated inside the human release PR,
  never pushed by a workflow.
- **Identity drift** (contributor with multiple emails / no resolvable login) →
  login-first resolution with owner-email fallback; unresolved authors skipped with
  a stderr warning rather than leaking an email.

## Out of scope / deferred

- Non-code contribution typing (docs/design/bug-report emoji), as all-contributors
  offers — deferred; revisit if contribution volume grows.
- Live avatar image (contrib.rocks) in README — rejected in favor of a filtered,
  generated `CONTRIBUTORS.md` (clean bot/owner exclusion).
