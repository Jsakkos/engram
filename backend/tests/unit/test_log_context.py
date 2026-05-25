"""Tests for job-id-tagged logging.

The diagnostics bundle extracts one job's logs by grepping a ``job=<id>``
token. That only works if ``logger.contextualize(job_id=...)`` tags BOTH
loguru-native calls (used in ``app/matcher``) AND stdlib ``logging`` calls
(used in the services/coordinators) which reach loguru via ``InterceptHandler``.
These tests prove that contract against the real log format.
"""

import io
import logging as stdlogging

import pytest
from loguru import logger

from app.core.log_context import job_log_context, with_job_log_context
from app.core.logging import _FILE_LOG_FORMAT, InterceptHandler


@pytest.mark.unit
class TestJobLogContext:
    def test_tags_both_loguru_and_stdlib_lines(self):
        sink = io.StringIO()
        logger.configure(extra={"job_id": "-"})
        sink_id = logger.add(sink, format=_FILE_LOG_FORMAT, level="DEBUG")

        std_logger = stdlogging.getLogger("test_job_ctx")
        std_logger.handlers = [InterceptHandler()]
        std_logger.setLevel(stdlogging.DEBUG)
        std_logger.propagate = False
        try:
            with job_log_context(7):
                logger.info("native tagged")
                std_logger.info("stdlib tagged")  # routed through InterceptHandler
            logger.info("native untagged")
        finally:
            logger.remove(sink_id)
            logger.configure(extra={})
            std_logger.handlers = []

        out = sink.getvalue()
        # Both the loguru-native and the stdlib-routed line carry the tag.
        assert out.count("job=7 |") == 2
        assert "native tagged" in out
        assert "stdlib tagged" in out
        # Outside the context the default tag applies.
        assert "job=- |" in out
        assert "native untagged" in out

    async def test_nested_create_task_inherits_context(self):
        """The real flow wraps a top-level task; nested match/progress tasks
        spawned inside it must inherit the tag (create_task copies the context),
        including stdlib lines routed through InterceptHandler."""
        import asyncio

        sink = io.StringIO()
        logger.configure(extra={"job_id": "-"})
        sink_id = logger.add(sink, format=_FILE_LOG_FORMAT, level="DEBUG")

        std_logger = stdlogging.getLogger("test_nested_ctx")
        std_logger.handlers = [InterceptHandler()]
        std_logger.setLevel(stdlogging.DEBUG)
        std_logger.propagate = False

        async def nested():
            std_logger.info("nested stdlib line")

        async def parent():
            await asyncio.create_task(nested())

        try:
            await with_job_log_context(123, parent())
        finally:
            logger.remove(sink_id)
            logger.configure(extra={})
            std_logger.handlers = []

        out = sink.getvalue()
        assert "job=123 |" in out
        assert "nested stdlib line" in out

    async def test_async_wrapper_tags_coroutine(self):
        sink = io.StringIO()
        logger.configure(extra={"job_id": "-"})
        sink_id = logger.add(sink, format=_FILE_LOG_FORMAT, level="DEBUG")

        async def work():
            logger.info("inside async job")

        try:
            await with_job_log_context(42, work())
        finally:
            logger.remove(sink_id)
            logger.configure(extra={})

        out = sink.getvalue()
        assert "job=42 |" in out
        assert "inside async job" in out

    def test_format_defaults_to_dash_without_context(self):
        sink = io.StringIO()
        logger.configure(extra={"job_id": "-"})
        sink_id = logger.add(sink, format=_FILE_LOG_FORMAT, level="DEBUG")
        try:
            logger.info("no job here")
        finally:
            logger.remove(sink_id)
            logger.configure(extra={})

        assert "job=- |" in sink.getvalue()
