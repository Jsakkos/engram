# CI frozen-build + smoke-test gate

Date: 2026-08-17

## Problem

Nothing in `ci.yml` builds the PyInstaller bundle. `backend-smoke` boots the app
from source with `uv run uvicorn app.main:app`, where dynamic imports resolve at
runtime. Only `release.yml` freezes a binary, and it runs after a tag. So a
dependency change that breaks bundling merges green and surfaces on release day.

This is not hypothetical. Renovate bumped scipy 1.17.0 to 1.17.1 in PR #570
(2026-08-01). That release relocated scipy's vendored `array_api_compat` from
`scipy._lib` to `scipy._external`. PyInstaller's built-in `hook-scipy.py`
hardcodes the old path, so its hidden import degraded to a non-fatal
`WARNING: Hidden import "..." not found!` line inside a roughly 90,000-line build
log. Builds stayed green and the binaries were dead on startup. v0.29.0,
v0.30.0 and v0.31.0 all shipped without a working artifact.

PR #598 fixes that specific spec bug. This is the systemic guard.

## Design

### 1. Path-filtered trigger, computed in `detect-release`

The `detect-release` job already checks out with `fetch-depth: 0` and computes a
changed-file list, but only inside the release-title branch. That computation is
hoisted to the top of the step and gains a second consumer, a new
`build_relevant` output.

Trigger paths:

- `backend/uv.lock`, `backend/pyproject.toml` (the dependency set)
- `backend/engram.spec` (what gets bundled)
- `backend/scripts/fetch_fpcalc.py` (the bundled binary the smoke test asserts)
- `backend/scripts/smoke_test_frozen.sh`, `.github/workflows/ci.yml` (the gate
  itself, so a PR editing the gate always exercises it)

`diff_ok` tracks whether a usable diff exists. A first push to a new branch has
an all-zeros `github.event.before` and cannot diff, so that case defaults to
running the build. The filter fails safe toward running, never toward skipping.

The filter deliberately fires on `chore: release` PRs, which touch `uv.lock` and
`pyproject.toml`. Those PRs skip most heavy jobs by design, but a release PR is
exactly when a working binary matters most, so this is the one heavy job they
keep.

Rejected alternatives: every-PR (about 5 minutes on every PR, and the extra
coverage is app-code changes that break bundling, a rarer class than dependency
churn); nightly on main (zero PR cost, but finds breakage after merge, which is
the status quo this replaces).

### 2. Shared smoke script

`release.yml` repeats the smoke assertions six times: Windows in PowerShell, and
five bash copies for Linux x64, Linux arm64, macOS, and the two headless
variants. Diffing them shows Linux x64, arm64 and macOS are byte-identical, and
the two headless copies are the same logic plus an `allow_lan_access == True`
assertion.

`backend/scripts/smoke_test_frozen.sh` takes the executable path and an optional
`--expect-lan-access`, and covers all five. The `env:` blocks stay at the call
site so the headless jobs keep their deliberately-unset `HOST` (they bind
`0.0.0.0`, which is the thing they exist to verify). The `pgrep` pattern for the
fork-bomb guard is derived from the executable path rather than hardcoded, so it
cannot silently stop matching if the dist layout moves.

The Windows PowerShell copy is left alone. Converting it to bash would change
the release path for the one platform this PR's CI job cannot exercise.

### 3. Hidden-import tripwire

The fatal grep stays narrow, matching only
`Hidden import "...scipy|array_api_compat..." not found`. Every unresolved
hidden import is additionally listed non-fatally, and `pyinstaller-build.log`
plus PyInstaller's own `build/engram/warn-engram.txt` are uploaded as artifacts.
Unresolved hidden imports are normal for most hooks, which declare imports
speculatively across library versions, so broadening the fatal pattern needs
evidence from a real build log rather than a guess.

The tripwire is redundant with the smoke test for this specific bug, since the
binary dies on startup. It is kept because it names the cause directly in the
log instead of leaving a maintainer to diagnose a traceback.

### 4. Gating

`ci.yml` has a `ci-gate` aggregator job that `needs:` every granular job and
reports one pass/fail. That is the named required check in branch protection.
Adding `frozen-build` to its `needs:` makes it gating with no GitHub settings
change. `ci-gate` treats `skipped` as acceptable and fails only on
`failure`/`cancelled`, so the path filter composes with it correctly.

## Verification

- The job must go red on `main` as of this writing, because PR #598 is still
  open and the scipy bug is live. That is the negative evidence.
- Applying #598's `engram.spec` change on a scratch commit must turn it green.
  That is the positive evidence, and the scratch commit is then dropped.
- This PR must therefore merge after #598, or be rebased onto it.
