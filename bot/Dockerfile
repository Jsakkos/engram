FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    BOT_DB_PATH=/data/engram-bot.db

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY engram_bot ./engram_bot
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 bot && mkdir -p /data && chown bot /data
USER bot
VOLUME ["/data"]

CMD ["/app/.venv/bin/python", "-m", "engram_bot"]
