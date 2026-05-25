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
git clone https://github.com/Jsakkos/engram.git
cd engram

# Install pre-commit hooks into .git/hooks (runs ruff + ESLint on commit)
pre-commit install

# Backend
cd backend && uv sync && cd ..

# Frontend
cd frontend && npm install && cd ..
```

### Start the dev servers

Run the backend and frontend in separate terminals:

```bash
# Backend (API on port 8000)
cd backend
uv run uvicorn app.main:app
```

```bash
# Frontend (dashboard on port 5173)
cd frontend
npm run dev
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` and `/ws` to the backend at `localhost:8000`.

> **Do not start the backend with `--reload`.** It spawns a child process with its own drive sentinel, which produces duplicate disc events.

## Code quality

```bash
# Backend
cd backend
uv run ruff check .       # Lint
uv run ruff format .      # Format

# Frontend
cd frontend
npm run lint              # ESLint
npm run build             # TypeScript check + production build
```

Ruff config: line length 100, target Python 3.11, rules `E/F/I/UP/B`, double quotes.

## Running tests

```bash
# Backend — all tests
cd backend
uv run pytest

# Backend — by category
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/pipeline/

# Frontend — E2E tests (requires backend with DEBUG=true)
cd frontend
npx playwright install    # first time only
npm run test:e2e
npm run test:e2e:ui       # interactive browser UI
```

See the [testing guide](https://jsakkos.github.io/engram/development/testing/) for detailed test categories, fixtures, and patterns.

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

## Git workflow

- Work on **feature branches**, never directly on `main`
- Branch naming: `fix/32-movie-track-state`, `feat/34-metadata-logging`
- One commit per issue/feature, referencing the issue number (e.g., `fix: correct DiscDB scan log URL (#124)`)
- Conventional-commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`
- Open a PR to merge into `main` (linear history is enforced)

## Key conventions

- **Async everywhere** — use `async`/`await` for database, subprocess, and I/O operations
- **Singleton services** — `job_manager`, `ws_manager`, `curator` are module-level singletons
- **Error hierarchy** — use specific exceptions from `app/core/errors.py`, never bare `except`
- **State machine** — all job lifecycle is tracked through `JobState` transitions
- **Tailwind v4** — uses `@theme inline` blocks in CSS, not `tailwind.config.js`

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

## Visual regression baselines

`frontend/e2e/visual-regression.spec.ts` snapshots key pages. To generate or update baselines:

```bash
cd frontend
npx playwright test visual-regression --update-snapshots
git add e2e/visual-regression.spec.ts-snapshots/
```

Only update baselines when an intentional UI change requires it.

## Building the docs

Documentation is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and auto-deploys to GitHub Pages on push to `main`.

```bash
pip install mkdocs-material "mkdocstrings[python]"
mkdocs serve
```

Edit files under `docs/`; they are picked up automatically. The root `CHANGELOG.md` and `CONTRIBUTING.md` are transcluded into the site, so edit those at the repo root rather than under `docs/`.
