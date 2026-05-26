"""Bind a job id into the logging context for the life of a coroutine.

Engram's logs are a single global stream (``~/.engram/engram.log``). To let
the diagnostics bundle extract exactly one job's lines, every per-job
coroutine is run inside ``logger.contextualize(job_id=...)`` so each emitted
line carries a ``job=<id>`` tag.

The tag flows through both logging paths:
- loguru-native calls in ``app/matcher`` (they read the contextvar directly), and
- stdlib ``logging`` calls in the services/coordinators, which reach loguru via
  the ``InterceptHandler`` in ``app/core/logging.py`` — and that handler emits
  through ``logger.opt(...).log(...)``, which also reads the contextvar.

Tasks spawned *within* the wrapped coroutine inherit the binding automatically,
because ``asyncio.create_task`` copies the current context at creation time.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from loguru import logger


async def with_job_log_context(job_id: int, coro: Coroutine[Any, Any, Any]) -> Any:
    """Await ``coro`` with ``job_id`` bound into the loguru logging context."""
    with logger.contextualize(job_id=job_id):
        return await coro


def job_log_context(job_id: int):
    """Return a context manager binding ``job_id`` into the logging context.

    For coroutines that may be entered directly (not only via
    ``asyncio.create_task``) — e.g. matching invoked straight from an API
    handler — wrap the body in ``with job_log_context(job_id):`` so the lines
    are tagged regardless of how the coroutine was reached.
    """
    return logger.contextualize(job_id=job_id)
