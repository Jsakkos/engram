"""
Unified logging configuration using Loguru.

Intercepts standard library logging and routes it through Loguru.
Configures file rotation, retention, and compression.
"""

import inspect
import logging
import sys
from pathlib import Path

from loguru import logger

from app.config import settings


class InterceptHandler(logging.Handler):
    """
    Default handler from examples in loguru documentation.
    See https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller that originated the logged message. Start from this
        # emit() frame (depth 0) and walk outward past every frame that lives in
        # the stdlib logging module, so Loguru attributes the record to the real
        # call site instead of logging.callHandlers. The `frame and` guard stops
        # cleanly if the walk reaches the top of the stack.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    """Configure logging for the application."""

    # intercept everything at the root logger
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(logging.INFO)

    # remove every other handler's handlers and propagate to root logger
    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # configure loguru
    logger.remove()  # Remove default handler

    # 1. Console handler (stderr)
    logger.add(
        sys.stderr,
        level="DEBUG" if settings.debug else "INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 2. File handler
    log_file = Path.home() / ".engram" / "engram.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_file),
        rotation="10 MB",  # Rotate when file reaches 10MB
        retention="1 week",  # Keep logs for 1 week
        compression="zip",  # Compress rotated logs
        level="DEBUG",  # Always log debug to file for troubleshooting
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True,  # Thread-safe for async
        backtrace=True,
        diagnose=True,
    )

    logger.info("Logging initialized via Loguru")
