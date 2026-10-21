frontend: sh -c 'uv run --no-project python scripts/ensure_frontend_deps.py && cd ./frontend/ && npm run dev'
backend: sh -c 'cd ./backend/ && uv sync --locked && DEBUG=${DEBUG:-true} uv run uvicorn app.main:app --port ${VITE_BACKEND_PORT:-8000}'
