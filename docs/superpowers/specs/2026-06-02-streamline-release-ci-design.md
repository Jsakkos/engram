# Streamline Release-PR CI — Design

**Date:** 2026-06-02
**Status:** Approved (design); implementation pending
**Branch:** `chore/streamline-release-ci`

## Problem

A release PR (titled `chore: release vX.Y.Z`) changes only version strings and
`CHANGELOG.md`, yet it currently triggers the full behavioral CI pipeline:
Playwright E2E (3 browsers), the 6-job `dependency-resolution` matrix, backend
integration + coverage, frontend unit tests, CodeQL-on-PR, and a Docker PR image
build — plus a duplicate full `ci.yml` run on the push to `main` after merge.

The released *code* was already validated when its source PRs merged. The release
tree differs from last-green `main` only in version strings + changelog, so almost
all of that work is redundant for a release-only change. We want to reduce a
release PR to the checks it actually needs — **without breaking branch protection**.

## The two hard constraints

### 1. Branch protection / required checks (the "green but BLOCKED" trap)

`main` requires these **9 named status checks** (verified via
`gh api repos/Jsakkos/engram/branches/main/protection`), with `strict: true`
(up-to-date branch required), squash-only, force-push blocked, conversation
resolution required:

```
Backend Lint
Backend Tests (unit) (ubuntu-latest)
Backend Tests (unit) (windows-latest)
Backend Tests (integration)
Backend Smoke Test
Alembic Migration Sanity
Frontend Lint & Build
Frontend Unit Tests
E2E Tests
```

Note the names are **job display names**, and the matrix legs append
`(ubuntu-latest)` / `(windows-latest)`. Notably, `Changelog Version Check`,
`Backend Coverage`, `Dependency Resolution …`, and the py314 guard are **not**
required.

A *required* status check that never reports (because its job was skipped at the
job level, or its whole workflow was filtered out) sits in "Expected — waiting"
forever → the PR is **permanently BLOCKED**. So we cannot simply add
`if:`-skips to the nine required jobs and leave branch protection as-is.

**Resolution — single aggregator required check.** Introduce one `CI Gate` job
that `needs:` every granular job and reports a single pass/fail. Flip branch
protection to require **only** `CI Gate`. The granular jobs become individually
non-required, so skipping any of them is safe. The aggregator is release-aware:
a *skipped* upstream job is acceptable (it was intentionally skipped), only a
*failed/cancelled* upstream job fails the gate. This also declutters normal PRs.

### 2. Lock-file ambiguity (release vs dependency bump)

A release bump **and** a Renovate/Dependabot dependency bump both touch
`backend/uv.lock` and `frontend/package-lock.json`. So "is this release-only?"
must key on a **release signal**, never on paths alone — otherwise a real
dependency bump would skip its own tests.

**Resolution — title signal AND strict file allowlist.** `is_release` is true
**iff**:
- the title (`pull_request.title`, or `head_commit.message` on push) starts with
  `chore: release v`, **AND**
- every changed file (git diff vs base) is in the allowlist:
  `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/uv.lock`,
  `frontend/package.json`, `frontend/package-lock.json`, `CHANGELOG.md`.

The title is the *necessary* signal (mirrors `tag-release.yml`'s existing
`contains(head_commit.message, 'chore: release v')` convention). The allowlist is
the belt-and-suspenders guard: a "release" PR that also edited source code fails
the allowlist and runs full CI. A dependency bump is titled `build(deps): …`, so
it never matches the title signal even though it touches the lock files.

## Architecture

All changes are additive to `ci.yml`, plus title-only skip guards on two
non-required workflows. **`release.yml` and `tag-release.yml` are not touched.**

### `ci.yml` — new `detect-release` job

