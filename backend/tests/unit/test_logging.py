"""Unit tests for the Loguru/stdlib logging bridge in app/core/logging.py.

Regression coverage for the InterceptHandler frame-depth bug: the old recipe
attributed every intercepted stdlib record to the logging module's
``callHandlers`` frame instead of the real caller, collapsing all backend logs
to ``logging:callHandlers:<line>`` and destroying per-module observability.
"""

import logging

from loguru import logger as loguru_logger

from app.core.logging import InterceptHandler


def test_intercept_handler_attributes_record_to_real_caller():
    """An intercepted stdlib record must carry the emitting caller's identity."""
    captured = []
    sink_id = loguru_logger.add(captured.append, level="DEBUG", format="{message}")

    probe = logging.getLogger("test.intercept.caller_attribution")
    probe.handlers = [InterceptHandler()]
    probe.propagate = False
    probe.setLevel(logging.INFO)

    try:
        probe.info("attribution probe")  # emitted from THIS function
    finally:
        loguru_logger.remove(sink_id)
        probe.handlers = []

    assert captured, "InterceptHandler did not forward the record to Loguru"
    record = captured[0].record

    # The buggy frame walk reported function='callHandlers', name='logging'.
    assert record["function"] == "test_intercept_handler_attributes_record_to_real_caller"
    assert record["name"] == __name__
    assert record["module"] != "logging"


def test_intercept_handler_forwards_exception_info():
    """exc_info on a stdlib record must reach Loguru as an attached exception."""
    captured = []
    sink_id = loguru_logger.add(captured.append, level="DEBUG", format="{message}")

    probe = logging.getLogger("test.intercept.exception_forwarding")
    probe.handlers = [InterceptHandler()]
    probe.propagate = False
    probe.setLevel(logging.INFO)

    try:
        try:
            raise ValueError("boom")
        except ValueError:
            probe.exception("handling failed")
    finally:
        loguru_logger.remove(sink_id)
        probe.handlers = []

    assert captured, "InterceptHandler did not forward the record to Loguru"
    record = captured[0].record
    assert record["exception"] is not None
    assert record["exception"].type is ValueError
