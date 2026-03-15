# Contributing

## Development Setup

```bash
git clone https://github.com/Jsakkos/engram.git
cd engram

# Backend
cd backend
uv sync
cd ..

# Frontend
cd frontend
npm install
cd ..
```

### Start Dev Servers

Start the backend and frontend in separate terminals:

=== "Backend"

    ```bash
    cd backend
    uv run uvicorn app.main:app --reload
    ```

=== "Frontend"

    ```bash
    cd frontend
    npm run dev
    ```

Open [http://localhost:5173](http://localhost:5173). The Vite dev server proxies `/api` and `/ws` to the backend at `localhost:8000`.

## Code Quality

### Backend

```bash
cd backend
uv run ruff check .       # Lint
uv run ruff format .      # Format
```

**Ruff config**: Line length 100, target Python 3.11, rules `E/F/I/UP/B`, double quotes.

### Frontend

```bash
cd frontend
npm run lint              # ESLint
npm run build             # TypeScript check + production build
```

## Running Tests

```bash
# Backend — all tests
cd backend
uv run pytest

# Backend — specific category
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/pipeline/

# Frontend — E2E tests (requires backend with DEBUG=true)
cd frontend
npx playwright install    # first time only
npm run test:e2e
npm run test:e2e:ui       # with interactive browser UI
```

See the [Testing](testing.md) page for detailed test categories and patterns.

## Git Workflow

- Work on **feature branches**, not directly on `main`
- Branch naming: `fix/32-movie-track-state`, `feat/34-metadata-logging`
- Reference issue numbers in commit messages
- Create PRs for merging to main

## Key Conventions

- **Async everywhere** — use `async`/`await` for database, subprocess, and I/O operations
- **Singleton services** — `job_manager`, `ws_manager`, `curator` are module-level singletons
- **Error hierarchy** — use specific exceptions from `app/core/errors.py`, never bare `except`
- **State machine** — all job lifecycle tracked through `JobState` transitions
- **Tailwind v4** — uses `@theme inline` blocks in CSS, not `tailwind.config.js`

## Documentation

Documentation is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and auto-deploys to GitHub Pages on push to `main`.

```bash
# Preview docs locally
pip install mkdocs-material "mkdocstrings[python]"
mkdocs serve
```

Edit files under `docs/` and they'll be picked up automatically.
