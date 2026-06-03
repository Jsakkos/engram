# Streamline Release-PR CI — Implementation & Rollout Plan

**Date:** 2026-06-02
**Branch:** `chore/streamline-release-ci`
**Design:** [`docs/superpowers/specs/2026-06-02-streamline-release-ci-design.md`](../specs/2026-06-02-streamline-release-ci-design.md)

## Goal

Reduce a `chore: release vX.Y.Z` PR to the checks it actually needs, without
breaking branch protection and without weakening `release.yml`.

## Detection mechanism

New `detect-release` job in `ci.yml` outputs `is_release` and `os_matrix`.
`is_release=true` **iff** the title (`pull_request.title`, or `head_commit.message`
on push) matches `^chore: release v[0-9]` **AND** every changed file (git diff vs
base, `fetch-depth: 0`) is in the allowlist:

```
backend/pyproject.toml
backend/app/__init__.py
backend/uv.lock
frontend/package.json
frontend/package-lock.json
CHANGELOG.md
```

Title is the necessary signal (a Renovate/Dependabot lock-file bump touches
`uv.lock`/`package-lock.json` but is titled `build(deps):`, so it stays
`false` → full CI). The allowlist is the belt-and-suspenders guard (a "release"
PR that also edited source → `false` → full CI).

Dry-run verification (all pass — see PR description / session log):

| Scenario | Title | Files | `is_release` |
|---|---|---|---|
| normal code PR | `fix: …` | source files | `false` |
| dependency bump | `build(deps): …` | `uv.lock` | `false` |
| release-only | `chore: release v0.15.0` | version set + CHANGELOG | `true` |
| release + source | `chore: release v0.15.0` | + `extractor.py` | `false` |
| release, empty diff | `chore: release v0.15.0` | — | `false` |
| release merge commit | `chore: release v0.15.0 (#299)` | version + CHANGELOG | `true` |
| lookalike | `chore: release validation tweaks` | source | `false` |

## Required-check strategy: single `CI Gate` aggregator

New `ci-gate` job (`name: CI Gate`, `if: always()`) `needs:` every granular job
and fails iff any has `result` ∈ {`failure`,`cancelled`}; `skipped`/`success`
pass. Branch protection is flipped from the 9 granular contexts to the single
`CI Gate`. This is what makes skipping the heavy jobs safe — a *required* check
that never reports would block the PR forever; an aggregator always reports.

Side benefit: `changelog-version-check` (previously not required) now feeds the
gate, so a missing changelog section blocks a release PR.

## Jobs skipped vs kept on a release-only PR

**Kept (the cheap safety net):**
- `changelog-version-check` — the release-specific gate
- `backend-lint`
- `backend-test-unit` — ubuntu-only via dynamic matrix (`os_matrix`)
- `backend-smoke` — import + boot probe
- `frontend-lint-build` — validates the bumped `package.json` builds

**Skipped (`if: needs.detect-release.outputs.is_release != 'true'`):**
- `backend-test-unit` Windows leg (dropped from the matrix, not gated)
- `backend-test-integration`
- `backend-coverage`
- `dependency-resolution` (6-job matrix)
- `dependency-resolution-py314-guard`
- `alembic-check`
- `frontend-test-unit`
- `e2e-tests`

A custom `if:` without a status function is implicitly ANDed with `success()`,
so these gates also preserve the normal "don't run if a needed job failed"
behavior. Only `ci-gate` uses `always()`.

**Non-required workflows trimmed (title-only guard, can't block):**
- `codeql.yml` `analyze` — skipped on release PR and release push (weekly cron +
  normal PRs untouched).
- `docker.yml` `docker` — skips only the `pull_request` image build on release
  PRs. Tag/`workflow_dispatch` builds (the real release image) untouched.

**Not touched:** `release.yml`, `tag-release.yml`, `publish.yml`,
`download-stats.yml`, `docs.yml`, `dependabot-auto-merge.yml`.

## Branch-protection changes

Apply via the dedicated `required_status_checks` sub-resource (PATCH, not a full
PUT) so the other protections (linear history, conversation resolution,
force-push block, `strict: true`) are preserved.

**Apply (after this PR merges):**

```bash
gh api -X PATCH repos/Jsakkos/engram/branches/main/protection/required_status_checks \
  --input ci-gate-required.json
```
`ci-gate-required.json`:
```json
{"strict": true, "checks": [{"context": "CI Gate"}]}
```

**Rollback:**

```bash
gh api -X PATCH repos/Jsakkos/engram/branches/main/protection/required_status_checks \
  --input ci-gate-rollback.json
```
`ci-gate-rollback.json`:
```json
{"strict": true, "checks": [
  {"context": "Backend Lint"},
  {"context": "Backend Tests (unit) (ubuntu-latest)"},
  {"context": "Backend Tests (unit) (windows-latest)"},
  {"context": "Backend Tests (integration)"},
  {"context": "Backend Smoke Test"},
  {"context": "Alembic Migration Sanity"},
  {"context": "Frontend Lint & Build"},
  {"context": "Frontend Unit Tests"},
  {"context": "E2E Tests"}
]}
```

## Rollout order (cannot self-block)

1. This PR is a **normal** PR → `is_release=false` → full CI → produces all 9
   legacy contexts **and** the new `CI Gate` context → mergeable under the
   current (9-context) protection. (Does not require flipping protection first.)
2. Squash-merge.
3. **Immediately** run the apply PATCH above → `CI Gate` becomes the sole required
   context.
4. The next `chore: release` PR: heavy jobs skip, `CI Gate` stays green, PR is
   mergeable (not BLOCKED). `tag-release.yml` → `release.yml` then does the real
   3-platform binary validation on merge.

If anything looks wrong between steps 2 and 3, the legacy contexts still exist and
still report on normal PRs, so leaving protection on the 9 contexts (i.e. not
running step 3) is a safe no-op fallback while the workflow change bakes.

> **⚠️ Flip-window hazard — do NOT open a release PR between step 2 (merge) and
> step 3 (PATCH).** In that window, branch protection still requires the 9 legacy
> contexts, but a release-only PR (`is_release=true`) drops the Windows unit leg
> via the dynamic matrix, so `Backend Tests (unit) (windows-latest)` never reports
> → the release PR is permanently BLOCKED until the PATCH lands. Mitigation: run
> step 3 **immediately** after merge (the agent/maintainer owns this), and don't
> cut a release until `gh api .../required_status_checks` shows only `CI Gate`.
> The repo is currently solo (no other contributors opening PRs), so the window is
> safe in practice — this warning guards the "forgot to flip" scenario.

## Rollback story

- **Protection only:** run the rollback PATCH (restores the 9 contexts).
- **Workflows:** `git revert` the implementation commit. Safe to do independently —
  the legacy job names are unchanged, so they keep reporting; only `detect-release`
  and `ci-gate` disappear. If protection was already flipped to `CI Gate`, run the
  rollback PATCH too (since `CI Gate` would no longer be produced after a revert).

## Verification

- `act` not installed → validated by: (1) PyYAML parse of all three workflows
  (pass, 14/1/1 jobs); (2) shell dry-run of `detect-release` across the 7
  scenarios above (all correct); (3) live on this PR — confirm all granular jobs
  run + `CI Gate` is green; (4) post-merge, confirm a `chore: release` PR skips the
  heavy jobs while `CI Gate` stays green and the PR is mergeable.