```yaml
detect-release:
  name: Detect Release
  runs-on: ubuntu-latest
  outputs:
    is_release: ${{ steps.detect.outputs.is_release }}
    os_matrix: ${{ steps.detect.outputs.os_matrix }}
  steps:
    - uses: actions/checkout@v6
      with:
        fetch-depth: 0   # need base history for the changed-file diff
    - id: detect
      shell: bash
      env:
        PR_TITLE: ${{ github.event.pull_request.title }}
        HEAD_MSG: ${{ github.event.head_commit.message }}
        EVENT_NAME: ${{ github.event_name }}
        PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}
        PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        PUSH_BEFORE: ${{ github.event.before }}
        PUSH_AFTER: ${{ github.sha }}
      run: |
        title="${PR_TITLE:-$HEAD_MSG}"
        echo "event=$EVENT_NAME title=$title"
        is_release=false
        if printf '%s' "$title" | grep -qE '^chore: release v[0-9]'; then
          if [ "$EVENT_NAME" = "pull_request" ]; then
            base="$PR_BASE_SHA"; head="$PR_HEAD_SHA"
          else
            base="$PUSH_BEFORE"; head="$PUSH_AFTER"
          fi
          changed=$(git diff --name-only "$base" "$head" 2>/dev/null || true)
          echo "changed files:"; printf '%s\n' "$changed"
          allow='^(backend/pyproject\.toml|backend/app/__init__\.py|backend/uv\.lock|frontend/package\.json|frontend/package-lock\.json|CHANGELOG\.md)$'
          # release-only iff there is a diff AND no changed file falls outside the allowlist
          if [ -n "$changed" ] && ! printf '%s\n' "$changed" | grep -qvE "$allow"; then
            is_release=true
          else
            echo "Title says release but diff is empty or includes non-allowlisted files — full CI."
          fi
        fi
        if [ "$is_release" = "true" ]; then
          os_matrix='["ubuntu-latest"]'
        else
          os_matrix='["ubuntu-latest","windows-latest"]'
        fi
        echo "is_release=$is_release" >> "$GITHUB_OUTPUT"
        echo "os_matrix=$os_matrix" >> "$GITHUB_OUTPUT"
        echo "RESULT is_release=$is_release os_matrix=$os_matrix"
```

### `ci.yml` — new `ci-gate` aggregator

```yaml
ci-gate:
  name: CI Gate
  runs-on: ubuntu-latest
  if: always()
  needs:
    - detect-release
    - changelog-version-check
    - backend-lint
    - backend-test-unit
    - backend-test-integration
    - backend-coverage
    - backend-smoke
    - dependency-resolution
    - dependency-resolution-py314-guard
    - alembic-check
    - frontend-lint-build
    - frontend-test-unit
    - e2e-tests
  steps:
    - name: Gate on needed jobs (skipped is OK, failed/cancelled is not)
      shell: bash
      env:
        NEEDS_JSON: ${{ toJSON(needs) }}
      run: |
        printf '%s\n' "$NEEDS_JSON"
        bad=$(printf '%s' "$NEEDS_JSON" | python3 -c '
        import json,sys
        needs=json.load(sys.stdin)
        bad=[k for k,v in needs.items() if v.get("result") in ("failure","cancelled")]
        print(",".join(bad))
        ')
        if [ -n "$bad" ]; then
          echo "CI Gate FAILED — failed/cancelled jobs: $bad"
          exit 1
        fi
        echo "CI Gate PASSED."
```

### `ci.yml` — per-job behavior on a release-only PR

| Job | On release-only PR |
|---|---|
| `changelog-version-check` | **runs** (no gate) — the release-specific gate |
| `backend-lint` | **runs** (no gate) |
| `backend-test-unit` | **runs**, dynamic matrix → ubuntu-only (`needs: [detect-release]`, `matrix.os: ${{ fromJSON(needs.detect-release.outputs.os_matrix) }}`) |
| `backend-smoke` | **runs** (no gate) — import + boot probe |
| `frontend-lint-build` | **runs** (no gate) — validates the bumped `package.json` builds |
| `backend-test-integration` | **skipped** (`if: needs.detect-release.outputs.is_release != 'true'`, add `detect-release` to `needs`) |
| `backend-coverage` | **skipped** (same gate) |
| `dependency-resolution` | **skipped** (same gate) |
| `dependency-resolution-py314-guard` | **skipped** (same gate) |
| `alembic-check` | **skipped** (same gate) |
| `frontend-test-unit` | **skipped** (same gate) |
| `e2e-tests` | **skipped** (same gate) |

Gating mechanics: a custom `if:` *without* a status-check function is implicitly
ANDed with `success()`. So `if: needs.detect-release.outputs.is_release != 'true'`
keeps the "skip on release" behavior *and* the normal "don't run if a needed job
failed" behavior — no explicit `&& success()` required. The `ci-gate` job is the
exception: it uses `if: always()` precisely so it still runs when upstream jobs
were skipped or failed.

Side effect (intended improvement): `changelog-version-check` — currently *not* a
required context — now feeds `CI Gate`, so a missing changelog section fails the
gate and blocks the release PR.

### `codeql.yml` — skip `analyze` on release (non-required, title-only)

```yaml
analyze:
  ...
  if: >-
    !(
      (github.event_name == 'pull_request' && startsWith(github.event.pull_request.title, 'chore: release v')) ||
      (github.event_name == 'push' && startsWith(github.event.head_commit.message, 'chore: release v'))
    )
```

