"""Shared retry-with-backoff decorator for matcher network operations."""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from loguru import logger

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator for retrying operations with exponential backoff.

    Args:
        max_retries: Number of retry attempts after the initial call.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum delay between retries.
        backoff_factor: Multiplier applied to the delay after each retry.
        exceptions: Tuple of exception types that trigger a retry.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            delay = base_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}: {e}"
                        )
                        raise e

                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries + 1} failed for "
                        f"{func.__name__}: {e}, retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

            raise last_exception

        return wrapper  # type: ignore

    return decorator
