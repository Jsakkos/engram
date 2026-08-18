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

### 3. New-unresolved-hidden-import baseline

First attempt was a fatal grep for `Hidden import "...scipy|array_api_compat..."
not found`. A real build log (CI run 32103108296) shows that does not work.
Seven hidden imports are unresolved on a healthy build, two of which match that
pattern:

    MySQLdb
    pycparser.lextab
    pycparser.yacctab
    pysqlite2
    scipy._lib.array_api_compat.numpy.fft
    scipy.special._cdflib
    sklearn.neighbors._typedefs

`scipy._lib.array_api_compat.numpy.fft` is PyInstaller's own bundled
`hook-scipy.py` naming the location scipy abandoned. That hook is not changed by
PR #598, so the warning persists after the bundle is fixed. The pattern
therefore cannot distinguish broken from working, and would fail forever.

The deeper point: the breakage was a warning *appearing*, not a particular
warning existing. Before scipy 1.17.1 that import resolved silently. So the
actionable signal is a diff against a known-good baseline.

`backend/scripts/check_hidden_imports.sh` extracts every unresolved hidden
import from the build log, compares it to `backend/pyinstaller-known-warnings.txt`,
and fails on anything new. Baseline entries that stop being reported are noted
but not fatal. This would have caught #570 on the Renovate PR from the log
alone, and it generalizes to any dependency relocating a module.

The baseline file documents, per entry, why that import is safe to ignore, and
tells a future maintainer to declare the real path in `engram.spec` rather than
reflexively appending to the baseline.

The check lives in a script rather than inline YAML so it can be shellchecked
and run against a saved build log locally. Booting the binary remains the real
gate; this check exists to name the cause in the log instead of leaving someone
to diagnose a startup traceback.

### 4. Gating

`ci.yml` has a `ci-gate` aggregator job that `needs:` every granular job and
reports one pass/fail. That is the named required check in branch protection.
Adding `frozen-build` to its `needs:` makes it gating with no GitHub settings
change. `ci-gate` treats `skipped` as acceptable and fails only on
`failure`/`cancelled`, so the path filter composes with it correctly.

## Verification

Both directions were observed on real CI, not inferred from the YAML.

- **Red.** Run 32103108296, on top of `main` before #598 landed, failed the
  frozen-build job on the live scipy bug, while `backend-smoke` (which boots
  from source) passed in the same run. That contrast is the gap this closes.
- **Green.** #598 merged mid-task and shipped as v0.31.1, so the branch was
  rebased onto `main` rather than carrying a scratch commit. The job then builds
  and boots the real fixed bundle.
- **Path filter.** Exercised locally against real commits: this PR true, a
  frontend-only Renovate bump (19b3c913) false, an unusable diff true
  (fail-safe), a `chore: release` commit (6ee2063a) true. Confirmed on CI, where
  `detect-release` printed `build_relevant=true`.
- **Hidden-import checker.** Run against the saved failing build log in all
  three modes: baseline complete (pass), an entry removed to simulate a module
  relocating (fail, exit 1), and a baseline entry no longer reported (noted, not
  fatal).