Weekly `schedule` scan and normal PRs/pushes are unaffected. CodeQL is not a
required check, so a title-only guard (no allowlist) is acceptable — a mis-skip
only means one missing non-required scan, never a block.

### `docker.yml` — skip the PR image build on release PRs (non-required, title-only)

```yaml
docker:
  ...
  if: >-
    !(github.event_name == 'pull_request' && startsWith(github.event.pull_request.title, 'chore: release v'))
```

This skips **only** the `pull_request` image build for release PRs. The real
release image is built when `tag-release.yml` dispatches `docker.yml` on the tag
(`workflow_dispatch` / tag ref) — that path is `event_name != 'pull_request'`, so
it is untouched. Normal PRs and tag builds are unaffected.

### Branch protection flip (applied post-merge by maintainer/agent with admin)

Rollout order that cannot self-block:

1. Open this change as a **normal** PR. `is_release=false` → full CI runs →
   produces all 9 legacy contexts **and** the new `CI Gate` context → mergeable
   under the *current* (9-context) protection.
2. Squash-merge.
3. Immediately PATCH the required-checks sub-resource to require **only**
   `CI Gate` (keeping `strict: true`):

   ```bash
   gh api -X PATCH repos/Jsakkos/engram/branches/main/protection/required_status_checks \
     --input ci-gate-required.json
   # ci-gate-required.json: {"strict": true, "checks": [{"context": "CI Gate"}]}
   ```

Using the dedicated `required_status_checks` sub-resource (not a full-protection
PUT) avoids clobbering the other protections (linear history, conversation
resolution, force-push block).

**Rollback** — restore the 9 contexts:

```bash
gh api -X PATCH repos/Jsakkos/engram/branches/main/protection/required_status_checks \
  --input ci-gate-rollback.json
```
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

(If the workflow changes themselves need reverting, also revert the `ci.yml` /
`codeql.yml` / `docker.yml` commit. Reverting the commit alone, without the
protection rollback, is safe because the legacy job names still exist and would
report — but the protection would then list `CI Gate` which still reports too.)

## Interactions verified

- **Dependabot auto-merge** (`dependabot-auto-merge.yml`): a deps PR is titled
  `build(deps): …` → `is_release=false` → full CI → `CI Gate` reflects real
  results → auto-merge waits for `CI Gate` and proceeds normally. No change to
  that workflow.
- **`tag-release.yml` → `release.yml` / `docker.yml`**: untouched. On the release
  push to `main`, `tag-release.yml` still tags and dispatches the 3-platform
  binary build (certifi assertion, `engram --selftest`, sha256 manifests) and the
  GHCR image. This is the real release validation and is unchanged.
- **`docs.yml`**: runs on push to `main`, rebuilds the docs site (incl. the new
  CHANGELOG section). Desired; untouched; non-required.
- **Push-to-`main` duplicate `ci.yml` run**: the release squash commit message
  starts with `chore: release v`, so `detect-release` returns `is_release=true`
  on that push too → the heavy jobs skip on the duplicate run as well. Cheap nets
  (lint/unit-ubuntu/smoke/changelog/frontend-build) still run.

## What still validates a release

`CI Gate` (the sole required check) ← `changelog-version-check` + `backend-lint` +
`backend-test-unit (ubuntu)` + `backend-smoke` + `frontend-lint-build`. Then on
merge: `tag-release.yml` → `release.yml` (3-platform binaries, certifi CA-bundle
assertion, `engram --selftest` TLS round-trip, per-platform sha256 manifests,
pinned PyInstaller) — the actual release gate, deliberately left intact.

## Validation plan

`act` is not installed locally. Validation is by:
1. **YAML lint / parse** of the three edited workflows.
2. **Reasoning dry-run** of `detect-release` against three scenarios:
   normal code PR (full CI), dependency-bump PR (full CI), release-only PR
   (heavy jobs skipped, `CI Gate` green).
3. **Live confirmation** on the PR itself: a normal PR must show all granular
   jobs running plus a green `CI Gate`. Optionally, a throwaway release-shaped
   PR (or inspecting the post-merge `chore: release` PR) confirms the heavy jobs
   skip while `CI Gate` stays green and the PR is mergeable (not BLOCKED).

## Out of scope

- Reducing CI on *normal* PRs (only release-only changes are streamlined).
- Touching `release.yml` / `tag-release.yml` / `publish.yml` / `download-stats.yml`.
- Changing `strict: true`, conversation-resolution, or any non-status-check
  protection.
