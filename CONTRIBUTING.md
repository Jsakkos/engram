# Contributing to Engram

## Local development setup

### Prerequisites

- Python 3.11
- Node.js 24
- [uv](https://github.com/astral-sh/uv) for Python dependency management
- MakeMKV with a valid license (for end-to-end work with real discs)
- Pre-commit framework: `pipx install pre-commit` or `uv tool install pre-commit`

### One-time setup after cloning

```bash
# Install pre-commit hooks into .git/hooks (runs ruff + ESLint on commit)
pre-commit install

# Backend
cd backend && uv sync

# Frontend
cd frontend && npm install
```

## Pre-commit hooks

`.pre-commit-config.yaml` runs on every commit:

- **Backend**: `ruff check --fix` and `ruff format` on changed Python files in `backend/`
- **Frontend**: `eslint --max-warnings 0` on changed `*.ts`/`*.tsx`/`*.js`/`*.jsx` files in `frontend/`
- **Repo hygiene**: trailing whitespace, EOF newlines, YAML/TOML validity, merge-conflict markers, large-file guard (1 MB)

To run hooks against all files manually (useful after a rebase):

```bash
pre-commit run --all-files
```

To skip a commit for a genuine emergency:

```bash
git commit --no-verify -m "..."
```

Don't make a habit of it.

## CI pipeline

`.github/workflows/ci.yml` runs the following jobs on every PR and push to `main`. All jobs run in parallel unless `needs:` is specified.

| Job | What it checks | Runs on |
|-----|---------------|---------|
| `Backend Lint` | `ruff check` + `ruff format --check` | Linux |
| `Backend Tests (unit)` | `pytest tests/unit/` with coverage | Linux + Windows |
| `Backend Tests (integration)` | `pytest tests/integration/ tests/pipeline/` (after unit passes) | Linux |
| `Backend Smoke Test` | Imports `app.main:app` and probes `GET /api/jobs` | Linux |
| `Alembic Migration Roundtrip` | `upgrade head → downgrade base → upgrade head` | Linux |
| `Frontend Lint & Build` | ESLint + `tsc` + Vite production build + bundle-size budget | Linux |
| `Frontend Unit Tests` | Vitest with v8 coverage | Linux |
| `E2E Tests` | Playwright against a real backend+frontend (after Lint & Build) | Linux |
| `CodeQL Analyze` | Security/quality scan for Python and TypeScript | Linux |

Concurrency control cancels stale runs when you push fixups to a PR.

### Bundle size budget

Configured in `frontend/scripts/check-bundle-size.mjs`. Current thresholds (gzipped):

- Total JS: 600 KB
- Total CSS: 60 KB
- Largest single JS chunk: 350 KB

Edit the `BUDGETS_KB` constant in that file when you intentionally cross a threshold. Don't bump it casually — investigate first.

### Coverage

Backend coverage XML and frontend coverage HTML are uploaded as workflow artifacts on every run. To wire up Codecov for PR comments, add a `CODECOV_TOKEN` secret and a `codecov/codecov-action` step.

## Branch protection (configured in GitHub settings)

Under **Settings → Branches → Branch protection rules** for `main`:

- **Require a pull request before merging**: enabled
- **Require status checks to pass before merging**: enabled
  - Required checks:
    - `Backend Lint`
    - `Backend Tests (unit) (ubuntu-latest)`
    - `Backend Tests (unit) (windows-latest)`
    - `Backend Tests (integration)`
    - `Backend Smoke Test`
    - `Alembic Migration Roundtrip`
    - `Frontend Lint & Build`
    - `Frontend Unit Tests`
    - `E2E Tests`
- **Require branches to be up to date before merging**: enabled
- **Require linear history**: enabled (matches the single-commit-per-issue convention)
- **Require conversation resolution before merging**: enabled

## Dependency updates

Renovate (`.github/renovate.json`) opens PRs Monday mornings:

- **Patch + pin updates**: auto-merge after CI passes
- **Minor npm updates (non-0.x)**: auto-merge after CI passes
- **Major updates**: land in the dashboard for manual review

Install the [Renovate GitHub App](https://github.com/apps/renovate) on the repo to activate.

## Releases

Tag a commit with `v*` (e.g., `v0.7.0`) to trigger `.github/workflows/release.yml`:

1. Builds PyInstaller bundles for Windows, Linux, and macOS
2. Smoke-tests each built executable by starting it and hitting `/api/jobs`
3. Uploads `.zip` / `.tar.gz` artifacts to a GitHub Release with auto-generated notes

A failing smoke test blocks the release. Do not bypass.

## Commit style

- One commit per issue/feature
- Reference the issue number in the message (e.g., `fix: correct DiscDB scan log URL (#124)`)
- Conventional-commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`

## Known broken tests

These pre-existing unit tests fail in CI and are explicitly deselected in
`.github/workflows/ci.yml` (Backend Tests (unit) job). They surfaced when
the `| tee` shell-pipe bug was fixed and stopped masking pytest's exit
code. They are tracked for follow-up:

| Test | Root cause |
|---|---|
| `test_coverage_improvements.py::TestAnalystPropertyBased` (4 tests) | `get_config_sync()` uses a cached sync engine that bypasses the unit conftest's `async_session` monkeypatch |
| `test_database_migration.py::TestOpenSubtitlesCleanup` (3 tests) | Tests assert `opensubtitles_*` fields are removed from `AppConfig`; the cleanup migration was never shipped |
| `test_database_migration.py::TestSchemaMigration::test_migration_handles_extra_columns` | Migration runs twice → `duplicate column name` |
| `test_disc_name_identification.py` (3 tests) | Same `get_config_sync()` root cause as `TestAnalystPropertyBased` |
| `test_mime_types.py::TestMimeTypeRegistration::test_app_main_registers_types_on_import` | `mimetypes.init()` reverts the explicit `.mjs` registration between import and test |
| `test_organizer.py::TestMovieOrganization` + `TestTVOrganization` (6 tests) | Same `get_config_sync()` root cause |

**To fix the largest group**, refactor the unit test conftest at
`backend/tests/unit/conftest.py` to:
1. Use a single shared SQLite file (not `:memory:`) for both sync and async engines.
2. Patch `_get_sync_engine()` (or reset its `_sync_engine` cache) to point at
   that file.
3. Run `create_all()` against both engines before yielding.

Once a group's root cause is fixed, remove the matching `--deselect`
line in `.github/workflows/ci.yml`.

## Visual regression baselines

`frontend/e2e/visual-regression.spec.ts` snapshots key pages. To generate or update baselines:

```bash
cd frontend
npx playwright test visual-regression --update-snapshots
git add e2e/visual-regression.spec.ts-snapshots/
```

Only update baselines when an intentional UI change requires it.
